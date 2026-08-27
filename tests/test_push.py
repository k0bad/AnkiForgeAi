"""Тесты для pipeline.push_approved: отсутствующий в Anki Note Type должен давать
понятную ошибку вместо тихого "pushed: 0" (Note Type mismatch после обновления,
когда anki.note_type в language.yaml сменился, а `ankiforgeai init` не перезапускался)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ankicards.anki.connect import AnkiConnect
from ankicards.anki.notetype import _get_note_type_name
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
    resolve_anki_profile,
)
from ankicards.db import Database
from ankicards.models import POS, Card, Status
from ankicards.pipeline import NoteTypeMissingError, push_approved


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


class _FakeAnki:
    """Двойник AnkiConnect: без реального HTTP, только model_names/ensure_deck/
    add_note/store_media."""

    def __init__(self, models: list[str], rename_media: bool = False) -> None:
        self._models = models
        self.added: list[dict] = []
        self.stored: list[str] = []
        # rename_media — имя в коллекции занято чужим файлом, реальный
        # storeMediaFile в этом случае вернёт другое имя.
        self._rename_media = rename_media

    async def ensure_deck(self) -> None:
        pass

    async def model_names(self) -> list[str]:
        return self._models

    async def add_note(self, fields: dict, tags: list[str]) -> int:
        self.added.append(fields)
        return len(self.added)

    async def store_media(self, filename: str, file_path: Path) -> str:
        self.stored.append(filename)
        if not self._rename_media:
            return filename
        stem, _, ext = filename.rpartition(".")
        return f"{stem}-deadbeef.{ext}"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


async def test_push_approved_raises_when_note_type_missing(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path)
    card = Card(language="nb", word="hus", pos=POS.NOUN, translation="дом", status=Status.APPROVED)
    db.insert_card(card)

    anki = _FakeAnki(models=["SomeOtherModel"])

    with pytest.raises(NoteTypeMissingError, match=_get_note_type_name()):
        await push_approved(db, anki, cfg)  # type: ignore[arg-type]

    # push не должен был успеть отправить ни одной карточки
    assert anki.added == []


async def test_push_approved_succeeds_when_note_type_exists(tmp_path: Path, db: Database) -> None:
    cfg = _make_config(tmp_path)
    card = Card(language="nb", word="hus", pos=POS.NOUN, translation="дом", status=Status.APPROVED)
    db.insert_card(card)

    anki = _FakeAnki(models=[_get_note_type_name()])

    count = await push_approved(db, anki, cfg)  # type: ignore[arg-type]

    assert count == 1
    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.status == Status.PUSHED


async def test_push_approved_only_pushes_active_language(tmp_path: Path, db: Database) -> None:
    """Issue #63: push без языковой фильтрации отправлял бы approved-карточки
    вообще всех языков при каждом запуске — push_approved должен сузиться до
    cfg.language и не трогать approved-карточки других языков."""
    cfg_nb = _make_config(tmp_path)
    nb_card = Card(
        language="nb", word="hus", pos=POS.NOUN, translation="дом", status=Status.APPROVED
    )
    de_card = Card(
        language="de", word="Haus", pos=POS.NOUN, translation="дом (де)", status=Status.APPROVED
    )
    db.insert_card(nb_card)
    db.insert_card(de_card)

    anki = _FakeAnki(models=[_get_note_type_name()])
    count = await push_approved(db, anki, cfg_nb)  # type: ignore[arg-type]

    assert count == 1
    assert len(anki.added) == 1
    pushed_nb = db.get_by_id(nb_card.id)
    untouched_de = db.get_by_id(de_card.id)
    assert pushed_nb is not None
    assert pushed_nb.status == Status.PUSHED
    assert untouched_de is not None
    assert untouched_de.status == Status.APPROVED  # push для "nb" не тронул "de"


def test_ankiconnect_resolves_deck_from_active_language_not_static_config_copy(
    tmp_path: Path,
) -> None:
    """Issue #63: cfg.anki.deck_name — статическая копия, которую setup_wizard
    пишет в config.yaml один раз для языка, активного на момент setup (здесь —
    "Norsk", как если бы setup был для nb). AnkiConnect должен резолвить deck из
    ЖИВОГО профиля languages/{cfg.language}/language.yaml, а не из этой копии,
    когда --language переключает язык на de (языковой профиль de → "Deutsch")."""
    cfg = _make_config(tmp_path)
    cfg.anki.deck_name = "Norsk"
    cfg.anki.note_type = "LanguageCard"
    cfg.language = "de"

    anki = AnkiConnect(cfg)

    assert anki.deck == "Deutsch"


def test_deck_can_be_overridden_for_one_run(tmp_path: Path) -> None:
    """`push --deck` — разовая подмена: постоянная колода в общем профиле языка,
    и переписывать её ради одного набора карточек нельзя."""
    cfg = _make_config(tmp_path)

    assert AnkiConnect(cfg).deck == resolve_anki_profile(cfg).deck_name
    assert AnkiConnect(cfg, deck="Norsk::Ekstra").deck == "Norsk::Ekstra"
    # пустая строка — не подмена, а «не задано»: остаётся колода профиля
    assert AnkiConnect(cfg, deck="").deck == resolve_anki_profile(cfg).deck_name


async def test_push_uses_the_name_anki_actually_stored_media_under(
    tmp_path: Path, db: Database
) -> None:
    """collection.media — одна папка на всю коллекцию, а `{card.id}_nb.mp3`
    уникально только внутри своей базы SQLite. Когда имя уже занято чужим файлом,
    storeMediaFile кладёт наш рядом и возвращает другое имя; push обязан сослаться
    в заметке именно на него. Раньше результат store_media отбрасывался, заметка
    ссылалась на запрошенное имя — то есть на чужой файл, и карточка проигрывала
    чужое слово."""
    cfg = _make_config(tmp_path)
    cfg.paths.audio_dir.mkdir(parents=True, exist_ok=True)
    (cfg.paths.audio_dir / "1_nb.mp3").write_bytes(b"lyd")

    card = Card(
        language="nb",
        word="hus",
        pos=POS.NOUN,
        translation="дом",
        status=Status.APPROVED,
        audio="1_nb.mp3",
    )
    db.insert_card(card)

    anki = _FakeAnki(models=[_get_note_type_name()], rename_media=True)
    await push_approved(db, anki, cfg)  # type: ignore[arg-type]

    assert anki.stored == ["1_nb.mp3"]
    assert "[sound:1_nb-deadbeef.mp3]" in anki.added[0]["Audio"]

    # локальное имя остаётся детерминированным — переименование живёт только в Anki
    saved = db.get_by_id(card.id)
    assert saved is not None
    assert saved.audio == "1_nb.mp3"


async def test_push_does_not_ask_anki_to_overwrite_existing_media(tmp_path: Path) -> None:
    """Гарантия на уровне запроса: deleteExisting должен уходить False.
    Значение по умолчанию у storeMediaFile — true, и именно оно молча переписало
    аудио соседней колоды при отправке второй базы."""
    cfg = _make_config(tmp_path)
    media = tmp_path / "1_nb.mp3"
    media.write_bytes(b"lyd")

    sent: dict = {}

    class _Recording(AnkiConnect):
        async def _post(self, payload: dict) -> dict:  # type: ignore[override]
            sent.update(payload)
            return {"error": None, "result": payload["params"]["filename"]}

    await _Recording(cfg).store_media("1_nb.mp3", media)

    assert sent["action"] == "storeMediaFile"
    assert sent["params"]["deleteExisting"] is False
