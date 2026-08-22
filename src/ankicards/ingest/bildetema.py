"""Импорт словаря Bildetema (NAFO / OsloMet) — https://nybildetema.oslomet.no

Сайт — SPA, но парсить его HTML не нужно: всё содержимое приходит в браузер
одним файлом (BILDETEMA_DB_URL) — ~1100 слов по 21 теме, подписи на 28 языках
(включая nob и rus), фотографии и аудио, начитанное живым диктором.

Формат файла: gzip'нутый JSON. Расширение .tar.gz историческое — внутри не tar,
а сразу database.json:

    {"topics": [...], "languages": [...], "translations": {...}}

Тема (и подтема — та же структура рекурсивно) держит слова в поле `words`:
dict {код языка: [ {id, labels, images, audioFiles} ]}. Списки разных языков
выровнены по `id` слова (V0001, ...), поэтому перевод ищется сопоставлением по
этому id, а не по позиции в списке — порядок совпадает не всегда.

Часть речи Bildetema не хранит. У существительных её выдаёт поле `article`
("en" / "ei/en" / "et"), у остальных (~20% слов) она определяется батч-вызовом
LLM по prompts/pos_classify.md.

Медиа скачивается не здесь, а attach_media() уже после dedupe/insert: имена
файлов детерминированы по card.id (см. CLAUDE.md принцип 6), а сам id
выделяется только при вставке карточки в БД.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .._net import http_retry
from ..config import Config
from ..enrich.pos import classify_pos_batch
from ..log import get_logger
from ..media.images import download_image
from ..models import POS, Card, Level, Status

logger = get_logger(__name__)

BILDETEMA_DB_URL = "https://cdn-prod-bildetema.azureedge.net/data/database.json.tar.gz"
DOWNLOAD_TIMEOUT = 120.0
SOURCE_PREFIX = "bildetema"

# Код языкового профиля проекта (languages/{code}/) → код языка в Bildetema.
# Немецкого в Bildetema нет вовсе, поэтому `de` тут отсутствует намеренно.
LANG_BY_PROFILE = {"nb": "nob", "en": "eng", "es": "spa"}
# config.ui_language → язык перевода на обратной стороне карточки.
LANG_BY_UI = {"ru": "rus", "en": "eng"}


class BildetemaError(Exception):
    """Тема не найдена, язык не поддерживается, база не скачалась."""


@dataclass(frozen=True)
class Media:
    """Ссылки на медиа одного слова — то, что скачается после выделения card.id."""

    image_urls: tuple[str, ...] = ()
    audio_url: str | None = None


@dataclass
class TopicInfo:
    """Узел дерева тем: сама тема или подтема (структура у них одинаковая)."""

    id: str
    label: str
    path: tuple[str, ...]  # метки предков + своя, от корня
    word_ids: set[str] = field(default_factory=set)
    subtopics: list[str] = field(default_factory=list)

    @property
    def full_label(self) -> str:
        return " / ".join(self.path)

    @property
    def slug(self) -> str:
        return _slug_of(self.path)


# ───────────────────────── загрузка базы ─────────────────────────


@http_retry
async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


def cache_path(cfg: Config) -> Path:
    """Куда кладётся распакованная база — рядом с SQLite, в data/bildetema/."""
    return cfg.paths.db.parent / SOURCE_PREFIX / "database.json"


async def load_database(cfg: Config, refresh: bool = False) -> dict:
    """Отдать базу Bildetema, скачав её при первом обращении (или при refresh=True).

    Файл ~900 КБ в gzip и ~29 МБ распакованным, содержимое меняется редко —
    поэтому кэш на диске без TTL, обновление только по явному запросу.
    """
    path = cache_path(cfg)
    if path.exists() and not refresh:
        logger.debug("bildetema.cache_hit", path=str(path))
        cached: dict = json.loads(path.read_text(encoding="utf-8"))
        return cached

    logger.info("bildetema.download", url=BILDETEMA_DB_URL)
    raw = await _download(BILDETEMA_DB_URL)
    try:
        payload = gzip.decompress(raw)
    except OSError as e:  # не gzip — значит формат раздачи поменялся
        raise BildetemaError(f"{BILDETEMA_DB_URL} вернул не gzip: {e}") from e

    database: dict = json.loads(payload)
    if "topics" not in database:
        raise BildetemaError("В базе Bildetema нет ключа 'topics' — формат изменился")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(database, ensure_ascii=False), encoding="utf-8")
    logger.info("bildetema.cached", path=str(path), topics=len(database["topics"]))
    return database


# ───────────────────────── дерево тем ─────────────────────────


def _iter_nodes(
    nodes: list[dict], path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], dict]]:
    for node in nodes:
        here = (*path, node["label"])
        yield here, node
        yield from _iter_nodes(node.get("subTopics") or [], here)


def _node_word_ids(node: dict, lang: str) -> set[str]:
    """id слов самого узла — без спуска в подтемы."""
    entries = (node.get("words") or {}).get(lang) or []
    return {entry["id"] for entry in entries}


def list_topics(database: dict, lang: str = "nob") -> list[TopicInfo]:
    """Плоский список всех тем и подтем.

    word_ids темы включают её подтемы — у Bildetema слово лежит и в подтеме, и в
    родительской теме, но одинаковые id схлопываются, так что счётчик честный.
    """
    topics: list[TopicInfo] = []
    for path, node in _iter_nodes(database["topics"]):
        word_ids: set[str] = set()
        for _, descendant in _iter_nodes([node]):
            word_ids |= _node_word_ids(descendant, lang)
        topics.append(
            TopicInfo(
                id=node["id"],
                label=node["label"],
                path=path,
                word_ids=word_ids,
                subtopics=[sub["label"] for sub in node.get("subTopics") or []],
            )
        )
    return topics


def resolve_topic(database: dict, query: str, lang: str = "nob") -> TopicInfo:
    """Найти тему по id (T034), точной метке или подстроке метки.

    Ищет и по темам, и по подтемам — «Frukt» находится так же, как «Mat og drikke».
    """
    topics = list_topics(database, lang)
    needle = query.strip().casefold()

    for topic in topics:
        if topic.id.casefold() == needle:
            return topic

    for candidates in (
        [t for t in topics if t.label.casefold() == needle],
        [t for t in topics if needle in t.label.casefold()],
    ):
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            listing = ", ".join(f"{t.id} ({t.full_label})" for t in candidates)
            raise BildetemaError(f"Тема {query!r} неоднозначна: {listing}")

    raise BildetemaError(f"Тема {query!r} не найдена — см. `ankiforgeai ingest bildetema --list`")


def resolve_language(code: str, mapping: dict[str, str], database: dict, what: str) -> str:
    """Код профиля/ui ('nb', 'ru') или уже трёхбуквенный код Bildetema ('nob')."""
    available = {lang["code"] for lang in database.get("languages") or []}
    resolved = mapping.get(code, code)
    if resolved not in available:
        raise BildetemaError(
            f"{what} {code!r} в Bildetema нет. Доступны: {', '.join(sorted(available))}"
        )
    return resolved


# ───────────────────────── сборка карточек ─────────────────────────


def _slug_of(path: tuple[str, ...]) -> str:
    """Путь по дереву тем → иерархический Anki-тег («klær::sko»).

    Пробел внутри метки ломает тег, потому что теги в Anki разделяются пробелами.
    Не-ASCII не трогаем — с ним Anki работает нормально.
    """
    return "::".join(re.sub(r"[\s/]+", "-", part.strip()).strip("-").casefold() for part in path)


def _label_of(entry: dict) -> str:
    """Подпись слова. У Bildetema всегда ровно одна labels-запись, но join
    оставлен на случай второй — потерять её молча хуже, чем склеить."""
    labels = entry.get("labels") or []
    return " / ".join(label["label"].strip() for label in labels if label.get("label"))


def _article_of(entry: dict) -> str | None:
    for label in entry.get("labels") or []:
        if label.get("article"):
            return str(label["article"])
    return None


# Артикль Bildetema → род существительного. Это данные словаря, а не догадка:
# промпт grammar_forms велит модели «"f" (ei — rare, treat as m if unsure)», то есть
# записывать женский род в мужской при малейшем сомнении, а таких слов у Bildetema
# 198 из 1112. Раз источник род знает, спрашивать его у LLM незачем.
# Составные вроде "ei/en/et" сюда намеренно не попадают: там род и правда неоднозначен,
# пусть решает модель.
_GENDER_BY_ARTICLE = {
    "en": "m",
    "ei": "f",
    "ei/en": "f",  # женский, допускающий и мужское склонение — у Bildetema это отдельная пометка
    "et": "n",
}


def _gender_of(article: str | None) -> str | None:
    return _GENDER_BY_ARTICLE.get((article or "").strip().lower())


def _media_of(entry: dict) -> Media:
    images = tuple(img["src"] for img in entry.get("images") or [] if img.get("src"))
    audio = next((a["url"] for a in entry.get("audioFiles") or [] if a.get("url")), None)
    return Media(image_urls=images, audio_url=audio)


def _is_descendant(path: tuple[str, ...], ancestor: tuple[str, ...]) -> bool:
    return len(path) > len(ancestor) and path[: len(ancestor)] == ancestor


@dataclass
class Entry:
    """Одно слово Bildetema, готовое стать карточкой."""

    word_id: str
    card: Card
    media: Media
    article: str | None


def build_entries(
    database: dict,
    topic: TopicInfo,
    *,
    language: str,
    target_lang: str,
    translation_lang: str,
    gloss_lang: str = "eng",
    level: Level | None = Level.A1,
) -> list[Entry]:
    """Собрать карточки одной темы вместе со всеми её подтемами.

    Часть речи проставляется только по артиклю; у слов без него остаётся
    POS.OTHER — их доопределяет classify_missing_pos().
    """
    # Порядок обхода = порядок в словаре: сначала слова самой темы, затем каждая
    # подтема своей группой, внутри группы — по её полю `order`. Глобально `order`
    # не сквозной (в каждой подтеме он начинается с 1), поэтому сортировать все
    # слова темы разом одним ключом нельзя — группы перемешались бы.
    by_id: dict[str, dict] = {}
    paths: dict[str, tuple[str, ...]] = {}
    translations: dict[str, dict] = {}
    glosses: dict[str, dict] = {}
    for path, node in _iter_nodes(database["topics"]):
        if node["id"] != topic.id and not _is_descendant(path, topic.path):
            continue
        words = node.get("words") or {}
        for entry in sorted(words.get(target_lang) or [], key=lambda e: e.get("order", 0)):
            by_id.setdefault(entry["id"], entry)
            # Слово лежит и в подтеме, и в родительской теме. Для тега берём самый
            # глубокий путь — «klær::sko» полезнее в Anki, чем просто «klær», а
            # порядок при этом остаётся по первому появлению (обход идёт сверху вниз).
            if len(path) > len(paths.get(entry["id"], ())):
                paths[entry["id"]] = path
        for entry in words.get(translation_lang) or []:
            translations.setdefault(entry["id"], entry)
        for entry in words.get(gloss_lang) or []:
            glosses.setdefault(entry["id"], entry)

    entries: list[Entry] = []
    for word_id, entry in by_id.items():
        word = _label_of(entry)
        translation = _label_of(translations.get(word_id, {}))
        if not word or not translation:
            logger.warning("bildetema.skip_incomplete", word_id=word_id, word=word)
            continue

        article = _article_of(entry)
        gender = _gender_of(article)
        entries.append(
            Entry(
                word_id=word_id,
                card=Card(
                    language=language,
                    word=word,
                    translation=translation,
                    image_query=_label_of(glosses.get(word_id, {})) or None,
                    pos=POS.NOUN if article else POS.OTHER,
                    # Только род: остальную парадигму досыпает enrich_grammar_batch,
                    # для которого неполные forms — всё ещё повод сходить к LLM.
                    forms={"gender": gender} if gender else None,
                    level=level,
                    topic=_slug_of(paths.get(word_id, topic.path)),
                    source=f"{SOURCE_PREFIX}:{word_id}",
                    status=Status.PENDING,
                ),
                media=_media_of(entry),
                article=article,
            )
        )
    return entries


async def classify_missing_pos(entries: list[Entry]) -> None:
    """Доопределить часть речи у слов без артикля одним батч-вызовом LLM.

    Артикль ("en"/"ei/en"/"et") — надёжный признак существительного, но его нет
    примерно у каждого пятого слова, и это не только существительные: там же
    прилагательные (glad, syk), pluralia tantum (foreldre, briller) и
    неисчисляемые (melk, vann). Без разбора всё это ушло бы в POS.OTHER и
    осталось бы без грамматических форм на стадии enrich (INFLECTED_POS).

    Сама логика живёт в enrich.pos: то же самое нужно и на accept, куда карточки
    приходят после `--no-enrich`, уже без всякой связи с Bildetema.
    """
    await classify_pos_batch([entry.card for entry in entries])


# ───────────────────────── медиа ─────────────────────────


@http_retry
async def _download_audio(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        out_path.write_bytes(response.content)


async def attach_media(cards: list[Card], media: dict[str, Media], cfg: Config) -> dict:
    """Скачать фото и аудио Bildetema для уже вставленных в БД карточек.

    Ключ media — card.source ("bildetema:V0001"): он переживает dedupe, который
    часть карточек отбрасывает, в отличие от позиции в исходном списке.

    Провал скачивания не фатален: карточка просто остаётся без медиафайла, и
    обычная media-стадия пайплайна (edge-tts / поиск картинки) подхватит её как
    любую другую — см. pipeline.enrich_and_generate_media.
    """
    stats = {"images": 0, "audio": 0, "failed": 0}
    if not cards:
        return stats

    semaphore = asyncio.Semaphore(cfg.concurrency)

    async def _one(card: Card) -> None:
        item = media.get(card.source or "")
        if item is None:
            return
        async with semaphore:
            if item.image_urls:
                try:
                    filename = f"{card.id}.jpg"
                    await download_image(item.image_urls[0], cfg.paths.images_dir / filename, cfg)
                    card.image = filename
                    stats["images"] += 1
                except Exception as e:
                    stats["failed"] += 1
                    logger.warning("bildetema.image_failed", card_id=card.id, error=str(e))
            if item.audio_url:
                try:
                    filename = f"{card.id}_nb.mp3"
                    await _download_audio(item.audio_url, cfg.paths.audio_dir / filename)
                    card.audio = filename
                    stats["audio"] += 1
                except Exception as e:
                    stats["failed"] += 1
                    logger.warning("bildetema.audio_failed", card_id=card.id, error=str(e))

    await asyncio.gather(*(_one(card) for card in cards))
    logger.info("bildetema.media_done", **stats)
    return stats
