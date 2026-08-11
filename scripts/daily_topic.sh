#!/usr/bin/env bash
# Ежедневная генерация слов по расписанию тем (для cron).
# Полный автоматический цикл: генерация → авто-принятие → push в Anki → уведомления.
# Доставка уведомлений (каналы из config.yaml -> notifications) выполняется
# внутри daily_topic.py через ankicards.notify — этот скрипт лишь тонкая
# обёртка для cron.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

.venv/bin/python scripts/daily_topic.py --notify
