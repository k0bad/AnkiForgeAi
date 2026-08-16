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
    assert "<b>bold</b>" not in fields["Example"]
    assert "&amp;" in fields["ExampleTranslation"]


def test_image_audio_pos_forms_are_intentional_html(patch_language) -> None:
    """Image/Audio/POS/Forms — единственные поля, где сырой HTML нужен намеренно
    (img-тег, [sound:], цветной pill, ячейки грида) — значения внутри экранированы."""
    patch_language()
    card = Card(word="hus", pos=POS.NOUN, translation="дом")
    card.image = "abc123.jpg"
    card.audio = "abc123_nb.mp3"
    card.forms = {"indefinite_singular": "hus", "definite_singular": "huset"}

    fields = notetype.card_to_anki_fields(card)

    assert fields["Image"] == '<img src="abc123.jpg" alt="">'
    assert fields["Audio"] == "[sound:abc123_nb.mp3]"
    assert fields["POS"] == '<span class="pos-pill pos-noun">substantiv</span>'
    assert '<div class="form-cell">' in fields["Forms"]


def test_pos_pill_colors_by_raw_pos_value_not_localized_label(patch_language) -> None:
    """Кодирование цвета (.pos-noun/.pos-verb/.pos-adj/.pos-adv в CSS) идёт по
    card.pos.value (raw-слаг "verb"), видимый текст — локализованный лейбл ("Verb" для de)."""
    patch_language(language="de")
    card = Card(word="sprechen", pos=POS.VERB, translation="говорить")
    fields = notetype.card_to_anki_fields(card)
    assert fields["POS"] == '<span class="pos-pill pos-verb">Verb</span>'


def test_get_note_type_name_resolves_from_configured_language(patch_language) -> None:
    patch_language(language="de")
    assert notetype._get_note_type_name() == "LanguageCard"


def test_pos_label_differs_per_language(patch_language) -> None:
    patch_language(language="nb")
    assert notetype.pos_label("noun") == "substantiv"

    patch_language(language="de")
    assert notetype.pos_label("noun") == "Substantiv / Nomen"


def test_forms_grid_cells_have_label_and_value_spans(patch_language) -> None:
    patch_language(language="nb")
    html = notetype._render_forms_grid_html(
        "noun", {"indefinite_singular": "hus", "definite_singular": "huset"}
    )
    assert '<span class="fl">' in html
    assert '<span class="fv">' in html
    assert "<table>" not in html


def test_forms_grid_empty_when_no_forms(patch_language) -> None:
    patch_language(language="nb")
    assert notetype._render_forms_grid_html("noun", None) == ""
    assert notetype._render_forms_grid_html("noun", {}) == ""


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
            back_labels={},
        )
        monkeypatch.setattr(notetype, "get_language", lambda _code=None: lang)

    return _patch


def test_default_fields_used_when_language_has_no_override(patch_language) -> None:
    """Порядок соответствует reference-fields.png хендоффа: Word, Pronunciation,
    Translation, Example, ExampleTranslation, POS, Forms, Image, Audio, Level, Topic, ID."""
    patch_language(language="nb")
    assert notetype.field_names() == [
        "Word",
        "Pronunciation",
        "Translation",
        "Example",
        "ExampleTranslation",
        "POS",
        "Forms",
        "Image",
        "Audio",
        "Level",
        "Topic",
        "ID",
    ]


def test_diff_fields_empty_when_matching(patch_language) -> None:
    patch_language(language="nb")
    local = notetype.field_names()

    missing, extra = notetype.diff_fields(local)

    assert missing == []
    assert extra == []


def test_diff_fields_detects_missing_and_extra(patch_language) -> None:
    """anki.fields поменялся в коде после того, как Note Type уже был создан в Anki:
    init не переносит список полей сам (см. cli.py init), только делает это видимым."""
    patch_language(language="nb")
    local = notetype.field_names()
    anki_fields = [*local[:-1], "LegacyField"]  # "ID" отсутствует, "LegacyField" лишний

    missing, extra = notetype.diff_fields(anki_fields)

    assert missing == ["ID"]
    assert extra == ["LegacyField"]


def test_custom_field_subset_reflected_in_field_names_and_card_fields(
    patch_custom_fields,
) -> None:
    patch_custom_fields(
        [
            NoteFieldDef(name="Front", source="word", slot="front_title"),
            NoteFieldDef(name="Back", source="translation", slot="translation"),
        ]
    )
    assert notetype.field_names() == ["Front", "Back"]

    card = Card(word="hus", pos=POS.NOUN, translation="дом")
    fields = notetype.card_to_anki_fields(card)
    assert fields == {"Front": "hus", "Back": "дом"}


def test_unknown_source_raises_note_type_config_error(patch_custom_fields) -> None:
    patch_custom_fields([NoteFieldDef(name="Synonyms", source="synonyms", slot="translation")])

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
            NoteFieldDef(name="Word", source="translation", slot="translation"),
        ]
    )
    with pytest.raises(notetype.NoteTypeConfigError, match="Duplicate field names"):
        notetype.field_names()


