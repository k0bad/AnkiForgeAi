# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**AnkiForgeAI** — multilingual vocabulary pipeline: ingest words → enrich with grammar → generate media → push to Anki. Target language is configurable via `language:` in `config.yaml` and `languages/{code}/language.yaml` profiles (built-in: `nb` Norwegian Bokmål, `de` German, `en` English, `es` Spanish — see `languages/`). Three names, on purpose: PyPI/distribution name `ankiforgeai`, CLI command `ankiforgeai`, Python import package `ankicards` (kept for backwards compat, predates the rename).

Primary workflow: user speaks Russian in VS Code chat, Claude invokes CLI commands and shows results. Example: «сгенерируй 20 слов по теме одежда A2» → `ankiforgeai ingest topic "одежда" --count 20 --level A2`.

## Commands

```bash
# Install
uv pip install -e ".[dev]"

# CLI entry point (script name is `ankiforgeai`, package/import name is `ankicards`)
ankiforgeai ingest topic "еда" --count 20 --level A2
ankiforgeai ingest url https://...
ankiforgeai review
ankiforgeai push
ankiforgeai sync
ankiforgeai stats
ankiforgeai doctor          # consistency check: enrich/images config toggles vs actual card data
ankiforgeai delete <id> [<id> ...]  # permanently delete cards, frees their id for reuse (irreversible)
ankiforgeai setup           # interactive wizard: pick language, provider, Anki URL → writes config.yaml

# Setup (run after install; safe to re-run)
ankiforgeai init                    # creates the SQLite schema + the LanguageCard note type in Anki
                                     # (name comes from languages/{language}/language.yaml anki.note_type,
                                     #  must match anki.note_type in config.yaml; requires Anki running
                                     #  with AnkiConnect for the note-type half). If the note type already
                                     # exists, re-running pushes the current front/back templates + CSS
                                     # (updateModelTemplates/updateModelStyling) instead of a no-op — rerun
                                     # after editing anki/notetype.py to sync the card design. Field-list
                                     # changes (anki.fields in language.yaml) are NOT synced this way —
                                     # init only warns on drift (notetype.diff_fields); renaming/removing
                                     # a field in an existing Note Type is a manual Anki Browse operation
                                     # (data loss risk), never done automatically.

# Dev
ruff check src/
ruff format src/
mypy src/
pytest tests/
pytest tests/test_dedupe.py::test_exact_match_returns_merge  # single test
```

## Architecture

Data flows through stages; each stage produces `list[Card]` with increasingly populated fields:

```
Ingest (url / topic)          → Card(status=pending, word, pos, translation)
   ↓
Dedupe (rapidfuzz)            → Decision(new | review | merge | skip)
   ↓                            review/merge → status=review, waits for Review
Enrich (grammar/example)      → Card + forms, example, example_translation
   ↓
Media (edge-tts / image provider) → Card + audio, image (filenames only)
   ↓
DB save                       → status=approved
   ↓
Push → AnkiConnect addNote    → status=pushed, anki_note_id filled
```

Review can interrupt at any stage — user sees pending/review cards and accepts/edits/rejects.

## Implementation status

**Fully implemented.** All modules contain real code, not stubs. See each module's docstring for details.

## Key modules

| Module | Purpose |
|---|---|
| `models.py` | `Card`, `NounForms`/`VerbForms`/`AdjectiveForms`, `Decision`, `Status` enum |
| `config.py` | Loads `config.yaml` + `.env`; all config objects are Pydantic, cached |
| `db.py` | `Database` class — SQLite with `connect()` context manager |
| `pipeline.py` | Orchestrator `run_ingest_pipeline()` and `push_approved()`; shared `enrich_and_generate_media()` also used by `review/interactive.py` accept path |
| `dedupe.py` | `check_card()` → fuzzy match via rapidfuzz; `judge_review()` → LLM adjudicates ambiguous matches; thresholds in `config.yaml` |
| `anki/connect.py` | Async HTTP client for AnkiConnect API (port 8765) |
| `anki/notetype.py` | LanguageCard note type definition: 12 fields, HTML/CSS templates |
| `ingest/topic.py` | Calls Claude with `prompts/topic_words.md` → `list[Card]` |
| `enrich/grammar.py` | Calls Claude with `prompts/grammar_forms.md` → populates `card.forms` |
| `media/tts.py` | edge-tts → `{card.id}_nb.mp3` in `media/audio/` |
| `media/images.py` | Provider from `images.provider` (unsplash/pexels/pixabay/openverse) → `{card.id}.jpg` in `media/images/` (nouns only) |
| `review/interactive.py` | rich + questionary terminal UI; `accept` batches enrich/media via `pipeline.enrich_and_generate_media()` at session end |

