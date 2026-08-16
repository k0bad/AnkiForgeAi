"""Перевод карточки на русский (если ещё не переведена).

Промпт языко-агностичен: берётся из languages/{code}/prompts/translation.md,
как и остальные enrich-стадии. Заодно просит у LLM короткий английский эквивалент
слова (card.image_query) — card.translation для этого не годится, он всегда на
русском независимо от cfg.ui_language, а провайдеры картинок (media/images.py)
индексируют преимущественно по-английски (issue #10).
"""

from __future__ import annotations

from ..llm import call_text, load_prompt
from ..log import get_logger
from ..models import Card

logger = get_logger(__name__)


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
    """Добавить русский перевод (1-2 варианта), если его ещё нет, и англ. gloss
    для поиска картинок, если его ещё нет.

    Раньше уже заполненный translation означал полный skip — а ingest topic
    (prompts/topic_words.md) сам не спрашивает EN-фразу, только word/pos/translation,
    так что image_query для таких карточек не заполнялся никогда (issue #47):
    вся цепочка фиксов #10/#29/#34/#35 не применялась к основному workflow
    проекта. Теперь при уже заполненном translation вызов всё равно идёт, если
    не хватает именно image_query — но сам translation не перезаписывается
    новым результатом LLM.
    """
    if card.translation and card.image_query:
        return card

    prompt = load_prompt("translation", word=card.word, pos=card.pos.value)
    raw = await call_text(prompt)
    ru, en = _parse_translation(raw)
    if en and " / " in en:
        # Модель иногда всё же сваливает EN: в тот же "sense1 / sense2" формат,
        # что RU: (см. пример омонимов ниже) — вопреки инструкции промпта дать
        # ОДНУ расшифрованную фразу. Составной запрос бьёт по случайной части
        # смысла в провайдере картинок, так что берём только первый вариант вместо
        # того, чтобы отправлять его как есть (issue #50, найдено на "lønn").
        logger.warning("translation.image_query_multi_sense", word=card.word, raw_en=en)
        en = en.split(" / ")[0].strip()
    if ru and not card.translation:
        card.translation = ru
    if en:
        card.image_query = en
    return card
