"""Дедупликация: проверка кандидата против локальной БД и кэша Anki.

Алгоритм (по убыванию строгости):
1. Точное совпадение по `word` → decision=merge.
2. rapidfuzz fuzzy match на word:
   - score >= fuzzy_threshold_review (85)  → decision=review
   - score >= fuzzy_threshold_auto   (70)  → decision=review (semiauto)
   - score < fuzzy_threshold_auto          → decision=new
3. Дополнительно для близких score: сравнить translation/example.

check_card() — чистая строковая эвристика, синхронная, без сети.
judge_review() — опциональный второй проход поверх decision="review": спрашивает
LLM, дубликат ли это на самом деле, чтобы не откладывать на человека каждое
похожее по буквам, но по смыслу разное слово (см. cfg.dedupe.ai_adjudication).

Источник эталонных слов:
- db.all_words()        — staging
- db.all_anki_words()   — кэш Anki (обновляется командой `sync`)
"""

from __future__ import annotations

from rapidfuzz import fuzz, process

from .config import Config
from .db import Database
from .llm import call_text, load_prompt
from .log import get_logger
from .models import Card, Decision, DuplicateMatch

logger = get_logger(__name__)


def _normalize(word: str) -> str:
    """Нормализация: lower + trim + collapse whitespace + strip 'å '-префикса глагола."""
    normalized = " ".join(word.lower().split())
    if normalized.startswith("å "):
        normalized = normalized[2:]
    return normalized


def check_card(card: Card, db: Database, cfg: Config) -> Decision:
    """Проверить одного кандидата против staging (db.all_words()) и кэша Anki.

    Кандидаты внутри одного батча ingest тоже ловятся: run_ingest_pipeline
    вставляет каждую принятую "new"-карточку в БД сразу же (id нужен ещё до
    enrich/media — см. db.py::_next_free_id), так что уже проверенные в этом
    же прогоне карточки видны здесь через staging без отдельного механизма.
    """
    staging = db.all_words()
    anki_cache = [(str(nid), w) for nid, w in db.all_anki_words()]
    candidates = [c for c in staging + anki_cache if c[0] != str(card.id)]

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


async def judge_review(card: Card, decision: Decision, cfg: Config) -> Decision:
    """Для decision="review" спросить LLM, дубликат ли это на самом деле.

    rapidfuzz сравнивает буквы, а не смысл, поэтому "review" часто ловит просто
    похожие по написанию, но разные слова. Здесь LLM смотрит на слово+перевод и
    решает: SAME → merge (пропустить как дубликат), DIFFERENT → new (принять),
    UNSURE или сбой вызова → decision не меняется, остаётся на человеке
    (никогда не молчим до auto-accept при ошибке LLM).
    """
    if not cfg.dedupe.ai_adjudication or decision.decision != "review" or not decision.matches:
        return decision

    match = decision.matches[0]
    try:
        prompt = load_prompt(
            "dedupe_judge",
            new_word=card.word,
            new_translation=card.translation,
            new_pos=card.pos.value,
            existing_word=match.existing_word,
            score=match.score,
        )
        raw = await call_text(prompt, model=cfg.dedupe.judge_model or None, stage="dedupe")
        verdict = raw.strip().upper()
    except Exception as e:
        logger.warning("dedupe.judge_failed", card_id=card.id, word=card.word, error=str(e))
        return decision

    if verdict == "SAME":
        return Decision(
            decision="merge",
            matches=decision.matches,
            reason=f"AI: дубликат '{match.existing_word}' (fuzzy {match.score:.0f})",
        )
    if verdict == "DIFFERENT":
        return Decision(
            decision="new",
            reason=f"AI: другое слово, не '{match.existing_word}' (fuzzy {match.score:.0f})",
        )
    return decision
