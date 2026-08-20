"""Парсинг URL → список кандидатов.

Стратегия:
1. Скачать страницу через httpx (с лимитом размера).
2. Извлечь основной текст через trafilatura.
3. Передать текст Claude через prompts/url_extract.md
   с инструкцией "выдели слова уровня A2-B1, верни JSON".
4. Распарсить JSON → list[Card] со status=pending.
"""

from __future__ import annotations

import httpx
import trafilatura

from .._net import http_retry
from ..config import get_config
from ..llm import call_json, load_prompt
from ..models import POS, Card, Level, Status

MAX_TEXT_CHARS = 12_000


@http_retry
async def _fetch(url: str, timeout: float) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response


async def ingest_from_url(url: str, level: str = "A2", topic: str | None = None) -> list[Card]:
    """Извлечь кандидатов со страницы по URL."""
    cfg = get_config()
    limit_bytes = cfg.ingest.url_max_size_mb * 1024 * 1024
    timeout = cfg.ingest.url_timeout_seconds

    response = await _fetch(url, timeout)
    content = response.content
    if len(content) > limit_bytes:
        raise ValueError(f"Страница слишком большая: {len(content)} > {limit_bytes} байт")
    html = response.text

    extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not extracted:
        raise ValueError(f"trafilatura не смогла извлечь текст со страницы: {url}")

    text = extracted[:MAX_TEXT_CHARS]
    prompt = load_prompt("url_extract", level=level, text=text)
    raw_items = await call_json(prompt)
    if not isinstance(raw_items, list):
        raise ValueError(f"LLM вернул не массив: {type(raw_items).__name__}")

    return [
        _to_card(item, url=url, level=level, topic=topic, language=cfg.language)
        for item in raw_items
        if isinstance(item, dict) and item.get("word") and item.get("translation")
    ]


def _to_card(item: dict, url: str, level: str, topic: str | None, language: str) -> Card:
    pos_raw = str(item.get("pos", "other")).lower()
    try:
        pos = POS(pos_raw)
    except ValueError:
        pos = POS.OTHER

    level_enum: Level | None
    try:
        level_enum = Level(level.lower())
    except ValueError:
        level_enum = None

    return Card(
        language=language,
        word=str(item["word"]).strip(),
        pos=pos,
        translation=str(item["translation"]).strip(),
        example=(str(item["example"]).strip() if item.get("example") else None),
        example_translation=(
            str(item["example_translation"]).strip() if item.get("example_translation") else None
        ),
        level=level_enum,
        topic=topic,
        source=f"url:{url}",
        status=Status.PENDING,
    )
