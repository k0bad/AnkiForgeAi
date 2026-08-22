"""Тесты импорта из Bildetema: разбор базы, части речи, медиа, статус на выходе."""

from __future__ import annotations

import gzip
import json
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
from ankicards.enrich import grammar
from ankicards.enrich import pos as pos_stage
from ankicards.ingest import bildetema
from ankicards.models import POS, Card, Status


def _word(word_id: str, label: str, article: str | None = None, order: int = 1) -> dict:
    entry: dict = {
        "id": word_id,
        "labels": [{"label": label} | ({"article": article} if article else {})],
        "images": [{"src": f"https://cdn/images/large/{word_id}a.jpeg"}],
        "audioFiles": [{"url": f"https://cdn/audio/nob/{word_id}.mp3", "extension": "mp3"}],
        "order": order,
    }
    return entry


@pytest.fixture
def database() -> dict:
    """Мини-база в формате Bildetema: тема со своими словами + две подтемы.

    Порядок слов в списке rus намеренно не совпадает с nob — сопоставление идёт
    по id, и тест обязан ловить попытку связать их по позиции.
    """
    return {
        "languages": [
            {"code": "nob", "label": "Bokmål", "rtl": False},
            {"code": "rus", "label": "Russisk", "rtl": False},
            {"code": "eng", "label": "Engelsk", "rtl": False},
        ],
        "topics": [
            {
                "id": "T034",
                "label": "Klær",
                "order": 1,
                "words": {
                    "nob": [_word("V0376", "lue", "ei/en", order=1)],
                    "rus": [_word("V0376", "шапка")],
                    "eng": [_word("V0376", "winter hat")],
                },
                "subTopics": [
                    {
                        "id": "T035",
                        "label": "Sko",
                        "order": 1,
                        "words": {
                            "nob": [
                                _word("V0451", "støvler", order=2),
                                _word("V0450", "sko", "en", order=1),
                            ],
                            "rus": [_word("V0450", "ботинок"), _word("V0451", "сапоги")],
                            "eng": [_word("V0450", "shoe"), _word("V0451", "boots")],
                        },
                        "subTopics": [],
                    },
                    {
                        "id": "T036",
                        "label": "Undertøy",
                        "order": 2,
                        "words": {
                            "nob": [_word("V0436", "truse", "ei/en", order=1)],
                            "rus": [],  # перевода нет — слово должно отвалиться
                            "eng": [],
                        },
                        "subTopics": [],
                    },
                ],
            },
            {"id": "T013", "label": "Farger", "order": 2, "words": {}, "subTopics": []},
        ],
    }


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


# ───────────────────────── дерево тем ─────────────────────────


def test_list_topics_counts_include_subtopic_words(database: dict) -> None:
    topics = {t.id: t for t in bildetema.list_topics(database)}

    assert len(topics["T034"].word_ids) == 4  # своё слово + 2 из Sko + 1 из Undertøy
    assert len(topics["T035"].word_ids) == 2
    assert topics["T035"].full_label == "Klær / Sko"
    assert topics["T013"].word_ids == set()


def test_resolve_topic_by_id_label_and_substring(database: dict) -> None:
    assert bildetema.resolve_topic(database, "T035").label == "Sko"
    assert bildetema.resolve_topic(database, "klær").id == "T034"
    assert bildetema.resolve_topic(database, "under").id == "T036"


def test_resolve_topic_reports_unknown_topic(database: dict) -> None:
    with pytest.raises(bildetema.BildetemaError, match="не найдена"):
        bildetema.resolve_topic(database, "Sport")


def test_resolve_language_rejects_language_absent_from_bildetema(database: dict) -> None:
    assert bildetema.resolve_language("nb", bildetema.LANG_BY_PROFILE, database, "Язык") == "nob"
    # Немецкого в Bildetema нет — молча импортировать не тот язык нельзя.
    with pytest.raises(bildetema.BildetemaError, match="Доступны"):
        bildetema.resolve_language("de", bildetema.LANG_BY_PROFILE, database, "Язык")


