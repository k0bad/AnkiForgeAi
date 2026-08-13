"""Перевод карточки на русский (если ещё не переведена).

Промпт языко-агностичен: берётся из languages/{code}/prompts/translation.md,
как и остальные enrich-стадии. Заодно просит у LLM короткий английский эквивалент
слова (card.image_query) — card.translation для этого не годится, он всегда на
русском независимо от cfg.ui_language, а провайдеры картинок (media/images.py)
индексируют преимущественно по-английски (issue #10).
"""

from __future__ import annotations

from ..llm import call_text, load_prompt
from ..models import Card


def _parse_translation(raw: str) -> tuple[str, str | None]:
    """Ответ LLM — две строки 'RU: ...' / 'EN: ...'. Если модель не выдержала
    формат, весь ответ трактуется как перевод (совместимо со старым однострочным промптом)."""
    ru: str | None = None
    en: str | None = None
    for line in raw.strip().splitlines():
        line = line.strip()
        if line[:3].upper() == "RU:":
            ru = line[3:].strip().strip('"').strip("'")
        elif line[:3].upper() == "EN:":
            en = line[3:].strip().strip('"').strip("'")

    if ru is None:
        ru = raw.strip().strip('"').strip("'")
    return ru, (en or None)


async def enrich_translation(card: Card) -> Card:
    """Добавить русский перевод (1-2 варианта) и англ. gloss для поиска картинок."""
    if card.translation:
        return card

    prompt = load_prompt("translation", word=card.word, pos=card.pos.value)
    raw = await call_text(prompt)
    ru, en = _parse_translation(raw)
    if ru:
        card.translation = ru
    if en:
        card.image_query = en
    return card
