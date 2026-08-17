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

from ..config import LanguageConfig, NoteFieldDef, get_config, get_language
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
# "Business card" дизайн (design_handoff_language_card): физическая карточка
# 85x55мм. Front — фоновое фото слоями (размытая подложка + чёткий вырезанный
# subject + directional-скрим) под word/POS/audio. Back — компактная шапка
# (recap word+pronunciation, POS/Level/Audio пилюли, Translation справа),
# затем табы examples/forms (реальный JS-переключатель, см. _TAB_SCRIPT),
# footer с Topic/ID. POS/Audio/Image используются и на фронте, и на бэке —
# у каждого свой слот (pos/front_audio/front_image), задействованный обоими
# билдерами шаблона напрямую, без отдельного механизма recap.

# ВАЖНО: порядок списка — это inOrderFields (field_names(), порядок полей в
# редакторе заметок Anki). На порядок зон бэка не влияет — тот фиксирован в
# _build_back_template(); порядок влияет только на соседство nest_in_previous
# (ExampleTranslation должна идти сразу за Example).
DEFAULT_FIELDS: list[NoteFieldDef] = [
    NoteFieldDef(name="Word", source="word", slot="front_title", css_class="word"),
    NoteFieldDef(
        name="Pronunciation",
        source="pronunciation",
        slot="front_sub",
        optional=True,
        css_class="ipa",
    ),
    NoteFieldDef(
        name="Translation", source="translation", slot="translation", css_class="translation"
    ),
    NoteFieldDef(name="Example", source="example", slot="example", optional=True, css_class="ex"),
    NoteFieldDef(
        name="ExampleTranslation",
        source="example_translation",
        slot="example",
        optional=True,
        css_class="ex-translation",
        nest_in_previous=True,
    ),
    NoteFieldDef(name="POS", source="pos_pill_html", slot="pos"),
    NoteFieldDef(
        name="Forms", source="forms_grid_html", slot="forms", optional=True, css_class="forms-grid"
    ),
    NoteFieldDef(
        name="Image", source="image_html", slot="front_image", optional=True, css_class="bg"
    ),
    NoteFieldDef(name="Audio", source="audio_html", slot="front_audio", css_class="audio"),
    NoteFieldDef(name="Level", source="level", slot="level", optional=True, css_class="level"),
    NoteFieldDef(name="Topic", source="topic", slot="topic", optional=True, css_class="topic"),
    NoteFieldDef(name="ID", source="id", slot="id", optional=True, css_class="id"),
]

