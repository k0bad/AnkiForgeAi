"""Тесты для setup_wizard._write_env_secrets и _generate_first_card (issue #58, часть #1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ankicards import setup_wizard
from ankicards.anki.connect import AnkiConnectError
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
from ankicards.models import POS, Card
from ankicards.pipeline import NoteTypeMissingError

# ─── _write_env_secrets ───


def test_creates_env_from_example_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    example = tmp_path / ".env.example"
    example.write_text(
        "OPENROUTER_API_KEY=your-openrouter-key-here\nNOTIFY_WEBHOOK_URL=\n", encoding="utf-8"
    )
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE_PATH", example)
    env_path = tmp_path / ".env"

    setup_wizard._write_env_secrets(env_path, {"OPENROUTER_API_KEY": "sk-test-123"})

    text = env_path.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=sk-test-123" in text
    assert "NOTIFY_WEBHOOK_URL=" in text  # untouched placeholder preserved


def test_preserves_existing_env_and_only_touches_matching_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment\nOPENROUTER_API_KEY=old-key\nNOTIFY_WEBHOOK_URL=https://example.com/hook\n",
        encoding="utf-8",
    )

    setup_wizard._write_env_secrets(env_path, {"OPENROUTER_API_KEY": "new-key"})

    lines = env_path.read_text(encoding="utf-8").splitlines()
    assert "# comment" in lines
    assert "OPENROUTER_API_KEY=new-key" in lines
    assert "NOTIFY_WEBHOOK_URL=https://example.com/hook" in lines


def test_appends_key_not_present_as_line(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=x\n", encoding="utf-8")

    setup_wizard._write_env_secrets(env_path, {"UNSPLASH_ACCESS_KEY": "unsplash-key"})

    text = env_path.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=x" in text
    assert "UNSPLASH_ACCESS_KEY=unsplash-key" in text


def test_no_env_no_example_still_writes_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE_PATH", tmp_path / "does-not-exist.example")
    env_path = tmp_path / ".env"

    setup_wizard._write_env_secrets(env_path, {"ANTHROPIC_API_KEY": "sk-ant-test"})

    assert env_path.read_text(encoding="utf-8").strip() == "ANTHROPIC_API_KEY=sk-ant-test"


# ─── _generate_first_card ───


def _make_config(tmp_path: Path, language: str = "nb") -> Config:
    return Config(
        language=language,
        paths=PathsConfig(
            db=tmp_path / "test.db",
            logs_dir=tmp_path / "logs",
            audio_dir=tmp_path / "audio",
            images_dir=tmp_path / "images",
            prompts_dir=tmp_path / "prompts",
        ),
        anki=AnkiConfig(),
        dedupe=DedupeConfig(
            fuzzy_threshold_review=85, fuzzy_threshold_auto=70, ai_adjudication=False
        ),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(grammar=False, examples=False, pronunciation=False),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


def _noop_init() -> None:
    pass


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    cfg: Config,
    *,
    ingest_by_topic: Any,
    run_ingest_pipeline: Any = None,
) -> None:
    monkeypatch.setattr("ankicards.config.get_config", lambda: cfg)
    monkeypatch.setattr("ankicards.cli.init", _noop_init)
    monkeypatch.setattr("ankicards.ingest.topic.ingest_by_topic", ingest_by_topic)
    if run_ingest_pipeline is not None:
        monkeypatch.setattr("ankicards.pipeline.run_ingest_pipeline", run_ingest_pipeline)


async def _stats(cards: list[Card]) -> dict:
    return {"new": len(cards), "review": 0, "merged": 0, "enriched": 0, "audio": 0, "errors": 0}


def test_generate_first_card_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _make_config(tmp_path)

    async def fake_ingest(
        topic: str, count: int, level: str, exclude_words: list[str]
    ) -> list[Card]:
        return [Card(language="nb", word="hus", pos=POS.NOUN, translation="dom")]

    async def fake_pipeline(cards: list[Card], db: Any, cfg: Any) -> dict:
        return await _stats(cards)

    async def fake_push(db: Any, anki: Any, cfg: Any) -> int:
        return 1

    _patch_common(monkeypatch, cfg, ingest_by_topic=fake_ingest, run_ingest_pipeline=fake_pipeline)
    monkeypatch.setattr("ankicards.pipeline.push_approved", fake_push)

    result = setup_wizard._generate_first_card(10)

    assert "generated and pushed 1 card" in result


def test_generate_first_card_no_cards_from_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)

    async def fake_ingest_empty(
        topic: str, count: int, level: str, exclude_words: list[str]
    ) -> list[Card]:
        return []

    _patch_common(monkeypatch, cfg, ingest_by_topic=fake_ingest_empty)

    result = setup_wizard._generate_first_card(10)

    assert "no cards" in result
    assert "ingest topic" in result


def test_generate_first_card_anki_unreachable_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)

    async def fake_ingest(
        topic: str, count: int, level: str, exclude_words: list[str]
    ) -> list[Card]:
        return [Card(language="nb", word="hus", pos=POS.NOUN, translation="dom")]

    async def fake_pipeline(cards: list[Card], db: Any, cfg: Any) -> dict:
        return await _stats(cards)

    async def fake_push_down(db: Any, anki: Any, cfg: Any) -> int:
        raise AnkiConnectError("connection refused")

    _patch_common(monkeypatch, cfg, ingest_by_topic=fake_ingest, run_ingest_pipeline=fake_pipeline)
    monkeypatch.setattr("ankicards.pipeline.push_approved", fake_push_down)

    result = setup_wizard._generate_first_card(10)

    assert "generated 1 card" in result
    assert "start Anki" in result
    assert "ankiforgeai init" in result


def test_generate_first_card_note_type_missing_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)

    async def fake_ingest(
        topic: str, count: int, level: str, exclude_words: list[str]
    ) -> list[Card]:
        return [Card(language="nb", word="hus", pos=POS.NOUN, translation="dom")]

    async def fake_pipeline(cards: list[Card], db: Any, cfg: Any) -> dict:
        return await _stats(cards)

    async def fake_push_missing(db: Any, anki: Any, cfg: Any) -> int:
        raise NoteTypeMissingError("LanguageCard")

    _patch_common(monkeypatch, cfg, ingest_by_topic=fake_ingest, run_ingest_pipeline=fake_pipeline)
    monkeypatch.setattr("ankicards.pipeline.push_approved", fake_push_missing)

    result = setup_wizard._generate_first_card(10)

    assert "generated 1 card" in result
    assert "ankiforgeai init" in result


def test_generate_first_card_unexpected_exception_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr("ankicards.cli.init", boom)

    result = setup_wizard._generate_first_card(10)

    assert "couldn't generate cards yet" in result
    assert "disk full" in result


def test_generate_first_card_caps_batch_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)
    captured: dict[str, int] = {}

    async def fake_ingest(
        topic: str, count: int, level: str, exclude_words: list[str]
    ) -> list[Card]:
        captured["count"] = count
        return []

    _patch_common(monkeypatch, cfg, ingest_by_topic=fake_ingest)

    setup_wizard._generate_first_card(100)

    assert captured["count"] == setup_wizard._FIRST_BATCH_SIZE


def test_generate_first_card_respects_small_words_per_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)
    captured: dict[str, int] = {}

    async def fake_ingest(
        topic: str, count: int, level: str, exclude_words: list[str]
    ) -> list[Card]:
        captured["count"] = count
        return []

    _patch_common(monkeypatch, cfg, ingest_by_topic=fake_ingest)

    setup_wizard._generate_first_card(3)

    assert captured["count"] == 3


def test_generate_first_card_backfills_legacy_rows_with_configured_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: _generate_first_card's Database(cfg.paths.db) call used to omit
    default_language=cfg.language, so re-running `ankiforgeai setup` against an
    existing pre-issue-63 DB (no cards.language column yet) silently backfilled
    every legacy row to the hardcoded "nb" default — even when the wizard had
    just saved a different language to config.yaml. Reproduce that upgrade
    scenario with a language other than "nb" so the bug can't hide behind a
    default that happens to match."""
    cfg = _make_config(tmp_path, language="de")
    cfg.paths.db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(cfg.paths.db)
    try:
        conn.executescript(
            """
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
        )
        conn.execute(
            "INSERT INTO cards (id, word, translation, pos, status, date_added) "
            "VALUES (1, 'Haus', 'дом', 'noun', 'pending', '2026-01-01')"
        )
        conn.commit()
    finally:
        conn.close()

    async def fake_ingest(
        topic: str, count: int, level: str, exclude_words: list[str]
    ) -> list[Card]:
        return []

    _patch_common(monkeypatch, cfg, ingest_by_topic=fake_ingest)

    setup_wizard._generate_first_card(10)

    db = Database(cfg.paths.db, default_language="nb")  # reopening must not overwrite
    card = db.get_by_id(1)
    assert card is not None
    assert card.language == "de"  # not the hardcoded "nb" fallback
