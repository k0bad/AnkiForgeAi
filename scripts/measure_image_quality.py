"""issue #37: measure image-match quality on a fresh sample instead of judging by impression.

Ingests a sample of cards across languages/topics straight from the LLM (no DB/Anki
writes — this never touches the user's real collection), resolves image_query and the
top search-provider candidate for each noun, and writes a JSON file with an embedded
base64 thumbnail per card. A human then scores each thumbnail pass/fail (does it depict
the word) to get a % baseline — this script does not judge match quality itself.

Usage:
    uv run python scripts/measure_image_quality.py --out data/image_quality_sample.json
    uv run python scripts/measure_image_quality.py --languages nb,en \
        --topics-per-language 4 --words-per-topic 8
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from dataclasses import asdict, dataclass

import httpx

from ankicards.config import Config, get_config
from ankicards.enrich.translation import enrich_translation
from ankicards.ingest.topic import ingest_by_topic
from ankicards.media.images import search_images
from ankicards.models import Card

# Английские ярлыки тем понятны LLM независимо от целевого языка карточек —
# не нужно переводить их на nb/de/es отдельно.
DEFAULT_TOPICS = ["food", "travel", "work", "weather and nature", "home"]


@dataclass
class SampleResult:
    language: str
    topic: str
    word: str
    pos: str
    translation: str
    image_query: str | None
    provider: str | None
    candidate_url: str | None
    candidate_author: str | None
    candidate_html: str | None
    thumb_data_uri: str | None
    error: str | None = None


async def _fetch_thumb_data_uri(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, timeout=20.0)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    b64 = base64.b64encode(resp.content).decode("ascii")
    return f"data:{content_type};base64,{b64}"


async def _process_card(
    card: Card, topic: str, language: str, cfg: Config, client: httpx.AsyncClient
) -> SampleResult:
    base = dict(
        language=language,
        topic=topic,
        word=card.word,
        pos=card.pos.value,
        translation=card.translation,
        image_query=None,
        provider=None,
        candidate_url=None,
        candidate_author=None,
        candidate_html=None,
        thumb_data_uri=None,
    )
    try:
        await enrich_translation(card)  # добьёт image_query, если его не было (issue #47)
    except Exception as e:
        return SampleResult(**base, error=f"translation: {e}")
    base["image_query"] = card.image_query

    query = card.image_query or card.word
    try:
        candidates = await search_images(query, cfg=cfg, count=cfg.images.per_page)
    except Exception as e:
        return SampleResult(**base, error=f"search: {e}")

    if not candidates:
        return SampleResult(**base, error="no results")

    top = candidates[0]
    base["provider"] = cfg.images.provider
    base["candidate_url"] = top.get("url")
    base["candidate_author"] = top.get("author")
    base["candidate_html"] = top.get("html")

    thumb_source = top.get("thumb") or top.get("url")
    thumb = await _fetch_thumb_data_uri(client, thumb_source) if thumb_source else None
    if thumb is None:
        return SampleResult(**base, error="thumbnail download failed")

    base["thumb_data_uri"] = thumb
    return SampleResult(**base)


async def collect_sample(
    languages: list[str], topics: list[str], words_per_topic: int, level: str
) -> list[SampleResult]:
    cfg = get_config()
    results: list[SampleResult] = []

    async with httpx.AsyncClient() as client:
        for language in languages:
            cfg.language = language  # load_prompt() читает cfg.language через get_config()
            for topic in topics:
                print(f"[{language}] ingesting topic {topic!r}...")
                try:
                    cards = await ingest_by_topic(topic, count=words_per_topic, level=level)
                except Exception as e:
                    print(f"  FAILED: {e}")
                    continue

                nouns = [c for c in cards if c.pos.value in cfg.images.only_for_pos]
                print(f"  {len(cards)} words, {len(nouns)} eligible for images")

                for card in nouns:
                    result = await _process_card(card, topic, language, cfg, client)
                    results.append(result)
                    status = "OK" if result.thumb_data_uri else f"SKIP ({result.error})"
                    print(f"    {card.word:20s} -> {result.image_query!r:35s} {status}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sample cards for manual image-match scoring (issue #37)"
    )
    parser.add_argument("--languages", default="nb,en", help="comma-separated language codes")
    parser.add_argument("--topics-per-language", type=int, default=len(DEFAULT_TOPICS))
    parser.add_argument("--words-per-topic", type=int, default=8)
    parser.add_argument("--level", default="A2")
    parser.add_argument("--out", required=True, help="output JSON path")
    args = parser.parse_args()

    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]
    topics = DEFAULT_TOPICS[: args.topics_per_language]

    results = asyncio.run(collect_sample(languages, topics, args.words_per_topic, args.level))

    scored = [r for r in results if r.thumb_data_uri]
    print(f"\nDone: {len(scored)}/{len(results)} cards got a scoreable thumbnail")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=2)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
