"""Обогащение карточки грамматическими формами.

Для каждой части речи запрашивает у Claude соответствующие формы:
- noun  → NounForms (en/et + 4 формы)
- verb  → VerbForms (инфинитив, презенс, претерит, перфект)
- adj   → AdjectiveForms (positive m/n/pl + комп/суперлатив)

Если pos не один из основных — оставляет forms=None.
"""

from __future__ import annotations

import json

from ..llm import call_json, load_prompt
from ..models import POS, Card

INFLECTED_POS = {POS.NOUN, POS.VERB, POS.ADJECTIVE}
_INFLECTED_POS = INFLECTED_POS  # для совместимости с doctor.py


async def enrich_grammar(card: Card) -> Card:
    """Добавить к карточке грамматические формы по части речи."""
    if card.pos not in INFLECTED_POS:
        return card
    result = await enrich_grammar_batch([card])
    return result[0]


async def enrich_grammar_batch(cards: list[Card]) -> list[Card]:
    """Батч-обработка для экономии токенов (один LLM-вызов на N карточек)."""
    # not c.forms — как в enrich_pronunciation_batch и enrich_example_batch: спрашиваем
    # только то, чего нет. Эта стадия одна из трёх фильтра не имела и переспрашивала
    # формы для всех склоняемых карточек пачки. На повторном accept (карточка вернулась
    # в review из-за сбоя другой стадии) это означало заново сгенерировать сотню уже
    # готовых парадигм — лишние минуты и лишний шанс словить транзиентный сбой,
    # который снова уронит всю пачку. Карточке, которой формы нужно пересчитать,
    # их обнуляют явно — см. review/actions.py::edit_card при смене pos.
    targets = [c for c in cards if c.pos in INFLECTED_POS and not c.forms]
    if not targets:
        return cards

    payload = [{"id": c.id, "word": c.word, "pos": c.pos.value} for c in targets]
    prompt = load_prompt(
        "grammar_forms",
        words_json=json.dumps(payload, ensure_ascii=False),
    )
    raw = await call_json(prompt, stage="enrich")
    if not isinstance(raw, list):
        raise ValueError(f"LLM вернул не массив: {type(raw).__name__}")

    forms_by_id: dict[str, dict | None] = {}
    for item in raw:
        if isinstance(item, dict) and "id" in item:
            forms_by_id[str(item["id"])] = item.get("forms")

    for card in cards:
        key = str(card.id)
        if key in forms_by_id and forms_by_id[key]:
            card.forms = forms_by_id[key]
    return cards
