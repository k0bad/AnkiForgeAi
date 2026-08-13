"""Тесты для media.images: диспетчеризация по cfg.images.provider и парсинг ответов."""

from __future__ import annotations

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
    PathsConfig,
    ReviewConfig,
    Secrets,
    TagsConfig,
    TTSConfig,
)
from ankicards.media import images as images_module


def _patch_get(monkeypatch: pytest.MonkeyPatch, response_json: dict[str, Any]) -> dict[str, Any]:
    """Подменяет httpx.AsyncClient.get фейком, который отдаёт response_json
    и запоминает url/params/headers последнего вызова."""
    captured: dict[str, Any] = {}

    async def fake_get(
        self: httpx.AsyncClient,
        url: str,
        *,
        params: Any = None,
        headers: Any = None,
        **kwargs: Any,
    ) -> httpx.Response:
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return httpx.Response(200, json=response_json, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    return captured


def _patch_secrets(monkeypatch: pytest.MonkeyPatch, **keys: str) -> None:
    # model_construct, не Secrets(**keys): BaseSettings иначе подмешивает реальные .env/os.environ
    fake = Secrets.model_construct(**keys)
    monkeypatch.setattr(images_module, "get_secrets", lambda: fake)


def _make_config(**images_overrides: Any) -> Config:
    return Config(
        language="nb",
        paths=PathsConfig(
            db=Path("test.db"),
            logs_dir=Path("logs"),
            audio_dir=Path("audio"),
            images_dir=Path("images"),
            prompts_dir=Path("prompts"),
        ),
        anki=AnkiConfig(),
        dedupe=DedupeConfig(),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(**images_overrides),
        review=ReviewConfig(),
        enrich=EnrichConfig(),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


async def test_unknown_provider_raises() -> None:
    cfg = _make_config(provider="flickr")
    with pytest.raises(ValueError, match="Неизвестный провайдер"):
        await images_module.search_images("hus", cfg)


async def test_unsplash_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_secrets(monkeypatch)  # все ключи пустые
    cfg = _make_config(provider="unsplash")
    with pytest.raises(RuntimeError, match="UNSPLASH_ACCESS_KEY"):
        await images_module.search_images("hus", cfg)


async def test_unsplash_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_secrets(monkeypatch, unsplash_access_key="u-key")
    captured = _patch_get(
        monkeypatch,
        {
            "results": [
                {
                    "urls": {"regular": "https://img/full.jpg", "thumb": "https://img/thumb.jpg"},
                    "user": {"name": "Anna", "links": {"html": "https://unsplash.com/@anna"}},
                    "links": {"html": "https://unsplash.com/photos/1"},
                }
            ]
        },
    )
    cfg = _make_config(provider="unsplash", per_page=5)

    results = await images_module.search_images("hus", cfg, count=1)

    assert results == [
        {
            "url": "https://img/full.jpg",
            "thumb": "https://img/thumb.jpg",
            "author": "Anna",
            "author_url": "https://unsplash.com/@anna",
            "html": "https://unsplash.com/photos/1",
        }
    ]
    assert captured["url"] == images_module.UNSPLASH_SEARCH_URL
    assert captured["headers"]["Authorization"] == "Client-ID u-key"


async def test_pexels_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_secrets(monkeypatch, pexels_api_key="p-key")
    captured = _patch_get(
        monkeypatch,
        {
            "photos": [
                {
                    "src": {"large": "https://img/large.jpg", "tiny": "https://img/tiny.jpg"},
                    "photographer": "Bob",
                    "photographer_url": "https://pexels.com/@bob",
                    "url": "https://pexels.com/photo/1",
                }
            ]
        },
    )
    cfg = _make_config(provider="pexels", per_page=5)

    results = await images_module.search_images("hus", cfg, count=1)

    assert results[0]["url"] == "https://img/large.jpg"
    assert results[0]["author"] == "Bob"
    assert captured["url"] == images_module.PEXELS_SEARCH_URL
    assert captured["headers"]["Authorization"] == "p-key"


async def test_pixabay_parses_results_and_min_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_secrets(monkeypatch, pixabay_api_key="pb-key")
    captured = _patch_get(
        monkeypatch,
        {
            "hits": [
                {
                    "largeImageURL": "https://img/large.jpg",
                    "previewURL": "https://img/preview.jpg",
                    "user": "Carl",
                    "user_id": 42,
                    "pageURL": "https://pixabay.com/photos/1",
                }
            ]
        },
    )
    cfg = _make_config(provider="pixabay", per_page=5)

    results = await images_module.search_images("hus", cfg, count=1)

    assert results[0]["url"] == "https://img/large.jpg"
    assert results[0]["author_url"] == "https://pixabay.com/users/Carl-42/"
    # Pixabay требует per_page >= 3, даже если запросили меньше
    assert captured["params"]["per_page"] == 3


async def test_openverse_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_secrets(monkeypatch)  # ни один ключ не задан
    captured = _patch_get(
        monkeypatch,
        {
            "results": [
                {
                    "url": "https://img/full.jpg",
                    "thumbnail": "https://img/thumb.jpg",
                    "creator": "Dana",
                    "creator_url": "https://openverse.org/@dana",
                    "foreign_landing_url": "https://openverse.org/image/1",
                }
            ]
        },
    )
    cfg = _make_config(provider="openverse", per_page=5)

    results = await images_module.search_images("hus", cfg, count=1)

    assert results[0]["author"] == "Dana"
    assert captured["url"] == images_module.OPENVERSE_SEARCH_URL
