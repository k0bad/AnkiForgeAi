"""Рендер структурированного report в Telegram-flavored markdown.

Общий для всех бэкендов, которым нужен готовый текст сообщения (webhook
format="text", telegram) — не завязан на конкретный канал доставки.
"""

from __future__ import annotations

from typing import Any


def format_report(report: dict[str, Any]) -> str:
    """Отрендерить структурированный отчёт в Telegram-flavored markdown."""
    label = report.get("label") or report["topic"]
    lines = [
        "📚 AnkiForgeAI · ежедневная генерация",
        "",
        f"🗓️ День: {report['day']} | Тема: {label} ({report['topic']}) | "
        f"{report['count']} слов | {report['level']}",
        f"🕐 {report['generated_at']}",
        "",
    ]

    new_words = report.get("new_words") or []
    if not new_words:
        lines.append("❌ Не удалось сгенерировать слова (пустой ответ LLM).")
    else:
        lines.append(f"✅ Новых слов: {len(new_words)}")
        for w in new_words:
            lines.append(f"  • **{w['word']}** ({w['pos']}) — {w['translation']}")

    stats = report.get("stats", {})
    lines.append("")
    lines.append(
        f"📊 Статусы: new={stats.get('new', 0)}, review={stats.get('review', 0)}, "
        f"merged={stats.get('merged', 0)}, enriched={stats.get('enriched', 0)}, "
        f"audio={stats.get('audio', 0)}, errors={stats.get('errors', 0)}"
    )

    # images отсутствует в stats целиком, если images.enabled=false (issue #54) —
    # тогда строку не показываем вовсе, а не 0/0/0, который выглядел бы как факт
    # о картинках при выключенной стадии.
    images = stats.get("images")
    if images:
        lines.append(
            f"🖼️ Картинки: {images.get('found', 0)} найдено, "
            f"{images.get('skipped_not_noun', 0)} пропущено (не существительное), "
            f"{images.get('failed_no_result', 0)} не найдено (провайдер)"
        )

    if report.get("needs_review"):
        lines.append(
            f"⏳ Ждут ручного ревью: {report['needs_review']} карточек (`ankiforgeai review`)"
        )

    if report.get("pushed"):
        lines.append(f"📤 Отправлено в Anki: {report['pushed']} карточек")
    elif report.get("push_error"):
        lines.append(f"⚠️ Push: {report['push_error']}")
    else:
        lines.append("📤 Push: нет новых карточек для отправки")

    # streak_days отсутствует целиком для отчётов, собранных до issue #58 (часть #2) —
    # тогда строку не показываем вовсе, а не "🔥 Стрик: 0 дн.", который выглядел бы
    # как реальный факт о стрике.
    if "streak_days" in report:
        streak = report["streak_days"]
        lines.append(f"🔥 Стрик: {streak} дн." if streak else "🔥 Стрик прерван")

    lines.append("")
    lines.append("✅ Полный цикл завершён автоматически")
    return "\n".join(lines)
