# AnkiForgeAI

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![CI](https://github.com/k0bad/AnkiForgeAi/actions/workflows/test.yml/badge.svg)](https://github.com/k0bad/AnkiForgeAi/actions)

AI-powered vocabulary flashcard pipeline for any language with automatic delivery to Anki.
**Language-agnostic** via configurable YAML profiles (`languages/{code}/language.yaml`).

Built-in languages: 🇳🇴 Norwegian Bokmål (`nb`), 🇩🇪 German (`de`), 🇬🇧 English (`en`), 🇪🇸 Spanish (`es`).

## What it does

1. **Ingest** — generates word candidates:
   - by topic via LLM ("20 food-related words, A2")
   - from a URL (web page extraction + LLM parsing)
2. **Enrich** — adds grammar forms, translation, example sentences.
3. **Dedupe** — checks duplicates against existing Anki notes and staging DB.
4. **Media** — generates mp3 audio (edge-tts) and downloads images (pick a provider: Unsplash, Pexels, Pixabay, or key-free Openverse).
5. **Review** — interactive review of ambiguous candidates (CLI).
6. **Push** — sends approved cards to Anki via AnkiConnect.

## Architecture

| Decision | Why |
|----------|-----|
| **AnkiConnect**, not CSV | Two-way sync, dedupe against live Anki deck |
| **SQLite** as staging + audit + cache | Anki = source of truth, SQLite = local index + history |
| **`forms` as JSON** | Different schemas for nouns/verbs/adjectives per language |
| **edge-tts** over gTTS | Microsoft neural voices, free, per-language quality |
| **Pluggable image provider** | `images.provider`: Unsplash, Pexels, Pixabay, or key-free Openverse — legal, free tiers |
| **Selectable transcription** | `transcription`: `practical` (Cyrillic respelling) or `ipa` — pronunciation hints aren't hardcoded to Russian speakers |
| **Prompts in `prompts/*.md`** | Improve card quality without touching code |
| **Any LLM provider** | OpenRouter or Anthropic Claude |

## Card statuses

```
pending → review → approved → pushed
              ↓
           skipped / suspended
```

- **pending** — just created, not yet enriched
- **review** — potential duplicates found, needs decision
- **approved** — ready for push to Anki
- **pushed** — already in Anki (`anki_note_id` set)
- **skipped** — discarded (reason in audit_log)
- **suspended** — postponed

## Supported Languages

Language profiles live in `languages/{code}/`. Currently supported:

| Language | Code | Status |
|----------|------|--------|
| 🇳🇴 Norwegian Bokmål | `nb` | ✅ Complete |
| 🇩🇪 German | `de` | ✅ Complete |
| 🇬🇧 English | `en` | ✅ Complete |
| 🇪🇸 Spanish | `es` | ✅ Complete |

Select the active language with `language: <code>` in `config.yaml`, or pick it interactively via `ankiforgeai setup`.

### Adding Your Language

1. Create `languages/{code}/language.yaml` using [nb](languages/nb/language.yaml) or [de](languages/de/language.yaml) as template:
```yaml
code: xx              # ISO 639-1
name: Language Name
article: true         # whether nouns have articles
pos_labels:           # POS → target language name
  noun: ...
  verb: ...
forms:                # grammar field schema per POS
  noun:
    - {key: gender, label: "Gender"}
    - {key: ...}
tts:                  # edge-tts voices
  voice_female: xx-XX-NameNeural
anki:
  deck_name: MyDeck
back_labels:          # labels in your native language
  translation: "Translation"
```
2. Copy prompts: `cp prompts/*.md languages/{code}/prompts/`
3. Adapt prompts for the target language
4. **Minimal setup for a working language:** `language.yaml` + `topic_words.md`

## Installation

```bash
# Python 3.11+ required
# Anki desktop + AnkiConnect addon (https://ankiweb.net/shared/info/2055492159)

uv venv
source .venv/bin/activate     # Linux/macOS
# .venv\Scripts\activate      # Windows
uv pip install -e ".[dev]"

# Configuration
cp .env.example .env
# edit .env — add your API keys

# Initialize
python scripts/init_db.py

# (With Anki running) Create Note Type
python scripts/setup_anki_notetype.py
```

## Usage

```bash
# Generate 20 food-related words at A2 level
ankiforgeai ingest topic "mat" --count 20 --level A2

# Extract words from a web page
ankiforgeai ingest url "https://example.com/lesson"

# Run interactive review
ankiforgeai review

# Push approved cards to Anki
ankiforgeai push

# Sync Anki → local cache (daily)
ankiforgeai sync

# View stats
ankiforgeai stats
```

## Automated Daily Cycle

```bash
# Generate → dedupe/enrich/media → auto-accept → push (no Telegram notification)
python scripts/daily_topic.py

# Preview what today's topic would be
python scripts/daily_topic.py --dry-run

# Override topic and count
python scripts/daily_topic.py --topic dyr --count 5 --no-push

# Full cycle incl. notifications (config.yaml -> notifications:) — use this for cron
./scripts/daily_topic.sh
```

`daily_topic.py` alone does not send notifications — pass `--notify` (which is what
`daily_topic.sh` does) to fan the report out to every enabled channel in
`config.yaml -> notifications:`. Today that's a generic `webhook` backend (POST JSON to
any URL — n8n, Zapier, a custom bot gateway); see `src/ankicards/notify/`. Set up
`daily_topic.sh` as a cron job for hands-free daily vocabulary generation with delivery
to your configured channel.

## Project Structure

```
src/ankicards/
├── models.py              # Card, POS, Status, Decision (Pydantic)
├── config.py              # config.yaml + language profiles
├── db.py                  # SQLite layer
├── cli.py                 # Typer CLI
├── pipeline.py            # Stage orchestration
├── llm.py                 # LLM client (OpenRouter/Anthropic)
├── dedupe.py              # Exact + fuzzy matching (rapidfuzz)
├── ingest/
│   ├── url.py             # trafilatura + LLM
│   └── topic.py           # Topic-based generation
├── enrich/
│   ├── grammar.py         # Grammar forms per POS
│   ├── translation.py     # Translations
│   ├── examples.py        # Example sentences
│   └── pronunciation.py   # Pronunciation hints
├── media/
│   ├── tts.py             # edge-tts audio
│   └── images.py          # Image search: unsplash/pexels/pixabay/openverse
├── anki/
│   ├── connect.py         # HTTP client for AnkiConnect
│   ├── sync.py            # Anki → cache sync
│   └── notetype.py        # Note type definition
└── review/
    └── interactive.py     # Rich + questionary UI

languages/                 # Language profiles (YAML + prompts)
prompts/                   # Default prompts
scripts/                   # init_db, setup, daily_topic
tests/
data/                      # DB, logs (gitignored)
media/                     # Audio, images (gitignored)
```

## Roadmap

- [x] Multi-language architecture
- [x] Ingest by topic (LLM)
- [x] Ingest from URL (trafilatura + LLM)
- [x] Dedupe (rapidfuzz)
- [x] Grammar enrichment
- [x] edge-tts audio
- [x] Pluggable image providers (Unsplash / Pexels / Pixabay / Openverse)
- [x] AnkiConnect push & sync
- [x] Interactive review CLI
- [x] Full auto cycle (cron + auto-accept + push + notify)
- [ ] PyPI publication
- [ ] Nynorsk support
- [ ] Telegram bot for mobile review

## For Developers

See [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for the full architecture reference, and [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.