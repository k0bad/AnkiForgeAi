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
from ..models import POS, Card, Status
from ..pipeline import _record, delete_card_record, enrich_and_generate_media

# pos тут потому, что определить часть речи автоматически удаётся не всегда
# (ingest bildetema берёт её из артикля, а у бесартиклевых слов — из LLM, который
# может и не ответить). Без правки такую карточку оставалось только удалить и
# импортировать заново. Значение проверяется по enum — см. _validated().
EDITABLE_FIELDS = ("word", "translation", "example", "example_translation", "pos")


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
    verified: bool = False,
) -> dict[int, str]:
    """Принять карточки: enrich + media, затем approved (или review, если
    enrichment оказался неполным). Возвращает {card_id: итоговый статус}.

    auto_pick_images=False — см. enrich_and_generate_media: используется
    review_pending(), которое само подбирает картинку с человеком после этого вызова.

    verified=True навешивает тег «человек посмотрел и одобрил» (Card.mark_verified).
    Флагом, а не по умолчанию: эту же функцию дёргают скрипты и AI-агенты, и
    отметка о личной проверке, поставленная автоматом, была бы просто неправдой.
    Тег ставится и тем карточкам, что вернулись в review из-за неполного
    enrichment: человек проверял слово и картинку, а не то, доехали ли формы.
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
        tag = card.mark_verified() if verified else None
        db.update_card(card)
        _record(db, "info", "review_finalized", card.id, status=card.status.value, verified=tag)
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


def _validated(field: str, value: str) -> str:
    """Привести значение к тому, что колонка реально принимает.

    Текстовые поля пишутся как есть, но `pos` — это enum: опечатка вроде
    `adjective` не помешает UPDATE, зато карточка перестанет читаться из БД
    (Card.model_validate упадёт на неизвестном значении). Ловим на входе.
    """
    if field != "pos":
        return value
    try:
        return POS(value.strip().lower()).value
    except ValueError as e:
        allowed = ", ".join(p.value for p in POS)
        raise ValueError(f"Неизвестная часть речи {value!r} (допустимы: {allowed})") from e


def edit_card(
    card_id: int, updates: dict[str, str], db: Database, language: str | None = None
) -> Card:
    card = _require_cards([card_id], db, language)[0]
    bad_fields = [k for k in updates if k not in EDITABLE_FIELDS]
    if bad_fields:
        raise ValueError(
            f"Нельзя редактировать поля: {', '.join(bad_fields)} "
            f"(доступны: {', '.join(EDITABLE_FIELDS)})"
        )
    if not updates:
        raise ValueError("Не указано ни одного поля для изменения")

    updates = {field: _validated(field, value) for field, value in updates.items()}

    # Смена части речи обнуляет формы: они были сгенерированы под прежний POS и
    # к новому не относятся (склонение существительного у глагола — мусор, а не
    # данные). Пустое поле честнее неверного, и следующий accept сгенерирует
    # правильные — enrich_grammar_batch смотрит как раз на card.pos.
    clear_forms = "pos" in updates and updates["pos"] != card.pos.value

    with db.connect() as conn:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        if clear_forms:
            set_clause += ", forms = NULL"
        conn.execute(f"UPDATE cards SET {set_clause} WHERE id = ?", (*updates.values(), card_id))
    _record(db, "info", "review_edit", card_id, **updates)

    updated = db.get_by_id(card_id)
    assert updated is not None
    return updated