CSS = """.card {
    --bg: #f4f3f0;
    --card: #ffffff;
    --card2: #f6f5f2;
    --line: #e2e0da;
    --ink: #191917;
    --ink2: #6a6a64;
    --ink3: #9b9b93;
    --noun: #0f9c72;
    --verb: #2f6fd0;
    --adj: #b4681c;
    --adv: #8c46b8;
    --shadow: 0 14px 34px rgba(30, 28, 22, .14);

    background: var(--bg);
    color: var(--ink);
    font-family: "IBM Plex Sans", Helvetica, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 12px;
    color-scheme: light;
}
/* Anki (desktop/AnkiDroid/AnkiMobile) adds .night_mode to an ancestor of .card.
   color-scheme opts .card out of Qt WebEngine's Chromium ForceDarkMode, which Anki
   enables whenever night mode is on regardless of whether the card already ships its
   own dark palette (see issue #17) — without it, ForceDarkMode re-darkens already-dark
   colors on top, washing most of them out. */
.night_mode .card {
    --bg: #141416;
    --card: #1c1c1f;
    --card2: #232327;
    --line: #303036;
    --ink: #f2f2f4;
    --ink2: #a6a6ae;
    --ink3: #6e6e78;
    --noun: #56dfae;
    --verb: #5b9df9;
    --adj: #e8a15e;
    --adv: #c77df0;
    --shadow: 0 18px 40px rgba(0, 0, 0, .55);
    color-scheme: dark;
}

/* ---------- shell: fixed physical business-card size (85 x 55mm) ---------- */

.shell {
    position: relative;
    width: 85mm;
    height: 55mm;
    box-sizing: border-box;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 3mm;
    box-shadow: var(--shadow);
    overflow: hidden;
    font-family: "IBM Plex Sans", Helvetica, sans-serif;
}
/* Desktop review window has plenty of room and sits further from the eye than a
   phone — scale the whole card up rather than hand-tuning every mm/pt value a
   second time. transform keeps every internal proportion identical, just bigger;
   .card centers .shell with nothing else competing for space, so the extra visual
   footprint from scaling (transform doesn't grow the layout box) has nowhere to
   collide. Below 600px (phone width) the card stays true to its 85 x 55mm size. */
@media (min-width: 600px) {
    .shell { transform: scale(1.3); }
}
.mono { font-family: "IBM Plex Mono", ui-monospace, monospace; }

/* ---------- shared: pos pill ---------- */

.pos-pill {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-weight: 500;
    border: 1px solid currentColor;
    border-radius: 999px;
    white-space: nowrap;
}
.pos-noun { color: var(--noun); }
.pos-verb { color: var(--verb); }
.pos-adj { color: var(--adj); }
.pos-adv { color: var(--adv); }

/* ---------- front ---------- */

.front { padding: 4.5mm; display: flex; }

.front .bg { position: absolute; inset: 0; overflow: hidden; }
.front .bg-ambient img {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    filter: blur(14px) saturate(.7);
    opacity: .28;
    transform: scale(1.15);
}
.front .bg-subject img {
    position: absolute;
    top: 2mm;
    right: 1mm;
    bottom: 1mm;
    width: 44%;
    object-fit: contain;
    object-position: bottom right;
    opacity: .95;
    -webkit-mask-image: radial-gradient(ellipse 78% 82% at 62% 55%, #000 52%, transparent 88%);
    mask-image: radial-gradient(ellipse 78% 82% at 62% 55%, #000 52%, transparent 88%);
}
.front .scrim {
    position: absolute;
    inset: 0;
    background: linear-gradient(78deg, var(--card) 30%, rgba(0, 0, 0, 0) 78%);
}

.front .content {
    position: relative;
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
}
.front .top-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 3mm;
}
.front .pos-pill { font-size: 8pt; padding: 1mm 2.6mm; }
.front .audio-circle {
    width: 8.5mm;
    height: 8.5mm;
    border-radius: 50%;
    border: 1px solid var(--line);
    background: var(--card2);
    display: flex;
    align-items: center;
    justify-content: center;
}
/* [sound:] renders as Anki's own <a class="replay-button"><svg>...</svg></a> (40x40px,
   filled circle by default) — our own circle/pill already provides the background, so
   we shrink the icon and blank out Anki's built-in circle, keeping only the triangle. */
.audio-circle .replay-button, .audio-pill .replay-button {
    display: inline-flex;
    text-decoration: none;
}
.audio-circle .replay-button svg { width: 9pt; height: 9pt; }
.audio-pill .replay-button svg { width: 7pt; height: 7pt; }
.audio-circle .replay-button svg circle, .audio-pill .replay-button svg circle {
    fill: none;
    stroke: none;
}
.audio-circle .replay-button svg path { fill: var(--ink2); stroke: var(--ink2); }
.audio-pill .replay-button svg path { fill: var(--ink2); stroke: var(--ink2); }
.front .spacer { flex: 1; }
.front .word-block { display: flex; flex-direction: column; gap: 1.5mm; }
.front .word { font-size: 22pt; font-weight: 600; letter-spacing: -.02em; line-height: 1; }
.front .ipa {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 9pt;
    color: var(--ink2);
}

/* Shown instead of the bg/scrim stack when the card has no Image
   (design_handoff_image_placeholder) — same footprint as .bg-subject so layout doesn't
   shift once a real image is added later. */
.front .bg-empty {
    position: absolute;
    top: 2mm;
    right: 1mm;
    bottom: 1mm;
    width: 44%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 1.5mm;
    opacity: .55;
}
.front .bg-empty .mark {
    width: 9mm;
    height: 9mm;
    border-radius: 50%;
    border: 1px dashed var(--line);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 12pt;
    color: var(--ink3);
}
.front .bg-empty .cap {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 6pt;
    letter-spacing: .14em;
    text-transform: uppercase;
    color: var(--ink3);
}

/* ---------- back ---------- */

.back { padding: 4mm 4.5mm 3.5mm; display: flex; flex-direction: column; gap: 2.5mm; }

.back .bg-back {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    width: 32%;
}
.back .bg-back img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    object-position: bottom right;
    opacity: .1;
    -webkit-mask-image: radial-gradient(ellipse 80% 85% at 60% 55%, #000 46%, transparent 86%);
    mask-image: radial-gradient(ellipse 80% 85% at 60% 55%, #000 46%, transparent 86%);
}

/* Shown instead of .bg-back when the card has no Image (design_handoff_image_placeholder). */
.back .bg-back-empty {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    width: 32%;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: .55;
}
.back .bg-back-empty .mark {
    width: 6mm;
    height: 6mm;
    border-radius: 50%;
    border: 1px dashed var(--line);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 8pt;
    color: var(--ink3);
}

.back .header { position: relative; display: flex; gap: 3mm; align-items: center; }
.back .header-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1mm; }
.back .baseline { display: flex; align-items: baseline; gap: 2mm; }
.back .word { font-size: 12pt; font-weight: 600; letter-spacing: -.01em; }
.back .ipa {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 7.5pt;
    color: var(--ink2);
}
.back .meta-row { display: flex; align-items: center; gap: 1.6mm; flex-wrap: wrap; }
.back .pos-pill { font-size: 7pt; padding: .6mm 2mm; }
.back .level-pill {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 7pt;
    color: var(--ink3);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: .6mm 2mm;
}
.back .audio-pill {
    display: inline-flex;
    align-items: center;
    gap: .8mm;
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 7pt;
    color: var(--ink2);
    background: var(--card2);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: .6mm 2mm;
}
.back .translation {
    flex: none;
    text-align: right;
    font-size: 12pt;
    font-weight: 600;
    line-height: 1.1;
    letter-spacing: -.01em;
    max-width: 34mm;
}

.back .divider { position: relative; height: 1px; background: var(--line); }

.back .body { position: relative; flex: 1; min-height: 0; overflow: hidden; }
.back .panel {
    display: none;
    height: 100%;
    overflow-y: auto;
    flex-direction: column;
    padding-left: 2.5mm;
    border-left: 2px solid var(--line);
}
.back .panel.active { display: flex; }

.back .example-pair { display: flex; flex-direction: column; gap: 1mm; }
.back .ex { font-size: 8.5pt; line-height: 1.32; text-wrap: pretty; }
.back .ex-translation { font-size: 8pt; line-height: 1.32; color: var(--ink2); text-wrap: pretty; }

.back .forms-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1.4mm 4mm; }
.back .form-cell {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 8.5pt;
}
.back .form-cell .fl { color: var(--ink2); }
.back .form-cell .fv { color: var(--ink); }

.back .footer {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 2mm;
    padding-top: 1.8mm;
    border-top: 1px solid var(--line);
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 7pt;
}
.back .topic { color: var(--ink2); }
.back .id { color: var(--ink3); }
.back .switcher { display: flex; align-items: center; gap: 1.5mm; }
.back .tab-btn {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 7pt;
    border: 1px solid var(--line);
    background: transparent;
    border-radius: 999px;
    padding: .6mm 2mm;
    color: var(--ink3);
    cursor: pointer;
}
.back .tab-btn.active { color: var(--ink); }"""

