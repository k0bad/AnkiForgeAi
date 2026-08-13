"""Загрузка config.yaml и переменных окружения.

Конфиг кэшируется в памяти. Для перезагрузки — рестарт процесса.
"""

from __future__ import annotations

from functools import cache, lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PACKAGE_DIR = Path(__file__).resolve().parent
BUNDLED_LANGUAGES_DIR = _PACKAGE_DIR / "languages"
BUNDLED_PROMPTS_DIR = _PACKAGE_DIR / "prompts"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if not (PROJECT_ROOT / "languages").is_dir():
    # Installed from PyPI (pip/uv tool install) — no sibling languages/ dir
    # next to the package. config.yaml / data / media live in the directory
    # the user runs `ankiforgeai` from instead. languages/ and prompts/
    # fall back to the copies bundled inside the package (see pyproject.toml
    # tool.hatch.build.targets.wheel.force-include and languages_dir() below).
    PROJECT_ROOT = Path.cwd()

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def languages_dir() -> Path:
    """Root dir for language profiles: PROJECT_ROOT/languages, or bundled if absent."""
    checkout_dir = PROJECT_ROOT / "languages"
    return checkout_dir if checkout_dir.is_dir() else BUNDLED_LANGUAGES_DIR


# ───────────── Pydantic-схемы для config.yaml ─────────────


class PathsConfig(BaseModel):
    db: Path
    logs_dir: Path
    audio_dir: Path
    images_dir: Path
    prompts_dir: Path


class AnkiConfig(BaseModel):
    url: str = "http://127.0.0.1:8765"
    deck_name: str = "Norsk"
    note_type: str = "LanguageCard"


class DedupeConfig(BaseModel):
    exact_match_field: str = "Word"
    fuzzy_threshold_review: float = 85
    fuzzy_threshold_auto: float = 70
    compare_fields: list[str] = Field(default_factory=lambda: ["Word", "Translation"])
    # Для нечётких совпадений спрашивать LLM "это дубликат или просто похожие слова",
    # вместо того чтобы всегда откладывать решение на человека (см. dedupe.judge_review).
    ai_adjudication: bool = True
    # Модель именно для judge_review (пусто = взять llm.model). Задача бинарная
    # (SAME/DIFFERENT/UNSURE), топовая/дорогая модель тут не нужна — можно
    # указать дешёвую/быструю, провайдер (llm.provider) при этом не меняется.
    judge_model: str = ""


class IngestConfig(BaseModel):
    url_max_size_mb: int = 5
    url_timeout_seconds: int = 30
    default_count: int = 20
    default_level: str = "A2"


class LLMConfig(BaseModel):
    provider: str = "openrouter"  # "anthropic" | "openrouter"
    model: str = "deepseek/deepseek-v4-flash"
    base_url: str = "https://openrouter.ai/api/v1"
    max_tokens: int = 4096
    temperature: float = 0.3


class TTSConfig(BaseModel):
    voice_male: str = "nb-NO-FinnNeural"
    voice_female: str = "nb-NO-PernilleNeural"
    default_voice: str = "female"
    rate: str = "+0%"
    pitch: str = "+0Hz"


class ImagesConfig(BaseModel):
    enabled: bool = True
    provider: str = "unsplash"  # "unsplash" | "pexels" | "pixabay" | "openverse"
    per_page: int = 5
    only_for_pos: list[str] = Field(default_factory=lambda: ["noun"])
    max_size_kb: int = 500
    resize_to: tuple[int, int] = (600, 600)


class ReviewConfig(BaseModel):
    mode: str = "semiauto"  # manual / semiauto / auto
    auto_accept_below_score: bool = True


class EnrichConfig(BaseModel):
    """Какие стадии обогащения запускать (настраивается в setup wizard)."""

    grammar: bool = True
    examples: bool = True
    pronunciation: bool = True


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json_logs: bool = True
    human_logs: bool = True


class NotificationConfig(BaseModel):
    """Один канал доставки уведомлений (см. notify/)."""

    type: str = "webhook"  # пока единственный бэкенд: POST JSON на произвольный URL
    enabled: bool = True
    url: str = ""
    # "text" — готовое Telegram-flavored сообщение (для n8n и т.п.);
    # "json" — сырой report целиком, для посредника со своей маршрутизацией (Hermes и т.п.)
    format: str = "text"


