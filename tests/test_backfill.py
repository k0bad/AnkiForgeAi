"""Тесты на догон enrich-стадии по накопленной базе.

Покрыть то, чем догон отличается от стадии в пайплайне:
- карточки режутся на куски, каждый уходит своим вызовом
- результат куска пишется в БД сразу, а не в конце прогона
- провал одного куска не отменяет остальные
- карточки, для которых модель ничего не вернула, попадают в отчёт, а не в БД
- --dry-run не пишет ничего
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards.db import Database
from ankicards.enrich.backfill import backfill_stage
from ankicards.models import POS, Card, Status


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _cards(db: Database, count: int) -> list[Card]:
    made = []
    for n in range(count):
        card = Card(
            language="nb",
            word=f"ord{n}",
            translation=f"слово{n}",
            pos=POS.NOUN,
            status=Status.REVIEW,
        )
        db.insert_card(card)
        made.append(card)
    return made


def _has_pronunciation(card: Card) -> bool:
    return bool(card.pronunciation)


async def test_splits_into_chunks(db: Database) -> None:
    cards = _cards(db, 25)
    seen: list[int] = []

    async def stage(batch: list[Card]) -> list[Card]:
        seen.append(len(batch))
        for card in batch:
            card.pronunciation = "ор"
        return batch

    report = await backfill_stage(
        "pronunciation", stage, _has_pronunciation, cards, db, chunk_size=10
    )
    assert seen == [10, 10, 5]
    assert report.counts() == {"done": 25, "empty": 0, "failed": 0, "calls": 3}


async def test_each_chunk_is_saved_immediately(db: Database) -> None:
    """Обрыв на середине не должен отменять то, что уже сделано."""
    cards = _cards(db, 20)
    calls = 0

    async def stage(batch: list[Card]) -> list[Card]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("сервис недоступен")
        for card in batch:
            card.pronunciation = "ор"
        return batch

    report = await backfill_stage(
        "pronunciation", stage, _has_pronunciation, cards, db, chunk_size=10
    )
    saved = [c for c in db.get_by_status(Status.REVIEW) if c.pronunciation]
    assert len(saved) == 10
    assert report.counts() == {"done": 10, "empty": 0, "failed": 10, "calls": 2}


async def test_failed_chunk_does_not_stop_the_rest(db: Database) -> None:
    cards = _cards(db, 30)
    calls = 0

    async def stage(batch: list[Card]) -> list[Card]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("первый кусок не прошёл")
        for card in batch:
            card.pronunciation = "ор"
        return batch

    report = await backfill_stage(
        "pronunciation", stage, _has_pronunciation, cards, db, chunk_size=10
    )
    assert report.counts()["done"] == 20
    assert report.counts()["failed"] == 10
    assert report.calls == 3


async def test_cards_llm_skipped_are_reported_not_saved(db: Database) -> None:
    """Вызов прошёл, но часть id модель не вернула — это не «готово»."""
    cards = _cards(db, 4)

    async def stage(batch: list[Card]) -> list[Card]:
        for card in batch[:2]:
            card.pronunciation = "ор"
        return batch

    report = await backfill_stage(
        "pronunciation", stage, _has_pronunciation, cards, db, chunk_size=10
    )
    assert len(report.done) == 2
    assert len(report.empty) == 2
    assert len([c for c in db.get_by_status(Status.REVIEW) if c.pronunciation]) == 2


async def test_already_filled_cards_are_not_sent(db: Database) -> None:
    cards = _cards(db, 5)
    cards[0].pronunciation = "уже есть"
    cards[1].pronunciation = "и тут"
    sent: list[str] = []

    async def stage(batch: list[Card]) -> list[Card]:
        sent.extend(c.word for c in batch)
        for card in batch:
            card.pronunciation = "ор"
        return batch

    await backfill_stage("pronunciation", stage, _has_pronunciation, cards, db, chunk_size=10)
    assert sent == ["ord2", "ord3", "ord4"]


async def test_nothing_to_do_makes_no_calls(db: Database) -> None:
    cards = _cards(db, 3)
    for card in cards:
        card.pronunciation = "ор"

    async def stage(batch: list[Card]) -> list[Card]:
        raise AssertionError("вызова быть не должно")

    report = await backfill_stage(
        "pronunciation", stage, _has_pronunciation, cards, db, chunk_size=10
    )
    assert report.counts() == {"done": 0, "empty": 0, "failed": 0, "calls": 0}


async def test_dry_run_writes_nothing(db: Database) -> None:
    cards = _cards(db, 12)

    async def stage(batch: list[Card]) -> list[Card]:
        raise AssertionError("при --dry-run модель не зовётся")

    report = await backfill_stage(
        "pronunciation", stage, _has_pronunciation, cards, db, chunk_size=5, dry_run=True
    )
    assert report.counts()["done"] == 12
    assert report.calls == 3
    assert not [c for c in db.get_by_status(Status.REVIEW) if c.pronunciation]


async def test_status_is_not_touched(db: Database) -> None:
    """Догон дозаполняет поля; судьба карточки — по-прежнему за человеком."""
    cards = _cards(db, 3)

    async def stage(batch: list[Card]) -> list[Card]:
        for card in batch:
            card.pronunciation = "ор"
        return batch

    await backfill_stage("pronunciation", stage, _has_pronunciation, cards, db, chunk_size=10)
    assert len(db.get_by_status(Status.REVIEW)) == 3
    assert db.get_by_status(Status.APPROVED) == []


async def test_progress_reports_running_total(db: Database) -> None:
    cards = _cards(db, 25)
    ticks: list[tuple[int, int]] = []

    async def stage(batch: list[Card]) -> list[Card]:
        for card in batch:
            card.pronunciation = "ор"
        return batch

    await backfill_stage(
        "pronunciation",
        stage,
        _has_pronunciation,
        cards,
        db,
        chunk_size=10,
        progress=lambda done, total: ticks.append((done, total)),
    )
    assert ticks == [(10, 25), (20, 25), (25, 25)]


async def test_limit_counts_only_unfilled_cards(db: Database) -> None:
    """--limit должен двигать прогон вперёд, а не тратиться на уже готовые карточки."""
    cards = _cards(db, 10)
    for card in cards[:6]:
        card.pronunciation = "уже есть"
    sent: list[str] = []

    async def stage(batch: list[Card]) -> list[Card]:
        sent.extend(c.word for c in batch)
        for card in batch:
            card.pronunciation = "ор"
        return batch

    report = await backfill_stage(
        "pronunciation", stage, _has_pronunciation, cards, db, chunk_size=10, limit=3
    )
    assert sent == ["ord6", "ord7", "ord8"]
    assert report.counts()["done"] == 3
