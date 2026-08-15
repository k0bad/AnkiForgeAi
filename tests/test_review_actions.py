"""Тесты для review.actions.delete_cards: удалить карточку насовсем (Anki +
локальная БД) и освободить её номер для переиспользования."""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards.db import Database
from ankicards.models import POS, Card, Status
from ankicards.review import actions


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


def _card(word: str) -> Card:
    return Card(word=word, pos=POS.NOUN, translation="перевод")


def _mark_pushed(db: Database, card: Card, note_id: int) -> Card:
    with db.connect() as conn:
        conn.execute(
            "UPDATE cards SET status = ?, anki_note_id = ? WHERE id = ?",
            (Status.PUSHED.value, note_id, card.id),
        )
    reloaded = db.get_by_id(card.id)
    assert reloaded is not None
    return reloaded


class _FakeAnki:
    def __init__(self) -> None:
        self.deleted: list[list[int]] = []

    async def delete_notes(self, note_ids: list[int]) -> None:
        self.deleted.append(note_ids)


async def test_delete_cards_removes_pushed_note_and_row(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)
    card = _mark_pushed(db, card, note_id=12345)

    anki = _FakeAnki()
    deleted = await actions.delete_cards([card.id], db, anki)  # type: ignore[arg-type]

    assert deleted == [card.id]
    assert anki.deleted == [[12345]]
    assert db.get_by_id(card.id) is None


async def test_delete_cards_skips_anki_call_for_unpushed_card(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)  # ещё не запушена — anki_note_id пуст

    anki = _FakeAnki()
    deleted = await actions.delete_cards([card.id], db, anki)  # type: ignore[arg-type]

    assert deleted == [card.id]
    assert anki.deleted == []  # deleteNotes не звался — нечего удалять в Anki
    assert db.get_by_id(card.id) is None


async def test_delete_cards_frees_id_for_next_insert(db: Database) -> None:
    a, b, c = _card("hus"), _card("bil"), _card("katt")
    for card in (a, b, c):
        db.insert_card(card)

    await actions.delete_cards([b.id], db, _FakeAnki())  # type: ignore[arg-type]

    new_card = _card("fisk")
    db.insert_card(new_card)
    assert new_card.id == b.id


async def test_delete_cards_raises_for_unknown_id(db: Database) -> None:
    with pytest.raises(ValueError, match="не найдены"):
        await actions.delete_cards([999], db, _FakeAnki())  # type: ignore[arg-type]
