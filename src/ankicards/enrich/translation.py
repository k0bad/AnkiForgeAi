"""Перевод карточки на русский (если ещё не переведена).

Промпт языко-агностичен: берётся из languages/{code}/prompts/translation.md,
как и остальные enrich-стадии.
"""

from __future__ import annotations

from ..llm import call_text, load_prompt
from ..models import Card


async def enrich_translation(card: Card) -> Card:
    """Добавить русский перевод (1-2 варианта)."""
    if card.translation:
        return card

    prompt = load_prompt("translation", word=card.word, pos=card.pos.value)
    translation = (await call_text(prompt)).strip().strip('"').strip("'")
    if translation:
        card.translation = translation
    return card
