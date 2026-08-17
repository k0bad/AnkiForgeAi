"""Тесты для llm._parse_json (устойчивость к markdown-обёрткам и мусору вокруг JSON)
и для retry на пустом completion от провайдера (issue #28: раньше пустой content
тихо превращался в "" и падал уже вне retry-обёртки как ValueError)."""

from __future__ import annotations

import asyncio
import json

import pytest

from ankicards import llm
from ankicards.config import (
    AnkiConfig,
    Config,
    DedupeConfig,
    EnrichConfig,
    ImagesConfig,
    IngestConfig,
    LLMConfig,
    LoggingConfig,
    PathsConfig,
    ReviewConfig,
    TagsConfig,
    TTSConfig,
)
from ankicards.llm import EmptyCompletionError, _parse_json


def test_parse_json_plain_array() -> None:
    assert _parse_json('[{"a": 1}]') == [{"a": 1}]


def test_parse_json_json_fence() -> None:
    text = '```json\n[{"a": 1}, {"a": 2}]\n```'
    assert _parse_json(text) == [{"a": 1}, {"a": 2}]


def test_parse_json_bare_fence() -> None:
    text = '```\n[{"a": 1}]\n```'
    assert _parse_json(text) == [{"a": 1}]


def test_parse_json_recovers_array_from_surrounding_prose() -> None:
    text = 'Конечно, вот результат:\n[{"word": "hus"}]\nНадеюсь, помогло!'
    assert _parse_json(text) == [{"word": "hus"}]


def test_parse_json_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError, match="невалидный JSON"):
        _parse_json("это вообще не JSON")


def _make_config(tmp_path) -> Config:  # type: ignore[no-untyped-def]
    return Config(
        paths=PathsConfig(
            db=tmp_path / "test.db",
            logs_dir=tmp_path / "logs",
            audio_dir=tmp_path / "audio",
            images_dir=tmp_path / "images",
            prompts_dir=tmp_path / "prompts",
        ),
        anki=AnkiConfig(),
        dedupe=DedupeConfig(),
        ingest=IngestConfig(),
        llm=LLMConfig(provider="openrouter"),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """Отдаёт по одному content из `contents` за вызов — имитирует провайдера,
    который иногда возвращает пустой completion при HTTP 200."""

    def __init__(self, contents: list[str | None]) -> None:
        self._contents = list(contents)
        self.call_count = 0

    async def create(self, **kwargs: object) -> _FakeCompletion:
        content = self._contents[self.call_count]
        self.call_count += 1
        return _FakeCompletion(content)


class _FakeOpenAIClient:
    def __init__(self, contents: list[str | None]) -> None:
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(contents)})()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """tenacity ждёт 2-10с экспоненциально между попытками — не ждём это по-настоящему."""

    async def _instant(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


async def test_call_openai_retries_past_transient_empty_completion(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    """Первые два вызова возвращают пустой content (глюк провайдера), третий —
    настоящий ответ. Раньше первый же пустой content тихо становился "" и уходил
    в вызывающий код как валидный (но бессмысленный) результат."""
    cfg = _make_config(tmp_path)
    fake_client = _FakeOpenAIClient(["", None, "настоящий ответ"])
    monkeypatch.setattr(llm, "_client_openai", lambda cfg: fake_client)

    result = await llm._call_openai("промпт", cfg, "some-model")

    assert result == "настоящий ответ"
    assert fake_client.chat.completions.call_count == 3


async def test_call_openai_raises_empty_completion_error_after_exhausting_retries(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    """Если провайдер отдаёт пустой content на всех попытках — падаем понятной
    EmptyCompletionError, а не тихо возвращаем "" вызывающему коду."""
    cfg = _make_config(tmp_path)
    fake_client = _FakeOpenAIClient(["", "", ""])
    monkeypatch.setattr(llm, "_client_openai", lambda cfg: fake_client)

    with pytest.raises(EmptyCompletionError):
        await llm._call_openai("промпт", cfg, "some-model")

    assert fake_client.chat.completions.call_count == 3


class _FakeProcess:
    """Имитирует asyncio.subprocess.Process для _call_claude_cli."""

    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> None:
        return None


def _envelope(**kwargs: object) -> bytes:
    return json.dumps(kwargs).encode()


async def test_call_claude_cli_parses_result_field(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _make_config(tmp_path)
    fake_proc = _FakeProcess(_envelope(is_error=False, result="готовый ответ"))

    async def fake_exec(*args: object, **kwargs: object) -> _FakeProcess:
        return fake_proc

    monkeypatch.setattr(llm.asyncio, "create_subprocess_exec", fake_exec)

    result = await llm._call_claude_cli("промпт", cfg, "sonnet")

    assert result == "готовый ответ"


async def test_call_claude_cli_raises_on_is_error(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _make_config(tmp_path)
    fake_proc = _FakeProcess(_envelope(is_error=True, result="что-то сломалось"))

    async def fake_exec(*args: object, **kwargs: object) -> _FakeProcess:
        return fake_proc

    monkeypatch.setattr(llm.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="что-то сломалось"):
        await llm._call_claude_cli("промпт", cfg, "sonnet")


async def test_call_claude_cli_nonzero_exit_raises_runtime_error(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _make_config(tmp_path)
    fake_proc = _FakeProcess(b"", stderr=b"auth error", returncode=1)

    async def fake_exec(*args: object, **kwargs: object) -> _FakeProcess:
        return fake_proc

    monkeypatch.setattr(llm.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="завершился с кодом 1"):
        await llm._call_claude_cli("промпт", cfg, "sonnet")


async def test_call_claude_cli_missing_binary_raises_runtime_error(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _make_config(tmp_path)

    async def fake_exec(*args: object, **kwargs: object) -> _FakeProcess:
        raise FileNotFoundError()

    monkeypatch.setattr(llm.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="не найден в PATH"):
        await llm._call_claude_cli("промпт", cfg, "sonnet")


async def test_call_claude_cli_empty_result_raises_empty_completion_error(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _make_config(tmp_path)
    fake_proc = _FakeProcess(_envelope(is_error=False, result=""))

    async def fake_exec(*args: object, **kwargs: object) -> _FakeProcess:
        return fake_proc

    monkeypatch.setattr(llm.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(EmptyCompletionError):
        await llm._call_claude_cli("промпт", cfg, "sonnet")
