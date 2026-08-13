---
name: ankiforgeai
description: Drive the ankiforgeai CLI (vocabulary ingest → dedupe → review → push to Anki) in this repo without a human terminal. Use whenever the user asks to add/generate vocabulary words, check pending/review cards, accept/reject/edit cards, push to Anki, or check pipeline status — including Russian requests like "сгенерируй слов", "покажи что на ревью", "запушь в анки", "прими карточку X".
---

# ankiforgeai CLI

Non-interactive contract for driving this repo's pipeline. Read this instead of
re-deriving CLI usage from `cli.py` each session.

## Golden rule: never run `ankiforgeai review` (no args)

`ankiforgeai review` with no subcommand launches a `questionary` TTY prompt loop —
it hangs/fails when run from an agent (no terminal to answer prompts). It's for the
human in an interactive shell only.

Instead, use the non-interactive subcommands below. They do the exact same DB
writes and enrichment as the interactive flow (same underlying code in
`src/ankicards/review/actions.py`), just addressed by card ID instead of prompts.

## Output convention

Every command that supports `--json` prints **only** the result JSON to stdout;
structured logs (JSON lines) go to stderr. Always pass `--json` and parse stdout —
don't parse the Rich tables meant for humans. Non-zero exit code = failure; the
error message is on stdout as `✗ <reason>` even in `--json` mode (only the success
path emits JSON), so check exit code before parsing.

## Commands

```bash
# 1. Add words
ankiforgeai ingest topic "<тема>" --count 20 --level A2 --json
ankiforgeai ingest url <URL> --json
# → {"new": N, "review": M, "merged": K, "enriched": ..., "audio": ..., "errors": ...}
# Cards that clear dedupe automatically end up status=approved already — no review needed.
# Cards with decision=review (fuzzy duplicate, AI unsure) need step 2.

# 2. See what needs a human/agent decision
ankiforgeai review list --json
# → JSON array of Card objects (status: review|pending) with id, word, translation, pos, ...
# review list without pending/review = nothing to do, skip straight to push.

# 3. Act on cards by id (all accept multiple ids; skip/suspend/accept are idempotent-ish
#    but will error ValueError→exit 1 on an unknown id, so pass ids you got from `review list`)
ankiforgeai review accept  <id> [<id> ...] --json   # → enrich+media, then approved (or back to review if enrichment incomplete)
ankiforgeai review skip    <id> [<id> ...]          # → status=skipped
ankiforgeai review suspend <id> [<id> ...]          # → status=suspended, revisit later
ankiforgeai review resume  <id> [<id> ...]          # → status=review again (undo skip/suspend)
ankiforgeai review edit    <id> -f word=... -f translation=... -f example=... -f example_translation=...

# 4. Push approved cards to Anki (requires Anki running with AnkiConnect on :8765)
ankiforgeai push --json   # → {"pushed": N}

# 5. Refresh local dedupe cache from Anki (run after manual edits in Anki itself)
ankiforgeai sync --json   # → {"synced": N}

# Status check anytime
ankiforgeai stats --json  # → {"pending": N, "review": N, "approved": N, "pushed": N, "suspended": N, "skipped": N, "anki_cache": N}

# Consistency check (approved/pushed cards vs enrich/images config toggles)
ankiforgeai doctor --json  # → JSON array of problems, [] = clean; exit 1 if any found
```

## Typical end-to-end flow

```bash
ankiforgeai ingest topic "еда" --count 20 --level A2 --json
ankiforgeai review list --json                       # inspect what's left in review/pending
ankiforgeai review accept <id1> <id2> ... --json      # or skip/suspend the rest
ankiforgeai push --json
```

If `review list` returns `[]` after ingest, everything cleared dedupe automatically —
go straight to `push`.

## Decide accept vs skip vs suspend vs edit

`review list --json` includes each card's current fields. To see *why* it needs
review (duplicate match scores/reasons), check the audit log directly — that detail
isn't in `review list` output:

```bash
sqlite3 data/ankicards.db "SELECT details FROM audit_log WHERE card_id='<id>' AND action='review_needed' ORDER BY id DESC LIMIT 1"
```

- **accept**: genuinely new word, or an acceptable near-duplicate the user wants kept.
- **skip**: true duplicate / not wanted.
- **suspend**: unsure, revisit later — stays out of `review list`'s pending/review set until `review resume`.
- **resume**: undo a skip/suspend — puts the card back in `review list`.
- **edit**: fix a wrong word/translation/example before accepting (only text fields — pos/level/topic aren't editable via CLI, fix at ingest time).

## Setup / troubleshooting still need a human

`ankiforgeai setup` (initial config wizard) and `ankiforgeai init` (first-run Anki
Note Type creation, needs Anki open) are one-time/rare — safe to ask the user to run
these themselves rather than scripting around them. `.env` holds secrets: never read
it, never print values loaded from it (see CLAUDE.md → Security).

## Full architecture

See `CLAUDE.md` (pipeline stages, principles) and `DEVELOPER_GUIDE.md` (schema,
config keys, troubleshooting table) for anything not covered above.
