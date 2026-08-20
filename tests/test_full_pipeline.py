"""Полный прогон pipeline: ingest → dedupe → review accept → push → sync → doctor.

Закрывает пункт "Full pipeline dry run ... cross-check every Status transition
against the pipeline diagram in CLAUDE.md" из issue #28 — единственное, что
подменено, это HTTP-граница AnkiConnect (_FakeAnki, тот же двойник, что и в
test_push.py), чтобы прогон был детерминированным и не требовал живого Anki
в CI. Каждая стадия вызывается через настоящий продакшн-код
(pipeline.run_ingest_pipeline/push_approved, review.actions.accept_cards,
anki.sync.sync_anki_to_cache, doctor.find_inconsistencies) — до этого теста
эти модули были покрыты только по отдельности, переходы между ними никто
не проверял."""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards import pipeline
from ankicards.anki.sync import sync_anki_to_cache
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
from ankicards.doctor import find_inconsistencies
from ankicards.models import POS, Card, Status
from ankicards.review.actions import accept_cards


def _make_config(tmp_path: Path) -> Config:
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
        dedupe=DedupeConfig(
            fuzzy_threshold_review=85, fuzzy_threshold_auto=70, ai_adjudication=False
        ),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(grammar=True, examples=True, pronunciation=True),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


def _card(word: str, translation: str = "перевод") -> Card:
    return Card(language="nb", word=word, pos=POS.NOUN, translation=translation)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


class _FakeAnki:
    """Двойник AnkiConnect без реального HTTP: покрывает и push (ensure_deck/
    model_names/add_note, как _FakeAnki в test_push.py), и sync (find_notes/
    notes_info читают обратно то, что записал add_note)."""

    def __init__(self, models: list[str]) -> None:
        self._models = models
        self._notes: dict[int, dict] = {}
        self._next_id = 1000

    async def ensure_deck(self) -> None:
        pass

    async def model_names(self) -> list[str]:
        return self._models

    async def add_note(self, fields: dict, tags: list[str]) -> int:
        note_id = self._next_id
        self._next_id += 1
        self._notes[note_id] = {"fields": fields, "tags": tags}
        return note_id

    async def find_notes(self, query: str) -> list[int]:
        return list(self._notes)

    async def notes_info(self, note_ids: list[int]) -> list[dict]:
        return [
            {
                "noteId": nid,
                "fields": {k: {"value": v} for k, v in self._notes[nid]["fields"].items()},
                "tags": self._notes[nid]["tags"],
            }
            for nid in note_ids
            if nid in self._notes
        ]


@pytest.fixture(autouse=True)
def _stub_llm_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """grammar/examples/pronunciation и audio — не предмет этого теста (это
    механика Status-переходов, а не качество генерации); заменяем на
    детерминированные заглушки, чтобы не дергать реальные LLM/edge-tts.
    generate_audio стабим и здесь: accept_cards() зовёт enrich_and_generate_media
    с auto_media=True по умолчанию, так что review-accept путь тоже его вызывает."""

    async def _grammar(cards: list[Card]) -> list[Card]:
        for card in cards:
            card.forms = {"gender": "m"}
        return cards

    async def _examples(cards: list[Card]) -> list[Card]:
        for card in cards:
            card.example = f"Jeg har en {card.word}."
            card.example_translation = "У меня есть..."
        return cards

    async def _pronunciation(cards: list[Card]) -> list[Card]:
        for card in cards:
            card.pronunciation = f"[{card.word}]"
        return cards

    async def _audio_noop(card: Card, cfg: Config) -> Card:
        return card

    monkeypatch.setattr(pipeline, "enrich_grammar_batch", _grammar)
    monkeypatch.setattr(pipeline, "enrich_example_batch", _examples)
    monkeypatch.setattr(pipeline, "enrich_pronunciation_batch", _pronunciation)
    monkeypatch.setattr(pipeline, "generate_audio", _audio_noop)


