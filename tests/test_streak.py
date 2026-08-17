"""Тесты для db.count_pushed_by_date и pipeline.compute_streak (issue #58, часть #2)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from ankicards.db import Database
from ankicards.pipeline import compute_streak

_TODAY = date(2026, 8, 17)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "cards.db")


def _log_push(db: Database, day: date, count: int = 1) -> None:
    """Insert `count` raw push audit_log rows timestamped on `day` (noon UTC)."""
    with db.connect() as conn:
        for i in range(count):
            conn.execute(
                "INSERT INTO audit_log (timestamp, action, card_id, details, run_id) "
                "VALUES (?, 'push', ?, '{}', NULL)",
                (f"{day.isoformat()}T12:00:00+00:00", i + 1),
            )


def _log_other_action(db: Database, day: date, action: str = "create") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, card_id, details, run_id) "
            "VALUES (?, ?, 1, '{}', NULL)",
            (f"{day.isoformat()}T12:00:00+00:00", action),
        )


# ─── count_pushed_by_date ───


def test_count_pushed_by_date_empty_when_nothing_pushed(db: Database) -> None:
    assert db.count_pushed_by_date() == {}


def test_count_pushed_by_date_groups_and_counts(db: Database) -> None:
    _log_push(db, _TODAY, count=3)
    _log_push(db, _TODAY - timedelta(days=1), count=1)

    result = db.count_pushed_by_date()

    assert result[_TODAY.isoformat()] == 3
    assert result[(_TODAY - timedelta(days=1)).isoformat()] == 1


def test_count_pushed_by_date_ignores_non_push_actions(db: Database) -> None:
    _log_other_action(db, _TODAY, action="create")
    _log_other_action(db, _TODAY, action="sync")

    assert db.count_pushed_by_date() == {}


# ─── compute_streak ───


def test_compute_streak_zero_when_never_pushed(db: Database) -> None:
    assert compute_streak(db, today=_TODAY) == 0


def test_compute_streak_counts_consecutive_days_including_today(db: Database) -> None:
    _log_push(db, _TODAY)
    _log_push(db, _TODAY - timedelta(days=1))
    _log_push(db, _TODAY - timedelta(days=2))

    assert compute_streak(db, today=_TODAY) == 3


def test_compute_streak_stops_at_gap(db: Database) -> None:
    _log_push(db, _TODAY)
    _log_push(db, _TODAY - timedelta(days=1))
    # gap at day-2
    _log_push(db, _TODAY - timedelta(days=3))

    assert compute_streak(db, today=_TODAY) == 2


def test_compute_streak_falls_back_to_yesterday_when_today_not_pushed_yet(db: Database) -> None:
    """Before the daily-automation job has run today, the streak shouldn't look broken."""
    _log_push(db, _TODAY - timedelta(days=1))
    _log_push(db, _TODAY - timedelta(days=2))

    assert compute_streak(db, today=_TODAY) == 2


def test_compute_streak_is_zero_when_yesterday_also_missing(db: Database) -> None:
    _log_push(db, _TODAY - timedelta(days=5))

    assert compute_streak(db, today=_TODAY) == 0


def test_compute_streak_defaults_today_to_real_date(db: Database) -> None:
    _log_push(db, date.today())

    assert compute_streak(db) == 1
