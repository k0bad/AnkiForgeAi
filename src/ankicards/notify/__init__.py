"""Pluggable notification layer.

Каналы задаются в config.yaml -> notifications: [...], по тому же паттерну,
что и llm.provider — добавить канал значит добавить файл с классом (async
def send(self, report) -> None) и запись в _BACKENDS, без правки dispatch().
Каждый бэкенд сам знает, как собрать себя из NotificationConfig (+ секретов
из .env, если они у него есть) через from_entry(); None означает "нечем
слать" (уже залогировано внутри from_entry) — пропускаем канал молча.
Ошибка одного канала не должна останавливать остальные, поэтому dispatch()
ловит исключения на уровне каждого Notifier.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import Config, NotificationConfig
from ..log import get_logger
from .base import Notifier
from .telegram import TelegramNotifier
from .webhook import WebhookNotifier

logger = get_logger(__name__)

_BACKENDS: dict[str, Callable[[NotificationConfig], Notifier | None]] = {
    "webhook": WebhookNotifier.from_entry,
    "telegram": TelegramNotifier.from_entry,
}


async def dispatch(report: dict[str, Any], cfg: Config) -> None:
    """Отправить отчёт во все включённые каналы cfg.notifications."""
    for entry in cfg.notifications:
        if not entry.enabled:
            continue
        await _send_one(entry, report)


async def _send_one(entry: NotificationConfig, report: dict[str, Any]) -> None:
    factory = _BACKENDS.get(entry.type)
    if factory is None:
        logger.warning("notify.unknown_backend", type=entry.type)
        return
    notifier = factory(entry)
    if notifier is None:
        return
    try:
        await notifier.send(report)
    except Exception as e:
        logger.warning("notify.send_failed", type=entry.type, error=str(e))
