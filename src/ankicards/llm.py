"""LLM calls with provider selection: Anthropic Claude or OpenAI-compatible (OpenRouter).

Provider выбирается из config.yaml -> llm.provider:
- "anthropic"  — использует Anthropic SDK + ANTHROPIC_API_KEY
- "openrouter" — OpenAI-compatible API через openai SDK (OpenRouter / любые OpenAI-совместимые)

Промпты загружаются из prompts/*.md с подстановкой {placeholders} через str.format.
"""

from __future__ import annotations

import json
import os
from typing import Any

from tenacity import RetryCallState, retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import Config, get_config, get_secrets
from .log import get_logger

logger = get_logger(__name__)


def _is_transient(exc: BaseException) -> bool:
    """Ретраить сетевые/API-ошибки провайдера, но не наши ValueError/RuntimeError
    (невалидный ключ, невалидный JSON) — повторный вызов их не исправит."""
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


def _client_openai(cfg: Config) -> Any:
    """Создать OpenAI-клиент."""
    from openai import AsyncOpenAI

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


async def call_text(prompt: str, cfg: Config | None = None, model: str | None = None) -> str:
    """Вызвать LLM и вернуть текстовый ответ.

    Провайдер выбирается из cfg.llm.provider. `model` переопределяет cfg.llm.model
    только для этого вызова (например, дешёвая модель под простую бинарную задачу
    типа dedupe.judge_review) — провайдер и его ключ остаются те же.
    """
    cfg = cfg or get_config()
    provider = cfg.llm.provider
    model = model or cfg.llm.model

    if provider == "anthropic":
        return await _call_anthropic(prompt, cfg, model)
    elif provider == "openrouter":
        return await _call_openai(prompt, cfg, model)
    else:
        raise ValueError(
            f"Неизвестный LLM провайдер: {provider!r}. Ожидается: 'anthropic' или 'openrouter'"
        )


async def call_json(prompt: str, cfg: Config | None = None) -> Any:
    """Вызвать LLM и распарсить ответ как JSON.

    Устойчив к обёрткам в ```json ... ``` code fences.
    """
    raw = await call_text(prompt, cfg=cfg)
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
    return "".join(parts).strip()


# ───────────── OpenAI / OpenRouter ─────────────


@_llm_retry
async def _call_openai(prompt: str, cfg: Config, model: str) -> str:
    client = _client_openai(cfg)

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
    return content.strip() if content else ""


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
