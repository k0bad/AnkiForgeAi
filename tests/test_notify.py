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
    TagsConfig,
    TTSConfig,
)
from ankicards.notify import dispatch
from ankicards.notify.webhook import WebhookNotifier, format_report


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
