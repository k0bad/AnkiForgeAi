"""LLM calls with provider selection: Anthropic Claude or OpenAI-compatible (OpenRouter).

Provider выбирается из config.yaml -> llm.provider:
- "anthropic"  — использует Anthropic SDK + ANTHROPIC_API_KEY
- "openrouter" — OpenAI-compatible API через openai SDK (OpenRouter / любые OpenAI-совместимые)

Промпты загружаются из prompts/*.md с подстановкой {placeholders} через str.format.
"""

from __future__ import annotations

import json
import os
from typing import Any, cast

from tenacity import RetryCallState, retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import Config, get_config, get_secrets
from .log import get_logger

logger = get_logger(__name__)


class EmptyCompletionError(Exception):
    """Провайдер вернул пустой completion (content=None/"" при HTTP 200).

    Раньше это тихо превращалось в "" и всплывало только позже как
    ValueError('LLM вернул невалидный JSON') из _parse_json — уже вне
    retry-обёртки _call_openai/_call_anthropic, да ещё и ValueError
    сознательно исключён из ретраев в _is_transient. На практике пустой
    completion почти всегда транзиентный глюк провайдера (см. issue #28),
    поэтому поднимаем её прямо в теле @_llm_retry-функции — обычный,
    ретраящийся случай, а не "невалидный ответ, повтор не поможет"."""


def _is_transient(exc: BaseException) -> bool:
    """Ретраить сетевые/API-ошибки провайдера (включая пустой completion), но не
    наши ValueError/RuntimeError (невалидный ключ, невалидный JSON) — повторный
    вызов их не исправит."""
    return not isinstance(exc, (RuntimeError, ValueError))


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning("llm.retry", attempt=retry_state.attempt_number, error=str(exc))


_llm_retry = retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
    before_sleep=_log_retry,
)


def _client_openai(cfg: Config, key: str | None = None) -> Any:
    """Создать OpenAI-клиент. `key` переопределяет ключ из .env."""
    from openai import AsyncOpenAI

    if key:
        api_key = key
    else:
        secrets = get_secrets()
        api_key = secrets.openrouter_api_key or os.getenv("OPENROUTER_API_KEY") or ""
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY не задан. Проверь .env в корне проекта.")

    return AsyncOpenAI(
        api_key=api_key,
        base_url=cfg.llm.base_url,
    )


def _client_anthropic() -> Any:
    """Создать Anthropic-клиент."""
    from anthropic import AsyncAnthropic

    secrets = get_secrets()
    if not secrets.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY не задан. Проверь .env в корне проекта.")
    return AsyncAnthropic(api_key=secrets.anthropic_api_key)


def load_prompt(name: str, **kwargs: Any) -> str:
    """Загрузить prompts/{name}.md, подставить переменные, искать сначала в языке.

    Поиск:
    1. languages/{target}/prompts/{name}.md
    2. prompts/{name}.md (fallback)
    """
    from .config import get_language

    cfg = get_config()
    path = cfg.paths.prompts_dir / f"{name}.md"

    # Сначала пытаемся загрузить из языковой папки
    try:
        lang = get_language(cfg.language)
        lang_path = lang.prompts_dir / f"{name}.md"
        if lang_path.exists():
            path = lang_path
    except Exception:
        pass  # fallback к общему prompts/

    template = path.read_text(encoding="utf-8")
    if kwargs:
        return template.format(**kwargs)
    return template


async def call_text(prompt: str, cfg: Config | None = None, model: str | None = None, stage: str = "") -> str:
    """Вызвать LLM и вернуть текстовый ответ.

    Провайдер выбирается из cfg.llm.provider. `model` переопределяет cfg.llm.model
    только для этого вызова. `stage` выбирает API-ключ для этой стадии:
    - "enrich" → OPENROUTER_KEY_ENRICH
    - "dedupe" → OPENROUTER_KEY_DEDUPE
    - "" (default) → OPENROUTER_API_KEY
    """
    cfg = cfg or get_config()
    provider = cfg.llm.provider
    model = model or cfg.llm.model

    if provider == "anthropic":
        return await _call_anthropic(prompt, cfg, model)
    elif provider == "openrouter":
        secrets = get_secrets()
        stage_key = {
            "enrich": secrets.openrouter_key_enrich,
            "dedupe": secrets.openrouter_key_dedupe,
        }.get(stage, "")
        return await _call_openai(prompt, cfg, model, key=stage_key or None)
    else:
        raise ValueError(
            f"Неизвестный LLM провайдер: {provider!r}. Ожидается: 'anthropic' или 'openrouter'"
        )


async def call_json(prompt: str, cfg: Config | None = None, stage: str = "") -> Any:
    """Вызвать LLM и распарсить ответ как JSON.

    Устойчив к обёрткам в ```json ... ``` code fences.
    """
    raw = await call_text(prompt, cfg=cfg, stage=stage)
    return _parse_json(raw)


# ───────────── Anthropic ─────────────


@_llm_retry
async def _call_anthropic(prompt: str, cfg: Config, model: str) -> str:
    from anthropic.types import TextBlock

    client = _client_anthropic()
    message = await client.messages.create(
        model=model,
        max_tokens=cfg.llm.max_tokens,
        temperature=cfg.llm.temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [b.text for b in message.content if isinstance(b, TextBlock)]
    text = "".join(parts).strip()
    if not text:
        raise EmptyCompletionError(f"anthropic/{model} вернул пустой completion")
    return text


# ───────────── OpenAI / OpenRouter ─────────────


@_llm_retry
async def _call_openai(prompt: str, cfg: Config, model: str, key: str | None = None) -> str:
    client = _client_openai(cfg, key=key)

    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=cfg.llm.max_tokens,
        temperature=cfg.llm.temperature,
        extra_headers={
            "HTTP-Referer": "https://github.com/k0bad/AnkiForgeAi",
            "X-Title": "AnkiForgeAI",
        },
    )

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise EmptyCompletionError(f"{cfg.llm.provider}/{model} вернул пустой completion")
    return cast(str, content.strip())


# ───────────── JSON parsing ─────────────


def _parse_json(text: str) -> Any:
    """Распарсить JSON, отрезав markdown code fences если есть."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as e:
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start != -1 and end != -1 and end > start:
            return json.loads(stripped[start : end + 1])
        raise ValueError(f"LLM вернул невалидный JSON: {text[:500]!r}") from e
