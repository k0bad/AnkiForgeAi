"""Прямой Telegram Bot API бэкенд — альтернатива notify/webhook.py для тех,
кто не хочет держать промежуточный n8n/Zapier (issue #55). Можно включить
оба канала сразу: dispatch() фан-аутит по всем enabled-записям независимо.

POST на https://api.telegram.org/bot<token>/sendMessage — тот же
Telegram-flavored текст, что и webhook format="text" (см. notify/format.py).
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
API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        topic_id: int | None = None,
        parse_mode: str = "Markdown",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.token = token
        self.chat_id = chat_id
        self.topic_id = topic_id
        self.parse_mode = parse_mode
        self._timeout = timeout

    @classmethod
    def from_entry(cls, entry: NotificationConfig) -> TelegramNotifier | None:
        # notify_telegram_token (.env) переопределяет entry.token из config.yaml —
        # тот файл коммитится в публичный репозиторий, реальный bot-токен там не
        # держим (см. Secrets.notify_telegram_token).
        token = get_secrets().notify_telegram_token or entry.token
        if not token:
            logger.warning("notify.no_token", type=entry.type)
            return None
        if not entry.chat_id:
            logger.warning("notify.no_chat_id", type=entry.type)
            return None
        return cls(
            token=token,
            chat_id=entry.chat_id,
            topic_id=entry.topic_id,
            parse_mode=entry.parse_mode,
        )

    async def send(self, report: dict[str, Any]) -> None:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": format_report(report),
            "parse_mode": self.parse_mode,
        }
        if self.topic_id is not None:
            payload["message_thread_id"] = self.topic_id
        await self._post(payload)

    @http_retry
    async def _post(self, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(API_URL.format(token=self.token), json=payload)
            response.raise_for_status()
