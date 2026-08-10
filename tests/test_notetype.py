"""Тесты для anki/notetype.py: экранирование HTML и резолвинг языка/UI-лейблов."""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards.anki import notetype
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
from ankicards.models import POS, Card


def _make_config(tmp_path: Path, language: str = "nb", ui_language: str = "ru") -> Config:
    return Config(
        language=language,
        ui_language=ui_language,
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
        enrich=EnrichConfig(),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


@pytest.fixture
def patch_language(tmp_path, monkeypatch):
    """Подменяет notetype.get_config(), чтобы не трогать реальный config.yaml проекта."""

    def _patch(language: str = "nb", ui_language: str = "ru") -> Config:
        cfg = _make_config(tmp_path, language=language, ui_language=ui_language)
        monkeypatch.setattr(notetype, "get_config", lambda: cfg)
        return cfg

    return _patch


def test_card_to_anki_fields_escapes_html(patch_language) -> None:
    patch_language()
    card = Card(
        word="<script>alert(1)</script>",
        pos=POS.NOUN,
        translation="<img src=x onerror=alert(1)>",
        example="<b>bold</b> example",
        example_translation="перевод & <i>тест</i>",
    )
    fields = notetype.card_to_anki_fields(card)

    assert "<script>" not in fields["Word"]
    assert "&lt;script&gt;" in fields["Word"]
    assert "<img" not in fields["Translation"]
    assert "&lt;img" in fields["Translation"]
    assert "<b>" not in fields["Example"]
    assert "&amp;" in fields["ExampleTranslation"]


def test_card_to_anki_fields_image_and_audio_are_intentional_html(patch_language) -> None:
    """Image/Audio — единственные поля, где сырой HTML нужен намеренно (img-тег, [sound:])."""
    patch_language()
    card = Card(word="hus", pos=POS.NOUN, translation="дом")
    card.image = "abc123.jpg"
    card.audio = "abc123_nb.mp3"

    fields = notetype.card_to_anki_fields(card)

    assert fields["Image"] == '<img src="abc123.jpg">'
    assert fields["Audio"] == "[sound:abc123_nb.mp3]"


def test_get_note_type_name_resolves_from_configured_language(patch_language) -> None:
    patch_language(language="de")
    assert notetype._get_note_type_name() == "LanguageCard"


def test_back_labels_default_to_russian(patch_language) -> None:
    patch_language(ui_language="ru")
    template = notetype._build_back_template()
    assert "Перевод" in template


def test_back_labels_switch_to_english(patch_language) -> None:
    patch_language(ui_language="en")
    template = notetype._build_back_template()
    assert "Translation" in template
    assert "Перевод" not in template


def test_pos_label_differs_per_language(patch_language) -> None:
    patch_language(language="nb")
    assert notetype.pos_label("noun") == "substantiv"

    patch_language(language="de")
    assert notetype.pos_label("noun") == "Substantiv / Nomen"
