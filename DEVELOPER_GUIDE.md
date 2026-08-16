# AnkiForgeAI — Developer Guide

Полный справочник по архитектуре проекта для AI-агентов и контрибьюторов.  
Здесь описано **как всё устроено, как добавлять языки, как работает пайплайн, и как не сломать проект**.

---

## 1. Быстрый старт для AI-агента

```bash
# Установка
cd path/to/AnkiForgeAI
uv pip install -e ".[dev]"

# CLI (бинарь называется ankiforgeai, import-пакет — ankicards)
ankiforgeai ingest topic "еда" --count 10 --level A2   # генерация слов
ankiforgeai ingest url https://...                       # извлечение из страницы
ankiforgeai review                                        # интерактивный ревью (только в TTY)
ankiforgeai push                                          # отправка в Anki
ankiforgeai sync                                          # синхронизация Anki → кэш
ankiforgeai stats                                         # статистика
ankiforgeai doctor                                        # сверка enrich/images-тумблеров с данными карточек
ankiforgeai delete <id> [<id> ...]                        # безвозвратное удаление, номер освобождается
ankiforgeai migrate-ids                                   # разовая миграция UUID → последовательные ID

# Тесты + линтеры
pytest tests/ -v
ruff check src/
ruff format src/
mypy src/

# Ежедневный cron (полный автоцикл)
python scripts/daily_topic.py --dry-run                 # что бы сгенерировало
python scripts/daily_topic.py                           # генерация + AI-дедуп + push (без уведомления)
python scripts/daily_topic.py --notify                  # то же + уведомление по каналам из config.yaml
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
├── doctor.py          # find_inconsistencies() — enrich/images тумблеры vs данные карточек
├── migrate_ids.py      # разовая миграция card.id UUID → последовательные int (ankiforgeai migrate-ids)
├── setup_wizard.py     # интерактивный мастер `ankiforgeai setup` → пишет config.yaml
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
│   └── images.py      # Поиск картинок (unsplash/pexels/pixabay/openverse) → jpg (nouns only)
├── anki/
│   ├── connect.py     # HTTP-клиент к AnkiConnect API
│   ├── sync.py        # Синхронизация Anki → локальный кэш
│   └── notetype.py    # Note Type, шаблоны, рендеринг — МУЛЬТИЯЗЫЧНЫЙ
├── notify/
│   ├── base.py         # Notifier protocol
│   ├── webhook.py       # generic POST JSON бэкенд (n8n/Hermes/Zapier/…), format: text|json
│   └── __init__.py      # dispatch() — фан-аут по cfg.notifications, канал за каналом
└── review/
    ├── interactive.py # rich + questionary UI (только TTY)
    └── actions.py      # non-interactive accept/skip/suspend/resume/edit/delete (для AI-агента)

scripts/
├── daily_topic.py     # Ежедневный автоцикл: generate → dedupe (AI-адъюдикация) → push → notify
├── daily_topic.sh     # Тонкая обёртка для cron: python scripts/daily_topic.py --notify
└── run_images.py      # Batch-скачивание картинок для всех существительных

(БД и Anki Note Type создаёт `ankiforgeai init` — см. cli.py; отдельных
скриптов init_db.py / setup_anki_notetype.py больше нет, они были легаси-дублями.)

languages/{code}/
├── language.yaml      # Единственный источник истины для языка (см. секцию 4)
└── prompts/           # Языковые промпты (переопределяют prompts/)

.claude/skills/ankiforgeai/
└── SKILL.md            # Контракт для AI-агента: --json/review по id вместо TTY-промптов
                         # (в git, но НЕ публикуется на PyPI; `ankiforgeai setup` синхронизирует
                         # её trigger-phrase примеры под выбранный ui_language, см. §10)
```

