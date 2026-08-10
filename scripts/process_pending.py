"""Обработать pending-карточки: enrich + audio мелкими батчами.

Запуск: python scripts/process_pending.py [--batch 15]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ankicards.config import get_config
from ankicards.db import Database
from ankicards.enrich.grammar import enrich_grammar_batch
from ankicards.enrich.examples import enrich_example_batch
from ankicards.enrich.translation import enrich_translation
from ankicards.media.images import attach_image
from ankicards.media.tts import generate_audio
from ankicards.models import Status


async def main() -> None:
    batch_size = 15
    if "--batch" in sys.argv:
        idx = sys.argv.index("--batch")
        if idx + 1 < len(sys.argv):
            batch_size = int(sys.argv[idx + 1])

    cfg = get_config()
    db = Database(cfg.paths.db)

    pending = db.get_by_status(Status.PENDING)
    if not pending:
        print("Нет pending-карточек.")
        return

    print(f"Всего pending: {len(pending)}, батч: {batch_size}")

    total_enriched = 0
    total_audio = 0
    total_images = 0
    total_errors = 0

    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        print(f"\n--- Батч {start // batch_size + 1}: {len(batch)} карт ---")

        # 1. Enrich: перевод (если пустой)
        for card in batch:
            if not card.translation:
                try:
                    await enrich_translation(card)
                except Exception as e:
                    print(f"   ❌ translation error: {card.word}: {e}")

        # 2. Enrich: грамматика
        try:
            await enrich_grammar_batch(batch)
            total_enriched += len(batch)
        except Exception as e:
            total_errors += 1
            print(f"   ❌ grammar error: {e}")

        # 3. Enrich: примеры
        try:
            await enrich_example_batch(batch)
        except Exception as e:
            total_errors += 1
            print(f"   ❌ examples error: {e}")

        # 4. Audio
        for card in batch:
            try:
                await generate_audio(card, cfg)
                total_audio += 1
            except Exception as e:
                total_errors += 1
                print(f"   ❌ audio error: {card.word}: {e}")

        # 5. Images (если есть ключ)
        if cfg.images.enabled:
            for card in batch:
                try:
                    await attach_image(card, cfg, auto_pick=True)
                    if card.image:
                        total_images += 1
                except Exception as e:
                    total_errors += 1
                    print(f"   ❌ image error: {card.word}: {e}")

        # 6. Update status в БД
        for card in batch:
            card.status = Status.APPROVED
            with db.connect() as conn:
                conn.execute(
                    """UPDATE cards SET
                        pronunciation=?, translation=?, example=?, example_translation=?,
                        forms=?, audio=?, image=?, status=?
                    WHERE id=?""",
                    (
                        card.pronunciation,
                        card.translation,
                        card.example,
                        card.example_translation,
                        json.dumps(card.forms, ensure_ascii=False) if card.forms else None,
                        card.audio,
                        card.image,
                        Status.APPROVED.value,
                        card.id,
                    ),
                )
            db.log_action("enrich_approve", card_id=card.id, details={"word": card.word})

        print(f"   ✅ enrich={len(batch)} audio={total_audio}")

    print(f"\n{'='*40}")
    print(f"✅ Готово!")
    print(f"   Обогащено: {total_enriched}")
    print(f"   Аудио:     {total_audio}")
    print(f"   Картинки:  {total_images}")
    print(f"   Ошибок:    {total_errors}")


if __name__ == "__main__":
    asyncio.run(main())