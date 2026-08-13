"""Неинтерактивные review-действия — accept/skip/suspend/edit без TTY.

Та же логика, что и в review/interactive.py (questionary-цикл), но вызываемая
напрямую по card_id — чтобы CLI и внешние вызывающие (например, AI-агент)
могли управлять review без интерактивного терминала. interactive.py переиспользует
эти же функции, а не дублирует запись в БД.
"""

from __future__ import annotations

from ..config import Config
from ..db import Database
from ..models import Card, Status
from ..pipeline import enrich_and_generate_media

EDITABLE_FIELDS = ("word", "translation", "example", "example_translation")


def _require_cards(card_ids: list[str], db: Database) -> list[Card]:
    cards: list[Card] = []
    missing: list[str] = []
    for card_id in card_ids:
        card = db.get_by_id(card_id)
        if card is None:
            missing.append(card_id)
        else:
            cards.append(card)
    if missing:
        raise ValueError(f"Карточки не найдены: {', '.join(missing)}")
    return cards


async def accept_cards(card_ids: list[str], db: Database, cfg: Config) -> dict[str, str]:
    """Принять карточки: enrich + media, затем approved (или review, если
    enrichment оказался неполным). Возвращает {card_id: итоговый статус}."""
    cards = _require_cards(card_ids, db)
    if not cards:
        return {}

    _, incomplete_ids = await enrich_and_generate_media(cards, db, cfg)
    results: dict[str, str] = {}
    for card in cards:
        card.status = Status.REVIEW if card.id in incomplete_ids else Status.APPROVED
        db.update_card(card)
        db.log_action("review_finalized", card_id=card.id, details={"status": card.status.value})
        results[card.id] = card.status.value
    return results


def _set_status(card_ids: list[str], status: Status, action: str, db: Database) -> list[str]:
    _require_cards(card_ids, db)
    for card_id in card_ids:
        db.update_status(card_id, status)
        db.log_action(action, card_id=card_id, details={})
    return card_ids


def skip_cards(card_ids: list[str], db: Database) -> list[str]:
    return _set_status(card_ids, Status.SKIPPED, "review_skip", db)


def suspend_cards(card_ids: list[str], db: Database) -> list[str]:
    return _set_status(card_ids, Status.SUSPENDED, "review_suspend", db)


def resume_cards(card_ids: list[str], db: Database) -> list[str]:
    """Вернуть suspended/skipped карточки в review (передумали)."""
    return _set_status(card_ids, Status.REVIEW, "review_resume", db)


def edit_card(card_id: str, updates: dict[str, str], db: Database) -> Card:
    _require_cards([card_id], db)
    bad_fields = [k for k in updates if k not in EDITABLE_FIELDS]
    if bad_fields:
        raise ValueError(
            f"Нельзя редактировать поля: {', '.join(bad_fields)} "
            f"(доступны: {', '.join(EDITABLE_FIELDS)})"
        )
    if not updates:
        raise ValueError("Не указано ни одного поля для изменения")

    with db.connect() as conn:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE cards SET {set_clause} WHERE id = ?", (*updates.values(), card_id))
    db.log_action("review_edit", card_id=card_id, details=updates)

    updated = db.get_by_id(card_id)
    assert updated is not None
    return updated
