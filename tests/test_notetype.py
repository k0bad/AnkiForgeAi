"""Тесты для anki/notetype.py: экранирование HTML, резолвинг языка/UI-лейблов
и декларативная схема полей (anki.fields в language.yaml)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards.anki import notetype
from ankicards.config import (
    AnkiConfig,
    AnkiProfileConfig,
    Config,
    DedupeConfig,
    EnrichConfig,
    ImagesConfig,
    IngestConfig,
    LanguageConfig,
    LLMConfig,
    LoggingConfig,
    NoteFieldDef,
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


# ───────────── Декларативная схема полей (anki.fields) ─────────────


@pytest.fixture
def patch_custom_fields(tmp_path, monkeypatch):
    """Подменяет и get_config(), и get_language() — язык с кастомным anki.fields,
    без обращения к настоящим languages/{code}/language.yaml."""

    def _patch(fields: list[NoteFieldDef], code: str = "xx") -> None:
        cfg = _make_config(tmp_path, language=code)
        monkeypatch.setattr(notetype, "get_config", lambda: cfg)

        lang = LanguageConfig(
            code=code,
            name="Test Language",
            pos_labels={"noun": "noun"},
            anki=AnkiProfileConfig(deck_name="Test", note_type="LanguageCard", fields=fields),
            back_labels={"translation": "Перевод"},
        )
        monkeypatch.setattr(notetype, "get_language", lambda _code=None: lang)

    return _patch


def test_default_fields_used_when_language_has_no_override(patch_language) -> None:
    """Порядок соответствует визуальному рендеру бэка (Translation/POS/Pronunciation/
    Forms/Example/ExampleTranslation), а не старой захардкоженной константе FIELDS —
    см. комментарий над DEFAULT_FIELDS в anki/notetype.py."""
    patch_language(language="nb")
    assert notetype.field_names() == [
        "Word", "Translation", "POS", "Pronunciation", "Forms", "Example",
        "ExampleTranslation", "Image", "Audio", "Level", "Topic", "ID",
    ]


def test_custom_field_subset_reflected_in_field_names_and_card_fields(
    patch_custom_fields,
) -> None:
    patch_custom_fields(
        [
            NoteFieldDef(name="Front", source="word", slot="front_title"),
            NoteFieldDef(name="Back", source="translation", slot="section"),
        ]
    )
    assert notetype.field_names() == ["Front", "Back"]

    card = Card(word="hus", pos=POS.NOUN, translation="дом")
    fields = notetype.card_to_anki_fields(card)
    assert fields == {"Front": "hus", "Back": "дом"}


def test_unknown_source_raises_note_type_config_error(patch_custom_fields) -> None:
    patch_custom_fields([NoteFieldDef(name="Synonyms", source="synonyms", slot="section")])

    card = Card(word="hus", pos=POS.NOUN, translation="дом")
    with pytest.raises(notetype.NoteTypeConfigError, match="synonyms"):
        notetype.card_to_anki_fields(card)

    with pytest.raises(notetype.NoteTypeConfigError, match="synonyms"):
        notetype.field_names()


def test_duplicate_front_title_slot_raises(patch_custom_fields) -> None:
    patch_custom_fields(
        [
            NoteFieldDef(name="Word", source="word", slot="front_title"),
            NoteFieldDef(name="Word2", source="word", slot="front_title"),
        ]
    )
    with pytest.raises(notetype.NoteTypeConfigError, match="front_title"):
        notetype.field_names()


def test_duplicate_field_names_raise(patch_custom_fields) -> None:
    patch_custom_fields(
        [
            NoteFieldDef(name="Word", source="word", slot="front_title"),
            NoteFieldDef(name="Word", source="translation", slot="section"),
        ]
    )
    with pytest.raises(notetype.NoteTypeConfigError, match="Duplicate field names"):
        notetype.field_names()


def test_custom_tag_and_section_slots_land_in_expected_places(patch_custom_fields) -> None:
    patch_custom_fields(
        [
            NoteFieldDef(name="Word", source="word", slot="front_title"),
            NoteFieldDef(
                name="Translation", source="translation", slot="section", label_key="translation"
            ),
            NoteFieldDef(name="Difficulty", source="level", slot="tag"),
        ]
    )
    template = notetype._build_back_template()
    assert '<div class="section">' in template
    assert "{{Translation}}" in template
    assert '{{#Difficulty}}<span class="tag">{{Difficulty}}</span>{{/Difficulty}}' in template
    # front-only и hidden поля не должны просачиваться на бэк
    assert "{{Word}}" not in template


def test_front_template_uses_custom_css_class(patch_custom_fields) -> None:
    patch_custom_fields(
        [NoteFieldDef(name="Word", source="word", slot="front_title", css_class="headword")]
    )
    assert '<div class="headword">{{Word}}</div>' in notetype.front_template()
