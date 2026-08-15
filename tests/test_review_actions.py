"""Тесты для review/actions.py: неинтерактивные review-действия должны и менять
статус в SQLite, и оставлять след в audit_log (issue #31 — review-действия
логируются через тот же pipeline._record(), что enrich/media-стадии, так что
структурированная трасса и персистентный аудит пишутся одним вызовом)."""

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


def _audit_actions(db: Database, card_id: str) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT action FROM audit_log WHERE card_id = ? ORDER BY id", (card_id,)
        ).fetchall()
    return [r["action"] for r in rows]


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
        actions.skip_cards(["missing-id"], db)
