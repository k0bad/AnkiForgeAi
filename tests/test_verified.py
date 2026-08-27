"""Тег `verified::<дата>` — отметка «эту карточку человек посмотрел глазами».

Ставится только явным действием человека, переживает запись в БД и уходит в Anki
вместе с остальными auto_tags(). Скрипты и агенты зовут ту же accept_cards(), и
для них отметка была бы ложью — поэтому она флаг, а не поведение по умолчанию.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards.config import (
    AnkiConfig,
    Config,
    DedupeConfig,
    EnrichConfig,
    ImagesConfig,
    IngestConfig,
    LLMConfig,
    LoggingConfig,
    PathsConfig,
    ReviewConfig,
    TagsConfig,
    TTSConfig,
)
from ankicards.db import Database
from ankicards.models import POS, Card, Status


def _config(tmp_path: Path) -> Config:
    return Config(
        language="nb",
        paths=PathsConfig(
            db=tmp_path / "data" / "cards.db",
            logs_dir=tmp_path / "logs",
            audio_dir=tmp_path / "audio",
            images_dir=tmp_path / "images",
            prompts_dir=tmp_path / "prompts",
        ),
        anki=AnkiConfig(),
        dedupe=DedupeConfig(ai_adjudication=False),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


def test_mark_verified_tag_carries_the_date_and_reaches_anki() -> None:
    from datetime import date

    card = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка", topic="klær")

    tag = card.mark_verified(day=date(2026, 8, 21))

    assert tag == "verified::2026-08-21"
    assert card.is_verified()
    # auto_tags() — то, что уходит в Anki при push; тег обязан быть там.
    assert "verified::2026-08-21" in card.auto_tags()


def test_mark_verified_is_idempotent_and_keeps_the_first_date() -> None:
    """Повторный accept не плодит теги и не переписывает дату первой проверки."""
    from datetime import date

    card = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка")
    card.mark_verified(day=date(2026, 8, 21))
    card.mark_verified(day=date(2026, 9, 1))

    assert card.tags == ["verified::2026-08-21"]


def test_card_is_not_verified_until_a_human_says_so() -> None:
    """Импортированная карточка сама по себе непроверенная — иначе тег ничего не значит."""
    card = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка", topic="klær")

    assert not card.is_verified()
    assert not any(t.startswith("verified") for t in card.auto_tags())


async def test_accept_with_verified_persists_the_tag_through_update_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Регрессия: update_card не писал колонку tags, и тег терялся сразу после accept."""
    from ankicards.review import actions

    cfg = _config(tmp_path)
    db = Database(tmp_path / "cards.db")
    card = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка")
    db.insert_card(card)

    async def _noop(cards, db_, cfg_, **kw):  # type: ignore[no-untyped-def]
        return {}, set()

    monkeypatch.setattr(actions, "enrich_and_generate_media", _noop)

    await actions.accept_cards([1], db, cfg, verified=True)

    reloaded = db.get_by_id(1)
    assert reloaded is not None
    assert reloaded.status is Status.APPROVED
    assert reloaded.is_verified(), "тег не пережил перезагрузку из БД"


async def test_accept_without_verified_leaves_no_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Скрипты и агенты зовут ту же функцию — отметка о личной проверке от них была бы ложью."""
    from ankicards.review import actions

    cfg = _config(tmp_path)
    db = Database(tmp_path / "cards.db")
    db.insert_card(Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка"))

    async def _noop(cards, db_, cfg_, **kw):  # type: ignore[no-untyped-def]
        return {}, set()

    monkeypatch.setattr(actions, "enrich_and_generate_media", _noop)

    await actions.accept_cards([1], db, cfg)

    reloaded = db.get_by_id(1)
    assert reloaded is not None
    assert reloaded.tags == []
