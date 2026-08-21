"""Визуальный ревью-лист: карточки на review одной страницей HTML.

Терминальный `ankiforgeai review` показывает карточки по одной — это нормально для
десятка кандидатов от LLM, но не для импорта, где за раз приезжает сотня слов с
готовыми фотографиями: главный вопрос там не «что написано», а «подходит ли
картинка слову», и на него отвечает глаз, а не чтение полей подряд.

Страница самодостаточна: картинки и аудио вшиты как data: URI, внешних запросов
нет вовсе — файл одинаково открывается локально, уезжает в Artifact или просто
пересылается. Отмеченные к отбраковке карточки собираются в готовую команду
`ankiforgeai review skip ...`, то есть страница ничего не решает сама — решение
всё равно исполняет CLI.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

from ..config import Config
from ..log import get_logger
from ..models import Card

logger = get_logger(__name__)

# Свыше этого data: URI картинок начинают весить больше, чем стоит их польза
# на ревью — 16 МБ жёсткий потолок Artifact, остальное запас на аудио и разметку.
SIZE_WARN_BYTES = 12 * 1024 * 1024

POS_LABELS = {
    "noun": "сущ.",
    "verb": "глаг.",
    "adj": "прил.",
    "adv": "нареч.",
    "pron": "мест.",
    "prep": "предл.",
    "conj": "союз",
    "interj": "межд.",
    "num": "числ.",
    "phrase": "фраза",
    "other": "—",
}

FORM_LABELS = {
    "gender": "род",
    "indefinite_singular": "ед. неопр.",
    "definite_singular": "ед. опр.",
    "indefinite_plural": "мн. неопр.",
    "definite_plural": "мн. опр.",
    "infinitive": "инфинитив",
    "present": "présens",
    "past": "претерит",
    "perfect": "перфект",
    "positive_common": "полож.",
    "positive_neuter": "полож. ср.",
    "positive_plural": "полож. мн.",
    "comparative": "сравн.",
    "superlative": "превосх.",
}


def _data_uri(path: Path, mime: str) -> str | None:
    try:
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as e:
        logger.warning("html_report.media_unreadable", path=str(path), error=str(e))
        return None
    return f"data:{mime};base64,{payload}"


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _forms_html(card: Card) -> str:
    if not card.forms:
        return ""
    rows = [
        f'<div class="form"><dt>{_esc(FORM_LABELS.get(key, key.replace("_", " ")))}</dt>'
        f"<dd>{_esc(value)}</dd></div>"
        for key, value in card.forms.items()
        if value
    ]
    return f'<dl class="forms">{"".join(rows)}</dl>' if rows else ""


def _card_html(card: Card, cfg: Config, include_audio: bool) -> str:
    image = _data_uri(cfg.paths.images_dir / card.image, "image/jpeg") if card.image else None
    audio = (
        _data_uri(cfg.paths.audio_dir / card.audio, "audio/mpeg")
        if include_audio and card.audio
        else None
    )

    figure = (
        f'<img class="photo" src="{image}" alt="{_esc(card.word)}" loading="lazy">'
        if image
        else '<div class="photo photo--empty"><span>без фото</span></div>'
    )
    player = f'<audio class="audio" controls preload="none" src="{audio}"></audio>' if audio else ""
    pronunciation = (
        f'<p class="pronunciation">[{_esc(card.pronunciation)}]</p>' if card.pronunciation else ""
    )
    example = (
        f'<blockquote class="example"><p>{_esc(card.example)}</p>'
        + (
            f"<p><span>{_esc(card.example_translation)}</span></p>"
            if card.example_translation
            else ""
        )
        + "</blockquote>"
        if card.example
        else ""
    )
    # Чего у карточки нет. Не приговор — enrichment мог не доехать (провайдер лёг,
    # батч сорвался), и `review accept` прогонит стадии заново. Но на ревью это
    # надо видеть до того, как карточка уедет в Anki неполной.
    missing = [
        label
        for label, present in (
            ("фото", bool(card.image)),
            ("аудио", bool(card.audio)),
            ("формы", bool(card.forms)),
            ("пример", bool(card.example)),
            ("транскрипция", bool(card.pronunciation)),
        )
        if not present
    ]
    gaps = f'<p class="gaps">не хватает: {_esc(", ".join(missing))}</p>' if missing else ""

    return f"""<article class="card" data-id="{card.id}" data-pos="{_esc(card.pos.value)}">
  <button class="toggle" type="button" aria-pressed="false">
    <span class="toggle__keep">оставить</span><span class="toggle__drop">отбросить</span>
  </button>
  {figure}
  <div class="body">
    <p class="meta"><span class="id">#{card.id}</span><span class="pos">{
        _esc(POS_LABELS.get(card.pos.value, card.pos.value))
    }</span></p>
    <h2 class="word">{_esc(card.word)}</h2>
    {pronunciation}
    <p class="translation">{_esc(card.translation)}</p>
    {player}
    {_forms_html(card)}
    {example}
    {gaps}
  </div>
