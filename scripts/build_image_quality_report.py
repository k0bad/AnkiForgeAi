"""issue #37: turn a measure_image_quality.py JSON sample into a self-contained,
click-to-score HTML report (word + search phrase + candidate thumbnail + pass/fail),
with a live-computed match-quality % and per-language breakdown.

Usage:
    uv run python scripts/build_image_quality_report.py --in data/sample.json --out report.html
"""

from __future__ import annotations

import argparse
import json
from html import escape

PAGE_TEMPLATE = """<!doctype html>
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
:root {{
  --bg: #eef0ee;
  --surface: #ffffff;
  --surface-dim: #e4e7e2;
  --border: #d6dad3;
  --ink: #1f2421;
  --ink-dim: #5b625b;
  --accent: #2b4c5c;
  --accent-soft: #d9e6ea;
  --pass: #3f7d4f;
  --pass-soft: #dcede0;
  --fail: #b54a3f;
  --fail-soft: #f3ded9;
  --unscored: #8a8f87;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14181a;
    --surface: #1d2224;
    --surface-dim: #262b2c;
    --border: #333a3a;
    --ink: #e9e7e0;
    --ink-dim: #a3a89f;
    --accent: #8fbecb;
    --accent-soft: #223540;
    --pass: #7fc493;
    --pass-soft: #1e3324;
    --fail: #e08b7e;
    --fail-soft: #3a2321;
    --unscored: #74796f;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #14181a;
  --surface: #1d2224;
  --surface-dim: #262b2c;
  --border: #333a3a;
  --ink: #e9e7e0;
  --ink-dim: #a3a89f;
  --accent: #8fbecb;
  --accent-soft: #223540;
  --pass: #7fc493;
  --pass-soft: #1e3324;
  --fail: #e08b7e;
  --fail-soft: #3a2321;
  --unscored: #74796f;
}}
* {{ box-sizing: border-box; }}
body {{
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  margin: 0;
  padding: 0 0 4rem;
}}
.headword {{
  font-family: Georgia, "Iowan Old Style", "Palatino Linotype", "Noto Serif", serif;
}}
header.summary {{
  position: sticky;
  top: 0;
  z-index: 10;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 1.1rem 1.5rem;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 1.5rem;
}}
header.summary h1 {{
  font-size: 1.05rem;
  font-weight: 600;
  margin: 0;
  flex: 1 1 auto;
  min-width: 12rem;
}}
header.summary p.sub {{
  margin: 0.15rem 0 0;
  font-size: 0.82rem;
  color: var(--ink-dim);
  font-weight: 400;
}}
.stat {{
  text-align: right;
}}
.stat .num {{
  font-variant-numeric: tabular-nums;
  font-size: 1.6rem;
  font-weight: 700;
  line-height: 1;
  color: var(--accent);
}}
.stat .label {{
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-dim);
}}
.lang-breakdown {{
  display: flex;
  gap: 0.9rem;
  font-size: 0.78rem;
  color: var(--ink-dim);
  font-variant-numeric: tabular-nums;
}}
main {{
  max-width: 76rem;
  margin: 0 auto;
  padding: 1.5rem;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(15.5rem, 1fr));
  gap: 1rem;
}}
.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: opacity 0.15s ease;
}}
.card[data-scored="1"] {{ opacity: 0.55; }}
.card[data-scored="1"]:hover {{ opacity: 1; }}
.thumb-wrap {{
  aspect-ratio: 4 / 3;
  background: var(--surface-dim);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}}
.thumb-wrap img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
.thumb-wrap.empty {{
  color: var(--ink-dim);
  font-size: 0.8rem;
  text-align: center;
  padding: 1rem;
}}
.card-body {{
  padding: 0.85rem 0.9rem 0.95rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  flex: 1;
}}
.eyebrow {{
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-dim);
  display: flex;
  gap: 0.5em;
}}
.word {{
  font-size: 1.15rem;
  font-weight: 600;
  line-height: 1.15;
}}
.translation {{
  font-size: 0.85rem;
  color: var(--ink-dim);
}}
.query {{
  font-size: 0.78rem;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 5px;
  padding: 0.15rem 0.45rem;
  display: inline-block;
  width: fit-content;
  font-style: italic;
}}
.actions {{
  margin-top: auto;
  padding-top: 0.6rem;
  display: flex;
  gap: 0.5rem;
}}
button.score-btn {{
  flex: 1;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--ink);
  border-radius: 6px;
  padding: 0.4rem 0;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
}}
button.score-btn:focus-visible {{
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}}
button.pass-btn:hover, button.pass-btn.active {{
  background: var(--pass-soft);
  border-color: var(--pass);
  color: var(--pass);
}}
button.fail-btn:hover, button.fail-btn.active {{
  background: var(--fail-soft);
  border-color: var(--fail);
  color: var(--fail);
}}
.pill {{
  font-size: 0.72rem;
  font-weight: 600;
  border-radius: 999px;
  padding: 0.2rem 0.6rem;
  width: fit-content;
}}
.pill.pass {{ background: var(--pass-soft); color: var(--pass); }}
.pill.fail {{ background: var(--fail-soft); color: var(--fail); }}
.pill.error {{ background: var(--surface-dim); color: var(--unscored); }}
footer {{
  max-width: 76rem;
  margin: 0 auto;
  padding: 0 1.5rem;
  color: var(--ink-dim);
  font-size: 0.75rem;
}}
</style>
<header class="summary">
  <div>
    <h1>Image-match baseline &mdash; issue #37</h1>
    <p class="sub">
      Click Pass/Fail per card. Coverage = candidate found; Match rate = Pass among scored.
    </p>
  </div>
  <div class="lang-breakdown" id="lang-breakdown"></div>
  <div class="stat">
    <div class="num" id="stat-coverage">&ndash;</div>
    <div class="label">Coverage</div>
  </div>
  <div class="stat">
    <div class="num" id="stat-scored">0/0</div>
    <div class="label">Scored</div>
  </div>
  <div class="stat">
    <div class="num" id="stat-rate">&ndash;</div>
    <div class="label">Match rate</div>
  </div>
</header>
<main>
  <div class="grid" id="grid"></div>
</main>
<footer>
  Generated by scripts/measure_image_quality.py + build_image_quality_report.py.
  Scores are kept in this browser only (localStorage), not sent anywhere.
</footer>
<script>
const DATA = {data_json};
const STORE_KEY = "image_quality_scores::{storage_key}";
let scores = {{}};
try {{
  scores = JSON.parse(localStorage.getItem(STORE_KEY) || "{{}}");
}} catch (e) {{
  scores = {{}};
}}

function rowKey(row, i) {{ return row.language + ":" + row.word + ":" + i; }}

function render() {{
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  DATA.forEach((row, i) => {{
    const key = rowKey(row, i);
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.scored = scores[key] ? "1" : "0";

    const thumbWrap = document.createElement("div");
    if (row.thumb_data_uri) {{
      thumbWrap.className = "thumb-wrap";
      const img = document.createElement("img");
      img.src = row.thumb_data_uri;
      img.alt = row.image_query || row.word;
      thumbWrap.appendChild(img);
    }} else {{
      thumbWrap.className = "thumb-wrap empty";
      thumbWrap.textContent = row.error || "no candidate";
    }}
    card.appendChild(thumbWrap);

    const body = document.createElement("div");
    body.className = "card-body";

    const eyebrow = document.createElement("div");
    eyebrow.className = "eyebrow";
    eyebrow.innerHTML =
      `<span>${{row.language}}</span><span>&middot;</span>` +
      `<span>${{row.pos}}</span><span>&middot;</span>` +
      `<span>${{row.topic}}</span>`;
    body.appendChild(eyebrow);

    const word = document.createElement("div");
    word.className = "word headword";
    word.textContent = row.word;
    body.appendChild(word);

    const translation = document.createElement("div");
    translation.className = "translation";
    translation.textContent = row.translation;
    body.appendChild(translation);

    if (row.image_query) {{
      const query = document.createElement("div");
      query.className = "query";
      query.textContent = "\\u201c" + row.image_query + "\\u201d";
      body.appendChild(query);
    }}

    if (row.thumb_data_uri) {{
      const actions = document.createElement("div");
      actions.className = "actions";
      const passBtn = document.createElement("button");
      passBtn.className = "score-btn pass-btn";
      passBtn.textContent = "Pass";
      const failBtn = document.createElement("button");
      failBtn.className = "score-btn fail-btn";
      failBtn.textContent = "Fail";
      const current = scores[key];
      if (current === "pass") passBtn.classList.add("active");
      if (current === "fail") failBtn.classList.add("active");
      passBtn.onclick = () => setScore(key, "pass");
      failBtn.onclick = () => setScore(key, "fail");
      actions.appendChild(passBtn);
      actions.appendChild(failBtn);
      body.appendChild(actions);
    }} else {{
      const pill = document.createElement("div");
      pill.className = "pill error";
      pill.textContent = "excluded from match rate";
      body.appendChild(pill);
    }}

    card.appendChild(body);
    grid.appendChild(card);
  }});
  updateStats();
}}

function setScore(key, value) {{
  scores[key] = scores[key] === value ? null : value;
  if (!scores[key]) delete scores[key];
  localStorage.setItem(STORE_KEY, JSON.stringify(scores));
  render();
}}

function updateStats() {{
  const withCandidate = DATA.filter(r => r.thumb_data_uri);
  const total = DATA.length;
  const scoredKeys = Object.keys(scores).filter(k => scores[k]);
  const passCount = scoredKeys.filter(k => scores[k] === "pass").length;
  const scoredCount = scoredKeys.length;

  document.getElementById("stat-coverage").textContent =
    total ? Math.round((withCandidate.length / total) * 100) + "%" : "\\u2013";
  document.getElementById("stat-scored").textContent = scoredCount + "/" + withCandidate.length;
  document.getElementById("stat-rate").textContent =
    scoredCount ? Math.round((passCount / scoredCount) * 100) + "%" : "\\u2013";

  const byLang = {{}};
  DATA.forEach((row, i) => {{
    const key = rowKey(row, i);
    const b = byLang[row.language] || (byLang[row.language] = {{ pass: 0, scored: 0, total: 0 }});
    if (row.thumb_data_uri) b.total++;
    if (scores[key]) {{
      b.scored++;
      if (scores[key] === "pass") b.pass++;
    }}
  }});
  const breakdown = document.getElementById("lang-breakdown");
  breakdown.innerHTML = Object.entries(byLang)
    .map(([lang, b]) => {{
      const passPct = b.scored ? ", " + Math.round((b.pass / b.scored) * 100) + "% pass" : "";
      return `<div>${{lang}}: ${{b.scored}}/${{b.total}} scored${{passPct}}</div>`;
    }})
    .join("");
}}

render();
</script>
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the click-to-score HTML report for issue #37"
    )
    parser.add_argument("--in", dest="input_path", required=True)
    parser.add_argument("--out", dest="output_path", required=True)
    parser.add_argument("--title", default="Image-match baseline")
    args = parser.parse_args()

    with open(args.input_path, encoding="utf-8") as f:
        rows = json.load(f)

    html = PAGE_TEMPLATE.format(
        title=escape(args.title),
        data_json=json.dumps(rows, ensure_ascii=False),
        storage_key=escape(args.input_path),
    )
    with open(args.output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {args.output_path} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
