# Changelog

All notable changes to AnkiForgeAI will be documented in this file.

## [Unreleased]

### Added
- `ankiforgeai setup` now offers to register the daily-automation cycle with the OS
  scheduler on the spot (`src/ankicards/scheduler.py`): Task Scheduler on Windows via
  `Register-ScheduledTask -Force` (idempotent re-registration), cron elsewhere via a
  marker-commented `crontab` line (re-running replaces the old entry instead of
  duplicating it). Previously this required manually following `README.md` ->
  `Automated Daily Cycle`, which stays as the manual/inspectable path. Part of #58.
- Fixed dead `ingest.default_count` config: `ankiforgeai ingest topic` was ignoring it
  and always defaulting `--count` to a hardcoded 20 regardless of what `setup`'s "words
  per day" question wrote to `config.yaml`. `--count` now falls back to
  `ingest.default_count` when omitted. Part of #58.
- Direct Telegram backend for notifications (`notify/telegram.py`, closes #55): a
  `type: telegram` channel alongside the existing `webhook`, posting straight to the
  Telegram Bot API `sendMessage` — no n8n/Zapier relay required. Config gets `chat_id`,
  `topic_id` (optional, for forum topics) and `parse_mode` on `NotificationConfig`; the
  bot token lives in `.env` (`NOTIFY_TELEGRAM_TOKEN`, `Secrets.notify_telegram_token`),
  never in the committed `config.yaml`. Both backends can be enabled at once —
  `dispatch()` fans out to every enabled channel independently. `_BACKENDS` now maps
  each type to a `from_entry(NotificationConfig) -> Notifier | None` factory instead of
  a raw class, since construction differs per backend; `format_report()` moved from
  `notify/webhook.py` to `notify/format.py` since both backends render the same
  Telegram-flavored markdown text.
- Windows Task Scheduler support for the daily automation cycle: `scripts/daily_topic.ps1`
  mirrors `daily_topic.sh`'s full cycle (generate → dedupe/enrich → auto-push → notify) for
  environments without bash, documented in `README.md` alongside the existing cron example.
  The webhook URL also gets the same `.env` override treatment as the Telegram token above:
  `NOTIFY_WEBHOOK_URL` takes priority over `notifications[].url` in `config.yaml`, and a
  webhook channel with no URL from either source is skipped (`notify.no_url`) instead of
  failing the run — `WebhookNotifier.from_entry()` mirrors `TelegramNotifier.from_entry()`.

### Changed
- `config.yaml` moved out of git tracking (closes #52): `git pull` was overwriting local settings
  (Anki URL, n8n webhook, dedupe thresholds, image provider) with the repo's defaults. `config.yaml`
  is now in `.gitignore`; the repo ships `config.yaml.example` as the template. One-time setup:
  `cp config.yaml.example config.yaml` (also done automatically by `ankiforgeai setup`). See
  `DEVELOPER_GUIDE.md` §11 for the one-time migration steps on existing checkouts.

## [0.4.0] — 2026-08-16

### Added
- Second card redesign — physical business-card layout (`design_handoff_language_card`, `anki/notetype.py`): fixed 85×55mm front/back template with a layered background photo (blurred ambient copy + masked subject + directional scrim), POS color pills, and a header recap of word/pronunciation/POS/audio on both sides. Back body is an interactive two-tab examples/forms panel (vanilla JS), shown per-card only when both are present. `NoteFieldDef`'s slot vocabulary (`config.py`) changed to this design's slots (`front_sub`/`pos`/`level`/`translation`/`forms`/...) instead of the previous generic `section`/`tag`/`hidden` model.
- `scripts/measure_image_quality.py` + `scripts/build_image_quality_report.py` (issue #37): reusable baseline/post-fix tooling to measure image-search match quality — samples cards straight from the LLM (no DB/Anki writes), resolves the image query and top provider candidate, and renders a click-to-score HTML report with a live match-rate breakdown per language.
- Interactive `review` now lets a human pick the card's image instead of always auto-attaching the first search result (closes #36): after batch enrichment finishes for accepted cards, `review/interactive.py` shows up to `images.per_page` candidates (author + link) per eligible card and asks which to use (default = the same first result auto-pick would have chosen, so accepting is still one keypress) or to skip entirely. `media/images.py`'s `attach_image()` is split into `find_candidates()` (search only) and `save_image()` (download the chosen one) so the picker reuses the same search call instead of querying providers twice. Only applies to the interactive terminal flow — `enrich_and_generate_media()`/`accept_cards()` gained an `auto_pick_images` flag (default `True`) so the automatic ingest pipeline and the non-interactive `review accept` (AI-agent) path keep today's auto-pick-first behavior unchanged.
- `ankiforgeai setup` now syncs `.claude/skills/ankiforgeai/SKILL.md`'s trigger-phrase examples to the chosen `ui_language` (closes #25): `ru` keeps the Russian examples ("сгенерируй слов", ...), `en` switches to English equivalents ("generate words", ...). Only rewrites the trailing example clause of the frontmatter `description` line — idempotent, no-op if SKILL.md isn't present (e.g. pip-installed outside a checkout of this repo).

### Added
- Structured `structlog` events for review actions (closes #31): `review/actions.py` and `review/interactive.py` now emit `review_accept`/`review_skip`/`review_suspend`/`review_resume`/`review_edit`/`review_finalized` via the same `pipeline._record()` dual-write helper the rest of the pipeline uses, instead of writing to `audit_log` alone — so review sessions (including non-TTY `review accept`/`skip`/... for AI-agent-driven control) are visible in the live trace, not only via a later SQL query.
- Structured `structlog` events for Note Type create/update/sync during `ankiforgeai init` (closes #30): `notetype.created`, `notetype.templates_synced`, `notetype.styling_synced`, and `anki.unreachable` (previously a silent early return with no structlog trace at all) — distinguishing the three states relevant to the 0.3.0 silent-no-op bug fixed above.
- Two-tier logging model (`structlog` vs `audit_log`) documented in `DEVELOPER_GUIDE.md` §6 (closes #33): which layer writes which, why, and how `run_id` joins them.
- `tests/test_logging_coverage.py` (closes #32): asserts real failure paths in `anki/connect.py`, `media/images.py`, and `pipeline.py`'s enrich/media orchestration actually emit a `warning`-level `structlog` event via `structlog.testing.capture_logs()`, not just an `audit_log` row.
- Sequential, reusable `card.id` instead of random UUIDs: numbers are now assigned in insertion order (like a spreadsheet row) and a deleted card's number is reused by the next new one. A one-time `ankiforgeai migrate-ids` command migrates existing UUID-keyed data (`data/ankicards.db` backed up automatically before touching it; safe to re-run — a no-op once already migrated). `cards.id` and `audit_log.card_id` are now `INTEGER` (were `TEXT`).
- `ankiforgeai delete <id> [<id> ...]` (`--json`): permanently removes cards and frees their id for reuse. If the card was already pushed, the linked Anki note and its review history are deleted too — irreversible, the CLI prints a warning before proceeding in non-`--json` mode.
- `anki sync` now detects notes removed directly in Anki (not via `ankiforgeai delete`): a `cards.anki_note_id` that disappears from a fresh `findNotes()` is treated as deleted locally too, freeing its id — guarded against false positives from a mass `findNotes()` failure (deck rename, query typo) by only triggering when at least 3 linked notes vanish *and* they're more than half of all tracked notes.

### Fixed
- LLM calls now retry an empty completion (`content=None`/`""` with HTTP 200) instead of treating it as valid text (part of #28): previously the empty string passed through untouched and only failed later as a `ValueError` from JSON parsing, outside `_llm_retry`'s retry boundary (`_is_transient()` excludes `ValueError`) — cards could reach `APPROVED` with silently missing enrichment whenever a provider had this hiccup. `_call_openai`/`_call_anthropic` now raise `EmptyCompletionError` inside the retry-wrapped call so `tenacity` retries it like any other transient failure.
- `enrich_translation()` now backfills `card.image_query` even when `card.translation` already arrived from ingest (closes #47): `ingest topic` — the project's primary documented workflow — always returns cards with `translation` already set (`prompts/topic_words.md` doesn't ask for an English gloss at all), and `enrich_translation()` previously skipped entirely whenever `translation` was already present, so `image_query` was never populated for these cards and every fix in the #10 → #29 → #34 → #35 chain had no effect on them — silently masked on the `en` profile, where `card.word` happens to already be English. `pipeline.py`'s translation stage now calls `enrich_translation()` whenever `translation` is missing *or* the card is image-eligible (`images.enabled`, `pos` in `images.only_for_pos`) and `image_query` is still missing; the existing `translation` is never overwritten by that backfill call.
- The `EN:` line in translation prompts sometimes carried two senses joined by `" / "` (the same separator `RU:` legitimately uses for multiple translations) instead of one disambiguated phrase, for genuine homonyms (found via #37's baseline measurement, on Norwegian "lønn" — maple tree / salary; part of #50). The compound string was sent to the image provider as a single query and matched only one (effectively random) sense. `languages/{code}/prompts/translation.md` now explicitly forbids `" / "` in the `EN:` line across all four profiles (`de`/`en`/`es`/`nb`); `enrich_translation()` also detects it as a safety net, logs a `translation.image_query_multi_sense` warning, and keeps only the first phrase instead of sending the compound string as-is.
- The `EN:` line asked for in translation prompts (`languages/{code}/prompts/translation.md`) now requests a short disambiguated 2-4 word image-search phrase instead of a bare 1-word gloss (closes #34): homonyms/polysemous words (bank, spring, ...) and abstract nouns were resolving to whichever sense or "artsy" stock photo the provider's default ranking favored. `card.image_query`/`_parse_translation()` already accepted free text, so no code changes were needed — prompt-only fix across all four language profiles (`de`/`en`/`es`/`nb`). The silent-fallback-to-`card.word` half of this issue was already fixed by #29.
- `media/images.py`'s provider search calls now pin explicit relevance/content-type params instead of leaving them to each provider's default (closes #35): Unsplash gets `order_by=relevant` (confirmed against Unsplash's docs — already the default, but no longer implicit) and Openverse gets `category=photograph` (excludes illustration/digitized-artwork results), the same filtering Pixabay already had via `image_type=photo`. Pexels and Unsplash have no photo-vs-illustration filter to wire in — both catalogs are photography-only, so there was nothing to add there.
- `_run_enrich_stage()`'s `enrich_failed`/`enrich_incomplete` events (grammar/examples/pronunciation batch failures — #7/#39 territory) wrote to `audit_log` directly via `db.log_action()`, bypassing `structlog` entirely — found while writing the coverage tests for #32. Every enrichment-stage failure since #7 was invisible in the live trace, queryable only after the fact via SQL. Switched to `_record()` like every other stage.
- `prompts_dir` had no bundled-package fallback (closes #38): `languages_dir()` already fell back to the wheel-bundled copy when `PROJECT_ROOT/languages` was absent, but `Config.resolve_paths()` had no equivalent for `prompts_dir` — `BUNDLED_PROMPTS_DIR` was defined and never used. A `pip install`ed user with no local `prompts/` crashed the first time dedupe hit a fuzzy match (`prompts/dedupe_judge.md` has no per-language copies to fall back to first).
- Pronunciation enrichment failures now route the card to `REVIEW` instead of silently reaching `APPROVED` with no pronunciation (closes #39, found via #28 audit): it shared one try/except with translation, unlike grammar/examples, so a failed batch call never populated `incomplete_ids`. Moved onto the same per-card `_run_enrich_stage` helper.
- `enrich_translation()` logs a `translation.image_query_missing` warning when the LLM's response parses a Russian translation but not the English image-search gloss (closes #29): previously this failure was invisible — the card reached `APPROVED` normally, and `attach_image()` silently fell back to searching by the target-language word, which providers barely index. Only logged for cards `images.enabled`/`images.only_for_pos` will actually search for.
- `push`/`sync` now catch `AnkiConnectError` (closes #24): previously an unreachable Anki during `push`/`sync` fell through to a bare traceback on stderr with empty stdout, contradicting SKILL.md's documented `--json` contract that failures always print `✗ <reason>` to stdout.
- `push` now detects a missing Anki Note Type before attempting to push cards, instead of failing deep inside AnkiConnect calls; added a non-interactive `review` CLI path.
- `enrich/{grammar,examples,pronunciation}.py` matched LLM responses back to cards through a str-keyed `{str(card.id): ...}` dict — silently broken once `card.id` became `int` (see sequential-ids entry above), since the LLM's JSON response keys never matched the stringified lookup. Latent since the UUID→int migration; keyed by `card.id` directly now.

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