"""Interactive setup wizard for first-time configuration.

Guides user through:
1. Target language selection
2. UI language (for back_labels)
3. Card content preferences (+ image provider API key)
4. LLM provider + model (+ API key)
5. Anki connection
6. Writes config.yaml and .env (any API keys entered above)
7. Optional: generates + pushes a first small batch of cards right away
   via .cli.init + .ingest.topic.ingest_by_topic + .pipeline
8. Optional: registers the daily-automation cycle with the OS scheduler
   (Task Scheduler / cron) via .scheduler.register_daily_automation
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import cast

import questionary
import yaml
from rich.console import Console
from rich.panel import Panel

from .config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, languages_dir
from .scheduler import register_daily_automation

LANGUAGES_DIR = languages_dir()
CONFIG_PATH = DEFAULT_CONFIG_PATH
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"
SKILL_MD_PATH = PROJECT_ROOT / ".claude" / "skills" / "ankiforgeai" / "SKILL.md"

_IMAGE_KEY_VARS = {
    "unsplash": "UNSPLASH_ACCESS_KEY",
    "pexels": "PEXELS_API_KEY",
    "pixabay": "PIXABAY_API_KEY",
}
_FIRST_BATCH_SIZE = 5
_FIRST_BATCH_TOPIC = "generelt"
_FIRST_BATCH_LEVEL = "A2"

# Trigger-phrase examples appended to SKILL.md's frontmatter `description`, keyed
# by ui_language — what the user actually types in chat, not the target language.
_SKILL_TRIGGER_EXAMPLES = {
    "ru": (
        'including Russian requests like "сгенерируй слов", "покажи что на ревью", '
        '"запушь в анки", "прими карточку X".'
    ),
    "en": (
        'including requests like "generate words", "show what\'s in review", '
        '"push to anki", "accept card X".'
    ),
}

console = Console()


def _sync_skill_trigger_phrases(ui_lang: str, skill_path: Path = SKILL_MD_PATH) -> bool:
    """Sync SKILL.md's description trigger-phrase examples to `ui_lang`.

    No-op if SKILL.md isn't present (e.g. pip-installed outside a checkout of
    this repo). Only rewrites the trailing example clause of the frontmatter
    `description` line, leaving the rest of the file untouched. Returns
    whether the file was changed.
    """
    if not skill_path.exists():
        return False

    examples = _SKILL_TRIGGER_EXAMPLES.get(ui_lang, _SKILL_TRIGGER_EXAMPLES["en"])
    lines = skill_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not line.startswith("description: "):
            continue
        prefix, sep, _ = line.partition(" — including")
        if not sep:
            return False
        newline = "\n" if line.endswith("\n") else ""
        new_line = f"{prefix} — {examples}{newline}"
        if new_line == line:
            return False
        lines[i] = new_line
        skill_path.write_text("".join(lines), encoding="utf-8")
        return True
    return False


def _discover_languages() -> list[str]:
    """Find available language profiles."""
    langs = []
    for d in sorted(LANGUAGES_DIR.iterdir()):
        if d.is_dir() and (d / "language.yaml").exists():
            langs.append(d.name)
    return langs


def _load_language_meta(code: str) -> dict:
    """Load language.yaml metadata."""
    with (LANGUAGES_DIR / code / "language.yaml").open(encoding="utf-8") as f:
        return cast(dict, yaml.safe_load(f))


def _write_env_secrets(env_path: Path, updates: dict[str, str]) -> None:
    """Set `KEY=value` lines in `env_path` for `updates`, preserving everything else.

    Seeds from .env.example (structure/comments) if `env_path` doesn't exist yet.
    Keys not already present as a line get appended.
    """
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    elif ENV_EXAMPLE_PATH.exists():
        lines = ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        lines = []

    remaining = dict(updates)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0]
        if key in remaining:
            newline = "\n" if line.endswith("\n") else ""
            lines[i] = f"{key}={remaining.pop(key)}{newline}"

    for key, value in remaining.items():
        lines.append(f"{key}={value}\n")

    env_path.write_text("".join(lines), encoding="utf-8")


def _generate_first_card(count: int) -> str:
    """Create the DB/Note Type (if needed) and generate + push a small first batch.

    Best-effort: any failure (bad/missing API key, Anki not running, ...) is
    caught and turned into a plain-language status string instead of crashing
    the wizard — config.yaml is already saved by the time this runs.
    """
    from .cli import init as cli_init

    async def _run() -> str:
        from .anki.connect import AnkiConnect, AnkiConnectError
        from .config import get_config
        from .db import Database
        from .ingest.topic import ingest_by_topic
        from .pipeline import NoteTypeMissingError, push_approved, run_ingest_pipeline

        cfg = get_config()
        db = Database(cfg.paths.db)
        exclude = [w for _, w in db.all_words()]
        cards = await ingest_by_topic(
            topic=_FIRST_BATCH_TOPIC,
            count=min(count, _FIRST_BATCH_SIZE),
            level=_FIRST_BATCH_LEVEL,
            exclude_words=exclude,
        )
        if not cards:
            return (
                "the LLM returned no cards — check your API key, "
                "then run `ankiforgeai ingest topic ...`"
            )
        stats = await run_ingest_pipeline(cards, db=db, cfg=cfg)

        try:
            anki = AnkiConnect(cfg)
            pushed = await push_approved(db, anki, cfg)
        except (AnkiConnectError, NoteTypeMissingError):
            return (
                f"generated {stats['new']} card(s) — start Anki, "
                "run `ankiforgeai init` then `ankiforgeai push`"
            )

        if pushed:
            return f"generated and pushed {pushed} card(s) — open Anki to see them"
        return (
            f"generated {stats['new']} card(s), {stats['review']} need review "
            "— run `ankiforgeai review`"
        )

    try:
        cli_init()
        return asyncio.run(_run())
    except Exception as e:
        return (
            f"couldn't generate cards yet ({e}) — check .env, "
            "then run `ankiforgeai ingest topic ...`"
        )


def run_setup() -> None:
    console.print()
    console.print(
        Panel.fit(
            "🧠 AnkiForgeAI — First-time Setup\nConfigure your flashcard pipeline in a few steps.",
            border_style="cyan",
        )
    )

    # ─── 1. Target language ───
    available = _discover_languages()
    choices = []
    for code in available:
        meta = _load_language_meta(code)
        choices.append(
            questionary.Choice(
                title=f"{meta['name']} ({code})",
                value=code,
            )
        )

    target = questionary.select(
        "Which language do you want to learn?",
        choices=choices,
    ).ask()

    if not target:
        console.print("[red]Setup cancelled.[/]")
        return

    meta = _load_language_meta(target)
    console.print(f"[green]✓[/] Target language: {meta['name']}")

    # ─── 2. UI language ───
    ui_lang = questionary.select(
        "What language should card labels use? (back side of card)",
        choices=[
            questionary.Choice("🇷🇺 Russian", value="ru"),
            questionary.Choice("🇬🇧 English", value="en"),
        ],
    ).ask()

    console.print(f"[green]✓[/] Card labels: {'English' if ui_lang == 'en' else 'Russian'}")

    transcription = questionary.select(
        "How should pronunciation be shown?",
        choices=[
            questionary.Choice(
                "Practical transcription (Cyrillic, for Russian speakers)", value="practical"
            ),
            questionary.Choice("IPA (International Phonetic Alphabet, universal)", value="ipa"),
        ],
    ).ask()

    # ─── 3. Card content preferences ───
    env_updates: dict[str, str] = {}

    console.print()
    console.print("[cyan]What to show on flashcards?[/]")
    show_image = questionary.confirm("Show images for nouns?", default=True).ask()
    image_provider = "unsplash"
    if show_image:
        image_provider = questionary.select(
            "Which image provider?",
            choices=[
                questionary.Choice("Unsplash (recommended, real photos)", value="unsplash"),
                questionary.Choice("Pexels", value="pexels"),
                questionary.Choice("Pixabay", value="pixabay"),
                questionary.Choice("Openverse (no API key needed)", value="openverse"),
            ],
        ).ask()
        image_key_var = _IMAGE_KEY_VARS.get(image_provider)
        if image_key_var:
            image_api_key = questionary.password(
                f"{image_key_var} (leave empty to add it to .env yourself later):"
            ).ask()
            if image_api_key:
                env_updates[image_key_var] = image_api_key
    show_grammar = questionary.confirm("Show grammar table on back?", default=True).ask()
    show_examples = questionary.confirm("Show example sentences?", default=True).ask()
    show_pronunciation = questionary.confirm("Show pronunciation hints?", default=True).ask()
    auto_accept = questionary.confirm(
        "Auto-accept words without manual review? (recommended for daily cron)",
        default=True,
    ).ask()

    words_per_day = int(questionary.text("How many words per day?", default="10").ask() or "10")

    # ─── 4. LLM provider ───
    provider = questionary.select(
        "Which LLM provider?",
        choices=[
            questionary.Choice("OpenRouter (recommended, many models)", value="openrouter"),
            questionary.Choice("Anthropic Claude (API key)", value="anthropic"),
            questionary.Choice(
                "Claude Code CLI — no API key, uses your `claude login`"
                " (Pro/Max subscription or ANTHROPIC_API_KEY at the Claude Code level)",
                value="claude_cli",
            ),
        ],
    ).ask()

    if provider == "openrouter":
        default_model = "deepseek/deepseek-v4-flash"
    elif provider == "claude_cli":
        default_model = "sonnet"
    else:
        default_model = "claude-sonnet-4-5-20250929"
    model = questionary.text("Model name:", default=default_model).ask()

    llm_api_key = None
    if provider == "claude_cli":
        if shutil.which("claude") is None:
            console.print(
                "[yellow]⚠ `claude` not found in PATH — install Claude Code first, "
                "then run `claude login` before using this provider.[/]"
            )
        console.print(
            "[dim]claude_cli shares rate-limit/quota with your interactive Claude Code "
            "sessions — fine for occasional generation, not recommended for a heavy daily "
            "cron job.[/]"
        )
    else:
        llm_key_var = "OPENROUTER_API_KEY" if provider == "openrouter" else "ANTHROPIC_API_KEY"
        llm_api_key = questionary.password(
            f"{llm_key_var} (leave empty to add it to .env yourself later):"
        ).ask()
        if llm_api_key:
            env_updates[llm_key_var] = llm_api_key
    llm_ready = bool(llm_api_key) or provider == "claude_cli"

    # ─── 5. Anki connection ───
    console.print()
    console.print("[cyan]Anki connection[/]")
    anki_url = questionary.text(
        "AnkiConnect URL (Anki must be running with the addon)",
        default="http://127.0.0.1:8765",
    ).ask()

    # ─── 6. Build config ───
    config = {
        "language": target,
        "ui_language": ui_lang,
        "transcription": transcription,
        "paths": {
            "db": "data/ankicards.db",
            "logs_dir": "data/logs",
            "audio_dir": "media/audio",
            "images_dir": "media/images",
            "prompts_dir": "prompts",
        },
        "anki": {
            "url": anki_url,
            "deck_name": meta["anki"]["deck_name"],
            "note_type": meta["anki"].get("note_type", "LanguageCard"),
        },
        "dedupe": {
            "exact_match_field": "Word",
            "fuzzy_threshold_review": 85,
            "fuzzy_threshold_auto": 82 if auto_accept else 70,
            "compare_fields": ["Word", "Translation"],
        },
        "ingest": {
            "url_max_size_mb": 5,
            "url_timeout_seconds": 30,
            "default_count": words_per_day,
            "default_level": "A2",
        },
        "llm": {
            "provider": provider,
            "model": model,
            "base_url": "https://openrouter.ai/api/v1" if provider == "openrouter" else "",
            "max_tokens": 4096,
            "temperature": 0.3,
        },
        "tts": {
            "voice_male": meta["tts"]["voice_male"],
            "voice_female": meta["tts"]["voice_female"],
            "default_voice": meta["tts"].get("default_voice", "female"),
            "rate": "+0%",
            "pitch": "+0Hz",
        },
        "images": {
            "enabled": show_image,
            "provider": image_provider,
            "per_page": 5,
            "only_for_pos": ["noun"],
            "max_size_kb": 500,
            "resize_to": [600, 600],
        },
        "review": {
            "mode": "auto" if auto_accept else "semiauto",
            "auto_accept_below_score": auto_accept,
        },
        "enrich": {
            "grammar": show_grammar,
            "examples": show_examples,
            "pronunciation": show_pronunciation,
        },
        "logging": {
            "level": "INFO",
            "json_logs": True,
            "human_logs": True,
        },
        "tags": {
            "topic_prefix": "topic",
            "level_prefix": "level",
            "pos_prefix": "pos",
            "source_prefix": "source",
        },
    }

    # Backup existing config
    if CONFIG_PATH.exists():
        backup = CONFIG_PATH.with_suffix(".yaml.bak")
        shutil.copy2(CONFIG_PATH, backup)
        console.print(f"[dim]Backed up existing config to {backup.name}[/]")

    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    if _sync_skill_trigger_phrases(ui_lang):
        console.print(f"[green]✓[/] Synced skill trigger phrases ({ui_lang})")

    if env_updates:
        _write_env_secrets(ENV_PATH, env_updates)
        console.print(f"[green]✓[/] Saved {len(env_updates)} API key(s) to .env")

    # ─── 7. First card (optional) ───
    console.print()
    console.print("[cyan]First card[/]")
    first_card_summary = "skipped — see Next steps below"
    if llm_ready:
        generate_now = questionary.confirm(
            f"Generate your first {min(words_per_day, _FIRST_BATCH_SIZE)} card(s) right now?",
            default=True,
        ).ask()
        if generate_now:
            first_card_summary = _generate_first_card(words_per_day)
            console.print(f"[green]✓[/] {first_card_summary}")
    else:
        console.print(
            "[dim]Skipped — no LLM API key entered. Add one to .env, then run "
            "`ankiforgeai init` and `ankiforgeai ingest topic ...`.[/]"
        )

    # ─── 8. Daily automation (optional) ───
    console.print()
    console.print("[cyan]Daily automation[/]")
    setup_automation = questionary.confirm(
        "Set up daily automatic card generation (Task Scheduler / cron)?",
        default=True,
    ).ask()

    automation_summary = "not set up — see README.md → Automated Daily Cycle"
    if setup_automation:
        run_time = (
            questionary.text("What time should it run? (24h HH:MM)", default="08:00").ask()
            or "08:00"
        )
        ok, message = register_daily_automation(PROJECT_ROOT, run_time)
        console.print(f"[green]✓[/] {message}" if ok else f"[yellow]![/] {message}")
        automation_summary = message if ok else f"failed ({message}) — see README.md"

    # ─── 9. Summary ───
    next_steps = []
    if provider == "claude_cli":
        next_steps.append("  claude login            — one-time, if not already logged in")
    elif not env_updates:
        next_steps.append("  cp .env.example .env    — add your API keys")
    elif not llm_api_key:
        next_steps.append(f"  add your LLM key to .env — {llm_key_var}")
    if not llm_ready or "generated" not in first_card_summary:
        next_steps.append(
            "  ankiforgeai init        — creates the DB and Anki Note Type (start Anki first)"
        )
    next_steps_text = "\n".join(next_steps) if next_steps else "  You're all set — open Anki!"

    console.print()
    console.print(
        Panel.fit(
            "✅ Setup complete!\n\n"
            f"Language:     {meta['name']}\n"
            f"Deck:         {meta['anki']['deck_name']}\n"
            f"Words/day:    {words_per_day}\n"
            f"LLM:          {model} ({provider})\n"
            f"Images:       {image_provider if show_image else 'no'}\n"
            f"Pronunciation:{transcription if show_pronunciation else 'no'}\n"
            f"Auto-accept:  {'yes' if auto_accept else 'no'}\n"
            f"First card:   {first_card_summary}\n"
            f"Automation:   {automation_summary}\n\n"
            f"Next steps:\n{next_steps_text}\n\n"
            "Tip: `ankiforgeai about` — version, roadmap, changelog links",
            border_style="green",
            title="🎉 AnkiForgeAI is ready!",
        )
    )
