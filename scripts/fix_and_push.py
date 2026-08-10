"""Fix and update all cards in Anki via AnkiConnect.

Запуск: python scripts/fix_and_push.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ankicards.config import get_config
from ankicards.db import Database
from ankicards.anki.connect import AnkiConnect
from ankicards.anki.notetype import card_to_anki_fields
from ankicards.enrich.grammar import enrich_grammar_batch
from ankicards.enrich.examples import enrich_example_batch
from ankicards.models import POS, Level, Status


def has_cyrillic(text: str) -> bool:
    if not text:
        return False
    return any(ord(ch) > 0x0400 for ch in text)


async def main() -> None:
    cfg = get_config()
    db = Database(cfg.paths.db)
    anki = AnkiConnect(cfg)

    # Все pushed карточки
    pushed = db.get_by_status(Status.PUSHED)
    approved = db.get_by_status(Status.APPROVED)
    all_cards = pushed + approved

    print(f"Всего карточек: {len(all_cards)} (pushed: {len(pushed)}, approved: {len(approved)})")

    # 1. Исправляем pronunciation: очищаем кириллицу
    pron_fixed = 0
    ex_swapped = 0
    for card in all_cards:
        # Pronunciation: если кириллица — очищаем
        if card.pronunciation and has_cyrillic(card.pronunciation):
            card.pronunciation = None
            pron_fixed += 1

        # Example ↔ ExampleTranslation если перепутаны
        if card.example and card.example_translation:
            ex_cyr = has_cyrillic(card.example)
            ext_cyr = has_cyrillic(card.example_translation)
            if ex_cyr and not ext_cyr:
                card.example, card.example_translation = card.example_translation, card.example
                ex_swapped += 1

    print(f"Pronunciation очищено: {pron_fixed}")
    print(f"Example переставлено: {ex_swapped}")

    # 2. Enrich: грамматика для noun/verb/adj без forms
    needs_forms = [c for c in all_cards if c.pos in (POS.NOUN, POS.VERB, POS.ADJECTIVE) and not c.forms]
    print(f"\nФормы нужны: {len(needs_forms)}")

    for start in range(0, len(needs_forms), 10):
        batch = needs_forms[start:start + 10]
        print(f"  enrich grammar batch {start // 10 + 1}...")
        try:
            await enrich_grammar_batch(batch)
        except Exception as e:
            print(f"  ❌ {e}")

    # 3. Enrich: примеры для тех без example
    needs_ex = [c for c in all_cards if not c.example]
    print(f"\nПримеры нужны: {len(needs_ex)}")
    for start in range(0, len(needs_ex), 10):
        batch = needs_ex[start:start + 10]
        print(f"  enrich examples batch {start // 10 + 1}...")
        try:
            await enrich_example_batch(batch)
        except Exception as e:
            print(f"  ❌ {e}")

    # 4. Сохраняем в БД и обновляем в Anki
    print(f"\nОбновление в БД и Anki...")
    updated = 0
    failed = 0

    for card in all_cards:
        # Сохраняем в БД
        with db.connect() as conn:
            conn.execute(
                """UPDATE cards SET
                    pronunciation=?, example=?, example_translation=?,
                    forms=?, status=?
                WHERE id=?""",
                (
                    card.pronunciation,
                    card.example,
                    card.example_translation,
                    json.dumps(card.forms, ensure_ascii=False) if card.forms else None,
                    card.status.value if isinstance(card.status, Status) else card.status,
                    card.id,
                ),
            )

        # Обновляем в Anki (если pushed)
        if card.anki_note_id is not None:
            try:
                fields = card_to_anki_fields(card)
                await anki.update_note_fields(note_id=card.anki_note_id, fields=fields)
                updated += 1
            except Exception as e:
                failed += 1
                print(f"  ❌ update failed: {card.word} — {e}")

    print(f"\n✅ Обновлено в Anki: {updated}")
    print(f"❌ Ошибок: {failed}")

    # Итог
    print(f"\n=== ИТОГ ===")
    conn2 = __import__("sqlite3").connect(str(cfg.paths.db))
    conn2.row_factory = __import__("sqlite3").Row
    empty_pron = conn2.execute("SELECT COUNT(*) FROM cards WHERE pronunciation IS NULL OR pronunciation=''").fetchone()[0]
    empty_forms = conn2.execute("SELECT COUNT(*) FROM cards WHERE (forms IS NULL OR forms='') AND pos IN ('noun','verb','adj')").fetchone()[0]
    empty_ex = conn2.execute("SELECT COUNT(*) FROM cards WHERE example IS NULL OR example=''").fetchone()[0]
    print(f"  ∅ pronunciation: {empty_pron}")
    print(f"  ∅ forms:         {empty_forms}")
    print(f"  ∅ example:       {empty_ex}")
    conn2.close()


if __name__ == "__main__":
    asyncio.run(main())