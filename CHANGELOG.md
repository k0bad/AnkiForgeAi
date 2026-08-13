# Changelog

All notable changes to AnkiForgeAI will be documented in this file.

## [Unreleased]

## [0.3.0] — 2026-08-13

### Added
- `doctor` command (`ankiforgeai doctor`, `--json`, closes #9): compares `enrich.*`/`images.*` config toggles against the fields actually populated on approved/pushed cards, and flags pushed cards missing `anki_note_id`. Inspects DB state directly so it can't be fooled by a caller that skipped a stage without leaving a trace.
- Run-scoped structlog tracing (closes #8): `log.bound_run(command)` binds a short `run_id` + command name to structlog's contextvars for one CLI invocation; `audit_log` gained a `run_id` column so the persistent audit trail and the live log trace share one id. Added `stage.start`/`stage.done` and per-card `audio_generated`/`image_generated` events so successful runs are traceable, not just failures.
- Card front/back redesign (issue #17): POS moves inline next to the word (front, and in a back-side recap header) instead of its own section; Example/ExampleTranslation merge into one boxed note. Adds `.night_mode` (dark theme) support and mobile breakpoints. `NoteFieldDef` gained `recap_on_back`/`nest_in_previous` and a `title_meta` slot so the field-schema engine stays generic instead of hardcoding the layout in Python.
- Opt-in fallback chain between image providers (`images.fallback_providers`, closes #20): if `images.provider` comes back empty, missing a key, or hits 403/429/5xx, `search_images()` cascades through the configured providers in order before giving up. Empty (default) list keeps behavior identical to before.
- PyPI packaging: complete `pyproject.toml` metadata (readme, license, authors, classifiers, keywords, project URLs), `languages/` and `prompts/` bundled into the wheel, and a trusted-publishing GitHub Actions workflow (`.github/workflows/publish.yml`, tag-triggered, no stored API token).
- `ankiforgeai init` now also creates the Anki Note Type (previously required separately running `scripts/setup_anki_notetype.py`, which doesn't exist for a `pip install`ed user).
- AI-adjudicated dedupe (`dedupe.judge_review`, `prompts/dedupe_judge.md`): when rapidfuzz flags a candidate as a possible duplicate, the LLM is asked whether it's really the same word or just similarly spelled. `SAME` → merged/skipped, `DIFFERENT` → accepted as new, `UNSURE` (or an LLM error) → left for manual review, same as before. Toggle via `dedupe.ai_adjudication` in `config.yaml` (default `true`). `llm.call_text()` now takes an optional `model` override so this (or any future) call can use a cheaper/faster model than `llm.model` without switching provider; wire it up via `dedupe.judge_model`.
- Declarative Note Type fields (`anki.fields` in `language.yaml`, closes #5): the Anki Note Type field set — previously a fixed Python list (`FIELDS` in `anki/notetype.py`) — is now config-driven. Each field declares a data `source` (from a fixed resolver registry) and a `slot` (`front_title`/`front_audio`/`front_image`/`section`/`tag`/`hidden`). Backward-compatible: no existing `language.yaml` needs changes, all four ship without `anki.fields` and fall back to `DEFAULT_FIELDS`. A misconfigured `source` or a duplicate unique slot now fails fast at `ankiforgeai init` (`NoteTypeConfigError`) instead of during enrichment. See `DEVELOPER_GUIDE.md` §10 for the schema and an example.

### Fixed
- Logging coverage: several failure paths produced no structlog trace at all. `dedupe.judge_review()` swallowed any AI-adjudication error (network, bad model, malformed response) completely silently, falling back to manual review with zero evidence why; unhandled exceptions in CLI commands (e.g. Anki unreachable during `push`/`sync`) bypassed structlog entirely and crashed with a raw traceback. `log.bound_run()` now logs `command.failed` with a full traceback for any unhandled exception before re-raising; AnkiConnect calls, `sync`, TTS voice selection, and HTTP/LLM retry attempts are now logged too instead of failing/retrying silently.
- `ankiforgeai init` silently no-oped when the Anki Note Type already existed, so front/back template and CSS changes (e.g. the card redesign above) never reached Anki after the first run — pushing cards only ever updates field values, not the note type's templates/styling. `init` now calls AnkiConnect's `updateModelTemplates`/`updateModelStyling` to sync the current design into an existing Note Type; re-run it after any card-design change.
- Cards accepted from manual review skipped enrichment entirely (Fixes #11): pronunciation/grammar/examples/audio/image generation only ever ran from the ingest pipeline for dedupe-accepted cards — accepting a `review`-status card just flipped its status to `APPROVED` with nothing enriched. The enrich+media block is now a shared `pipeline.enrich_and_generate_media()` helper used by both paths; incomplete cards are bounced back to `REVIEW` instead of marked `APPROVED`.
- Per-card error handling for batch enrichment failures (Fixes #7): pronunciation/grammar/examples previously shared one try/except per batch, so a single failed LLM call masked which card broke and let every accepted card reach `APPROVED` regardless of whether it was actually enriched. Each stage now runs independently, logs `enrich_failed`/`enrich_incomplete` per card, and incomplete cards route to `REVIEW`.
- `doctor`'s grammar check produced false positives on non-inflected parts of speech (adverb/pronoun/preposition/...) — `forms=None` is correct by design for those, but the check didn't know that. Now mirrors the same POS gate `enrich/grammar.py` already uses.
- Dedupe now catches duplicates within the same ingest batch (Fixes #13): `check_card()` only compared against already-persisted cards, so two duplicate/near-duplicate words returned by the LLM in one `ingest topic` call both slipped through as "new".
- Images are searched by an English gloss instead of the target-language word (Fixes #10): Unsplash/Pexels/Pixabay index tags predominantly in English, so searching by the nb/de/es lemma returned nothing for most non-English cards. `enrich_translation()` now also asks for a short English gloss (`Card.image_query`); `attach_image()` searches by it, falling back to `word`, and logs a warning instead of failing silently on an empty result.
- Setup wizard pointed new users at `scripts/init_db.py` and `scripts/setup_anki_notetype.py` — the latter raised `ImportError` immediately (superseded by the declarative Note Type engine above). Both scripts are gone; the wizard now points at `ankiforgeai init`.

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