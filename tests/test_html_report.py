"""Тесты страницы ревью: то, что ломается тихо и обнаруживается только глазами."""

from __future__ import annotations

import re
from pathlib import Path

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
from ankicards.models import POS, Card
from ankicards.review import html_report

# Нативный аудиоплеер Chrome ровно такой высоты. Его кнопки живут в shadow DOM и
# не масштабируются: любая меньшая высота их обрезает, и по «play» становится не
# попасть — при том что звук исправно играет из кода, так что со стороны это
# выглядит как пропавшее аудио, а не как вёрстка.
NATIVE_AUDIO_CONTROL_HEIGHT_PX = 54


def _config(tmp_path: Path) -> Config:
    return Config(
        language="nb",
        paths=PathsConfig(
            db=tmp_path / "cards.db",
            logs_dir=tmp_path / "logs",
            audio_dir=tmp_path / "audio",
            images_dir=tmp_path / "images",
            prompts_dir=tmp_path / "prompts",
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


def _page(tmp_path: Path) -> str:
    cfg = _config(tmp_path)
    cfg.paths.audio_dir.mkdir(parents=True, exist_ok=True)
    card = Card(id=1, language="nb", word="sti", pos=POS.NOUN, translation="тропа")
    card.audio = "1_nb.mp3"
    (cfg.paths.audio_dir / card.audio).write_bytes(b"ID3\x04\x00fake")
    return html_report.build_report([card], cfg)


def test_audio_player_is_not_squashed_below_its_native_height(tmp_path: Path) -> None:
    page = _page(tmp_path)

    rule = re.search(r"\.audio\s*\{([^}]*)\}", page)
    assert rule, "правило .audio пропало — плеер верстается неизвестно как"

    height = re.search(r"(?<!-)\bheight\s*:\s*(\d+)px", rule.group(1))
    assert height is None or int(height.group(1)) >= NATIVE_AUDIO_CONTROL_HEIGHT_PX, (
        f"высота {height.group(1) if height else '?'}px обрежет кнопки плеера "
        f"(нативная — {NATIVE_AUDIO_CONTROL_HEIGHT_PX}px)"
    )


def test_audio_is_embedded_as_a_playable_data_uri(tmp_path: Path) -> None:
    """Страница обязана быть самодостаточной: ссылка на файл на диске переживёт
    открытие локально, но не пересылку и не Artifact."""
    page = _page(tmp_path)

    assert 'src="data:audio/mpeg;base64,' in page
    assert "<audio" in page and "controls" in page
