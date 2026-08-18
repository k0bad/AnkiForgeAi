"""Оркестратор pipeline: связывает все стадии.

Pipeline для одного потока кандидатов:
    Ingest → Normalize → Dedupe → (Review если нужно) → Enrich → Media → DB

Команда `push` — отдельный шаг: approved → Anki.
Команда `sync` — отдельный шаг: Anki → cache.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, timedelta

from .anki.connect import AnkiConnect, AnkiConnectError
from .anki.notetype import _get_note_type_name as get_note_type_name
from .anki.notetype import card_to_anki_fields
from .config import Config, resolve_anki_profile
from .db import Database
from .dedupe import check_card, judge_review
from .enrich.examples import enrich_example_batch
from .enrich.grammar import INFLECTED_POS, enrich_grammar_batch
from .enrich.pronunciation import enrich_pronunciation_batch
from .enrich.translation import enrich_translation
from .log import get_logger
from .media.images import attach_image
from .media.tts import generate_audio
from .models import Card, Level, Status

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

        # Translation — базовая стадия (без тумблера, всегда включена). Каждая
        # карточка — отдельный LLM-вызов (в отличие от batch-стадий ниже), так что
        # гоняем их параллельно (ограничено cfg.concurrency) вместо одной за другой —
        # иначе N карточек означает N последовательных round-trip'ов. Ошибка одной
        # карточки не должна блокировать остальные, поэтому try/except — per-card.
        translation_targets = [c for c in cards if _needs_translation_stage(c, cfg)]
        if translation_targets:
            logger.info("stage.start", stage="translation", count=len(translation_targets))
            sem = asyncio.Semaphore(cfg.concurrency)

            async def _translate_one(card: Card) -> None:
                async with sem:
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
                        return
                if not card.translation:
                    incomplete_ids.add(_card_id(card))
                    db.log_action(
                        "enrich_incomplete",
                        card_id=card.id,
                        details={"stage": "translation"},
                    )

            await asyncio.gather(*(_translate_one(c) for c in translation_targets))
            logger.info("stage.done", stage="translation")

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
        # Аудио и картинки — независимые per-card сетевые вызовы (edge-tts / image API),
        # раньше прогонялись строго по одной карточке за раз, сначала всё аудио, потом
        # все картинки. Теперь обе стадии идут параллельно (и друг с другом тоже) под
        # общим лимитом cfg.concurrency — тот же паттерн, что и translation выше.
        sem = asyncio.Semaphore(cfg.concurrency)

        async def _generate_audio_one(card: Card) -> None:
            async with sem:
                try:
                    await generate_audio(card, cfg)
                except Exception as e:
                    stats["errors"] += 1
                    _record(db, "warning", "audio_failed", card.id, error=str(e))
                    return
            stats["audio"] += 1
            _record(db, "info", "audio_generated", card.id)

        tasks = [_generate_audio_one(card) for card in cards]

        # Картинки для существительных (если включено в конфиге). Разбивка по
        # причине отсутствия (issue #54): "не тот POS" — норма, для остальных
        # частей речи картинки не ищутся вовсе; "провайдер не нашёл" — пустой
        # результат поиска или ошибка запроса (403/429/5xx) — это уже повод
        # чинить провайдера/ключ, а не ожидаемое поведение.
        if cfg.images.enabled and auto_pick_images:
            stats["images"] = {"found": 0, "skipped_not_noun": 0, "failed_no_result": 0}

            async def _attach_image_one(card: Card) -> None:
                async with sem:
                    try:
                        await attach_image(card, cfg, auto_pick=True)
                    except Exception as e:
                        _record(db, "warning", "image_failed", card.id, error=str(e))
                if card.image:
                    stats["images"]["found"] += 1
                    _record(db, "info", "image_generated", card.id)
                else:
                    stats["images"]["failed_no_result"] += 1

            for card in cards:
                if card.pos.value not in cfg.images.only_for_pos:
                    stats["images"]["skipped_not_noun"] += 1
                else:
                    tasks.append(_attach_image_one(card))

        await asyncio.gather(*tasks)

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
    """Отправить все approved карточки активного языка в Anki. Вернуть количество."""
    approved = db.get_by_status(Status.APPROVED, cfg.language)
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


async def check_level_progress(db: Database, anki: AnkiConnect, cfg: Config) -> list[dict]:
    """Для каждого уровня с запушенными карточками проверить, не пора ли предложить
    следующий уровень: минимум level_progress_min_cards карточек в Anki И доля с
    interval >= level_progress_mature_interval_days дней >= level_progress_mature_ratio.

    SQLite-подсчёт (db.count_by_level) — дешёвый ранний выход без похода в сеть; сама
    проверка "зрелости" идёт по данным из Anki (source of truth, не SQLite — см.
    CLAUDE.md принцип 1)."""
    ic = cfg.ingest
    order = list(Level)
    totals = db.count_by_level(cfg.language)
    hints = []
    deck = resolve_anki_profile(cfg).deck_name
    for level in order[:-1]:  # C2 — расти уже некуда
        min_cards = ic.level_progress_min_cards.get(level.value)
        if min_cards is None:
            continue
        pushed = totals.get(level.value, {}).get(Status.PUSHED.value, 0)
        if pushed < min_cards:
            continue
        card_ids = await anki.find_cards(f'deck:"{deck}" tag:level::{level.value}')
        if len(card_ids) < min_cards:
            continue
        infos = await anki.cards_info(card_ids)
        mature = sum(
            1 for c in infos if c.get("interval", 0) >= ic.level_progress_mature_interval_days
        )
        ratio = mature / len(infos) if infos else 0.0
        if ratio >= ic.level_progress_mature_ratio:
            next_level = order[order.index(level) + 1]
            hints.append(
                {
                    "level": level.value,
                    "next_level": next_level.value,
                    "total": len(infos),
                    "mature": mature,
                    "mature_ratio": round(ratio, 2),
                }
            )
    return hints


def compute_streak(db: Database, today: date | None = None) -> int:
    """Consecutive days up to `today` (default: real today) with >=1 card pushed.

    If nothing has been pushed yet today, counts back from yesterday instead of
    reporting 0 — otherwise every streak would look broken each morning before
    that day's daily-automation run has had a chance to push anything (see
    scripts/daily_topic.py). Based on db.count_pushed_by_date (audit_log), not
    Card.date_added — see that method's docstring for why.
    """
    pushed_by_date = db.count_pushed_by_date()
    today = today or date.today()
    day = today if pushed_by_date.get(today.isoformat()) else today - timedelta(days=1)

    streak = 0
    while pushed_by_date.get(day.isoformat()):
        streak += 1
        day -= timedelta(days=1)
    return streak
