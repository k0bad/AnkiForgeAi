"""Разовая миграция cards.id из UUID (TEXT) в последовательные целые числа.

Запускается командой `ankiforgeai migrate-ids`. До этой миграции card.id был
случайным UUID4 (см. models.py); теперь — наименьший свободный положительный
int (db.py::Database._next_free_id). Эта миграция переносит уже существующие
карточки на новую схему одним разом: 1..N в порядке rowid, то есть в порядке
их фактического создания.

Работает напрямую через sqlite3, в обход Database — Database._migrate() сама
отказывается открывать БД со старой (TEXT) схемой id (см. IdMigrationRequiredError
в db.py), а этой миграции как раз нужно открыть именно такую базу, чтобы её
поправить.

Не трогает audio/image: имена медиафайлов фиксируются один раз при генерации
(media/tts.py, media/images.py) и никогда не перевычисляются из card.id, а
Anki хранит собственную копию файла в collection.media под тем же именем —
так что уже запушенные карточки продолжают работать без переименования файлов.
Единственное, что меняется в Anki, — скрытое поле ID на каждой уже запушенной
заметке (через updateNoteFields).

Идемпотентна: если cards.id уже INTEGER, needs_migration() вернёт False и
migrate_ids() ничего не сделает.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .anki.connect import AnkiConnect, AnkiConnectError
from .log import get_logger

logger = get_logger(__name__)

# Порядок совпадает с cards.* в SCHEMA (db.py), минус id — он выделяется заново.
_CARDS_COLUMNS = (
    "word",
    "pronunciation",
    "translation",
    "image_query",
    "example",
    "example_translation",
    "pos",
    "forms",
    "level",
    "topic",
    "source",
    "image",
    "audio",
    "tags",
    "status",
    "date_added",
    "anki_note_id",
)


@dataclass
class MigrationResult:
    already_current: bool = False
    backup_path: Path | None = None
    cards_migrated: int = 0
    audit_rows_remapped: int = 0
    anki_updated: int = 0
    anki_failed: list[tuple[int, str]] = field(default_factory=list)


def needs_migration(db_path: Path) -> bool:
    """cards.id ещё TEXT (старые UUID) — миграция не выполнена."""
    if not db_path.exists():
        return False
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(cards)")}
    finally:
        conn.close()
    return str(cols.get("id", "")).upper() == "TEXT"


def _backup(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}.backup-{stamp}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _rebuild_sqlite(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], dict[str, int], int]:
    """Перенумеровать cards в 1..N (порядок rowid) и remap audit_log.card_id.
    Выполняется в одной транзакции. Возвращает (old_cards, mapping, remapped)."""
    old_cards = conn.execute("SELECT rowid AS _rowid, * FROM cards ORDER BY rowid").fetchall()
    mapping: dict[str, int] = {row["id"]: i + 1 for i, row in enumerate(old_cards)}

    conn.execute("BEGIN IMMEDIATE")

    conn.execute(
        """CREATE TABLE cards_new (
            id                  INTEGER PRIMARY KEY,
            word                TEXT NOT NULL,
            pronunciation       TEXT,
            translation         TEXT NOT NULL,
            image_query         TEXT,
            example             TEXT,
            example_translation TEXT,
            pos                 TEXT NOT NULL,
            forms               TEXT,
            level               TEXT,
            topic               TEXT,
            source              TEXT,
            image               TEXT,
            audio               TEXT,
            tags                TEXT,
            status              TEXT NOT NULL DEFAULT 'pending',
            date_added          TEXT NOT NULL,
            anki_note_id        INTEGER
        )"""
    )
    insert_cols = ", ".join(_CARDS_COLUMNS)
    placeholders = ", ".join("?" for _ in _CARDS_COLUMNS)
    for row in old_cards:
        conn.execute(
            f"INSERT INTO cards_new (id, {insert_cols}) VALUES (?, {placeholders})",
            (mapping[row["id"]], *(row[c] for c in _CARDS_COLUMNS)),
        )
    conn.execute("DROP TABLE cards")
    conn.execute("ALTER TABLE cards_new RENAME TO cards")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_word   ON cards(word)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_topic  ON cards(topic)")

    old_audit = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    conn.execute(
        """CREATE TABLE audit_log_new (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            action      TEXT NOT NULL,
            card_id     INTEGER,
            details     TEXT,
            run_id      TEXT
        )"""
    )
    remapped = 0
    for row in old_audit:
        old_card_id = row["card_id"]
        new_card_id = mapping.get(old_card_id) if old_card_id is not None else None
        if new_card_id is not None:
            remapped += 1
        conn.execute(
            """INSERT INTO audit_log_new (id, timestamp, action, card_id, details, run_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["timestamp"],
                row["action"],
                new_card_id,
                row["details"],
                row["run_id"],
            ),
        )
    conn.execute("DROP TABLE audit_log")
    conn.execute("ALTER TABLE audit_log_new RENAME TO audit_log")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_card ON audit_log(card_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts   ON audit_log(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_run  ON audit_log(run_id)")

    return old_cards, mapping, remapped


async def migrate_ids(db_path: Path, anki: AnkiConnect) -> MigrationResult:
    """Перенумеровать cards.id в 1..N (порядок rowid), remap audit_log.card_id,
    и обновить скрытое поле ID на уже запушенных заметках в Anki. Резервная
    копия БД делается перед любыми изменениями."""
    if not needs_migration(db_path):
        return MigrationResult(already_current=True)

    backup_path = _backup(db_path)
    logger.info("migrate_ids.backup", path=str(backup_path))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        old_cards, mapping, remapped = _rebuild_sqlite(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    result = MigrationResult(
        backup_path=backup_path,
        cards_migrated=len(old_cards),
        audit_rows_remapped=remapped,
    )

    for row in old_cards:
        note_id = row["anki_note_id"]
        if note_id is None:
            continue
        new_id = mapping[row["id"]]
        try:
            await anki.update_note_fields(note_id, {"ID": str(new_id)})
            result.anki_updated += 1
        except AnkiConnectError as e:
            result.anki_failed.append((new_id, str(e)))
            logger.warning(
                "migrate_ids.anki_update_failed", new_id=new_id, note_id=note_id, error=str(e)
            )

    return result
