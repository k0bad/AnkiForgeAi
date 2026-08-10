"""Генерация русской транскрипции для норвежских слов.

Промпт: prompts/russian_pronunciation.md
"""

from __future__ import annotations

import json

from ..llm import call_json, load_prompt
from ..models import Card


async def enrich_pronunciation_batch(cards: list[Card]) -> list[Card]:
    """Сгенерировать русскую транскрипцию для пачки карточек."""
    targets = [c for c in cards if not c.pronunciation]
    if not targets:
        return cards

    payload = [{"id": c.id, "word": c.word, "pos": c.pos.value} for c in targets]
    prompt = load_prompt(
        "russian_pronunciation",
        words_json=json.dumps(payload, ensure_ascii=False),
    )
    raw = await call_json(prompt)
    if not isinstance(raw, list):
        raise ValueError(f"LLM вернул не массив: {type(raw).__name__}")

    by_id = {
        str(item["id"]): item.get("pronunciation", "")
        for item in raw
        if isinstance(item, dict) and "id" in item
    }

    for card in cards:
        pron = by_id.get(card.id)
        if pron:
            card.pronunciation = pron.strip()
    return cards


async def enrich_pronunciation(card: Card) -> Card:
    """Сгенерировать транскрипцию для одной карточки."""
    if card.pronunciation:
        return card
    result = await enrich_pronunciation_batch([card])
    return result[0]
