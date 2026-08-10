"""Поиск и скачивание картинок через Unsplash API.

Бесплатный тариф: 50 запросов/час.
https://unsplash.com/documentation

Скачивает только для существительных (cfg.images.only_for_pos).
Имя файла: {card.id}.jpg, ресайзит до cfg.images.resize_to.

В режиме review показывает N вариантов, пользователь выбирает.
В auto-режиме берёт первый результат.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
from PIL import Image

from ..config import Config, get_secrets
from ..models import Card

UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"


async def search_images(query: str, cfg: Config, count: int = 5) -> list[dict]:
    """Поиск картинок. Возвращает список {url, thumb, author, ...}."""
    secrets = get_secrets()
    if not secrets.unsplash_access_key:
        raise RuntimeError("UNSPLASH_ACCESS_KEY не задан в .env")

    per_page = min(max(count, 1), cfg.images.per_page)
    headers = {"Authorization": f"Client-ID {secrets.unsplash_access_key}"}
    params: dict[str, str | int] = {
        "query": query,
        "per_page": per_page,
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


async def attach_image(card: Card, cfg: Config, auto_pick: bool = False) -> Card:
    """Найти + скачать + обновить card.image."""
    if not cfg.images.enabled:
        return card
    if card.pos.value not in cfg.images.only_for_pos:
        return card

    results = await search_images(card.word, cfg=cfg, count=cfg.images.per_page)
    if not results:
        return card
    if not auto_pick:
        return card

    filename = f"{card.id}.jpg"
    out_path = cfg.paths.images_dir / filename
    await download_image(results[0]["url"], out_path, cfg)
    card.image = filename
    return card
