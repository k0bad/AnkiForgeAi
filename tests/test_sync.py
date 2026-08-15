"""Тесты для anki.sync.sync_anki_to_cache: обновление кэша + обнаружение
заметок, удалённых прямо в Anki (не через `ankiforgeai delete`).

Покрыть:
- обычный апдейт: найденные заметки попадают в anki_cache
- заметка, привязанная к карточке, пропавшая из findNotes() — карточка
  удаляется локально, её номер освобождается для переиспользования
- guard против массового ложного удаления (например, из-за смены deck_name)
  не срабатывает на единичном удалении при маленькой базе, но срабатывает,
  когда пропадает много привязанных заметок разом
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards.anki.sync import sync_anki_to_cache
from ankicards.config import (
    AnkiConfig,
    Config,
    DedupeConfig,
    ImagesConfig,
    IngestConfig,
    LLMConfig,
    LoggingConfig,
    PathsConfig,
    ReviewConfig,
    TagsConfig,
    TTSConfig,
)
from ankicards.db import Database
from ankicards.models import POS, Card, Status


def _make_config(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(
            db=tmp_path / "test.db",
            logs_dir=tmp_path / "logs",
            audio_dir=tmp_path / "audio",
            images_dir=tmp_path / "images",
            prompts_dir=tmp_path / "prompts",
        ),
        anki=AnkiConfig(),
        dedupe=DedupeConfig(),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(),
        review=ReviewConfig(),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return _make_config(tmp_path)


class _FakeAnki:
    """note_id -> {"fields": {...}}; find_notes/notes_info читают отсюда."""

    def __init__(self, notes: dict[int, dict]) -> None:
        self._notes = notes

    async def find_notes(self, query: str) -> list[int]:
        return list(self._notes.keys())

    async def notes_info(self, note_ids: list[int]) -> list[dict]:
        return [
            {
                "noteId": nid,
                "fields": {k: {"value": v} for k, v in self._notes[nid]["fields"].items()},
                "tags": self._notes[nid].get("tags", []),
            }
            for nid in note_ids
        ]


def _pushed_card(db: Database, word: str, note_id: int) -> Card:
    card = Card(word=word, pos=POS.NOUN, translation="перевод", status=Status.PUSHED)
    db.insert_card(card)
    with db.connect() as conn:
        conn.execute("UPDATE cards SET anki_note_id = ? WHERE id = ?", (note_id, card.id))
    reloaded = db.get_by_id(card.id)
    assert reloaded is not None
    return reloaded


async def test_sync_upserts_found_notes(db: Database, cfg: Config) -> None:
    anki = _FakeAnki({111: {"fields": {"Word": "hus"}}})
    count = await sync_anki_to_cache(db, anki, cfg)  # type: ignore[arg-type]
    assert count == 1
    assert db.all_anki_words() == [(111, "hus")]


async def test_sync_detects_note_deleted_in_anki(db: Database, cfg: Config) -> None:
    card = _pushed_card(db, "hus", 111)
    await sync_anki_to_cache(db, _FakeAnki({111: {"fields": {"Word": "hus"}}}), cfg)  # type: ignore[arg-type]

    # Заметку удалили прямо в Anki — findNotes() её больше не отдаёт
    await sync_anki_to_cache(db, _FakeAnki({}), cfg)  # type: ignore[arg-type]

    assert db.get_by_id(card.id) is None
    assert db.all_anki_words() == []


async def test_sync_reuses_id_freed_by_anki_side_deletion(db: Database, cfg: Config) -> None:
    card = _pushed_card(db, "hus", 111)
    await sync_anki_to_cache(db, _FakeAnki({111: {"fields": {"Word": "hus"}}}), cfg)  # type: ignore[arg-type]
    await sync_anki_to_cache(db, _FakeAnki({}), cfg)  # type: ignore[arg-type]
    assert db.get_by_id(card.id) is None

    new_card = Card(word="bil", pos=POS.NOUN, translation="машина")
    db.insert_card(new_card)
    assert new_card.id == card.id


async def test_sync_single_deletion_not_blocked_by_mass_delete_guard(
    db: Database, cfg: Config
) -> None:
    """1 из 1 отслеживаемых карточек пропала (100%) — но ниже порога по
    количеству (_MASS_DELETE_MIN_COUNT), поэтому это не считается "массовым"."""
    card = _pushed_card(db, "hus", 111)
    await sync_anki_to_cache(db, _FakeAnki({111: {"fields": {"Word": "hus"}}}), cfg)  # type: ignore[arg-type]

    await sync_anki_to_cache(db, _FakeAnki({}), cfg)  # type: ignore[arg-type]

    assert db.get_by_id(card.id) is None


async def test_sync_mass_deletion_guard_skips_deletion(db: Database, cfg: Config) -> None:
    cards = [_pushed_card(db, w, i) for i, w in enumerate(("hus", "bil", "katt"), start=100)]
    notes = {c.anki_note_id: {"fields": {"Word": c.word}} for c in cards}
    await sync_anki_to_cache(db, _FakeAnki(notes), cfg)  # type: ignore[arg-type]

    # Все три "пропали" разом — похоже на смену deck_name/поискового запроса,
    # а не на реальное массовое удаление; ничего не должно стереться локально.
    await sync_anki_to_cache(db, _FakeAnki({}), cfg)  # type: ignore[arg-type]

    for c in cards:
        assert db.get_by_id(c.id) is not None
