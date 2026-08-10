"""Генерация слов по теме через Claude API.

Стратегия:
1. Загрузить промпт prompts/topic_words.md.
2. Подставить переменные: topic, count, level, дополнительные ограничения.
3. Запросить Claude с response_format=JSON.
4. Распарсить → list[Card] со status=pending.

Каждый Card после этой стадии содержит минимум: word, pos, translation.
Грамматические формы добавятся на стадии enrich.
"""

from __future__ import annotations

from ..llm import call_json, load_prompt
from ..models import POS, Card, Level, Status


async def ingest_by_topic(
    topic: str,
    count: int = 20,
    level: str = "A2",
    exclude_words: list[str] | None = None,
) -> list[Card]:
    """Сгенерировать список слов по теме.

    `exclude_words` — слова, уже имеющиеся в Anki/staging,
    чтобы LLM их не повторял.
    """
    exclude_list = ", ".join(exclude_words) if exclude_words else "(none)"
    prompt = load_prompt(
        "topic_words",
        topic=topic,
        count=count,
        level=level,
        exclude_list=exclude_list,
    )
    raw_items = await call_json(prompt)
    if not isinstance(raw_items, list):
        raise ValueError(f"LLM вернул не массив: {type(raw_items).__name__}")

    return [
        _to_card(item, topic=topic, level=level)
        for item in raw_items
        if isinstance(item, dict) and item.get("word") and item.get("translation")
    ]


def _to_card(item: dict, topic: str, level: str) -> Card:
    pos_raw = str(item.get("pos", "other")).lower()
    try:
        pos = POS(pos_raw)
    except ValueError:
        pos = POS.OTHER

    level_enum: Level | None
    try:
        level_enum = Level(level.lower())
    except ValueError:
        level_enum = None

    return Card(
        word=str(item["word"]).strip(),
        pos=pos,
        translation=str(item["translation"]).strip(),
        level=level_enum,
        topic=topic,
        source="topic-gen",
        status=Status.PENDING,
    )
