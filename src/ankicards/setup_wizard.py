"""Interactive setup wizard for first-time configuration.

Guides user through:
1. Target language selection
2. UI language (for back_labels)
3. Card content preferences
4. LLM provider + model
5. Anki connection
6. Writes config.yaml
"""
from __future__ import annotations

import shutil
from pathlib import Path

import questionary
import yaml
from rich.console import Console
from rich.panel import Panel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LANGUAGES_DIR = PROJECT_ROOT / "languages"
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

console = Console()


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
        return yaml.safe_load(f)


def run_setup() -> None:
    console.print()
    console.print(
        Panel.fit(
            "🧠 AnkiForgeAI — First-time Setup\n"
            "Configure your flashcard pipeline in a few steps.",
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

    # ─── 3. Card content preferences ───
    console.print()
    console.print("[cyan]What to show on flashcards?[/]")
    show_image = questionary.confirm("Show images (Unsplash) for nouns?", default=True).ask()
    show_grammar = questionary.confirm("Show grammar table on back?", default=True).ask()
    show_examples = questionary.confirm("Show example sentences?", default=True).ask()
    show_pronunciation = questionary.confirm(
        "Show pronunciation hints?", default=True
    ).ask()
    auto_accept = questionary.confirm(
        "Auto-accept words without manual review? (recommended for daily cron)",
        default=True,
    ).ask()

    words_per_day = int(
        questionary.text("How many words per day?", default="10").ask() or "10"
    )

    # ─── 4. LLM provider ───
    provider = questionary.select(
        "Which LLM provider?",
        choices=[
            questionary.Choice(
                "OpenRouter (recommended, many models)", value="openrouter"
            ),
            questionary.Choice("Anthropic Claude", value="anthropic"),
        ],
    ).ask()

    if provider == "openrouter":
        default_model = "deepseek/deepseek-v4-flash"
    else:
        default_model = "claude-sonnet-4-5-20250929"
    model = questionary.text("Model name:", default=default_model).ask()

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

    # ─── 7. Summary ───
    console.print()
    console.print(Panel.fit(
        "✅ Setup complete!\n\n"
        f"Language:     {meta['name']}\n"
        f"Deck:         {meta['anki']['deck_name']}\n"
        f"Words/day:    {words_per_day}\n"
        f"LLM:          {model}\n"
        f"Images:       {'yes' if show_image else 'no'}\n"
        f"Auto-accept:  {'yes' if auto_accept else 'no'}\n\n"
        "Next steps:\n"
        "  cp .env.example .env    — add your API keys\n"
        "  python scripts/init_db.py\n"
        "  python scripts/setup_anki_notetype.py  (with Anki running)",
        border_style="green",
        title="🎉 AnkiForgeAI is ready!",
    ))