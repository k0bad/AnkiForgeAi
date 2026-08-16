"""CLI точка входа: `ankicards <command>`.

Команды:
    ingest url <URL>              Парсинг страницы
    ingest topic <TOPIC>          Генерация по теме через Claude
    review                        Интерактивный ревью pending (нужен TTY)
    review list                   Список pending/review-карточек (--json) — без TTY
    review accept <id...>         Принять карточки (enrich + media → approved) — без TTY
    review skip/suspend <id...>   Отклонить/отложить карточки — без TTY
    review resume <id...>         Вернуть suspended/skipped обратно в review — без TTY
    review edit <id> -f k=v       Отредактировать поля карточки — без TTY
    push                          Approved → Anki
    sync                          Обновить кэш заметок из Anki
    delete <id...>                Удалить карточки насовсем (Anki + БД), освободить номер
    stats                         Статистика по статусам
    init                          Инициализация БД и Note Type в Anki
    doctor                        Проверка согласованности карточек с enrich-конфигом
    migrate-ids                   Разовая миграция id: UUID -> последовательные числа
"""

from __future__ import annotations

import asyncio
import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from .anki.connect import AnkiConnect, AnkiConnectError
from .anki.notetype import (
    CARD_TEMPLATE_NAME,
    CSS,
    NoteTypeConfigError,
    back_template,
    field_names,
    front_template,
    validate_active_fields,
)
from .anki.notetype import _get_note_type_name as get_note_type_name
from .anki.sync import sync_anki_to_cache
from .config import Config, get_config
from .db import Database, IdMigrationRequiredError
from .doctor import find_inconsistencies
from .ingest.topic import ingest_by_topic
from .ingest.url import ingest_from_url
from .log import bound_run, get_logger
from .migrate_ids import migrate_ids as run_migrate_ids
from .migrate_ids import needs_migration
from .models import Status
from .pipeline import NoteTypeMissingError, push_approved, run_ingest_pipeline
from .review import actions as review_actions
from .review.interactive import review_pending

logger = get_logger(__name__)


