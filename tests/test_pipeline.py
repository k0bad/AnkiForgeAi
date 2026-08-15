"""Тесты для pipeline.run_ingest_pipeline: dedupe → enrich (по флагам) → media → save."""

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


def _make_config(tmp_path: Path, **enrich_overrides: bool) -> Config:
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
        dedupe=DedupeConfig(fuzzy_threshold_review=85, fuzzy_threshold_auto=70),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(**enrich_overrides),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


def _card(word: str, translation: str = "перевод") -> Card:
    return Card(word=word, pos=POS.NOUN, translation=translation)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


@pytest.fixture(autouse=True)
def _stub_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Аудио — не предмет этих тестов; заменяем на no-op, чтобы не трогать edge-tts."""

    async def _noop_audio(card: Card, cfg: Config) -> Card:
        return card

    monkeypatch.setattr(pipeline, "generate_audio", _noop_audio)


async def test_new_card_gets_approved_and_saved(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path)

    stats = await pipeline.run_ingest_pipeline([_card("hus")], db=db, cfg=cfg, auto_enrich=False)

    assert stats["new"] == 1
    assert stats["merged"] == 0
    saved = db.get_by_status(Status.APPROVED)
    assert len(saved) == 1
    assert saved[0].word == "hus"


async def test_exact_duplicate_is_merged_not_saved(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path)
    db.insert_card(_card("hus"))

    stats = await pipeline.run_ingest_pipeline([_card("Hus")], db=db, cfg=cfg, auto_enrich=False)

    assert stats["merged"] == 1
    assert stats["new"] == 0
    assert db.get_by_status(Status.APPROVED) == []


async def test_duplicate_within_same_batch_is_merged_not_both_saved(
    tmp_path: Path, db: Database
) -> None:
    """Регрессия на #13: LLM вернул одно и то же слово дважды в одном ответе —
    второе должно поймать первое как дубликат, а не проскочить как "new".
    Карточка вставляется в БД сразу при принятии (см. db.py::_next_free_id),
    так что уже принятая в этом же батче карточка видна следующей проверке
    через staging (db.all_words()) без отдельного механизма."""
    cfg = _make_config(tmp_path)

    stats = await pipeline.run_ingest_pipeline(
        [_card("hus"), _card("Hus")], db=db, cfg=cfg, auto_enrich=False
    )

    assert stats["new"] == 1
    assert stats["merged"] == 1
    assert len(db.get_by_status(Status.APPROVED)) == 1


async def test_enrich_stage_failure_routes_card_to_review_not_approved(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если batch-вызов enrichment падает, карточка не должна тихо стать APPROVED."""

    async def _boom(cards: list[Card]) -> list[Card]:
        raise RuntimeError("LLM недоступен")

    monkeypatch.setattr(pipeline, "enrich_grammar_batch", _boom)

    cfg = _make_config(tmp_path, grammar=True, examples=False, pronunciation=False)
    card = _card("hus")
    stats = await pipeline.run_ingest_pipeline(
        [card], db=db, cfg=cfg, auto_enrich=True, auto_media=False
    )

    assert stats["errors"] == 1
    assert stats["enrich_incomplete"] == 1
    assert db.get_by_status(Status.APPROVED) == []
    review_cards = db.get_by_status(Status.REVIEW)
    assert len(review_cards) == 1
    assert review_cards[0].word == "hus"

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT action, card_id FROM audit_log WHERE action = 'enrich_failed'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["card_id"] == card.id


