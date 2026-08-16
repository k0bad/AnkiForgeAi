"""Оркестратор pipeline: связывает все стадии.

Pipeline для одного потока кандидатов:
    Ingest → Normalize → Dedupe → (Review если нужно) → Enrich → Media → DB

Команда `push` — отдельный шаг: approved → Anki.
Команда `sync` — отдельный шаг: Anki → cache.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .anki.connect import AnkiConnect, AnkiConnectError
from .anki.notetype import _get_note_type_name as get_note_type_name
from .anki.notetype import card_to_anki_fields
from .config import Config
from .db import Database
from .dedupe import check_card, judge_review
from .enrich.examples import enrich_example_batch
from .enrich.grammar import INFLECTED_POS, enrich_grammar_batch
from .enrich.pronunciation import enrich_pronunciation_batch
from .enrich.translation import enrich_translation
from .log import get_logger
from .media.images import attach_image
from .media.tts import generate_audio
from .models import Card, Status

logger = get_logger(__name__)


class NoteTypeMissingError(Exception):
    """Note Type из активного языкового профиля не существует в Anki.

    Обычно значит, что anki.note_type в language.yaml сменился (например,
    после апдейта репо через git pull), а `ankiforgeai init` не переигрывался,
    чтобы (пере)создать Note Type в самом Anki.
    """

    def __init__(self, note_type: str) -> None:
        self.note_type = note_type
        super().__init__(
            f"Note Type '{note_type}' не найден в Anki — запусти `ankiforgeai init`, "
            "чтобы создать его (или синхронизировать дизайн, если он уже есть)."
        )


def _record(db: Database, level: str, action: str, card_id: int | None, **details: object) -> None:
    """Записать событие и в audit_log (SQLite, персистентный audit), и в structlog
    (live-трасса текущего run_id — см. log.bound_run). Одна точка вместо двух
    рассинхронизирующихся вызовов на каждое событие пайплайна."""
    db.log_action(action, card_id=card_id, details=details)
    getattr(logger, level)(action, card_id=card_id, **details)


def delete_card_record(db: Database, card: Card, action: str) -> None:
    """Стереть карточку из локальной БД насовсем — её номер освобождается и
    достанется следующей новой карточке (см. db.py::_next_free_id). Общая
    точка для review/actions.delete_cards (явная команда `ankiforgeai delete`)
    и anki/sync.py (заметку удалили прямо в Anki) — сам вызов AnkiConnect
    deleteNotes (если он вообще нужен) остаётся на совести вызывающего кода.
    """
    assert card.id is not None, "delete_card_record ожидает уже сохранённую карточку"
    _record(db, "info", action, card.id, word=card.word, anki_note_id=card.anki_note_id)
    db.delete_card(card.id)


def _card_id(card: Card) -> int:
    """card.id как int — enrich-стадии получают только уже вставленные карточки
    (id выделяется до них, см. run_ingest_pipeline и review/actions.accept_cards)."""
    assert card.id is not None
    return card.id


def _needs_translation_stage(card: Card, cfg: Config) -> bool:
    """translation отсутствует — вызов нужен однозначно. Если translation уже
    есть (напр. с ingest topic, который сам не спрашивает EN-фразу), вызов всё
    равно нужен, если карточке вообще будут искать картинку и image_query ещё
    не заполнен (issue #47) — иначе вызов не нужен вовсе."""
    if not card.translation:
        return True
    return cfg.images.enabled and card.pos.value in cfg.images.only_for_pos and not card.image_query


async def _run_enrich_stage(
    stage: str,
    fn: Callable[[list[Card]], Awaitable[list[Card]]],
    cards: list[Card],
    is_complete: Callable[[Card], bool],
    db: Database,
    stats: dict,
    incomplete_ids: set[int],
) -> None:
    """Прогнать один batch-вызов enrichment; отследить провал и неполный результат.

    Провал самого вызова (сеть/парсинг/LLM) бьёт по всем cards в batch — это одна
    LLM-задача на всех. Но даже при успешном вызове LLM мог не вернуть данные для
    части карточек (id отсутствует в ответе) — это тоже неполный enrichment,
    просто без исключения, так что проверяем результат отдельно.
    """
    logger.info("stage.start", stage=stage, count=len(cards))
    try:
        await fn(cards)
    except Exception as e:
        stats["errors"] += 1
        for card in cards:
            _record(db, "warning", "enrich_failed", card.id, stage=stage, error=str(e))
        incomplete_ids.update(_card_id(c) for c in cards)
        return
    logger.info("stage.done", stage=stage)

    for card in cards:
        if not is_complete(card):
            incomplete_ids.add(_card_id(card))
            _record(db, "warning", "enrich_incomplete", card.id, stage=stage)


async def enrich_and_generate_media(
    cards: list[Card],
    db: Database,
    cfg: Config,
    auto_enrich: bool = True,
    auto_media: bool = True,
    auto_pick_images: bool = True,
) -> tuple[dict, set[int]]:
    """Прогнать enrich- и media-стадии для карточек, уже прошедших dedupe.

    Общий шаг для run_ingest_pipeline (accepted-кандидаты) и review_pending
    (карточки, принятые вручную из review) — раньше accept просто менял статус,
    ничего не enrich'я (issue #11). Возвращает (stats, incomplete_ids);
    incomplete_ids — id карточек, где часть enrichment не удалась: вызывающий
    код должен вернуть такие карточки в review, а не помечать approved.

    auto_pick_images=False (issue #36) полностью пропускает картиночную стадию
    здесь — вызывающий код (review_pending) сам находит кандидатов и даёт
    человеку выбрать, вместо повторного (и лишнего для лимитов провайдеров)
    поискового запроса после автовыбора первого результата.
    """
    stats: dict = {"enriched": 0, "enrich_incomplete": 0, "audio": 0, "errors": 0}
    incomplete_ids: set[int] = set()

    if auto_enrich and cards:
        # Pronunciation — обработана через _run_enrich_stage с per-card ошибками,
        # как grammar/examples: раньше делила try/except с translation ниже, и падение
        # batch-вызова не помечало карточки incomplete — они тихо уходили в APPROVED
        # без произношения (тот же баг, что #7 чинил для grammar/examples, но не докрыл
        # для этой стадии).
        if cfg.enrich.pronunciation:
            await _run_enrich_stage(
                "pronunciation",
                enrich_pronunciation_batch,
                cards,
                lambda c: bool(c.pronunciation),
                db,
                stats,
                incomplete_ids,
            )

        # Translation — базовая стадия (без тумблера, всегда включена)
        try:
            logger.info("stage.start", stage="translation", count=len(cards))
            for card in cards:
                if _needs_translation_stage(card, cfg):
                    await enrich_translation(card)
            logger.info("stage.done", stage="translation")
        except Exception as e:
            stats["errors"] += 1
            _record(db, "warning", "enrich_failed", None, error=str(e), count=len(cards))

        # Per-card translation retry (если не заполнилось)
        for card in cards:
            if not _needs_translation_stage(card, cfg):
                continue
            try:
                await enrich_translation(card)
            except Exception as e:
                stats["errors"] += 1
                db.log_action(
                    "enrich_failed",
                    card_id=card.id,
                    details={"stage": "translation", "error": str(e)},
                )
                incomplete_ids.add(_card_id(card))
            else:
                if not card.translation:
                    incomplete_ids.add(_card_id(card))
                    db.log_action(
                        "enrich_incomplete",
                        card_id=card.id,
                        details={"stage": "translation"},
                    )

        # image_query (англ. gloss для поиска картинок) генерируется тем же LLM-вызовом,
        # что и translation, но парсится отдельной строкой ("EN:") и может не прийти,
        # даже когда ru распарсился нормально. card.translation тут не считается
        # incomplete — перевод корректен, — но без трассы это тихо роняет качество
        # image-поиска (attach_image() падает обратно на card.word, см. issue #10),
        # и ничего не сигналит об этом (issue #29). Актуально только для карточек,
        # которым вообще будут искать картинку.
        if cfg.images.enabled:
            for card in cards:
                if (
                    card.translation
                    and not card.image_query
                    and card.pos.value in cfg.images.only_for_pos
                ):
                    _record(
                        db,
                        "warning",
                        "translation.image_query_missing",
                        card.id,
                        word=card.word,
                    )

        # Grammar и examples — обработаны через _run_enrich_stage с per-card ошибками
        if cfg.enrich.grammar:
            await _run_enrich_stage(
                "grammar",
                enrich_grammar_batch,
                cards,
                lambda c: c.pos not in INFLECTED_POS or bool(c.forms),
                db,
                stats,
                incomplete_ids,
            )

        if cfg.enrich.examples:
            await _run_enrich_stage(
                "examples",
                enrich_example_batch,
                cards,
                lambda c: bool(c.example),
                db,
                stats,
                incomplete_ids,
            )

        stats["enriched"] = len(cards) - len(incomplete_ids)
        stats["enrich_incomplete"] = len(incomplete_ids)

    if auto_media and cards:
        for card in cards:
            try:
                await generate_audio(card, cfg)
                stats["audio"] += 1
                _record(db, "info", "audio_generated", card.id)
            except Exception as e:
                stats["errors"] += 1
                _record(db, "warning", "audio_failed", card.id, error=str(e))
        # Картинки для существительных (если включено в конфиге)
        if cfg.images.enabled and auto_pick_images:
            for card in cards:
                try:
                    await attach_image(card, cfg, auto_pick=True)
                    if card.image:
                        stats.setdefault("images", 0)
                        stats["images"] += 1
                        _record(db, "info", "image_generated", card.id)
                except Exception as e:
                    _record(db, "warning", "image_failed", card.id, error=str(e))

    return stats, incomplete_ids


async def run_ingest_pipeline(
    cards: list[Card],
    db: Database,
    cfg: Config,
    auto_enrich: bool = True,
    auto_media: bool = True,
) -> dict:
    """Прогнать кандидатов через dedupe → enrich → media → save.

    Возвращает статистику: {new: N, review: M, merged: K, ...}
    """
    stats = {
        "new": 0,
        "review": 0,
        "merged": 0,
        "enriched": 0,
        "enrich_incomplete": 0,
        "audio": 0,
        "errors": 0,
    }

    accepted: list[Card] = []
    for card in cards:
        decision = check_card(card, db, cfg)
        decision = await judge_review(card, decision, cfg)
        if decision.decision == "merge":
            _record(
                db,
                "info",
                "skip_duplicate",
                card.id,
                word=card.word,
                matches=[m.model_dump() for m in decision.matches],
                reason=decision.reason,
            )
            stats["merged"] += 1
            continue
        if decision.decision == "review":
            card.status = Status.REVIEW
            db.insert_card(card)
            _record(
                db,
                "info",
                "review_needed",
                card.id,
                matches=[m.model_dump() for m in decision.matches],
                reason=decision.reason,
            )
            stats["review"] += 1
            continue
        # Выделяем id сразу (INSERT, не UPDATE) — enrich_and_generate_media ниже
        # уже использует card.id для имён медиафайлов, значит номер должен
        # существовать до неё, а не после. Финальное сохранение ниже — update_card.
        db.insert_card(card)
        logger.debug("dedupe.accepted", card_id=card.id, word=card.word)
        accepted.append(card)
        stats["new"] += 1

    enrich_stats, incomplete_ids = await enrich_and_generate_media(
        accepted, db, cfg, auto_enrich, auto_media
    )
    stats.update(enrich_stats)

    for card in accepted:
        card.status = Status.REVIEW if card.id in incomplete_ids else Status.APPROVED
        db.update_card(card)
        _record(db, "info", "create", card.id, word=card.word)

    return stats


async def push_approved(db: Database, anki: AnkiConnect, cfg: Config) -> int:
    """Отправить все approved карточки в Anki. Вернуть количество."""
    approved = db.get_by_status(Status.APPROVED)
    if not approved:
        return 0

    logger.info("stage.start", stage="push", count=len(approved))
    await anki.ensure_deck()

    note_type = get_note_type_name()
    if note_type not in await anki.model_names():
        raise NoteTypeMissingError(note_type)

    pushed = 0
    for card in approved:
        try:
            if card.audio:
                audio_path = cfg.paths.audio_dir / card.audio
                if audio_path.exists():
                    await anki.store_media(card.audio, audio_path)
            if card.image:
                image_path = cfg.paths.images_dir / card.image
                if image_path.exists():
                    await anki.store_media(card.image, image_path)

            fields = card_to_anki_fields(card)
            tags = card.auto_tags()
            note_id = await anki.add_note(fields=fields, tags=tags)

            card.anki_note_id = note_id
            card.status = Status.PUSHED
            with db.connect() as conn:
                conn.execute(
                    "UPDATE cards SET status = ?, anki_note_id = ? WHERE id = ?",
                    (Status.PUSHED.value, note_id, card.id),
                )
            _record(db, "info", "push", card.id, note_id=note_id)
            pushed += 1
        except AnkiConnectError as e:
            _record(db, "warning", "push_failed", card.id, error=str(e))
    return pushed
