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
| **Any LLM provider** | OpenRouter, Anthropic Claude, or the local `claude` CLI (`claude_cli` — no API key, reuses your `claude login`; shares that quota with interactive Claude Code use, so better for occasional generation than heavy cron) |

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

Requires Python 3.11+ and Anki desktop + the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) addon.

### As a tool (end users)

```bash
uv tool install ankiforgeai   # or: pipx install ankiforgeai / pip install ankiforgeai
ankiforgeai setup             # writes config.yaml in the current directory
ankiforgeai init              # creates the local DB and the Anki Note Type
```

`ankiforgeai setup` will ask which LLM provider to use. Pick **"Claude Code CLI"** if you
don't want to set up a separate API key — see [No API subscription?](#no-api-subscription-use-your-claude-code-login)
below for what that needs and what it trades off.

`config.yaml`, `data/`, and `media/` are created in whatever directory you run `ankiforgeai` from — `cd` into a project folder first (e.g. `mkdir ~/ankiforgeai && cd ~/ankiforgeai`).

`setup` also asks for your LLM/image API keys directly (masked input, written to
`.env`) and, if given one, offers to generate and push a small first batch of cards
right away — say no and add keys to `.env` yourself later if you'd rather review
what it runs first. It also offers to register the daily-automation cycle (see below)
with your OS scheduler on the spot.

`init` is safe to re-run: if the Note Type already exists in Anki, it pushes the current card templates and CSS to it instead of skipping, so re-run it after pulling an update that changes the card design. This also applies if an update renames the Note Type itself (`anki.note_type` in `languages/{code}/language.yaml`) — `ankiforgeai push` will fail with `Note Type '...' не найден в Anki` until you re-run `init` to create it.

### From source (contributors)

```bash
git clone https://github.com/k0bad/AnkiForgeAi.git
cd AnkiForgeAi
uv venv
source .venv/bin/activate     # Linux/macOS
# .venv\Scripts\activate      # Windows
uv pip install -e ".[dev]"

# Configuration
cp .env.example .env
# edit .env — add your API keys
cp config.yaml.example config.yaml
# edit config.yaml — Anki URL, deck name, language, providers, etc.

# Initialize (DB + Anki Note Type)
ankiforgeai init
```

### No API subscription? Use your Claude Code login

