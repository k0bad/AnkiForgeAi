# Changelog

All notable changes to AnkiForgeAI will be documented in this file.

## [0.1.0] — 2026-08-10

### Added
- Multi-language architecture via `languages/{code}/language.yaml` profiles
- Built-in support: Norwegian Bokmål (nb), German (de)
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