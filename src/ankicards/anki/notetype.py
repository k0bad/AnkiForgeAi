"""Определение Note Type для Anki.

Мультиязычная версия: все лейблы, схемы форм и шаблоны
читаются из languages/{code}/language.yaml через LanguageConfig.
Никакого хардкода под конкретный язык.

Набор полей Note Type декларативен (NoteFieldDef в config.py):
    language.yaml -> anki.fields: [...] переопределяет DEFAULT_FIELDS ниже.
    Каждое поле ссылается на источник данных через FIELD_RESOLVERS.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from ..config import EN_BACK_LABELS, LanguageConfig, NoteFieldDef, get_config, get_language
from ..models import Card


class NoteTypeConfigError(Exception):
    """Некорректная схема полей Note Type в language.yaml (anki.fields)."""


def _current_language() -> LanguageConfig:
    """Языковой профиль, выбранный в config.yaml (language: ...)."""
    return get_language(get_config().language)


def _get_note_type_name() -> str:
    return _current_language().anki.note_type


NOTE_TYPE_NAME_PROP = property(lambda self: _get_note_type_name())  # псевдо-константа

CARD_TEMPLATE_NAME = "Recognition"


# ───────────── Схема полей по умолчанию ─────────────
# Дефолтная схема для языка без своего anki.fields (см. issue #17 — "Reference Card":
# POS вынесен в title_meta (стоит рядом со словом на фронте и в шапке бэка вместо
# отдельной секции), ExampleTranslation вложен в Example через nest_in_previous
# (единый блок "пример + перевод" вместо двух секций).


# ВАЖНО: порядок списка — это и inOrderFields (field_names()), и порядок секций
# на бэке (_build_back_template() рендерит section-поля в порядке этого списка).
DEFAULT_FIELDS: list[NoteFieldDef] = [
    NoteFieldDef(
        name="Word", source="word", slot="front_title", css_class="word", recap_on_back=True
    ),
    NoteFieldDef(
        name="Translation",
        source="translation",
        slot="section",
        label_key="translation",
        css_class="translation",
    ),
    NoteFieldDef(
        name="POS",
        source="pos_label",
        slot="title_meta",
        css_class="pos",
        recap_on_back=True,
    ),
    NoteFieldDef(
        name="Pronunciation",
        source="pronunciation",
        slot="section",
        optional=True,
        label_key="pronunciation",
        css_class="pronunciation-ru",
    ),
    NoteFieldDef(
        name="Forms",
        source="forms_html",
        slot="section",
        optional=True,
        label_key="grammar",
        css_class="forms",
    ),
    NoteFieldDef(
        name="Example",
        source="example",
        slot="section",
        optional=True,
        label_key="example",
        css_class="example",
    ),
    NoteFieldDef(
        name="ExampleTranslation",
        source="example_translation",
        slot="section",
        optional=True,
        css_class="example-translation",
        nest_in_previous=True,
    ),
    NoteFieldDef(name="Image", source="image_html", slot="front_image", css_class="card-image"),
    NoteFieldDef(name="Audio", source="audio_html", slot="front_audio", css_class="audio"),
    NoteFieldDef(name="Level", source="level", slot="tag"),
    NoteFieldDef(name="Topic", source="topic", slot="tag"),
    NoteFieldDef(name="ID", source="id", slot="hidden"),
]

CSS = """.card {
    --bg: #F2F1EC;
    --surface: #FBFAF6;
    --ink: #22283A;
    --ink-soft: #5B6373;
    --stamp: #8C2F2F;
    --rule: #C9C3B4;

    font-family: -apple-system, "Segoe UI", "Noto Sans", sans-serif;
    font-size: 20px;
    text-align: left;
    color: var(--ink);
    background: var(--bg);
    padding: 24px 32px;
    max-width: 600px;
    margin: 0 auto;
    line-height: 1.55;
}
/* Anki (desktop/AnkiDroid/AnkiMobile) adds .night_mode to an ancestor of .card */
.night_mode .card {
    --bg: #242220;
    --surface: #2C2926;
    --ink: #EDE6D8;
    --ink-soft: #B9AF9C;
    --stamp: #C1544F;
    --rule: #443F38;
}
@media (max-width: 420px) {
    .card { padding: 16px 20px; font-size: 18px; }
}

