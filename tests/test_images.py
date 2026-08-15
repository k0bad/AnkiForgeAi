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
from ankicards.models import POS, Card


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
    assert captured["params"]["order_by"] == "relevant"


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
    assert captured["params"]["category"] == "photograph"


async def test_fallback_used_when_primary_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def empty(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        calls.append("unsplash")
        return []

    async def hit(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        calls.append("pexels")
        return [{"url": "https://img/x.jpg"}]

    monkeypatch.setitem(images_module._PROVIDERS, "unsplash", empty)
    monkeypatch.setitem(images_module._PROVIDERS, "pexels", hit)
    cfg = _make_config(provider="unsplash", fallback_providers=["pexels"])

    results = await images_module.search_images("hus", cfg, count=1)

    assert calls == ["unsplash", "pexels"]
    assert results == [{"url": "https://img/x.jpg"}]


async def test_fallback_used_when_primary_raises_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def forbidden(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        calls.append("unsplash")
        request = httpx.Request("GET", "https://api.unsplash.com/search/photos")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("403", request=request, response=response)

    async def hit(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        calls.append("pexels")
        return [{"url": "https://img/y.jpg"}]

    monkeypatch.setitem(images_module._PROVIDERS, "unsplash", forbidden)
    monkeypatch.setitem(images_module._PROVIDERS, "pexels", hit)
    cfg = _make_config(provider="unsplash", fallback_providers=["pexels"])

    results = await images_module.search_images("hus", cfg, count=1)

    assert calls == ["unsplash", "pexels"]
    assert results[0]["url"] == "https://img/y.jpg"


async def test_fallback_used_when_primary_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отсутствующий ключ у fallback-провайдера не роняет всю цепочку."""
    calls: list[str] = []

    async def missing_key(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        calls.append("unsplash")
        raise RuntimeError("UNSPLASH_ACCESS_KEY не задан в .env")

    async def hit(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        calls.append("openverse")
        return [{"url": "https://img/z.jpg"}]

    monkeypatch.setitem(images_module._PROVIDERS, "unsplash", missing_key)
    monkeypatch.setitem(images_module._PROVIDERS, "openverse", hit)
    cfg = _make_config(provider="unsplash", fallback_providers=["openverse"])

    results = await images_module.search_images("hus", cfg, count=1)

    assert calls == ["unsplash", "openverse"]
    assert results[0]["url"] == "https://img/z.jpg"


async def test_all_providers_fail_returns_empty_list_when_fallback_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def empty(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        return []

    monkeypatch.setitem(images_module._PROVIDERS, "unsplash", empty)
    monkeypatch.setitem(images_module._PROVIDERS, "openverse", empty)
    cfg = _make_config(provider="unsplash", fallback_providers=["openverse"])

    results = await images_module.search_images("hus", cfg, count=1)

    assert results == []


async def test_last_provider_error_does_not_raise_when_fallback_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Даже ошибка последнего провайдера в цепочке не пробрасывается — карточка без картинки."""

    async def broken(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        raise RuntimeError("PEXELS_API_KEY не задан в .env")

    monkeypatch.setitem(images_module._PROVIDERS, "pexels", broken)
    cfg = _make_config(provider="unsplash", fallback_providers=["pexels"])

    async def missing_key(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        raise RuntimeError("UNSPLASH_ACCESS_KEY не задан в .env")

    monkeypatch.setitem(images_module._PROVIDERS, "unsplash", missing_key)

    results = await images_module.search_images("hus", cfg, count=1)

    assert results == []


async def test_unknown_fallback_provider_raises() -> None:
    cfg = _make_config(provider="unsplash", fallback_providers=["flickr"])
    with pytest.raises(ValueError, match="Неизвестный провайдер"):
        await images_module.search_images("hus", cfg)


async def _fake_search(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Подменяет search_images, чтобы поймать query без реального HTTP-вызова."""
    captured: dict[str, str] = {}

    async def fake(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
        captured["query"] = query
        return []

    monkeypatch.setattr(images_module, "search_images", fake)
    return captured


async def test_attach_image_uses_image_query_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = await _fake_search(monkeypatch)
    cfg = _make_config()
    card = Card(word="hus", pos=POS.NOUN, translation="дом", image_query="house")

    await images_module.attach_image(card, cfg, auto_pick=True)

    assert captured["query"] == "house"


async def test_attach_image_falls_back_to_word_without_image_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = await _fake_search(monkeypatch)
    cfg = _make_config()
    card = Card(word="hus", pos=POS.NOUN, translation="дом")

    await images_module.attach_image(card, cfg, auto_pick=True)

    assert captured["query"] == "hus"
