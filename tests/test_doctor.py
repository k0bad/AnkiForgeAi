"""Тесты для doctor.find_inconsistencies: сверка enrich/images тумблеров с данными карточки."""

from __future__ import annotations

from pathlib import Path

import pytest

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
from ankicards.doctor import count_images_skipped_not_noun, find_inconsistencies
from ankicards.models import POS, Card, Status

_ALL_ENRICH_OFF = EnrichConfig(grammar=False, examples=False, pronunciation=False)


def _make_config(
    tmp_path: Path,
    *,
    enrich: EnrichConfig | None = None,
    images: ImagesConfig | None = None,
) -> Config:
    return Config(
        paths=PathsConfig(
            db=tmp_path / "test.db",
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
        images=images or ImagesConfig(),
        review=ReviewConfig(),
        enrich=enrich or EnrichConfig(),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


def _card(
    db: Database,
    word: str = "hus",
    pos: POS = POS.NOUN,
    status: Status = Status.APPROVED,
    image: str | None = "img.jpg",
    **overrides: object,
) -> Card:
    # image заполнен по умолчанию, чтобы не задевать images-проверку в тестах других check'ов
    card = Card(
        language="nb",
        word=word,
        pos=pos,
        translation="дом",
        status=status,
        image=image,
        **overrides,
    )
    db.insert_card(card)  # find_inconsistencies работает с уже сохранёнными карточками (id: int)
    return card


def test_missing_forms_flagged_when_grammar_enabled(tmp_path: Path, db: Database) -> None:
    enrich = EnrichConfig(grammar=True, examples=False, pronunciation=False)
    cfg = _make_config(tmp_path, enrich=enrich)
    card = _card(db, forms=None)

    problems = find_inconsistencies([card], cfg)

    assert len(problems) == 1
    assert problems[0].check == "enrich.grammar"
    assert problems[0].card_id == card.id


def test_populated_forms_not_flagged(tmp_path: Path, db: Database) -> None:
    enrich = EnrichConfig(grammar=True, examples=False, pronunciation=False)
    cfg = _make_config(tmp_path, enrich=enrich)
    card = _card(db, forms={"gender": "m"})

    assert find_inconsistencies([card], cfg) == []


def test_missing_forms_not_flagged_for_non_inflected_pos(tmp_path: Path, db: Database) -> None:
    """enrich/grammar.py заполняет forms только для noun/verb/adj — для остальных
    частей речи forms=None корректно, даже когда enrich.grammar=true."""
    enrich = EnrichConfig(grammar=True, examples=False, pronunciation=False)
    cfg = _make_config(tmp_path, enrich=enrich)
    card = _card(db, pos=POS.ADVERB, forms=None)

    assert find_inconsistencies([card], cfg) == []


def test_missing_example_flagged_when_examples_enabled(tmp_path: Path, db: Database) -> None:
    enrich = EnrichConfig(grammar=False, examples=True, pronunciation=False)
    cfg = _make_config(tmp_path, enrich=enrich)
    card = _card(db, example=None)

    problems = find_inconsistencies([card], cfg)

    assert len(problems) == 1
    assert problems[0].check == "enrich.examples"


def test_missing_pronunciation_flagged_when_pronunciation_enabled(
    tmp_path: Path, db: Database
) -> None:
    enrich = EnrichConfig(grammar=False, examples=False, pronunciation=True)
    cfg = _make_config(tmp_path, enrich=enrich)
    card = _card(db, pronunciation=None)

    problems = find_inconsistencies([card], cfg)

    assert len(problems) == 1
    assert problems[0].check == "enrich.pronunciation"


def test_disabled_toggles_do_not_flag_empty_fields(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path, enrich=_ALL_ENRICH_OFF)
    card = _card(db, forms=None, example=None, pronunciation=None)

    assert find_inconsistencies([card], cfg) == []


def test_missing_image_flagged_for_pos_in_only_for_pos(tmp_path: Path, db: Database) -> None:
    images = ImagesConfig(enabled=True, only_for_pos=["noun"])
    cfg = _make_config(tmp_path, enrich=_ALL_ENRICH_OFF, images=images)
    card = _card(db, pos=POS.NOUN, image=None)

    problems = find_inconsistencies([card], cfg)

    assert len(problems) == 1
    assert problems[0].check == "images.enabled"


def test_missing_image_not_flagged_for_pos_outside_only_for_pos(
    tmp_path: Path, db: Database
) -> None:
    images = ImagesConfig(enabled=True, only_for_pos=["noun"])
    cfg = _make_config(tmp_path, enrich=_ALL_ENRICH_OFF, images=images)
    card = _card(db, pos=POS.VERB, image=None)

    assert find_inconsistencies([card], cfg) == []


def test_missing_image_not_flagged_when_images_disabled(tmp_path: Path, db: Database) -> None:
    images = ImagesConfig(enabled=False, only_for_pos=["noun"])
    cfg = _make_config(tmp_path, enrich=_ALL_ENRICH_OFF, images=images)
    card = _card(db, pos=POS.NOUN, image=None)

    assert find_inconsistencies([card], cfg) == []


# ───────────── count_images_skipped_not_noun ─────────────


def test_count_images_skipped_not_noun_counts_pos_outside_only_for_pos(
    tmp_path: Path, db: Database
) -> None:
    images = ImagesConfig(enabled=True, only_for_pos=["noun"])
    cfg = _make_config(tmp_path, enrich=_ALL_ENRICH_OFF, images=images)
    _card(db, word="å smake", pos=POS.VERB, image=None)
    _card(db, word="søt", pos=POS.ADJECTIVE, image=None)
    _card(db, word="hus", pos=POS.NOUN, image="hus.jpg")  # noun с картинкой — не считается

    cards = db.get_by_status(Status.APPROVED)

    assert count_images_skipped_not_noun(cards, cfg) == 2


def test_count_images_skipped_not_noun_excludes_flagged_provider_empty(
    tmp_path: Path, db: Database
) -> None:
    """Карточка noun без картинки — это check=images.enabled в find_inconsistencies,
    а не "не тот POS"; count_images_skipped_not_noun не должен её тоже посчитать."""
    images = ImagesConfig(enabled=True, only_for_pos=["noun"])
    cfg = _make_config(tmp_path, enrich=_ALL_ENRICH_OFF, images=images)
    _card(db, pos=POS.NOUN, image=None)

    cards = db.get_by_status(Status.APPROVED)

    assert count_images_skipped_not_noun(cards, cfg) == 0


def test_count_images_skipped_not_noun_zero_when_images_disabled(
    tmp_path: Path, db: Database
) -> None:
    images = ImagesConfig(enabled=False, only_for_pos=["noun"])
    cfg = _make_config(tmp_path, enrich=_ALL_ENRICH_OFF, images=images)
    _card(db, pos=POS.VERB, image=None)

    cards = db.get_by_status(Status.APPROVED)

    assert count_images_skipped_not_noun(cards, cfg) == 0


def test_count_images_skipped_not_noun_excludes_pending_and_review(
    tmp_path: Path, db: Database
) -> None:
    images = ImagesConfig(enabled=True, only_for_pos=["noun"])
    cfg = _make_config(tmp_path, enrich=_ALL_ENRICH_OFF, images=images)
    cards = [
        _card(db, pos=POS.VERB, image=None, status=Status.PENDING),
        _card(db, pos=POS.VERB, image=None, status=Status.REVIEW),
    ]

    assert count_images_skipped_not_noun(cards, cfg) == 0


def test_pushed_card_missing_anki_note_id_flagged(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path, enrich=_ALL_ENRICH_OFF)
    card = _card(db, status=Status.PUSHED, anki_note_id=None)

    problems = find_inconsistencies([card], cfg)

    assert len(problems) == 1
    assert problems[0].check == "anki_note_id"


def test_pushed_card_with_anki_note_id_not_flagged(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path, enrich=_ALL_ENRICH_OFF)
    card = _card(db, status=Status.PUSHED, anki_note_id=12345)

    assert find_inconsistencies([card], cfg) == []


def test_pending_and_review_cards_are_skipped_entirely(tmp_path: Path, db: Database) -> None:
    """Карточки, ещё не прошедшие pipeline целиком — пустые поля там не баг."""
    cfg = _make_config(tmp_path)  # дефолт: все enrich- и images-тумблеры включены
    cards = [
        _card(db, status=Status.PENDING, forms=None, example=None, pronunciation=None, image=None),
        _card(db, status=Status.REVIEW, forms=None, example=None, pronunciation=None, image=None),
    ]

    assert find_inconsistencies(cards, cfg) == []


def test_multiple_problems_on_one_card_all_reported(tmp_path: Path, db: Database) -> None:
    enrich = EnrichConfig(grammar=True, examples=True, pronunciation=True)
    images = ImagesConfig(enabled=True, only_for_pos=["noun"])
    cfg = _make_config(tmp_path, enrich=enrich, images=images)
    card = _card(db, pos=POS.NOUN, forms=None, example=None, pronunciation=None, image=None)

    problems = find_inconsistencies([card], cfg)

    checks = {p.check for p in problems}
    assert checks == {"enrich.grammar", "enrich.examples", "enrich.pronunciation", "images.enabled"}


def test_fully_enriched_card_has_no_problems(tmp_path: Path, db: Database) -> None:
    enrich = EnrichConfig(grammar=True, examples=True, pronunciation=True)
    images = ImagesConfig(enabled=True, only_for_pos=["noun"])
    cfg = _make_config(tmp_path, enrich=enrich, images=images)
    card = _card(
        db,
        pos=POS.NOUN,
        forms={"gender": "m"},
        example="Jeg har et hus.",
        pronunciation="хус",
        image="abc.jpg",
        status=Status.PUSHED,
        anki_note_id=1,
    )

    assert find_inconsistencies([card], cfg) == []


class _FakeAnkiSync:
    """Минимальный двойник AnkiConnect — только то, что нужно sync_anki_to_cache."""

    async def find_notes(self, query: str) -> list[int]:
        return [1]

    async def notes_info(self, note_ids: list[int]) -> list[dict]:
        return [{"noteId": 1, "fields": {"Word": {"value": "hus"}}, "tags": []}]


async def test_corruption_still_caught_after_anki_sync_populates_cache(
    tmp_path: Path, db: Database
) -> None:
    """issue #28 checklist: "manually corrupt one card ... confirm doctor flags it"
    против *live-synced* БД, не просто bare-фикстуры. find_inconsistencies() читает
    только переданный ей list[Card] (из таблицы cards) — anki_cache (её заполняет
    `sync`) на её проверки не влияет вообще; это и проверяем явно, а не оставляем
    неявным допущением: синкаем в anki_cache "здоровую" заметку, при этом отдельно
    корраптим карточку в cards (roundtrip через настоящий SQLite, как это делает
    сама CLI-команда `doctor` через db.get_by_status), и убеждаемся, что doctor
    её всё равно ловит."""
    enrich = EnrichConfig(grammar=True, examples=False, pronunciation=False)
    cfg = _make_config(tmp_path, enrich=enrich)

    corrupted = _card(db, word="bil", status=Status.PUSHED, forms=None, anki_note_id=None)

    synced = await sync_anki_to_cache(db, _FakeAnkiSync(), cfg)  # type: ignore[arg-type]
    assert synced == 1
    assert db.all_anki_words() == [(1, "hus")]

    cards = db.get_by_status(Status.APPROVED) + db.get_by_status(Status.PUSHED)
    problems = find_inconsistencies(cards, cfg)

    checks = {(p.card_id, p.check) for p in problems}
    assert checks == {(corrupted.id, "enrich.grammar"), (corrupted.id, "anki_note_id")}
