"""Определение Note Type для Anki.

Мультиязычная версия: все лейблы, схемы форм и шаблоны
читаются из languages/{code}/language.yaml через LanguageConfig.
Никакого хардкода под конкретный язык.

Поля (универсальные):
    Word, Pronunciation, Translation, Example, ExampleTranslation,
    POS, Forms, Image, Audio, Level, Topic, ID
"""
from __future__ import annotations

from html import escape

from ..config import get_language
from ..models import Card


def _get_note_type_name() -> str:
    return get_language().anki.get("note_type", "LanguageCard")


NOTE_TYPE_NAME_PROP = property(lambda self: _get_note_type_name())  # псевдо-константа


FIELDS = [
    "Word", "Pronunciation", "Translation", "Example", "ExampleTranslation",
    "POS", "Forms", "Image", "Audio", "Level", "Topic", "ID",
]

CSS = """.card {
    font-family: -apple-system, "Segoe UI", "Noto Sans", sans-serif;
    font-size: 20px;
    text-align: left;
    color: #1a1a1a;
    background: #fafafa;
    padding: 24px 32px;
    max-width: 600px;
    margin: 0 auto;
    line-height: 1.6;
}
.word { font-size: 36px; font-weight: 700; text-align: center; margin: 40px 0 8px; }
.pronunciation { text-align: center; color: #888; font-size: 18px; font-style: italic; }
.section { margin-bottom: 20px; }
.label { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #999; margin-bottom: 4px; }  # noqa: E501
.translation { font-size: 26px; font-weight: 600; color: #2c5282; }
.pos { font-size: 16px; color: #555; background: #e8e8e8; display: inline-block; padding: 2px 10px; border-radius: 4px; }  # noqa: E501
.forms { margin: 8px 0; }
.forms table { border-collapse: collapse; width: 100%; }
.forms td { padding: 6px 12px; border-bottom: 1px solid #e2e8f0; font-size: 17px; }
.forms td:first-child { color: #888; font-size: 13px; width: 120px; vertical-align: top; padding-top: 8px; }  # noqa: E501
.forms tr:last-child td { border-bottom: none; }
.example { font-style: italic; color: #2d3748; font-size: 18px; }
.example-translation { color: #718096; font-size: 17px; }
.audio { text-align: center; margin: 8px 0; }
.card-image { text-align: center; margin: 12px 0; }
.card-image img { max-width: 280px; border-radius: 8px; }
.tags { margin-top: 20px; font-size: 12px; color: #a0aec0; }
.tag { display: inline-block; background: #edf2f7; padding: 1px 8px; border-radius: 3px; margin-right: 4px; }"""  # noqa: E501

FRONT_TEMPLATE = """<div class="word">{{Word}}</div>
{{#Audio}}<div class="audio">{{Audio}}</div>{{/Audio}}
{{#Image}}<div class="card-image">{{Image}}</div>{{/Image}}"""


def _build_back_template() -> str:
    """Динамически генерирует BACK_TEMPLATE из language.back_labels."""
    L = get_language().back_labels  # noqa: N806
    parts = [
        '<hr id=answer>',
        f'<div class="section"><div class="label">{L.get("translation","Translation")}</div><div class="translation">{{{{Translation}}}}</div></div>',  # noqa: E501
        f'<div class="section"><div class="label">{L.get("part_of_speech","Part of Speech")}</div><div class="pos">{{{{POS}}}}</div></div>',  # noqa: E501
        '{{#Pronunciation}}',
        f'<div class="section"><div class="label">{L.get("pronunciation","Pronunciation")}</div><div class="pronunciation-ru">{{{{Pronunciation}}}}</div></div>',  # noqa: E501
        '{{/Pronunciation}}',
        '{{#Forms}}',
        f'<div class="section"><div class="label">{L.get("grammar","Grammar")}</div><div class="forms">{{{{Forms}}}}</div></div>',  # noqa: E501
        '{{/Forms}}',
        '{{#Example}}',
        f'<div class="section"><div class="label">{L.get("example","Example")}</div><div class="example">{{{{Example}}}}</div></div>',  # noqa: E501
        '{{/Example}}',
        '{{#ExampleTranslation}}',
        f'<div class="section"><div class="label">{L.get("example_translation","Example Translation")}</div><div class="example-translation">{{{{ExampleTranslation}}}}</div></div>',  # noqa: E501
        '{{/ExampleTranslation}}',
        '<div class="tags">',
        '{{#Level}}<span class="tag">{{Level}}</span>{{/Level}}',
        '{{#Topic}}<span class="tag">{{Topic}}</span>{{/Topic}}',
        '</div>',
    ]
    return "\n".join(parts)


