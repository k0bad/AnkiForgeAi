"""CLI точка входа: `ankicards <command>`.

Команды:
    ingest url <URL>              Парсинг страницы
    ingest topic <TOPIC>          Генерация по теме через Claude
    review                        Интерактивный ревью pending
    push                          Approved → Anki
    sync                          Обновить кэш заметок из Anki
    stats                         Статистика по статусам
    init                          Инициализация БД и Note Type в Anki
                                   (--sync-template — обновить Front/Back/CSS существующего типа)
    doctor                        Проверка согласованности карточек с enrich-конфигом
"""

from __future__ import annotations

import asyncio
import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from .anki.connect import AnkiConnect
from .anki.notetype import (
    CSS,
    NoteTypeConfigError,
    back_template,
    field_names,
    front_template,
    validate_active_fields,
)
from .anki.notetype import _get_note_type_name as get_note_type_name
from .anki.sync import sync_anki_to_cache
from .config import get_config
from .db import Database
from .doctor import find_inconsistencies
from .ingest.topic import ingest_by_topic
from .ingest.url import ingest_from_url
from .log import bound_run
from .models import Status
from .pipeline import push_approved, run_ingest_pipeline
from .review.interactive import review_pending


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

console = Console()


@app.command()
def setup() -> None:
    """Интерактивный мастер первичной настройки."""
    from .setup_wizard import run_setup

    run_setup()


@app.command()
def init(
    sync_template: bool = typer.Option(
        False,
        "--sync-template",
        help="Обновить Front/Back/CSS уже существующего Note Type до текущей версии из кода",
    ),
) -> None:
    """Создать БД и Note Type в Anki."""
    cfg = get_config()

    try:
        validate_active_fields()
    except NoteTypeConfigError as e:
        console.print(f"[red]✗[/] Некорректная схема полей (anki.fields в language.yaml): {e}")
        raise typer.Exit(code=1) from e

    Database(cfg.paths.db)
    console.print(f"[green]✓[/] БД создана: {cfg.paths.db}")

    async def _run() -> None:
        anki = AnkiConnect(cfg)
        try:
            await anki.ensure_deck()
        except Exception as e:
            console.print(
                f"[yellow]![/] Anki недоступен, Note Type не создан: {e}\n"
                "    Запусти `ankiforgeai init` ещё раз, когда Anki будет открыт "
                "(с addon AnkiConnect)."
            )
            return
        console.print(f"[green]✓[/] Deck готов: {cfg.anki.deck_name}")

        note_type = get_note_type_name()
        if note_type in await anki.model_names():
            if not sync_template:
                console.print(f"[green]✓[/] Note Type уже существует: {note_type}")
                return
            await anki.update_model_templates(
                model_name=note_type,
                templates={"Recognition": {"Front": front_template(), "Back": back_template()}},
            )
            await anki.update_model_styling(model_name=note_type, css=CSS)
            console.print(f"[green]✓[/] Note Type обновлён (шаблон + CSS): {note_type}")
            return

        await anki.create_model(
            model_name=note_type,
            fields=field_names(),
            css=CSS,
            card_templates=[
                {"Name": "Recognition", "Front": front_template(), "Back": back_template()}
            ],
        )
        console.print(f"[green]✓[/] Note Type создан: {note_type}")

    with bound_run("init"):
        asyncio.run(_run())


@ingest_app.command("url")
def ingest_url_cmd(
    url: str,
    level: str = typer.Option("A2", help="CEFR уровень"),
    topic: str | None = typer.Option(None, help="Метка темы для тегов"),
) -> None:
    """Извлечь слова со страницы по URL."""
    cfg = get_config()
    db = Database(cfg.paths.db)

    async def _run() -> None:
        console.print(f"[cyan]→[/] Загружаю {url}")
        cards = await ingest_from_url(url, level=level, topic=topic)
        console.print(f"[cyan]→[/] LLM извлёк {len(cards)} слов")
        stats = await run_ingest_pipeline(cards, db=db, cfg=cfg)
        _print_stats(stats)

    with bound_run("ingest_url"):
        asyncio.run(_run())


@ingest_app.command("topic")
def ingest_topic_cmd(
    topic: str,
    count: int = typer.Option(20, help="Сколько слов запросить"),
    level: str = typer.Option("A2", help="CEFR уровень"),
) -> None:
    """Сгенерировать слова по теме через Claude."""
    cfg = get_config()
    db = Database(cfg.paths.db)

    async def _run() -> None:
        console.print(f"[cyan]→[/] Генерирую {count} слов по теме '{topic}' ({level})")
        exclude = [w for _, w in db.all_words()]
        cards = await ingest_by_topic(topic=topic, count=count, level=level, exclude_words=exclude)
        console.print(f"[cyan]→[/] LLM вернул {len(cards)} кандидатов")
        stats = await run_ingest_pipeline(cards, db=db, cfg=cfg)
        _print_stats(stats)

    with bound_run("ingest_topic"):
        asyncio.run(_run())


@app.command()
def review() -> None:
    """Интерактивный ревью pending-карточек."""
    cfg = get_config()
    db = Database(cfg.paths.db)
    with bound_run("review"):
        review_pending(db, cfg)


@app.command()
def push() -> None:
    """Отправить approved-карточки в Anki."""
    cfg = get_config()
    db = Database(cfg.paths.db)
    anki = AnkiConnect(cfg)

    async def _run() -> None:
        count = await push_approved(db, anki, cfg)
        console.print(f"[green]✓[/] Отправлено в Anki: {count}")

    with bound_run("push"):
        asyncio.run(_run())


@app.command()
def sync() -> None:
    """Обновить локальный кэш из Anki."""
    cfg = get_config()
    db = Database(cfg.paths.db)
    anki = AnkiConnect(cfg)

    async def _run() -> None:
        count = await sync_anki_to_cache(db, anki, cfg)
        console.print(f"[green]✓[/] Синхронизировано заметок: {count}")

    with bound_run("sync"):
        asyncio.run(_run())


@app.command()
def stats() -> None:
    """Статистика по статусам."""
    cfg = get_config()
    db = Database(cfg.paths.db)

    table = Table(title="AnkiCards — статистика")
    table.add_column("Статус", style="cyan")
    table.add_column("Количество", justify="right")

    for status in Status:
        cards = db.get_by_status(status)
        table.add_row(status.value, str(len(cards)))

    anki_cached = db.all_anki_words()
    table.add_row("[dim]anki_cache[/]", str(len(anki_cached)))

    console.print(table)


@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
) -> None:
    """Сверить approved/pushed карточки с включёнными enrich/images тумблерами (issue #9)."""
    cfg = get_config()
    db = Database(cfg.paths.db)

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
            table.add_row(p.card_id, p.word, p.check, p.reason)
        console.print(table)

    if problems:
        raise typer.Exit(code=1)


def _print_stats(stats: dict) -> None:
    table = Table(title="Результат pipeline")
    table.add_column("Метрика", style="cyan")
    table.add_column("Значение", justify="right")
    for key, value in stats.items():
        table.add_row(key, str(value))
    console.print(table)


if __name__ == "__main__":
    app()