</article>"""


def build_report(
    cards: list[Card],
    cfg: Config,
    *,
    title: str = "Ревью карточек",
    subtitle: str = "",
    include_audio: bool = True,
    standalone: bool = True,
) -> str:
    """Собрать HTML-страницу ревью для списка карточек.

    standalone=False отдаёт то же самое без <!doctype>/<html>/<head>/<body> — в
    таком виде страницу принимает Artifact, который оборачивает содержимое в свой
    собственный скелет документа.
    """
    tiles = "\n".join(_card_html(card, cfg, include_audio) for card in cards)
    counts = {
        "всего": len(cards),
        "с фото": sum(1 for c in cards if c.image),
        "с аудио": sum(1 for c in cards if c.audio),
        "с примером": sum(1 for c in cards if c.example),
    }
    summary = "".join(
        f'<div class="stat"><dt>{_esc(label)}</dt><dd>{value}</dd></div>'
        for label, value in counts.items()
    )

    content = _CONTENT.format(
        title=_esc(title),
        subtitle=_esc(subtitle),
        summary=summary,
        tiles=tiles or '<p class="empty">Карточек на ревью нет.</p>',
        ids=json.dumps([card.id for card in cards]),
    )
    if not standalone:
        return content
    return (
        '<!doctype html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"</head>\n<body>\n{content}\n</body>\n</html>\n"
    )


def write_report(path: Path, markup: str) -> int:
    """Записать страницу, вернуть её размер в байтах."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markup, encoding="utf-8")
    size = path.stat().st_size
    if size > SIZE_WARN_BYTES:
        logger.warning("html_report.large", path=str(path), bytes=size)
    return size


