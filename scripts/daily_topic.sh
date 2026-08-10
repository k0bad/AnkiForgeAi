#!/usr/bin/env bash
# Ежедневная генерация слов по расписанию тем (для cron).
# Полный автоматический цикл: генерация → авто-принятие → push в Anki → Telegram
set -euo pipefail
cd /opt/data/projects/Anki

# 1. Generate + auto-accept + auto-push
OUTPUT=$(.venv/bin/python scripts/daily_topic.py 2>&1)
echo "$OUTPUT"

# 2. Отправка уведомления через n8n webhook → Telegram (минуя gateway)
MESSAGE=$(echo "$OUTPUT" | python3 -c "import sys,json; print(json.dumps({'text': sys.stdin.read().strip()}))")
N8N_WEBHOOK_URL="${N8N_WEBHOOK_URL:-http://localhost:5678/webhook/ankicards-notify}"
curl -s -X POST "$N8N_WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$MESSAGE" > /dev/null 2>&1 || echo "[daily_topic] WARNING: n8n webhook call failed"

echo "[daily_topic] Notification sent via n8n webhook"