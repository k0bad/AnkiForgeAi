"""Тесты для enrich.grammar: кого стадия вообще спрашивает у LLM."""

from __future__ import annotations

import json

import pytest

from ankicards.enrich import grammar as grammar_module
from ankicards.models import POS, Card


def _card(card_id: int, word: str, pos: POS = POS.NOUN, forms: dict | None = None) -> Card:
    # id обязателен: ответ LLM разбирается по нему (forms_by_id), и две карточки
    # без id слились бы в один ключ "None".
    return Card(id=card_id, language="nb", word=word, pos=pos, translation="перевод", forms=forms)


def _capture(
    monkeypatch: pytest.MonkeyPatch, answer: list[dict] | None = None
) -> dict[str, list[dict]]:
    """Подменить load_prompt/call_json и вернуть то, что стадия положила в payload."""
    seen: dict[str, list[dict]] = {}

    def _fake_load_prompt(name: str, **kwargs: str) -> str:
        seen["payload"] = json.loads(kwargs["words_json"])
        return "prompt"

    async def _fake_call_json(prompt: str, **kwargs: object) -> list[dict]:
        seen["called"] = [{"yes": True}]
        return answer or []

    monkeypatch.setattr(grammar_module, "load_prompt", _fake_load_prompt)
    monkeypatch.setattr(grammar_module, "call_json", _fake_call_json)
    return seen


async def test_asks_only_about_cards_without_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Готовые парадигмы не переспрашиваются — как в pronunciation/examples.

    Важно не только для токенов: карточка, вернувшаяся в review из-за сбоя другой
    стадии, при повторном accept тянула бы за собой перегенерацию форм для всей
    пачки, а любой транзиентный сбой этого вызова снова уронил бы её целиком.
    """
    done = _card(1, "hus", forms={"gender": "n"})
    todo = _card(2, "kåpe")
    seen = _capture(monkeypatch, answer=[{"id": todo.id, "forms": {"gender": "f"}}])

    await grammar_module.enrich_grammar_batch([done, todo])

    assert [item["word"] for item in seen["payload"]] == ["kåpe"]
    assert done.forms == {"gender": "n"}


async def test_no_llm_call_when_every_card_already_has_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture(monkeypatch)

    await grammar_module.enrich_grammar_batch([_card(1, "hus", forms={"gender": "n"})])

    assert "called" not in seen


async def test_uninflected_parts_of_speech_are_never_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture(monkeypatch)

    await grammar_module.enrich_grammar_batch([_card(1, "tolv", pos=POS.NUMERAL)])

    assert "called" not in seen
