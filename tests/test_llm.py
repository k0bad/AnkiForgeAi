"""Тесты для llm._parse_json — устойчивость к markdown-обёрткам и мусору вокруг JSON."""

from __future__ import annotations

import pytest

from ankicards.llm import _parse_json


def test_parse_json_plain_array() -> None:
    assert _parse_json('[{"a": 1}]') == [{"a": 1}]


def test_parse_json_json_fence() -> None:
    text = '```json\n[{"a": 1}, {"a": 2}]\n```'
    assert _parse_json(text) == [{"a": 1}, {"a": 2}]


def test_parse_json_bare_fence() -> None:
    text = '```\n[{"a": 1}]\n```'
    assert _parse_json(text) == [{"a": 1}]


def test_parse_json_recovers_array_from_surrounding_prose() -> None:
    text = 'Конечно, вот результат:\n[{"word": "hus"}]\nНадеюсь, помогло!'
    assert _parse_json(text) == [{"word": "hus"}]


def test_parse_json_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError, match="невалидный JSON"):
        _parse_json("это вообще не JSON")
