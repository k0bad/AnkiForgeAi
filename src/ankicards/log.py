"""Настройка structlog для всего проекта.

Инициализируется один раз при импорте. Конфиг берётся из config.yaml.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog

from .config import get_config


def setup_logging() -> None:
    """Настроить structlog один раз (идемпотентно)."""
    cfg = get_config()
    level = getattr(logging, cfg.logging.level.upper(), logging.INFO)

    # Подавляем шумные логгеры сторонних библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.WARNING)

    shared_processors: list[structlog.types.Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if cfg.logging.json_logs:
        processors = shared_processors + [
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Корневой логгер
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Получить structlog-логгер."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name or __name__))


# Auto-setup при первом импорте
setup_logging()
