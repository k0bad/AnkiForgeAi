"""Pluggable notification layer.

Каналы задаются в config.yaml -> notifications: [...], по тому же паттерну,
что и llm.provider — добавить канал значит добавить бэкенд в _BACKENDS,
без правки вызывающего кода. Ошибка одного канала не должна останавливать
остальные, поэтому dispatch() ловит исключения на уровне каждого Notifier.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import Config, NotificationConfig
from ..log import get_logger
from .base import Notifier
from .webhook import WebhookNotifier

logger = get_logger(__name__)

_BACKENDS: dict[str, Callable[..., Notifier]] = {
    "webhook": WebhookNotifier,
}


async def dispatch(report: dict[str, Any], cfg: Config) -> None:
    """Отправить отчёт во все включённые каналы cfg.notifications."""
    for entry in cfg.notifications:
        if not entry.enabled:
            continue
        await _send_one(entry, report)


async def _send_one(entry: NotificationConfig, report: dict[str, Any]) -> None:
    backend = _BACKENDS.get(entry.type)
    if backend is None:
        logger.warning("notify.unknown_backend", type=entry.type)
        return
    try:
        await backend(url=entry.url, format=entry.format).send(report)
    except Exception as e:
        logger.warning("notify.send_failed", type=entry.type, url=entry.url, error=str(e))
