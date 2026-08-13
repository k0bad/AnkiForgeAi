# Contributing to AnkiForgeAI

Thanks for your interest in contributing!

## Quick Start

```bash
git clone https://github.com/k0bad/AnkiForgeAi.git
cd AnkiForgeAi
uv pip install -e ".[dev]"
cp .env.example .env   # add your API keys
pytest tests/ -v        # run tests
```

## Project Structure

See [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for the full architecture reference.

## Adding a New Language

1. Create `languages/{code}/language.yaml` (see [nb](languages/nb/language.yaml) or [de](languages/de/language.yaml) as templates)
2. Copy prompts: `cp prompts/*.md languages/{code}/prompts/`
3. Adapt prompts for the target language
4. Run `pytest tests/test_language.py -v` to verify1

## Code Quality

- `ruff check src/` — linting
- `ruff format src/` — formatting
- `mypy src/` — type checking
- `pytest tests/ -v` — all tests must pass

## Conventions

- `from __future__ import annotations` in every file
- Async for all I/O
- Pydantic v2 for config and models
- All DB operations in transactions via `Database.connect()`
- Never hardcode language — use `get_language()` + `language.yaml`

## Security

- **Never commit `.env`** — it's in `.gitignore`
- **Never commit API keys** in code, config, or prompts
- Use `.env.example` with placeholder values only
- Internal IPs/URLs belong in environment variables, not code