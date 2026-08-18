"""Полная синхронизация кэша заметок из Anki в локальную SQLite.

Запускается командой `ankiforgeai sync`.
Не источник истины — только для быстрой дедупликации.

Дополнительно ловит заметки, удалённые прямо в Anki (не через `ankiforgeai
delete`): если note_id, который раньше был в anki_cache и совпадает с чьей-то
cards.anki_note_id, пропадает из свежего findNotes() — карточка считается
удалённой, и её номер освобождается для переиспользования (см.
pipeline.delete_card_record). Ложные срабатывания от смены deck_name/поисковой
строки гасит _MASS_DELETE_GUARD_RATIO — см. _handle_vanished_notes.
"""

from __future__ import annotations

from ..config import Config, resolve_anki_profile
from ..db import Database
from ..log import get_logger
from ..pipeline import delete_card_record
from .connect import AnkiConnect

logger = get_logger(__name__)

BATCH_SIZE = 200

# Guard срабатывает, только если пропало ХОТЯ БЫ _MASS_DELETE_MIN_COUNT
# привязанных заметок И это больше _MASS_DELETE_GUARD_RATIO от всех карточек
# с anki_note_id — порог по количеству нужен отдельно от доли, иначе при 1-2
# отслеживаемых карточках обычное единичное удаление (100% от такой мелкой
# базы) само выглядело бы как "массовое" и блокировалось бы ложно.
_MASS_DELETE_GUARD_RATIO = 0.5
_MASS_DELETE_MIN_COUNT = 3


async def sync_anki_to_cache(db: Database, anki: AnkiConnect, cfg: Config) -> int:
    """Скачать все заметки из Anki deck в anki_cache. Вернуть количество."""
    deck = resolve_anki_profile(cfg).deck_name
    query = f'deck:"{deck}"'
    logger.info("anki_sync.start", deck=deck)

    # Фильтр по языку (issue #63) — иначе кэш других языков виделся бы здесь как
    # "исчезнувший" на каждом sync: fresh_note_ids ограничен этим deck'ом (query
    # выше), а без фильтра previous_note_ids тянет ноуты вообще всех языков.
    previous_note_ids = {note_id for note_id, _ in db.all_anki_words(cfg.language)}
    note_ids = await anki.find_notes(query)
    fresh_note_ids = {int(nid) for nid in note_ids}

    total = 0
    for start in range(0, len(note_ids), BATCH_SIZE):
        chunk = note_ids[start : start + BATCH_SIZE]
        infos = await anki.notes_info(chunk)
        for info in infos:
            note_id = info.get("noteId")
            if note_id is None:
                logger.warning("anki_sync.note_missing_id", info=info)
                continue
            raw_fields = info.get("fields", {})
            fields = {name: entry.get("value", "") for name, entry in raw_fields.items()}
            word = fields.get("Word", "").strip()
            tags = info.get("tags", []) or []
            db.upsert_anki_note(
                note_id=int(note_id),
                language=cfg.language,
                word=word,
                fields=fields,
                tags=list(tags),
            )
            total += 1

    _handle_vanished_notes(previous_note_ids - fresh_note_ids, db, language=cfg.language)

    logger.info("anki_sync.done", deck=deck, count=total)
    return total


def _handle_vanished_notes(vanished: set[int], db: Database, language: str) -> None:
    """note_id, которые раньше были в кэше и пропали из свежего findNotes().

    Кэш для них чистится всегда. Если пропавший note_id привязан к одной из
    наших карточек (cards.anki_note_id) — это, вероятно, значит, что заметку
    удалили прямо в Anki; карточка удаляется локально и её номер освобождается,
    если это не выглядит как массовая (скорее всего ложная) волна удалений.
    """
    if not vanished:
        return

    for note_id in vanished:
        db.purge_anki_cache([note_id])

    linked_cards = [
        card for card in (db.get_by_anki_note_id(nid) for nid in vanished) if card is not None
    ]
    if not linked_cards:
        return

    # language=... (issue #63): без фильтра по языку массовое удаление ноутов
    # ОДНОГО языка размывается знаменателем по всем языкам в базе — guard может
    # не сработать, когда как раз должен (см. count_cards_with_anki_note_id).
    total_tracked = db.count_cards_with_anki_note_id(language)
    if (
        len(linked_cards) >= _MASS_DELETE_MIN_COUNT
        and total_tracked
        and len(linked_cards) > total_tracked * _MASS_DELETE_GUARD_RATIO
    ):
        logger.warning(
            "anki_sync.mass_deletion_guard",
            vanished_linked=len(linked_cards),
            total_tracked=total_tracked,
            hint="возможно сменился anki.deck_name/поисковый запрос — карточки НЕ удалены локально",
        )
        return

    for card in linked_cards:
        logger.info("anki_sync.note_deleted_in_anki", card_id=card.id, word=card.word)
        delete_card_record(db, card, action="delete_detected_in_anki")
