"""Тесты для review/actions.py: неинтерактивные review-действия должны и менять
статус в SQLite, и оставлять след в audit_log (issue #31 — review-действия
логируются через тот же pipeline._record(), что enrich/media-стадии, так что
структурированная трасса и персистентный аудит пишутся одним вызовом), а
delete_cards должен удалять карточку насовсем (Anki + локальная БД) и
освобождать её номер для переиспользования."""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards.db import Database
from ankicards.models import POS, Card, Status
from ankicards.review import actions


def _card(word: str, status: Status = Status.REVIEW) -> Card:
    return Card(word=word, pos=POS.NOUN, translation="дом", status=status)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


def _audit_actions(db: Database, card_id: int) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT action FROM audit_log WHERE card_id = ? ORDER BY id", (card_id,)
        ).fetchall()
    return [r["action"] for r in rows]


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


def test_skip_cards_updates_status_and_logs_action(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)

    actions.skip_cards([card.id], db)

    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.SKIPPED
    assert "review_skip" in _audit_actions(db, card.id)


def test_suspend_cards_updates_status_and_logs_action(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)

    actions.suspend_cards([card.id], db)

    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.SUSPENDED
    assert "review_suspend" in _audit_actions(db, card.id)


def test_resume_cards_updates_status_and_logs_action(db: Database) -> None:
    card = _card("hus", status=Status.SUSPENDED)
    db.insert_card(card)

    actions.resume_cards([card.id], db)

    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.REVIEW
    assert "review_resume" in _audit_actions(db, card.id)


def test_edit_card_updates_fields_and_logs_action(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)

    updated = actions.edit_card(card.id, {"translation": "домик"}, db)

    assert updated.translation == "домик"
    assert "review_edit" in _audit_actions(db, card.id)


def test_set_status_raises_on_missing_card(db: Database) -> None:
    with pytest.raises(ValueError, match="не найдены"):
        actions.skip_cards([999], db)


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
