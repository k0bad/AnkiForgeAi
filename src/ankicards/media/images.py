"""Поиск и скачивание картинок — провайдер выбирается cfg.images.provider.

Провайдеры (все бесплатные/freemium):
- unsplash  (default) — https://unsplash.com/documentation, 50 запросов/час, нужен ключ
- pexels    — https://www.pexels.com/api/documentation/, 200 запросов/час, нужен ключ
- pixabay   — https://pixabay.com/api/docs/, 100 запросов/60с, нужен ключ
- openverse — https://api.openverse.org/v1/#tag/images, ключ не нужен (CC-агрегатор)

Если cfg.images.fallback_providers не пуст, при пустом результате/ошибке текущего
провайдера (нет ключа, 403/429/5xx) поиск переходит к следующему провайдеру из
списка по очереди; финальная неудача не считается ошибкой — карточка остаётся
без картинки, как и при единственном провайдере. Пустой fallback_providers
(значение по умолчанию) поведение не меняет: ошибка/пустой результат
единственного провайдера ведут себя как раньше.

Скачивает только для существительных (cfg.images.only_for_pos).
Имя файла: {card.id}.jpg, ресайзит до cfg.images.resize_to.
"""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from PIL import Image

from .._net import http_retry
from ..config import Config, get_secrets
from ..log import get_logger
from ..models import Card

logger = get_logger(__name__)

UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
PIXABAY_SEARCH_URL = "https://pixabay.com/api/"
OPENVERSE_SEARCH_URL = "https://api.openverse.org/v1/images/"