# ───────────────────────── сборка карточек ─────────────────────────


def _entries(database: dict, topic_query: str = "T034") -> list[bildetema.Entry]:
    topic = bildetema.resolve_topic(database, topic_query)
    return bildetema.build_entries(
        database,
        topic,
        language="nb",
        target_lang="nob",
        translation_lang="rus",
    )


def test_build_entries_matches_translation_by_id_not_position(database: dict) -> None:
    """В подтеме Sko порядок nob (støvler, sko) обратен порядку rus (ботинок, сапоги)."""
    by_word = {e.card.word: e.card.translation for e in _entries(database)}

    assert by_word["sko"] == "ботинок"
    assert by_word["støvler"] == "сапоги"


def test_build_entries_derives_pos_from_article(database: dict) -> None:
    by_word = {e.card.word: e for e in _entries(database)}

    assert by_word["lue"].card.pos is POS.NOUN
    assert by_word["lue"].article == "ei/en"
    # Артикля нет — часть речи тут ещё неизвестна, её доопределит classify_missing_pos.
    assert by_word["støvler"].card.pos is POS.OTHER
    assert by_word["støvler"].article is None


def test_build_entries_skips_word_without_translation(database: dict) -> None:
    words = {e.card.word for e in _entries(database)}

    assert "truse" not in words  # rus-список подтемы Undertøy пуст
    assert words == {"lue", "sko", "støvler"}


def test_build_entries_keeps_subtopic_grouping(database: dict) -> None:
    """`order` не сквозной по теме — у каждой подтемы своя нумерация с 1, поэтому
    слова идут группами (сначала сама тема, потом подтемы), а не вперемешку."""
    assert [e.card.word for e in _entries(database)] == ["lue", "sko", "støvler"]


def test_build_entries_tags_word_with_its_deepest_subtopic(database: dict) -> None:
    """Слово лежит и в подтеме, и в родительской теме. В тег идёт подтема:
    «topic::klær::sko» в Anki полезнее плоского «topic::klær»."""
    by_word = {e.card.word: e.card.topic for e in _entries(database)}

    assert by_word["sko"] == "klær::sko"
    assert by_word["lue"] == "klær"  # это слово живёт только в самой теме


def test_build_entries_fills_card_metadata(database: dict) -> None:
    card = next(e.card for e in _entries(database) if e.card.word == "lue")

    assert card.language == "nb"
    assert card.source == "bildetema:V0376"
    assert card.topic == "klær"
    assert card.status is Status.PENDING
    assert card.image_query == "winter hat"
    assert "topic::klær" in card.auto_tags()
    assert "source::bildetema" in card.auto_tags()


def test_topic_slug_strips_spaces_that_would_break_anki_tags(database: dict) -> None:
    """Теги в Anki разделяются пробелами, поэтому «Klær / Sko» не может попасть
    в тег как есть."""
    topic = bildetema.resolve_topic(database, "T035")

    assert topic.slug == "klær::sko"
    assert " " not in topic.slug


def test_build_entries_collects_media_urls(database: dict) -> None:
    entry = next(e for e in _entries(database) if e.card.word == "lue")

    assert entry.media.image_urls == ("https://cdn/images/large/V0376a.jpeg",)
    assert entry.media.audio_url == "https://cdn/audio/nob/V0376.mp3"


# ───────────────────────── часть речи ─────────────────────────


