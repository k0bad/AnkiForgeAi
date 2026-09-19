"""Тесты на раскладку карточек по темам.

Покрыть:
- что считается «темы нет»: пусто, плоская свалка импорта, тема кириллицей
- справочник: темы из базы + extra_topics профиля, без тех, что сами ждут раскладки
- пояснения к темам доезжают до промпта
- тема не из справочника — выдумка модели, а не новая тема
- null от модели оставляет карточку как была
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards import config as config_module
from ankicards.db import Database
from ankicards.enrich import topics as topics_module
from ankicards.enrich.topics import classify_topics_batch, has_topic, known_topics, needs_topic
from ankicards.models import POS, Card, Status


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _card(word: str, topic: str | None, card_id: int | None = None) -> Card:
    return Card(
        id=card_id,
        language="nb",
        word=word,
        translation="—",
        pos=POS.NOUN,
        topic=topic,
        status=Status.REVIEW,
    )


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        (None, True),
        ("", True),
        ("leksjon", True),
        ("LEKSJON", True),
        ("еда-и-напитки", True),  # придумана на ходу, дублирует mat-og-drikke
        ("язык-и-письмо", True),
        ("dyr::fugler", False),
        ("klær", False),
        ("småord::mengde", False),
    ],
)
def test_needs_topic(topic: str | None, expected: bool) -> None:
    assert needs_topic(_card("ord", topic)) is expected
    assert has_topic(_card("ord", topic)) is not expected


def test_catalogue_skips_topics_that_themselves_need_sorting(db: Database, monkeypatch) -> None:
    """`leksjon` в справочнике закрепил бы ровно то, что стадия пришла исправить."""
    for word, topic in [
        ("hund", "dyr::kjæledyr"),
        ("brød", "mat-og-drikke::brød-og-bakverk"),
        ("presis", "leksjon"),
        ("kurv", "магазин-и-покупки"),
    ]:
        db.insert_card(_card(word, topic))

    monkeypatch.setattr(
        topics_module,
        "get_language",
        lambda code: config_module.LanguageConfig(
            code="nb", name="nb", extra_topics={"egenskaper": "признак", "småord::mengde": ""}
        ),
    )
    # Справочник читает базу по cfg.language, а карточки выше — nb: язык берём
    # не из config.yaml (там может стоять en), а тот же, что у карточек.
    cfg = config_module.get_config().model_copy(update={"language": "nb"})
    catalogue = known_topics(db, cfg)

    assert set(catalogue) == {
        "dyr::kjæledyr",
        "mat-og-drikke::brød-og-bakverk",
        "egenskaper",
        "småord::mengde",
    }
    assert catalogue["egenskaper"] == "признак"


async def test_hints_reach_the_prompt(monkeypatch) -> None:
    """Без пояснения `småord::andre` не значит ничего и собирает что попало."""
    seen: dict[str, str] = {}

    def _fake_prompt(name: str, **kwargs: str) -> str:
        seen.update(kwargs)
        return "prompt"

    async def _fake_call(prompt: str, stage: str = "") -> list:
        return [{"id": 1, "topic": "egenskaper"}]

    monkeypatch.setattr(topics_module, "load_prompt", _fake_prompt)
    monkeypatch.setattr(topics_module, "call_json", _fake_call)

    stage = classify_topics_batch({"egenskaper": "признак, качество", "dyr::fugler": ""})
    card = _card("presis", "leksjon", card_id=1)
    await stage([card])

    assert "egenskaper — признак, качество" in seen["topics"]
    assert "dyr::fugler" in seen["topics"]
    assert "dyr::fugler —" not in seen["topics"]  # без пояснения — без тире
    assert card.topic == "egenskaper"


async def test_topic_outside_the_catalogue_is_refused(monkeypatch) -> None:
    """Принять выдуманную тему — завести в дереве тегов ветку из одной карточки."""
    monkeypatch.setattr(topics_module, "load_prompt", lambda name, **kw: "prompt")

    async def _fake_call(prompt: str, stage: str = "") -> list:
        return [{"id": 1, "topic": "мои-любимые-слова"}]

    monkeypatch.setattr(topics_module, "call_json", _fake_call)

    stage = classify_topics_batch({"egenskaper": ""})
    card = _card("presis", "leksjon", card_id=1)
    await stage([card])

    assert card.topic == "leksjon"


async def test_null_leaves_the_card_alone(monkeypatch) -> None:
    """Пустая тема лучше натянутой: её человек заметит, чужую — нет."""
    monkeypatch.setattr(topics_module, "load_prompt", lambda name, **kw: "prompt")

    async def _fake_call(prompt: str, stage: str = "") -> list:
        return [{"id": 1, "topic": None}]

    monkeypatch.setattr(topics_module, "call_json", _fake_call)

    stage = classify_topics_batch({"egenskaper": ""})
    card = _card("paraply", "leksjon", card_id=1)
    await stage([card])

    assert card.topic == "leksjon"


async def test_cards_that_already_have_a_topic_are_not_sent(monkeypatch) -> None:
    sent: list[str] = []

    def _fake_prompt(name: str, **kwargs: str) -> str:
        sent.append(kwargs["words_json"])
        return "prompt"

    async def _fake_call(prompt: str, stage: str = "") -> list:
        return []

    monkeypatch.setattr(topics_module, "load_prompt", _fake_prompt)
    monkeypatch.setattr(topics_module, "call_json", _fake_call)

    stage = classify_topics_batch({"egenskaper": ""})
    await stage([_card("hund", "dyr::kjæledyr", card_id=1), _card("presis", None, card_id=2)])

    assert "hund" not in sent[0]
    assert "presis" in sent[0]


def test_update_card_persists_the_topic(db: Database) -> None:
    """Разложенная тема должна пережить объект — иначе тег topic:: не появится."""
    card = _card("presis", "leksjon")
    db.insert_card(card)
    card.topic = "egenskaper"
    db.update_card(card)

    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.topic == "egenskaper"
    assert "topic::egenskaper" in saved.auto_tags()
