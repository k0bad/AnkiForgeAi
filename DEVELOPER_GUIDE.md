# AnkiCards — Developer Guide

Полный справочник по архитектуре проекта для AI-агентов и контрибьюторов.  
Здесь описано **как всё устроено, как добавлять языки, как работает пайплайн, и как не сломать проект**.

---

## 1. Быстрый старт для AI-агента

```bash
# Установка (после клонирования репозитория)
uv pip install -e ".[dev]"

# CLI (команда — ankiforgeai, пакет/import-имя — ankicards)
ankiforgeai ingest topic "еда" --count 10 --level A2   # генерация слов
ankiforgeai ingest url https://...                       # извлечение из страницы
ankiforgeai review                                        # интерактивный ревью (только в TTY)
ankiforgeai push                                          # отправка в Anki
ankiforgeai sync                                          # синхронизация Anki → кэш
ankiforgeai stats                                         # статистика

# Тесты + линтеры
pytest tests/ -v
ruff check src/
ruff format src/
mypy src/

# Ежедневный cron (полный автоцикл)
python scripts/daily_topic.py --dry-run                 # что бы сгенерировало
python scripts/daily_topic.py                           # генерация + авто-принятие + push (БЕЗ Telegram)
./scripts/daily_topic.sh                                 # то же + уведомление в Telegram через n8n
python scripts/daily_topic.py --topic dyr --count 5     # другая тема вручную
```

---

## 2. Архитектура проекта

```
src/ankicards/
├── models.py          # Card, POS, Status, Decision — универсальные модели
├── config.py          # Config + LanguageConfig (YAML → Pydantic, lru_cache)
├── db.py              # SQLite (cards, audit_log, anki_cache), транзакции
├── pipeline.py        # Оркестратор: run_ingest_pipeline(), push_approved()
├── cli.py             # Typer CLI — все команды
├── llm.py             # LLM-клиент (OpenRouter/Anthropic), load_prompt()
├── dedupe.py          # Exact + fuzzy совпадения (rapidfuzz)
├── log.py             # structlog (JSON + human)
├── ingest/
│   ├── topic.py       # Генерация слов по теме через LLM
│   └── url.py         # Извлечение слов из URL (trafilatura + LLM)
├── enrich/
│   ├── grammar.py     # LLM заполняет грамматические формы
│   ├── translation.py # LLM переводит слово
│   ├── examples.py    # LLM генерирует примеры
│   └── pronunciation.py # Транскрипция
├── media/
│   ├── tts.py         # edge-tts → mp3
│   └── images.py      # Unsplash → jpg (только для существительных)
├── anki/
│   ├── connect.py     # HTTP-клиент к AnkiConnect API
│   ├── sync.py        # Синхронизация Anki → локальный кэш
│   └── notetype.py    # Note Type, шаблоны, рендеринг — МУЛЬТИЯЗЫЧНЫЙ
└── review/
    └── interactive.py # rich + questionary UI (только TTY)

scripts/
├── daily_topic.py     # Ежедневный автоцикл: generate → auto-accept → push → notify
├── daily_topic.sh     # Обёртка для cron: python + n8n webhook → Telegram
├── init_db.py         # Создать SQLite схему
├── setup_anki_notetype.py # Создать NorskCard/LanguageCard Note Type в Anki
└── run_images.py      # Batch-скачивание картинок для всех существительных

languages/{code}/
├── language.yaml      # Единственный источник истины для языка (см. секцию 4)
└── prompts/           # Языковые промпты (переопределяют prompts/)
```

---

## 3. Пайплайн: полный путь карточки

