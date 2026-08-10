"""Создать Note Type 'NorskCard' в Anki через AnkiConnect.

Запуск (один раз, при первом старте):
    python scripts/setup_anki_notetype.py

Требует: запущенный Anki desktop с addon AnkiConnect (id 2055492159).
"""

from __future__ import annotations

import asyncio

from ankicards.anki.connect import AnkiConnect
from ankicards.anki.notetype import (
    back_template,
    CSS,
    FIELDS,
    FRONT_TEMPLATE,
    _get_note_type_name as get_note_type_name,
)
from ankicards.config import get_config


async def main() -> None:
    cfg = get_config()
    anki = AnkiConnect(cfg)

    await anki.ensure_deck()
    print(f"✓ Deck готов: {cfg.anki.deck_name}")

    existing_models = await anki.model_names()
    note_type = get_note_type_name()
    if note_type in existing_models:
        print(f"✓ Note Type уже существует: {note_type}")
        return

    await anki.create_model(
        model_name=note_type,
        fields=FIELDS,
        css=CSS,
        card_templates=[
            {
                "Name": "Recognition",
                "Front": FRONT_TEMPLATE,
                "Back": back_template(),
            }
        ],
    )
    print(f"✓ Note Type создан: {note_type}")


if __name__ == "__main__":
    asyncio.run(main())
