"""Полная синхронизация кэша заметок из Anki в локальную SQLite.

Запускается командой `ankiforgeai sync`.
Не источник истины — только для быстрой дедупликации.
"""

from __future__ import annotations

from ..config import Config
from ..db import Database
from ..log import get_logger
from .connect import AnkiConnect

logger = get_logger(__name__)

BATCH_SIZE = 200


async def sync_anki_to_cache(db: Database, anki: AnkiConnect, cfg: Config) -> int:
    """Скачать все заметки из Anki deck в anki_cache. Вернуть количество."""
    query = f'deck:"{cfg.anki.deck_name}"'
    logger.info("anki_sync.start", deck=cfg.anki.deck_name)
    note_ids = await anki.find_notes(query)
    if not note_ids:
        logger.info("anki_sync.done", deck=cfg.anki.deck_name, count=0)
        return 0

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
                word=word,
                fields=fields,
                tags=list(tags),
            )
            total += 1
    logger.info("anki_sync.done", deck=cfg.anki.deck_name, count=total)
    return total
