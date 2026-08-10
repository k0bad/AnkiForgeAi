"""Download Unsplash images for all noun cards without images.

Usage:
    uv run python scripts/run_images.py               # process all nouns
    uv run python scripts/run_images.py --limit 20     # process only 20 (test run)
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3

from ankicards.config import get_config, get_secrets
from ankicards.db import Database
from ankicards.media.images import attach_image
from ankicards.models import Status


async def main(limit: int | None = None) -> None:
    cfg = get_config()
    db = Database(cfg.paths.db)

    # Get all pushed + approved noun cards without images
    cards = [
        c
        for c in db.get_by_status(Status.PUSHED) + db.get_by_status(Status.APPROVED)
        if c.pos.value == "noun" and not c.image
    ]

    print(f"Nouns without images: {len(cards)}")
    if not cards:
        print("Nothing to do.")
        return

    if limit:
        cards = cards[:limit]
        print(f"Processing first {limit} cards (test run)")

    # Direct SQLite connection for updates (avoids going through the ORM)
    conn = sqlite3.connect(str(cfg.paths.db))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row

    ok = 0
    fail = 0
    for card in cards:
        try:
            await attach_image(card, cfg, auto_pick=True)
            if card.image:
                conn.execute(
                    "UPDATE cards SET image = ? WHERE id = ?",
                    (card.image, card.id),
                )
                conn.commit()
                ok += 1
                print(f"  OK  {card.word} -> {card.image}")
            else:
                print(f"  SKIP {card.word}: no image returned (no results)")
                fail += 1
        except Exception as e:
            fail += 1
            print(f"  FAIL {card.word}: {e}")

    conn.close()
    print(f"\nDone: {ok} ok, {fail} fail")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Unsplash images for noun cards")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of cards to process (safety limit for testing)",
    )
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit))
