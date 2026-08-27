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
from ankicards.llm import ClaudeCliError, EmptyCompletionError, _parse_json


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
    monkeypatch.setattr(llm, "_client_openai", lambda cfg, **kw: fake_client)

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
    monkeypatch.setattr(llm, "_client_openai", lambda cfg, **kw: fake_client)

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

    with pytest.raises(ClaudeCliError, match="что-то сломалось"):
        await llm._call_claude_cli("промпт", cfg, "sonnet")


async def test_call_claude_cli_nonzero_exit_is_retryable_not_runtime_error(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ненулевой код возврата — транзиентный сбой, а не «повтор не поможет».

    RuntimeError исключён из ретраев (_is_transient), и раньше выход CLI с кодом 1
    попадал именно туда: один такой выход мгновенно ронял всю пачку карточек
    обратно в review, хотя следующая попытка сработала бы.
    """
    cfg = _make_config(tmp_path)
    fake_proc = _FakeProcess(b"", stderr=b"auth error", returncode=1)

    async def fake_exec(*args: object, **kwargs: object) -> _FakeProcess:
        return fake_proc

    monkeypatch.setattr(llm.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ClaudeCliError, match="завершился с кодом 1"):
        await llm._call_claude_cli("промпт", cfg, "sonnet")

    assert not isinstance(ClaudeCliError(), RuntimeError)
    assert llm._is_transient(ClaudeCliError())


async def test_call_claude_cli_nonzero_exit_reports_stdout_too(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При --output-format json причина ошибки уходит в конверт на stdout, а stderr
    остаётся пустым. Сообщение «завершился с кодом 1: » без единого слова причины
    ничего не давало для диагностики — теперь в него попадают оба потока."""
    cfg = _make_config(tmp_path)
    fake_proc = _FakeProcess(b'{"is_error":true,"result":"usage limit reached"}', returncode=1)

    async def fake_exec(*args: object, **kwargs: object) -> _FakeProcess:
        return fake_proc

    monkeypatch.setattr(llm.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ClaudeCliError, match="usage limit reached"):
        await llm._call_claude_cli("промпт", cfg, "sonnet")


async def test_call_claude_cli_retries_a_failed_exit_and_succeeds(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Полный путь через @_llm_retry: сорвавшийся вызов повторяется и второй
    заход отдаёт результат, вместо того чтобы обнулить enrichment всей пачки."""
    cfg = _make_config(tmp_path)
    responses = [
        _FakeProcess(b"", returncode=1),
        _FakeProcess(_envelope(is_error=False, result="со второй попытки")),
    ]
    calls = {"n": 0}

    async def fake_exec(*args: object, **kwargs: object) -> _FakeProcess:
        proc = responses[calls["n"]]
        calls["n"] += 1
        return proc

    monkeypatch.setattr(llm.asyncio, "create_subprocess_exec", fake_exec)

    assert await llm._call_claude_cli("промпт", cfg, "sonnet") == "со второй попытки"
    assert calls["n"] == 2


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


def test_parse_json_fence_followed_by_prose() -> None:
    """Реальный ответ claude_cli: блок с JSON, а сразу за ним разбор сложных слов.

    Закрывающая ``` оказывалась в середине текста, и невалидным считался весь
    ответ — падала вся пачка карточек разом, а не одна.
    """
    text = (
        "```json\n"
        '[{"id": 51, "pronunciation": "хьуле"}]\n'
        "```\n\n"
        "Примечания по нетривиальным словам:\n\n"
        "- **kjole** — `kj` = /ç/, передаётся как `хь`."
    )
    assert _parse_json(text) == [{"id": 51, "pronunciation": "хьуле"}]


def test_parse_json_merges_an_answer_split_into_several_blocks() -> None:
    """Ответ, разбитый на два блока с комментарием посередине, склеивается.

    json.loads видит здесь «Extra data» и падает; взять только первый блок
    значило бы молча потерять половину слов пачки.
    """
    text = (
        '```json\n[{"id": 1}, {"id": 2}]\n```\n\nДальше числительные:\n\n```json\n[{"id": 3}]\n```'
    )
    assert _parse_json(text) == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_parse_json_reads_objects_written_one_per_line() -> None:
    """JSONL вместо массива — для json.loads это тоже «Extra data»."""
    assert _parse_json('{"id": 1}\n{"id": 2}') == [{"id": 1}, {"id": 2}]


def test_parse_json_ignores_brackets_that_are_not_json() -> None:
    """Скобка из прозы (markdown-ссылка) не должна попасть в результат."""
    text = 'См. [документацию](http://example.com):\n[{"id": 1}]'
    assert _parse_json(text) == [{"id": 1}]


def test_parse_json_keeps_a_single_object_an_object() -> None:
    """Одиночный объект не заворачивается в список: dedupe.judge_review ждёт dict."""
    assert _parse_json('Вот вердикт:\n{"duplicate": true}') == {"duplicate": True}
