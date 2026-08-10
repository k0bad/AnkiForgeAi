"""Дедупликация: проверка кандидата против локальной БД и кэша Anki.

Алгоритм (по убыванию строгости):
1. Точное совпадение по `word` → decision=merge.
2. rapidfuzz fuzzy match на word:
   - score >= fuzzy_threshold_review (85)  → decision=review
   - score >= fuzzy_threshold_auto   (70)  → decision=review (semiauto)
   - score < fuzzy_threshold_auto          → decision=new
3. Дополнительно для близких score: сравнить translation/example.

Источник эталонных слов:
- db.all_words()        — staging
- db.all_anki_words()   — кэш Anki (обновляется командой `sync`)
"""

from __future__ import annotations

from rapidfuzz import fuzz, process

from .config import Config
from .db import Database
from .models import Card, Decision, DuplicateMatch


def _normalize(word: str) -> str:
    """Нормализация: lower + trim + collapse whitespace + strip 'å '-префикса глагола."""
    normalized = " ".join(word.lower().split())
    if normalized.startswith("å "):
        normalized = normalized[2:]
    return normalized


def check_card(card: Card, db: Database, cfg: Config) -> Decision:
    """Проверить одного кандидата."""
    staging = db.all_words()
    anki_cache = [(str(nid), w) for nid, w in db.all_anki_words()]
    candidates = [c for c in staging + anki_cache if c[0] != card.id]

    if not candidates:
        return Decision(decision="new")

    exact_id = _exact_match(card.word, candidates)
    if exact_id is not None:
        existing_word = next(w for cid, w in candidates if cid == exact_id)
        return Decision(
            decision="merge",
            matches=[
                DuplicateMatch(
                    existing_card_id=exact_id,
                    existing_word=existing_word,
                    score=100.0,
                    matched_field="word",
                )
            ],
            reason="exact match on word",
        )

    review_threshold = cfg.dedupe.fuzzy_threshold_review
    auto_threshold = cfg.dedupe.fuzzy_threshold_auto

    matches = _fuzzy_matches(card.word, candidates, threshold=auto_threshold)
    if not matches:
        return Decision(decision="new")

    top_score = matches[0].score
    if top_score >= review_threshold:
        return Decision(
            decision="review",
            matches=matches,
            reason=f"fuzzy score {top_score:.1f} >= {review_threshold}",
        )
    return Decision(
        decision="review",
        matches=matches,
        reason=f"fuzzy score {top_score:.1f} in semiauto band",
    )


def _exact_match(word: str, candidates: list[tuple[str, str]]) -> str | None:
    """Найти точное совпадение по слову (case-insensitive, normalized)."""
    needle = _normalize(word)
    for cid, existing in candidates:
        if _normalize(existing) == needle:
            return cid
    return None


def _fuzzy_matches(
    word: str,
    candidates: list[tuple[str, str]],
    threshold: float,
    limit: int = 5,
) -> list[DuplicateMatch]:
    """Найти fuzzy-совпадения через rapidfuzz."""
    if not candidates:
        return []
    needle = _normalize(word)
    choices = {cid: _normalize(w) for cid, w in candidates}
    results = process.extract(
        needle,
        choices,
        scorer=fuzz.WRatio,
        limit=limit,
        score_cutoff=threshold,
    )
    id_to_word = dict(candidates)
    return [
        DuplicateMatch(
            existing_card_id=cid,
            existing_word=id_to_word[cid],
            score=float(score),
            matched_field="word",
        )
        for _, score, cid in results
    ]