async def test_pronunciation_stage_failure_routes_card_to_review_not_approved(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Регрессия: pronunciation раньше делила try/except с translation и при падении
    batch-вызова не помечала карточки incomplete — они тихо уходили в APPROVED
    без произношения (тот же баг, что #7 чинил для grammar/examples)."""

    async def _boom(cards: list[Card]) -> list[Card]:
        raise RuntimeError("LLM недоступен")

    monkeypatch.setattr(pipeline, "enrich_pronunciation_batch", _boom)

    cfg = _make_config(tmp_path, grammar=False, examples=False, pronunciation=True)
    card = _card("hus")
    stats = await pipeline.run_ingest_pipeline(
        [card], db=db, cfg=cfg, auto_enrich=True, auto_media=False
    )

    assert stats["errors"] == 1
    assert stats["enrich_incomplete"] == 1
    assert db.get_by_status(Status.APPROVED) == []
    review_cards = db.get_by_status(Status.REVIEW)
    assert len(review_cards) == 1
    assert review_cards[0].word == "hus"

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT action, card_id FROM audit_log WHERE action = 'enrich_failed'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["card_id"] == card.id


async def test_translation_missing_image_query_logs_warning_but_stays_approved(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Регрессия на #29: если LLM в translation-стадии распарсил RU, но не EN,
    card.image_query остаётся пустым — это должно залогироваться отдельным
    warning-событием, но не блокировать карточку (сам перевод корректен)."""

    async def _translate(card: Card) -> Card:
        card.translation = "дом"
        return card

    monkeypatch.setattr(pipeline, "enrich_translation", _translate)

    cfg = _make_config(tmp_path, grammar=False, examples=False, pronunciation=False)
    cfg.images.enabled = True
    card = _card("hus", translation="")
    stats = await pipeline.run_ingest_pipeline(
        [card], db=db, cfg=cfg, auto_enrich=True, auto_media=False
    )

    assert stats["enrich_incomplete"] == 0
    approved = db.get_by_status(Status.APPROVED)
    assert len(approved) == 1
    assert approved[0].translation == "дом"
    assert approved[0].image_query is None

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT action, card_id FROM audit_log WHERE action = 'translation.image_query_missing'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["card_id"] == card.id


async def test_translation_image_query_missing_not_logged_when_images_disabled(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тот же пробел в image_query не должен шуметь в логах, если картинки вообще
    выключены (images.enabled=False) — поле для такой карточки не используется."""

    async def _translate(card: Card) -> Card:
        card.translation = "дом"
        return card

    monkeypatch.setattr(pipeline, "enrich_translation", _translate)

    cfg = _make_config(tmp_path, grammar=False, examples=False, pronunciation=False)
    assert cfg.images.enabled is False
    card = _card("hus", translation="")
    await pipeline.run_ingest_pipeline([card], db=db, cfg=cfg, auto_enrich=True, auto_media=False)

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT action FROM audit_log WHERE action = 'translation.image_query_missing'"
        ).fetchall()
    assert rows == []


async def test_topic_ingest_shaped_card_still_gets_image_query_backfilled(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #47: карточка, пришедшая с уже заполненным translation (как из
    ingest topic) и подходящая под images.only_for_pos, всё равно должна
    прогнать enrich_translation ради image_query — раньше это молча
    пропускалось навсегда, и вся цепочка #10/#29/#34/#35 не применялась
    к основному workflow проекта."""
    calls: list[Card] = []

    async def _fake_translate(card: Card) -> Card:
        calls.append(card)
        card.image_query = "house"
        return card

    monkeypatch.setattr(pipeline, "enrich_translation", _fake_translate)

    cfg = _make_config(tmp_path, grammar=False, examples=False, pronunciation=False)
    cfg.images.enabled = True
    card = _card("hus", translation="дом")  # translation уже есть — как после ingest topic

    await pipeline.run_ingest_pipeline([card], db=db, cfg=cfg, auto_enrich=True, auto_media=False)

    assert calls == [card]
    assert card.translation == "дом"
    assert card.image_query == "house"


async def test_non_noun_with_existing_translation_skips_translation_stage(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Контраст с тестом выше: карточка не из images.only_for_pos (verb) и с уже
    заполненным translation не должна вызывать enrich_translation вообще — ей
    image_query никогда не понадобится, лишний LLM-вызов был бы просто тратой."""
    calls: list[Card] = []

    async def _fake_translate(card: Card) -> Card:
        calls.append(card)
        return card

    monkeypatch.setattr(pipeline, "enrich_translation", _fake_translate)

    cfg = _make_config(tmp_path, grammar=False, examples=False, pronunciation=False)
    cfg.images.enabled = True
    card = Card(word="spise", pos=POS.VERB, translation="есть")

    await pipeline.run_ingest_pipeline([card], db=db, cfg=cfg, auto_enrich=True, auto_media=False)

    assert calls == []


async def test_enrich_partial_llm_response_routes_only_that_card_to_review(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если LLM в успешном batch-ответе не вернул данные для одной карточки —
    APPROVED получает только полностью обогащённая карточка, вторая уходит в REVIEW."""

    async def _partial_grammar(cards: list[Card]) -> list[Card]:
        # Отвечаем только за первую карточку, вторую LLM "забыл" вернуть.
        cards[0].forms = {"gender": "m"}
        return cards

    monkeypatch.setattr(pipeline, "enrich_grammar_batch", _partial_grammar)

    cfg = _make_config(tmp_path, grammar=True, examples=False, pronunciation=False)
    ok_card = _card("hus")
    missing_card = _card("bil")
    stats = await pipeline.run_ingest_pipeline(
        [ok_card, missing_card], db=db, cfg=cfg, auto_enrich=True, auto_media=False
    )

    assert stats["enrich_incomplete"] == 1
    approved_words = {c.word for c in db.get_by_status(Status.APPROVED)}
    review_words = {c.word for c in db.get_by_status(Status.REVIEW)}
    assert approved_words == {"hus"}
    assert review_words == {"bil"}


async def test_enrich_flags_gate_which_stages_run(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cfg.enrich.* должны реально включать/выключать соответствующие LLM-стадии."""
    calls = {"pronunciation": 0, "grammar": 0, "examples": 0}

    def _tracker(key: str):
        async def _tracked(cards: list[Card]) -> list[Card]:
            calls[key] += 1
            return cards

        return _tracked

    monkeypatch.setattr(pipeline, "enrich_pronunciation_batch", _tracker("pronunciation"))
    monkeypatch.setattr(pipeline, "enrich_grammar_batch", _tracker("grammar"))
    monkeypatch.setattr(pipeline, "enrich_example_batch", _tracker("examples"))

    cfg = _make_config(tmp_path, grammar=False, examples=False, pronunciation=True)
    await pipeline.run_ingest_pipeline(
        [_card("hus")], db=db, cfg=cfg, auto_enrich=True, auto_media=False
    )

    assert calls == {"pronunciation": 1, "grammar": 0, "examples": 0}