_TAB_SCRIPT = """<script>
(function () {
    var root = document.querySelector(".shell.back");
    if (!root) return;
    var panels = root.querySelectorAll(".panel");
    var buttons = root.querySelectorAll(".tab-btn");
    function show(tab) {
        panels.forEach(function (p) { p.classList.toggle("active", p.dataset.tab === tab); });
        buttons.forEach(function (b) { b.classList.toggle("active", b.dataset.tab === tab); });
    }
    buttons.forEach(function (b) {
        b.addEventListener("click", function () { show(b.dataset.tab); });
    });
    if (panels.length) show(panels[0].dataset.tab);
})();
</script>"""


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

    unique_slots = (
        "front_title",
        "front_sub",
        "front_audio",
        "front_image",
        "pos",
        "translation",
        "level",
        "topic",
        "id",
    )
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


def diff_fields(anki_fields: list[str]) -> tuple[list[str], list[str]]:
    """(missing_in_anki, extra_in_anki) — сравнение реальных полей Note Type в Anki
    (AnkiConnect.model_field_names) со схемой в коде (anki.fields в language.yaml).

    init никогда не переносит это расхождение сам: modelFieldAdd/Remove/Rename
    затрагивают данные уже существующих заметок, это осознанно ручная операция."""
    local_fields = field_names()
    missing_in_anki = [f for f in local_fields if f not in anki_fields]
    extra_in_anki = [f for f in anki_fields if f not in local_fields]
    return missing_in_anki, extra_in_anki


