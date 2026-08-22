"""CLI точка входа: `ankicards <command>`.

Команды:
    about                          Версия, ссылки на roadmap/changelog/issues
    ingest url <URL>              Парсинг страницы
    ingest topic <TOPIC>          Генерация по теме через Claude
    ingest bildetema <TOPIC>      Импорт темы из картинного словаря Bildetema (со своими фото/аудио)
    review                        Интерактивный ревью pending (нужен TTY)
    review list                   Список pending/review-карточек (--json) — без TTY
    review html                   Страница ревью с фото и звуком (--out FILE) — без TTY
    review accept <id...>         Принять карточки (enrich + media → approved) — без TTY
                                  --verified: тег verified::<дата>, «проверил лично»
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
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .anki.connect import AnkiConnect, AnkiConnectError
from .anki.notetype import (
    CARD_TEMPLATE_NAME,
    CSS,
    NoteTypeConfigError,
    back_template,
    diff_fields,
    field_names,
    front_template,
    validate_active_fields,
)
from .anki.notetype import _get_note_type_name as get_note_type_name
from .anki.sync import sync_anki_to_cache
from .config import Config, get_config, set_config_override, with_language
from .db import Database, IdMigrationRequiredError
from .doctor import (
    count_images_failed_no_result,
    count_images_found,
    count_images_skipped_not_noun,
    find_inconsistencies,
)
from .ingest import bildetema
from .ingest.topic import ingest_by_topic
from .ingest.url import ingest_from_url
from .log import bound_run, get_logger
from .migrate_ids import migrate_ids as run_migrate_ids
from .migrate_ids import needs_migration
from .models import Card, Level, Status
from .pipeline import (
    NoteTypeMissingError,
    check_level_progress,
    compute_streak,
    push_approved,
    run_ingest_pipeline,
)
from .review import actions as review_actions
from .review import html_report
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

_LANGUAGE_OPT = typer.Option(
    None, "--language", "-l", help="Переопределить language: из config.yaml на этот запуск"
)


def _cfg(language: str | None) -> Config:
    """Получить активный Config, при --language подменив cfg.language на этот запуск
    (issue #63). set_config_override делает подмену видимой и для кода, который зовёт
    get_config() сам (load_prompt, notetype._current_language) — не только для cfg,
    переданного явно сюда дальше по стеку."""
    cfg = get_config()
    if not language:
        return cfg
    try:
        overridden = with_language(cfg, language)
    except FileNotFoundError as e:
        console.print(f"[red]✗[/] {e}")
        raise typer.Exit(code=1) from e
    set_config_override(overridden)
    return overridden


def _open_db(cfg: Config) -> Database:
    """Открыть Database, но с понятной ошибкой вместо трейсбека, если БД ещё
    на старой (UUID) схеме id и ждёт `ankiforgeai migrate-ids`."""
    try:
        return Database(cfg.paths.db, default_language=cfg.language)
    except IdMigrationRequiredError as e:
        console.print(f"[red]✗[/] {e}")
        raise typer.Exit(code=1) from e


@app.command()
def about() -> None:
    """Версия и ссылки: репозиторий, roadmap, changelog, issues."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        pkg_version = version("ankiforgeai")
    except PackageNotFoundError:
        pkg_version = "dev (editable install)"

    console.print(
        Panel.fit(
            f"AnkiForgeAI v{pkg_version}\n\n"
            "Repository:  https://github.com/k0bad/AnkiForgeAi\n"
            "Roadmap:     https://github.com/k0bad/AnkiForgeAi#roadmap\n"
            "Changelog:   https://github.com/k0bad/AnkiForgeAi/blob/main/CHANGELOG.md\n"
            "Issues:      https://github.com/k0bad/AnkiForgeAi/issues",
            border_style="cyan",
            title="ℹ️  About",
        )
    )


@app.command()
def setup() -> None:
    """Интерактивный мастер первичной настройки."""
    from .setup_wizard import run_setup

    run_setup()


@app.command()
def init(language: str | None = _LANGUAGE_OPT) -> None:
    """Создать БД и Note Type в Anki."""
    cfg = _cfg(language)

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
        console.print(f"[green]✓[/] Deck готов: {anki.deck}")

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

            # updateModelTemplates/updateModelStyling переносят только HTML/CSS — список
            # полей Note Type они не трогают (см. notetype.diff_fields). Здесь только
            # делаем расхождение видимым, автоматически ничего не чиним.
            anki_fields = await anki.model_field_names(note_type)
            missing_in_anki, extra_in_anki = diff_fields(anki_fields)
            if missing_in_anki or extra_in_anki:
                logger.warning(
                    "notetype.fields_mismatch",
                    note_type=note_type,
                    missing_in_anki=missing_in_anki,
                    extra_in_anki=extra_in_anki,
                )
                console.print(
                    "[yellow]![/] Список полей Note Type в Anki разошёлся со схемой в коде "
                    "(anki.fields в language.yaml) — init не переносит его автоматически "
                    "(переименование/удаление поля стирает данные в существующих заметках, "
                    "это ручная операция: Anki → Browse → Manage Note Types)."
                )
                if missing_in_anki:
                    console.print(f"    Есть в коде, нет в Anki: {', '.join(missing_in_anki)}")
                if extra_in_anki:
                    console.print(f"    Есть в Anki, нет в коде: {', '.join(extra_in_anki)}")
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
    language: str | None = _LANGUAGE_OPT,
) -> None:
    """Извлечь слова со страницы по URL."""
    cfg = _cfg(language)
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

    level_totals = _level_totals(db, cfg.language)
    if as_json:
        print(json.dumps({**stats, "level_totals": level_totals}, ensure_ascii=False, indent=2))
    else:
        _print_stats(stats)
        _print_level_totals(level_totals)


