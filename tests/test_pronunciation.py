"""Тесты для enrich.pronunciation: выбор промпта по config.transcription."""

from __future__ import annotations

import pytest

from ankicards.enrich import pronunciation as pronunciation_module
from ankicards.models import POS, Card


class _StubConfig:
    def __init__(self, transcription: str) -> None:
        self.transcription = transcription


def _card(word: str = "hus") -> Card:
    return Card(language="nb", word=word, pos=POS.NOUN, translation="дом")


async def test_practical_transcription_loads_russian_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def _fake_load_prompt(name: str, **kwargs: str) -> str:
        seen["name"] = name
        return "prompt"

    async def _fake_call_json(prompt: str) -> list[dict]:
        return [{"id": card.id, "pronunciation": "хус"}]

    card = _card()
    monkeypatch.setattr(pronunciation_module, "get_config", lambda: _StubConfig("practical"))
    monkeypatch.setattr(pronunciation_module, "load_prompt", _fake_load_prompt)
    monkeypatch.setattr(pronunciation_module, "call_json", _fake_call_json)

    await pronunciation_module.enrich_pronunciation_batch([card])

    assert seen["name"] == "russian_pronunciation"
    assert card.pronunciation == "хус"


async def test_ipa_transcription_loads_ipa_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def _fake_load_prompt(name: str, **kwargs: str) -> str:
        seen["name"] = name
        return "prompt"

    card = _card()

    async def _fake_call_json(prompt: str) -> list[dict]:
        return [{"id": card.id, "pronunciation": "/hʉːs/"}]

    monkeypatch.setattr(pronunciation_module, "get_config", lambda: _StubConfig("ipa"))
    monkeypatch.setattr(pronunciation_module, "load_prompt", _fake_load_prompt)
    monkeypatch.setattr(pronunciation_module, "call_json", _fake_call_json)

    await pronunciation_module.enrich_pronunciation_batch([card])

    assert seen["name"] == "ipa_pronunciation"
    assert card.pronunciation == "/hʉːs/"


async def test_unknown_transcription_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pronunciation_module, "get_config", lambda: _StubConfig("klingon"))

    with pytest.raises(ValueError, match="klingon"):
        await pronunciation_module.enrich_pronunciation_batch([_card()])


async def test_cards_with_existing_pronunciation_skip_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> None:
        raise AssertionError("get_config should not be called when nothing needs enrichment")

    monkeypatch.setattr(pronunciation_module, "get_config", _boom)

    card = _card()
    card.pronunciation = "хус"

    result = await pronunciation_module.enrich_pronunciation_batch([card])

    assert result[0].pronunciation == "хус"
