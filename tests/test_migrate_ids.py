"""Тесты для migrate_ids: разовая миграция card.id UUID -> последовательные int.

Строит "старую" БД вручную (id TEXT — схема до этой версии), запускает
migrate_ids() против нее с поддельным AnkiConnect и проверяет:
- id становятся 1..N в порядке rowid (то есть в порядке создания)
- audit_log.card_id переносится на новые id (записи без соответствующей
  карточки — например, за уже отклонённый на dedupe дубликат — теряют
  ссылку, card_id и так nullable)
- updateNoteFields зовётся по разу на каждую уже запушенную карточку с новым id
- повторный запуск — no-op (already_current=True)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ankicards.migrate_ids import migrate_ids, needs_migration


def _make_legacy_db(path: Path) -> None:
    """Схема "до миграции": id TEXT, как её создавал старый Database.SCHEMA."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE cards (
                id                  TEXT PRIMARY KEY,
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
            );
            CREATE TABLE audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                action      TEXT NOT NULL,
                card_id     TEXT,
                details     TEXT,
                run_id      TEXT
            );
            """
        )
        # Порядок вставки задаёт rowid, а значит и ожидаемую новую нумерацию (1, 2, 3).
        insert_card = (
            "INSERT INTO cards "
            "(id, word, translation, pos, tags, status, date_added, anki_note_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        conn.execute(
            insert_card, ("uuid-a", "hus", "дом", "noun", "[]", "pushed", "2026-01-01", 111)
        )
        conn.execute(
            insert_card, ("uuid-b", "bil", "машина", "noun", "[]", "pushed", "2026-01-02", 222)
        )
        conn.execute(
            insert_card, ("uuid-c", "katt", "кот", "noun", "[]", "pending", "2026-01-03", None)
        )
        # Обычная запись + одна "осиротевшая" — как за дубликат, отклонённый на
        # dedupe ещё до вставки в cards (см. pipeline.run_ingest_pipeline).
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, card_id, details, run_id) "
            "VALUES ('2026-01-01T00:00:00', 'create', 'uuid-a', '{}', 'run1')"
        )
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, card_id, details, run_id) "
            "VALUES ('2026-01-01T00:00:01', 'skip_duplicate', 'uuid-ghost', '{}', 'run1')"
        )
        conn.commit()
    finally:
        conn.close()


class _FakeAnki:
    def __init__(self) -> None:
        self.updated: list[tuple[int, dict]] = []

    async def update_note_fields(self, note_id: int, fields: dict) -> None:
        self.updated.append((note_id, fields))


def test_needs_migration_true_for_legacy_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(db_path)
    assert needs_migration(db_path) is True


def test_needs_migration_false_when_missing(tmp_path: Path) -> None:
    assert needs_migration(tmp_path / "nope.db") is False


async def test_migrate_ids_renumbers_in_rowid_order(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(db_path)

    anki = _FakeAnki()
    result = await migrate_ids(db_path, anki)  # type: ignore[arg-type]

    assert result.already_current is False
    assert result.cards_migrated == 3
    assert result.backup_path is not None
    assert result.backup_path.exists()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT id, word FROM cards ORDER BY id").fetchall()
        assert [(r["id"], r["word"]) for r in rows] == [(1, "hus"), (2, "bil"), (3, "katt")]

        col_types = {r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(cards)")}
        assert col_types["id"].upper() == "INTEGER"
    finally:
        conn.close()

    assert needs_migration(db_path) is False


async def test_migrate_ids_remaps_audit_log_and_drops_orphan_refs(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(db_path)

    await migrate_ids(db_path, _FakeAnki())  # type: ignore[arg-type]

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = {
            r["action"]: r["card_id"] for r in conn.execute("SELECT action, card_id FROM audit_log")
        }
    finally:
        conn.close()

    assert rows["create"] == 1  # uuid-a -> новый id 1
    assert rows["skip_duplicate"] is None  # uuid-ghost никогда не была в cards


async def test_migrate_ids_updates_anki_hidden_id_field_for_pushed_cards(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(db_path)

    anki = _FakeAnki()
    await migrate_ids(db_path, anki)  # type: ignore[arg-type]

    # Только 2 из 3 карточек были запушены (anki_note_id задан) — третья pending.
    assert dict(anki.updated) == {111: {"ID": "1"}, 222: {"ID": "2"}}


async def test_migrate_ids_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(db_path)

    await migrate_ids(db_path, _FakeAnki())  # type: ignore[arg-type]
    second = await migrate_ids(db_path, _FakeAnki())  # type: ignore[arg-type]

    assert second.already_current is True
    assert second.cards_migrated == 0
