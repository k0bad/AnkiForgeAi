# Windows Task Scheduler-версия scripts/daily_topic.sh: полный автоцикл
# (генерация -> dedupe/enrich -> auto-push в Anki -> notify), см. daily_topic.py.
# Доставка уведомлений (каналы из config.yaml -> notifications, URL — из .env
# NOTIFY_WEBHOOK_URL) выполняется внутри daily_topic.py через ankicards.notify —
# этот скрипт лишь тонкая обёртка для Task Scheduler.
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

& "$ProjectRoot\.venv\Scripts\python.exe" "$ProjectRoot\scripts\daily_topic.py" --notify
exit $LASTEXITCODE
