"""Оркестратор pipeline: связывает все стадии.

Pipeline для одного потока кандидатов:
    Ingest → Normalize → Dedupe → (Review если нужно) → Enrich → Media → DB

Команда `push` — отдельный шаг: approved → Anki.
Команда `sync` — отдельный шаг: Anki → cache.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .anki.connect import AnkiConnect, AnkiConnectError
from .anki.notetype import card_to_anki_fields
from .config import Config
from .db import Database
from .dedupe import check_card, judge_review
from .enrich.examples import enrich_example_batch
from .enrich.grammar import INFLECTED_POS, enrich_grammar_batch
from .enrich.pronunciation import enrich_pronunciation_batch
from .enrich.translation import enrich_translation
from .media.images import attach_image
from .media.tts import generate_audio
from .models import Card, Status


async def _run_enrich_stage(
    stage: str,
    fn: Callable[[list[Card]], Awaitable[list[Card]]],
    cards: list[Card],
    is_complete: Callable[[Card], bool],
    db: Database,
    stats: dict,
    incomplete_ids: set[str],
) -> None:
    """Прогнать один batch-вызов enrichment; отследить провал и неполный результат.

    Провал самого вызова (сеть/парсинг/LLM) бьёт по всем cards в batch — это одна
    LLM-задача на всех. Но даже при успешном вызове LLM мог не вернуть данные для
    части карточек (id отсутствует в ответе) — это тоже неполный enrichment,
    просто без исключения, так что проверяем результат отдельно.
    """
    try:
        await fn(cards)
    except Exception as e:
        stats["errors"] += 1
        for card in cards:
            db.log_action(
                "enrich_failed",
                card_id=card.id,
                details={"stage": stage, "error": str(e)},
            )
        incomplete_ids.update(c.id for c in cards)
        return

    for card in cards:
        if not is_complete(card):
            incomplete_ids.add(card.id)
            db.log_action(
                "enrich_incomplete",
                card_id=card.id,
                details={"stage": stage},
            )


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
            db.log_action(
                "skip_duplicate",
                card_id=card.id,
                details={
                    "word": card.word,
                    "matches": [m.model_dump() for m in decision.matches],
                    "reason": decision.reason,
                },
            )
            stats["merged"] += 1
            continue
        if decision.decision == "review":
            card.status = Status.REVIEW
            db.insert_card(card)
            db.log_action(
                "review_needed",
                card_id=card.id,
                details={
                    "matches": [m.model_dump() for m in decision.matches],
                    "reason": decision.reason,
                },
            )
            stats["review"] += 1
            continue
        accepted.append(card)
        stats["new"] += 1

    incomplete_ids: set[str] = set()

    if auto_enrich and accepted:
        # Сначала транскрипция, потом перевод, грамматика и примеры.
        # Каждая стадия — свой try/except: одна упавшая стадия не должна
        # маскировать успех/провал остальных, и каждая карточка, которую
        # LLM не обогатил, должна быть видна в audit_log по своему card_id
        # вместо того, чтобы молча уйти в APPROVED.
        if cfg.enrich.pronunciation:
            await _run_enrich_stage(
                "pronunciation",
                enrich_pronunciation_batch,
                accepted,
                lambda c: bool(c.pronunciation),
                db,
                stats,
                incomplete_ids,
            )

        for card in accepted:
            if card.translation:
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
                incomplete_ids.add(card.id)
            else:
                if not card.translation:
                    incomplete_ids.add(card.id)
                    db.log_action(
                        "enrich_incomplete",
                        card_id=card.id,
                        details={"stage": "translation"},
                    )

        if cfg.enrich.grammar:
            await _run_enrich_stage(
                "grammar",
                enrich_grammar_batch,
                accepted,
                lambda c: c.pos not in INFLECTED_POS or bool(c.forms),
                db,
                stats,
                incomplete_ids,
            )

        if cfg.enrich.examples:
            await _run_enrich_stage(
                "examples",
                enrich_example_batch,
                accepted,
                lambda c: bool(c.example),
                db,
                stats,
                incomplete_ids,
            )

        stats["enriched"] = len(accepted) - len(incomplete_ids)
        stats["enrich_incomplete"] = len(incomplete_ids)

    if auto_media and accepted:
        for card in accepted:
            try:
                await generate_audio(card, cfg)
                stats["audio"] += 1
            except Exception as e:
                stats["errors"] += 1
                db.log_action(
                    "audio_failed",
                    card_id=card.id,
                    details={"error": str(e)},
                )
        # Картинки для существительных (если включено в конфиге)
        if cfg.images.enabled:
            for card in accepted:
                try:
                    await attach_image(card, cfg, auto_pick=True)
                    if card.image:
                        stats.setdefault("images", 0)
                        stats["images"] += 1
                except Exception as e:
                    db.log_action(
                        "image_failed",
                        card_id=card.id,
                        details={"error": str(e)},
                    )

    for card in accepted:
        card.status = Status.REVIEW if card.id in incomplete_ids else Status.APPROVED
        db.insert_card(card)
        db.log_action("create", card_id=card.id, details={"word": card.word})

    return stats


async def push_approved(db: Database, anki: AnkiConnect, cfg: Config) -> int:
    """Отправить все approved карточки в Anki. Вернуть количество."""
    approved = db.get_by_status(Status.APPROVED)
    if not approved:
        return 0

    await anki.ensure_deck()
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
            db.log_action("push", card_id=card.id, details={"note_id": note_id})
            pushed += 1
        except AnkiConnectError as e:
            db.log_action("push_failed", card_id=card.id, details={"error": str(e)})
    return pushed
