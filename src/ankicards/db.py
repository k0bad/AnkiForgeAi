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

from .log import get_logger
from .models import Card, Status

logger = get_logger(__name__)


class IdMigrationRequiredError(RuntimeError):
    """cards.id всё ещё TEXT (старые UUID) — нужно запустить `ankiforgeai migrate-ids`
    перед тем, как эта версия сможет работать с базой (см. migrate_ids.py)."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id                  INTEGER PRIMARY KEY,
    language            TEXT,           -- код языкового профиля (issue #63), см. Database._migrate
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
CREATE INDEX IF NOT EXISTS idx_cards_word     ON cards(word);
CREATE INDEX IF NOT EXISTS idx_cards_status   ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_topic    ON cards(topic);
-- idx_cards_language НЕ здесь: на БД до issue #63 колонка cards.language ещё не
-- существует на момент этого executescript (её добавляет ALTER TABLE в _migrate(),
-- который выполняется позже) — индекс создаётся там же, уже после ALTER.

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
    language    TEXT,                   -- код языкового профиля (issue #63), см. Database._migrate
    word        TEXT NOT NULL,
    fields      TEXT NOT NULL,          -- JSON всех полей
    tags        TEXT,                   -- JSON list
    synced_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anki_word ON anki_cache(word);
"""


