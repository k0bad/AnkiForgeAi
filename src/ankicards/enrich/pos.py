"""Доопределение части речи у карточек, где её не дал источник.

Отдельная стадия, а не деталь импортёра Bildetema: часть речи нужна и грамматике
(INFLECTED_POS решает, у кого вообще бывают формы), и картинкам
(`images.only_for_pos`), и тегам в Anki (`pos::noun` — то, чем колода
существительных отделяется от колоды глаголов). Карточка, оставшаяся POS.OTHER,
молча теряет всё перечисленное.
"""

from __future__ import annotations

import json

from ..llm import call_json, load_prompt
from ..log import get_logger
from ..models import POS, Card

logger = get_logger(__name__)

_POS_BY_VALUE = {p.value: p for p in POS}


async def classify_pos_batch(cards: list[Card]) -> list[Card]:
    """Определить часть речи для всех POS.OTHER одним батч-вызовом.

    Ключ ответа — позиция в списке запроса, а не card.id: на импорте эта стадия
    работает до вставки в БД, когда номера ещё нет вовсе.

    Провал не фатален: часть речи — уточнение, а не условие. Слово, перевод, фото
    и аудио уже есть, и ронять из-за неё весь заход значило бы потерять и их —
    карточка просто остаётся POS.OTHER, человек видит это на ревью и правит через
    `review edit`.
    """
    targets = [card for card in cards if card.pos is POS.OTHER]
    if not targets:
        return cards

    payload = [
        {"id": index, "word": card.word, "translation": card.translation}
        for index, card in enumerate(targets)
    ]
    prompt = load_prompt("pos_classify", words_json=json.dumps(payload, ensure_ascii=False))
    try:
        raw = await call_json(prompt, stage="enrich")
    except Exception as e:
        logger.warning("pos_classify_failed", count=len(targets), error=str(e))
        return cards
    if not isinstance(raw, list):
        logger.warning("pos_classify_bad_shape", got=type(raw).__name__)
        return cards

    pos_by_key = {
        str(item["id"]): str(item.get("pos", "")).lower()
        for item in raw
        if isinstance(item, dict) and "id" in item
    }
    for index, card in enumerate(targets):
        pos = _POS_BY_VALUE.get(pos_by_key.get(str(index), ""))
        if pos is None:
            logger.warning("pos_unresolved", word=card.word)
            continue
        card.pos = pos
    return cards
