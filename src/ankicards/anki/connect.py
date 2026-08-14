"""HTTP-клиент к AnkiConnect.

Документация: https://foosoft.net/projects/anki-connect/
Все вызовы — POST на cfg.anki.url, тело {"action", "version": 6, "params"}.

Используемые actions:
- deckNames / createDeck
- modelNames / createModel / updateModelTemplates / updateModelStyling
- addNote / updateNoteFields / deleteNotes
- findNotes / notesInfo
- storeMediaFile  — загрузить mp3/jpg в collection.media
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, cast

import httpx

from .._net import http_retry
from ..config import Config

ANKI_CONNECT_VERSION = 6
DEFAULT_TIMEOUT = 30.0


class AnkiConnectError(Exception):
    """Ошибка от AnkiConnect или сетевая."""


class AnkiConnect:
    """Тонкая обёртка над AnkiConnect API."""

    def __init__(self, cfg: Config, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = cfg.anki.url
        self.deck = cfg.anki.deck_name
        self.note_type = cfg.anki.note_type
        self._timeout = timeout

    @http_retry
    async def _post(self, payload: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self.url, json=payload)
            response.raise_for_status()
            return response.json()

    async def _call(self, action: str, **params: Any) -> Any:
        """Вызвать AnkiConnect action и вернуть result."""
        payload = {"action": action, "version": ANKI_CONNECT_VERSION, "params": params}
        try:
            data = await self._post(payload)
        except httpx.HTTPError as e:
            raise AnkiConnectError(f"HTTP error calling {action}: {e}") from e

        if not isinstance(data, dict) or "error" not in data or "result" not in data:
            raise AnkiConnectError(f"Malformed AnkiConnect response for {action}: {data!r}")
        if data["error"] is not None:
            raise AnkiConnectError(f"AnkiConnect error on {action}: {data['error']}")
        return data["result"]

    # ───────────── Deck ─────────────

    async def deck_names(self) -> list[str]:
        return cast(list[str], await self._call("deckNames"))

    async def ensure_deck(self) -> None:
        """Создать deck, если не существует."""
        existing = await self.deck_names()
        if self.deck not in existing:
            await self._call("createDeck", deck=self.deck)

    # ───────────── Model / Note Type ─────────────

    async def model_names(self) -> list[str]:
        return cast(list[str], await self._call("modelNames"))

    async def create_model(
        self,
        model_name: str,
        fields: list[str],
        css: str,
        card_templates: list[dict[str, str]],
    ) -> Any:
        """Создать Note Type. card_templates: [{"Name","Front","Back"}]."""
        return await self._call(
            "createModel",
            modelName=model_name,
            inOrderFields=fields,
            css=css,
            cardTemplates=card_templates,
        )

    async def update_model_templates(
        self, model_name: str, templates: dict[str, dict[str, str]]
    ) -> None:
        """Обновить Front/Back существующего Note Type. templates: {"CardName": {Front, Back}}."""
        await self._call(
            "updateModelTemplates",
            model={"name": model_name, "templates": templates},
        )

    async def update_model_styling(self, model_name: str, css: str) -> None:
        """Обновить CSS существующего Note Type."""
        await self._call(
            "updateModelStyling",
            model={"name": model_name, "css": css},
        )

    # ───────────── Notes ─────────────

    async def add_note(self, fields: dict[str, str], tags: list[str]) -> int:
        """Добавить заметку, вернуть note_id."""
        note = {
            "deckName": self.deck,
            "modelName": self.note_type,
            "fields": fields,
            "tags": tags,
            "options": {
                "allowDuplicate": False,
                "duplicateScope": "deck",
            },
        }
        return cast(int, await self._call("addNote", note=note))

    async def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        await self._call(
            "updateNoteFields",
            note={"id": note_id, "fields": fields},
        )

    async def delete_notes(self, note_ids: list[int]) -> None:
        await self._call("deleteNotes", notes=note_ids)

    async def find_notes(self, query: str) -> list[int]:
        """Поиск заметок по Anki-синтаксису ('deck:Norsk Word:gå')."""
        return cast(list[int], await self._call("findNotes", query=query))

    async def notes_info(self, note_ids: list[int]) -> list[dict]:
        """Получить детали заметок (поля, теги)."""
        if not note_ids:
            return []
        return cast(list[dict], await self._call("notesInfo", notes=note_ids))

    # ───────────── Media ─────────────

    async def store_media(self, filename: str, file_path: Path) -> str:
        """Загрузить файл в collection.media. Возвращает имя файла в Anki."""
        data = file_path.read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        return cast(str, await self._call("storeMediaFile", filename=filename, data=encoded))
