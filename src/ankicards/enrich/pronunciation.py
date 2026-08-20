"""Генерация транскрипции произношения для слов.

Тип транскрипции берётся из config.transcription:
    practical → prompts/russian_pronunciation.md (кириллица, по умолчанию)
    ipa       → prompts/ipa_pronunciation.md (Международный фонетический алфавит)
"""

from __future__ import annotations

import json

from ..config import get_config
from ..llm import call_json, load_prompt
from ..models import Card

_PROMPTS: dict[str, str] = {
    "practical": "russian_pronunciation",
    "ipa": "ipa_pronunciation",
}


async def enrich_pronunciation_batch(cards: list[Card]) -> list[Card]:
    """Сгенерировать транскрипцию произношения для пачки карточек."""
    targets = [c for c in cards if not c.pronunciation]
    if not targets:
        return cards

    cfg = get_config()
    prompt_name = _PROMPTS.get(cfg.transcription)
    if prompt_name is None:
        raise ValueError(f"Неизвестный тип транскрипции: {cfg.transcription!r}")

    payload = [{"id": c.id, "word": c.word, "pos": c.pos.value} for c in targets]
    prompt = load_prompt(
        prompt_name,
        words_json=json.dumps(payload, ensure_ascii=False),
    )
    raw = await call_json(prompt, stage="enrich")
    if not isinstance(raw, list):
        raise ValueError(f"LLM вернул не массив: {type(raw).__name__}")

    by_id = {
        str(item["id"]): item.get("pronunciation", "")
        for item in raw
        if isinstance(item, dict) and "id" in item
    }

    for card in cards:
        pron = by_id.get(str(card.id))
        if pron:
            card.pronunciation = pron.strip()
    return cards


async def enrich_pronunciation(card: Card) -> Card:
    """Сгенерировать транскрипцию для одной карточки."""
    if card.pronunciation:
        return card
    result = await enrich_pronunciation_batch([card])
    return result[0]
