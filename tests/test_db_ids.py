"""Тесты на выделение и переиспользование числовых card.id (db.py).

Покрыть:
- нумерация начинается с 1 и идёт по порядку
- delete_card() освобождает номер для следующей вставки (дырка в середине)
- дырка у самого начала последовательности заполняется первой
- полностью опустевшая таблица снова начинает с 1
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards.db import Database
from ankicards.models import POS, Card


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


def _card(word: str) -> Card:
    return Card(word=word, pos=POS.NOUN, translation="перевод")


def test_first_card_gets_id_1(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)
    assert card.id == 1


def test_ids_are_sequential(db: Database) -> None:
    ids = []
    for word in ("hus", "bil", "katt"):
        card = _card(word)
        db.insert_card(card)
        ids.append(card.id)
    assert ids == [1, 2, 3]


def test_delete_in_middle_frees_id_for_next_insert(db: Database) -> None:
    a, b, c = _card("hus"), _card("bil"), _card("katt")
    for card in (a, b, c):
        db.insert_card(card)
    assert (a.id, b.id, c.id) == (1, 2, 3)

    db.delete_card(b.id)

    d = _card("fisk")
    db.insert_card(d)
    assert d.id == 2  # заняла освободившуюся дырку, а не уехала в конец (4)


def test_gap_at_start_is_filled_before_appending(db: Database) -> None:
    a, b = _card("hus"), _card("bil")
    db.insert_card(a)
    db.insert_card(b)
    db.delete_card(a.id)  # освобождаем id=1, id=2 остаётся занят

    c = _card("katt")
    db.insert_card(c)
    assert c.id == 1


def test_emptying_table_resets_next_id_to_1(db: Database) -> None:
    a = _card("hus")
    db.insert_card(a)
    db.delete_card(a.id)

    b = _card("bil")
    db.insert_card(b)
    assert b.id == 1


def test_delete_card_removes_row(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)
    db.delete_card(card.id)
    assert db.get_by_id(card.id) is None