## Principles

1. **Source of truth is Anki**, not SQLite. SQLite is staging + audit + dedup cache.
2. **Card ID** is stored in the Anki note field `ID` and in `anki_note_id` in SQLite.
3. **All LLM calls go through `llm.py`**, provider selected by `llm.provider` in `config.yaml`: `openrouter` (OpenAI-compatible SDK, default) or `anthropic` (Claude SDK). No local models.
4. **Prompts in `languages/{code}/prompts/*.md`**, falling back to top-level `prompts/*.md` — edit them to improve card quality without touching Python. No hardcoded per-language prompt text in Python (see `enrich/translation.py` for the pattern).
5. **All DB operations in transactions** via `Database.connect()` context manager.
6. **Media filenames are deterministic**: `{card.id}_nb.mp3`, `{card.id}.jpg` (the `_nb` suffix is legacy and not language-specific). Store filename only, not path.
7. **Target language is per-profile**, not hardcoded. Currently `nb`/`de`/`en`/`es`; Nynorsk not supported within `nb`.

## Config

`config.yaml` is the main config; `.env` holds secrets (see `.env.example`).

Key settings to know:
- `language: nb` — active language profile, must match a `languages/{code}/` directory
- `ui_language: ru` — back-of-card label language (`ru` or `en`; see `config.EN_BACK_LABELS`)
- `transcription: practical | ipa` — pronunciation hint style: `practical` (Cyrillic respelling, default) or `ipa` (International Phonetic Alphabet); picks between `languages/{code}/prompts/russian_pronunciation.md` and `ipa_pronunciation.md`
- `llm.provider` / `llm.model`: default is `openrouter` / `deepseek/deepseek-v4-flash`; set `provider: anthropic` + a `claude-*` model to use Claude instead
- `dedupe.fuzzy_threshold_review: 85` — score ≥ 85 → mandatory review; 70–84 → semiauto
- `dedupe.ai_adjudication: true` — for fuzzy matches, ask the LLM whether it's a real duplicate before falling back to human review (`dedupe.judge_review`, `prompts/dedupe_judge.md`); `dedupe.judge_model` optionally pins a cheaper/faster model for just that call (empty = `llm.model`, same provider)
- `tts.voice_female` / `tts.voice_male` — come from the active language profile by default (`languages/{code}/language.yaml` → `tts:`); override in `config.yaml` to pin a different voice
- `images.provider: unsplash | pexels | pixabay | openverse` — search backend for `media/images.py`; matching API key goes in `.env` (`UNSPLASH_ACCESS_KEY` / `PEXELS_API_KEY` / `PIXABAY_API_KEY`), `openverse` needs none
- `images.fallback_providers: []` — opt-in list of providers to try in order if `images.provider` returns nothing (empty/no key/403/429/5xx); empty by default, so behavior is unchanged unless explicitly configured
- `images.only_for_pos: [noun]` — images generated only for nouns
- `enrich.grammar` / `enrich.examples` / `enrich.pronunciation` — toggle individual enrichment stages (set by `ankiforgeai setup`)
- `concurrency: 5` — how many cards `enrich_and_generate_media` processes in parallel for the non-batched per-card stages (translation, TTS, image search/download); grammar/examples/pronunciation are already single batch LLM calls and unaffected. Raise for faster ingest, lower if an image provider's rate limit gets hit

## Code conventions

- `from __future__ import annotations` in every file.
- Async for HTTP/TTS/LLM I/O (`anki/connect.py`, `media/tts.py`, `llm.py`). `db.py` uses synchronous
  `sqlite3` directly, called blocking from async code — not a genuine async DB layer despite the
  pipeline functions calling it being `async def`.
- Pydantic v2 for models and config.
- `structlog` for logging (JSON + human-readable).
- `ruff` line-length 100, selects E/F/I/N/UP/B/SIM.
- `pytest-asyncio` with `asyncio_mode = "auto"`.

## What NOT to do

- No local LLMs in this pipeline.
- No file paths in DB — filenames only.
- No MCP wrappers yet.
- No CSV export as priority — push via AnkiConnect is the main path.

## Security

- **Never read `.env` files** — do not open, cat, or display `.env` or any file containing secrets (API keys, tokens, passwords).
- **Never output secrets** — do not print, log, or include in responses any value loaded from `.env`, even partially or masked.
- `.env.example` (no real values) is the only secrets-related file that may be read or shown.
- If a task requires a secret, ask the user to confirm it is set in the environment — do not read the file to verify.
