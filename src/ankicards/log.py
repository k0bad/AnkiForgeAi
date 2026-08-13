"""Настройка structlog для всего проекта.

Инициализируется один раз при импорте. Конфиг берётся из config.yaml.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import uuid
from collections.abc import Iterator
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
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,  # без него exc_info=True рендерится как голое "true"
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


@contextlib.contextmanager
def bound_run(command: str) -> Iterator[str]:
    """Привязать run_id и имя команды к контексту логов на время одного CLI-запуска.

    run_id попадает во все structlog-записи через merge_contextvars и в audit_log
    через Database.log_action (читает его из тех же contextvars), так что оба
    источника трассировки можно связать по одному идентификатору запуска.
    """
    run_id = uuid.uuid4().hex[:8]
    structlog.contextvars.bind_contextvars(run_id=run_id, command=command)
    try:
        yield run_id
    except Exception as e:
        # Без этого необработанные исключения (Anki недоступен, сбой БД и т.п.)
        # вылетают голым traceback'ом мимо structlog — команда падает без единой
        # структурированной записи о том, что и почему сломалось.
        get_logger(__name__).error("command.failed", error=str(e), exc_info=True)
        raise
    finally:
        structlog.contextvars.clear_contextvars()


# Auto-setup при первом импорте
setup_logging()
