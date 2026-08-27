"""Что происходит на accept: обогащение идёт пачками, а часть речи доопределяется.

`review html` собирает accept сразу на весь просмотренный список, а enrich-стадия
шлёт один LLM-вызов на всё переданное — без разбивки формы теряла бы вся партия,
а обрыв посреди отменял бы уже сделанное.
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
from ankicards.enrich import pos as pos_stage
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


async def test_accept_enriches_in_batches_instead_of_one_huge_llm_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`review html` собирает accept сразу на весь просмотренный список, а enrich-стадия
    шлёт один вызов на всё переданное — без разбивки формы теряла бы вся партия."""
    from ankicards.review import actions

    cfg = _config(tmp_path)
    db = Database(tmp_path / "cards.db")
    for n in range(7):
        db.insert_card(Card(language="nb", word=f"ord{n}", pos=POS.NOUN, translation=f"с{n}"))
    sizes: list[int] = []

    async def _spy(cards, db_, cfg_, **kw):  # type: ignore[no-untyped-def]
        sizes.append(len(cards))
        return {}, set()

    monkeypatch.setattr(actions, "enrich_and_generate_media", _spy)

    results = await actions.accept_cards(list(range(1, 8)), db, cfg, batch_size=3)

    assert sizes == [3, 3, 1]
    assert len(results) == 7
    assert all(status == Status.APPROVED.value for status in results.values())


async def test_accept_keeps_earlier_batches_when_a_later_one_blows_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Карточки сохраняются пачка за пачкой, а не одной транзакцией в конце."""
    from ankicards.review import actions

    cfg = _config(tmp_path)
    db = Database(tmp_path / "cards.db")
    for n in range(4):
        db.insert_card(Card(language="nb", word=f"ord{n}", pos=POS.NOUN, translation=f"с{n}"))
    calls = 0

    async def _boom_on_second(cards, db_, cfg_, **kw):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("провайдер лёг")
        return {}, set()

    monkeypatch.setattr(actions, "enrich_and_generate_media", _boom_on_second)

    with pytest.raises(RuntimeError):
        await actions.accept_cards([1, 2, 3, 4], db, cfg, verified=True, batch_size=2)

    assert [c.word for c in db.get_by_status(Status.APPROVED)] == ["ord0", "ord1"]
    assert db.get_by_id(1).is_verified()  # type: ignore[union-attr]
    assert not db.get_by_id(3).is_verified()  # type: ignore[union-attr]


async def test_accept_resolves_a_part_of_speech_the_import_left_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После `--no-enrich` карточка приходит POS.OTHER, и добрать часть речи больше
    негде: импортёр её уже не увидит. Без этого она молча остаётся и без граммати-
    ческих форм (INFLECTED_POS), и без тега pos::noun, которым в Anki отделяют
    колоду существительных от колоды глаголов.
    """
    cfg = _config(tmp_path)
    # Остальные стадии выключены намеренно: проверяется только определение части
    # речи, а с ними тест уходил бы в живой LLM на минуту с лишним.
    cfg.enrich.pronunciation = cfg.enrich.grammar = cfg.enrich.examples = False
    db = Database(tmp_path / "cards.db")

    async def _classified(prompt: str, **_: object) -> list[dict]:
        return [{"id": 0, "pos": "noun"}]

    monkeypatch.setattr(pos_stage, "load_prompt", lambda _name, **kw: kw["words_json"])
    monkeypatch.setattr(pos_stage, "call_json", _classified)

    card = Card(language="nb", word="tenner", pos=POS.OTHER, translation="зубы")
    db.insert_card(card)

    await pipeline.enrich_and_generate_media([card], db, cfg, auto_enrich=True, auto_media=False)
    # Ровно то, что делают оба вызывающих места — run_ingest_pipeline и
    # review.actions.accept_cards: обогащение мутирует объект, сохраняет вызывающий.
    db.update_card(card)

    # Читаем из БД, а не из объекта: определить часть речи мало, её надо ещё
    # сохранить, а UPDATE в update_card эту колонку не трогал вовсе.
    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.pos is POS.NOUN
    assert "pos::noun" in saved.auto_tags()
