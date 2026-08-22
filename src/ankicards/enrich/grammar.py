"""Обогащение карточки грамматическими формами.

Для каждой части речи запрашивает у Claude соответствующие формы:
- noun  → NounForms (en/et + 4 формы)
- verb  → VerbForms (инфинитив, презенс, претерит, перфект)
- adj   → AdjectiveForms (positive m/n/pl + комп/суперлатив)

Если pos не один из основных — оставляет forms=None.
"""

from __future__ import annotations

import json
from typing import Any

from ..llm import call_json, load_prompt
from ..models import POS, Card

INFLECTED_POS = {POS.NOUN, POS.VERB, POS.ADJECTIVE}
_INFLECTED_POS = INFLECTED_POS  # для совместимости с doctor.py

# Поля, без которых парадигма считается недоделанной. comparative/superlative у
# прилагательных сюда не входят: у «syk» их нет, и требовать их — значит гонять
# такую карточку к модели на каждом заходе.
_REQUIRED_FORMS: dict[POS, tuple[str, ...]] = {
    POS.NOUN: (
        "gender",
        "indefinite_singular",
        "definite_singular",
        "indefinite_plural",
        "definite_plural",
    ),
    POS.VERB: ("infinitive", "present", "past", "perfect"),
    POS.ADJECTIVE: ("positive_common", "positive_neuter", "positive_plural"),
}


def _known_forms(card: Card) -> dict[str, Any]:
    """Непустые формы, уже стоящие на карточке — то, что не надо ни спрашивать, ни терять."""
    return {key: value for key, value in (card.forms or {}).items() if value}


def forms_complete(card: Card) -> bool:
    """Есть ли у карточки полная парадигма для её части речи.

    Не `bool(card.forms)`: Bildetema проставляет существительному только род (он у
    них в словаре), и по «непусто» такая карточка выглядела бы готовой — склонение
    к ней уже никто не добрал бы.
    """
    if card.pos not in INFLECTED_POS:
        return True
    forms = card.forms or {}
    return all(forms.get(key) for key in _REQUIRED_FORMS[card.pos])


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
    targets = [c for c in cards if not forms_complete(c)]
    if not targets:
        return cards

    # Известный род уходит в запрос, а не только накладывается на ответ: у женского
    # рода своё склонение (ei jente → jenta, не jenten), и молча переписать род поверх
    # мужской парадигмы значило бы оставить внутри карточки противоречие.
    payload: list[dict[str, Any]] = []
    for card in targets:
        entry: dict[str, Any] = {"id": card.id, "word": card.word, "pos": card.pos.value}
        known = _known_forms(card)
        if known:
            entry["known_forms"] = known
        payload.append(entry)
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
        generated = forms_by_id.get(str(card.id))
        if generated:
            # То, что уже стояло, перекрывает ответ модели: род существительного
            # пришёл из словаря Bildetema, а не из догадки (см. _GENDER_BY_ARTICLE),
            # и промпт прямым текстом разрешает модели путать женский род с мужским.
            card.forms = {**generated, **_known_forms(card)}
    return cards
