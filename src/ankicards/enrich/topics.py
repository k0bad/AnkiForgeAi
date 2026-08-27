"""Раскладка карточек по темам — тому, из чего вырастает тег `topic::` в Anki.

Карточкам из картинного словаря тема достаётся даром: такой источник сам держит
их в дереве (`dyr::fugler`), и импортёр переносит путь как есть. А слово, занесённое
с урока или из таблицы, приходит либо вовсе без темы, либо с темой, придуманной
на ходу («еда-и-напитки» рядом с уже существующей `mat-og-drikke`). В дереве
тегов Anki такие оседают отдельной кучей и сортировке не поддаются.

Справочник тем собирается из двух источников: темы, уже встречающиеся в базе
(то есть дерево источника), плюс `extra_topics` из языкового профиля — под то,
чего у картинного словаря нет в принципе. Хардкода тем здесь нет: сменится язык
— сменится и справочник.

Стадия ничего не выдумывает: слову, которому ни одна тема не подходит, модель
возвращает `null`, и карточка остаётся как была. Пустая тема лучше натянутой —
её человек заметит и поправит, а чужую не заметит.
"""

from __future__ import annotations

import json

from ..config import Config, get_language
from ..db import Database
from ..llm import call_json, load_prompt
from ..log import get_logger
from ..models import Card
from .backfill import StageFn

logger = get_logger(__name__)


def known_topics(db: Database, cfg: Config) -> dict[str, str]:
    """Справочник тем: что уже есть в базе + `extra_topics` языкового профиля.

    Значение — пояснение для модели, у большинства тем пустое: `dyr::fugler` и
    `mat-og-drikke::frukt` говорят сами за себя, а `småord::andre` без пояснения
    не значит ничего — по одному имени модель складывала туда что попало.

    Темы, которые сами нуждаются в раскладке (см. `needs_topic`), в справочник не
    попадают — иначе `leksjon` предлагался бы моделью как готовый ответ и стадия
    закрепляла бы ровно то, что пришла исправить.
    """
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT topic FROM cards WHERE topic IS NOT NULL AND language = ?",
            (cfg.language,),
        ).fetchall()
    catalogue = {row[0]: "" for row in rows if row[0] and not needs_topic_value(row[0])}
    catalogue.update(get_language(cfg.language).extra_topics)
    return dict(sorted(catalogue.items()))


#: Тема, из которой карточку надо разложить: её нет вовсе, либо это плоская
#: свалка импорта, либо она записана не на языке дерева (кириллицей) — то есть
#: придумана на ходу и дублирует уже существующую норвежскую.
FLAT_TOPICS = {"leksjon", "lesson", "manual", ""}


def needs_topic_value(topic: str | None) -> bool:
    if not topic:
        return True
    if topic.strip().lower() in FLAT_TOPICS:
        return True
    return any("Ѐ" <= ch <= "ӿ" for ch in topic)


def needs_topic(card: Card) -> bool:
    return needs_topic_value(card.topic)


def has_topic(card: Card) -> bool:
    """Обратное к needs_topic — в таком виде его ждёт backfill_stage."""
    return not needs_topic(card)


def classify_topics_batch(topics: dict[str, str]) -> StageFn:
    """Собрать стадию под конкретный справочник тем.

    Возвращает функцию в том виде, в каком её принимает `backfill.backfill_stage`
    (список карточек → тот же список), чтобы нарезка на куски, запись после
    каждого и переживание сбоя достались этой стадии даром.
    """

    async def _stage(cards: list[Card]) -> list[Card]:
        targets = [card for card in cards if needs_topic(card)]
        if not targets:
            return cards
        payload = [
            {
                "id": card.id,
                "word": card.word,
                "pos": card.pos.value,
                "translation": card.translation,
            }
            for card in targets
        ]
        catalogue = "\n".join(f"{name} — {hint}" if hint else name for name, hint in topics.items())
        prompt = load_prompt(
            "topic_classify",
            topics=catalogue,
            words_json=json.dumps(payload, ensure_ascii=False),
        )
        raw = await call_json(prompt, stage="enrich")
        if not isinstance(raw, list):
            raise ValueError(f"LLM вернул не массив: {type(raw).__name__}")

        allowed = set(topics)  # dict → множество имён; пояснения тут уже не нужны
        by_id: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, dict) or "id" not in item:
                continue
            topic = item.get("topic")
            if not topic:
                continue
            # Тема не из справочника — это выдумка модели, а не новая тема.
            # Принять её значит завести в дереве тегов ветку из одной карточки,
            # которую никто не заказывал.
            if topic not in allowed:
                logger.warning("topics.unknown", topic=topic, card_id=item["id"])
                continue
            by_id[str(item["id"])] = topic

        for card in targets:
            topic = by_id.get(str(card.id))
            if topic:
                card.topic = topic
        return cards

    return _stage