async def test_classify_missing_pos_only_asks_about_words_without_article(
    database: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = _entries(database)
    asked: list[list[dict]] = []

    async def _fake_call_json(prompt: str, **_: object) -> list[dict]:
        asked.append(json.loads(prompt))
        return [{"id": 0, "pos": "noun"}]

    monkeypatch.setattr(pos_stage, "load_prompt", lambda _name, **kw: kw["words_json"])
    monkeypatch.setattr(pos_stage, "call_json", _fake_call_json)

    await bildetema.classify_missing_pos(entries)

    assert [item["word"] for item in asked[0]] == ["støvler"]
    assert next(e.card.pos for e in entries if e.card.word == "støvler") is POS.NOUN
    assert next(e.card.pos for e in entries if e.card.word == "lue") is POS.NOUN


async def test_classify_missing_pos_leaves_card_alone_when_llm_skips_it(
    database: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = _entries(database)

    async def _fake_call_json(prompt: str, **_: object) -> list[dict]:
        return [{"id": 0, "pos": "нечто"}]

    monkeypatch.setattr(pos_stage, "load_prompt", lambda _name, **kw: kw["words_json"])
    monkeypatch.setattr(pos_stage, "call_json", _fake_call_json)

    await bildetema.classify_missing_pos(entries)

    assert next(e.card.pos for e in entries if e.card.word == "støvler") is POS.OTHER


# ───────────────────────── база и кэш ─────────────────────────


async def test_load_database_unpacks_gzip_and_caches_it(
    tmp_path: Path, database: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    calls = 0

    async def _fake_download(url: str) -> bytes:
        nonlocal calls
        calls += 1
        return gzip.compress(json.dumps(database).encode("utf-8"))

    monkeypatch.setattr(bildetema, "_download", _fake_download)

    first = await bildetema.load_database(cfg)
    second = await bildetema.load_database(cfg)

    assert calls == 1, "второе обращение обязано читать кэш, а не ходить в сеть"
    assert first["topics"][0]["id"] == second["topics"][0]["id"] == "T034"
    assert bildetema.cache_path(cfg).exists()


async def test_load_database_rejects_payload_that_is_not_gzip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_download(url: str) -> bytes:
        return b"<html>404</html>"

    monkeypatch.setattr(bildetema, "_download", _fake_download)

    with pytest.raises(bildetema.BildetemaError, match="не gzip"):
        await bildetema.load_database(_config(tmp_path))


# ───────────────────────── медиа ─────────────────────────


async def test_attach_media_matches_cards_by_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dedupe выкидывает часть карточек, так что позиция в списке не годится
    как ключ — медиа ищется по card.source."""
    cfg = _config(tmp_path)
    downloaded: dict[str, str] = {}

    async def _fake_image(url: str, out_path: Path, _cfg: Config) -> None:
        downloaded[out_path.name] = url

    async def _fake_audio(url: str, out_path: Path) -> None:
        downloaded[out_path.name] = url

    monkeypatch.setattr(bildetema, "download_image", _fake_image)
    monkeypatch.setattr(bildetema, "_download_audio", _fake_audio)

    card = Card(
        id=7,
        language="nb",
        word="lue",
        pos=POS.NOUN,
        translation="шапка",
        source="bildetema:V0376",
    )
    media = {
        "bildetema:V0376": bildetema.Media(("https://cdn/a.jpeg",), "https://cdn/a.mp3"),
        "bildetema:V9999": bildetema.Media(("https://cdn/other.jpeg",), None),
    }

    stats = await bildetema.attach_media([card], media, cfg)

    assert card.image == "7.jpg"
    assert card.audio == "7_nb.mp3"
    assert downloaded == {"7.jpg": "https://cdn/a.jpeg", "7_nb.mp3": "https://cdn/a.mp3"}
    assert stats == {"images": 1, "audio": 1, "failed": 0}


async def test_attach_media_survives_failed_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не скачалось — карточка просто остаётся без картинки, обычная media-стадия
    подхватит её как любую другую."""
    cfg = _config(tmp_path)

    async def _boom(url: str, out_path: Path, _cfg: Config) -> None:
        raise RuntimeError("502")

    async def _fake_audio(url: str, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mp3")

    monkeypatch.setattr(bildetema, "download_image", _boom)
    monkeypatch.setattr(bildetema, "_download_audio", _fake_audio)

    card = Card(
        id=3,
        language="nb",
        word="sko",
        pos=POS.NOUN,
        translation="ботинок",
        source="bildetema:V0450",
    )
    stats = await bildetema.attach_media(
        [card],
        {"bildetema:V0450": bildetema.Media(("https://cdn/x.jpeg",), "https://cdn/x.mp3")},
        cfg,
    )

    assert card.image is None
    assert card.audio == "3_nb.mp3"
    assert stats["failed"] == 1


# ───────────────────────── стыковка с pipeline ─────────────────────────


async def test_pipeline_runs_hook_before_enrichment_and_keeps_cards_in_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """on_inserted обязан отработать до media-стадии: она смотрит на файлы на диске,
    чтобы не переозвучить уже скачанное аудио."""
    cfg = _config(tmp_path)
    db = Database(tmp_path / "cards.db")
    order: list[str] = []

    async def _hook(cards: list[Card]) -> None:
        order.append("hook")
        for card in cards:
            path = cfg.paths.audio_dir / f"{card.id}_nb.mp3"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"bildetema")
            card.audio = path.name

    async def _tts(card: Card, _cfg: Config) -> Card:
        order.append("tts")
        (cfg.paths.audio_dir / f"{card.id}_nb.mp3").write_bytes(b"edge-tts")
        return card

    monkeypatch.setattr(pipeline, "generate_audio", _tts)

    card = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка")
    stats = await pipeline.run_ingest_pipeline(
        [card], db=db, cfg=cfg, auto_enrich=False, on_inserted=_hook, force_review=True
    )

    assert order == ["hook"], "edge-tts не должен затирать уже скачанное аудио"
    assert stats["media_reused"] == 1
    assert (cfg.paths.audio_dir / "1_nb.mp3").read_bytes() == b"bildetema"

    saved = db.get_by_status(Status.REVIEW)
    assert [c.word for c in saved] == ["lue"]
    assert db.get_by_status(Status.APPROVED) == []


async def test_pipeline_regenerates_audio_when_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Имя в БД есть, а файла нет (не доехал, удалили руками) — озвучиваем заново."""
    cfg = _config(tmp_path)
    db = Database(tmp_path / "cards.db")
    generated: list[int] = []

    async def _tts(card: Card, _cfg: Config) -> Card:
        assert card.id is not None
        generated.append(card.id)
        return card

    monkeypatch.setattr(pipeline, "generate_audio", _tts)

    card = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка", audio="1_nb.mp3")
    await pipeline.run_ingest_pipeline([card], db=db, cfg=cfg, auto_enrich=False)

    assert generated == [1]


async def test_classify_missing_pos_survives_llm_outage(
    database: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Провайдер прилёг — слова, переводы, фото и аудио у нас уже есть, и терять
    их из-за неуточнённой части речи нельзя."""
    entries = _entries(database)

    async def _boom(prompt: str, **_: object) -> list[dict]:
        raise RuntimeError("claude CLI завершился с кодом 1")

    monkeypatch.setattr(pos_stage, "load_prompt", lambda _name, **kw: kw["words_json"])
    monkeypatch.setattr(pos_stage, "call_json", _boom)

    await bildetema.classify_missing_pos(entries)

    assert next(e.card.pos for e in entries if e.card.word == "støvler") is POS.OTHER
    assert next(e.card.pos for e in entries if e.card.word == "lue") is POS.NOUN


def test_merge_stats_sums_flat_counters_and_nested_image_breakdown() -> None:
    """Импорт идёт пачками, и сводка в конце должна складывать их, а не показывать
    последнюю: images приходит вложенным словарём (см. enrich_and_generate_media)."""
    from ankicards.cli import _merge_stats

    total: dict = {}
    _merge_stats(total, {"new": 3, "merged": 1, "images": {"found": 3, "skipped_not_noun": 0}})
    _merge_stats(total, {"new": 2, "merged": 0, "images": {"found": 1, "skipped_not_noun": 1}})

    assert total == {
        "new": 5,
        "merged": 1,
        "images": {"found": 4, "skipped_not_noun": 1},
    }


# ───────────────────────── verified-тег ─────────────────────────


def test_mark_verified_tag_carries_the_date_and_reaches_anki() -> None:
    from datetime import date

    card = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка", topic="klær")

    tag = card.mark_verified(day=date(2026, 8, 21))

    assert tag == "verified::2026-08-21"
    assert card.is_verified()
    # auto_tags() — то, что уходит в Anki при push; тег обязан быть там.
    assert "verified::2026-08-21" in card.auto_tags()


def test_mark_verified_is_idempotent_and_keeps_the_first_date() -> None:
    """Повторный accept не плодит теги и не переписывает дату первой проверки."""
    from datetime import date

    card = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка")
    card.mark_verified(day=date(2026, 8, 21))
    card.mark_verified(day=date(2026, 9, 1))

    assert card.tags == ["verified::2026-08-21"]


def test_card_is_not_verified_until_a_human_says_so() -> None:
    """Импортированная карточка сама по себе непроверенная — иначе тег ничего не значит."""
    card = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка", topic="klær")

    assert not card.is_verified()
    assert not any(t.startswith("verified") for t in card.auto_tags())


async def test_accept_with_verified_persists_the_tag_through_update_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Регрессия: update_card не писал колонку tags, и тег терялся сразу после accept."""
    from ankicards.review import actions

    cfg = _config(tmp_path)
    db = Database(tmp_path / "cards.db")
    card = Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка")
    db.insert_card(card)

    async def _noop(cards, db_, cfg_, **kw):  # type: ignore[no-untyped-def]
        return {}, set()

    monkeypatch.setattr(actions, "enrich_and_generate_media", _noop)

    await actions.accept_cards([1], db, cfg, verified=True)

    reloaded = db.get_by_id(1)
    assert reloaded is not None
    assert reloaded.status is Status.APPROVED
    assert reloaded.is_verified(), "тег не пережил перезагрузку из БД"


async def test_accept_without_verified_leaves_no_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Скрипты и агенты зовут ту же функцию — отметка о личной проверке от них была бы ложью."""
    from ankicards.review import actions

    cfg = _config(tmp_path)
    db = Database(tmp_path / "cards.db")
    db.insert_card(Card(language="nb", word="lue", pos=POS.NOUN, translation="шапка"))

    async def _noop(cards, db_, cfg_, **kw):  # type: ignore[no-untyped-def]
        return {}, set()

    monkeypatch.setattr(actions, "enrich_and_generate_media", _noop)

    await actions.accept_cards([1], db, cfg)

    reloaded = db.get_by_id(1)
    assert reloaded is not None
    assert reloaded.tags == []


# ───────────────────────── правка части речи ─────────────────────────


def _seeded(tmp_path: Path, **overrides: object) -> tuple[Database, Card]:
    db = Database(tmp_path / "cards.db")
    fields: dict = {
        "language": "nb",
        "word": "glad",
        "pos": POS.OTHER,
        "translation": "рад",
    }
    fields.update(overrides)
    card = Card(**fields)
    db.insert_card(card)
    return db, card


def test_edit_can_fix_a_part_of_speech_the_classifier_got_wrong(tmp_path: Path) -> None:
    """Без этого неверный POS чинился только удалением карточки и переимпортом."""
    from ankicards.review import actions

    db, _ = _seeded(tmp_path)

    updated = actions.edit_card(1, {"pos": "adj"}, db)

    assert updated.pos is POS.ADJECTIVE
    assert db.get_by_id(1).pos is POS.ADJECTIVE  # type: ignore[union-attr]


def test_edit_rejects_a_part_of_speech_that_is_not_in_the_enum(tmp_path: Path) -> None:
    """Опечатка не помешала бы UPDATE, но карточка перестала бы читаться из БД."""
    from ankicards.review import actions

    db, _ = _seeded(tmp_path)

    with pytest.raises(ValueError, match="Неизвестная часть речи"):
        actions.edit_card(1, {"pos": "adjective"}, db)

    assert db.get_by_id(1).pos is POS.OTHER  # type: ignore[union-attr]


def test_edit_normalises_part_of_speech_case_and_spacing(tmp_path: Path) -> None:
    from ankicards.review import actions

    db, _ = _seeded(tmp_path)

    assert actions.edit_card(1, {"pos": " ADJ "}, db).pos is POS.ADJECTIVE


def test_changing_part_of_speech_drops_forms_generated_for_the_old_one(tmp_path: Path) -> None:
    """Склонение существительного у прилагательного — мусор, а не данные;
    пустые формы честнее, следующий accept сгенерирует правильные."""
    from ankicards.review import actions

    db, _ = _seeded(
        tmp_path, word="varm", pos=POS.NOUN, forms={"gender": "m", "definite_singular": "varmen"}
    )

    updated = actions.edit_card(1, {"pos": "adj"}, db)

    assert updated.forms is None


def test_editing_text_leaves_forms_alone(tmp_path: Path) -> None:
    """Обнуление форм привязано к смене POS, а не к любой правке."""
    from ankicards.review import actions

    db, _ = _seeded(tmp_path, word="hatt", pos=POS.NOUN, forms={"gender": "m"})

    updated = actions.edit_card(1, {"translation": "шляпа"}, db)

    assert updated.forms == {"gender": "m"}


def test_repeating_the_same_part_of_speech_keeps_the_forms(tmp_path: Path) -> None:
    from ankicards.review import actions

    db, _ = _seeded(tmp_path, word="hatt", pos=POS.NOUN, forms={"gender": "m"})

    updated = actions.edit_card(1, {"pos": "noun"}, db)

    assert updated.forms == {"gender": "m"}


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


async def test_hook_also_runs_for_cards_dedupe_sent_to_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Карточка, которую dedupe увёл на человеческую адъюдикацию, тоже получает медиа.

    Точка скачивания фото и аудио с CDN ровно одна — этот колбэк, и повторный ingest
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
            path.write_bytes(b"bildetema")
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


async def _passthrough_judge(_card: Card, decision: object, _cfg: Config) -> object:
    """LLM-адъюдикация в тесте не нужна: проверяется ветка, в которую dedupe уже попал."""
    return decision


@pytest.mark.parametrize(
    ("article", "gender"),
    [
        ("en", "m"),
        ("ei", "f"),
        ("ei/en", "f"),  # у Bildetema это отдельная пометка, а не «или то, или это»
        ("et", "n"),
        ("ei/en/et", None),  # род и правда неоднозначен — пусть решает модель
        ("en/et", None),
        (None, None),
    ],
)
def test_gender_comes_from_the_article(article: str | None, gender: str | None) -> None:
    assert bildetema._gender_of(article) == gender


def test_entry_carries_gender_but_not_a_finished_paradigm(database: dict) -> None:
    """Из артикля берётся только род: склонение Bildetema не хранит, и карточка
    обязана остаться «неполной», иначе enrich_grammar_batch пройдёт мимо неё."""
    topic = bildetema.resolve_topic(database, "Klær", "nob")
    entries = bildetema.build_entries(
        database, topic, language="nb", target_lang="nob", translation_lang="rus"
    )
    by_word = {e.card.word: e.card for e in entries}

    lue = by_word["lue"]  # ei/en → женский
    assert lue.forms == {"gender": "f"}
    assert not grammar.forms_complete(lue), "склонение всё ещё надо добрать у модели"

    assert by_word["sko"].forms == {"gender": "m"}  # en
    assert by_word["støvler"].forms is None  # без артикля — и род неизвестен


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

    assert card.pos is POS.NOUN
    assert "pos::noun" in card.auto_tags()
