"""Тесты для enrich.translation: парсинг RU/EN из ответа LLM (issue #10 — image_query)."""

from __future__ import annotations

import pytest
import structlog

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


async def test_multi_sense_en_line_takes_first_variant_and_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """issue #50: модель иногда сваливает EN: в тот же "sense1 / sense2" формат,
    что RU: (найдено на "lønn" — клён/зарплата, оба смысла сразу). Составной
    запрос бьёт по случайной части смысла в провайдере картинок, так что берём
    только первый вариант и логируем деградацию, а не отправляем как есть."""
    monkeypatch.setattr(translation_module, "load_prompt", lambda name, **kw: "prompt")

    async def _fake_call_text(prompt: str) -> str:
        return "RU: клён / зарплата\nEN: maple tree leaves / salary paycheck money"

    monkeypatch.setattr(translation_module, "call_text", _fake_call_text)

    with structlog.testing.capture_logs() as cap:
        card = await translation_module.enrich_translation(_card())

    assert card.translation == "клён / зарплата"  # RU: не трогаем, там "/" легитимен
    assert card.image_query == "maple tree leaves"

    matches = [e for e in cap if e.get("event") == "translation.image_query_multi_sense"]
    assert len(matches) == 1
    assert matches[0]["log_level"] == "warning"
    assert matches[0]["word"] == "hus"


async def test_single_sense_en_line_with_no_slash_is_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Контроль: обычная (не составная) EN-фраза не должна ничего логировать
    или обрезаться — только явный " / " внутри EN: считается деградацией."""
    monkeypatch.setattr(translation_module, "load_prompt", lambda name, **kw: "prompt")

    async def _fake_call_text(prompt: str) -> str:
        return "RU: пружина\nEN: metal coil spring"

    monkeypatch.setattr(translation_module, "call_text", _fake_call_text)

    with structlog.testing.capture_logs() as cap:
        card = await translation_module.enrich_translation(_card())

    assert card.image_query == "metal coil spring"
    assert [e for e in cap if e.get("event") == "translation.image_query_multi_sense"] == []


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