_CONTENT = """<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Literata:ital,opsz,wght@0,7..72,400;0,7..72,600;1,7..72,400&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root {{
  --ground: #f4f6f8;
  --surface: #ffffff;
  --edge: #dfe4ea;
  --ink: #161a20;
  --muted: #63707f;
  --accent: #1d5f5a;
  --accent-soft: #e2efed;
  --drop: #a8382a;
  --drop-soft: #f7e7e4;
  --shadow: 0 1px 2px rgba(22, 26, 32, .06), 0 8px 24px -16px rgba(22, 26, 32, .28);
  --radius: 10px;
  --sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --serif: "Literata", Georgia, "Times New Roman", serif;
  --mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground: #12151a;
    --surface: #1a1e25;
    --edge: #2b313a;
    --ink: #e7eaee;
    --muted: #97a3b2;
    --accent: #6fbfb5;
    --accent-soft: #16302e;
    --drop: #e08573;
    --drop-soft: #37201d;
    --shadow: 0 1px 2px rgba(0, 0, 0, .5), 0 8px 24px -16px rgba(0, 0, 0, .8);
  }}
}}
:root[data-theme="dark"] {{
  --ground: #12151a;
  --surface: #1a1e25;
  --edge: #2b313a;
  --ink: #e7eaee;
  --muted: #97a3b2;
  --accent: #6fbfb5;
  --accent-soft: #16302e;
  --drop: #e08573;
  --drop-soft: #37201d;
  --shadow: 0 1px 2px rgba(0, 0, 0, .5), 0 8px 24px -16px rgba(0, 0, 0, .8);
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 1240px; margin: 0 auto; padding: 32px 24px 96px; }}
header.page {{ display: flex; flex-direction: column; gap: 6px; margin-bottom: 24px; }}
header.page h1 {{
  font-family: var(--serif); font-weight: 600; font-size: clamp(26px, 3.4vw, 38px);
  margin: 0; letter-spacing: -.015em; text-wrap: balance;
}}
header.page p {{ margin: 0; color: var(--muted); max-width: 62ch; }}
.bar {{
  position: sticky; top: 0; z-index: 5;
  display: flex; flex-wrap: wrap; align-items: center; gap: 20px;
  padding: 14px 18px; margin-bottom: 28px;
  background: var(--surface); border: 1px solid var(--edge);
  border-radius: var(--radius); box-shadow: var(--shadow);
}}
.stats {{ display: flex; flex-wrap: wrap; gap: 20px; margin: 0; }}
.stat {{ display: flex; flex-direction: column; gap: 1px; }}
.stat dt {{
  font-size: 10.5px; text-transform: uppercase; letter-spacing: .09em; color: var(--muted);
}}
.stat dd {{
  margin: 0; font-family: var(--mono); font-size: 17px;
  font-variant-numeric: tabular-nums;
}}
.stat--drop dd {{ color: var(--drop); }}
.actions {{ display: flex; gap: 8px; margin-left: auto; }}
button {{ font: inherit; cursor: pointer; }}
.act {{
  padding: 7px 14px; border-radius: 7px; border: 1px solid var(--edge);
  background: var(--surface); color: var(--ink); font-weight: 500;
  transition: border-color .15s, background .15s;
}}
.act:hover {{ border-color: var(--accent); }}
.act--primary {{ background: var(--accent); border-color: var(--accent); color: var(--surface); }}
.act--primary:hover {{ filter: brightness(1.08); }}
:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.commands {{
  display: none; flex-direction: column; gap: 10px;
  padding: 16px 18px; margin-bottom: 28px;
  background: var(--surface); border: 1px solid var(--edge); border-radius: var(--radius);
}}
.commands.open {{ display: flex; }}
.commands h2 {{
  margin: 0; font-size: 11px; text-transform: uppercase; letter-spacing: .09em;
  color: var(--muted); font-weight: 600;
}}
.commands pre {{
  margin: 0; padding: 12px 14px; overflow-x: auto;
  background: var(--ground); border: 1px solid var(--edge); border-radius: 7px;
  font-family: var(--mono); font-size: 13px; line-height: 1.7;
}}
.grid {{
  display: grid; gap: 18px;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
}}
.card {{
  position: relative; display: flex; flex-direction: column; overflow: hidden;
  background: var(--surface); border: 1px solid var(--edge); border-radius: var(--radius);
  box-shadow: var(--shadow); transition: opacity .15s, border-color .15s;
}}
.card.dropped {{ opacity: .5; border-color: var(--drop); }}
.card.dropped::after {{
  content: ""; position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--drop);
}}
.toggle {{
  position: absolute; top: 10px; right: 10px; z-index: 2;
  padding: 5px 11px; border-radius: 999px; border: 1px solid var(--edge);
  background: var(--surface); color: var(--muted);
  font-size: 11.5px; font-weight: 500; letter-spacing: .01em;
}}
.toggle:hover {{ color: var(--ink); border-color: var(--accent); }}
.toggle__drop {{ display: none; }}
.card.dropped .toggle {{
  background: var(--drop-soft); border-color: var(--drop); color: var(--drop);
}}
.card.dropped .toggle__keep {{ display: none; }}
.card.dropped .toggle__drop {{ display: inline; }}
/* contain, не cover: у Bildetema снимки предметные и часто вертикальные —
   кадрирование по ширине срезало бы как раз то, ради чего смотрят карточку. */
.photo {{
  display: block; width: 100%; aspect-ratio: 4 / 3; object-fit: contain;
  padding: 8px; background: var(--ground); border-bottom: 1px solid var(--edge);
}}
.photo--empty {{
  display: grid; place-items: center; color: var(--muted); font-size: 12px;
}}
.body {{ display: flex; flex-direction: column; gap: 8px; padding: 14px 16px 16px; }}
.meta {{ display: flex; align-items: center; gap: 8px; margin: 0; }}
.id {{ font-family: var(--mono); font-size: 12px; color: var(--muted); }}
.pos {{
  padding: 2px 8px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent);
  font-size: 11px; font-weight: 600; letter-spacing: .02em;
}}
.word {{
  font-family: var(--serif); font-weight: 600; font-size: 22px; line-height: 1.2;
  margin: 0; letter-spacing: -.01em; text-wrap: balance;
}}
.pronunciation {{
  margin: -4px 0 0; font-family: var(--mono); font-size: 12.5px; color: var(--muted);
}}
.translation {{ margin: 0; font-size: 15px; }}
.audio {{ width: 100%; height: 32px; }}
.forms {{
  display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 2px 0 0;
  padding-top: 10px; border-top: 1px solid var(--edge);
}}
.form {{ display: contents; }}
.forms dt {{
  font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted);
  align-self: baseline;
}}
.forms dd {{ margin: 0; font-size: 13.5px; }}
.example {{
  margin: 2px 0 0; padding: 10px 0 0; border-top: 1px solid var(--edge);
  font-family: var(--serif); font-size: 14px;
}}
.example p {{ margin: 0; }}
.example span {{ font-family: var(--sans); font-size: 13px; color: var(--muted); }}
.gaps {{ margin: 0; font-size: 12px; color: var(--drop); }}
.empty {{ padding: 48px 0; text-align: center; color: var(--muted); }}
@media (prefers-reduced-motion: reduce) {{
  * {{ transition: none !important; animation: none !important; }}
}}
</style>
<div class="wrap">
  <header class="page">
    <h1>{title}</h1>
    <p>{subtitle}</p>
  </header>

  <div class="bar">
    <dl class="stats">
      {summary}
      <div class="stat stat--drop"><dt>отброшено</dt><dd id="drop-count">0</dd></div>
    </dl>
    <div class="actions">
      <button class="act" type="button" id="reset">Сбросить</button>
      <button class="act act--primary" type="button" id="build">Собрать команды</button>
    </div>
  </div>

  <section class="commands" id="commands">
    <h2>Выполни в терминале</h2>
    <pre id="cmd-skip"></pre>
    <pre id="cmd-accept"></pre>
  </section>

  <main class="grid">
{tiles}
  </main>
</div>

<script>
(function () {{
  var ALL = {ids};
  var dropped = new Set();
  var counter = document.getElementById('drop-count');
  var panel = document.getElementById('commands');

  function render() {{
    counter.textContent = dropped.size;
  }}

  document.querySelectorAll('.card').forEach(function (card) {{
    var id = Number(card.dataset.id);
    card.querySelector('.toggle').addEventListener('click', function (event) {{
      var pressed = dropped.has(id);
      if (pressed) {{ dropped.delete(id); }} else {{ dropped.add(id); }}
      card.classList.toggle('dropped', !pressed);
      event.currentTarget.setAttribute('aria-pressed', String(!pressed));
      render();
    }});
  }});

  document.getElementById('reset').addEventListener('click', function () {{
    dropped.clear();
    document.querySelectorAll('.card').forEach(function (card) {{
      card.classList.remove('dropped');
      card.querySelector('.toggle').setAttribute('aria-pressed', 'false');
    }});
    panel.classList.remove('open');
    render();
  }});

  document.getElementById('build').addEventListener('click', function () {{
    var drop = ALL.filter(function (id) {{ return dropped.has(id); }});
    var keep = ALL.filter(function (id) {{ return !dropped.has(id); }});
    document.getElementById('cmd-skip').textContent = drop.length
      ? 'ankiforgeai review skip ' + drop.join(' ')
      : '# отбрасывать нечего';
    document.getElementById('cmd-accept').textContent = keep.length
      ? 'ankiforgeai review accept ' + keep.join(' ')
      : '# принимать нечего';
    panel.classList.add('open');
    panel.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
  }});

  render();
}})();
</script>
"""
