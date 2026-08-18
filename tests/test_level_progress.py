"""Тесты для db.count_by_level и pipeline.check_level_progress:

- count_by_level группирует карточки по (level, status), игнорирует карточки без level.
- check_level_progress подсказывает переход на следующий уровень только когда И в SQLite
  накопилось достаточно pushed-карточек уровня, И в самой Anki (source of truth) доля
  "зрелых" карточек (interval >= mature_interval_days) не ниже порога. Не должна вообще
  дёргать AnkiConnect, если по SQLite-счётчику карточек заведомо недостаточно.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
from ankicards.models import POS, Card, Level, Status
from ankicards.pipeline import check_level_progress


def _make_config(tmp_path: Path, ingest: IngestConfig | None = None) -> Config:
    return Config(
        language="nb",
        paths=PathsConfig(
            db=tmp_path / "test.db",
            logs_dir=tmp_path / "logs",
            audio_dir=tmp_path / "audio",
            images_dir=tmp_path / "images",
            prompts_dir=tmp_path / "prompts",
        ),
        anki=AnkiConfig(),
        dedupe=DedupeConfig(),
        ingest=ingest or IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(grammar=False, examples=False, pronunciation=False),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


def _card(word: str, level: Level | None, status: Status) -> Card:
    return Card(
        language="nb", word=word, pos=POS.NOUN, translation="перевод", level=level, status=status
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


class _NoCallAnki:
    """Двойник AnkiConnect, который падает, если до Anki вообще дошло дело —
    используется, когда check_level_progress должен отсеяться по SQLite-счётчику."""

    async def find_cards(self, query: str) -> list[int]:
        raise AssertionError("find_cards не должен вызываться при недостатке карточек в SQLite")

    async def cards_info(self, card_ids: list[int]) -> list[dict]:
        raise AssertionError("cards_info не должен вызываться при недостатке карточек в SQLite")


class _FakeAnki:
    def __init__(self, card_ids: list[int], infos: list[dict]) -> None:
        self._card_ids = card_ids
        self._infos = infos

    async def find_cards(self, query: str) -> list[int]:
        return self._card_ids

    async def cards_info(self, card_ids: list[int]) -> list[dict]:
        return self._infos


def test_count_by_level_groups_and_ignores_null_level(db: Database) -> None:
    db.insert_card(_card("hus", Level.A1, Status.PUSHED))
    db.insert_card(_card("bil", Level.A1, Status.PUSHED))
    db.insert_card(_card("katt", Level.A1, Status.REVIEW))
    db.insert_card(_card("hund", Level.A2, Status.PUSHED))
    db.insert_card(_card("uten_level", None, Status.PENDING))

    totals = db.count_by_level()

    assert totals == {
        "a1": {"pushed": 2, "review": 1},
        "a2": {"pushed": 1},
    }


async def test_no_hint_and_no_anki_call_when_below_min_cards(db: Database, tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, ingest=IngestConfig(level_progress_min_cards={"a1": 3}))
    db.insert_card(_card("hus", Level.A1, Status.PUSHED))
    db.insert_card(_card("bil", Level.A1, Status.PUSHED))

    hints = await check_level_progress(db, _NoCallAnki(), cfg)  # type: ignore[arg-type]

    assert hints == []


async def test_no_hint_when_mature_ratio_below_threshold(db: Database, tmp_path: Path) -> None:
    cfg = _make_config(
        tmp_path,
        ingest=IngestConfig(
            level_progress_min_cards={"a1": 2},
            level_progress_mature_ratio=0.8,
            level_progress_mature_interval_days=21,
        ),
    )
    db.insert_card(_card("hus", Level.A1, Status.PUSHED))
    db.insert_card(_card("bil", Level.A1, Status.PUSHED))
    anki = _FakeAnki(
        card_ids=[1, 2],
        infos=[{"interval": 30}, {"interval": 5}],  # только 50% зрелых, порог 80%
    )

    hints = await check_level_progress(db, anki, cfg)  # type: ignore[arg-type]

    assert hints == []


async def test_hint_when_both_thresholds_met(db: Database, tmp_path: Path) -> None:
    cfg = _make_config(
        tmp_path,
        ingest=IngestConfig(
            level_progress_min_cards={"a1": 2},
            level_progress_mature_ratio=0.8,
            level_progress_mature_interval_days=21,
        ),
    )
    db.insert_card(_card("hus", Level.A1, Status.PUSHED))
    db.insert_card(_card("bil", Level.A1, Status.PUSHED))
    anki = _FakeAnki(
        card_ids=[1, 2],
        infos=[{"interval": 30}, {"interval": 25}],  # 100% зрелых
    )

    hints = await check_level_progress(db, anki, cfg)  # type: ignore[arg-type]

    assert hints == [
        {
            "level": "a1",
            "next_level": "a2",
            "total": 2,
            "mature": 2,
            "mature_ratio": 1.0,
        }
    ]


async def test_no_hint_for_max_level_even_if_thresholds_met(db: Database, tmp_path: Path) -> None:
    # C2 не входит в level_progress_min_cards (см. IngestConfig) — расти с него уже
    # некуда, но даже если бы порог был явно задан, C2 всё равно не должен проверяться.
    cfg = _make_config(
        tmp_path,
        ingest=IngestConfig(level_progress_min_cards={"c2": 2}, level_progress_mature_ratio=0.8),
    )
    db.insert_card(_card("word1", Level.C2, Status.PUSHED))
    db.insert_card(_card("word2", Level.C2, Status.PUSHED))

    hints = await check_level_progress(db, _NoCallAnki(), cfg)  # type: ignore[arg-type]

    assert hints == []
