"""Перевод карточки на русский (если ещё не переведена)."""

from __future__ import annotations

from ..llm import call_text
from ..models import Card


async def enrich_translation(card: Card) -> Card:
    """Добавить русский перевод (1-2 варианта)."""
    if card.translation:
        return card

    prompt = (
        "Переведи норвежское слово (bokmål) на русский. "
        "Дай 1-2 короткие варианта, через ' / '. "
        "Ответь ТОЛЬКО переводом, без пояснений и кавычек.\n\n"
        f"Слово: {card.word}\n"
        f"Часть речи: {card.pos.value}\n"
    )
    translation = (await call_text(prompt)).strip().strip('"').strip("'")
    if translation:
        card.translation = translation
    return card