@ingest_app.command("topic")
def ingest_topic_cmd(
    topic: str,
    count: int | None = typer.Option(
        None, help="Сколько слов запросить (по умолчанию — ingest.default_count из config.yaml)"
    ),
    level: str = typer.Option("A2", help="CEFR уровень"),
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
    language: str | None = _LANGUAGE_OPT,
) -> None:
    """Сгенерировать слова по теме через Claude."""
    cfg = _cfg(language)
    db = _open_db(cfg)
    count = count if count is not None else cfg.ingest.default_count

    async def _run() -> dict:
        if not as_json:
            console.print(f"[cyan]→[/] Генерирую {count} слов по теме '{topic}' ({level})")
        exclude = [w for _, w in db.all_words(cfg.language)]
        cards = await ingest_by_topic(topic=topic, count=count, level=level, exclude_words=exclude)
        if not as_json:
            console.print(f"[cyan]→[/] LLM вернул {len(cards)} кандидатов")
        return await run_ingest_pipeline(cards, db=db, cfg=cfg)

    with bound_run("ingest_topic"):
        stats = asyncio.run(_run())

    level_totals = _level_totals(db, cfg.language)
    if as_json:
        print(json.dumps({**stats, "level_totals": level_totals}, ensure_ascii=False, indent=2))
    else:
        _print_stats(stats)
        _print_level_totals(level_totals)


_REVIEW_OUT_OPT = typer.Option(
    None, "--out", "-o", help="Куда положить файл (по умолчанию data/review/review-<язык>.html)"
)
_BILDETEMA_TOPIC_ARG = typer.Argument(
    None, help="Тема Bildetema: id (T034), метка целиком (Klær) или её кусок (frukt)"
)


