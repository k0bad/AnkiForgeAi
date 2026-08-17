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
from ..config import NotificationConfig, get_secrets
from ..log import get_logger
from .format import format_report

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 10.0


class WebhookNotifier:
    def __init__(self, url: str, format: str = "text", timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = url
        self.format = format
        self._timeout = timeout

    @classmethod
    def from_entry(cls, entry: NotificationConfig) -> WebhookNotifier | None:
        # .env (Secrets.notify_webhook_url) переопределяет entry.url из config.yaml —
        # тот файл коммитится в публичный репозиторий, реальный адрес (может
        # содержать bot-токен) там намеренно пуст.
        url = get_secrets().notify_webhook_url or entry.url
        if not url:
            logger.warning("notify.no_url", type=entry.type)
            return None
        return cls(url=url, format=entry.format)

    async def send(self, report: dict[str, Any]) -> None:
        payload = report if self.format == "json" else {"text": format_report(report)}
        await self._post(payload)

    @http_retry
    async def _post(self, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self.url, json=payload)
            response.raise_for_status()
