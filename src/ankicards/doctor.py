"""Consistency check ("doctor"): сверяет включённые enrichment-тумблеры
конфига с фактически заполненными полями карточки.

Смотрит только на конечное состояние в SQLite, а не на логи (см. log.py /
run_id-трассировку из #8) — поэтому не может быть обмануто вызывающим
(человеком, скриптом или AI-агентом), который пропустил стадию pipeline, не
оставив следа. См. issue #9.

Проверяются только карточки со status in (approved, pushed) — pending/review
ещё не прошли pipeline целиком, пустые поля там ожидаемы и не являются багом.
"""

from __future__ import annotations

from .config import Config
from .enrich.grammar import _INFLECTED_POS
from .models import Card, Inconsistency, Status

# (тумблер cfg.enrich.<toggle>, поле card.<field>) — 1:1 соответствие полям Card,
# которые заполняет enrich/*.py при включённом тумблере. grammar сюда не входит:
# forms заполняются только для _INFLECTED_POS (см. enrich/grammar.py), у остальных
# частей речи forms=None — это корректное состояние, а не незавершённый enrichment.
_ENRICH_CHECKS: tuple[tuple[str, str], ...] = (
    ("examples", "example"),
    ("pronunciation", "pronunciation"),
)

_CHECKED_STATUSES = (Status.APPROVED, Status.PUSHED)


def find_inconsistencies(cards: list[Card], cfg: Config) -> list[Inconsistency]:
    """Найти карточки, где включённый тумблер enrichment не оставил следа в данных."""
    problems: list[Inconsistency] = []
    for card in cards:
        if card.status not in _CHECKED_STATUSES:
            continue
        problems.extend(_check_card(card, cfg))
    return problems


def _check_card(card: Card, cfg: Config) -> list[Inconsistency]:
    problems: list[Inconsistency] = []

    if cfg.enrich.grammar and card.pos in _INFLECTED_POS and not card.forms:
        problems.append(
            Inconsistency(
                card_id=card.id,
                word=card.word,
                check="enrich.grammar",
                reason=(
                    f"enrich.grammar=true, pos={card.pos.value!r} требует forms, "
                    "но card.forms пусто"
                ),
            )
        )

    for toggle_name, field_name in _ENRICH_CHECKS:
        if not getattr(cfg.enrich, toggle_name) or getattr(card, field_name):
            continue
        problems.append(
            Inconsistency(
                card_id=card.id,
                word=card.word,
                check=f"enrich.{toggle_name}",
                reason=f"enrich.{toggle_name}=true, но card.{field_name} пусто",
            )
        )

    if cfg.images.enabled and card.pos.value in cfg.images.only_for_pos and not card.image:
        problems.append(
            Inconsistency(
                card_id=card.id,
                word=card.word,
                check="images.enabled",
                reason=(
                    f"images.enabled=true и pos={card.pos.value!r} в images.only_for_pos, "
                    "но card.image пусто"
                ),
            )
        )

    if card.status == Status.PUSHED and card.anki_note_id is None:
        problems.append(
            Inconsistency(
                card_id=card.id,
                word=card.word,
                check="anki_note_id",
                reason="status=pushed, но anki_note_id пусто",
            )
        )

    return problems
