"""Тесты для review/actions.py: неинтерактивные review-действия должны и менять
статус в SQLite, и оставлять след в audit_log (issue #31 — review-действия
логируются через тот же pipeline._record(), что enrich/media-стадии, так что
структурированная трасса и персистентный аудит пишутся одним вызовом), а
delete_cards должен удалять карточку насовсем (Anki + локальная БД) и
освобождать её номер для переиспользования."""

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
from ankicards.review import actions


def _card(word: str, status: Status = Status.REVIEW) -> Card:
    return Card(language="nb", word=word, pos=POS.NOUN, translation="дом", status=status)


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
        dedupe=DedupeConfig(),
        ingest=IngestConfig(),
        llm=LLMConfig(),
        tts=TTSConfig(),
        images=ImagesConfig(enabled=True),
        review=ReviewConfig(),
        enrich=EnrichConfig(grammar=False, examples=False, pronunciation=False),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


def _audit_actions(db: Database, card_id: int) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT action FROM audit_log WHERE card_id = ? ORDER BY id", (card_id,)
        ).fetchall()
    return [r["action"] for r in rows]


def _mark_pushed(db: Database, card: Card, note_id: int) -> Card:
    with db.connect() as conn:
        conn.execute(
            "UPDATE cards SET status = ?, anki_note_id = ? WHERE id = ?",
            (Status.PUSHED.value, note_id, card.id),
        )
    reloaded = db.get_by_id(card.id)
    assert reloaded is not None
    return reloaded


class _FakeAnki:
    def __init__(self) -> None:
        self.deleted: list[list[int]] = []

    async def delete_notes(self, note_ids: list[int]) -> None:
        self.deleted.append(note_ids)


def test_skip_cards_updates_status_and_logs_action(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)

    actions.skip_cards([card.id], db)

    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.SKIPPED
    assert "review_skip" in _audit_actions(db, card.id)


def test_suspend_cards_updates_status_and_logs_action(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)

    actions.suspend_cards([card.id], db)

    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.SUSPENDED
    assert "review_suspend" in _audit_actions(db, card.id)


def test_resume_cards_updates_status_and_logs_action(db: Database) -> None:
    card = _card("hus", status=Status.SUSPENDED)
    db.insert_card(card)

    actions.resume_cards([card.id], db)

    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.REVIEW
    assert "review_resume" in _audit_actions(db, card.id)


def test_edit_card_updates_fields_and_logs_action(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)

    updated = actions.edit_card(card.id, {"translation": "домик"}, db)

    assert updated.translation == "домик"
    assert "review_edit" in _audit_actions(db, card.id)


def test_set_status_raises_on_missing_card(db: Database) -> None:
    with pytest.raises(ValueError, match="не найдены"):
        actions.skip_cards([999], db)


def test_skip_cards_rejects_id_from_different_language(db: Database) -> None:
    """Issue #63: --language на review-командах должен ловить чужой-язык id
    (устаревший список, опечатка) жёсткой ошибкой, а не тихим действием над
    карточкой другого языка."""
    nb_card = _card("hus")
    de_card = Card(
        language="de", word="Haus", pos=POS.NOUN, translation="дом", status=Status.REVIEW
    )
    db.insert_card(nb_card)
    db.insert_card(de_card)

    with pytest.raises(ValueError, match="другого языка"):
        actions.skip_cards([nb_card.id, de_card.id], db, language="nb")

    # Ошибка — до начала цикла обновлений: ни одна карточка не тронута.
    saved_nb = db.get_by_id(nb_card.id)
    saved_de = db.get_by_id(de_card.id)
    assert saved_nb is not None and saved_nb.status == Status.REVIEW
    assert saved_de is not None and saved_de.status == Status.REVIEW


def test_skip_cards_accepts_matching_language(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)

    actions.skip_cards([card.id], db, language="nb")

    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.SKIPPED


async def test_delete_cards_removes_pushed_note_and_row(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)
    card = _mark_pushed(db, card, note_id=12345)

    anki = _FakeAnki()
    deleted = await actions.delete_cards([card.id], db, anki)  # type: ignore[arg-type]

    assert deleted == [card.id]
    assert anki.deleted == [[12345]]
    assert db.get_by_id(card.id) is None


async def test_delete_cards_skips_anki_call_for_unpushed_card(db: Database) -> None:
    card = _card("hus")
    db.insert_card(card)  # ещё не запушена — anki_note_id пуст

    anki = _FakeAnki()
    deleted = await actions.delete_cards([card.id], db, anki)  # type: ignore[arg-type]

    assert deleted == [card.id]
    assert anki.deleted == []  # deleteNotes не звался — нечего удалять в Anki
    assert db.get_by_id(card.id) is None


async def test_delete_cards_frees_id_for_next_insert(db: Database) -> None:
    a, b, c = _card("hus"), _card("bil"), _card("katt")
    for card in (a, b, c):
        db.insert_card(card)

    await actions.delete_cards([b.id], db, _FakeAnki())  # type: ignore[arg-type]

    new_card = _card("fisk")
    db.insert_card(new_card)
    assert new_card.id == b.id


async def test_delete_cards_raises_for_unknown_id(db: Database) -> None:
    with pytest.raises(ValueError, match="не найдены"):
        await actions.delete_cards([999], db, _FakeAnki())  # type: ignore[arg-type]


async def test_accept_cards_auto_pick_images_false_does_not_attach_image(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #36: с auto_pick_images=False review_pending подбирает картинку сам
    после accept_cards — сама accept_cards не должна трогать attach_image вообще
    (ни поиска, ни автовыбора первого результата)."""
    called = False

    async def _spy_attach(card: Card, cfg: Config, auto_pick: bool = False) -> Card:
        nonlocal called
        called = True
        card.image = "should-not-happen.jpg"
        return card

    async def _noop_audio(card: Card, cfg: Config) -> Card:
        return card

    monkeypatch.setattr(pipeline, "attach_image", _spy_attach)
    monkeypatch.setattr(pipeline, "generate_audio", _noop_audio)

    cfg = _make_config(tmp_path)
    card = _card("hus")
    # translation и image_query уже заполнены — иначе _needs_translation_stage()
    # (issue #47) сочтёт карточку image-eligible-но-без-gloss и дернёт настоящий
    # enrich_translation()/call_text() без мока, что не имеет отношения к тому,
    # что проверяет этот тест (auto_pick_images/attach_image).
    card.image_query = "house"
    db.insert_card(card)

    results = await actions.accept_cards([card.id], db, cfg, auto_pick_images=False)

    assert called is False
    assert results[card.id] == Status.APPROVED.value
    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.image is None
