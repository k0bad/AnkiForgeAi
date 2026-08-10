"""SQLite layer: staging кандидатов, audit log, кэш заметок Anki.

Три основные таблицы:
- cards         — все карточки (pending / review / approved / pushed / ...)
- audit_log     — каждое действие (create / merge / skip / push / ...)
- anki_cache    — снимок заметок из Anki (для быстрой дедупликации)

Все операции выполняются в транзакциях через контекстный менеджер.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .models import Card, Status

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id                  TEXT PRIMARY KEY,
    word                TEXT NOT NULL,
    pronunciation       TEXT,
    translation         TEXT NOT NULL,
    example             TEXT,
    example_translation TEXT,
    pos                 TEXT NOT NULL,
    forms               TEXT,           -- JSON
    level               TEXT,
    topic               TEXT,
    source              TEXT,
    image               TEXT,
    audio               TEXT,
    tags                TEXT,           -- JSON list
    status              TEXT NOT NULL DEFAULT 'pending',
    date_added          TEXT NOT NULL,
    anki_note_id        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cards_word   ON cards(word);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_topic  ON cards(topic);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    action      TEXT NOT NULL,          -- create / merge / skip / push / sync / ...
    card_id     TEXT,
    details     TEXT                    -- JSON
);
CREATE INDEX IF NOT EXISTS idx_audit_card ON audit_log(card_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts   ON audit_log(timestamp);

CREATE TABLE IF NOT EXISTS anki_cache (
    note_id     INTEGER PRIMARY KEY,
    word        TEXT NOT NULL,
    fields      TEXT NOT NULL,          -- JSON всех полей
    tags        TEXT,                   -- JSON list
    synced_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anki_word ON anki_cache(word);
"""


class Database:
    """Тонкая обёртка над sqlite3 с явными транзакциями."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Соединение с авто-commit/rollback и Row factory."""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ───────────── Cards ─────────────

    def insert_card(self, card: Card) -> None:
        """Вставить новую карточку."""
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO cards (
                    id, word, pronunciation, translation, example, example_translation,
                    pos, forms, level, topic, source, image, audio, tags, status,
                    date_added, anki_note_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    card.id,
                    card.word,
                    card.pronunciation,
                    card.translation,
                    card.example,
                    card.example_translation,
                    card.pos.value,
                    json.dumps(card.forms) if card.forms else None,
                    card.level.value if card.level else None,
                    card.topic,
                    card.source,
                    card.image,
                    card.audio,
                    json.dumps(card.tags),
                    card.status.value,
                    card.date_added.isoformat(),
                    card.anki_note_id,
                ),
            )

    def update_status(self, card_id: str, status: Status) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE cards SET status = ? WHERE id = ?",
                (status.value, card_id),
            )

    def get_by_status(self, status: Status) -> list[Card]:
        """Все карточки с заданным статусом."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cards WHERE status = ? ORDER BY date_added",
                (status.value,),
            ).fetchall()
        return [_row_to_card(r) for r in rows]

    def get_by_id(self, card_id: str) -> Card | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return _row_to_card(row) if row else None

    def all_words(self) -> list[tuple[str, str]]:
        """Список (id, word) всех карточек — для быстрой дедупликации."""
        with self.connect() as conn:
            rows = conn.execute("SELECT id, word FROM cards").fetchall()
        return [(r["id"], r["word"]) for r in rows]

    # ───────────── Audit ─────────────

    def log_action(self, action: str, card_id: str | None, details: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (timestamp, action, card_id, details) VALUES (?, ?, ?, ?)",
                (
                    datetime.utcnow().isoformat(timespec="seconds"),
                    action,
                    card_id,
                    json.dumps(details, ensure_ascii=False),
                ),
            )

    # ───────────── Anki cache ─────────────

    def upsert_anki_note(self, note_id: int, word: str, fields: dict, tags: list[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO anki_cache (note_id, word, fields, tags, synced_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(note_id) DO UPDATE SET
                       word=excluded.word,
                       fields=excluded.fields,
                       tags=excluded.tags,
                       synced_at=excluded.synced_at""",
                (
                    note_id,
                    word,
                    json.dumps(fields, ensure_ascii=False),
                    json.dumps(tags),
                    datetime.utcnow().isoformat(timespec="seconds"),
                ),
            )

    def all_anki_words(self) -> list[tuple[int, str]]:
        """Список (note_id, word) из кэша Anki."""
        with self.connect() as conn:
            rows = conn.execute("SELECT note_id, word FROM anki_cache").fetchall()
        return [(r["note_id"], r["word"]) for r in rows]


def _row_to_card(row: sqlite3.Row) -> Card:
    """Восстановить Card из строки SQLite."""
    from datetime import date as date_cls

    return Card(
        id=row["id"],
        word=row["word"],
        pronunciation=row["pronunciation"],
        translation=row["translation"],
        example=row["example"],
        example_translation=row["example_translation"],
        pos=row["pos"],
        forms=json.loads(row["forms"]) if row["forms"] else None,
        level=row["level"],
        topic=row["topic"],
        source=row["source"],
        image=row["image"],
        audio=row["audio"],
        tags=json.loads(row["tags"]) if row["tags"] else [],
        status=row["status"],
        date_added=date_cls.fromisoformat(row["date_added"]),
        anki_note_id=row["anki_note_id"],
    )