def _force_utf8_stdio() -> None:
    """Windows console defaults to cp1251 — принудительно ставим UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()

app = typer.Typer(no_args_is_help=True, add_completion=False)
ingest_app = typer.Typer(no_args_is_help=True, help="Источники кандидатов")
app.add_typer(ingest_app, name="ingest")
review_app = typer.Typer(
    no_args_is_help=False,
    help="Ревью pending/review-карточек: без подкоманды — интерактивный TTY-режим; "
    "list/accept/skip/suspend/edit — неинтерактивные, для скриптов и AI-агентов",
)
app.add_typer(review_app, name="review")

console = Console()


def _open_db(cfg: Config) -> Database:
    """Открыть Database, но с понятной ошибкой вместо трейсбека, если БД ещё
    на старой (UUID) схеме id и ждёт `ankiforgeai migrate-ids`."""
    try:
        return Database(cfg.paths.db)
    except IdMigrationRequiredError as e:
        console.print(f"[red]✗[/] {e}")
        raise typer.Exit(code=1) from e


@app.command()
def setup() -> None:
    """Интерактивный мастер первичной настройки."""
    from .setup_wizard import run_setup

    run_setup()


@app.command()
def init() -> None:
    """Создать БД и Note Type в Anki."""
    cfg = get_config()

    try:
        validate_active_fields()
    except NoteTypeConfigError as e:
        console.print(f"[red]✗[/] Некорректная схема полей (anki.fields в language.yaml): {e}")
        raise typer.Exit(code=1) from e

    _open_db(cfg)
    console.print(f"[green]✓[/] БД создана: {cfg.paths.db}")

    async def _run() -> None:
        anki = AnkiConnect(cfg)
        try:
            await anki.ensure_deck()
        except Exception as e:
            logger.warning("anki.unreachable", error=str(e))
            console.print(
                f"[yellow]![/] Anki недоступен, Note Type не создан: {e}\n"
                "    Запусти `ankiforgeai init` ещё раз, когда Anki будет открыт "
                "(с addon AnkiConnect)."
            )
            return
        console.print(f"[green]✓[/] Deck готов: {cfg.anki.deck_name}")

        note_type = get_note_type_name()
        front, back = front_template(), back_template()
        if note_type in await anki.model_names():
            # Note Type уже существует — createModel тут не поможет (разовая операция),
            # поэтому дизайн (front_template/back_template/CSS) синхронизируем отдельными
            # вызовами, иначе правки в notetype.py никогда не долетают до Anki.
            await anki.update_model_templates(
                note_type, {CARD_TEMPLATE_NAME: {"Front": front, "Back": back}}
            )
            logger.info("notetype.templates_synced", note_type=note_type)
            await anki.update_model_styling(note_type, CSS)
            logger.info("notetype.styling_synced", note_type=note_type)
            console.print(f"[green]✓[/] Note Type обновлён (дизайн синхронизирован): {note_type}")
            return

        fields = field_names()
        await anki.create_model(
            model_name=note_type,
            fields=fields,
            css=CSS,
            card_templates=[{"Name": CARD_TEMPLATE_NAME, "Front": front, "Back": back}],
        )
        logger.info("notetype.created", note_type=note_type, field_count=len(fields))
        console.print(f"[green]✓[/] Note Type создан: {note_type}")

    with bound_run("init"):
        asyncio.run(_run())


@ingest_app.command("url")
def ingest_url_cmd(
    url: str,
    level: str = typer.Option("A2", help="CEFR уровень"),
    topic: str | None = typer.Option(None, help="Метка темы для тегов"),
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
) -> None:
    """Извлечь слова со страницы по URL."""
    cfg = get_config()
    db = _open_db(cfg)

    async def _run() -> dict:
        if not as_json:
            console.print(f"[cyan]→[/] Загружаю {url}")
        cards = await ingest_from_url(url, level=level, topic=topic)
        if not as_json:
            console.print(f"[cyan]→[/] LLM извлёк {len(cards)} слов")
        return await run_ingest_pipeline(cards, db=db, cfg=cfg)

    with bound_run("ingest_url"):
        stats = asyncio.run(_run())

    if as_json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        _print_stats(stats)


@ingest_app.command("topic")
def ingest_topic_cmd(
    topic: str,
    count: int = typer.Option(20, help="Сколько слов запросить"),
    level: str = typer.Option("A2", help="CEFR уровень"),
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
) -> None:
    """Сгенерировать слова по теме через Claude."""
    cfg = get_config()
    db = _open_db(cfg)

    async def _run() -> dict:
        if not as_json:
            console.print(f"[cyan]→[/] Генерирую {count} слов по теме '{topic}' ({level})")
        exclude = [w for _, w in db.all_words()]
        cards = await ingest_by_topic(topic=topic, count=count, level=level, exclude_words=exclude)
        if not as_json:
            console.print(f"[cyan]→[/] LLM вернул {len(cards)} кандидатов")
        return await run_ingest_pipeline(cards, db=db, cfg=cfg)

    with bound_run("ingest_topic"):
        stats = asyncio.run(_run())

    if as_json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        _print_stats(stats)


@review_app.callback(invoke_without_command=True)
def review(ctx: typer.Context) -> None:
    """Без подкоманды — интерактивный ревью pending-карточек (нужен TTY)."""
    if ctx.invoked_subcommand is not None:
        return
    cfg = get_config()
    db = _open_db(cfg)
    with bound_run("review"):
        review_pending(db, cfg)


@review_app.command("list")
def review_list_cmd(
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
) -> None:
    """Список карточек на ревью (review + pending) — без TTY."""
    cfg = get_config()
    db = _open_db(cfg)
    cards = db.get_by_status(Status.REVIEW) + db.get_by_status(Status.PENDING)

    if as_json:
        print(json.dumps([c.model_dump(mode="json") for c in cards], ensure_ascii=False, indent=2))
        return

    if not cards:
        console.print("[green]Нечего ревьюить.[/]")
        return

    table = Table(title=f"На ревью: {len(cards)}")
    table.add_column("id", style="dim")
    table.add_column("word", style="cyan")
    table.add_column("pos")
    table.add_column("translation")
    table.add_column("status")
    for c in cards:
        table.add_row(str(c.id), c.word, c.pos.value, c.translation, c.status.value)
    console.print(table)


_CARD_IDS_ARG = typer.Argument(..., help="ID карточек (см. review list)")
_FIELD_HELP = (
    f"поле=значение, можно повторять (доступны: {', '.join(review_actions.EDITABLE_FIELDS)})"
)
_FIELD_OPT = typer.Option(..., "--field", "-f", help=_FIELD_HELP)


@review_app.command("accept")
def review_accept_cmd(
    card_ids: list[int] = _CARD_IDS_ARG,
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
) -> None:
    """Принять карточки без TTY: enrich + media, затем approved."""
    cfg = get_config()
    db = _open_db(cfg)

    with bound_run("review_accept"):
        try:
            results = asyncio.run(review_actions.accept_cards(card_ids, db, cfg))
        except ValueError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for card_id, status in results.items():
        icon = "[green]✓[/]" if status == Status.APPROVED.value else "[yellow]⚠[/]"
        console.print(f"{icon} {card_id} → {status}")


@review_app.command("skip")
def review_skip_cmd(card_ids: list[int] = _CARD_IDS_ARG) -> None:
    """Пометить карточки как skipped без TTY."""
    cfg = get_config()
    db = _open_db(cfg)
    with bound_run("review_skip"):
        try:
            review_actions.skip_cards(card_ids, db)
        except ValueError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e
    for card_id in card_ids:
        console.print(f"[yellow]skipped[/] {card_id}")


@review_app.command("suspend")
def review_suspend_cmd(card_ids: list[int] = _CARD_IDS_ARG) -> None:
    """Пометить карточки как suspended без TTY."""
    cfg = get_config()
    db = _open_db(cfg)
    with bound_run("review_suspend"):
        try:
            review_actions.suspend_cards(card_ids, db)
        except ValueError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e
    for card_id in card_ids:
        console.print(f"[yellow]suspended[/] {card_id}")


@review_app.command("resume")
def review_resume_cmd(card_ids: list[int] = _CARD_IDS_ARG) -> None:
    """Вернуть suspended/skipped карточки обратно в review без TTY."""
    cfg = get_config()
    db = _open_db(cfg)
    with bound_run("review_resume"):
        try:
            review_actions.resume_cards(card_ids, db)
        except ValueError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e
    for card_id in card_ids:
        console.print(f"[green]resumed[/] {card_id}")


@review_app.command("edit")
def review_edit_cmd(
    card_id: int,
    field: list[str] = _FIELD_OPT,
) -> None:
    """Отредактировать текстовые поля карточки без TTY."""
    updates: dict[str, str] = {}
    for item in field:
        if "=" not in item:
            console.print(f"[red]✗[/] Некорректный --field '{item}', ожидается key=value")
            raise typer.Exit(code=1)
        key, value = item.split("=", 1)
        updates[key] = value

    cfg = get_config()
    db = _open_db(cfg)
    with bound_run("review_edit"):
        try:
            updated = review_actions.edit_card(card_id, updates, db)
        except ValueError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e
    console.print(f"[green]✓[/] {updated.word} обновлено: {', '.join(updates)}")


@app.command()
def push(as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод")) -> None:
    """Отправить approved-карточки в Anki."""
    cfg = get_config()
    db = _open_db(cfg)
    anki = AnkiConnect(cfg)

    async def _run() -> int:
        return await push_approved(db, anki, cfg)

    with bound_run("push"):
        try:
            count = asyncio.run(_run())
        except (NoteTypeMissingError, AnkiConnectError) as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e

    if as_json:
        print(json.dumps({"pushed": count}, ensure_ascii=False))
    else:
        console.print(f"[green]✓[/] Отправлено в Anki: {count}")


@app.command()
def sync(as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод")) -> None:
    """Обновить локальный кэш из Anki."""
    cfg = get_config()
    db = _open_db(cfg)
    anki = AnkiConnect(cfg)

    async def _run() -> int:
        return await sync_anki_to_cache(db, anki, cfg)

    with bound_run("sync"):
        try:
            count = asyncio.run(_run())
        except AnkiConnectError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e

    if as_json:
        print(json.dumps({"synced": count}, ensure_ascii=False))
    else:
        console.print(f"[green]✓[/] Синхронизировано заметок: {count}")


@app.command()
def delete(
    card_ids: list[int] = _CARD_IDS_ARG,
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
) -> None:
    """Удалить карточки насовсем и освободить их номер для новых карточек.

    Если карточка уже запушена в Anki, вместе с заметкой стирается и её
    история повторений/интервалов там — это необратимо.
    """
    cfg = get_config()
    db = _open_db(cfg)
    anki = AnkiConnect(cfg)

    if not as_json:
        console.print(
            "[yellow]![/] Удаление уже запушенной карточки стирает её историю "
            "повторений в Anki — это необратимо."
        )

    async def _run() -> list[int]:
        return await review_actions.delete_cards(card_ids, db, anki)

    with bound_run("delete"):
        try:
            deleted = asyncio.run(_run())
        except (ValueError, AnkiConnectError) as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e

    if as_json:
        print(json.dumps({"deleted": deleted}, ensure_ascii=False))
        return
    for card_id in deleted:
        console.print(f"[red]deleted[/] {card_id}")


@app.command()
def stats(as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод")) -> None:
    """Статистика по статусам."""
    cfg = get_config()
    db = _open_db(cfg)

    counts = {status.value: len(db.get_by_status(status)) for status in Status}
    anki_cached = len(db.all_anki_words())

    if as_json:
        print(json.dumps({**counts, "anki_cache": anki_cached}, ensure_ascii=False, indent=2))
        return

    table = Table(title="AnkiForgeAI — статистика")
    table.add_column("Статус", style="cyan")
    table.add_column("Количество", justify="right")

    for status_value, count in counts.items():
        table.add_row(status_value, str(count))
    table.add_row("[dim]anki_cache[/]", str(anki_cached))

    console.print(table)


@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
) -> None:
    """Сверить approved/pushed карточки с включёнными enrich/images тумблерами (issue #9)."""
    cfg = get_config()
    db = _open_db(cfg)

    cards = db.get_by_status(Status.APPROVED) + db.get_by_status(Status.PUSHED)
    problems = find_inconsistencies(cards, cfg)

    if as_json:
        print(json.dumps([p.model_dump() for p in problems], ensure_ascii=False, indent=2))
    elif not problems:
        console.print("[green]✓[/] Несоответствий не найдено")
    else:
        table = Table(title=f"doctor — найдено несоответствий: {len(problems)}")
        table.add_column("Card ID", style="dim")
        table.add_column("Слово", style="cyan")
        table.add_column("Проверка")
        table.add_column("Причина")
        for p in problems:
            table.add_row(str(p.card_id), p.word, p.check, p.reason)
        console.print(table)

    if problems:
        raise typer.Exit(code=1)


@app.command(name="migrate-ids")
def migrate_ids_cmd(
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
) -> None:
    """Разовая миграция: card.id UUID -> последовательные целые числа (1, 2, 3, ...).

    Нужен запущенный Anki с AnkiConnect — обновляет скрытое поле ID на уже
    запушенных заметках. Перед изменениями делает резервную копию БД. Можно
    перезапускать сколько угодно раз: если уже мигрировано — ничего не делает.
    """
    cfg = get_config()

    if not needs_migration(cfg.paths.db):
        if as_json:
            print(json.dumps({"already_current": True}, ensure_ascii=False))
        else:
            console.print("[green]✓[/] Уже мигрировано — id целочисленные.")
        return

    if not as_json:
        console.print(
            f"[yellow]![/] Перенумеровываю карточки в {cfg.paths.db} и обновляю "
            "скрытое поле ID на уже запушенных заметках в Anki."
        )

    anki = AnkiConnect(cfg)
    with bound_run("migrate_ids"):
        result = asyncio.run(run_migrate_ids(cfg.paths.db, anki))

    if as_json:
        print(
            json.dumps(
                {
                    "backup_path": str(result.backup_path) if result.backup_path else None,
                    "cards_migrated": result.cards_migrated,
                    "audit_rows_remapped": result.audit_rows_remapped,
                    "anki_updated": result.anki_updated,
                    "anki_failed": result.anki_failed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    console.print(f"[green]✓[/] Перенумеровано карточек: {result.cards_migrated}")
    console.print(f"[green]✓[/] Обновлено записей audit_log: {result.audit_rows_remapped}")
    console.print(f"[green]✓[/] Обновлено заметок в Anki: {result.anki_updated}")
    if result.anki_failed:
        console.print(f"[red]✗[/] Не удалось обновить в Anki: {len(result.anki_failed)}")
        for new_id, error in result.anki_failed:
            console.print(f"    id={new_id}: {error}")
    console.print(f"[dim]Резервная копия: {result.backup_path}[/]")


def _print_stats(stats: dict) -> None:
    table = Table(title="Результат pipeline")
    table.add_column("Метрика", style="cyan")
    table.add_column("Значение", justify="right")
    for key, value in stats.items():
        table.add_row(key, str(value))
    console.print(table)


if __name__ == "__main__":
    app()