Проект — одновременно обычный PyPI CLI-пакет (`ankiforgeai`, что видит `pip install`) и Claude
Code Agent Skill в том же репозитории. Второе не упаковывается и не публикуется — это git-only
слой поверх того же CLI для AI-агента, описанный в SKILL.md выше и в «Primary workflow» CLAUDE.md.

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
│  2. DEDUPE (check_card + judge_review)                          │
│     ├─ _exact_match → merge (score=100)                         │
│     ├─ _fuzzy_matches(threshold=cfg.dedupe.fuzzy_threshold_auto)│
│     │   ├─ score >= fuzzy_threshold_review → review             │
│     │   ├─ score in [auto, review)        → review (semiauto)   │
│     │   └─ no match → new (авто-добавление)                     │
│     ├─ judge_review(): для decision=review LLM решает,          │
│     │   правда ли это дубликат (prompts/dedupe_judge.md)        │
│     │   SAME → merge · DIFFERENT → new · UNSURE/ошибка → review │
│     │   (если cfg.dedupe.ai_adjudication выключен — пропускается)│
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
│     └─ images.provider → {card.id}.jpg в media/images/ (nouns)  │
│                                                                 │
│  5. DB SAVE → status=approved                                   │
│                                                                 │
│  6. REVIEW QUEUE (daily_topic.py)                               │
│     └─ Что осталось в status=review (шаг 2 не смог разрешить) — │
│        НЕ трогается, только считается → report["needs_review"]  │
│        разобрать вручную: ankiforgeai review                    │
│                                                                 │
│  7. AUTO-PUSH (daily_topic.py)                                  │
│     ├─ AnkiConnect.ensure_deck() → createDeck                   │
│     ├─ AnkiConnect.store_media() → mp3/jpg в collection.media   │
│     ├─ AnkiConnect.add_note() → card_to_anki_fields()           │
│     └─ status=pushed, anki_note_id заполнен                     │
│                                                                 │
│  8. NOTIFY (daily_topic.py --notify → ankicards.notify.dispatch)│
│     └─ по каждому включённому cfg.notifications:                │
│         webhook → POST {"text": ...} на настроенный URL         │
│         (сейчас: n8n → Telegram, http://.../webhook/…-notify)   │
│         ошибка одного канала не блокирует остальные             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Статусы карточки

```
pending → approved (dedupe уверен / AI решил DIFFERENT) → pushed (в Anki)
        → review   (AI не уверен или выключен) → approved (вручную, ankiforgeai review) → pushed
        → skipped  (отброшена)
        → merged   (дубликат — не добавляется)
```

### Конфиг (config.yaml) — ключевые параметры

`config.yaml` — локальный файл, в `.gitignore` (как `.env`). В репозитории лежит только
`config.yaml.example` (шаблон с дефолтами). Первичная настройка: `cp config.yaml.example
config.yaml`, затем отредактировать под себя (`ankiforgeai setup` делает то же самое
интерактивно). `git pull` больше не трогает `config.yaml` — реальные Anki URL, webhook и пороги
dedupe не перезаписываются обновлениями репозитория. Если правишь дефолты для всех — правь
`config.yaml.example`, а не `config.yaml`.

```yaml
transcription: practical       # practical (кириллица) | ipa (Международный фонетический алфавит)

dedupe:
  fuzzy_threshold_review: 85   # ≥ 85 → обязательный ревью (если AI тоже не разрешит)
  fuzzy_threshold_auto: 82     # < 82 → авто-добавление; 82-84 → semiauto ревью
  ai_adjudication: true        # LLM решает SAME/DIFFERENT для review-кандидатов
                                # (см. dedupe.judge_review); false = как раньше, всегда на человека
  judge_model: ""              # своя (дешёвая/быстрая) модель для judge_review;
                                # пусто = llm.model. Провайдер (llm.provider) общий.

llm:
  provider: openrouter          # openrouter | anthropic
  model: deepseek/deepseek-v4-flash
  max_tokens: 4096
  temperature: 0.3

images:
  enabled: true
  provider: unsplash             # unsplash | pexels | pixabay | openverse
  only_for_pos: [noun]          # картинки только для существительных

review:
  mode: semiauto                # manual / semiauto / auto
```

### Секреты (.env)

```
OPENROUTER_API_KEY=your-openrouter-key
ANTHROPIC_API_KEY=your-anthropic-key   # опционально
# Картинки — нужен только ключ провайдера, выбранного в images.provider:
UNSPLASH_ACCESS_KEY=...        # provider: unsplash (default)
PEXELS_API_KEY=...             # provider: pexels
PIXABAY_API_KEY=...            # provider: pixabay
# provider: openverse — ключ не нужен
```

### База данных (SQLite — `data/ankicards.db`)

```
cards (id, word, pronunciation, translation, example, example_translation,
       pos, forms JSON, level, topic, source, image, audio, tags JSON,
       status, date_added, anki_note_id)

audit_log (id, timestamp, action, card_id, details JSON)
  └─ create / skip_duplicate / review_needed / review_accept / review_edit /
     review_skip / review_suspend / push / push_failed / enrich_failed / ...

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
| `russian_pronunciation.md` | Транскрипция (practical, кириллица) | Особенности произношения языка |
| `ipa_pronunciation.md` | Транскрипция (IPA) | Правила IPA-транскрипции для языка (см. `config.transcription`) |

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
| Английский (en) | только число (singular/plural) | 5 форм (base/past/participle/-ing/3rd person) | 3 степени сравнения |
| Испанский (es) | род + число | 12 форм (5 лиц наст. вр. + претерит + имперфект + futuro + participio + gerundio) | 4 формы (род × число) |
| Французский* | род (m/f) + число | 6 форм лица + времена | род + число + сравнение |
| Японский* | нет рода/числа | вежливые/простые формы | い/な прилагательные |

nb/de/en/es — реализованные профили (`languages/{code}/`). \*Французский и японский — гипотетические
примеры того, что можно добавить через `language.yaml` без правки кода, языковых профилей для них пока нет.

---

## 5. Как работает доставка уведомлений (webhook → Telegram и др.)

Уведомления — pluggable слой `src/ankicards/notify/` (см. также `notify/webhook.py`):
каналы описаны в `config.yaml -> notifications:`, каждый — `{type, enabled, url, format}`.
Единственный бэкенд пока — `webhook` (generic POST JSON, покрывает n8n/Hermes/Zapier/
любой свой шлюз-посредник). `format` управляет тем, что летит в теле запроса:
- `text` (default) — готовое Telegram-flavored сообщение `{"text": "..."}`, под текущий
  n8n workflow (parse_mode: Markdown).
- `json` — сырой structured `report` целиком, без форматирования под конкретный
  мессенджер — для посредника со своей маршрутизацией по каналам (Hermes и т.п.:
  сам решает, Telegram это, другая соцсеть или AI-агент).

Ошибка одного канала логируется и не мешает остальным (`dispatch()` в `notify/__init__.py`).

```
Cron (Hermes) → daily_topic.sh
  ├─ 1. python daily_topic.py --notify → генерация + AI-дедуп + push
  ├─ 2. ankicards.notify.dispatch(report, cfg) → для каждого enabled-канала:
  │      webhook.WebhookNotifier.send() → POST payload на cfg.notifications[i].url
  │      payload = {"text": format_report(report)} если format=text (n8n)
  │      payload = report                          если format=json (Hermes и т.п.)
  └─ 3a. n8n workflow "AnkiCards Send Notification" (format=text канал)
        ├─ Webhook → Code: Prepare Message → HTTP Request (Telegram Bot API)
        ├─ chat_id + message_thread_id (настроить в n8n workflow)
        └─ parse_mode: Markdown (поддерживает **bold**, `code`)
     3b. Hermes/другой шлюз (format=json канал, если настроен) — сам решает
        маршрутизацию по структурированным полям report
```

Добавить канал (Slack/Discord/…) — новый файл `notify/<name>.py` с классом,
у которого есть `async def send(self, report: dict) -> None`, плюс запись в
`_BACKENDS` в `notify/__init__.py`. Правка `daily_topic.py`/`.sh` не нужна.

**Почему n8n, а не Hermes gateway:** gateway сейчас нестабилен (конфликт двух профилей).  
n8n шлёт напрямую через Telegram Bot API — надёжнее, как weather-workflow.

---

## 6. База данных: SQLite schema

```sql
CREATE TABLE cards (
    id              INTEGER PRIMARY KEY,     -- последовательный, переиспользуемый номер
                                              -- (был UUID до migrate_ids.py, см. §10)
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
    action      TEXT NOT NULL,               -- create/skip_duplicate/push/review_needed/review_accept/...
    card_id     INTEGER,                     -- FK → cards.id (может быть NULL)
    details     TEXT,                        -- JSON с деталями операции
    run_id      TEXT                         -- FK к structlog run_id того же вызова (см. ниже)
);

CREATE TABLE anki_cache (
    note_id     INTEGER PRIMARY KEY,         -- Anki note ID
    word        TEXT NOT NULL,
    fields      TEXT NOT NULL,               -- JSON всех полей
    tags        TEXT,                        -- JSON list
    synced_at   TEXT NOT NULL                -- время последней синхронизации
);
```

### Два уровня логирования: structlog vs audit_log

В проекте два независимых механизма логирования, с разной зоной ответственности:

| | `structlog` (`log.py`) | `audit_log` (`db.py`) |
|---|---|---|
| Что | Операционная трасса одного запуска CLI | Персистентная история фактов по карточке |
| Живёт | Пока не улетит в stderr/JSON — не хранится | Вечно, в SQLite, между запусками |
| Гранулярность | Событие (`stage.start`, retry, parse-ошибка, ...) | Факт с `card_id` (create/push/review_skip/...) |
| Как получить | `get_logger(__name__)` + `.info/.warning/.error(...)` | `db.log_action(action, card_id, details)` |
| Читает кто | Оператор/монитор в моменте (или лог-агрегатор) | `doctor`, `review` (последний `review_needed`), сам аудит |

Оба источника связаны через **`run_id`**: `bound_run(command)` в `cli.py` привязывает `run_id` + имя команды к structlog-контексту (`contextvars`) на время одного CLI-вызова; `db.log_action()` читает тот же `run_id` из контекста и пишет его в колонку `audit_log.run_id`. Так можно по одному `run_id` восстановить и live-трассу конкретного запуска, и все карточные факты, которые он произвёл.

Кто чем пишет — по слоям:

1. **Оркестрация с решениями по карточке** (`pipeline.py`, `review/actions.py`, `review/interactive.py`) — пишет **и туда, и туда** через `pipeline._record(db, level, action, card_id, **details)`: один вызов, два места записи, названия события в structlog и `action` в `audit_log` совпадают (`enrich_failed`, `audio_generated`, `push`, `review_skip`, ...), чтобы не пришлось искать соответствие. Само решение — "это facts достойный аудита" — принимается здесь, а не в низкоуровневых модулях ниже.
   Отдельно, для событий без `card_id` (прогресс, а не факт по карточке) — голый `logger.info` с точечным именем: `stage.start`/`stage.done` вокруг каждой enrichment-стадии в `enrich_and_generate_media()`.
2. **Низкоуровневые модули** (`dedupe.py`, `llm.py`, `_net.py`, `anki/connect.py`, `media/tts.py`, `media/images.py`, `notify/`) — только `structlog`, без `audit_log`: retry-попытки, сетевые ошибки, разбор ответа LLM. Эти модули не знают, к какой карточке (если вообще к какой-то) относится вызов и является ли сбой окончательным — это решает вызывающий код в оркестрации (слой 1), который уже видит `card_id` и итоговый статус.
3. **Разовые операции без карточки** (`anki/notetype.py` — создание/синхронизация Note Type при `init`) — только `structlog`, без `audit_log`: писать некуда (`card_id` нет), а сам факт нужен только как трасса запуска `init`, для которой достаточно `run_id`.
4. **Бэкстоп на непредвиденное** — `bound_run()` перехватывает любое необработанное исключение внутри `with bound_run(...):`, логирует `command.failed` с полным traceback и перевыбрасывает. Это не замена точечному логированию в перечисленных выше слоях, а гарантия, что вообще никакой сбой не пройдёт совсем без следа.

Итог по review-действиям (issue #31): `audit_log` был и остаётся источником истины по карточным решениям (`review_accept`/`review_skip`/`review_suspend`/`review_resume`/`review_edit`/`review_finalized`) — но раньше писался напрямую через `db.log_action()`, без парного structlog-события. С #31 `review/actions.py` и `review/interactive.py` переиспользуют тот же `pipeline._record()`, что и остальная оркестрация (слой 1 выше), так что review-сессии (включая безтерминальные `review accept`/`skip`/... для управления из AI-агента) теперь видны и в live-трассе, а не только постфактум через SQL-запрос к `audit_log`.

---

## 7. Инварианты (что НИКОГДА не делать)

1. **Не читать `.env`** — секреты (API-ключи). Для проверки `get_secrets()` уже делает это безопасно.
2. **Не менять файлы в `.venv/`** — виртуальное окружение.
3. **Не хардкодить язык** — всё через `get_language()` + `language.yaml`.
4. **Не хранить пути к файлам в БД** — только имена файлов (`card.image = "abc.jpg"`, не `"media/images/abc.jpg"`).
5. **Все операции с БД — в транзакциях** через `db.connect()`.
6. **Имена медиа-файлов детерминированы:** `{card.id}_nb.mp3`, `{card.id}.jpg`.
7. **Source of truth = Anki**, не SQLite. SQLite — staging + audit + кэш.
8. **Норвежский — только Bokmål (`nb`)**. Остальные языковые профили (`de`/`en`/`es`) полностью
   реализованы и независимы друг от друга; Nynorsk можно добавить так же — отдельным `language.yaml`
   с кодом `nn`.

---

## 8. Что ломается и как чинить

| Симптом | Причина | Фикс |
|---------|---------|------|
| `ankiforgeai push` → ConnectTimeout | Комп выключен / Anki не запущен / Tailscale не подключён | Включить комп, открыть Anki, проверить `curl http://<tailscale-anki-ip>:8765` |
| Cron `exit code 1` | LLM API недоступен / n8n не отвечает | Проверить `curl http://<n8n-host>:5678/healthz` |
| `review` не работает в моём терминале | `questionary` требует TTY | Запустить `ankiforgeai review` в интерактивном терминале пользователя |
| Уведомление в Telegram не пришло | n8n workflow упал | Проверить executions: `curl http://<n8n-host>:5678/api/v1/executions?workflowId=<workflow-id>` |
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

# Проверить, доступен ли Anki
curl -m 5 http://<tailscale-anki-ip>:8765 -d '{"action":"version","version":6}'

# Проверить конфиг языка
python -c "from ankicards.config import get_language; l=get_language('nb'); print(l.name, l.code)"
python -c "from ankicards.config import get_language; l=get_language('de'); print(l.forms['noun'])"

# Проверить n8n webhook
curl -X POST http://<n8n-host>:5678/webhook/ankicards-notify -H 'Content-Type: application/json' -d '{"text":"test"}'
```

---

## 10. План на будущее

- [x] Добавить `language: nb` в `config.yaml` (сейчас — дефолт `get_language()`)
- [x] Языковые профили `de`/`en`/`es` в дополнение к `nb` (все четыре — полные, `languages/{code}/`)
- [ ] `ankiforgeai config set language.de` (CLI-команда для смены языка без ручного редактирования `config.yaml`)
- [x] AI-адъюдикация дедупа (`dedupe.ai_adjudication`, `dedupe.judge_review`) — LLM решает SAME/DIFFERENT
      для нечётких совпадений вместо автоматического ухода в ревью
- [x] Pluggable-каналы уведомлений (`notify/`, generic webhook: n8n/Zapier/Hermes/…)
- [x] Fallback-цепочка провайдеров картинок (`images.fallback_providers`)
- [x] Интерактивный выбор картинки в `review` вместо автовыбора первого результата (issue #36)
- [x] Последовательные переиспользуемые `card.id` вместо UUID + команда `delete` (см. §6, `migrate_ids.py`)
- [ ] Поддержка `parse_mode: MarkdownV2` с экранированием спецсимволов (`notify/webhook.py` сейчас рендерит под legacy `parse_mode: Markdown`)
- [ ] Nynorsk как отдельный `language.yaml` (`nn`)
- [x] CI/CD (GitHub Actions: ruff + mypy + pytest)
- [ ] Публикация в PyPI — пакет/workflow готовы (см. §12), не хватает только разовой ручной регистрации trusted publisher на pypi.org и первого `git push origin vX.Y.Z`
- [x] LICENSE (MIT)
- [x] CHANGELOG
- [x] Конфигурируемый Note Type — набор полей теперь декларативен (`anki.fields` в `language.yaml`), см. пример ниже. Без переопределения используется `DEFAULT_FIELDS` (те же 12 полей, что и раньше).

### Декларативная схема полей Note Type (`anki.fields`)

`language.yaml -> anki.fields:` — список полей Note Type. Каждое поле ссылается на
источник данных через `source` (реестр `FIELD_RESOLVERS` в `anki/notetype.py`) и
задаёт своё место на карточке через `slot`:

- `front_title` / `front_audio` / `front_image` — фронт карточки (максимум одно поле на слот)
- `section` — подписанный блок на бэке (порядок = порядок полей в списке)
- `tag` — пилюля в блоке тегов на бэке
- `hidden` — не рендерится нигде (например `ID`, для поиска/дедупликации)

```yaml
anki:
  deck_name: MyDeck
  note_type: LanguageCard
  fields:
    - {name: Word, source: word, slot: front_title}
    - {name: Translation, source: translation, slot: section, label_key: translation}
    - {name: Audio, source: audio_html, slot: front_audio}
```

Доступные `source`: `word`, `pronunciation`, `translation`, `example`,
`example_translation`, `pos_label`, `forms_html`, `image_html`, `audio_html`,
`level`, `topic`, `id`. Кастомное поле может ссылаться только на уже существующий
источник — новая сущность (например «Synonyms») требует нового атрибута `Card` и
отдельного этапа enrichment, это отдельная задача.

Опечатка в `source` или два поля с одним и тем же уникальным слотом (`front_title`
и т.п.) ловятся сразу при `ankiforgeai init` (`NoteTypeConfigError`), а не посреди
enrichment. **Важно:** `init` создаёт Note Type только один раз — если он уже
существует в Anki, изменение `anki.fields` не добавит/не уберёт поля в уже
созданном Note Type, пока его не пересоздать вручную в Anki.

### ⚠️ Смена дизайна карточек: что обновится, а что нет

Поведение `ankiforgeai init` при уже существующем Note Type:

| Что менялось | Обновится через `init`? | Как применить |
|--------------|------------------------|----------------|
| CSS / шаблоны `front`/`back` (`anki/notetype.py`) | ✅ Да — `updateModelTemplates` + `updateModelStyling` | просто `ankiforgeai init` |
| Схема полей (`anki.fields` в `language.yaml`) | ❌ Нет — поля создаются только при первом `init` | вручную в Anki или пересоздать Note Type |

**Если поменялись только CSS/шаблоны** (визуальный стиль): запусти `ankiforgeai init`
с открытым Anki — дизайн синхронизируется без потери прогресса. Это безопасно и
повторяемо.

**Если поменялась схема полей** (добавили/удалили поле): `init` её не тронет, потому
что `createModel` — разовая операция, а апдейта полей AnkiConnect не даёт. Два пути:

**Вариант 1 — добавить/убрать поле вручную без потери прогресса.**
В Anki: Tools → Manage Note Types → `LanguageCard` → Fields… → добавить/удалить поле.
Прогресс повторений сохраняется. Минус — ручная работа.

**Вариант 2 — удалить Note Type и пересоздать (проще, но с потерей).**
Удалить Note Type/колоду в Anki → `ankiforgeai init` (создаст с новой схемой) →
`ankiforgeai push` (пересоздаст карточки). **SRS-прогресс (интервалы, история
повторений) пропадёт** — Anki будет считать карточки новыми.

**Как понять, что дизайн не применился:** на карточке в Anki отсутствуют поля,
секции или стили, которые есть в `anki/notetype.py` / `anki.fields`. Если после
`init`+`push` карточки выглядят «голыми» — значит менялась именно схема полей,
нужен один из вариантов выше.

---

## 11. Обновление репозитория на других машинах (git pull)

**В общем случае обычный `git pull` безопасен.** `config.yaml` — локальный файл (`.gitignore`,
см. §3), `git pull` его больше не трогает вообще: новые поля (`transcription`, `notifications`,
`images.provider`, ...) и поля в `.env` (`PEXELS_API_KEY`, `PIXABAY_API_KEY`, ...) всегда получают
дефолты в Pydantic-моделях (`config.py`) — старый `config.yaml`/`.env` без этих полей продолжит
работать как раньше, просто без новых фич, пока их не включат вручную. Схему БД (`db.py`) и Anki
note type (`anki/notetype.py`) эти апдейты не трогали — повторно запускать `ankiforgeai init` не
нужно, если только `DEVELOPER_GUIDE.md` явно не скажет обратное для конкретного изменения.

**Разовая миграция для чекаутов, сделанных до этого изменения (issue #52):** раньше `config.yaml`
был отслеживаемым файлом, поэтому `git pull`/`reset --hard` мог перезаписать реальные настройки
(Anki URL, webhook, пороги dedupe) дефолтами из репозитория — либо, при локальных незакоммиченных
правках, git мог применить rename `config.yaml → config.yaml.example` и унести реальные значения
в файл, который снова окажется отслеживаемым. После обновления один раз:

```bash
git status                          # если config.yaml.example содержит твои реальные значения —
                                     # значит именно это и произошло
cp config.yaml.example config.yaml  # вернуть реальные настройки в теперь-локальный файл
git checkout HEAD -- config.yaml.example  # откатить example обратно к чистому шаблону из репо
```

Дальше `config.yaml` в `.gitignore` и `git pull` его больше не задевает.

**Разовое исключение:** история ветки `dev` была переписана и force-push'нута 2026-08-11 (из
авторов коммитов удалён реальный email). Любой клон/чекаут `dev`, сделанный **до** этой даты, не
сможет сделать обычный `git pull` — история разошлась с корня, будет `non-fast-forward` или
задвоенный лог. На такой машине (например, крон-сервер) один раз нужно:

```bash
git fetch origin
git status                      # убедиться, что нет незакоммиченных правок в отслеживаемых файлах
git reset --hard origin/dev     # переключиться на переписанную историю
```

`.env` в `.gitignore`, `reset --hard` его не тронет. Если на машине есть локальные незакоммиченные
изменения в отслеживаемых файлах — сначала `git stash -u` (или закоммитить их отдельно) перед
`reset --hard`, иначе они потеряются.

---

## 12. Публикация в PyPI

Пакет называется `ankiforgeai` на PyPI (import-имя внутри остаётся `ankicards` — историческое,
менять не стали, чтобы не переписывать все импорты). `languages/` и `prompts/` вшиты в wheel
(`tool.hatch.build.targets.wheel.force-include` в `pyproject.toml`); `config.py` определяет
`PROJECT_ROOT`/`languages_dir()` с фоллбэком на данные, вшитые в пакет, если рядом с кодом нет
папки `languages/` — то есть при обычном dev-чекауте ничего не меняется, а при `pip install`
пакет сам находит свои языковые профили и промпты вместо репо-путей.

Проверить локально перед релизом:

```bash
rm -rf dist/
uv build
uv tool run --from twine twine check dist/*
```

### Разовая настройка (сделать один раз, руками, на pypi.org)

Публикация идёт через **PyPI Trusted Publishing (OIDC)** — GitHub Actions публикует пакет без
хранения токена в репозитории. Перед первым релизом:

1. Завести аккаунт на [pypi.org](https://pypi.org) (если его ещё нет).
2. В настройках аккаунта → **Publishing** → **Add a new pending publisher**, указать:
   - PyPI Project Name: `ankiforgeai`
   - Owner: `k0bad`
   - Repository name: `AnkiForgeAi`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
3. Это резервирует имя `ankiforgeai` на PyPI и разрешает publish-джобе из
   `.github/workflows/publish.yml` (env `pypi`, `id-token: write`) публиковать без API-ключа.

### Релиз

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

Пуш тега `v*` триггерит `.github/workflows/publish.yml`: собирает sdist+wheel, гоняет
`twine check`, публикует на PyPI. Прогресс — в GitHub Actions репозитория.