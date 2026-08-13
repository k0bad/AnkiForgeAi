"""Тесты для pipeline.push_approved: отсутствующий в Anki Note Type должен давать
понятную ошибку вместо тихого "pushed: 0" (Note Type mismatch после обновления,
когда anki.note_type в language.yaml сменился, а `ankiforgeai init` не перезапускался)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards.anki.notetype import _get_note_type_name
from ankicards.config import (
    AnkiConfig,
    Config,
    DedupeConfig,
    EnrichConfig,
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
from ankicards.pipeline import NoteTypeMissingError, push_approved


def _make_config(tmp_path: Path) -> Config:
    return Config(
        language="nb",
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
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(grammar=False, examples=False, pronunciation=False),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


class _FakeAnki:
    """Двойник AnkiConnect: без реального HTTP, только model_names/ensure_deck/add_note."""

    def __init__(self, models: list[str]) -> None:
        self._models = models
        self.added: list[dict] = []

    async def ensure_deck(self) -> None:
        pass

    async def model_names(self) -> list[str]:
        return self._models

    async def add_note(self, fields: dict, tags: list[str]) -> int:
        self.added.append(fields)
        return len(self.added)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


async def test_push_approved_raises_when_note_type_missing(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path)
    card = Card(word="hus", pos=POS.NOUN, translation="дом", status=Status.APPROVED)
    db.insert_card(card)

    anki = _FakeAnki(models=["SomeOtherModel"])

    with pytest.raises(NoteTypeMissingError, match=_get_note_type_name()):
        await push_approved(db, anki, cfg)  # type: ignore[arg-type]

    # push не должен был успеть отправить ни одной карточки
    assert anki.added == []


async def test_push_approved_succeeds_when_note_type_exists(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path)
    card = Card(word="hus", pos=POS.NOUN, translation="дом", status=Status.APPROVED)
    db.insert_card(card)

    anki = _FakeAnki(models=[_get_note_type_name()])

    count = await push_approved(db, anki, cfg)  # type: ignore[arg-type]

    assert count == 1
    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.PUSHED
