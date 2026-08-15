"""Тесты для config.py: resolve_paths() должен давать prompts_dir тот же
bundled-фолбэк, что languages_dir() уже даёт для languages/ (issue #38 —
pip-установленный пользователь без локального prompts/ иначе падает на
первом же промпте без per-language копии, например dedupe_judge.md)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards import config as config_module
from ankicards import llm as llm_module
from ankicards.config import (
    AnkiConfig,
    Config,
    DedupeConfig,
    EnrichConfig,
    ImagesConfig,
    IngestConfig,
    LLMConfig,
    LoggingConfig,
    PathsConfig,
    ReviewConfig,
    TagsConfig,
    TTSConfig,
)


def _make_config(prompts_dir: Path) -> Config:
    return Config(
        language="nb",
        paths=PathsConfig(
            db=Path("db.sqlite"),
            logs_dir=Path("logs"),
            audio_dir=Path("audio"),
            images_dir=Path("images"),
            prompts_dir=prompts_dir,
        ),
        anki=AnkiConfig(),
        dedupe=DedupeConfig(),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(),
        review=ReviewConfig(),
        enrich=EnrichConfig(),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


def test_resolve_paths_falls_back_to_bundled_prompts_dir_when_checkout_copy_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой cwd без локального prompts/ (симулирует pip install вне checkout'а) —
    resolve_paths() должен подставить BUNDLED_PROMPTS_DIR, а не оставить путь,
    указывающий на несуществующую директорию."""
    bundled = tmp_path / "bundled_prompts"
    bundled.mkdir()
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_module, "BUNDLED_PROMPTS_DIR", bundled)

    cfg = _make_config(Path("prompts"))
    cfg.resolve_paths()

    assert cfg.paths.prompts_dir == bundled


def test_resolve_paths_keeps_existing_checkout_prompts_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если локальный prompts/ реально существует (обычный git checkout) — фолбэк
    не должен подменять его на bundled-копию."""
    checkout_prompts = tmp_path / "prompts"
    checkout_prompts.mkdir()
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    cfg = _make_config(Path("prompts"))
    cfg.resolve_paths()

    assert cfg.paths.prompts_dir == checkout_prompts


def test_load_prompt_resolves_bundled_copy_with_no_per_language_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Регрессия на #38: dedupe_judge.md не имеет per-language копий ни в одном
    языковом профиле, так что живёт только фолбэком через paths.prompts_dir.
    Без бандл-фолбэка это FileNotFoundError вне git checkout'а (воспроизведено
    в issue: пустой cwd без локальных languages/ и prompts/)."""
    bundled = tmp_path / "bundled_prompts"
    bundled.mkdir()
    (bundled / "dedupe_judge.md").write_text("bundled dedupe judge prompt", encoding="utf-8")
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_module, "BUNDLED_PROMPTS_DIR", bundled)

    cfg = _make_config(Path("prompts"))
    cfg.resolve_paths()
    monkeypatch.setattr(llm_module, "get_config", lambda: cfg)

    result = llm_module.load_prompt("dedupe_judge")

    assert result == "bundled dedupe judge prompt"