def _guard(field: NoteFieldDef, inner: str) -> str:
    return f"{{{{#{field.name}}}}}{inner}{{{{/{field.name}}}}}" if field.optional else inner


def _guard_with_fallback(field: NoteFieldDef, present: str, absent: str) -> str:
    """Как _guard, но с явной альтернативой на случай пустого поля — Anki поддерживает
    инвертированную секцию {{^Field}}...{{/Field}} (рендерится, только если поле пустое)."""
    return (
        f"{{{{#{field.name}}}}}{present}{{{{/{field.name}}}}}"
        f"{{{{^{field.name}}}}}{absent}{{{{/{field.name}}}}}"
    )


def front_template() -> str:
    """HTML фронта: фоновый стек фото (ambient blur + subject + scrim), поверх —
    top-row (POS pill + audio) и word-block (Word + Pronunciation), прижатый вниз."""
    by_slot: dict[str, NoteFieldDef] = {f.slot: f for f in _active_fields()}
    parts: list[str] = []

    image = by_slot.get("front_image")
    if image is not None:
        img = f"{{{{{image.name}}}}}"
        present = (
            f'<div class="{image.css_class or "bg"}">'
            f'<div class="bg-ambient">{img}</div><div class="bg-subject">{img}</div></div>'
            f'<div class="scrim"></div>'
        )
        if image.optional:
            absent = (
                '<div class="bg-empty"><div class="mark">?</div>'
                '<div class="cap">no image</div></div>'
            )
            parts.append(_guard_with_fallback(image, present, absent))
        else:
            parts.append(present)

    top_row: list[str] = []
    pos = by_slot.get("pos")
    if pos is not None:
        top_row.append(_guard(pos, f"{{{{{pos.name}}}}}"))
    audio = by_slot.get("front_audio")
    if audio is not None:
        css_class = audio.css_class or "audio"
        circle = f'<span class="{css_class}-circle">{{{{{audio.name}}}}}</span>'
        top_row.append(_guard(audio, circle))

    word_block: list[str] = []
    title = by_slot.get("front_title")
    if title is not None:
        css_class = title.css_class or title.name.lower()
        word_block.append(f'<div class="{css_class}">{{{{{title.name}}}}}</div>')
    sub = by_slot.get("front_sub")
    if sub is not None:
        css_class = sub.css_class or sub.name.lower()
        word_block.append(_guard(sub, f'<div class="{css_class}">{{{{{sub.name}}}}}</div>'))

    content = (
        '<div class="content">\n'
        f'    <div class="top-row">{" ".join(top_row)}</div>\n'
        '    <div class="spacer"></div>\n'
        f'    <div class="word-block">{"".join(word_block)}</div>\n'
        "  </div>"
    )
    parts.append(content)

    return '<div class="shell front">\n  ' + "\n  ".join(parts) + "\n</div>"


def _build_header(by_slot: dict[str, NoteFieldDef]) -> str:
    baseline: list[str] = []
    title = by_slot.get("front_title")
    if title is not None:
        css_class = title.css_class or title.name.lower()
        baseline.append(f'<span class="{css_class}">{{{{{title.name}}}}}</span>')
    sub = by_slot.get("front_sub")
    if sub is not None:
        css_class = sub.css_class or sub.name.lower()
        baseline.append(_guard(sub, f'<span class="{css_class}">{{{{{sub.name}}}}}</span>'))

    meta: list[str] = []
    pos = by_slot.get("pos")
    if pos is not None:
        meta.append(_guard(pos, f"{{{{{pos.name}}}}}"))
    level = by_slot.get("level")
    if level is not None:
        css_class = level.css_class or "level-pill"
        meta.append(_guard(level, f'<span class="{css_class}-pill">{{{{{level.name}}}}}</span>'))
    audio = by_slot.get("front_audio")
    if audio is not None:
        css_class = audio.css_class or "audio"
        audio_pill = (
            f'<span class="{css_class}-pill">{{{{{audio.name}}}}}'
            '<span class="audio-label">audio</span></span>'
        )
        meta.append(_guard(audio, audio_pill))

    header_left = (
        '<div class="header-left">\n'
        f'      <div class="baseline">{" ".join(baseline)}</div>\n'
        f'      <div class="meta-row">{" ".join(meta)}</div>\n'
        "    </div>"
    )

    translation = by_slot.get("translation")
    translation_html = ""
    if translation is not None:
        css_class = translation.css_class or translation.name.lower()
        translation_html = f'\n    <div class="{css_class}">{{{{{translation.name}}}}}</div>'

    return '<div class="header">\n    ' + header_left + translation_html + "\n  </div>"


