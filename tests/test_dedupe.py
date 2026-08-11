"""Тесты на дедупликацию.

Покрыть:
- exact match (case-insensitive, нормализованные пробелы)
- fuzzy match выше/ниже порога
- "ближайшие, но не совпадающие" (gå vs går — разные формы одного слова)
- сравнение по нескольким полям
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards import dedupe as dedupe_module
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
from ankicards.dedupe import _exact_match, _fuzzy_matches, check_card, judge_review
from ankicards.models import POS, Card, Decision, DuplicateMatch


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
        dedupe=DedupeConfig(
            fuzzy_threshold_review=85,
            fuzzy_threshold_auto=70,
        ),
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


def _card(word: str, pos: POS = POS.NOUN, translation: str = "перевод") -> Card:
    return Card(word=word, pos=pos, translation=translation)


def test_exact_match_case_insensitive() -> None:
    result = _exact_match("Bil", [("id1", "bil"), ("id2", "hund")])
    assert result == "id1"


def test_exact_match_strips_verb_particle() -> None:
    result = _exact_match("å gå", [("id1", "gå"), ("id2", "spise")])
    assert result == "id1"


def test_exact_match_none_when_no_match() -> None:
    assert _exact_match("hus", [("id1", "bil"), ("id2", "hund")]) is None


def test_exact_match_returns_merge(db: Database, cfg: Config) -> None:
    existing = _card("bil")
    db.insert_card(existing)

    candidate = _card("Bil")
    decision = check_card(candidate, db, cfg)

    assert decision.decision == "merge"
    assert decision.matches[0].existing_card_id == existing.id
    assert decision.matches[0].score == 100.0


def test_fuzzy_above_threshold_returns_review(db: Database, cfg: Config) -> None:
    db.insert_card(_card("kjøkken"))
    candidate = _card("kjokken")

    decision = check_card(candidate, db, cfg)

    assert decision.decision == "review"
    assert decision.matches
    assert decision.matches[0].score >= cfg.dedupe.fuzzy_threshold_review


def test_low_similarity_returns_new(db: Database, cfg: Config) -> None:
    db.insert_card(_card("bil"))
    candidate = _card("kjærlighet")

    decision = check_card(candidate, db, cfg)

    assert decision.decision == "new"
    assert decision.matches == []


def test_empty_db_returns_new(db: Database, cfg: Config) -> None:
    decision = check_card(_card("bil"), db, cfg)
    assert decision.decision == "new"


def test_fuzzy_matches_respects_threshold() -> None:
    candidates = [("id1", "bil"), ("id2", "bila"), ("id3", "helt annet")]
    result = _fuzzy_matches("bil", candidates, threshold=70, limit=5)

    words = [m.existing_word for m in result]
    assert "bil" in words
    assert "helt annet" not in words


def test_ignores_self_id(db: Database, cfg: Config) -> None:
    card = _card("bil")
    db.insert_card(card)

    decision = check_card(card, db, cfg)

    assert decision.decision == "new"


def _review_decision() -> Decision:
    return Decision(
        decision="review",
        matches=[
            DuplicateMatch(
                existing_card_id="id1", existing_word="kjøkken", score=90.0, matched_field="word"
            )
        ],
        reason="fuzzy score 90.0 >= 85",
    )


async def test_judge_review_passes_configured_judge_model(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def _fake_call_text(prompt: str, **kw: object) -> object:
        seen.update(kw)
        return _async("same")

    monkeypatch.setattr(dedupe_module, "load_prompt", lambda name, **kw: "prompt")
    monkeypatch.setattr(dedupe_module, "call_text", _fake_call_text)
    cfg.dedupe.judge_model = "deepseek/deepseek-v4-flash"

    await judge_review(_card("kjokken"), _review_decision(), cfg)

    assert seen["model"] == "deepseek/deepseek-v4-flash"


async def test_judge_review_same_verdict_returns_merge(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dedupe_module, "load_prompt", lambda name, **kw: "prompt")
    monkeypatch.setattr(dedupe_module, "call_text", lambda prompt, **kw: _async("same"))

    result = await judge_review(_card("kjokken"), _review_decision(), cfg)

    assert result.decision == "merge"
    assert result.matches[0].existing_word == "kjøkken"


async def test_judge_review_different_verdict_returns_new(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dedupe_module, "load_prompt", lambda name, **kw: "prompt")
    monkeypatch.setattr(dedupe_module, "call_text", lambda prompt, **kw: _async("different"))

    result = await judge_review(_card("kjokken"), _review_decision(), cfg)

    assert result.decision == "new"


async def test_judge_review_unsure_verdict_keeps_review(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dedupe_module, "load_prompt", lambda name, **kw: "prompt")
    monkeypatch.setattr(dedupe_module, "call_text", lambda prompt, **kw: _async("unsure"))

    decision = _review_decision()
    result = await judge_review(_card("kjokken"), decision, cfg)

    assert result is decision


async def test_judge_review_llm_error_keeps_review(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(prompt: str, **kw: str) -> str:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(dedupe_module, "load_prompt", lambda name, **kw: "prompt")
    monkeypatch.setattr(dedupe_module, "call_text", _boom)

    decision = _review_decision()
    result = await judge_review(_card("kjokken"), decision, cfg)

    assert result is decision


async def test_judge_review_disabled_skips_llm(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(name: str, **kw: str) -> str:
        raise AssertionError("load_prompt should not be called when ai_adjudication is off")

    monkeypatch.setattr(dedupe_module, "load_prompt", _boom)
    cfg.dedupe.ai_adjudication = False

    decision = _review_decision()
    result = await judge_review(_card("kjokken"), decision, cfg)

    assert result is decision


async def test_judge_review_noop_for_non_review_decision(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(name: str, **kw: str) -> str:
        raise AssertionError("load_prompt should not be called for a non-review decision")

    monkeypatch.setattr(dedupe_module, "load_prompt", _boom)

    decision = Decision(decision="new")
    result = await judge_review(_card("kjokken"), decision, cfg)

    assert result is decision


async def _async(value: str) -> str:
    return value