class TagsConfig(BaseModel):
    topic_prefix: str = "topic"
    level_prefix: str = "level"
    pos_prefix: str = "pos"
    source_prefix: str = "source"


NoteFieldSlot = Literal["front_title", "front_audio", "front_image", "section", "tag", "hidden"]


class NoteFieldDef(BaseModel):
    """Одно поле Anki Note Type: источник данных (FIELD_RESOLVERS в anki/notetype.py)
    и место в шаблоне карточки (slot)."""

    name: str
    source: str
    slot: NoteFieldSlot = "section"
    optional: bool = False  # section: обернуть в {{#Field}}...{{/Field}} на бэке
    label_key: str | None = None  # ключ в back_labels/EN_BACK_LABELS; по умолчанию name.lower()
    css_class: str | None = None  # по умолчанию name.lower()


class AnkiProfileConfig(BaseModel):
    """anki: секция в language.yaml — колода, Note Type и (опционально) схема полей.

    fields=None означает "использовать DEFAULT_FIELDS" (anki/notetype.py) —
    сегодняшний фиксированный набор из 12 полей.
    """

    deck_name: str = ""
    note_type: str = "LanguageCard"
    fields: list[NoteFieldDef] | None = None


class LanguageConfig(BaseModel):
    """Языковой профиль — загружается из languages/{code}/language.yaml."""

    code: str
    name: str
    article: bool = False
    pos_labels: dict[str, str] = Field(default_factory=dict)
    forms: dict[str, list[dict]] = Field(default_factory=dict)
    tts: dict[str, str] = Field(default_factory=dict)
    anki: AnkiProfileConfig = Field(default_factory=AnkiProfileConfig)
    back_labels: dict[str, str] = Field(default_factory=dict)

    @property
    def prompts_dir(self) -> Path:
        return languages_dir() / self.code / "prompts"


# Английские подписи бэк-стороны карточки — альтернатива дефолтным русским
# back_labels из languages/{code}/language.yaml (все языковые профили сейчас
# зашивают русские подписи). Выбирается в setup wizard (config.ui_language).
EN_BACK_LABELS: dict[str, str] = {
    "translation": "Translation",
    "part_of_speech": "Part of Speech",
    "grammar": "Grammar",
    "example": "Example",
    "example_translation": "Example Translation",
    "pronunciation": "Pronunciation",
    "level": "Level",
    "topic": "Topic",
}


class Config(BaseModel):
    language: str = "nb"  # код целевого языка → languages/{code}/language.yaml
    ui_language: str = "ru"  # язык подписей бэк-стороны карточки: "ru" | "en"
    transcription: str = "practical"  # тип транскрипции произношения: "practical" | "ipa"
    paths: PathsConfig
    anki: AnkiConfig
    dedupe: DedupeConfig
    ingest: IngestConfig
    llm: LLMConfig
    tts: TTSConfig
    images: ImagesConfig
    review: ReviewConfig
    enrich: EnrichConfig = Field(default_factory=EnrichConfig)
    logging: LoggingConfig
    tags: TagsConfig
    notifications: list[NotificationConfig] = Field(default_factory=list)

    def resolve_paths(self) -> None:
        """Сделать относительные пути абсолютными от корня проекта."""
        for field in self.paths.model_fields:
            value = getattr(self.paths, field)
            if not value.is_absolute():
                setattr(self.paths, field, PROJECT_ROOT / value)


# ───────────── Секреты из .env ─────────────


class Secrets(BaseSettings):
    """Чувствительные данные из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    unsplash_access_key: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""


# ───────────── API ─────────────


@lru_cache(maxsize=1)
def get_config(path: Path | None = None) -> Config:
    """Загрузить и закэшировать конфигурацию."""
    cfg_path = path or DEFAULT_CONFIG_PATH
    with cfg_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    cfg = Config.model_validate(raw)
    cfg.resolve_paths()
    return cfg


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    """Загрузить секреты из .env."""
    load_dotenv(PROJECT_ROOT / ".env")
    return Secrets()


@cache
def get_language(target: str | None = None) -> LanguageConfig:
    """Загрузить языковой профиль из languages/{target}/language.yaml."""
    code = target or "nb"
    lang_path = languages_dir() / code / "language.yaml"
    if not lang_path.exists():
        raise FileNotFoundError(f"Language file not found: {lang_path}")
    with lang_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    lang = LanguageConfig.model_validate(raw)
    return lang
