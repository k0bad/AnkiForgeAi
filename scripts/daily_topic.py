"""Ежедневная генерация слов по расписанию тем (для cron).

Читает data/topic_schedule.yaml, выбирает тему для сегодняшнего дня
и запускает полный пайплайн ingest (LLM → enrich → dedupe → save).

Уведомления (шаг 5) отправляются через src/ankicards/notify/ — каналы
берутся из config.yaml -> notifications: [...]. По умолчанию отчёт
только печатается в stdout; передай --notify, чтобы разослать его по
включённым каналам (так делает scripts/daily_topic.sh для cron — ручные
тестовые прогоны по умолчанию никого не уведомляют).

ПОЛНЫЙ АВТОМАТИЧЕСКИЙ ЦИКЛ (daily_topic.sh):
1. Генерация слов по теме
2. Dedupe + enrich + media
3. Авто-принятие всех review-карточек → approved
4. Авто-push в Anki (если AnkiConnect доступен)
5. Уведомление по каналам из config.yaml -> notifications

Использование:
    uv run python scripts/daily_topic.py             # тема по дню недели, без уведомлений
    uv run python scripts/daily_topic.py --dry-run   # показать, что бы сгенерировало
    uv run python scripts/daily_topic.py --topic dyr --count 5   # переопределить
    ./scripts/daily_topic.sh                          # полный цикл + уведомления (для cron)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEDULE_PATH = PROJECT_ROOT / "data" / "topic_schedule.yaml"
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def load_schedule() -> dict:
    with SCHEDULE_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def today_key() -> str:
    return datetime.now().strftime("%A").lower()  # monday..sunday


def pick_topic(schedule: dict, day_key: str) -> dict:
    days = schedule.get("schedule", {})
    if day_key in days:
        return days[day_key]
    return schedule.get("default", {"topic": day_key, "count": 10, "level": "A2"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M") + " UTC"


def main() -> None:
    parser = argparse.ArgumentParser(description="Ежедневная тема из расписания → ingest")
    parser.add_argument("--dry-run", action="store_true", help="Показать тему, не запускать")
    parser.add_argument("--topic", type=str, default=None, help="Переопределить тему")
    parser.add_argument("--count", type=int, default=None, help="Сколько слов")
    parser.add_argument("--level", type=str, default=None, help="CEFR уровень")
    parser.add_argument("--no-push", action="store_true", help="Не пушить в Anki (только генерация)")
    parser.add_argument(
        "--notify", action="store_true", help="Разослать отчёт по каналам из config.yaml"
    )
    args = parser.parse_args()

    schedule = load_schedule()
    day_key = today_key()
    entry = pick_topic(schedule, day_key)

    topic = args.topic or entry.get("topic") or "generelt"
    count = args.count or entry.get("count", 10)
    level = args.level or entry.get("level", "A2")
    label = entry.get("label", topic)

    header = f"День: {day_key} | Тема: {label} ({topic}) | {count} слов | {level}"

    if args.dry_run:
        print(f"[dry-run] {header}\n(ничего не генерирую)")
        return

    from ankicards.config import get_config
    from ankicards.db import Database
    from ankicards.ingest.topic import ingest_by_topic
    from ankicards.models import Status
    from ankicards.pipeline import run_ingest_pipeline

    cfg = get_config()
    db = Database(cfg.paths.db)

    async def _run() -> None:
        auto_accepted = 0
        pushed = 0
        push_error = ""

        # ─── 1. Генерация ───
        exclude = [w for _, w in db.all_words()]
        cards = await ingest_by_topic(topic=topic, count=count, level=level, exclude_words=exclude)
        stats = await run_ingest_pipeline(cards, db=db, cfg=cfg)

        # Собираем новые слова для отчёта
        new_words = []
        for c in cards:
            if not c.word or not c.translation:
                continue
            new_words.append((c.word, c.translation, c.pos.value))

        # ─── 2. Авто-принятие review-карточек → approved ───
        review_cards = db.get_by_status(Status.REVIEW)
        for card in review_cards:
            db.update_status(card.id, Status.APPROVED)
            db.log_action("auto_accept", card_id=card.id, details={})
            auto_accepted += 1

        # ─── 3. Авто-push в Anki (если не --no-push) ───
        if not args.no_push:
            from ankicards.anki.connect import AnkiConnect, AnkiConnectError

            try:
                anki = AnkiConnect(cfg)
                pushed = await _push_approved(db, anki, cfg)
            except AnkiConnectError as e:
                push_error = f"Anki недоступен: {e}"
            except Exception as e:
                push_error = f"Ошибка push: {e}"

        # ─── 4. Структурированный отчёт ───
        report = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "generated_at": _utc_now(),
            "day": day_key,
            "topic": topic,
            "label": label,
            "level": level,
            "count": count,
            "new_words": [
                {"word": word, "translation": trans, "pos": pos}
                for word, trans, pos in new_words
            ],
            "stats": stats,
            "auto_accepted": auto_accepted,
            "pushed": pushed,
            "push_error": push_error or None,
        }

        from ankicards.notify.webhook import format_report

        print(format_report(report))

        if args.notify:
            from ankicards.notify import dispatch

            await dispatch(report, cfg)

    asyncio.run(_run())


async def _push_approved(db, anki, cfg) -> int:
    """Push approved cards to Anki. Returns count pushed."""
    from ankicards.pipeline import push_approved
    return await push_approved(db, anki, cfg)


if __name__ == "__main__":
    main()