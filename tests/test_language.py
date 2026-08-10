"""Тесты мультиязычного слоя."""
from ankicards.config import get_language


def test_load_norwegian_language():
    """Норвежский language.yaml корректно загружается."""
    lang = get_language("nb")
    assert lang.code == "nb"
    assert lang.article is True
    assert lang.pos_labels["noun"] == "substantiv"
    assert lang.pos_labels["verb"] == "verb"
    assert len(lang.forms["noun"]) == 5
    assert lang.forms["noun"][0]["key"] == "gender"
    assert lang.tts["voice_female"] == "nb-NO-PernilleNeural"
    assert lang.anki["deck_name"] == "Norsk"
    assert lang.back_labels["translation"] == "Перевод"


def test_load_german_language():
    """Немецкий language.yaml корректно загружается."""
    lang = get_language("de")
    assert lang.code == "de"
    assert lang.article is True
    assert lang.pos_labels["noun"] == "Substantiv / Nomen"
    assert lang.pos_labels["adj"] == "Adjektiv"
    # Немецкие существительные: 7 полей (род, артикль, 4 падежа, мн.ч)
    assert len(lang.forms["noun"]) == 7
    noun_keys = [f["key"] for f in lang.forms["noun"]]
    assert "nominative" in noun_keys
    assert "dative" in noun_keys
    assert "accusative" in noun_keys
    assert lang.tts["voice_male"] == "de-DE-ConradNeural"
    assert lang.anki["deck_name"] == "Deutsch"


def test_language_prompts_directory():
    """prompts_dir ведёт в languages/{code}/prompts."""
    lang = get_language("nb")
    assert lang.prompts_dir.name == "prompts"
    assert "nb" in str(lang.prompts_dir)


def test_default_language_is_norwegian():
    """По умолчанию (без аргумента) — норвежский."""
    lang = get_language()
    assert lang.code == "nb"


def test_unknown_language_raises():
    """Несуществующий язык — FileNotFoundError."""
    import pytest
    with pytest.raises(FileNotFoundError):
        get_language("xx")