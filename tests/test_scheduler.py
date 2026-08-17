"""Тесты для scheduler.register_daily_automation (issue #58, часть #3)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ankicards import scheduler


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ─── validate_time ───


@pytest.mark.parametrize("value", ["08:00", "8:00", "0:00", "23:59", " 09:30 "])
def test_validate_time_accepts_valid_24h(value: str) -> None:
    assert scheduler.validate_time(value) is not None


@pytest.mark.parametrize(
    "value",
    [
        "24:00",
        "12:60",
        "not-a-time",
        "",
        '08:00"; Remove-Item -Recurse C:\\',
        "08:00 && rm -rf /",
    ],
)
def test_validate_time_rejects_invalid(value: str) -> None:
    assert scheduler.validate_time(value) is None


def test_validate_time_normalizes_single_digit_hour() -> None:
    assert scheduler.validate_time("8:05") == "08:05"


# ─── register_daily_automation dispatch ───


def test_register_daily_automation_rejects_bad_time_without_calling_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess.run should not be called for an invalid time")

    monkeypatch.setattr(subprocess, "run", _boom)
    ok, message = scheduler.register_daily_automation(Path("/repo"), "nonsense")
    assert ok is False
    assert "Invalid time" in message


def test_register_daily_automation_dispatches_to_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Windows")
    called = {}

    def _fake(project_root: Path, run_time: str) -> tuple[bool, str]:
        called["args"] = (project_root, run_time)
        return True, "ok"

    monkeypatch.setattr(scheduler, "_register_windows_task", _fake)
    ok, message = scheduler.register_daily_automation(Path("/repo"), "08:00")
    assert (ok, message) == (True, "ok")
    assert called["args"] == (Path("/repo"), "08:00")


def test_register_daily_automation_dispatches_to_cron_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Linux")
    called = {}

    def _fake(project_root: Path, run_time: str) -> tuple[bool, str]:
        called["args"] = (project_root, run_time)
        return True, "ok"

    monkeypatch.setattr(scheduler, "_register_cron", _fake)
    ok, message = scheduler.register_daily_automation(Path("/repo"), "08:00")
    assert (ok, message) == (True, "ok")
    assert called["args"] == (Path("/repo"), "08:00")


# ─── _register_windows_task ───


def test_register_windows_task_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["cmd"] = cmd
        return _completed(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, message = scheduler._register_windows_task(Path("/repo"), "08:00")

    assert ok is True
    assert scheduler._TASK_NAME in message
    assert "08:00" in message
    ps_command = captured["cmd"][-1]
    assert "-Force" in ps_command
    assert scheduler._TASK_NAME in ps_command
    assert "daily_topic.ps1" in ps_command
    assert "-At 08:00" in ps_command


def test_register_windows_task_nonzero_exit_reports_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _completed(returncode=1, stderr="access denied")
    )
    ok, message = scheduler._register_windows_task(Path("/repo"), "08:00")
    assert ok is False
    assert "access denied" in message


def test_register_windows_task_oserror_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("powershell.exe not found")

    monkeypatch.setattr(subprocess, "run", _boom)
    ok, message = scheduler._register_windows_task(Path("/repo"), "08:00")
    assert ok is False
    assert "powershell.exe not found" in message


# ─── _register_cron ───


def test_register_cron_appends_when_crontab_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        if cmd == ["crontab", "-l"]:
            return _completed(returncode=1, stdout="")  # no crontab yet
        if cmd == ["crontab", "-"]:
            captured["input"] = kwargs.get("input", "")
            return _completed(returncode=0)
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, message = scheduler._register_cron(Path("/repo"), "08:00")

    assert ok is True
    assert "08:00" in message
    assert scheduler._CRON_MARKER in captured["input"]
    assert "0 8 * * *" in captured["input"]


def test_register_cron_replaces_existing_marker_line_without_duplicating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_line = f"0 7 * * * echo old  {scheduler._CRON_MARKER}"
    unrelated_line = "0 3 * * * /usr/local/bin/backup.sh"
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        if cmd == ["crontab", "-l"]:
            return _completed(returncode=0, stdout=f"{unrelated_line}\n{old_line}\n")
        if cmd == ["crontab", "-"]:
            captured["input"] = kwargs.get("input", "")
            return _completed(returncode=0)
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, _ = scheduler._register_cron(Path("/repo"), "09:15")

    assert ok is True
    new_crontab = captured["input"]
    assert unrelated_line in new_crontab
    assert new_crontab.count(scheduler._CRON_MARKER) == 1
    assert "old" not in new_crontab
    assert "15 9 * * *" in new_crontab


def test_register_cron_update_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        if cmd == ["crontab", "-l"]:
            return _completed(returncode=1, stdout="")
        return _completed(returncode=1, stderr="crontab: permission denied")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, message = scheduler._register_cron(Path("/repo"), "08:00")
    assert ok is False
    assert "permission denied" in message


def test_register_cron_missing_binary_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("crontab: command not found")

    monkeypatch.setattr(subprocess, "run", _boom)
    ok, message = scheduler._register_cron(Path("/repo"), "08:00")
    assert ok is False
    assert "not available" in message
