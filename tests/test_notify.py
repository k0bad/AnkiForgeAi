"""Тесты для notify: format_report, WebhookNotifier и dispatch с фейковым HTTP-транспортом."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from ankicards.config import (
    AnkiConfig,
    Config,
    DedupeConfig,
    EnrichConfig,
    ImagesConfig,
    IngestConfig,
    LLMConfig,
    LoggingConfig,
    NotificationConfig,
    PathsConfig,
    ReviewConfig,
    Secrets,
    TagsConfig,
    TTSConfig,
)
from ankicards.notify import dispatch
from ankicards.notify.format import format_report
from ankicards.notify.telegram import API_URL, TelegramNotifier
from ankicards.notify.webhook import WebhookNotifier


def _report(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "date": "2026-08-11",
        "generated_at": "2026-08-11 09:00 UTC",
        "day": "tuesday",
        "topic": "mat",
        "label": "еда",
        "level": "A2",
        "count": 1,
        "new_words": [{"word": "brød", "translation": "хлеб", "pos": "noun"}],
        "stats": {"new": 1, "review": 0, "merged": 0, "enriched": 1, "audio": 1, "errors": 0},
        "needs_review": 0,
        "pushed": 1,
        "push_error": None,
    }
    base.update(overrides)
    return base


def _make_config(tmp_path: Path, notifications: list[NotificationConfig]) -> Config:
    return Config(
        language="nb",
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
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(),
        logging=LoggingConfig(),
        tags=TagsConfig(),
        notifications=notifications,
    )


# ───────────── format_report ─────────────


def test_format_report_includes_new_words() -> None:
    text = format_report(_report())
    assert "brød" in text
    assert "хлеб" in text
    assert "еда (mat)" in text


def test_format_report_empty_new_words() -> None:
    text = format_report(_report(new_words=[]))
    assert "Не удалось сгенерировать слова" in text


def test_format_report_push_error() -> None:
    text = format_report(_report(pushed=0, push_error="Anki недоступен: timeout"))
    assert "Anki недоступен: timeout" in text


def test_format_report_images_breakdown() -> None:
    stats = {
        "new": 1,
        "review": 0,
        "merged": 0,
        "enriched": 1,
        "audio": 1,
        "errors": 0,
        "images": {"found": 4, "skipped_not_noun": 2, "failed_no_result": 1},
    }
    text = format_report(_report(stats=stats))
    assert "🖼️ Картинки: 4 найдено, 2 пропущено (не существительное), " in text
    assert "1 не найдено (провайдер)" in text


def test_format_report_omits_images_line_when_images_disabled() -> None:
    # pipeline.enrich_and_generate_media не кладёт "images" в stats вовсе, если
    # cfg.images.enabled=false — тот же случай воспроизводим тут явно.
    text = format_report(_report())
    assert "🖼️" not in text


# ───────────── WebhookNotifier ─────────────


def _mock_transport(handler: Any) -> Any:
    """Патчит WebhookNotifier._post так, чтобы httpx слал запросы в MockTransport
    вместо реальной сети (обходя retry-декоратор — тестируем не ретраи, а payload)."""
    transport = httpx.MockTransport(handler)

    async def fake_post(self: WebhookNotifier, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(self.url, json=payload)
            response.raise_for_status()

    return fake_post


async def test_webhook_notifier_posts_text_payload_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(WebhookNotifier, "_post", _mock_transport(handler))

    notifier = WebhookNotifier(url="http://example.test/hook")
    await notifier.send(_report())

    assert captured["url"] == "http://example.test/hook"
    body = json.loads(captured["json"])
    assert set(body) == {"text"}
    assert "brød" in body["text"]


async def test_webhook_notifier_posts_raw_report_with_json_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(WebhookNotifier, "_post", _mock_transport(handler))

    notifier = WebhookNotifier(url="http://example.test/hook", format="json")
    report = _report()
    await notifier.send(report)

    body = json.loads(captured["json"])
    assert body == report


async def test_webhook_notifier_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        WebhookNotifier, "_post", _mock_transport(lambda request: httpx.Response(500))
    )

    notifier = WebhookNotifier(url="http://example.test/hook")
    with pytest.raises(httpx.HTTPStatusError):
        await notifier.send(_report())


# ───────────── TelegramNotifier ─────────────


def _mock_telegram_transport(handler: Any) -> Any:
    transport = httpx.MockTransport(handler)

    async def fake_post(self: TelegramNotifier, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(API_URL.format(token=self.token), json=payload)
            response.raise_for_status()

    return fake_post


async def test_telegram_notifier_posts_expected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(TelegramNotifier, "_post", _mock_telegram_transport(handler))

    notifier = TelegramNotifier(token="123:ABC", chat_id="42")
    await notifier.send(_report())

    assert captured["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    body = json.loads(captured["json"])
    assert body["chat_id"] == "42"
    assert body["parse_mode"] == "Markdown"
    assert "brød" in body["text"]
    assert "message_thread_id" not in body


async def test_telegram_notifier_includes_message_thread_id_when_topic_id_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(TelegramNotifier, "_post", _mock_telegram_transport(handler))

    notifier = TelegramNotifier(token="123:ABC", chat_id="42", topic_id=115802)
    await notifier.send(_report())

    body = json.loads(captured["json"])
    assert body["message_thread_id"] == 115802


async def test_telegram_notifier_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        TelegramNotifier, "_post", _mock_telegram_transport(lambda request: httpx.Response(500))
    )

    notifier = TelegramNotifier(token="123:ABC", chat_id="42")
    with pytest.raises(httpx.HTTPStatusError):
        await notifier.send(_report())


def test_telegram_from_entry_prefers_env_token_over_config_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ankicards.notify.telegram.get_secrets",
        lambda: Secrets(notify_telegram_token="from-env"),
    )
    entry = NotificationConfig(type="telegram", token="from-config", chat_id="42")

    notifier = TelegramNotifier.from_entry(entry)

    assert notifier is not None
    assert notifier.token == "from-env"


def test_telegram_from_entry_falls_back_to_config_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ankicards.notify.telegram.get_secrets", lambda: Secrets(notify_telegram_token="")
    )
    entry = NotificationConfig(type="telegram", token="from-config", chat_id="42")

    notifier = TelegramNotifier.from_entry(entry)

    assert notifier is not None
    assert notifier.token == "from-config"


def test_telegram_from_entry_none_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ankicards.notify.telegram.get_secrets", lambda: Secrets(notify_telegram_token="")
    )
    entry = NotificationConfig(type="telegram", token="", chat_id="42")

    assert TelegramNotifier.from_entry(entry) is None


def test_telegram_from_entry_none_without_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ankicards.notify.telegram.get_secrets", lambda: Secrets(notify_telegram_token="")
    )
    entry = NotificationConfig(type="telegram", token="from-config", chat_id="")

    assert TelegramNotifier.from_entry(entry) is None


# ───────────── dispatch ─────────────


async def test_dispatch_skips_disabled_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_send(self: WebhookNotifier, report: dict[str, Any]) -> None:
        calls.append(self.url)

    monkeypatch.setattr(WebhookNotifier, "send", fake_send)

    cfg = _make_config(
        tmp_path,
        notifications=[
            NotificationConfig(type="webhook", enabled=False, url="http://disabled.test"),
            NotificationConfig(type="webhook", enabled=True, url="http://enabled.test"),
        ],
    )

    await dispatch(_report(), cfg)

    assert calls == ["http://enabled.test"]


async def test_dispatch_one_channel_failure_does_not_block_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_send(self: WebhookNotifier, report: dict[str, Any]) -> None:
        calls.append(self.url)
        if self.url == "http://broken.test":
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(WebhookNotifier, "send", fake_send)

    cfg = _make_config(
        tmp_path,
        notifications=[
            NotificationConfig(type="webhook", enabled=True, url="http://broken.test"),
            NotificationConfig(type="webhook", enabled=True, url="http://ok.test"),
        ],
    )

    await dispatch(_report(), cfg)

    assert calls == ["http://broken.test", "http://ok.test"]


async def test_dispatch_passes_format_from_config_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    async def fake_send(self: WebhookNotifier, report: dict[str, Any]) -> None:
        captured.append(self.format)

    monkeypatch.setattr(WebhookNotifier, "send", fake_send)

    cfg = _make_config(
        tmp_path,
        notifications=[
            NotificationConfig(
                type="webhook", enabled=True, url="http://hermes.test", format="json"
            ),
        ],
    )

    await dispatch(_report(), cfg)

    assert captured == ["json"]


async def test_dispatch_unknown_backend_type_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_send(self: WebhookNotifier, report: dict[str, Any]) -> None:
        raise AssertionError("should not be called")

    monkeypatch.setattr(WebhookNotifier, "send", fake_send)

    cfg = _make_config(
        tmp_path,
        notifications=[NotificationConfig(type="slack", enabled=True, url="http://x.test")],
    )

    await dispatch(_report(), cfg)  # не должно бросить исключение


async def test_dispatch_fans_out_to_webhook_and_telegram_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_webhook_send(self: WebhookNotifier, report: dict[str, Any]) -> None:
        calls.append(f"webhook:{self.url}")

    async def fake_telegram_send(self: TelegramNotifier, report: dict[str, Any]) -> None:
        calls.append(f"telegram:{self.chat_id}")

    monkeypatch.setattr(WebhookNotifier, "send", fake_webhook_send)
    monkeypatch.setattr(TelegramNotifier, "send", fake_telegram_send)
    monkeypatch.setattr(
        "ankicards.notify.telegram.get_secrets", lambda: Secrets(notify_telegram_token="")
    )

    cfg = _make_config(
        tmp_path,
        notifications=[
            NotificationConfig(type="webhook", enabled=True, url="http://n8n.test"),
            NotificationConfig(
                type="telegram", enabled=True, token="123:ABC", chat_id="42"
            ),
        ],
    )

    await dispatch(_report(), cfg)

    assert set(calls) == {"webhook:http://n8n.test", "telegram:42"}


async def test_dispatch_skips_telegram_channel_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_send(self: TelegramNotifier, report: dict[str, Any]) -> None:
        raise AssertionError("should not be called")

    monkeypatch.setattr(TelegramNotifier, "send", fake_send)
    monkeypatch.setattr(
        "ankicards.notify.telegram.get_secrets", lambda: Secrets(notify_telegram_token="")
    )

    cfg = _make_config(
        tmp_path,
        notifications=[NotificationConfig(type="telegram", enabled=True, token="", chat_id="42")],
    )

    await dispatch(_report(), cfg)  # не должно бросить исключение (лог notify.no_token)
