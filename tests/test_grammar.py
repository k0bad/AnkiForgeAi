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


def _full_noun(word: str, gender: str = "n") -> dict:
    """Полная парадигма: одного рода теперь мало, чтобы карточка считалась готовой."""
    return {
        "gender": gender,
        "indefinite_singular": word,
        "definite_singular": f"{word}et",
        "indefinite_plural": word,
        "definite_plural": f"{word}ene",
    }


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
    done = _card(1, "hus", forms=_full_noun("hus"))
    todo = _card(2, "kåpe")
    seen = _capture(monkeypatch, answer=[{"id": todo.id, "forms": {"gender": "f"}}])

    await grammar_module.enrich_grammar_batch([done, todo])

    assert [item["word"] for item in seen["payload"]] == ["kåpe"]
    assert done.forms == _full_noun("hus")


async def test_no_llm_call_when_every_card_already_has_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture(monkeypatch)

    await grammar_module.enrich_grammar_batch([_card(1, "hus", forms=_full_noun("hus"))])

    assert "called" not in seen


async def test_uninflected_parts_of_speech_are_never_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture(monkeypatch)

    await grammar_module.enrich_grammar_batch([_card(1, "tolv", pos=POS.NUMERAL)])

    assert "called" not in seen


def test_a_noun_that_only_knows_its_gender_is_not_complete() -> None:
    """Род от Bildetema — ещё не парадигма.

    Ради этого случая проверка и не сводится к `bool(card.forms)`: карточка с одним
    лишь родом выглядела бы готовой, и склонение к ней никто бы не добрал.
    """
    assert not grammar_module.forms_complete(_card(1, "jente", forms={"gender": "f"}))
    assert grammar_module.forms_complete(_card(2, "hus", forms=_full_noun("hus")))


def test_adjective_is_complete_without_a_comparative() -> None:
    """У «syk» нет сравнительной степени: требовать её — значит гонять карточку к
    модели на каждом заходе и каждый раз получать тот же null."""
    card = _card(
        1,
        "syk",
        pos=POS.ADJECTIVE,
        forms={"positive_common": "syk", "positive_neuter": "sykt", "positive_plural": "syke"},
    )
    assert grammar_module.forms_complete(card)


async def test_known_gender_is_sent_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Род нужен не только нам: у женского рода своё склонение (jenta, не jenten),
    так что молча наложить его на мужскую парадигму нельзя."""
    seen = _capture(monkeypatch, answer=[{"id": 1, "forms": _full_noun("jente", "m")}])

    await grammar_module.enrich_grammar_batch([_card(1, "jente", forms={"gender": "f"})])

    assert seen["payload"] == [
        {"id": 1, "word": "jente", "pos": "noun", "known_forms": {"gender": "f"}}
    ]


async def test_dictionary_gender_survives_the_model_disagreeing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Промпт прямым текстом разрешал модели путать женский род с мужским
    («treat as m if unsure»), а у Bildetema он из словаря — значит побеждает он."""
    answer = _full_noun("jente", "m") | {"definite_singular": "jenta"}
    _capture(monkeypatch, answer=[{"id": 1, "forms": answer}])

    cards = await grammar_module.enrich_grammar_batch([_card(1, "jente", forms={"gender": "f"})])

    assert cards[0].forms["gender"] == "f"
    assert cards[0].forms["definite_singular"] == "jenta", "склонение из ответа должно остаться"
