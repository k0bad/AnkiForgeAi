"""Интерактивный ревью pending-карточек в терминале.

Использует rich для таблиц и questionary для prompts.
Для каждой карточки показывает:
- основные поля
- найденные дубликаты с score
- варианты картинок (если ingest=topic и pos=noun)

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
from ..models import Card, Decision, Status
from ..pipeline import enrich_and_generate_media

console = Console()


def review_pending(db: Database, cfg: Config) -> None:
    """Главный цикл ревью.

    Остаётся синхронной (questionary.ask() внутри не дружит с уже запущенным
    event loop'ом) — enrichment принятых карточек прогоняется через свой
    собственный asyncio.run() в finally, поэтому саму функцию нельзя вызывать
    из кода, который уже крутится в event loop'е.
    """
    cards = db.get_by_status(Status.REVIEW) + db.get_by_status(Status.PENDING)
    if not cards:
        console.print("[green]Нечего ревьюить.[/]")
        return

    console.print(f"[cyan]Карточек на ревью: {len(cards)}[/]")
    accepted: list[Card] = []

    try:
        for card in cards:
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
                # Enrich/media запускаются батчем в конце сессии — см. _finalize_accepted
                # ниже (issue #11: раньше accept просто менял статус, ничего не enrich'я).
                accepted.append(card)
                db.log_action("review_accept", card_id=card.id, details={})
                console.print("[green]✓ approved (enrichment — в конце сессии)[/]")
            elif verb == "skip":
                db.update_status(card.id, Status.SKIPPED)
                db.log_action("review_skip", card_id=card.id, details={})
                console.print("[yellow]skipped[/]")
            elif verb == "suspend":
                db.update_status(card.id, Status.SUSPENDED)
                db.log_action("review_suspend", card_id=card.id, details={})
                console.print("[yellow]suspended[/]")
            elif verb == "edit":
                _edit_card(db, card)
    finally:
        if accepted:
            console.print(f"[cyan]Enrich + media для {len(accepted)} карточек...[/]")
            asyncio.run(_finalize_accepted(accepted, db, cfg))


async def _finalize_accepted(cards: list[Card], db: Database, cfg: Config) -> None:
    """Прогнать enrich_and_generate_media для карточек, принятых за сессию ревью,
    и сохранить результат — единственное место, которое реально пишет их в БД
    (accept-ветка выше только собирает карточки в список, статус в SQLite ещё
    не меняет — старый status=review/pending остаётся, пока не отработает enrichment)."""
    _, incomplete_ids = await enrich_and_generate_media(cards, db, cfg)
    for card in cards:
        if card.id in incomplete_ids:
            card.status = Status.REVIEW
            console.print(f"[yellow]⚠ {card.word}: enrichment неполный — возвращено в review[/]")
        else:
            card.status = Status.APPROVED
        db.update_card(card)
        db.log_action("review_finalized", card_id=card.id, details={"status": card.status.value})


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


def _edit_card(db: Database, card: Card) -> None:
    """Простой редактор ключевых текстовых полей."""
    fields = ["word", "translation", "example", "example_translation"]
    updates: dict[str, str] = {}
    for field in fields:
        current = getattr(card, field) or ""
        new_value = questionary.text(f"{field}:", default=current).ask()
        if new_value is None:
            return
        if new_value != current:
            updates[field] = new_value

    if not updates:
        console.print("[dim]Изменений нет.[/]")
        return

    with db.connect() as conn:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE cards SET {set_clause} WHERE id = ?",
            (*updates.values(), card.id),
        )
    db.log_action("review_edit", card_id=card.id, details=updates)
    console.print("[green]✓ обновлено[/]")


def _load_last_decision(db: Database, card_id: str) -> Decision | None:
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
