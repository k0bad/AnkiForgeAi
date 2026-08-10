"""Генерация примера использования и его перевода.

Промпт: prompts/example_sentence.md
Ограничения: длина <= 12 слов, уровень соответствует card.level.
"""

from __future__ import annotations

import json

from ..llm import call_json, load_prompt
from ..models import Card


async def enrich_example(card: Card) -> Card:
    """Сгенерировать example + example_translation."""
    if card.example:
        return card
    result = await enrich_example_batch([card])
    return result[0]


async def enrich_example_batch(cards: list[Card], level: str = "A2") -> list[Card]:
    targets = [c for c in cards if not c.example]
    if not targets:
        return cards

    ref_level = targets[0].level.value if targets[0].level else level
    payload = [
        {"id": c.id, "word": c.word, "pos": c.pos.value, "translation": c.translation}
        for c in targets
    ]
    prompt = load_prompt(
        "example_sentence",
        level=ref_level,
        words_json=json.dumps(payload, ensure_ascii=False),
    )
    raw = await call_json(prompt)
    if not isinstance(raw, list):
        raise ValueError(f"LLM вернул не массив: {type(raw).__name__}")

    by_id: dict[str, dict] = {
        str(item["id"]): item for item in raw if isinstance(item, dict) and "id" in item
    }
    for card in cards:
        item = by_id.get(card.id)
        if not item:
            continue
        if item.get("example"):
            card.example = str(item["example"]).strip()
        if item.get("example_translation"):
            card.example_translation = str(item["example_translation"]).strip()
    return cards
