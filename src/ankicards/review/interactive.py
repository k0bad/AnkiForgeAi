"""Интерактивный ревью pending-карточек в терминале.

Использует rich для таблиц и questionary для prompts.
Для каждой карточки показывает основные поля и найденные дубликаты с score.
После accept, по итогам batch enrichment у принятых карточек — кандидатов на
картинку (если включено и pos подходит), человек выбирает нужную или пропускает.

Действия: accept / merge / edit / skip / suspend / quit
"""

from __future__ import annotations

import asyncio
import json

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..config import Config
from ..db import Database
from ..media.images import find_candidates, save_image
from ..models import Card, Decision, Status
from ..pipeline import _record
from . import actions

console = Console()


def review_pending(db: Database, cfg: Config) -> None:
    """Главный цикл ревью.

    Остаётся синхронной (questionary.ask() внутри не дружит с уже запущенным
    event loop'ом) — enrichment принятых карточек прогоняется через свой
    собственный asyncio.run() в finally, поэтому саму функцию нельзя вызывать
    из кода, который уже крутится в event loop'е.
    """
    cards = db.get_by_status(Status.REVIEW, cfg.language) + db.get_by_status(
        Status.PENDING, cfg.language
    )
    if not cards:
        console.print("[green]Нечего ревьюить.[/]")
        return

    console.print(f"[cyan]Карточек на ревью: {len(cards)}[/]")
    accepted: list[Card] = []

    try:
        for card in cards:
            assert card.id is not None  # уже в БД (get_by_status), id всегда назначен
            decision = _load_last_decision(db, card.id)
            _show_card(card, decision)

            action = questionary.select(
                "Действие:",
                choices=[
                    "accept   — одобрить (approved)",
                    "skip     — пропустить (skipped)",
                    "suspend  — отложить (suspended)",
                    "edit     — редактировать поля",
                    "quit     — выйти из ревью",
                ],
            ).ask()

            if action is None or action.startswith("quit"):
                console.print("[yellow]Выход.[/]")
                return

            verb = action.split()[0]
            if verb == "accept":
                # Enrich/media запускаются батчем в конце сессии — см. actions.accept_cards
                # ниже (issue #11: раньше accept просто менял статус, ничего не enrich'я).
                accepted.append(card)
                _record(db, "info", "review_accept", card.id)
                console.print("[green]✓ approved (enrichment — в конце сессии)[/]")
            elif verb == "skip":
                actions.skip_cards([card.id], db, language=cfg.language)
                console.print("[yellow]skipped[/]")
            elif verb == "suspend":
                actions.suspend_cards([card.id], db, language=cfg.language)
                console.print("[yellow]suspended[/]")
            elif verb == "edit":
                _edit_card(db, card, language=cfg.language)
    finally:
        if accepted:
            console.print(f"[cyan]Enrich + media для {len(accepted)} карточек...[/]")
            asyncio.run(_finalize_accepted(accepted, db, cfg))


async def _finalize_accepted(cards: list[Card], db: Database, cfg: Config) -> None:
    """Прогнать enrich + media для карточек, принятых за сессию ревью, и сохранить
    результат (accept-ветка выше только собирает карточки в список, статус в
    SQLite ещё не меняет — старый status=review/pending остаётся, пока не
    отработает actions.accept_cards).

    Картинки автовыбором не трогаются здесь (auto_pick_images=False) — после
    enrichment у карточки уже есть image_query, и человек сам выбирает из
    кандидатов через _pick_image (issue #36), вместо молчаливой подстановки
    первого результата поиска.
    """
    ids: list[int] = []
    for card in cards:
        assert card.id is not None  # уже в БД, id всегда назначен
        ids.append(card.id)

    results = await actions.accept_cards(
        ids, db, cfg, auto_pick_images=False, language=cfg.language
    )
    for card in cards:
        assert card.id is not None
        card_id = card.id
        card.status = Status(results[card_id])
        if card.status == Status.REVIEW:
            console.print(f"[yellow]⚠ {card.word}: enrichment неполный — возвращено в review[/]")

        fresh = db.get_by_id(card_id)  # свежие поля (image_query и т.д.) после enrichment
        assert fresh is not None
        await _pick_image(fresh, cfg, db)


