"""Register the daily-automation cycle (scripts/daily_topic.py) with the OS scheduler.

Windows -> Task Scheduler (Register-ScheduledTask), Linux/macOS -> cron (crontab).
Re-running is idempotent: Windows re-registration uses -Force, cron entries are
identified by a marker comment and replaced rather than duplicated.

Called from setup_wizard.run_setup() so `ankiforgeai setup` can offer hands-free
daily generation instead of requiring the manual steps documented in
README.md -> Automated Daily Cycle.
"""

from __future__ import annotations

import platform
import re
import subprocess
from pathlib import Path

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_TASK_NAME = "AnkiForgeAI Daily Words"
_CRON_MARKER = "# ankiforgeai-daily-topic"


def validate_time(value: str) -> str | None:
    """Normalize a 24h HH:MM string, or None if `value` doesn't match."""
    match = _TIME_RE.match(value.strip())
    if not match:
        return None
    hour, minute = match.groups()
    return f"{int(hour):02d}:{minute}"


def register_daily_automation(project_root: Path, run_time: str) -> tuple[bool, str]:
    """Register the daily cycle to run at `run_time` (24h HH:MM). Returns (ok, message)."""
    normalized = validate_time(run_time)
    if normalized is None:
        return False, f"Invalid time '{run_time}' (expected 24h HH:MM)."
    if platform.system() == "Windows":
        return _register_windows_task(project_root, normalized)
    return _register_cron(project_root, normalized)


def _register_windows_task(project_root: Path, run_time: str) -> tuple[bool, str]:
    script_path = project_root / "scripts" / "daily_topic.ps1"
    ps_command = (
        f'$action = New-ScheduledTaskAction -Execute "powershell.exe" '
        f"-Argument '-NoProfile -ExecutionPolicy Bypass -File \"{script_path}\"'; "
        f"$trigger = New-ScheduledTaskTrigger -Daily -At {run_time}; "
        f"$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable; "
        f'Register-ScheduledTask -TaskName "{_TASK_NAME}" -Action $action '
        f"-Trigger $trigger -Settings $settings "
        f'-Description "ankicards: generate + auto-push + notify daily vocabulary" -Force'
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_command],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"Could not run PowerShell: {e}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, f"Task Scheduler registration failed: {detail}"
    return True, f'Registered Task Scheduler job "{_TASK_NAME}" — runs daily at {run_time}.'


def _register_cron(project_root: Path, run_time: str) -> tuple[bool, str]:
    hour, minute = run_time.split(":")
    script_path = project_root / "scripts" / "daily_topic.sh"
    log_path = project_root / "data" / "logs" / "daily_topic_cron.log"
    cron_line = (
        f"{int(minute)} {int(hour)} * * * cd {project_root} && "
        f"{script_path} >> {log_path} 2>&1  {_CRON_MARKER}"
    )

    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"crontab not available: {e}"

    current = existing.stdout if existing.returncode == 0 else ""
    lines = [ln for ln in current.splitlines() if _CRON_MARKER not in ln]
    lines.append(cron_line)
    new_crontab = "\n".join(lines) + "\n"

    try:
        result = subprocess.run(
            ["crontab", "-"], input=new_crontab, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"Could not update crontab: {e}"

    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        return False, f"crontab update failed: {detail}"
    return True, f"Registered cron job — runs daily at {run_time}."