.word {
    font-family: Georgia, "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
    font-size: 36px;
    text-align: center;
    margin: 8px 0 4px;
}
.pos {
    font-family: Georgia, "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
    font-style: italic;
    font-size: 15px;
    color: var(--ink-soft);
    margin-left: 6px;
}
.pronunciation-ru {
    display: block;
    text-align: center;
    font-family: "SF Mono", "Cascadia Code", Consolas, "Liberation Mono", monospace;
    font-size: 14px;
    color: var(--ink-soft);
    margin-top: 2px;
}

.card-image {
    background: var(--surface);
    border: 1px solid var(--rule);
    padding: 6px;
    margin: 6px 0 16px;
}
.card-image img { display: block; width: 100%; height: auto; }
.audio { text-align: center; margin: 6px 0; }

.back-recap { text-align: center; padding-bottom: 12px; }
.back-recap .word { font-size: 26px; margin: 0; }
hr#answer { border: none; border-top: 2px solid var(--ink); margin: 0 0 18px; }

.section {
    display: grid;
    grid-template-columns: 90px 1fr;
    column-gap: 14px;
    row-gap: 4px;
    align-items: baseline;
    margin-bottom: 14px;
}
@media (max-width: 420px) { .section { grid-template-columns: 70px 1fr; } }
@media (max-width: 320px) { .section { grid-template-columns: 1fr; row-gap: 2px; } }
.label {
    font-family: "SF Mono", "Cascadia Code", Consolas, "Liberation Mono", monospace;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--ink-soft);
}
.translation { font-size: 22px; font-weight: 600; }
.forms table { border-collapse: collapse; width: 100%; }
.forms td { padding: 3px 10px 3px 0; font-size: 16px; }
.forms td:first-child { color: var(--ink-soft); white-space: nowrap; }
.example {
    font-style: italic;
    font-size: 17px;
    background: var(--surface);
    border-left: 3px solid var(--stamp);
    padding: 8px 12px;
}
.example-translation {
    display: block;
    font-style: normal;
    color: var(--ink-soft);
    font-size: 14px;
    margin-top: 4px;
}