async def _pick_image(card: Card, cfg: Config, db: Database) -> None:
    """Найти кандидатов на картинку и дать человеку выбрать/пропустить (issue #36) —
    раньше единственный результат подставлялся автоматически, без права увидеть
    альтернативы. Молча выходит, если картинки выключены/не для этой части речи/
    поиск ничего не дал (find_candidates уже залогировал пустой поиск сам)."""
    candidates = await find_candidates(card, cfg)
    if not candidates:
        return

    _show_image_candidates(card, candidates)
    choice = questionary.select(
        "Картинка:",
        choices=[str(i) for i in range(1, len(candidates) + 1)] + ["skip — без картинки"],
        default="1",
    ).ask()
    if choice is None or choice.startswith("skip"):
        return

    assert card.id is not None  # уже в БД (fresh из db.get_by_id)
    index = int(choice) - 1
    await save_image(card, candidates[index], cfg)
    db.update_card(card)
    _record(db, "info", "image_picked", card.id, index=index, url=candidates[index]["url"])


def _show_image_candidates(card: Card, candidates: list[dict[str, str]]) -> None:
    table = Table(title=f"Картинки для «{card.word}»", show_header=True)
    table.add_column("#", justify="right")
    table.add_column("автор")
    table.add_column("ссылка")
    for i, c in enumerate(candidates, start=1):
        table.add_row(str(i), c.get("author") or "-", c.get("html") or c.get("url") or "-")
    console.print(table)


def _show_card(card: Card, decision: Decision | None) -> None:
    """Красиво отрисовать карточку + дубликаты."""
    header = f"[bold]{card.word}[/] ({card.pos.value})  →  {card.translation}"
    level_str = card.level.value if card.level else "-"
    body_lines = [
        f"[dim]id:[/] {card.id}",
        (
            f"[dim]status:[/] {card.status.value}   "
            f"[dim]level:[/] {level_str}   "
            f"[dim]topic:[/] {card.topic or '-'}"
        ),
    ]
    if card.example:
        body_lines.append(f"[dim]example:[/] {card.example}")
        if card.example_translation:
            body_lines.append(f"[dim]перевод:[/] {card.example_translation}")
    if card.forms:
        body_lines.append(f"[dim]forms:[/] {card.forms}")

    console.print(Panel("\n".join(body_lines), title=header, border_style="cyan"))

    if decision and decision.matches:
        table = Table(title=f"Дубликаты ({decision.reason or ''})", show_header=True)
        table.add_column("score", justify="right")
        table.add_column("word")
        table.add_column("field")
        table.add_column("id", style="dim")
        for m in decision.matches:
            table.add_row(f"{m.score:.1f}", m.existing_word, m.matched_field, m.existing_card_id)
        console.print(table)


def _edit_card(db: Database, card: Card, language: str) -> None:
    """Простой редактор ключевых текстовых полей."""
    assert card.id is not None  # уже в БД, id всегда назначен
    updates: dict[str, str] = {}
    for field in actions.EDITABLE_FIELDS:
        current = getattr(card, field) or ""
        new_value = questionary.text(f"{field}:", default=current).ask()
        if new_value is None:
            return
        if new_value != current:
            updates[field] = new_value

    if not updates:
        console.print("[dim]Изменений нет.[/]")
        return

    actions.edit_card(card.id, updates, db, language=language)
    console.print("[green]✓ обновлено[/]")


def _load_last_decision(db: Database, card_id: int) -> Decision | None:
    """Достать последнее решение dedupe для карточки из audit_log."""
    with db.connect() as conn:
        row = conn.execute(
            """SELECT details FROM audit_log
               WHERE card_id = ? AND action = 'review_needed'
               ORDER BY id DESC LIMIT 1""",
            (card_id,),
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["details"])
    except (json.JSONDecodeError, TypeError):
        return None
    matches = payload.get("matches", [])
    reason = payload.get("reason")
    return Decision(decision="review", matches=matches, reason=reason)