@ingest_app.command("bildetema")
def ingest_bildetema_cmd(
    topic: str | None = _BILDETEMA_TOPIC_ARG,
    show_list: bool = typer.Option(False, "--list", help="Показать темы Bildetema и выйти"),
    level: str = typer.Option("A1", help="CEFR уровень импортируемых карточек"),
    limit: int | None = typer.Option(None, help="Взять только первые N слов темы"),
    translation_lang: str | None = typer.Option(
        None,
        "--translation-lang",
        help="Язык перевода: ru/en или код Bildetema (rus, ukr, ara...); дефолт — ui_language",
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Перекачать базу Bildetema мимо кэша"),
    enrich: bool = typer.Option(
        True, "--enrich/--no-enrich", help="Досыпать формы/примеры/транскрипцию через LLM"
    ),
    batch_size: int = typer.Option(
        25, "--batch-size", min=1, help="Сколько слов гнать через pipeline за один заход"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Показать, что импортируется, ничего не записывая"
    ),
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
    language: str | None = _LANGUAGE_OPT,
) -> None:
    """Импорт темы из картинного словаря Bildetema (NAFO / OsloMet).

    Слова, переводы, фотографии и записанное диктором аудио берутся с их CDN, так
    что LLM остаётся только грамматика и примеры. Карточки всегда встают в review,
    даже когда всё обогатилось: материал чужой, и каждую надо увидеть глазами перед
    push — `ankiforgeai review html` или `ankiforgeai review`.
    """
    cfg = _cfg(language)

    try:
        database = asyncio.run(bildetema.load_database(cfg, refresh=refresh))
        target_lang = bildetema.resolve_language(
            cfg.language, bildetema.LANG_BY_PROFILE, database, "Изучаемого языка"
        )
        if show_list or topic is None:
            _print_bildetema_topics(database, target_lang, as_json=as_json)
            return

        translation = bildetema.resolve_language(
            translation_lang or cfg.ui_language, bildetema.LANG_BY_UI, database, "Языка перевода"
        )
        if translation == target_lang:
            raise bildetema.BildetemaError(
                f"Язык перевода совпал с изучаемым ({target_lang}) — задай --translation-lang"
            )
        node = bildetema.resolve_topic(database, topic, target_lang)
    except bildetema.BildetemaError as e:
        console.print(f"[red]✗[/] {e}")
        raise typer.Exit(code=1) from e

    try:
        level_enum = Level(level.lower())
    except ValueError as e:
        console.print(f"[red]✗[/] Неизвестный уровень {level!r} (A1..C2)")
        raise typer.Exit(code=1) from e

    entries = bildetema.build_entries(
        database,
        node,
        language=cfg.language,
        target_lang=target_lang,
        translation_lang=translation,
        level=level_enum,
    )
    if limit is not None:
        entries = entries[:limit]
    if not entries:
        console.print(f"[yellow]![/] В теме «{node.full_label}» нет слов для {target_lang}")
        raise typer.Exit(code=1)

    if dry_run:
        _print_bildetema_preview(node, entries, as_json=as_json)
        return

    db = _open_db(cfg)
    media = {entry.card.source or "": entry.media for entry in entries}

    async def _download_media(cards: list[Card]) -> None:
        media_stats = await bildetema.attach_media(cards, media, cfg)
        if not as_json:
            console.print(
                f"[cyan]→[/] Медиа Bildetema: {media_stats['images']} фото, "
                f"{media_stats['audio']} аудио, ошибок {media_stats['failed']}"
            )

    # Пачками, а не темой целиком. Каждая enrich-стадия — это один LLM-вызов на весь
    # переданный список, а темы тут крупные (у «Mat og drikke» 143 слова): ответ с
    # грамматическими формами на сотню существительных упирается в llm.max_tokens, и
    # тогда формы теряет разом вся тема, а не одна пачка. Плюс виден прогресс, и обрыв
    # на середине (сеть, лимиты провайдера) не отменяет уже импортированное — карточки
    # сохраняются пачка за пачкой.
    batches = [entries[i : i + batch_size] for i in range(0, len(entries), batch_size)]

    async def _run() -> dict:
        if not as_json:
            console.print(
                f"[cyan]→[/] «{node.full_label}»: {len(entries)} слов из Bildetema"
                + (f", пачками по {batch_size}" if len(batches) > 1 else "")
            )
        total: dict = {}
        for number, batch in enumerate(batches, start=1):
            if len(batches) > 1 and not as_json:
                console.print(f"[dim]— пачка {number}/{len(batches)} ({len(batch)} слов)[/]")
            if enrich:
                await bildetema.classify_missing_pos(batch)
            try:
                stats = await run_ingest_pipeline(
                    [entry.card for entry in batch],
                    db=db,
                    cfg=cfg,
                    auto_enrich=enrich,
                    on_inserted=_download_media,
                    force_review=True,
                )
            except Exception as e:
                # Упавшая пачка не отменяет уже импортированные: карточки сохраняются
                # по мере готовности, и повторный запуск той же темы доберёт остаток —
                # dedupe отбросит то, что уже лежит в БД.
                logger.warning("bildetema.batch_failed", batch=number, error=str(e))
                _merge_stats(total, {"batches_failed": 1})
                if not as_json:
                    console.print(f"[red]✗[/] пачка {number} сорвалась: {e}")
                continue
            _merge_stats(total, stats)
        return total

    with bound_run("ingest_bildetema"):
        stats = asyncio.run(_run())

    level_totals = _level_totals(db, cfg.language)
    if as_json:
        print(json.dumps({**stats, "level_totals": level_totals}, ensure_ascii=False, indent=2))
    else:
        _print_stats(stats)
        _print_level_totals(level_totals)
        console.print(
            "[green]✓[/] Карточки ждут ревью: `ankiforgeai review html` (визуально, с фото) "
            "или `ankiforgeai review`"
        )


@review_app.callback(invoke_without_command=True)
def review(ctx: typer.Context, language: str | None = _LANGUAGE_OPT) -> None:
    """Без подкоманды — интерактивный ревью pending-карточек (нужен TTY)."""
    if ctx.invoked_subcommand is not None:
        return
    cfg = _cfg(language)
    db = _open_db(cfg)
    with bound_run("review"):
        review_pending(db, cfg)


@review_app.command("list")
def review_list_cmd(
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
    language: str | None = _LANGUAGE_OPT,
) -> None:
    """Список карточек на ревью (review + pending) — без TTY."""
    cfg = _cfg(language)
    db = _open_db(cfg)
    cards = db.get_by_status(Status.REVIEW, cfg.language) + db.get_by_status(
        Status.PENDING, cfg.language
    )

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


@review_app.command("html")
def review_html_cmd(
    out: Path | None = _REVIEW_OUT_OPT,
    topic: str | None = typer.Option(
        None, help="Только карточки, у которых topic содержит эту строку (напр. klær)"
    ),
    include_audio: bool = typer.Option(
        True, "--audio/--no-audio", help="Вшивать mp3 в страницу (крупнее файл, но слышно диктора)"
    ),
    fragment: bool = typer.Option(
        False, "--fragment", help="Без <html>/<head>/<body> — годится для публикации Artifact'ом"
    ),
    language: str | None = _LANGUAGE_OPT,
) -> None:
    """Собрать HTML-страницу ревью: все карточки review/pending с фото и звуком.

    Ничего не меняет в БД — на выходе страница, где карточки отмечаются к отбраковке,
    а кнопка собирает готовые `review skip ...` / `review accept ...` для терминала.
    """
    cfg = _cfg(language)
    db = _open_db(cfg)

    cards = db.get_by_status(Status.REVIEW, cfg.language) + db.get_by_status(
        Status.PENDING, cfg.language
    )
    if topic:
        needle = topic.casefold()
        cards = [c for c in cards if c.topic and needle in c.topic.casefold()]
    cards.sort(key=lambda c: c.id or 0)

    if not cards:
        console.print("[green]Нечего ревьюить.[/]")
        return

    path = out or (cfg.paths.db.parent / "review" / f"review-{cfg.language}.html")
    subtitle = (
        f"{len(cards)} карточек ждут решения. Отметь неподходящие — внизу появятся "
        "команды для терминала."
    )
    markup = html_report.build_report(
        cards,
        cfg,
        title=f"Ревью {topic or cfg.language}",
        subtitle=subtitle,
        include_audio=include_audio,
        standalone=not fragment,
    )
    size = html_report.write_report(path, markup)
    console.print(f"[green]✓[/] {path} ({size / 1024 / 1024:.1f} МБ, {len(cards)} карточек)")


_CARD_IDS_ARG = typer.Argument(..., help="ID карточек (см. review list)")
_FIELD_HELP = (
    f"поле=значение, можно повторять (доступны: {', '.join(review_actions.EDITABLE_FIELDS)})"
)
_FIELD_OPT = typer.Option(..., "--field", "-f", help=_FIELD_HELP)


@review_app.command("accept")
def review_accept_cmd(
    card_ids: list[int] = _CARD_IDS_ARG,
    verified: bool = typer.Option(
        False,
        "--verified",
        help="Пометить тегом verified::<дата> — «я это проверил лично»",
    ),
    batch_size: int = typer.Option(
        review_actions.ACCEPT_BATCH_SIZE,
        "--batch-size",
        min=1,
        help="Сколько карточек обогащать за один заход",
    ),
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
    language: str | None = _LANGUAGE_OPT,
) -> None:
    """Принять карточки без TTY: enrich + media, затем approved.

    --verified навешивает тег verified::<сегодня>, который уезжает в Anki вместе с
    карточкой: потом по `tag:verified::*` видно всё, что проходило через твои глаза,
    а по конкретной дате — что смотрелось в тот заход. Без флага тег не ставится:
    этой же командой пользуются скрипты, и отметка о личной проверке от них была бы
    неправдой.
    """
    cfg = _cfg(language)
    db = _open_db(cfg)

    with bound_run("review_accept"):
        try:
            results = asyncio.run(
                review_actions.accept_cards(
                    card_ids,
                    db,
                    cfg,
                    language=cfg.language,
                    verified=verified,
                    batch_size=batch_size,
                )
            )
        except ValueError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for card_id, status in results.items():
        icon = "[green]✓[/]" if status == Status.APPROVED.value else "[yellow]⚠[/]"
        console.print(f"{icon} {card_id} → {status}")
    if verified:
        console.print(f"[green]✓[/] Помечены тегом verified::{date.today().isoformat()}")


@review_app.command("skip")
def review_skip_cmd(
    card_ids: list[int] = _CARD_IDS_ARG, language: str | None = _LANGUAGE_OPT
) -> None:
    """Пометить карточки как skipped без TTY."""
    cfg = _cfg(language)
    db = _open_db(cfg)
    with bound_run("review_skip"):
        try:
            review_actions.skip_cards(card_ids, db, language=cfg.language)
        except ValueError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e
    for card_id in card_ids:
        console.print(f"[yellow]skipped[/] {card_id}")


@review_app.command("suspend")
def review_suspend_cmd(
    card_ids: list[int] = _CARD_IDS_ARG, language: str | None = _LANGUAGE_OPT
) -> None:
    """Пометить карточки как suspended без TTY."""
    cfg = _cfg(language)
    db = _open_db(cfg)
    with bound_run("review_suspend"):
        try:
            review_actions.suspend_cards(card_ids, db, language=cfg.language)
        except ValueError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e
    for card_id in card_ids:
        console.print(f"[yellow]suspended[/] {card_id}")


@review_app.command("resume")
def review_resume_cmd(
    card_ids: list[int] = _CARD_IDS_ARG, language: str | None = _LANGUAGE_OPT
) -> None:
    """Вернуть suspended/skipped карточки обратно в review без TTY."""
    cfg = _cfg(language)
    db = _open_db(cfg)
    with bound_run("review_resume"):
        try:
            review_actions.resume_cards(card_ids, db, language=cfg.language)
        except ValueError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e
    for card_id in card_ids:
        console.print(f"[green]resumed[/] {card_id}")


@review_app.command("edit")
def review_edit_cmd(
    card_id: int,
    field: list[str] = _FIELD_OPT,
    language: str | None = _LANGUAGE_OPT,
) -> None:
    """Отредактировать текстовые поля карточки без TTY."""
    updates: dict[str, str] = {}
    for item in field:
        if "=" not in item:
            console.print(f"[red]✗[/] Некорректный --field '{item}', ожидается key=value")
            raise typer.Exit(code=1)
        key, value = item.split("=", 1)
        updates[key] = value

    cfg = _cfg(language)
    db = _open_db(cfg)
    with bound_run("review_edit"):
        try:
            updated = review_actions.edit_card(card_id, updates, db, language=cfg.language)
        except ValueError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e
    console.print(f"[green]✓[/] {updated.word} обновлено: {', '.join(updates)}")


@app.command()
def push(
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
    language: str | None = _LANGUAGE_OPT,
) -> None:
    """Отправить approved-карточки в Anki."""
    cfg = _cfg(language)
    db = _open_db(cfg)
    anki = AnkiConnect(cfg)

    async def _run() -> tuple[int, list[dict]]:
        count = await push_approved(db, anki, cfg)
        hints = await check_level_progress(db, anki, cfg) if count > 0 else []
        return count, hints

    with bound_run("push"):
        try:
            count, hints = asyncio.run(_run())
        except (NoteTypeMissingError, AnkiConnectError) as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(code=1) from e

    if as_json:
        print(json.dumps({"pushed": count, "level_hints": hints}, ensure_ascii=False))
    else:
        console.print(f"[green]✓[/] Отправлено в Anki: {count}")
        for hint in hints:
            console.print(
                f"[yellow]💡[/] Уровень {hint['level'].upper()}: {hint['mature']}/{hint['total']} "
                f"карточек зрелые ({hint['mature_ratio']:.0%}) — похоже, пора добавлять "
                f"{hint['next_level'].upper()} (ankiforgeai ingest topic ... "
                f"--level {hint['next_level'].upper()})"
            )


@app.command()
def sync(
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
    language: str | None = _LANGUAGE_OPT,
) -> None:
    """Обновить локальный кэш из Anki."""
    cfg = _cfg(language)
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
    language: str | None = _LANGUAGE_OPT,
) -> None:
    """Удалить карточки насовсем и освободить их номер для новых карточек.

    Если карточка уже запушена в Anki, вместе с заметкой стирается и её
    история повторений/интервалов там — это необратимо.
    """
    cfg = _cfg(language)
    db = _open_db(cfg)
    anki = AnkiConnect(cfg)

    if not as_json:
        console.print(
            "[yellow]![/] Удаление уже запушенной карточки стирает её историю "
            "повторений в Anki — это необратимо."
        )

    async def _run() -> list[int]:
        return await review_actions.delete_cards(card_ids, db, anki, language=cfg.language)

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
def stats(
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
    language: str | None = typer.Option(
        None, "--language", "-l", help="Показать только этот язык (по умолчанию — все вместе)"
    ),
) -> None:
    """Статистика по статусам, стрик и общее число слов.

    Без --language — сводный вид по всем языкам сразу; с ним — только этот язык.
    """
    cfg = _cfg(language)
    db = _open_db(cfg)

    # language, не cfg.language: последний после _cfg() всегда конкретен (см. её
    # docstring), а тут именно "сырой" флаг нужен, чтобы отсутствие --language
    # значило "все языки", а не "активный язык" (issue #63).
    counts = {status.value: len(db.get_by_status(status, language)) for status in Status}
    anki_cached = len(db.all_anki_words(language))
    streak = compute_streak(db)
    total_words = sum(n for status, n in counts.items() if status != Status.SKIPPED.value)

    if as_json:
        payload = {
            **counts,
            "anki_cache": anki_cached,
            "streak_days": streak,
            "total_words": total_words,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    table = Table(title="AnkiForgeAI — статистика")
    table.add_column("Статус", style="cyan")
    table.add_column("Количество", justify="right")

    for status_value, count in counts.items():
        table.add_row(status_value, str(count))
    table.add_row("[dim]anki_cache[/]", str(anki_cached))

    console.print(table)
    streak_line = (
        f"🔥 Стрик: {streak} дн." if streak else "Стрик прерван — запушите что-нибудь сегодня"
    )
    console.print(f"{streak_line}   [dim]|[/]   📚 Всего слов: {total_words}")


@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод"),
    language: str | None = typer.Option(
        None, "--language", "-l", help="Проверить только этот язык (по умолчанию — все вместе)"
    ),
) -> None:
    """Сверить approved/pushed карточки с включёнными enrich/images тумблерами (issue #9).

    Без --language — сводная проверка по всем языкам (см. stats — тот же issue #63 резон)."""
    cfg = _cfg(language)
    db = _open_db(cfg)

    cards = db.get_by_status(Status.APPROVED, language) + db.get_by_status(Status.PUSHED, language)
    problems = find_inconsistencies(cards, cfg)

    # Сводка по картинкам (issue #73) — справочные числа, не Inconsistency и не
    # влияют на exit code: failed_no_result дублирует check="images.enabled" из
    # problems выше как готовый счётчик, found/skipped_not_noun (issue #54) дают
    # полную картину, а не только то, что не нашлось.
    images_summary = {
        "found": count_images_found(cards, cfg),
        "skipped_not_noun": count_images_skipped_not_noun(cards, cfg),
        "failed_no_result": count_images_failed_no_result(cards, cfg),
    }

    if as_json:
        print(
            json.dumps(
                {
                    "problems": [p.model_dump() for p in problems],
                    "images": images_summary,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if not problems:
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

        if cfg.images.enabled:
            console.print(
                f"[dim]ℹ Картинки — найдено: {images_summary['found']}, "
                f"не найдено: {images_summary['failed_no_result']}, "
                f"не существительное: {images_summary['skipped_not_noun']}[/]"
            )

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


def _merge_stats(total: dict, batch: dict) -> None:
    """Сложить статистику пачки в общую. images приходит вложенным словарём
    счётчиков (см. pipeline.enrich_and_generate_media), остальное — плоские числа."""
    for key, value in batch.items():
        if isinstance(value, dict):
            nested = total.setdefault(key, {})
            for sub_key, sub_value in value.items():
                nested[sub_key] = nested.get(sub_key, 0) + sub_value
        else:
            total[key] = total.get(key, 0) + value


def _print_bildetema_topics(database: dict, lang: str, as_json: bool = False) -> None:
    """Дерево тем Bildetema. Подтемы идут в том же списке с отступом — импортировать
    можно и то, и другое, поэтому прятать их не за чем."""
    topics = bildetema.list_topics(database, lang)
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "id": t.id,
                        "label": t.label,
                        "path": list(t.path),
                        "slug": t.slug,
                        "words": len(t.word_ids),
                    }
                    for t in topics
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    table = Table(title=f"Темы Bildetema ({lang})")
    table.add_column("id", style="dim")
    table.add_column("Тема")
    table.add_column("Слов", justify="right")
    for t in topics:
        depth = len(t.path) - 1
        label = "  " * depth + t.label
        table.add_row(t.id, label if depth else f"[cyan]{label}[/]", str(len(t.word_ids)))
    console.print(table)


def _print_bildetema_preview(
    node: bildetema.TopicInfo, entries: list[bildetema.Entry], as_json: bool = False
) -> None:
    """--dry-run: что именно уедет в pipeline. Часть речи тут ещё «сырая» —
    classify_missing_pos() не звалась, так что бесартиклевые слова показаны как other."""
    if as_json:
        print(
            json.dumps(
                {
                    "topic": {"id": node.id, "label": node.full_label, "slug": node.slug},
                    "cards": [
                        {
                            **e.card.model_dump(mode="json"),
                            "article": e.article,
                            "images": len(e.media.image_urls),
                            "audio": bool(e.media.audio_url),
                        }
                        for e in entries
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    table = Table(title=f"{node.full_label} — {len(entries)} слов (--dry-run, ничего не записано)")
    table.add_column("id", style="dim")
    table.add_column("Слово", style="cyan")
    table.add_column("Артикль")
    table.add_column("POS")
    table.add_column("Перевод")
    table.add_column("Фото", justify="right")
    for e in entries:
        table.add_row(
            e.word_id,
            e.card.word,
            e.article or "—",
            e.card.pos.value,
            e.card.translation,
            str(len(e.media.image_urls)),
        )
    console.print(table)


def _print_stats(stats: dict) -> None:
    table = Table(title="Результат pipeline")
    table.add_column("Метрика", style="cyan")
    table.add_column("Значение", justify="right")
    for key, value in stats.items():
        table.add_row(key, str(value))
    console.print(table)


def _level_totals(db: Database, language: str | None = None) -> dict[str, dict[str, int]]:
    """{level: {"total": N, "pushed": M}} — total считает все статусы кроме skipped,
    чтобы отражать реальный словарный запас, а не отклонённые дубликаты.
    language — сузить до одного языка (issue #63); вызывается сразу после ingest
    для этого языка, так что сводка не должна подмешивать другие языки."""
    raw = db.count_by_level(language)
    result = {}
    for level, by_status in sorted(raw.items()):
        pushed = by_status.get(Status.PUSHED.value, 0)
        total = sum(n for status, n in by_status.items() if status != Status.SKIPPED.value)
        result[level] = {"total": total, "pushed": pushed}
    return result


def _print_level_totals(level_totals: dict[str, dict[str, int]]) -> None:
    if not level_totals:
        return
    table = Table(title="Карточки по уровням")
    table.add_column("Уровень", style="cyan")
    table.add_column("Всего", justify="right")
    table.add_column("Запушено", justify="right")
    for level, counts in level_totals.items():
        table.add_row(level.upper(), str(counts["total"]), str(counts["pushed"]))
    console.print(table)


if __name__ == "__main__":
    app()
