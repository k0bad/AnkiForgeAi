"""Тесты для setup_wizard._sync_skill_trigger_phrases (issue #25)."""

from __future__ import annotations

from pathlib import Path

from ankicards.setup_wizard import _SKILL_TRIGGER_EXAMPLES, _sync_skill_trigger_phrases

_SKILL_MD = f"""---
name: ankiforgeai
description: Drive the ankiforgeai CLI in this repo — {_SKILL_TRIGGER_EXAMPLES["ru"]}
---

# ankiforgeai CLI
Some other content that must not change.
"""


def _write_skill_md(tmp_path: Path, content: str = _SKILL_MD) -> Path:
    path = tmp_path / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_missing_file_is_a_noop(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist" / "SKILL.md"
    assert _sync_skill_trigger_phrases("en", skill_path=missing) is False
    assert not missing.exists()


def test_switches_to_english_examples(tmp_path: Path) -> None:
    path = _write_skill_md(tmp_path)
    changed = _sync_skill_trigger_phrases("en", skill_path=path)
    assert changed is True

    text = path.read_text(encoding="utf-8")
    assert '"generate words"' in text
    assert "сгенерируй" not in text
    # rest of the file (name, body) is untouched
    assert "name: ankiforgeai" in text
    assert "Some other content that must not change." in text


def test_ru_is_idempotent(tmp_path: Path) -> None:
    path = _write_skill_md(tmp_path)
    before = path.read_text(encoding="utf-8")
    changed = _sync_skill_trigger_phrases("ru", skill_path=path)
    assert changed is False
    assert path.read_text(encoding="utf-8") == before


def test_rerun_with_same_language_is_a_noop(tmp_path: Path) -> None:
    path = _write_skill_md(tmp_path)
    assert _sync_skill_trigger_phrases("en", skill_path=path) is True
    assert _sync_skill_trigger_phrases("en", skill_path=path) is False


def test_unknown_ui_lang_falls_back_to_english(tmp_path: Path) -> None:
    path = _write_skill_md(tmp_path)
    changed = _sync_skill_trigger_phrases("fr", skill_path=path)
    assert changed is True
    assert '"generate words"' in path.read_text(encoding="utf-8")


def test_description_without_expected_clause_is_a_noop(tmp_path: Path) -> None:
    path = _write_skill_md(
        tmp_path,
        content="---\nname: ankiforgeai\ndescription: no em dash clause here.\n---\n",
    )
    before = path.read_text(encoding="utf-8")
    assert _sync_skill_trigger_phrases("en", skill_path=path) is False
    assert path.read_text(encoding="utf-8") == before