@http_retry
async def _search_unsplash(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
    secrets = get_secrets()
    if not secrets.unsplash_access_key:
        raise RuntimeError("UNSPLASH_ACCESS_KEY не задан в .env")

    headers = {"Authorization": f"Client-ID {secrets.unsplash_access_key}"}
    params: dict[str, str | int] = {
        "query": query,
        "per_page": count,
        "orientation": "squarish",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(UNSPLASH_SEARCH_URL, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()

    return [
        {
            "url": item["urls"]["regular"],
            "thumb": item["urls"]["thumb"],
            "author": item["user"]["name"],
            "author_url": item["user"]["links"]["html"],
            "html": item["links"]["html"],
        }
        for item in payload.get("results", [])
    ]


@http_retry
async def _search_pexels(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
    secrets = get_secrets()
    if not secrets.pexels_api_key:
        raise RuntimeError("PEXELS_API_KEY не задан в .env")

    headers = {"Authorization": secrets.pexels_api_key}
    params: dict[str, str | int] = {"query": query, "per_page": count}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(PEXELS_SEARCH_URL, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()

    return [
        {
            "url": item["src"]["large"],
            "thumb": item["src"]["tiny"],
            "author": item["photographer"],
            "author_url": item["photographer_url"],
            "html": item["url"],
        }
        for item in payload.get("photos", [])
    ]


@http_retry
async def _search_pixabay(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
    secrets = get_secrets()
    if not secrets.pixabay_api_key:
        raise RuntimeError("PIXABAY_API_KEY не задан в .env")

    params: dict[str, str | int] = {
        "key": secrets.pixabay_api_key,
        "q": query,
        "per_page": max(count, 3),  # Pixabay требует per_page >= 3
        "image_type": "photo",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(PIXABAY_SEARCH_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    return [
        {
            "url": item["largeImageURL"],
            "thumb": item["previewURL"],
            "author": item["user"],
            "author_url": f"https://pixabay.com/users/{item['user']}-{item['user_id']}/",
            "html": item["pageURL"],
        }
        for item in payload.get("hits", [])[:count]
    ]


@http_retry
async def _search_openverse(query: str, cfg: Config, count: int) -> list[dict[str, str]]:
    params: dict[str, str | int] = {"q": query, "page_size": count, "license_type": "commercial"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(OPENVERSE_SEARCH_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    return [
        {
            "url": item["url"],
            "thumb": item.get("thumbnail") or item["url"],
            "author": item.get("creator") or "unknown",
            "author_url": item.get("creator_url") or item.get("foreign_landing_url", ""),
            "html": item.get("foreign_landing_url", item["url"]),
        }
        for item in payload.get("results", [])
    ]


_PROVIDERS: dict[str, Callable[[str, Config, int], Awaitable[list[dict[str, str]]]]] = {
    "unsplash": _search_unsplash,
    "pexels": _search_pexels,
    "pixabay": _search_pixabay,
    "openverse": _search_openverse,
}


async def search_images(query: str, cfg: Config, count: int = 5) -> list[dict[str, str]]:
    """Поиск картинок через cfg.images.provider, с fallback на cfg.images.fallback_providers.

    Без fallback_providers (по умолчанию) ведёт себя как раньше: ошибка или пустой
    результат единственного провайдера возвращаются/пробрасываются как есть.
    С fallback_providers — любая ошибка провайдера в цепочке (в т.ч. последнего:
    отсутствующий ключ, 403/429/5xx) или пустой результат переходят к следующему;
    если не дал никто — возвращается пустой список, а не исключение.
    """
    provider_names = [cfg.images.provider, *cfg.images.fallback_providers]
    chain: list[tuple[str, Callable[[str, Config, int], Awaitable[list[dict[str, str]]]]]] = []
    for name in provider_names:
        search = _PROVIDERS.get(name)
        if search is None:
            raise ValueError(
                f"Неизвестный провайдер картинок: {name!r}. "
                f"Ожидается один из: {', '.join(_PROVIDERS)}"
            )
        chain.append((name, search))

    per_page = min(max(count, 1), cfg.images.per_page)
    has_fallback = len(chain) > 1

    for name, search in chain:
        try:
            results = await search(query, cfg, per_page)
        except (RuntimeError, httpx.HTTPError) as exc:
            if not has_fallback:
                raise
            logger.warning("images.provider_failed", provider=name, query=query, error=str(exc))
            continue
        if results:
            return results
        if has_fallback:
            logger.info("images.provider_empty", provider=name, query=query)

    return []


@http_retry
async def download_image(url: str, out_path: Path, cfg: Config) -> None:
    """Скачать и ресайзнуть."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.content

    max_w, max_h = cfg.images.resize_to
    with Image.open(io.BytesIO(data)) as source:
        rgb = source.convert("RGB")
        rgb.thumbnail((max_w, max_h))
        rgb.save(out_path, format="JPEG", quality=85, optimize=True)


async def find_candidates(card: Card, cfg: Config) -> list[dict[str, str]]:
    """Найти кандидатов на картинку для карточки, ничего не скачивая.

    Общая первая половина attach_image() — вынесена отдельно, чтобы review/interactive.py
    могло показать варианты человеку и передать выбор в save_image(), не делая
    повторный поисковый запрос (issue #36: до этого выбор всегда был неявным —
    первый результат без права человека увидеть альтернативы).
    """
    if not cfg.images.enabled:
        return []
    if card.pos.value not in cfg.images.only_for_pos:
        return []

    query = card.image_query or card.word
    results = await search_images(query, cfg=cfg, count=cfg.images.per_page)
    if not results:
        logger.warning("images.search_empty", card_id=card.id, word=card.word, query=query)
    return results


async def save_image(card: Card, result: dict[str, str], cfg: Config) -> Card:
    """Скачать выбранный (вручную или автоматически) результат и обновить card.image."""
    filename = f"{card.id}.jpg"
    out_path = cfg.paths.images_dir / filename
    await download_image(result["url"], out_path, cfg)
    card.image = filename
    return card


async def attach_image(card: Card, cfg: Config, auto_pick: bool = False) -> Card:
    """Найти + скачать первый результат + обновить card.image (полностью автоматически)."""
    results = await find_candidates(card, cfg)
    if not results or not auto_pick:
        return card
    return await save_image(card, results[0], cfg)
