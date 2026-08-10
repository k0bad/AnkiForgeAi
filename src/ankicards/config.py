"""Загрузка config.yaml и переменных окружения.

Конфиг кэшируется в памяти. Для перезагрузки — рестарт процесса.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


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


class TagsConfig(BaseModel):
    topic_prefix: str = "topic"
    level_prefix: str = "level"
    pos_prefix: str = "pos"
    source_prefix: str = "source"


class LanguageConfig(BaseModel):
    """Языковой профиль — загружается из languages/{code}/language.yaml."""

    code: str
    name: str
    article: bool = False
    pos_labels: dict[str, str] = Field(default_factory=dict)
    forms: dict[str, list[dict]] = Field(default_factory=dict)
    tts: dict[str, str] = Field(default_factory=dict)
    anki: dict[str, str] = Field(default_factory=dict)
    back_labels: dict[str, str] = Field(default_factory=dict)

    @property
    def prompts_dir(self) -> Path:
        return PROJECT_ROOT / "languages" / self.code / "prompts"


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


@lru_cache(maxsize=None)
def get_language(target: str | None = None) -> LanguageConfig:
    """Загрузить языковой профиль из languages/{target}/language.yaml."""
    code = target or "nb"
    lang_path = PROJECT_ROOT / "languages" / code / "language.yaml"
    if not lang_path.exists():
        raise FileNotFoundError(f"Language file not found: {lang_path}")
    with lang_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    lang = LanguageConfig.model_validate(raw)
    return lang
