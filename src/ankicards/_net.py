"""Общая политика retry/backoff для сетевых вызовов.

http_retry — для httpx-запросов (AnkiConnect, ingest url, Unsplash):
ретраит только транспортные сбои (обрыв соединения, таймаут), не HTTP-статусы
вроде 404 — повторный запрос их не исправит.
"""

from __future__ import annotations

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .log import get_logger

logger = get_logger(__name__)


def _log_retry(retry_state: RetryCallState) -> None:
    """tenacity ретраит транспортные сбои молча по умолчанию — без этого хука
    повторные попытки (и то, что первая попытка вообще упала) нигде не видны."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning("http.retry", attempt=retry_state.attempt_number, error=str(exc))


http_retry = retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
    before_sleep=_log_retry,
)
