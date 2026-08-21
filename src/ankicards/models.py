"""Модели данных проекта.

Главный принцип: одна Card = одна заметка в Anki.
Поле `forms` хранится как JSON и зависит от части речи.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# Префикс тега «эту карточку человек посмотрел глазами и одобрил» (Card.mark_verified).
# Отдельно от auto_tags(): те выводятся из метаданных карточки и появляются сами,
# а этот ставится только действием человека — импортированному материалу (ingest
# bildetema и прочим чужим источникам) он на слово не выдаётся.
VERIFIED_TAG_PREFIX = "verified"


class POS(StrEnum):
    """Часть речи (Part Of Speech)."""

    NOUN = "noun"
    VERB = "verb"
    ADJECTIVE = "adj"
    ADVERB = "adv"
    PRONOUN = "pron"
    PREPOSITION = "prep"
    CONJUNCTION = "conj"
    INTERJECTION = "interj"
    NUMERAL = "num"
    PHRASE = "phrase"  # для устойчивых выражений
    OTHER = "other"


class Gender(StrEnum):
    """Род существительного в bokmål."""

    MASCULINE = "m"  # en bil
    FEMININE = "f"  # ei jente (часто заменяется на m)
    NEUTER = "n"  # et hus


class Status(StrEnum):
    """Статус карточки в pipeline."""

    PENDING = "pending"  # только что создана, ждёт enrichment
    REVIEW = "review"  # требует ручного решения (дубликат?)
    APPROVED = "approved"  # готова к push в Anki
    PUSHED = "pushed"  # уже в Anki
    SUSPENDED = "suspended"  # отложена
    SKIPPED = "skipped"  # отброшена


class Level(StrEnum):
    """CEFR уровень."""

    A1 = "a1"
    A2 = "a2"
    B1 = "b1"
    B2 = "b2"
    C1 = "c1"
    C2 = "c2"


# ───────────────────────── Forms ─────────────────────────
# Формы зависят от языка. Схема полей читается из languages/{code}/language.yaml.
# Card.forms — dict с ключами из forms.{pos}[].key, заполняется LLM по схеме.
# Пример для норвежского:
#   noun: {gender, indefinite_singular, definite_singular, indefinite_plural, definite_plural}
#   verb: {infinitive, present, past, perfect}
#   adj:  {positive_common, positive_neuter, positive_plural, comparative, superlative}
# Для других языков структура задаётся в language.yaml — никакого хардкода.

# ───────────────────────── Card ─────────────────────────


class Card(BaseModel):
    """Словарная карточка. Соответствует одной заметке в Anki."""

    id: int | None = None  # выделяется Database.insert_card() — наименьший свободный номер
    language: str  # код языкового профиля на момент создания (languages/{code}/) — issue #63
    word: str  # основная форма (lemma)
    pronunciation: str | None = None  # практическая транскрипция или IPA — см. config.transcription
    translation: str  # 1-2 варианта
    image_query: str | None = None  # англ. gloss для поиска картинок (issue #10)
    example: str | None = None  # пример на норвежском
    example_translation: str | None = None  # перевод примера

    pos: POS
    forms: dict | None = None  # NounForms/VerbForms/AdjectiveForms.dict()

    level: Level | None = None
    topic: str | None = None  # mat, klær, reise...
    source: str | None = None  # nrk, manual, topic-gen, url:...

    image: str | None = None  # имя файла, не путь
    audio: str | None = None  # имя файла, не путь

    tags: list[str] = Field(default_factory=list)  # дополнительные кастомные теги
    status: Status = Status.PENDING
    date_added: date = Field(default_factory=date.today)

    # Anki-specific (заполняется после push)
    anki_note_id: int | None = None

    def mark_verified(self, day: date | None = None) -> str:
        """Пометить карточку как просмотренную человеком; вернуть навешенный тег.

        Тег иерархический (`verified::2026-08-21`), поэтому в Anki по `tag:verified::*`
        находятся все проверенные разом, а по конкретной дате — то, что смотрелось в
        тот заход. Повторный accept той же карточки тег не дублирует, но и дату
        первой проверки не переписывает: важно, когда человек её увидел впервые.
        """
        tag = f"{VERIFIED_TAG_PREFIX}::{(day or date.today()).isoformat()}"
        if not any(t.startswith(f"{VERIFIED_TAG_PREFIX}::") for t in self.tags):
            self.tags.append(tag)
        return tag

    def is_verified(self) -> bool:
        """Карточку уже подтверждал человек (см. mark_verified)."""
        return any(t.startswith(f"{VERIFIED_TAG_PREFIX}::") for t in self.tags)

    def auto_tags(self) -> list[str]:
        """Сгенерировать иерархические теги Anki из метаданных."""
        result = list(self.tags)
        if self.topic:
            result.append(f"topic::{self.topic}")
        if self.level:
            result.append(f"level::{self.level.value}")
        result.append(f"pos::{self.pos.value}")
        if self.source:
            # source может содержать URL — берём только домен или префикс
            src = self.source.split(":")[0] if ":" in self.source else self.source
            result.append(f"source::{src}")
        return result


# ───────────────────────── Decision ─────────────────────────

DecisionType = Literal["new", "review", "merge", "skip"]


class DuplicateMatch(BaseModel):
    """Найденный потенциальный дубликат."""

    existing_card_id: str
    existing_word: str
    score: float  # 0-100, rapidfuzz
    matched_field: str  # по какому полю совпало


class Decision(BaseModel):
    """Решение dedupe-стадии для одного кандидата."""

    decision: DecisionType
    matches: list[DuplicateMatch] = Field(default_factory=list)
    reason: str | None = None


# ───────────────────────── Inconsistency ─────────────────────────


class Inconsistency(BaseModel):
    """Найденное doctor-проверкой расхождение между включённым тумблером
    enrichment-конфига и фактическими данными карточки."""

    card_id: int
    word: str
    # "enrich.grammar" | "enrich.examples" | "enrich.pronunciation"
    # | "images.enabled" | "anki_note_id"
    check: str
    reason: str
