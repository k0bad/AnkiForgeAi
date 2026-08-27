"""Догон одной enrich-стадии по уже заведённым карточкам.

Отличие от пайплайна: там стадия идёт по свежей пачке из ingest (десяток-другой
карточек) и один batch-вызов покрывает её целиком. Здесь пачка — вся накопленная
база, и то же самое одним вызовом не проходит: ответ обрывается по лимиту токенов,
а карточки, до которых модель не дошла, тихо остаются пустыми.

Поэтому здесь три вещи, которых нет в `pipeline._run_enrich_stage`:

* карточки режутся на куски и каждый уходит своим вызовом;
* результат каждого куска сразу пишется в БД — обрыв на середине не отменяет то,
  что уже сделано, и повторный запуск продолжает с того же места;
* провал одного куска не роняет прогон: остальные всё равно обрабатываются, а
  проваленные карточки попадают в отчёт.

Статус карточки стадия не трогает: догон — это дозаполнение полей, а не решение
о судьбе карточки, оно за человеком.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field

from ..db import Database
from ..log import get_logger
from ..models import Card

logger = get_logger(__name__)

#: Сколько карточек уходит в один вызов. Меньше — больше round-trip'ов и
#: системных промптов; больше — дольше ответ и дороже цена одного сбоя.
#:
#: 30, а не «сколько влезет»: на llm.provider=claude_cli пачка в 50 карточек
#: стабильно возвращала `is_error` ровно через ~180 с — генерация упирается в
#: серверный потолок и до конца не доходит, а три ретрая превращают это в
#: девять потерянных минут на кусок. Тридцать укладывается в минуту с запасом.
#: Поднимать имеет смысл вместе с провайдером, который отвечает быстрее.
DEFAULT_CHUNK = 30

StageFn = Callable[[list[Card]], Awaitable[list[Card]]]
ProgressFn = Callable[[int, int], None]


@dataclass
class BackfillReport:
    """Итог догона: что заполнено, что осталось, где ломалось."""

    stage: str
    done: list[int] = field(default_factory=list)
    #: Вызов прошёл, но для этих карточек модель ничего не вернула.
    empty: list[int] = field(default_factory=list)
    #: Кусок целиком не удался — вместе с причиной.
    failed: list[dict] = field(default_factory=list)
    calls: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "done": len(self.done),
            "empty": len(self.empty),
            "failed": sum(len(f["ids"]) for f in self.failed),
            "calls": self.calls,
        }


def _chunks(cards: list[Card], size: int) -> Iterator[list[Card]]:
    for start in range(0, len(cards), size):
        yield cards[start : start + size]


async def backfill_stage(
    stage: str,
    fn: StageFn,
    is_complete: Callable[[Card], bool],
    cards: list[Card],
    db: Database,
    chunk_size: int = DEFAULT_CHUNK,
    progress: ProgressFn | None = None,
    dry_run: bool = False,
    limit: int = 0,
) -> BackfillReport:
    """Дозаполнить `stage` у тех карточек из `cards`, где его ещё нет.

    `limit` режет не входной список, а список тех, кому стадии не хватает: иначе
    на повторном запуске квота уходила бы на уже готовые карточки и прогон
    топтался бы на месте вместо того, чтобы двигаться дальше.
    """
    report = BackfillReport(stage=stage)
    targets = [c for c in cards if not is_complete(c)]
    if limit:
        targets = targets[:limit]
    if not targets:
        return report

    processed = 0
    for chunk in _chunks(targets, chunk_size):
        ids = [c.id for c in chunk if c.id is not None]
        report.calls += 1
        if dry_run:
            report.done.extend(ids)
            processed += len(chunk)
            if progress:
                progress(processed, len(targets))
            continue
        try:
            await fn(chunk)
        except Exception as e:
            logger.warning("backfill.chunk_failed", stage=stage, count=len(chunk), error=str(e))
            for card in chunk:
                db.log_action("enrich_failed", card.id, {"stage": stage, "error": str(e)})
            report.failed.append({"ids": ids, "error": str(e)})
            processed += len(chunk)
            if progress:
                progress(processed, len(targets))
            continue

        for card in chunk:
            if is_complete(card):
                db.update_card(card)
                if card.id is not None:
                    report.done.append(card.id)
            else:
                if card.id is not None:
                    report.empty.append(card.id)
                db.log_action("enrich_incomplete", card.id, {"stage": stage})
        processed += len(chunk)
        if progress:
            progress(processed, len(targets))

    logger.info("backfill.done", stage=stage, **report.counts())
    return report
