"""Тесты для media.tts: голос и суффикс имени файла должны следовать за
card.language, а не за тем, какой язык сейчас активен в cfg (issue #63 добавила
Card.language, но generate_audio продолжал жёстко ставить "_nb" всем карточкам
и выбирать голос по cfg.language — заметно только когда они расходятся)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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
from ankicards.media import tts as tts_module
from ankicards.models import POS, Card


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
        dedupe=DedupeConfig(),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


def _patch_synthesize(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Подменяет сетевой edge_tts вызов, запоминая voice/out_path для проверки."""
    captured: dict[str, Any] = {}

    async def fake_synthesize(text: str, voice: str, out_path: Path, rate: str, pitch: str) -> None:
        captured["text"] = text
        captured["voice"] = voice
        captured["out_path"] = out_path

    monkeypatch.setattr(tts_module, "_synthesize", fake_synthesize)
    return captured


async def test_english_card_gets_english_filename_and_voice_under_nb_cfg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The active cfg.language is deliberately "nb" here — only card.language
    is "en" — to prove the filename/voice follow the card, not the process."""
    cfg = _make_config(tmp_path, language="nb")
    captured = _patch_synthesize(monkeypatch)
    card = Card(id=42, language="en", word="bread", pos=POS.NOUN, translation="хлеб")

    result = await tts_module.generate_audio(card, cfg)

    assert result.audio == "42_en.mp3"
    assert captured["voice"] == "en-US-JennyNeural"  # languages/en/language.yaml, not nb-NO-*


async def test_norwegian_card_keeps_nb_filename_and_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path, language="nb")
    captured = _patch_synthesize(monkeypatch)
    card = Card(id=7, language="nb", word="brød", pos=POS.NOUN, translation="хлеб")

    result = await tts_module.generate_audio(card, cfg)

    assert result.audio == "7_nb.mp3"
    assert captured["voice"] == "nb-NO-PernilleNeural"


async def test_verb_infinitive_particle_stripped_for_pronunciation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path, language="nb")
    captured = _patch_synthesize(monkeypatch)
    card = Card(id=1, language="nb", word="å bo", pos=POS.VERB, translation="жить")

    await tts_module.generate_audio(card, cfg)

    assert captured["text"] == "bo"
