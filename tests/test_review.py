"""Тесты для review.interactive: accept должен реально прогонять enrich/media,
а не просто менять статус (issue #11)."""

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
from ankicards.models import POS, Card, Status
from ankicards.review import interactive as review_module


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
        images=ImagesConfig(enabled=False),
        review=ReviewConfig(),
        enrich=EnrichConfig(grammar=False, examples=False, pronunciation=False),
        logging=LoggingConfig(),
        tags=TagsConfig(),
    )


def _card(word: str) -> Card:
    return Card(language="nb", word=word, pos=POS.NOUN, translation="дом", status=Status.REVIEW)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


async def test_finalize_accepted_marks_approved_and_persists_enrichment(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)
    card = _card("hus")
    db.insert_card(card)

    async def _fake_enrich(
        cards: list[Card],
        db_: Database,
        cfg_: Config,
        auto_enrich: bool = True,
        auto_media: bool = True,
        auto_pick_images: bool = True,
    ) -> tuple[dict, set[str]]:
        for c in cards:
            c.example = "Jeg har et hus."
        return {}, set()

    monkeypatch.setattr(review_module.actions, "enrich_and_generate_media", _fake_enrich)

    await review_module._finalize_accepted([card], db, cfg)

    assert card.status == Status.APPROVED
    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.APPROVED
    assert saved.example == "Jeg har et hus."


async def test_finalize_accepted_returns_incomplete_cards_to_review(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)
    card = _card("hus")
    db.insert_card(card)

    async def _fake_enrich(
        cards: list[Card],
        db_: Database,
        cfg_: Config,
        auto_enrich: bool = True,
        auto_media: bool = True,
        auto_pick_images: bool = True,
    ) -> tuple[dict, set[str]]:
        return {}, {card.id}

    monkeypatch.setattr(review_module.actions, "enrich_and_generate_media", _fake_enrich)

    await review_module._finalize_accepted([card], db, cfg)

    assert card.status == Status.REVIEW
    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.REVIEW


class _FakeQuestion:
    def __init__(self, answer: str | None) -> None:
        self._answer = answer

    def ask(self) -> str | None:
        return self._answer


