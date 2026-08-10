"""Исправить данные импортированных карточек: enrich, формы, примеры.

Запуск: python scripts/fix_imported_data.py
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
from ankicards.models import POS, Level, Status


def has_cyrillic(text: str) -> bool:
    return any(ord(ch) > 0x0400 for ch in text)


async def main() -> None:
    cfg = get_config()
    db = Database(cfg.paths.db)

    # Берём approved + pushed (все кроме старых 3 pushed)
    all_cards = db.get_by_status(Status.APPROVED)
    print(f"Карточек для исправления: {len(all_cards)}")

    fixes = {"pronunciation": 0, "example_swap": 0, "grammar": 0, "level": 0}

    for card in all_cards:
        # 1. Fix pronunciation: очищаем, содержит мусор из соседних колонок
        if card.pronunciation and (" " in card.pronunciation or any(ord(ch) > 0x0400 for ch in card.pronunciation)):
            # Если в pronunciation есть пробел или кириллица — это мусор
            card.pronunciation = None
            fixes["pronunciation"] += 1

        # 2. Fix example/example_translation swap
        if card.example and card.example_translation:
            ex_has_cyrillic = has_cyrillic(card.example)
            extr_has_cyrillic = has_cyrillic(card.example_translation)
            if ex_has_cyrillic and not extr_has_cyrillic:
                # Перепутаны: example содержит русский, example_trans содержит норвежский
                card.example, card.example_translation = card.example_translation, card.example
                fixes["example_swap"] += 1

        # 3. Fix level: извлекаем из тегов, если не заполнен
        if not card.level and card.tags:
            for tag in card.tags:
                try:
                    card.level = Level(tag.upper())
                    fixes["level"] += 1
                    break
                except ValueError:
                    continue

    # Сохраняем исправления
    for card in all_cards:
        with db.connect() as conn:
            conn.execute(
                """UPDATE cards SET
                    pronunciation=?, example=?, example_translation=?,
                    level=?
                WHERE id=?""",
                (
                    card.pronunciation,
                    card.example,
                    card.example_translation,
                    card.level.value if card.level else None,
                    card.id,
                ),
            )

    print(f"Исправлено:")
    print(f"  pronunciation очищено:  {fixes['pronunciation']}")
    print(f"  example переставлен:   {fixes['example_swap']}")
    print(f"  level восстановлен:    {fixes['level']}")

    # 4. Enrich: грамматические формы для noun/verb/adj
    needs_forms = [c for c in all_cards if c.pos in (POS.NOUN, POS.VERB, POS.ADJECTIVE) and not c.forms]
    print(f"\nГрамматические формы нужны для: {len(needs_forms)} карточек")

    # Батчи по 10
    batch_size = 10
    for start in range(0, len(needs_forms), batch_size):
        batch = needs_forms[start:start + batch_size]
        print(f"  Батч {start // batch_size + 1}: {len(batch)} карт...")
        try:
            await enrich_grammar_batch(batch)
            for card in batch:
                if card.forms:
                    with db.connect() as conn:
                        conn.execute(
                            "UPDATE cards SET forms=? WHERE id=?",
                            (json.dumps(card.forms, ensure_ascii=False), card.id),
                        )
                    fixes["grammar"] += 1
        except Exception as e:
            print(f"  ❌ Ошибка: {e}")

    # 5. Enrich: примеры для тех, у кого их нет
    needs_examples = [c for c in all_cards if not c.example]
    print(f"\nПримеры нужны для: {len(needs_examples)} карточек")

    for start in range(0, len(needs_examples), batch_size):
        batch = needs_examples[start:start + batch_size]
        print(f"  Батч {start // batch_size + 1}: {len(batch)} карт...")
        try:
            await enrich_example_batch(batch)
            for card in batch:
                if card.example:
                    with db.connect() as conn:
                        conn.execute(
                            "UPDATE cards SET example=?, example_translation=? WHERE id=?",
                            (card.example, card.example_translation, card.id),
                        )
        except Exception as e:
            print(f"  ❌ Ошибка: {e}")

    print(f"\n✅ Готово!")
    print(f"  forms добавлено: {fixes['grammar']}")

    # Итоговая статистика
    conn2 = sqlite3.connect(str(cfg.paths.db))
    conn2.row_factory = sqlite3.Row
    empty_pron = conn2.execute("SELECT COUNT(*) FROM cards WHERE pronunciation IS NULL OR pronunciation=''").fetchone()[0]
    empty_forms = conn2.execute("SELECT COUNT(*) FROM cards WHERE (forms IS NULL OR forms='') AND pos IN ('noun','verb','adj')").fetchone()[0]
    empty_ex = conn2.execute("SELECT COUNT(*) FROM cards WHERE example IS NULL OR example=''").fetchone()[0]
    print(f"\nПосле исправления:")
    print(f"  ∅ pronunciation: {empty_pron}")
    print(f"  ∅ forms (noun/verb/adj): {empty_forms}")
    print(f"  ∅ example: {empty_ex}")
    conn2.close()


if __name__ == "__main__":
    import sqlite3
    asyncio.run(main())