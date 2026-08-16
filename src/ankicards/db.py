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
from datetime import UTC, datetime
from pathlib import Path

import structlog

from .models import Card, Status


class IdMigrationRequiredError(RuntimeError):
    """cards.id всё ещё TEXT (старые UUID) — нужно запустить `ankiforgeai migrate-ids`
    перед тем, как эта версия сможет работать с базой (см. migrate_ids.py)."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id                  INTEGER PRIMARY KEY,
    word                TEXT NOT NULL,
    pronunciation       TEXT,
    translation         TEXT NOT NULL,
    image_query         TEXT,           -- англ. gloss для поиска картинок (issue #10)
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
    card_id     INTEGER,
    details     TEXT,                   -- JSON
    run_id      TEXT                    -- одна CLI-команда = один run_id (см. log.bound_run)
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
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Догнать схему существующих БД, созданных до текущей версии SCHEMA."""
        card_info = {row["name"]: row for row in conn.execute("PRAGMA table_info(cards)")}
        id_col = card_info.get("id")
        if id_col is not None and str(id_col["type"]).upper() == "TEXT":
            raise IdMigrationRequiredError(
                "cards.id ещё TEXT (старые UUID-идентификаторы) — запусти "
                "`ankiforgeai migrate-ids`, чтобы перенумеровать карточки в "
                "последовательные целые числа, и повтори команду."
            )

        cols = {row["name"] for row in conn.execute("PRAGMA table_info(audit_log)")}
        if "run_id" not in cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN run_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id)")

        if "image_query" not in card_info:
            conn.execute("ALTER TABLE cards ADD COLUMN image_query TEXT")

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

    def _next_free_id(self, conn: sqlite3.Connection) -> int:
        """Наименьший свободный положительный int — переиспользует "дыры",
        оставшиеся после delete_card(), вместо бесконечного роста номера."""
        row = conn.execute(
            """SELECT MIN(t.id) + 1 AS next_free
               FROM (SELECT 0 AS id UNION SELECT id FROM cards) t
               WHERE NOT EXISTS (SELECT 1 FROM cards t2 WHERE t2.id = t.id + 1)"""
        ).fetchone()
        return int(row["next_free"])

    def insert_card(self, card: Card) -> None:
        """Вставить новую карточку, выделив ей наименьший свободный номер (мутирует card.id)."""
        with self.connect() as conn:
            card.id = self._next_free_id(conn)
            conn.execute(
                """INSERT INTO cards (
                    id, word, pronunciation, translation, image_query, example,
                    example_translation, pos, forms, level, topic, source, image, audio,
                    tags, status, date_added, anki_note_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    card.id,
                    card.word,
                    card.pronunciation,
                    card.translation,
                    card.image_query,
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

    def update_status(self, card_id: int, status: Status) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE cards SET status = ? WHERE id = ?",
                (status.value, card_id),
            )

    def update_card(self, card: Card) -> None:
        """Перезаписать поля, которые может изменить enrichment/media (+ status).

        Для карточек, уже существующих в БД (обычно review → accept, см. issue #11) —
        insert_card() тут не годится, он INSERT, а не UPDATE.
        """
        with self.connect() as conn:
            conn.execute(
                """UPDATE cards SET
                    pronunciation = ?, translation = ?, example = ?, example_translation = ?,
                    forms = ?, image = ?, audio = ?, status = ?
                   WHERE id = ?""",
                (
                    card.pronunciation,
                    card.translation,
                    card.example,
                    card.example_translation,
                    json.dumps(card.forms) if card.forms else None,
                    card.image,
                    card.audio,
                    card.status.value,
                    card.id,
                ),
            )

    def get_by_status(self, status: Status) -> list[Card]:
        """Все карточки с заданным статусом."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cards WHERE status = ? ORDER BY date_added",
                (status.value,),
            ).fetchall()
        return [_row_to_card(r) for r in rows]

    def get_by_id(self, card_id: int) -> Card | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return _row_to_card(row) if row else None

    def get_by_anki_note_id(self, note_id: int) -> Card | None:
        """Найти карточку по anki_note_id — используется sync.py, чтобы понять,
        какая (если вообще) наша карточка стоит за исчезнувшей заметкой Anki."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM cards WHERE anki_note_id = ?", (note_id,)).fetchone()
        return _row_to_card(row) if row else None

    def count_cards_with_anki_note_id(self) -> int:
        """Сколько карточек вообще привязаны к заметке в Anki — знаменатель для
        guard'а против массового ложного удаления в sync.py (см. _handle_vanished_notes)."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM cards WHERE anki_note_id IS NOT NULL"
            ).fetchone()
        return int(row["n"])

    def delete_card(self, card_id: int) -> None:
        """Удалить карточку насовсем — освобождает её номер для переиспользования
        (см. review/actions.py::delete_cards и anki/sync.py — вызывается либо по
        явной команде `ankiforgeai delete`, либо когда sync обнаружит, что
        соответствующая заметка исчезла из Anki)."""
        with self.connect() as conn:
            conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))

    def all_words(self) -> list[tuple[str, str]]:
        """Список (id, word) всех карточек — для быстрой дедупликации.

        id приводится к str() здесь же: dedupe.py уже работает со строковыми id
        вперемешку с note_id из Anki (другое пространство id, тоже str) — так
        переход card.id на int не требует никаких изменений в dedupe.py.
        """
        with self.connect() as conn:
            rows = conn.execute("SELECT id, word FROM cards").fetchall()
        return [(str(r["id"]), r["word"]) for r in rows]

    # ───────────── Audit ─────────────

    def log_action(self, action: str, card_id: int | None, details: dict) -> None:
        run_id = structlog.contextvars.get_contextvars().get("run_id")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (timestamp, action, card_id, details, run_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    action,
                    card_id,
                    json.dumps(details, ensure_ascii=False),
                    run_id,
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
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def all_anki_words(self) -> list[tuple[int, str]]:
        """Список (note_id, word) из кэша Anki."""
        with self.connect() as conn:
            rows = conn.execute("SELECT note_id, word FROM anki_cache").fetchall()
        return [(r["note_id"], r["word"]) for r in rows]

    def purge_anki_cache(self, note_ids: list[int]) -> None:
        """Убрать из кэша заметки, которых больше нет в Anki (см. anki/sync.py —
        upsert_anki_note никогда не чистит записи для исчезнувших заметок сам)."""
        if not note_ids:
            return
        with self.connect() as conn:
            conn.executemany(
                "DELETE FROM anki_cache WHERE note_id = ?", [(nid,) for nid in note_ids]
            )


def _row_to_card(row: sqlite3.Row) -> Card:
    """Восстановить Card из строки SQLite."""
    from datetime import date as date_cls

    return Card(
        id=row["id"],
        word=row["word"],
        pronunciation=row["pronunciation"],
        translation=row["translation"],
        image_query=row["image_query"],
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
