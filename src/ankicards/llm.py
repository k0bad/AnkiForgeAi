"""LLM calls with provider selection: Anthropic Claude, OpenAI-compatible (OpenRouter),
or the local Claude Code CLI.

Provider выбирается из config.yaml -> llm.provider:
- "anthropic"  — использует Anthropic SDK + ANTHROPIC_API_KEY
- "openrouter" — OpenAI-compatible API через openai SDK (OpenRouter / любые OpenAI-совместимые)
- "claude_cli" — headless-вызов локального `claude` CLI (`claude -p`, см. _call_claude_cli).
  Не требует своего ключа в .env — использует уже существующий `claude login` (подписку
  Pro/Max или ANTHROPIC_API_KEY, настроенный на уровне самого Claude Code). Расходует
  квоту/rate-limit этого логина совместно с интерактивной работой в Claude Code — не
  отдельный бюджет. Плохо подходит для частого/объёмного cron именно по этой причине,
  ок для ручной/нечастой генерации у тех, кто не хочет заводить отдельный API-ключ.

Промпты загружаются из prompts/*.md с подстановкой {placeholders} через str.format.
"""

from __future__ import annotations

import asyncio
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
    elif provider == "claude_cli":
        return await _call_claude_cli(prompt, cfg, model)
    else:
        raise ValueError(
            f"Неизвестный LLM провайдер: {provider!r}. "
            "Ожидается: 'anthropic', 'openrouter' или 'claude_cli'"
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
    text = "".join(parts).strip()
    if not text:
        raise EmptyCompletionError(f"anthropic/{model} вернул пустой completion")
    return text


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
    if not content or not content.strip():
        raise EmptyCompletionError(f"{cfg.llm.provider}/{model} вернул пустой completion")
    return cast(str, content.strip())


# ───────────── Claude Code CLI (headless, без ключа) ─────────────


@_llm_retry
async def _call_claude_cli(prompt: str, cfg: Config, model: str) -> str:
    """Вызвать `claude -p` подпроцессом и вернуть текст из envelope.result.

    Намеренно без --bare: --bare требует ANTHROPIC_API_KEY и никогда не читает
    OAuth/подписку (см. `claude --help`), а значит убивает смысл этого провайдера —
    у пользователя с подпиской не будет ключа вовсе. Платим за это более тяжёлым
    системным промптом на каждый вызов (полный CLAUDE.md/hooks/tools discovery),
    поэтому этот провайдер — для нечастых ручных вызовов, не для объёмного cron.
    --tools "" + --permission-mode dontAsk гарантируют, что модель не попытается
    дёрнуть инструмент и не зависнет в ожидании подтверждения без TTY.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude",
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "json",
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "claude CLI не найден в PATH. Установи Claude Code "
            "(https://claude.com/claude-code) или используй другой llm.provider."
        ) from e

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=cfg.llm.claude_cli_timeout_seconds
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise  # транзиентно для _is_transient — ретраится

    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI завершился с кодом {proc.returncode}: "
            f"{stderr.decode(errors='replace')[:500]}"
        )

    try:
        envelope: dict[str, Any] = json.loads(stdout.decode())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude CLI вернул невалидный JSON-конверт: {stdout[:500]!r}") from e

    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI ошибка: {str(envelope.get('result', ''))[:500]}")

    text = str(envelope.get("result") or "").strip()
    if not text:
        raise EmptyCompletionError(f"claude_cli/{model} вернул пустой completion")
    return text


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
