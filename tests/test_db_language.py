"""Тесты для языковой изоляции в db.py (issue #63).

Покрыть:
- all_words / all_anki_words / get_by_status / count_by_level / count_cards_with_anki_note_id
  сужаются по language, когда он передан, и остаются сводными (все языки), когда нет
- миграция БД, созданной до issue #63 (нет колонки cards.language): колонка
  добавляется и существующие строки бэкафиллятся default_language, событие
  db.language_backfilled логируется
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import structlog

from ankicards.db import Database
from ankicards.models import POS, Card, Level, Status


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


def _card(language: str, word: str, level: Level | None = None) -> Card:
    return Card(language=language, word=word, pos=POS.NOUN, translation="перевод", level=level)


def test_all_words_filters_by_language(db: Database) -> None:
    db.insert_card(_card("nb", "hus"))
    db.insert_card(_card("de", "Haus"))

    assert [w for _, w in db.all_words("nb")] == ["hus"]
    assert [w for _, w in db.all_words("de")] == ["Haus"]
    assert {w for _, w in db.all_words()} == {"hus", "Haus"}  # без фильтра — оба языка


def test_all_anki_words_filters_by_language(db: Database) -> None:
    db.upsert_anki_note(note_id=1, language="nb", word="hus", fields={}, tags=[])
    db.upsert_anki_note(note_id=2, language="de", word="Haus", fields={}, tags=[])

    assert db.all_anki_words("nb") == [(1, "hus")]
    assert db.all_anki_words("de") == [(2, "Haus")]
    assert set(db.all_anki_words()) == {(1, "hus"), (2, "Haus")}


def test_get_by_status_filters_by_language(db: Database) -> None:
    nb_card = _card("nb", "hus")
    de_card = _card("de", "Haus")
    db.insert_card(nb_card)
    db.insert_card(de_card)

    assert [c.id for c in db.get_by_status(Status.PENDING, "nb")] == [nb_card.id]
    assert [c.id for c in db.get_by_status(Status.PENDING, "de")] == [de_card.id]
    assert {c.id for c in db.get_by_status(Status.PENDING)} == {nb_card.id, de_card.id}


def test_count_by_level_filters_by_language(db: Database) -> None:
    db.insert_card(_card("nb", "hus", level=Level.A1))
    db.insert_card(_card("nb", "bil", level=Level.A1))
    db.insert_card(_card("de", "Haus", level=Level.A1))

    assert db.count_by_level("nb")["a1"][Status.PENDING.value] == 2
    assert db.count_by_level("de")["a1"][Status.PENDING.value] == 1
    assert db.count_by_level()["a1"][Status.PENDING.value] == 3


def test_count_cards_with_anki_note_id_filters_by_language(db: Database) -> None:
    nb_card = _card("nb", "hus")
    de_card = _card("de", "Haus")
    db.insert_card(nb_card)
    db.insert_card(de_card)
    with db.connect() as conn:
        conn.execute("UPDATE cards SET anki_note_id = 1 WHERE id = ?", (nb_card.id,))
        conn.execute("UPDATE cards SET anki_note_id = 2 WHERE id = ?", (de_card.id,))

    assert db.count_cards_with_anki_note_id("nb") == 1
    assert db.count_cards_with_anki_note_id("de") == 1
    assert db.count_cards_with_anki_note_id() == 2


_OLD_SCHEMA = """
CREATE TABLE cards (
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
);
CREATE TABLE anki_cache (
    note_id     INTEGER PRIMARY KEY,
    word        TEXT NOT NULL,
    fields      TEXT NOT NULL,
    tags        TEXT,
    synced_at   TEXT NOT NULL
);
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    action      TEXT NOT NULL,
    card_id     INTEGER,
    details     TEXT,
    run_id      TEXT
);
"""


def _make_pre_language_db(path: Path) -> None:
    """Симулирует БД, созданную до issue #63 — нет колонки cards.language/
    anki_cache.language, ровно то состояние, что встретит Database._migrate()
    у существующего пользователя после апгрейда."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO cards (id, word, translation, pos, status, date_added) "
            "VALUES (1, 'hus', 'дом', 'noun', 'pending', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO anki_cache (note_id, word, fields, tags, synced_at) "
            "VALUES (1, 'hus', '{}', '[]', '2026-01-01T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def test_migration_backfills_language_on_pre_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _make_pre_language_db(path)

    with structlog.testing.capture_logs() as cap:
        db = Database(path, default_language="de")

    card = db.get_by_id(1)
    assert card is not None
    assert card.language == "de"
    assert db.all_anki_words("de") == [(1, "hus")]

    events = [e for e in cap if e.get("event") == "db.language_backfilled"]
    assert {e["table"] for e in events} == {"cards", "anki_cache"}
    assert all(e["assumed_language"] == "de" for e in events)
    assert all(e["count"] == 1 for e in events)


def test_migration_is_idempotent_and_quiet_on_second_open(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _make_pre_language_db(path)
    Database(path, default_language="de")  # первый запуск — бэкафилл

    with structlog.testing.capture_logs() as cap:
        Database(path, default_language="nb")  # второй открытие — уже не NULL

    events = [e for e in cap if e.get("event") == "db.language_backfilled"]
    assert events == []  # ничего не перезаписано вторым default_language

    db = Database(path, default_language="nb")
    card = db.get_by_id(1)
    assert card is not None
    assert card.language == "de"  # осталось от первого бэкафилла, не "nb"
