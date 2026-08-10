# AnkiCards

Пайплайн словарных карточек для изучения языков с автоматической отправкой в Anki.
**Поддерживает любой язык** через конфигурируемые YAML-профили (`languages/{code}/language.yaml`).

Встроенные языки: 🇳🇴 Norwegian Bokmål (`nb`), 🇩🇪 German (`de`).

## Что делает

1. **Ingest** — собирает кандидатов в карточки одним из способов:
   - парсинг URL (норвежская статья / урок)
   - генерация по теме через Claude API («20 слов про еду, A2»)
2. **Enrich** — добавляет грамматические формы, перевод, пример с переводом.
3. **Dedupe** — проверяет дубликаты против уже существующих в Anki и в staging.
4. **Media** — генерирует mp3 (edge-tts) и качает картинку (Unsplash).
5. **Review** — интерактивный ревью спорных кандидатов в Claude Code chat или CLI.
6. **Push** — отправляет одобренные карточки в Anki через AnkiConnect.

## Архитектурные решения

| Решение | Почему |
|---|---|
| **AnkiConnect**, не CSV | Двусторонняя синхронизация, можно проверять дубликаты против реальной колоды Anki |
| **SQLite** как staging + audit + кэш | Anki = источник истины, SQLite = быстрый локальный индекс и история операций |
| **Расширенная схема `forms` как JSON** | Разные структуры для существительных/глаголов/прилагательных без раздувания таблицы |
| **edge-tts** вместо gTTS | Нейросетевые голоса Microsoft, бесплатно, отличное качество для bokmål |
| **Unsplash API** | Легально, бесплатно (50/час), реальные фото |
| **Одна колода `Norsk` + иерархические теги** | Anki SRS работает лучше с interleaving; теги дают гибкую фильтрацию |
| **Промпты в `prompts/*.md`** | Можно итерировать качество без правки кода |
| **Только Claude API** для LLM | Локальный qwen2.5-coder слаб для норвежского; токены дёшевы |

## Статусы карточки

```
pending → review → approved → pushed
              ↓
           skipped / suspended
```

- **pending** — только что создана, ещё не обогащена/не проверена
- **review** — найдены потенциальные дубликаты, нужно решение
- **approved** — готова к отправке в Anki
- **pushed** — уже в Anki (`anki_note_id` заполнен)
- **skipped** — отброшена (с указанием причины в audit_log)
- **suspended** — отложена (например, ждёт картинку)

## Поддерживаемые языки

Проект использует конфигурируемые YAML-профили в `languages/{code}/`. Поддерживаются:

| Язык | Код | Статус |
|------|-----|--------|
| 🇳🇴 Norwegian Bokmål | `nb` | ✅ Полный |
| 🇩🇪 German | `de` | ✅ Проверен |

### Как добавить свой язык

1. Создать `languages/{code}/language.yaml` по образцу ([nb](languages/nb/language.yaml) / [de](languages/de/language.yaml)):
```yaml
code: xx              # ISO 639-1
name: Language Name
article: true         # есть ли артикли у существительных
pos_labels:           # POS → название на целевом языке
  noun: ...
  verb: ...
forms:                # схема грамматических полей для каждой POS
  noun:
    - {key: gender, label: "Род"}
    - {key: ...}
tts:                  # голоса edge-tts
  voice_female: xx-XX-NameNeural
anki:
  deck_name: MyDeck
back_labels:          # переводы label'ов на язык пользователя
  translation: "Перевод"
```
2. Скопировать промпты: `cp prompts/*.md languages/{code}/prompts/`
3. Адаптировать промпты под целевой язык (контекст, примеры)
4. Поменять `language` в `config.yaml` (когда появится — пока через `get_language('xx'))

**Минимальный набор для рабочего языка:** `language.yaml` + промпт `topic_words.md`

## Установка

```powershell
# 1. Python 3.11+
# 2. Anki desktop + addon AnkiConnect (https://ankiweb.net/shared/info/2055492159)
# 3. Зависимости (рекомендую uv: https://github.com/astral-sh/uv)

uv venv
.venv\Scripts\activate
uv pip install -e ".[dev]"

# 4. Конфигурация
copy .env.example .env
# отредактировать .env — добавить ANTHROPIC_API_KEY и UNSPLASH_ACCESS_KEY

# 5. Инициализация
python scripts/init_db.py

# 6. (При запущенном Anki) создать Note Type
python scripts/setup_anki_notetype.py
```

## Использование

```powershell
# Сгенерировать 20 слов про еду уровня A2
ankicards ingest topic "mat" --count 20 --level A2

# Извлечь слова со страницы
ankicards ingest url "https://www.nrk.no/norge/..."

# Пройти ревью спорных кандидатов
ankicards review

# Отправить approved карточки в Anki
ankicards push

# Обновить локальный кэш заметок из Anki (раз в день)
ankicards sync

# Статистика
ankicards stats
```

## Workflow в Claude Code

В чате VS Code просто говори:

> «Сгенерируй 20 слов по теме клothing уровня A2»

Claude сам вызовет нужные команды, покажет результат, спросит про дубликаты, отправит в Anki после твоего «ок».

## Структура проекта

```
src/ankicards/
├── models.py              # Card, Forms, Decision (pydantic)
├── config.py              # config.yaml + .env
├── db.py                  # SQLite layer
├── cli.py                 # Typer CLI
├── pipeline.py            # оркестратор стадий
├── dedupe.py              # exact + fuzzy match
├── ingest/
│   ├── url.py             # trafilatura + LLM
│   └── topic.py           # Claude API
├── enrich/
│   ├── grammar.py         # формы по POS
│   ├── translation.py
│   └── examples.py
├── media/
│   ├── tts.py             # edge-tts
│   └── images.py          # Unsplash
├── anki/
│   ├── connect.py         # HTTP клиент
│   ├── sync.py            # Anki → cache
│   └── notetype.py        # Note Type definition
└── review/
    └── interactive.py     # rich + questionary

prompts/                   # MD-промпты, перезагружаются на каждом вызове
scripts/                   # init_db, setup_anki_notetype
tests/
data/                      # ankicards.db, logs (в .gitignore)
media/                     # audio, images (в .gitignore)
```

## Roadmap

- [x] Скелет проекта
- [ ] AnkiConnect клиент
- [ ] Ingest по теме (Claude API)
- [ ] Dedupe (rapidfuzz)
- [ ] edge-tts
- [ ] Note Type setup script
- [ ] Push pipeline
- [ ] Sync Anki → cache
- [ ] Ingest URL (trafilatura)
- [ ] Unsplash images
- [ ] Тесты
- [ ] Опционально: Telegram бот для мобильного ревью
- [ ] Опционально: nynorsk поддержка
