"""Тесты для enrich.translation: парсинг RU/EN из ответа LLM (issue #10 — image_query)."""

from __future__ import annotations

import pytest

from ankicards.enrich import translation as translation_module
from ankicards.models import POS, Card


def _card(word: str = "hus", translation: str = "") -> Card:
    return Card(word=word, pos=POS.NOUN, translation=translation)


async def test_structured_response_fills_translation_and_image_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(translation_module, "load_prompt", lambda name, **kw: "prompt")

    async def _fake_call_text(prompt: str) -> str:
        return "RU: дом / хата\nEN: house"

    monkeypatch.setattr(translation_module, "call_text", _fake_call_text)

    card = await translation_module.enrich_translation(_card())

    assert card.translation == "дом / хата"
    assert card.image_query == "house"


async def test_multi_word_disambiguated_phrase_parses_into_image_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """issue #34: EN: теперь несёт короткую disambiguated-фразу (2-4 слова)
    вместо голого 1-слова — парсинг должен принимать её как есть, без обрезки
    до первого слова."""
    monkeypatch.setattr(translation_module, "load_prompt", lambda name, **kw: "prompt")

    async def _fake_call_text(prompt: str) -> str:
        return "RU: пружина\nEN: metal coil spring"

    monkeypatch.setattr(translation_module, "call_text", _fake_call_text)

    card = await translation_module.enrich_translation(_card())

    assert card.translation == "пружина"
    assert card.image_query == "metal coil spring"


async def test_unstructured_response_falls_back_to_plain_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Модель, которая не выдержала формат RU:/EN: — весь ответ уходит в translation,
    image_query остаётся пустым (совместимо со старым однострочным промптом)."""
    monkeypatch.setattr(translation_module, "load_prompt", lambda name, **kw: "prompt")

    async def _fake_call_text(prompt: str) -> str:
        return '"дом / хата"'

    monkeypatch.setattr(translation_module, "call_text", _fake_call_text)

    card = await translation_module.enrich_translation(_card())

    assert card.translation == "дом / хата"
    assert card.image_query is None


async def test_cards_with_existing_translation_skip_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(name: str, **kw: str) -> str:
        raise AssertionError("load_prompt should not be called when translation is set")

    monkeypatch.setattr(translation_module, "load_prompt", _boom)

    card = _card(translation="дом")
    result = await translation_module.enrich_translation(card)

    assert result.translation == "дом"
    assert result.image_query is None