def back_template() -> str:
    return _build_back_template()


def pos_label(pos_value: str) -> str:
    """Название части речи на целевом языке (из language.yaml)."""
    lang = get_language()
    return lang.pos_labels.get(pos_value, pos_value)


# Гендер-маппинг для языков с артиклями (nb, de, fr, …)
# В language.yaml для артиклевых языков: gender переводится через forms.noun[gender].label
# Дополнительный маппинг для норвежского (en/ei/et):
_GENDER_MAP_NB = {"m": "мужской (en)", "f": "женский (ei)", "n": "средний (et)"}
_GENDER_MAP_DE = {"m": "мужской (der)", "f": "женский (die)", "n": "средний (das)"}

_GENDER_MAPS: dict[str, dict[str, str]] = {
    "nb": _GENDER_MAP_NB,
    "de": _GENDER_MAP_DE,
}


def _resolve_gender(gender_value: str | None) -> str:
    """Перевести код рода (m/f/n) в читаемую строку."""
    if not gender_value:
        return ""
    code = get_language().code
    return _GENDER_MAPS.get(code, {}).get(str(gender_value), str(gender_value))


def _render_forms_html(pos_value: str, forms: dict | None) -> str:
    """Отрендерить грамматику в HTML-таблицу по схеме из language.yaml."""
    if not forms:
        return ""

    lang = get_language()
    schema = lang.forms.get(str(pos_value), [])
    if not schema:
        return ""

    parts = ["<table>"]
    for field_def in schema:
        key = field_def["key"]
        label = field_def["label"]
        value = forms.get(key)
        if value is None or value == "":
            continue
        # Для поля gender — перевести код рода в читаемый вид
        if key == "gender":
            value = _resolve_gender(value)
        parts.append(f"<tr><td>{escape(str(label))}</td><td>{escape(str(value))}</td></tr>")
    parts.append("</table>")
    return "".join(parts)


def card_to_anki_fields(card: Card) -> dict[str, str]:
    """Конвертировать Card → словарь полей для AnkiConnect addNote.

    Текстовые поля экранируются: Anki рендерит {{Field}} как сырой HTML в
    webview, а значения (word/translation/example) могут в итоге восходить
    к LLM-обработке чужого веб-контента (ingest url) — без escape() это был
    бы вектор stored-HTML/JS-инъекции в карточку.
    """
    audio_field = f"[sound:{card.audio}]" if card.audio else ""
    image_field = f'<img src="{escape(card.image)}">' if card.image else ""
    forms_html = _render_forms_html(card.pos.value, card.forms)
    pos_display = pos_label(card.pos.value) if card.pos else ""

    return {
        "Word": escape(card.word),
        "Pronunciation": escape(card.pronunciation or ""),
        "Translation": escape(card.translation),
        "Example": escape(card.example or ""),
        "ExampleTranslation": escape(card.example_translation or ""),
        "POS": escape(pos_display),
        "Forms": forms_html,
        "Image": image_field,
        "Audio": audio_field,
        "Level": escape(card.level.value.upper() if card.level else ""),
        "Topic": escape(card.topic or ""),
        "ID": card.id,
    }