"""Колбэк on_inserted зовётся и для карточек, которых dedupe увёл в review.

Это единственная точка, где источник со своим медиа успевает подложить файлы:
у карточки уже есть id (а значит и детерминированные имена, CLAUDE.md принцип 6),
но media-стадия ещё не отработала. Раньше колбэк звался только для accepted, и
карточка, отправленная на человеческую адъюдикацию, оставалась без фото и звука
навсегда — второй раз ingest её не создаст, dedupe увидит её саму как дубль.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards import pipeline
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
from ankicards.db import Database
from ankicards.models import POS, Card, Status


def _config(tmp_path: Path) -> Config:
    return Config(
        language="nb",
        paths=PathsConfig(
            db=tmp_path / "data" / "cards.db",
            logs_dir=tmp_path / "logs",
            audio_dir=tmp_path / "audio",
            images_dir=tmp_path / "images",
            prompts_dir=tmp_path / "prompts",
        ),
        anki=AnkiConfig(),
        dedupe=DedupeConfig(ai_adjudication=False),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


async def _passthrough_judge(_card: Card, decision: object, _cfg: Config) -> object:
    """LLM-адъюдикация в тесте не нужна: проверяется ветка, в которую dedupe уже попал."""
    return decision


async def test_hook_also_runs_for_cards_dedupe_sent_to_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Карточка, которую dedupe увёл на человеческую адъюдикацию, тоже получает медиа.

    Точка, где источник подкладывает своё фото и аудио, ровно одна — этот колбэк,
    и повторный ingest
    той же темы её не спасёт: dedupe увидит уже лежащую в БД карточку как дубль. Без
    этого человек решает судьбу карточки, глядя на пустое место вместо фотографии.
    """
    cfg = _config(tmp_path)
    db = Database(tmp_path / "cards.db")
    seen: list[str] = []

    async def _hook(cards: list[Card]) -> None:
        for card in cards:
            seen.append(card.word)
            path = cfg.paths.audio_dir / f"{card.id}_nb.mp3"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"audio")
            card.audio = path.name

    # Первая карточка ложится в БД, вторая почти повторяет её по написанию — на ней
    # dedupe и срабатывает, отправляя в review вместо accepted.
    await pipeline.run_ingest_pipeline(
        [Card(language="nb", word="storesøster", pos=POS.NOUN, translation="старшая сестра")],
        db=db,
        cfg=cfg,
        auto_enrich=False,
        auto_media=False,
        force_review=True,
    )
    seen.clear()

    monkeypatch.setattr(pipeline, "judge_review", _passthrough_judge)
    stats = await pipeline.run_ingest_pipeline(
        [Card(language="nb", word="storesøstera", pos=POS.NOUN, translation="старшая сестра")],
        db=db,
        cfg=cfg,
        auto_enrich=False,
        auto_media=False,
        on_inserted=_hook,
        force_review=True,
    )

    assert stats["review"] == 1, "иначе тест проверяет не ту ветку — dedupe карточку пропустил"
    assert seen == ["storesøstera"]
    # Читаем из БД, а не из объекта: сохранить карточку после колбэка — забота
    # пайплайна, review-ветка вставила её раньше и сама к ней не возвращается.
    saved = [c for c in db.get_by_status(Status.REVIEW) if c.word == "storesøstera"]
    assert saved and saved[0].audio == f"{saved[0].id}_nb.mp3"