def _build_example_panel(fields: list[NoteFieldDef], idx: int, field: NoteFieldDef) -> str:
    """example-поле (+ вложенные nest_in_previous, например ExampleTranslation) → пара
    .ex/.ex-translation в панели examples. Без видимого лейбла — дизайн бизнес-карты
    держит всё на пиктограммах/пилюлях, место под текстовые лейблы не заложено."""
    css_class = field.css_class or "ex"
    pair = [f'<div class="{css_class}">{{{{{field.name}}}}}</div>']
    for nested in fields[idx + 1 :]:
        if not nested.nest_in_previous:
            break
        nested_class = nested.css_class or "ex-translation"
        pair.append(_guard(nested, f'<div class="{nested_class}">{{{{{nested.name}}}}}</div>'))
    panel = (
        '<div class="panel" data-tab="examples">\n'
        f'      <div class="example-pair">{"".join(pair)}</div>\n'
        "    </div>"
    )
    return _guard(field, panel)


def _build_forms_panel(field: NoteFieldDef) -> str:
    panel = (
        '<div class="panel" data-tab="forms">\n'
        f'      <div class="{field.css_class or "forms-grid"}">{{{{{field.name}}}}}</div>\n'
        "    </div>"
    )
    return _guard(field, panel)


def _build_footer(
    by_slot: dict[str, NoteFieldDef],
    example_field: NoteFieldDef | None,
    forms_field: NoteFieldDef | None,
) -> str:
    topic = by_slot.get("topic")
    topic_html = ""
    if topic is not None:
        css_class = topic.css_class or "topic"
        topic_html = _guard(topic, f'<span class="{css_class}">#{{{{{topic.name}}}}}</span>')

    switcher = ""
    if example_field is not None and forms_field is not None:
        # Оба поля объявлены в схеме языка — но показывать переключатель нужно только
        # если у КОНКРЕТНОЙ карточки есть данные в обоих (иначе один таб пуст) — это
        # решается не тут (на уровне схемы), а вложенными guard'ами, которые Anki
        # проверяет по факту значений полей у каждой карточки при рендере.
        switcher_html = (
            '<div class="switcher">\n'
            '        <button class="tab-btn active" data-tab="examples" type="button">'
            "examples</button>\n"
            '        <button class="tab-btn" data-tab="forms" type="button">forms</button>\n'
            "      </div>"
        )
        switcher = (
            f"{{{{#{example_field.name}}}}}{{{{#{forms_field.name}}}}}"
            f"{switcher_html}"
            f"{{{{/{forms_field.name}}}}}{{{{/{example_field.name}}}}}"
        )

    id_field = by_slot.get("id")
    id_html = ""
    if id_field is not None:
        css_class = id_field.css_class or "id"
        id_html = _guard(id_field, f'<span class="{css_class}">{{{{{id_field.name}}}}}</span>')

    return f'<div class="footer">\n    {topic_html}\n    {switcher}\n    {id_html}\n  </div>'