```
┌─────────────────────────────────────────────────────────────────┐
│ C R O N   0 7 * * *   (daily_topic.sh → daily_topic.py)         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. INGEST (topic/url)                                          │
│     ├─ load_prompt(topic_words.md) из languages/{code}/prompts/ │
│     ├─ LLM (OpenRouter/Anthropic) → list[Card]                  │
│     └─ Card(status=pending, word, pos, translation)             │
│                                                                 │
│  2. DEDUPE (check_card)                                         │
│     ├─ _exact_match → merge (score=100)                         │
│     ├─ _fuzzy_matches(threshold=cfg.dedupe.fuzzy_threshold_auto)│
│     │   ├─ score >= fuzzy_threshold_review → review             │
│     │   ├─ score in [auto, review)        → review (semiauto)   │
│     │   └─ no match → new (авто-добавление)                     │
│     └─ Decision(new | review | merge | skip)                    │
│                                                                 │
│  3. ENRICH (для accepted карточек)                              │
│     ├─ pronunciation.py (LLM: "произносится как...")            │
│     ├─ translation.py (если перевод не задан)                   │
│     ├─ grammar.py (LLM заполняет card.forms по схеме языка)     │
│     └─ examples.py (LLM: пример + перевод)                      │
│                                                                 │
│  4. MEDIA (для accepted карточек, если auto_media=True)          │
│     ├─ edge-tts → {card.id}_nb.mp3 в media/audio/               │
│     └─ Unsplash → {card.id}.jpg в media/images/ (только nouns)  │
│                                                                 │
│  5. DB SAVE → status=approved                                   │
│                                                                 │
│  6. AUTO-ACCEPT (daily_topic.py)                                │
│     └─ Все review-карточки → status=approved                    │
│                                                                 │
│  7. AUTO-PUSH (daily_topic.py)                                  │
│     ├─ AnkiConnect.ensure_deck() → createDeck                   │
│     ├─ AnkiConnect.store_media() → mp3/jpg в collection.media   │
│     ├─ AnkiConnect.add_note() → card_to_anki_fields()           │
│     └─ status=pushed, anki_note_id заполнен                     │
│                                                                 │
│  8. NOTIFY (daily_topic.sh → n8n webhook → Telegram)            │
│     └─ POST http://<n8n-host>:5678/webhook/ankicards-notify     │
│         → отформатированное сообщение → Telegram                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Статусы карточки

```
pending → approved (авто-принято) → pushed (в Anki)
        → review   (нужен человек) → approved → pushed
        → skipped  (отброшена)
        → merged   (дубликат — не добавляется)
```

### Конфиг (config.yaml) — ключевые параметры

```yaml
language: nb                    # активный языковой профиль → languages/{code}/
ui_language: ru                 # язык подписей бэк-стороны карточки: ru | en

dedupe:
  fuzzy_threshold_review: 85   # ≥ 85 → обязательный ревью
  fuzzy_threshold_auto: 82     # < 82 → авто-добавление; 82-84 → semiauto ревью

llm:
  provider: openrouter          # openrouter | anthropic
  model: deepseek/deepseek-v4-flash
  max_tokens: 4096
  temperature: 0.3

images:
  enabled: true
  only_for_pos: [noun]          # картинки только для существительных

review:
  mode: semiauto                # manual / semiauto / auto

enrich:
  grammar: true                 # включить/выключить отдельные стадии обогащения
  examples: true                # (настраивается в ankiforgeai setup)
  pronunciation: true
```

### Секреты (.env)

```
OPENROUTER_API_KEY=your-openrouter-key
ANTHROPIC_API_KEY=your-anthropic-key   # опционально
UNSPLASH_ACCESS_KEY=...        # для картинок
```

### База данных (SQLite — `data/ankicards.db`)

```
cards (id, word, pronunciation, translation, example, example_translation,
       pos, forms JSON, level, topic, source, image, audio, tags JSON,
       status, date_added, anki_note_id)

audit_log (id, timestamp, action, card_id, details JSON)
  └─ create / merge / skip / push / review_needed / auto_accept / ...

anki_cache (note_id, word, fields JSON, tags JSON, synced_at)
  └─ снимок заметок из Anki для быстрой дедупликации