class Database:
    """Тонкая обёртка над sqlite3 с явными транзакциями."""

    def __init__(self, path: Path, default_language: str = "nb") -> None:
        self.path = path
        self.default_language = default_language
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

        # language (issue #63) — бэкафилл на карточках, созданных до мультиязычности:
        # лучшая доступная догадка — язык, активный на момент первого запуска после
        # апгрейда (self.default_language). Если пользователь раньше вручную переключал
        # language: в config.yaml между прогонами, старые карточки другого языка будут
        # помечены неверно — restore невозможен, поэтому логируем факт бэкафилла, а не
        # тихо молчим (см. план issue #63).
        if "language" not in card_info:
            conn.execute("ALTER TABLE cards ADD COLUMN language TEXT")
        cur = conn.execute(
            "UPDATE cards SET language = ? WHERE language IS NULL", (self.default_language,)
        )
        if cur.rowcount:
            logger.info(
                "db.language_backfilled",
                table="cards",
                count=cur.rowcount,
                assumed_language=self.default_language,
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_language ON cards(language)")

        anki_cache_cols = {row["name"] for row in conn.execute("PRAGMA table_info(anki_cache)")}
        if "language" not in anki_cache_cols:
            conn.execute("ALTER TABLE anki_cache ADD COLUMN language TEXT")
        cur = conn.execute(
            "UPDATE anki_cache SET language = ? WHERE language IS NULL", (self.default_language,)
        )
        if cur.rowcount:
            logger.info(
                "db.language_backfilled",
                table="anki_cache",
                count=cur.rowcount,
                assumed_language=self.default_language,
            )

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
                    id, language, word, pronunciation, translation, image_query, example,
                    example_translation, pos, forms, level, topic, source, image, audio,
                    tags, status, date_added, anki_note_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    card.id,
                    card.language,
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
                    pos = ?, forms = ?, image = ?, audio = ?, tags = ?, status = ?
                   WHERE id = ?""",
                (
                    card.pronunciation,
                    card.translation,
                    card.example,
                    card.example_translation,
                    # pos — потому что его меняет не только человек через `review edit`
                    # (у того свой UPDATE), но и enrich.pos на accept. Без этой колонки
                    # разобранная часть речи жила только в объекте и умирала вместе с
                    # ним: карточка оставалась `other`, а с ней теряла и грамматические
                    # формы, и тег pos::noun, по которому в Anki режут колоды.
                    card.pos.value,
                    json.dumps(card.forms) if card.forms else None,
                    card.image,
                    card.audio,
                    # tags попадают сюда, потому что их меняет не только ingest:
                    # accept навешивает verified-тег (Card.mark_verified), и без
                    # этой колонки он терялся бы на первом же update_card.
                    json.dumps(card.tags),
                    card.status.value,
                    card.id,
                ),
            )

    def get_by_status(self, status: Status, language: str | None = None) -> list[Card]:
        """Все карточки с заданным статусом. language=None — без фильтра по языку
        (см. issue #63: review/push/sync-семейство фильтрует явным cfg.language,
        stats/doctor по умолчанию хотят сводный вид по всем языкам)."""
        query = "SELECT * FROM cards WHERE status = ?"
        params: list[str] = [status.value]
        if language is not None:
            query += " AND language = ?"
            params.append(language)
        query += " ORDER BY date_added"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_card(r) for r in rows]

    def count_by_level(self, language: str | None = None) -> dict[str, dict[str, int]]:
        """{level: {status: count}} по всем карточкам с непустым level — для отчёта
        по уровням после ingest и для подсказки о переходе на следующий уровень.
        language=None — без фильтра (см. get_by_status)."""
        query = "SELECT level, status, COUNT(*) as n FROM cards WHERE level IS NOT NULL"
        params: list[str] = []
        if language is not None:
            query += " AND language = ?"
            params.append(language)
        query += " GROUP BY level, status"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            result.setdefault(row["level"], {})[row["status"]] = row["n"]
        return result

    def count_pushed_by_date(self) -> dict[str, int]:
        """{'YYYY-MM-DD': N} — сколько карточек запушено в Anki в этот день, по
        audit_log (action='push', см. pipeline.push_approved -> _record). Основа для
        стрика в `stats` — считаем по факту доставки в Anki (source of truth, см.
        CLAUDE.md принцип 1), а не по date_added (когда карточка просто создана
        локально, но ещё не обязательно запушена)."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS n "
                "FROM audit_log WHERE action = 'push' GROUP BY day ORDER BY day"
            ).fetchall()
        return {row["day"]: row["n"] for row in rows}

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

    def count_cards_with_anki_note_id(self, language: str | None = None) -> int:
        """Сколько карточек вообще привязаны к заметке в Anki — знаменатель для
        guard'а против массового ложного удаления в sync.py (см. _handle_vanished_notes).

        language=None — без фильтра. sync.py всегда передаёт конкретный язык: иначе
        (issue #63) массовое удаление ноутов одного языка размывается знаменателем
        по ВСЕМ языкам и guard может не сработать, когда должен."""
        query = "SELECT COUNT(*) AS n FROM cards WHERE anki_note_id IS NOT NULL"
        params: list[str] = []
        if language is not None:
            query += " AND language = ?"
            params.append(language)
        with self.connect() as conn:
            row = conn.execute(query, params).fetchone()
        return int(row["n"])

    def delete_card(self, card_id: int) -> None:
        """Удалить карточку насовсем — освобождает её номер для переиспользования
        (см. review/actions.py::delete_cards и anki/sync.py — вызывается либо по
        явной команде `ankiforgeai delete`, либо когда sync обнаружит, что
        соответствующая заметка исчезла из Anki)."""
        with self.connect() as conn:
            conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))

    def all_words(self, language: str | None = None) -> list[tuple[str, str]]:
        """Список (id, word) всех карточек — для быстрой дедупликации.

        id приводится к str() здесь же: dedupe.py уже работает со строковыми id
        вперемешку с note_id из Anki (другое пространство id, тоже str) — так
        переход card.id на int не требует никаких изменений в dedupe.py.

        language=None — без фильтра. dedupe.py всегда передаёт card.language:
        иначе (issue #63) fuzzy-match сравнивал бы слова разных языков между собой.
        """
        query = "SELECT id, word FROM cards"
        params: list[str] = []
        if language is not None:
            query += " WHERE language = ?"
            params.append(language)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
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

    def upsert_anki_note(
        self, note_id: int, language: str, word: str, fields: dict, tags: list[str]
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO anki_cache (note_id, language, word, fields, tags, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(note_id) DO UPDATE SET
                       language=excluded.language,
                       word=excluded.word,
                       fields=excluded.fields,
                       tags=excluded.tags,
                       synced_at=excluded.synced_at""",
                (
                    note_id,
                    language,
                    word,
                    json.dumps(fields, ensure_ascii=False),
                    json.dumps(tags),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def all_anki_words(self, language: str | None = None) -> list[tuple[int, str]]:
        """Список (note_id, word) из кэша Anki. language=None — без фильтра
        (см. all_words — тот же issue #63 резон: sync.py и dedupe.py всегда
        передают конкретный язык, иначе заметки других языков либо считаются
        "исчезнувшими" при sync, либо загрязняют dedupe)."""
        query = "SELECT note_id, word FROM anki_cache"
        params: list[str] = []
        if language is not None:
            query += " WHERE language = ?"
            params.append(language)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
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
        language=row["language"],
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