async def test_full_pipeline_status_transitions_end_to_end(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path)

    # Существующая карточка в staging — цель для fuzzy-дубликата ниже (тот же
    # "kjøkken"/"kjokken" пример, что в test_dedupe.py::test_fuzzy_above_threshold_returns_review).
    db.insert_card(_card("kjøkken", translation="кухня"))

    # Ingest: "bok" ни на что не похож → new; "kjokken" фонетически совпадает
    # с уже существующим "kjøkken" → review.
    clean = _card("bok", translation="книга")
    near_dup = _card("kjokken", translation="кухня")

    stats = await pipeline.run_ingest_pipeline(
        [clean, near_dup], db=db, cfg=cfg, auto_enrich=True, auto_media=False
    )

    assert stats["new"] == 1
    assert stats["review"] == 1
    assert stats["merged"] == 0

    approved_after_ingest = db.get_by_status(Status.APPROVED)
    assert {c.word for c in approved_after_ingest} == {"bok"}
    assert approved_after_ingest[0].forms == {"gender": "m"}
    assert approved_after_ingest[0].example
    assert approved_after_ingest[0].pronunciation

    review_cards = db.get_by_status(Status.REVIEW)
    assert {c.word for c in review_cards} == {"kjokken"}
    review_card_id = review_cards[0].id

    # Review accept: тот же enrich_and_generate_media, что и ingest-путь (issue #11) —
    # карточка не должна попасть в approved без forms/example/pronunciation.
    results = await accept_cards([review_card_id], db, cfg)
    assert results[review_card_id] == Status.APPROVED.value
    assert db.get_by_status(Status.REVIEW) == []

    approved = db.get_by_status(Status.APPROVED)
    assert {c.word for c in approved} == {"bok", "kjokken"}

    # Push: approved → Anki (AnkiConnect.addNote), status=pushed, anki_note_id заполнен.
    anki = _FakeAnki(models=[cfg.anki.note_type])
    pushed_count = await pipeline.push_approved(db, anki, cfg)  # type: ignore[arg-type]
    assert pushed_count == 2
    assert db.get_by_status(Status.APPROVED) == []

    pushed = db.get_by_status(Status.PUSHED)
    assert {c.word for c in pushed} == {"bok", "kjokken"}
    assert all(c.anki_note_id is not None for c in pushed)

    # Sync: обратное направление, Anki → anki_cache (кэш для будущей dedupe).
    synced_count = await sync_anki_to_cache(db, anki, cfg)  # type: ignore[arg-type]
    assert synced_count == 2
    assert {w for _, w in db.all_anki_words()} == {"bok", "kjokken"}

    # Doctor: все enrich-тумблеры включены и реально заполнены → расхождений нет.
    problems = find_inconsistencies(pushed, cfg)
    assert problems == []


async def test_full_pipeline_doctor_catches_incomplete_pushed_card(
    tmp_path: Path, db: Database
) -> None:
    """Тот же маршрут, но одна LLM-стадия падает на review-этапе enrich — карточка
    не должна тихо дойти до pushed без enrichment (issue #7/#15); а если бы дошла,
    doctor обязан её поймать. Проверяет, что "живой" прогон и doctor согласованы
    друг с другом на негативном сценарии, не только на счастливом пути."""
    cfg = _make_config(tmp_path)
    card = _card("stol", translation="стул")
    db.insert_card(card)
    card.status = Status.APPROVED
    db.update_card(card)

    anki = _FakeAnki(models=[cfg.anki.note_type])
    pushed_count = await pipeline.push_approved(db, anki, cfg)  # type: ignore[arg-type]
    assert pushed_count == 1

    pushed = db.get_by_status(Status.PUSHED)
    assert len(pushed) == 1

    # Карточку запушили без прогона через enrich (мы вручную поставили approved) —
    # doctor обязан заметить пустые forms/example/pronunciation при включённых тумблерах.
    problems = find_inconsistencies(pushed, cfg)
    checks = {p.check for p in problems}
    assert checks == {"enrich.grammar", "enrich.examples", "enrich.pronunciation"}