```

---

## 4. Как добавить язык — ПОЛНАЯ ИНСТРУКЦИЯ

### Шаг 1: Создать `languages/{code}/language.yaml`

Образцы: [`languages/nb/language.yaml`](languages/nb/language.yaml) (норвежский)  
и [`languages/de/language.yaml`](languages/de/language.yaml) (немецкий).

```yaml
code: xx                     # ISO 639-1 код (fr, es, it, ja, …)
name: Language Name          # название на английском
article: true                # true если есть артикли (der/die/das, le/la)

pos_labels:                  # POS → название на ЦЕЛЕВОМ языке (для Anki-фронта)
  noun: ...
  verb: ...
  adj: ...
  adv: ...
  pron: ...
  prep: ...
  conj: ...
  interj: ...
  num: ...
  phrase: ...
  other: ...

forms:                       # схема грамматических полей ДЛЯ КАЖДОЙ POS
  noun:                      # ключи будут в card.forms как dict
    - {key: gender, label: "Род"}     # label — подпись в таблице на бэке
    - {key: article, label: "Артикль"}
    - …                       # поля зависят от грамматики языка
  verb:
    - {key: infinitive, label: "Инфинитив"}
    - …
  adj:
    - {key: positive, label: "Положительная степень"}
    - …

tts:                         # голоса edge-tts (https://github.com/rany2/edge-tts#supported-languages)
  voice_female: xx-XX-NameNeural
  voice_male: xx-XX-NameNeural
  default_voice: female

anki:
  deck_name: MyDeck          # имя колоды в Anki
  note_type: LanguageCard     # универсальное имя Note Type (не менять!)

back_labels:                 # переводы label'ов на ЯЗЫК ПОЛЬЗОВАТЕЛЯ (бэк-сторона карточки)
  translation: "Перевод"
  part_of_speech: "Часть речи"
  grammar: "Грамматика"
  example: "Пример"
  example_translation: "Перевод примера"
  pronunciation: "Транскрипция"
  level: "Уровень"
  topic: "Тема"
