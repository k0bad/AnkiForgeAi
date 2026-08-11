"""Generic webhook-бэкенд: POST JSON на произвольный URL.

Покрывает n8n, Hermes, Zapier и любой другой шлюз-посредник, который сам
решает, куда переслать сообщение дальше и как с ним обойтись (Telegram,
другая соцсеть, AI-агент). Отдельного бэкенда под каждого посредника не
нужно: с нашей стороны это всегда обычный webhook-приёмник — меняется
только URL и, при необходимости, форма payload:

- format="text" (по умолчанию) — {"text": "<Telegram-flavored markdown>"},
  готовое сообщение под уже существующий n8n workflow (parse_mode: Markdown).
- format="json" — сырой структурированный report целиком, без какого-либо
  форматирования под конкретный мессенджер. Для посредника, который сам
  решает, как и куда маршрутизировать (например Hermes с несколькими
  каналами) — ему нужны данные, а не готовый текст.
"""

from __future__ import annotations

from typing import Any

import httpx

from .._net import http_retry

DEFAULT_TIMEOUT = 10.0


class WebhookNotifier:
    def __init__(self, url: str, format: str = "text", timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = url
        self.format = format
        self._timeout = timeout

    async def send(self, report: dict[str, Any]) -> None:
        payload = report if self.format == "json" else {"text": format_report(report)}
        await self._post(payload)

    @http_retry
    async def _post(self, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self.url, json=payload)
            response.raise_for_status()


def format_report(report: dict[str, Any]) -> str:
    """Отрендерить структурированный отчёт в Telegram-flavored markdown."""
    label = report.get("label") or report["topic"]
    lines = [
        "📚 AnkiCards · ежедневная генерация",
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

    if report.get("auto_accepted"):
        lines.append(f"🔄 Авто-принято: {report['auto_accepted']} карточек → approved")

    if report.get("pushed"):
        lines.append(f"📤 Отправлено в Anki: {report['pushed']} карточек")
    elif report.get("push_error"):
        lines.append(f"⚠️ Push: {report['push_error']}")
    else:
        lines.append("📤 Push: нет новых карточек для отправки")

    lines.append("")
    lines.append("✅ Полный цикл завершён автоматически")
    return "\n".join(lines)