def test_front_template_uses_custom_css_class(patch_custom_fields) -> None:
    patch_custom_fields(
        [NoteFieldDef(name="Word", source="word", slot="front_title", css_class="headword")]
    )
    assert '<div class="headword">{{Word}}</div>' in notetype.front_template()


# ───────────── front/back structure ─────────────


def test_front_image_background_stack_is_guarded(patch_language) -> None:
    """Регрессия: Image должен быть optional, иначе фон рендерится даже без картинки."""
    patch_language(language="nb")
    front = notetype.front_template()
    assert "{{#Image}}" in front
    assert "{{/Image}}" in front
    assert '<div class="bg-ambient">{{Image}}</div>' in front
    assert '<div class="bg-subject">{{Image}}</div>' in front


def test_back_image_layer_is_guarded(patch_language) -> None:
    patch_language(language="nb")
    back = notetype._build_back_template()
    assert '{{#Image}}<div class="bg-back">{{Image}}</div>{{/Image}}' in back


def test_header_recaps_word_pronunciation_pos_level_audio(patch_language) -> None:
    patch_language(language="nb")
    back = notetype._build_back_template()
    assert '<span class="word">{{Word}}</span>' in back
    assert '{{#Pronunciation}}<span class="ipa">{{Pronunciation}}</span>{{/Pronunciation}}' in back
    assert "{{POS}}" in back
    assert '{{#Level}}<span class="level-pill">{{Level}}</span>{{/Level}}' in back
    assert '<span class="audio-pill">{{Audio}}<span class="audio-label">audio</span></span>' in back
    assert '<div class="translation">{{Translation}}</div>' in back


def test_example_and_forms_render_as_separate_tab_panels(patch_language) -> None:
    patch_language(language="nb")
    back = notetype._build_back_template()
    assert '<div class="panel" data-tab="examples">' in back
    assert '<div class="panel" data-tab="forms">' in back
    assert '<div class="ex">{{Example}}</div>' in back
    assert '{{#ExampleTranslation}}<div class="ex-translation">{{ExampleTranslation}}</div>' in back


def test_tab_switcher_nested_guard_checks_both_fields_have_data(patch_language) -> None:
    """Схема объявляет и Example, и Forms — но показывать переключатель можно только
    если у КОНКРЕТНОЙ карточки оба поля непустые. Это не решается на уровне схемы —
    переключатель должен быть обёрнут во вложенные {{#Example}}{{#Forms}}...{{/Forms}}
    {{/Example}}, которые Anki проверяет по факту значений у каждой карточки."""
    patch_language(language="nb")
    back = notetype._build_back_template()
    assert '{{#Example}}{{#Forms}}<div class="switcher">' in back
    assert "</div>{{/Forms}}{{/Example}}" in back


def test_no_switcher_when_only_one_of_example_forms_declared(patch_custom_fields) -> None:
    patch_custom_fields(
        [
            NoteFieldDef(name="Word", source="word", slot="front_title"),
            NoteFieldDef(
                name="Example", source="example", slot="example", optional=True, css_class="ex"
            ),
        ]
    )
    back = notetype._build_back_template()
    assert "switcher" not in back
    assert '<div class="panel" data-tab="examples">' in back
    # generic tab-JS (_TAB_SCRIPT) always mentions data-tab="forms" — it's reused
    # regardless of language; only the panel div itself should be absent here.
    assert '<div class="panel" data-tab="forms">' not in back


def test_footer_has_topic_and_id(patch_language) -> None:
    patch_language(language="nb")
    back = notetype._build_back_template()
    assert '{{#Topic}}<span class="topic">#{{Topic}}</span>{{/Topic}}' in back
    assert '{{#ID}}<span class="id">{{ID}}</span>{{/ID}}' in back


def test_back_template_includes_tab_script(patch_language) -> None:
    patch_language(language="nb")
    back = notetype._build_back_template()
    assert "<script>" in back
    assert 'document.querySelector(".shell.back")' in back
    # tab label is a static "forms" — no "· N" count, no .form-cell counting logic
    assert ">forms</button>" in back
    assert "form-cell" not in back


def test_nest_in_previous_pairs_example_translation_with_example(patch_custom_fields) -> None:
    patch_custom_fields(
        [
            NoteFieldDef(name="Word", source="word", slot="front_title"),
            NoteFieldDef(
                name="Example", source="example", slot="example", optional=True, css_class="ex"
            ),
            NoteFieldDef(
                name="ExampleTranslation",
                source="example_translation",
                slot="example",
                optional=True,
                css_class="ex-translation",
                nest_in_previous=True,
            ),
        ]
    )
    back = notetype._build_back_template()
    assert back.count('<div class="panel" data-tab="examples">') == 1
    assert '<div class="ex">{{Example}}</div>' in back
    assert (
        '{{#ExampleTranslation}}<div class="ex-translation">{{ExampleTranslation}}</div>'
        "{{/ExampleTranslation}}" in back
    )
