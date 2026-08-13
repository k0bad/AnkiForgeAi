"""Тесты для pipeline.run_ingest_pipeline: dedupe → enrich (по флагам) → media → save."""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards import pipeline
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


def _make_config(tmp_path: Path, **enrich_overrides: bool) -> Config:
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
        dedupe=DedupeConfig(fuzzy_threshold_review=85, fuzzy_threshold_auto=70),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(**enrich_overrides),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


def _card(word: str, translation: str = "перевод") -> Card:
    return Card(word=word, pos=POS.NOUN, translation=translation)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


@pytest.fixture(autouse=True)
def _stub_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Аудио — не предмет этих тестов; заменяем на no-op, чтобы не трогать edge-tts."""

    async def _noop_audio(card: Card, cfg: Config) -> Card:
        return card

    monkeypatch.setattr(pipeline, "generate_audio", _noop_audio)


async def test_new_card_gets_approved_and_saved(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path)

    stats = await pipeline.run_ingest_pipeline(
        [_card("hus")], db=db, cfg=cfg, auto_enrich=False
    )

    assert stats["new"] == 1
    assert stats["merged"] == 0
    saved = db.get_by_status(Status.APPROVED)
    assert len(saved) == 1
    assert saved[0].word == "hus"


async def test_exact_duplicate_is_merged_not_saved(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path)
    db.insert_card(_card("hus"))

    stats = await pipeline.run_ingest_pipeline(
        [_card("Hus")], db=db, cfg=cfg, auto_enrich=False
    )

    assert stats["merged"] == 1
    assert stats["new"] == 0
    assert db.get_by_status(Status.APPROVED) == []


async def test_duplicate_within_same_batch_is_merged_not_both_saved(
    tmp_path: Path, db: Database
) -> None:
    """Регрессия на #13: LLM вернул одно и то же слово дважды в одном ответе —
    второе должно поймать первое как дубликат, а не проскочить как "new",
    т.к. первое ещё не успело попасть в БД (insert только в конце функции)."""
    cfg = _make_config(tmp_path)

    stats = await pipeline.run_ingest_pipeline(
        [_card("hus"), _card("Hus")], db=db, cfg=cfg, auto_enrich=False
    )

    assert stats["new"] == 1
    assert stats["merged"] == 1
    assert len(db.get_by_status(Status.APPROVED)) == 1


async def test_enrich_flags_gate_which_stages_run(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cfg.enrich.* должны реально включать/выключать соответствующие LLM-стадии."""
    calls = {"pronunciation": 0, "grammar": 0, "examples": 0}

    def _tracker(key: str):
        async def _tracked(cards: list[Card]) -> list[Card]:
            calls[key] += 1
            return cards

        return _tracked

    monkeypatch.setattr(pipeline, "enrich_pronunciation_batch", _tracker("pronunciation"))
    monkeypatch.setattr(pipeline, "enrich_grammar_batch", _tracker("grammar"))
    monkeypatch.setattr(pipeline, "enrich_example_batch", _tracker("examples"))

    cfg = _make_config(tmp_path, grammar=False, examples=False, pronunciation=True)
    await pipeline.run_ingest_pipeline(
        [_card("hus")], db=db, cfg=cfg, auto_enrich=True, auto_media=False
    )

    assert calls == {"pronunciation": 1, "grammar": 0, "examples": 0}
