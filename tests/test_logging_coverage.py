"""Проверяет, что реальные failure-пути в enrich/media/anki-модулях действительно
эмиттят structlog-событие (warning/error), а не только пишут в audit_log или падают
молча (issue #32). Прошлые силентные баги (#7, #11, #29, #39) как раз были такого
рода — код формально "обрабатывал" ошибку (try/except), но трасса об этом никуда
не долетала. `structlog.testing.capture_logs()` подменяет процессоры на время теста
и ловит события независимо от реальной конфигурации в log.py (JSON/console)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import structlog

from ankicards import pipeline
from ankicards.anki.connect import AnkiConnect, AnkiConnectError
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
from ankicards.db import Database
from ankicards.media import images as images_module
from ankicards.models import POS, Card


def _make_config(tmp_path: Path, images: dict[str, object] | None = None) -> Config:
    images_kwargs: dict[str, object] = {"enabled": False}
    images_kwargs.update(images or {})
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
        images=ImagesConfig(**images_kwargs),
        review=ReviewConfig(),
        enrich=EnrichConfig(grammar=False, examples=False, pronunciation=False),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


def _card(word: str) -> Card:
    return Card(language="nb", word=word, pos=POS.NOUN, translation="перевод")


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


def _events(cap: list[dict], name: str) -> list[dict]:
    return [e for e in cap if e.get("event") == name]


# ───────────── anki/connect.py: AnkiConnect._call() ─────────────


async def test_anki_call_http_error_logs_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anki = AnkiConnect(_make_config(tmp_path))

    async def _boom(payload: dict) -> None:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(anki, "_post", _boom)

    with structlog.testing.capture_logs() as cap, pytest.raises(AnkiConnectError):
        await anki._call("deckNames")

    matches = _events(cap, "anki.http_error")
    assert len(matches) == 1
    assert matches[0]["log_level"] == "warning"


async def test_anki_call_malformed_response_logs_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anki = AnkiConnect(_make_config(tmp_path))

    async def _malformed(payload: dict) -> dict:
        return {"unexpected": "shape"}

    monkeypatch.setattr(anki, "_post", _malformed)

    with structlog.testing.capture_logs() as cap, pytest.raises(AnkiConnectError):
        await anki._call("deckNames")

    matches = _events(cap, "anki.malformed_response")
    assert len(matches) == 1
    assert matches[0]["log_level"] == "warning"


async def test_anki_call_error_field_logs_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anki = AnkiConnect(_make_config(tmp_path))

    async def _errored(payload: dict) -> dict:
        return {"result": None, "error": "collection is not available"}

    monkeypatch.setattr(anki, "_post", _errored)

    with structlog.testing.capture_logs() as cap, pytest.raises(AnkiConnectError):
        await anki._call("deckNames")

    matches = _events(cap, "anki.error")
    assert len(matches) == 1
    assert matches[0]["log_level"] == "warning"


# ───────────── media/images.py ─────────────


async def test_search_images_provider_failure_logs_warning_with_fallback(
    tmp_path: Path,
) -> None:
    async def forbidden(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        request = httpx.Request("GET", "https://api.unsplash.com/search/photos")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("403", request=request, response=response)

    async def hit(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        return [{"url": "https://img/x.jpg"}]

    orig_unsplash = images_module._PROVIDERS["unsplash"]
    orig_pexels = images_module._PROVIDERS["pexels"]
    images_module._PROVIDERS["unsplash"] = forbidden
    images_module._PROVIDERS["pexels"] = hit
    try:
        cfg = _make_config(
            tmp_path, images={"provider": "unsplash", "fallback_providers": ["pexels"]}
        )
        with structlog.testing.capture_logs() as cap:
            results = await images_module.search_images("hus", cfg, count=1)
    finally:
        images_module._PROVIDERS["unsplash"] = orig_unsplash
        images_module._PROVIDERS["pexels"] = orig_pexels

    assert results == [{"url": "https://img/x.jpg"}]
    matches = _events(cap, "images.provider_failed")
    assert len(matches) == 1
    assert matches[0]["log_level"] == "warning"
    assert matches[0]["provider"] == "unsplash"


async def test_attach_image_empty_search_logs_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def empty(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(images_module, "search_images", empty)
    cfg = _make_config(tmp_path, images={"enabled": True})
    card = _card("hus")

    with structlog.testing.capture_logs() as cap:
        await images_module.attach_image(card, cfg, auto_pick=True)

    matches = _events(cap, "images.search_empty")
    assert len(matches) == 1
    assert matches[0]["log_level"] == "warning"
    assert matches[0]["card_id"] == card.id


# ───────────── pipeline.py: оркестрация enrich/media (issue #7, #11, #39) ─────────────


async def test_enrich_stage_batch_failure_logs_warning(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enrich_failed должен быть виден в live-трассе, не только в audit_log —
    именно этого не хватало для диагностики бага #7/#39 в моменте, а не постфактум."""

    async def _boom(cards: list[Card]) -> list[Card]:
        raise RuntimeError("LLM недоступен")

    monkeypatch.setattr(pipeline, "enrich_grammar_batch", _boom)
    cfg = _make_config(tmp_path)
    cfg.enrich.grammar = True

    async def _noop_audio(card: Card, cfg: Config) -> Card:
        return card

    monkeypatch.setattr(pipeline, "generate_audio", _noop_audio)

    with structlog.testing.capture_logs() as cap:
        await pipeline.run_ingest_pipeline(
            [_card("hus")], db=db, cfg=cfg, auto_enrich=True, auto_media=False
        )

    matches = _events(cap, "enrich_failed")
    assert len(matches) == 1
    assert matches[0]["log_level"] == "warning"
    assert matches[0]["stage"] == "grammar"


async def test_audio_generation_failure_logs_warning(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(card: Card, cfg: Config) -> Card:
        raise RuntimeError("edge-tts недоступен")

    monkeypatch.setattr(pipeline, "generate_audio", _boom)
    cfg = _make_config(tmp_path)
    card = _card("hus")

    with structlog.testing.capture_logs() as cap:
        await pipeline.enrich_and_generate_media(
            [card], db=db, cfg=cfg, auto_enrich=False, auto_media=True
        )

    matches = _events(cap, "audio_failed")
    assert len(matches) == 1
    assert matches[0]["log_level"] == "warning"
    assert matches[0]["card_id"] == card.id


async def test_image_generation_failure_logs_warning(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _noop_audio(card: Card, cfg: Config) -> Card:
        return card

    async def _boom(card: Card, cfg: Config, auto_pick: bool = False) -> Card:
        raise RuntimeError("провайдер картинок недоступен")

    monkeypatch.setattr(pipeline, "generate_audio", _noop_audio)
    monkeypatch.setattr(pipeline, "attach_image", _boom)
    cfg = _make_config(tmp_path, images={"enabled": True})
    card = _card("hus")

    with structlog.testing.capture_logs() as cap:
        await pipeline.enrich_and_generate_media(
            [card], db=db, cfg=cfg, auto_enrich=False, auto_media=True
        )

    matches = _events(cap, "image_failed")
    assert len(matches) == 1
    assert matches[0]["log_level"] == "warning"
    assert matches[0]["card_id"] == card.id