.tags {
    margin-top: 18px;
    padding-top: 10px;
    border-top: 1px solid var(--rule);
    display: flex;
    gap: 12px;
    font-family: "SF Mono", "Cascadia Code", Consolas, "Liberation Mono", monospace;
    font-size: 11px;
    color: var(--ink-soft);
    text-transform: uppercase;
    letter-spacing: 0.03em;
}"""


# ───────────── Резолвинг активной схемы полей ─────────────


def _active_fields() -> list[NoteFieldDef]:
    """Схема полей активного языка: anki.fields из language.yaml, иначе DEFAULT_FIELDS."""
    fields = _current_language().anki.fields
    fields = fields if fields is not None else DEFAULT_FIELDS
    _validate_fields(fields)
    return fields


def _validate_fields(fields: list[NoteFieldDef]) -> None:
    lang_code = _current_language().code
    names = [f.name for f in fields]
    if len(names) != len(set(names)):
        raise NoteTypeConfigError(
            f"Duplicate field names in anki.fields for language {lang_code!r}: {names}"
        )

    unique_slots = ("front_title", "front_audio", "front_image", "title_meta")
    seen: dict[str, str] = {}
    for field in fields:
        if field.source not in FIELD_RESOLVERS:
            raise NoteTypeConfigError(
                f"Unknown field source {field.source!r} for field {field.name!r} "
                f"in language {lang_code!r}. Known sources: {sorted(FIELD_RESOLVERS)}"
            )
        if field.slot in unique_slots:
            if field.slot in seen:
                raise NoteTypeConfigError(
                    f"Slot {field.slot!r} is claimed by both {seen[field.slot]!r} and "
                    f"{field.name!r} in language {lang_code!r} — only one field per slot allowed"
                )
            seen[field.slot] = field.name


def validate_active_fields() -> None:
    """Проверить схему полей активного языка заранее (вызывается из `ankiforgeai init`),
    чтобы опечатка в anki.fields ловилась при инициализации, а не в середине enrichment."""
    _active_fields()


def field_names() -> list[str]:
    """Имена полей Note Type в порядке объявления — для AnkiConnect createModel."""
    return [f.name for f in _active_fields()]


def front_template() -> str:
    """HTML фронта карточки: front_title (всегда, + title_meta инлайном рядом, если есть),
    затем front_audio/front_image (guarded)."""
    front_slots = ("front_title", "front_audio", "front_image", "title_meta")
    by_slot: dict[str, NoteFieldDef] = {
        f.slot: f for f in _active_fields() if f.slot in front_slots
    }
    parts: list[str] = []

    title = by_slot.get("front_title")
    if title is not None:
        css_class = title.css_class or title.name.lower()
        inner = f"{{{{{title.name}}}}}"
        meta = by_slot.get("title_meta")
        if meta is not None:
            meta_class = meta.css_class or meta.name.lower()
            meta_html = f'<span class="{meta_class}">{{{{{meta.name}}}}}</span>'
            if meta.optional:
                meta_html = f"{{{{#{meta.name}}}}}{meta_html}{{{{/{meta.name}}}}}"
            inner = f"{inner} {meta_html}"
        parts.append(f'<div class="{css_class}">{inner}</div>')

    for slot, css_default in (("front_audio", "audio"), ("front_image", "card-image")):
        field = by_slot.get(slot)
        if field is None:
            continue
        css_class = field.css_class or css_default
        guard_open, guard_close = f"{{{{#{field.name}}}}}", f"{{{{/{field.name}}}}}"
        inner = f'<div class="{css_class}">{{{{{field.name}}}}}</div>'
        parts.append(f"{guard_open}{inner}{guard_close}")

    return "\n".join(parts)


def _back_labels() -> dict[str, str]:
    """Подписи бэк-стороны: EN_BACK_LABELS если cfg.ui_language == "en", иначе из language.yaml."""
    if get_config().ui_language == "en":
        return EN_BACK_LABELS
    return _current_language().back_labels


def _recap_html(fields: list[NoteFieldDef]) -> str:
    """Шапка бэка: поля с recap_on_back (обычно front_title + title_meta), повтор в одной строке."""
    recap_fields = [f for f in fields if f.recap_on_back]
    if not recap_fields:
        return ""
    pieces: list[str] = []
    for field in recap_fields:
        css_class = field.css_class or field.name.lower()
        piece = f'<span class="{css_class}">{{{{{field.name}}}}}</span>'
        if field.optional:
            piece = f"{{{{#{field.name}}}}}{piece}{{{{/{field.name}}}}}"
        pieces.append(piece)
    return f'<div class="back-recap">{" ".join(pieces)}</div>'


def _build_back_template() -> str:
    """Динамически генерирует BACK_TEMPLATE из активной схемы полей."""
    L = _back_labels()  # noqa: N806
    fields = _active_fields()
    section_parts: list[str] = []
    tag_parts: list[str] = []

    for idx, field in enumerate(fields):
        if field.nest_in_previous:
            continue  # уже отрендерено внутри div предыдущего section-поля, см. ниже
        if field.slot == "section":
            label_key = field.label_key or field.name.lower()
            css_class = field.css_class or field.name.lower()
            label = L.get(label_key, EN_BACK_LABELS.get(label_key, field.name))
            content = f"{{{{{field.name}}}}}"
            for nested in fields[idx + 1 :]:
                if not nested.nest_in_previous:
                    break
                nested_class = nested.css_class or nested.name.lower()
                nested_html = f'<span class="{nested_class}">{{{{{nested.name}}}}}</span>'
                if nested.optional:
                    nested_html = f"{{{{#{nested.name}}}}}{nested_html}{{{{/{nested.name}}}}}"
                content += nested_html
            div = (
                f'<div class="section"><div class="label">{label}</div>'
                f'<div class="{css_class}">{content}</div></div>'
            )
            if field.optional:
                section_parts.append(f"{{{{#{field.name}}}}}")
                section_parts.append(div)
                section_parts.append(f"{{{{/{field.name}}}}}")
            else:
                section_parts.append(div)
        elif field.slot == "tag":
            guard_open, guard_close = f"{{{{#{field.name}}}}}", f"{{{{/{field.name}}}}}"
            pill = f'<span class="tag">{{{{{field.name}}}}}</span>'
            tag_parts.append(f"{guard_open}{pill}{guard_close}")
        # front_title / front_audio / front_image / title_meta / hidden — на бэке не показываются
        # напрямую (кроме recap_on_back — см. _recap_html выше)

    recap = _recap_html(fields)
    parts = [recap] if recap else []
    parts += ["<hr id=answer>", *section_parts, '<div class="tags">', *tag_parts, "</div>"]
    return "\n".join(parts)


def back_template() -> str:
    return _build_back_template()


def pos_label(pos_value: str) -> str:
    """Название части речи на целевом языке (из language.yaml)."""
    lang = _current_language()
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
    code = _current_language().code
    return _GENDER_MAPS.get(code, {}).get(str(gender_value), str(gender_value))


def _render_forms_html(pos_value: str, forms: dict | None) -> str:
    """Отрендерить грамматику в HTML-таблицу по схеме из language.yaml."""
    if not forms:
        return ""

    lang = _current_language()
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


# ───────────── Резолверы источников данных (NoteFieldDef.source) ─────────────


def _resolve_word(card: Card) -> str:
    return escape(card.word)


def _resolve_pronunciation(card: Card) -> str:
    return escape(card.pronunciation or "")


def _resolve_translation(card: Card) -> str:
    return escape(card.translation)


def _resolve_example(card: Card) -> str:
    return escape(card.example or "")


def _resolve_example_translation(card: Card) -> str:
    return escape(card.example_translation or "")


def _resolve_pos_label(card: Card) -> str:
    return escape(pos_label(card.pos.value)) if card.pos else ""


def _resolve_forms_html(card: Card) -> str:
    return _render_forms_html(card.pos.value, card.forms)


def _resolve_image_html(card: Card) -> str:
    return f'<img src="{escape(card.image)}">' if card.image else ""


def _resolve_audio_html(card: Card) -> str:
    return f"[sound:{card.audio}]" if card.audio else ""


def _resolve_level(card: Card) -> str:
    return escape(card.level.value.upper() if card.level else "")


def _resolve_topic(card: Card) -> str:
    return escape(card.topic or "")


def _resolve_id(card: Card) -> str:
    return card.id


FIELD_RESOLVERS: dict[str, Callable[[Card], str]] = {
    "word": _resolve_word,
    "pronunciation": _resolve_pronunciation,
    "translation": _resolve_translation,
    "example": _resolve_example,
    "example_translation": _resolve_example_translation,
    "pos_label": _resolve_pos_label,
    "forms_html": _resolve_forms_html,
    "image_html": _resolve_image_html,
    "audio_html": _resolve_audio_html,
    "level": _resolve_level,
    "topic": _resolve_topic,
    "id": _resolve_id,
}


def card_to_anki_fields(card: Card) -> dict[str, str]:
    """Конвертировать Card → словарь полей для AnkiConnect addNote.

    Текстовые поля экранируются резолверами в FIELD_RESOLVERS: Anki рендерит
    {{Field}} как сырой HTML в webview, а значения (word/translation/example)
    могут в итоге восходить к LLM-обработке чужого веб-контента (ingest url) —
    без escape() это был бы вектор stored-HTML/JS-инъекции в карточку.
    Image/Audio — единственные источники, которые намеренно возвращают сырой
    HTML (img-тег, [sound:]).
    """
    result: dict[str, str] = {}
    for field in _active_fields():
        resolver = FIELD_RESOLVERS[field.source]
        result[field.name] = resolver(card)
    return result