def test_review_pending_accept_then_quit_still_finalizes_accepted_card(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """accept на первой карточке, потом quit на второй — finally должен всё равно
    прогнать enrichment для уже принятой, а не потерять её молча."""
    cfg = _make_config(tmp_path)
    accepted_card = _card("hus")
    untouched_card = _card("bil")
    db.insert_card(accepted_card)
    db.insert_card(untouched_card)

    answers = iter(
        [
            "accept   — одобрить (approved)",
            "quit     — выйти из ревью",
        ]
    )
    monkeypatch.setattr(
        review_module.questionary, "select", lambda *a, **kw: _FakeQuestion(next(answers))
    )

    async def _fake_enrich(
        cards: list[Card],
        db_: Database,
        cfg_: Config,
        auto_enrich: bool = True,
        auto_media: bool = True,
        auto_pick_images: bool = True,
    ) -> tuple[dict, set[str]]:
        for c in cards:
            c.example = "Jeg har et hus."
        return {}, set()

    monkeypatch.setattr(review_module.actions, "enrich_and_generate_media", _fake_enrich)

    review_module.review_pending(db, cfg)

    saved_accepted = db.get_by_id(accepted_card.id)
    assert saved_accepted is not None
    assert saved_accepted.status == Status.APPROVED
    assert saved_accepted.example == "Jeg har et hus."

    saved_untouched = db.get_by_id(untouched_card.id)
    assert saved_untouched is not None
    assert saved_untouched.status == Status.REVIEW


async def test_finalize_accepted_lets_user_pick_image_candidate(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)
    cfg.images.enabled = True
    card = _card("hus")
    db.insert_card(card)

    async def _fake_enrich(
        cards: list[Card],
        db_: Database,
        cfg_: Config,
        auto_enrich: bool = True,
        auto_media: bool = True,
        auto_pick_images: bool = True,
    ) -> tuple[dict, set[str]]:
        return {}, set()

    monkeypatch.setattr(review_module.actions, "enrich_and_generate_media", _fake_enrich)

    candidates = [
        {"url": "https://img/1.jpg", "author": "Anna", "html": "https://x/1"},
        {"url": "https://img/2.jpg", "author": "Bob", "html": "https://x/2"},
    ]

    async def _fake_find(card_: Card, cfg_: Config) -> list[dict[str, str]]:
        return candidates

    saved_results: list[dict[str, str]] = []

    async def _fake_save(card_: Card, result: dict[str, str], cfg_: Config) -> Card:
        saved_results.append(result)
        card_.image = "chosen.jpg"
        return card_

    monkeypatch.setattr(review_module, "find_candidates", _fake_find)
    monkeypatch.setattr(review_module, "save_image", _fake_save)
    # Второй кандидат (индекс 1), а не первый — иначе неотличимо от старого auto-pick.
    monkeypatch.setattr(review_module.questionary, "select", lambda *a, **kw: _FakeQuestion("2"))

    await review_module._finalize_accepted([card], db, cfg)

    assert saved_results == [candidates[1]]
    saved_card = db.get_by_id(card.id)
    assert saved_card is not None
    assert saved_card.image == "chosen.jpg"


async def test_finalize_accepted_skip_choice_leaves_card_without_image(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)
    cfg.images.enabled = True
    card = _card("hus")
    db.insert_card(card)

    async def _fake_enrich(
        cards: list[Card],
        db_: Database,
        cfg_: Config,
        auto_enrich: bool = True,
        auto_media: bool = True,
        auto_pick_images: bool = True,
    ) -> tuple[dict, set[str]]:
        return {}, set()

    monkeypatch.setattr(review_module.actions, "enrich_and_generate_media", _fake_enrich)

    async def _fake_find(card_: Card, cfg_: Config) -> list[dict[str, str]]:
        return [{"url": "https://img/1.jpg", "author": "Anna", "html": "https://x/1"}]

    save_calls: list[Card] = []

    async def _fake_save(card_: Card, result: dict[str, str], cfg_: Config) -> Card:
        save_calls.append(card_)
        return card_

    monkeypatch.setattr(review_module, "find_candidates", _fake_find)
    monkeypatch.setattr(review_module, "save_image", _fake_save)
    monkeypatch.setattr(
        review_module.questionary,
        "select",
        lambda *a, **kw: _FakeQuestion("skip — без картинки"),
    )

    await review_module._finalize_accepted([card], db, cfg)

    assert save_calls == []
    saved_card = db.get_by_id(card.id)
    assert saved_card is not None
    assert saved_card.image is None


# ───────────────────── review html --status ─────────────────────


def test_review_html_status_table_covers_every_status() -> None:
    """Таблица --status обязана знать все статусы: пропущенный означает, что до
    таких карточек страницей не добраться вовсе, а узнается это только в бою."""
    from ankicards.cli import _REVIEW_HTML_STATUSES

    covered = {s for statuses in _REVIEW_HTML_STATUSES.values() for s in statuses}
    assert covered == set(Status)
    assert _REVIEW_HTML_STATUSES["open"] == (Status.REVIEW, Status.PENDING)
    assert _REVIEW_HTML_STATUSES["all"] == tuple(Status)


def test_review_html_default_hides_already_accepted_cards(tmp_path: Path) -> None:
    """По умолчанию страница про то, что ждёт решения, — принятое туда не лезет.

    Перечитать approved всё же нужно (в одной такой нашлась опечатка, пережившая
    ручную проверку), но по явному --status approved, а не заодно с остальным.
    """
    from ankicards.cli import _REVIEW_HTML_STATUSES

    cfg = _make_config(tmp_path)
    db = Database(cfg.paths.db)
    waiting = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка")
    waiting.status = Status.REVIEW
    db.insert_card(waiting)
    done = Card(language="nb", word="sko", pos=POS.NOUN, translation="ботинок")
    done.status = Status.APPROVED
    db.insert_card(done)

    def _collect(status: str) -> list[str]:
        return [
            card.word
            for st in _REVIEW_HTML_STATUSES[status]
            for card in db.get_by_status(st, cfg.language)
        ]

    assert _collect("open") == ["lue"]
    assert _collect("approved") == ["sko"]
    assert sorted(_collect("all")) == ["lue", "sko"]
