"""Общий интерфейс канала доставки уведомлений."""

from __future__ import annotations

from typing import Any, Protocol


class Notifier(Protocol):
    async def send(self, report: dict[str, Any]) -> None: ...