If you already have [Claude Code](https://claude.com/claude-code) installed and logged in
(Pro/Max subscription, or an `ANTHROPIC_API_KEY` configured at the Claude Code level) you
can skip getting a separate OpenRouter/Anthropic key for this project entirely:

```bash
claude login          # one-time, skip if already logged in
```

Then in `config.yaml`:

```yaml
llm:
  provider: claude_cli   # instead of openrouter/anthropic
  model: sonnet           # alias — "sonnet" | "opus" | "haiku", not a provider/model string
```

`.env` needs no `OPENROUTER_API_KEY`/`ANTHROPIC_API_KEY` in this mode — `ankiforgeai setup`
sets this up for you if you pick "Claude Code CLI" at the provider prompt. Under the hood
this runs `claude -p` as a subprocess (see `src/ankicards/llm.py::_call_claude_cli`).

**Trade-off:** each call goes through your existing Claude Code session and shares its
rate-limit/quota with whatever else you use Claude Code for interactively — it is not a
separate billing pool. Fine for occasional/manual generation (`ankiforgeai ingest topic ...`
run by hand); not recommended for the unattended daily cron cycle below if you also do
heavy interactive Claude Code work on the same account, since a large batch could eat into
quota you'd rather have during the day.

## Usage

```bash
# Generate 20 food-related words at A2 level
ankiforgeai ingest topic "mat" --count 20 --level A2

# Extract words from a web page
ankiforgeai ingest url "https://example.com/lesson"

# Import a topic from the Bildetema picture dictionary — with its own photos and
# human-recorded audio (`--list` shows the topic tree, `--dry-run` previews)
ankiforgeai ingest bildetema "Klær"

# Run interactive review
ankiforgeai review

# ...or review visually: one self-contained page with every photo and audio clip
ankiforgeai review html

# Push approved cards to Anki
ankiforgeai push

# Sync Anki → local cache (daily)
ankiforgeai sync

# View stats
ankiforgeai stats

# Consistency check: enrich/images config toggles vs actual card data
ankiforgeai doctor

# Delete cards permanently (frees their id for reuse; irreversible if already pushed)
ankiforgeai delete <id> [<id> ...]
```

## Using with Claude Code

This repo ships two things: the `ankiforgeai` CLI above (what `pip install ankiforgeai` gets you),
and a [Claude Code Agent Skill](.claude/skills/ankiforgeai/SKILL.md) checked into git alongside it.
When [Claude Code](https://claude.com/claude-code) works inside this directory it auto-loads
`SKILL.md` and drives the same CLI non-interactively — `--json` output, `review accept/skip/suspend/
resume/edit <id>` by card id instead of the TTY prompts `ankiforgeai review` needs a human terminal
for. That's what powers the primary workflow: ask in chat ("сгенерируй 20 слов по теме одежда A2"),
Claude runs the CLI and reports back. The skill is git-only — it isn't part of the PyPI package.

## Automated Daily Cycle

`ankiforgeai setup` can register this for you (Task Scheduler on Windows, cron
elsewhere) — the steps below are for setting it up by hand, or changing a
registration setup already made.

```bash
# Generate → dedupe (AI-adjudicated)/enrich/media → push (no Telegram notification)
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
`config.yaml -> notifications:`. Two backends today, and both can be enabled at once:
- `webhook` — POST JSON to any URL (n8n, Zapier, a custom bot gateway); the URL itself
  is read from `NOTIFY_WEBHOOK_URL` in `.env`, overriding `notifications[].url` in
  `config.yaml` when set
- `telegram` — direct Telegram Bot API call, no intermediary: set `chat_id` (and
  optionally `topic_id`) in `config.yaml`, and the bot token via `NOTIFY_TELEGRAM_TOKEN`
  in `.env` — never in `config.yaml`, which is committed

Either way, keep the real address/token out of `config.yaml` (it's committed) — with no
`.env` override and an empty `config.yaml` value, that channel is skipped (logged as
`notify.no_url` / `notify.no_token`) instead of failing the run. See
`src/ankicards/notify/`. Set up `daily_topic.sh` as a cron job for hands-free daily
vocabulary generation with delivery to your configured channel(s).

### Windows

`daily_topic.sh` needs bash; on Windows use `scripts/daily_topic.ps1` (same
`--notify` full cycle) with Task Scheduler instead of cron:

```powershell
# One-off manual run, same as ./scripts/daily_topic.sh
powershell -File scripts\daily_topic.ps1

# Register a daily trigger (adjust -At to taste)
$action   = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument '-NoProfile -ExecutionPolicy Bypass -File "<repo-path>\scripts\daily_topic.ps1"'
$trigger  = New-ScheduledTaskTrigger -Daily -At 8:00am
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable
Register-ScheduledTask -TaskName "AnkiForgeAI Daily Words" -Action $action `
    -Trigger $trigger -Settings $settings `
    -Description "ankicards: generate + auto-push + notify daily vocabulary"
```

`-WakeToRun` wakes the PC from sleep for the trigger (needs BIOS/power support; won't
power on from a full shutdown); `-StartWhenAvailable` catches up on the next boot if the
PC was off at trigger time instead of silently skipping the day.

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
├── doctor.py              # Consistency check: enrich/images config vs card data
├── migrate_ids.py         # One-time UUID → sequential int id migration
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
├── notify/
│   ├── base.py            # Notifier protocol
│   ├── format.py          # Telegram-flavored markdown report renderer (shared)
│   ├── webhook.py         # Generic webhook backend (n8n, Zapier, ...)
│   └── telegram.py        # Direct Telegram Bot API backend
└── review/
    ├── interactive.py     # Rich + questionary UI
    └── actions.py         # Non-interactive accept/skip/suspend/resume/edit/delete

languages/                 # Language profiles (YAML + prompts)
prompts/                   # Default prompts
scripts/                   # daily_topic, run_images
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
- [x] Selectable pronunciation transcription (practical Cyrillic / IPA)
- [x] AnkiConnect push & sync
- [x] Interactive review CLI
- [x] Full auto cycle (cron + dedupe + push + notify)
- [x] Pluggable notification channels (generic webhook: n8n / Zapier / Hermes / any)
- [x] AI-adjudicated dedupe — ambiguous fuzzy matches are judged by the LLM (same word vs. coincidentally similar), not blindly auto-accepted or left for a human by default
- [x] PyPI publication — package and release workflow are ready (see `DEVELOPER_GUIDE.md` §12), pending one-time trusted-publisher setup on pypi.org

### Coming up

- [x] `--language`/`-l` override flag on data-touching commands — switch language per invocation without hand-editing `config.yaml` (issue #63); cards, dedupe, review, and Anki push/sync are all scoped per language, so `nb` and `de` vocab can coexist in the same local DB
- [ ] `parse_mode: MarkdownV2` support with special-character escaping (`notify/webhook.py` currently renders for legacy `parse_mode: Markdown`)
- [ ] Nynorsk as its own `language.yaml` (`nn`)

See `DEVELOPER_GUIDE.md` §10 for the full, actively-maintained dev-facing plan (this list is a snapshot of it).

## For Developers

See [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for the full architecture reference, and [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.