```

**Важно:** `forms.{pos}[].key` — это имена полей, которые LLM должен заполнить в `card.forms` (dict).  
Эти ключи передаются в промпт `grammar_forms.md` через `{form_keys}` (см. enrich/grammar.py).

### Шаг 2: Создать промпты `languages/{code}/prompts/`

Скопировать и адаптировать:

```bash
cp prompts/*.md languages/{code}/prompts/
```

Минимальный набор промптов:

| Файл | Назначение | Что адаптировать |
|------|-----------|-----------------|
| `topic_words.md` | Генерация слов по теме | Контекст языка, примеры слов, грамматические особенности |
| `grammar_forms.md` | Заполнение грамматических форм | Формат вывода (под схему из language.yaml.forms) |
| `example_sentence.md` | Генерация примера | Естественный контекст для языка |
| `url_extract.md` | Извлечение слов из URL | Язык страниц |
| `russian_pronunciation.md` | Транскрипция | Особенности произношения языка |
| `translation.md` | Перевод (fallback, если ingest не задал `translation`) | Название языка в тексте промпта |

### Шаг 3: Проверить

```bash
# Загрузить язык
python -c "from ankicards.config import get_language; print(get_language('xx').name)"

# Сгенерировать тестовые слова (без push)
python scripts/daily_topic.py --topic test --count 3 --no-push

# Проверить тесты
pytest tests/test_language.py -v
```

### Грамматические особенности по языкам

| Язык | Существительные | Глаголы | Прилагательные |
|------|----------------|---------|---------------|
| Норвежский (nb) | род (m/f/n) + 4 формы числа/определённости | 4 времени | 3 формы + сравнение |
| Немецкий (de) | род + 4 падежа + мн.ч | 5 форм лица + 2 времени | 3 степени |
| Английский (en) | ед./мн. число | 5 форм (base/past/participle/-ing/3rd person) | positive/comparative/superlative |
| Испанский (es) | род + число | 12 форм (5 лиц × наклонения/времена + причастие/герундий) | 4 формы (m/f × ед./мн.) |
| Французский* | род (m/f) + число | 6 форм лица + времена | род + число + сравнение |
| Японский* | нет рода/числа | вежливые/простые формы | い/な прилагательные |

*\*Ещё не реализовано — можно добавить через `language.yaml` без правки кода.*

---

## 5. Как работает доставка в Telegram

```
Cron (Hermes) → daily_topic.sh
  ├─ 1. python scripts/daily_topic.py → генерация + авто-принятие + push
  ├─ 2. curl POST → n8n webhook (`$N8N_WEBHOOK_URL` или `http://<n8n-host>:5678/webhook/ankicards-notify`)
  └─ 3. n8n workflow "AnkiCards Send Notification"
       ├─ Webhook → Code: Prepare Message → HTTP Request (Telegram Bot API)
       ├─ chat_id + message_thread_id (настроить в n8n workflow)
       └─ parse_mode: Markdown (поддерживает **bold**, `code`)
```

**Почему n8n, а не Hermes gateway:** gateway сейчас нестабилен (конфликт двух профилей).  
n8n шлёт напрямую через Telegram Bot API — надёжнее, как weather-workflow.

---

## 6. База данных: SQLite schema

```sql
CREATE TABLE cards (
    id              TEXT PRIMARY KEY,        -- UUID
    word            TEXT NOT NULL,           -- основная форма (lemma)
    pronunciation   TEXT,                    -- транскрипция
    translation     TEXT NOT NULL,           -- перевод
    example         TEXT,                    -- пример на целевом языке
    example_translation TEXT,                -- перевод примера
    pos             TEXT NOT NULL,           -- noun/verb/adj/adv/pron/prep/conj/interj/num/phrase/other
    forms           TEXT,                    -- JSON: {gender: "m", indefinite_singular: "en bil", ...}
    level           TEXT,                    -- a1/a2/b1/b2/c1/c2 (CEFR)
    topic           TEXT,                    -- mat, klær, dyr, reise...
    source          TEXT,                    -- nrk, manual, topic-gen, url:...
    image           TEXT,                    -- имя файла (не путь!) напр. {id}.jpg
    audio           TEXT,                    -- имя файла (не путь!) напр. {id}_nb.mp3
    tags            TEXT,                    -- JSON list: ["topic::mat", "level::a2"]
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending/review/approved/pushed/skipped/suspended
    date_added      TEXT NOT NULL,           -- ISO date
    anki_note_id    INTEGER                  -- ID заметки в Anki (заполняется после push)
);

CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,               -- UTC ISO
    action      TEXT NOT NULL,               -- create/merge/skip/push/review_needed/auto_accept/...
    card_id     TEXT,                        -- FK → cards.id (может быть NULL)
    details     TEXT                         -- JSON с деталями операции
);

CREATE TABLE anki_cache (
    note_id     INTEGER PRIMARY KEY,         -- Anki note ID
    word        TEXT NOT NULL,
    fields      TEXT NOT NULL,               -- JSON всех полей
    tags        TEXT,                        -- JSON list
    synced_at   TEXT NOT NULL                -- время последней синхронизации
);
```

---

## 7. Инварианты (что НИКОГДА не делать)

1. **Не читать `.env`** — секреты (API-ключи). Для проверки `get_secrets()` уже делает это безопасно.
2. **Не менять файлы в `.venv/`** — виртуальное окружение.
3. **Не хардкодить язык** — всё через `get_language()` + `language.yaml`.
4. **Не хранить пути к файлам в БД** — только имена файлов (`card.image = "abc.jpg"`, не `"media/images/abc.jpg"`).
5. **Все операции с БД — в транзакциях** через `db.connect()`.
6. **Имена медиа-файлов детерминированы:** `{card.id}_nb.mp3`, `{card.id}.jpg`.
7. **Source of truth = Anki**, не SQLite. SQLite — staging + audit + кэш.
8. **Мультиязычный** — целевой язык задаётся `language: <code>` в `config.yaml` (встроены `nb`/`de`/`en`/`es`). Nynorsk можно добавить как отдельный `language.yaml` (`nn`).

---

## 8. Что ломается и как чинить

| Симптом | Причина | Фикс |
|---------|---------|------|
| `ankiforgeai push` → ConnectTimeout | Комп выключен / Anki не запущен / Anki недоступен по сети (Tailscale и т.п.) | Включить комп, открыть Anki, проверить `curl <anki.url из config.yaml>` |
| Cron `exit code 1` | LLM API недоступен / n8n не отвечает | Проверить `curl <n8n-host>:5678/healthz` |
| `review` не работает в моём терминале | `questionary` требует TTY | Запустить `ankiforgeai review` в интерактивном терминале пользователя |
| Уведомление в Telegram не пришло | n8n workflow упал | Проверить executions в интерфейсе n8n для нужного workflow |
| `get_language('xx')` → FileNotFoundError | Нет `languages/xx/language.yaml` | Создать по образцу |
| `load_prompt` не находит промпт в языке | Промпт не скопирован в `languages/{code}/prompts/` | `cp prompts/*.md languages/{code}/prompts/` |
| Жирный текст не работает в Telegram | parse_mode: Markdown не совместим с некоторыми символами | Использовать HTML-теги или экранировать спецсимволы |
| 8 из 10 слов уходят в review | Порог `fuzzy_threshold_auto` слишком низкий | Поднять в `config.yaml` (рекомендуется 82) |
| Картинки не генерируются | `only_for_pos: [noun]` — только для существительных | Проверить POS карточки |

---

## 9. Полезные команды для диагностики

```bash
# Проверить БД — сколько карточек в каждом статусе
sqlite3 data/ankicards.db "SELECT status, COUNT(*) FROM cards GROUP BY status"

# Последние карточки
sqlite3 data/ankicards.db "SELECT word, topic, status, date_added FROM cards ORDER BY date_added DESC LIMIT 10"

# Запустить тесты
pytest tests/ -v

# Проверить кодстайл
ruff check src/ && mypy src/

# Проверить, доступен ли Anki (URL — из config.yaml → anki.url, по умолчанию локальный)
curl -m 5 http://127.0.0.1:8765 -d '{"action":"version","version":6}'

# Проверить конфиг языка
python -c "from ankicards.config import get_language; l=get_language('nb'); print(l.name, l.code)"
python -c "from ankicards.config import get_language; l=get_language('de'); print(l.forms['noun'])"

# Проверить n8n webhook (URL — из $N8N_WEBHOOK_URL, см. scripts/daily_topic.sh)
curl -X POST http://localhost:5678/webhook/ankicards-notify -H 'Content-Type: application/json' -d '{"text":"test"}'
```

---

## 10. План на будущее

- [x] Добавить `language: <code>` в `config.yaml`, прокинуть во все `get_language()` (было: всегда дефолт `nb`)
- [x] Выбор языка интерактивно — `ankiforgeai setup` (вместо предполагавшегося `ankicards config set language.de`)
- [ ] Поддержка `parse_mode: MarkdownV2` с экранированием спецсимволов
- [ ] Nynorsk как отдельный `language.yaml` (`nn`)
- [x] CI/CD (GitHub Actions: ruff + mypy + pytest)
- [ ] Публикация в PyPI
- [x] LICENSE (MIT)
- [x] CHANGELOG
- [ ] Конфигурируемый Note Type (сейчас жёстко `LanguageCard` с 12 полями)
- [ ] Pluggable-слой уведомлений (не только Telegram/n8n) — см. [issue #2](https://github.com/k0bad/AnkiForgeAi/issues/2)
- [ ] Выбор способа транскрипции: практическая (кириллица) vs IPA — см. [issue #4](https://github.com/k0bad/AnkiForgeAi/issues/4)