def _build_back_template() -> str:
    """Динамически генерирует BACK_TEMPLATE: header (recap word/pronunciation +
    pos/level/audio pills + translation) → divider → tabbed body (examples/forms,
    переключаются _TAB_SCRIPT) → footer (topic + switcher + id)."""
    fields = _active_fields()
    by_slot: dict[str, NoteFieldDef] = {f.slot: f for f in fields}

    parts: list[str] = []

    image = by_slot.get("front_image")
    if image is not None:
        img = f"{{{{{image.name}}}}}"
        present = f'<div class="bg-back">{img}</div>'
        if image.optional:
            absent = '<div class="bg-back-empty"><div class="mark">?</div></div>'
            parts.append(_guard_with_fallback(image, present, absent))
        else:
            parts.append(present)

    parts.append(_build_header(by_slot))
    parts.append('<div class="divider"></div>')

    body_parts: list[str] = []
    example_field = forms_field = None
    for idx, field in enumerate(fields):
        if field.slot == "example" and not field.nest_in_previous:
            body_parts.append(_build_example_panel(fields, idx, field))
            example_field = field
        elif field.slot == "forms":
            body_parts.append(_build_forms_panel(field))
            forms_field = field
    parts.append('<div class="body">\n    ' + "\n    ".join(body_parts) + "\n  </div>")

    parts.append(_build_footer(by_slot, example_field, forms_field))

    shell = '<div class="shell back">\n  ' + "\n  ".join(parts) + "\n</div>"
    return shell + "\n" + _TAB_SCRIPT


def back_template() -> str:
    return _build_back_template()


def pos_label(pos_value: str) -> str:
    """Название части речи на целевом языке (из language.yaml)."""
    lang = _current_language()
    return lang.pos_labels.get(pos_value, pos_value)


# Гендер-маппинг для языков с артиклями (nb, de, fr, …)
# В language.yaml для артиклевых языков: gender переводится через forms.noun[gender].label
# Дополнительный маппинг для норвежского (en/ei/et):
_GENDER_MAP_NB = {"m": "Hankjønn (en)", "f": "Hunkjønn (ei)", "n": "Intetkjønn (et)"}
_GENDER_MAP_DE = {"m": "Maskulinum (der)", "f": "Femininum (die)", "n": "Neutrum (das)"}

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


def _render_forms_grid_html(pos_value: str, forms: dict | None) -> str:
    """Отрендерить грамматику как ячейки 2-колоночного грида: <div class="form-cell">
    <span class="fl">Label</span> <span class="fv">value</span></div>."""
    if not forms:
        return ""

    lang = _current_language()
    schema = lang.forms.get(str(pos_value), [])
    if not schema:
        return ""

    cells = []
    for field_def in schema:
        key = field_def["key"]
        label = field_def["label"]
        value = forms.get(key)
        if value is None or value == "":
            continue
        if key == "gender":
            value = _resolve_gender(value)
        cells.append(
            f'<div class="form-cell"><span class="fl">{escape(str(label))}</span> '
            f'<span class="fv">{escape(str(value))}</span></div>'
        )
    return "".join(cells)


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


def _resolve_pos_pill_html(card: Card) -> str:
    """<span class="pos-pill pos-{value}">{локализованный лейбл}</span> — цветовое
    кодирование по card.pos.value (см. .pos-noun/.pos-verb/.pos-adj/.pos-adv в CSS),
    видимый текст — локализованный лейбл из language.yaml."""
    if not card.pos:
        return ""
    slug = escape(card.pos.value)
    label = escape(pos_label(card.pos.value))
    return f'<span class="pos-pill pos-{slug}">{label}</span>'


def _resolve_forms_grid_html(card: Card) -> str:
    return _render_forms_grid_html(card.pos.value, card.forms)


def _resolve_image_html(card: Card) -> str:
    return f'<img src="{escape(card.image)}" alt="">' if card.image else ""


def _resolve_audio_html(card: Card) -> str:
    return f"[sound:{card.audio}]" if card.audio else ""


def _resolve_level(card: Card) -> str:
    return escape(card.level.value.upper() if card.level else "")


def _resolve_topic(card: Card) -> str:
    return escape(card.topic or "")


def _resolve_id(card: Card) -> str:
    return str(card.id)


FIELD_RESOLVERS: dict[str, Callable[[Card], str]] = {
    "word": _resolve_word,
    "pronunciation": _resolve_pronunciation,
    "translation": _resolve_translation,
    "example": _resolve_example,
    "example_translation": _resolve_example_translation,
    "pos_pill_html": _resolve_pos_pill_html,
    "forms_grid_html": _resolve_forms_grid_html,
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
    Image/Audio/POS/Forms — единственные источники, которые намеренно возвращают
    сырой HTML (img-тег, [sound:], цветной pill, грид ячеек) — значения внутри экранированы.
    """
    result: dict[str, str] = {}
    for field in _active_fields():
        resolver = FIELD_RESOLVERS[field.source]
        result[field.name] = resolver(card)
    return result
