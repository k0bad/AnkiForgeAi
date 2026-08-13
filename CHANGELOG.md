# Changelog

All notable changes to AnkiForgeAI will be documented in this file.

## [Unreleased]

### Added
- PyPI packaging: complete `pyproject.toml` metadata (readme, license, authors, classifiers, keywords, project URLs), `languages/` and `prompts/` bundled into the wheel, and a trusted-publishing GitHub Actions workflow (`.github/workflows/publish.yml`, tag-triggered, no stored API token).
- `ankiforgeai init` now also creates the Anki Note Type (previously required separately running `scripts/setup_anki_notetype.py`, which doesn't exist for a `pip install`ed user).
- AI-adjudicated dedupe (`dedupe.judge_review`, `prompts/dedupe_judge.md`): when rapidfuzz flags a candidate as a possible duplicate, the LLM is asked whether it's really the same word or just similarly spelled. `SAME` → merged/skipped, `DIFFERENT` → accepted as new, `UNSURE` (or an LLM error) → left for manual review, same as before. Toggle via `dedupe.ai_adjudication` in `config.yaml` (default `true`). `llm.call_text()` now takes an optional `model` override so this (or any future) call can use a cheaper/faster model than `llm.model` without switching provider; wire it up via `dedupe.judge_model`.
- Declarative Note Type fields (`anki.fields` in `language.yaml`, closes #5): the Anki Note Type field set — previously a fixed Python list (`FIELDS` in `anki/notetype.py`) — is now config-driven. Each field declares a data `source` (from a fixed resolver registry) and a `slot` (`front_title`/`front_audio`/`front_image`/`section`/`tag`/`hidden`). Backward-compatible: no existing `language.yaml` needs changes, all four ship without `anki.fields` and fall back to `DEFAULT_FIELDS`. A misconfigured `source` or a duplicate unique slot now fails fast at `ankiforgeai init` (`NoteTypeConfigError`) instead of during enrichment. See `DEVELOPER_GUIDE.md` §10 for the schema and an example.

### Fixed
- `ankiforgeai init` silently no-oped when the Anki Note Type already existed, so front/back template and CSS changes (e.g. the card redesign) never reached Anki after the first run — pushing cards only ever updates field values, not the note type's templates/styling. `init` now calls AnkiConnect's `updateModelTemplates`/`updateModelStyling` to sync the current design into an existing Note Type; re-run it after any card-design change.

### Changed
- PyPI project name is `ankiforgeai` (the Python import package stays `ankicards` for backwards compatibility).
- `config.py` resolves `config.yaml` / `languages/` / `prompts/` from the current directory with a fallback to the data bundled in the installed package, instead of assuming a git checkout — needed for the package to work when `pip install`ed rather than run from source. No behavior change for the existing dev-checkout workflow.
- `scripts/daily_topic.py` no longer blindly force-approves every `review`-status card before pushing (previously step 2 of the cron cycle looped over all of them and called `update_status(..., APPROVED)` unconditionally, regardless of why dedupe flagged them). It now relies on the AI adjudication above to resolve most fuzzy matches automatically; whatever the LLM is still unsure about stays `review` and is reported as `needs_review` in the notification instead of being force-pushed to Anki.

## [0.2.0] — 2026-08-11

### Added
- Pluggable notification layer (`ankicards.notify`): channels configured in `config.yaml -> notifications` (`type`/`enabled`/`url`/`format`). Ships with a generic `webhook` backend covering n8n, Zapier, Hermes, or any other JSON-POST gateway; `format: text` sends a ready Telegram-flavored message, `format: json` sends the raw structured report for a smarter router to handle itself. One channel failing doesn't block the others.
- Pluggable image search providers: `images.provider` selects Unsplash (default), Pexels, Pixabay, or key-free Openverse.
- Selectable pronunciation transcription: `transcription: practical | ipa` — `practical` keeps the existing Cyrillic respelling, `ipa` generates real International Phonetic Alphabet transcriptions per language. `ankiforgeai setup` asks for it.
- Retry/backoff (tenacity) for LLM and HTTP calls (AnkiConnect, page fetch, image providers).
- Stricter mypy configuration.
- Tests: LLM JSON-parsing edge cases, note-type HTML escaping, pipeline enrich-stage routing, notify dispatch, image-provider response parsing, transcription-mode selection.

### Fixed
- **Security:** escape HTML in Anki note fields — LLM-derived text (including content extracted from arbitrary web pages via `ingest url`) was inserted into note fields unescaped, a stored-HTML/JS injection risk in Anki's card webview.
- **Security:** a real personal Tailscale IP left in `config.yaml`'s example comment was scrubbed to a generic placeholder.
- `language` / `ui_language` / `enrich.*` settings in `config.yaml` are now actually wired through the pipeline (prompt selection, note-type name, TTS voice, back-of-card labels, enrichment stage toggles) — previously every one of them silently fell back to Norwegian (`nb`) defaults regardless of config.
- English (`en`) and Spanish (`es`) language profiles: their `prompts/` were verbatim copies of the Norwegian ones, and German (`de`) had no `prompts/` at all — replaced with real per-language prompts matching each language's actual grammar.
- `anki.note_type` default in `config.yaml` didn't match what `setup_anki_notetype.py` actually creates — `push` would have silently failed against a nonexistent Note Type.
- TTS voice is now resolved from the active language profile first, falling back to `config.yaml` — previously always used Norwegian edge-tts voices regardless of `language`.
- `ankiforgeai setup` wizard now actually applies the answers it collects (`ui_language`, enrich toggles, note type) instead of discarding them.
- Stale `ankicards` CLI name (the package rename to `ankiforgeai` never fully propagated) fixed in `anki/sync.py` and `data/topic_schedule.yaml`; stale old repo URL fixed in `llm.py`'s OpenRouter request headers and `CONTRIBUTING.md`'s clone instructions.
- `run_images.py` moved into `scripts/` alongside the other one-off scripts.
- Removed a stray debug script from the repo root and stale personal infrastructure details (hardcoded personal paths and LAN/Tailscale IPs) from README, CLAUDE.md, DEVELOPER_GUIDE.md, and CONTRIBUTING.md.

## [0.1.0] — 2026-08-10

### Added
- Multi-language architecture via `languages/{code}/language.yaml` profiles
- Built-in support: Norwegian Bokmål (nb), German (de), English (en), Spanish (es)
- AI-powered word generation by topic via OpenRouter/Anthropic
- Web page word extraction (trafilatura + LLM)
- Grammar form enrichment (noun/verb/adjective schemas per language)
- Translation, example sentence generation, pronunciation hints via LLM
- Text-to-speech audio via edge-tts (per-language voices)
- Image search and download via Unsplash API (nouns only)
- Exact + fuzzy deduplication (rapidfuzz) against local DB and Anki cache
- AnkiConnect integration: create/push/sync
- Interactive review CLI (rich + questionary)
- Full autonomous daily cycle: cron → generate → auto-accept → push → notify
- Telegram notification delivery via n8n webhook
- SQLite staging database with audit log
- Configurable topic schedule (YAML)
- Developer guide (DEVELOPER_GUIDE.md)
- Kanban task board for development tracking