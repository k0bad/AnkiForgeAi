"""Сгенерировать русскую транскрипцию — по одной карточке.

Запуск: python scripts/generate_pronunciation.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ankicards.config import get_config
from ankicards.db import Database
from ankicards.anki.connect import AnkiConnect
from ankicards.anki.notetype import card_to_anki_fields
from ankicards.llm import call_text
from ankicards.models import Status


PRON_PROMPT = (
    'Напиши произношение норвежского слова русскими буквами. '
    'Только транскрипция, без пояснений, без кавычек, без точки в конце.\n\n'
    'Слово: {word}\n'
    'Произношение:'
)


async def main() -> None:
    cfg = get_config()
    db = Database(cfg.paths.db)
    anki = AnkiConnect(cfg)

    pushed = db.get_by_status(Status.PUSHED)
    approved = db.get_by_status(Status.APPROVED)
    all_cards = pushed + approved

    need = [c for c in all_cards if not c.pronunciation]
    print(f"Нужно транскрипций: {len(need)}")

    ok = 0
    fail = 0
    for i, card in enumerate(need):
        prompt = PRON_PROMPT.format(word=card.word)
        try:
            pron = (await call_text(prompt)).strip().strip('"').strip("'")
            if pron and len(pron) < 30:
                card.pronunciation = pron
                # Save to DB
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE cards SET pronunciation=? WHERE id=?",
                        (card.pronunciation, card.id),
                    )
                # Update in Anki
                if card.anki_note_id is not None:
                    fields = card_to_anki_fields(card)
                    await anki.update_note_fields(note_id=card.anki_note_id, fields=fields)
                ok += 1
                print(f"  [{i+1}/{len(need)}] {card.word:20s} → {pron}")
            else:
                fail += 1
                print(f"  [{i+1}/{len(need)}] {card.word:20s} ❌ пустой ответ")
        except Exception as e:
            fail += 1
            print(f"  [{i+1}/{len(need)}] {card.word:20s} ❌ {str(e)[:60]}")

    print(f"\n✅ Сгенерировано: {ok}")
    print(f"❌ Ошибок: {fail}")

    # Итог
    conn2 = __import__("sqlite3").connect(str(cfg.paths.db))
    has = conn2.execute("SELECT COUNT(*) FROM cards WHERE pronunciation IS NOT NULL AND pronunciation != ''").fetchone()[0]
    total = conn2.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    print(f"\nИтог: {has}/{total} карточек с транскрипцией")
    conn2.close()


if __name__ == "__main__":
    asyncio.run(main())