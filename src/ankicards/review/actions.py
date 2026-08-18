"""Неинтерактивные review-действия — accept/skip/suspend/edit без TTY.

Та же логика, что и в review/interactive.py (questionary-цикл), но вызываемая
напрямую по card_id — чтобы CLI и внешние вызывающие (например, AI-агент)
могли управлять review без интерактивного терминала. interactive.py переиспользует
эти же функции, а не дублирует запись в БД.
"""

from __future__ import annotations

from ..anki.connect import AnkiConnect
from ..config import Config
from ..db import Database
from ..models import Card, Status
from ..pipeline import _record, delete_card_record, enrich_and_generate_media

EDITABLE_FIELDS = ("word", "translation", "example", "example_translation")


def _require_cards(card_ids: list[int], db: Database, language: str | None = None) -> list[Card]:
    cards: list[Card] = []
    missing: list[int] = []
    for card_id in card_ids:
        card = db.get_by_id(card_id)
        if card is None:
            missing.append(card_id)
        else:
            cards.append(card)
    if missing:
        raise ValueError(f"Карточки не найдены: {', '.join(str(m) for m in missing)}")

    # language=... (issue #63): --language на CLI защищает от случайного действия
    # над карточкой другого языка (устаревший список, опечатка в id, забытый флаг) —
    # жёсткая ошибка вместо тихого пропуска, по решению пользователя при планировании.
    if language is not None:
        mismatched = [c for c in cards if c.language != language]
        if mismatched:
            details = ", ".join(f"{c.id} ({c.language})" for c in mismatched)
            raise ValueError(f"Карточки другого языка (ожидался {language!r}): {details}")

    return cards


async def accept_cards(
    card_ids: list[int],
    db: Database,
    cfg: Config,
    auto_pick_images: bool = True,
    language: str | None = None,
) -> dict[int, str]:
    """Принять карточки: enrich + media, затем approved (или review, если
    enrichment оказался неполным). Возвращает {card_id: итоговый статус}.

    auto_pick_images=False — см. enrich_and_generate_media: используется
    review_pending(), которое само подбирает картинку с человеком после этого вызова.
    """
    cards = _require_cards(card_ids, db, language)
    if not cards:
        return {}

    _, incomplete_ids = await enrich_and_generate_media(
        cards, db, cfg, auto_pick_images=auto_pick_images
    )
    results: dict[int, str] = {}
    for card in cards:
        assert card.id is not None
        card.status = Status.REVIEW if card.id in incomplete_ids else Status.APPROVED
        db.update_card(card)
        _record(db, "info", "review_finalized", card.id, status=card.status.value)
        results[card.id] = card.status.value
    return results


async def delete_cards(
    card_ids: list[int], db: Database, anki: AnkiConnect, language: str | None = None
) -> list[int]:
    """Удалить карточки насовсем: из Anki (если уже запушены — вместе с их
    историей повторений/интервалов там!), из локальной БД, освободить номер
    для переиспользования следующей новой карточкой.

    Anki-вызов идёт первым — если deleteNotes упадёт, локальная запись о
    карточке останется нетронутой, а не будет молча стёрта раньше времени.
    """
    cards = _require_cards(card_ids, db, language)
    deleted: list[int] = []
    for card in cards:
        assert card.id is not None
        if card.anki_note_id is not None:
            await anki.delete_notes([card.anki_note_id])
        delete_card_record(db, card, action="delete")
        deleted.append(card.id)
    return deleted


def _set_status(
    card_ids: list[int],
    status: Status,
    action: str,
    db: Database,
    language: str | None = None,
) -> list[int]:
    _require_cards(card_ids, db, language)
    for card_id in card_ids:
        db.update_status(card_id, status)
        _record(db, "info", action, card_id)
    return card_ids


def skip_cards(card_ids: list[int], db: Database, language: str | None = None) -> list[int]:
    return _set_status(card_ids, Status.SKIPPED, "review_skip", db, language)


def suspend_cards(card_ids: list[int], db: Database, language: str | None = None) -> list[int]:
    return _set_status(card_ids, Status.SUSPENDED, "review_suspend", db, language)


def resume_cards(card_ids: list[int], db: Database, language: str | None = None) -> list[int]:
    """Вернуть suspended/skipped карточки в review (передумали)."""
    return _set_status(card_ids, Status.REVIEW, "review_resume", db, language)


def edit_card(
    card_id: int, updates: dict[str, str], db: Database, language: str | None = None
) -> Card:
    _require_cards([card_id], db, language)
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
    _record(db, "info", "review_edit", card_id, **updates)

    updated = db.get_by_id(card_id)
    assert updated is not None
    return updated
