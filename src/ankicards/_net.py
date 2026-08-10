"""Общая политика retry/backoff для сетевых вызовов.

http_retry — для httpx-запросов (AnkiConnect, ingest url, Unsplash):
ретраит только транспортные сбои (обрыв соединения, таймаут), не HTTP-статусы
вроде 404 — повторный запрос их не исправит.
"""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

http_retry = retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
