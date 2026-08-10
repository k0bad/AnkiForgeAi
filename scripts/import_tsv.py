"""Импорт карточек из TSV-файла в БД с enrich + audio генерацией.

Запуск: python scripts/import_tsv.py <файл.tsv>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

# Добавляем src в путь для импорта ankicards
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ankicards.config import get_config
from ankicards.db import Database
from ankicards.models import POS, Card, Level, Status
from ankicards.pipeline import run_ingest_pipeline

# ─── Маппинг POS ───

POS_MAP: dict[str, POS] = {
    "substantiv": POS.NOUN,
    "verb": POS.VERB,
    "adjektiv": POS.ADJECTIVE,
    "adverb": POS.ADVERB,
    "pronomen": POS.PRONOUN,
    "eiepronomen": POS.PRONOUN,
    "spørreord": POS.PRONOUN,
    "pekende": POS.PRONOUN,
    "konjunksjon": POS.CONJUNCTION,
    "preposisjon": POS.PREPOSITION,
    "interjeksjon": POS.INTERJECTION,
    "setning": POS.PHRASE,
    "determinativ": POS.OTHER,
    "tallord": POS.NUMERAL,
}

# ─── Известные POS-значения для поиска в строке ───
KNOWN_POS = sorted(POS_MAP.keys(), key=len, reverse=True)  # от длинных к коротким

def parse_line(line: str) -> dict[str, str] | None:
    """Разобрать строку файла.

    Алгоритм:
    1. Найти POS (известное норвежское слово)
    2. От POS 6 токенов вправо: Gender, Plural, Tags, Topic, Status, DateAdded
    3. От POS 2 токена влево: Image, Audio
    4. Всё что левее Audio: первые 6 колонок, разделённые 2+ пробелами
    """
    line = line.rstrip("\n\r")
    if not line or line.startswith("ID"):
        return None

    # Находим все токены с их позициями
    import re
    tokens = [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", line)]

    # 1. Находим POS (сканируем справа, ищем известное слово,
    #    после которого есть 6 токенов до конца строки)
    pos_idx = -1
    for i in range(len(tokens) - 7, -1, -1):
        word = tokens[i][0].lower()
        if word in KNOWN_POS:
            # Проверяем, что после POS 6 токенов (Gender..DateAdded)
            # Токен на i+6 должен быть датой
            if re.match(r"\d{4}-\d{2}-\d{2}", tokens[i + 6][0]):
                pos_idx = i
                break

    if pos_idx < 0 or pos_idx + 6 >= len(tokens):
        return None

    # 2. 6 колонок справа от POS
    result = {
        "POS": tokens[pos_idx][0],
        "Gender": tokens[pos_idx + 1][0],
        "Plural": tokens[pos_idx + 2][0],
        "Tags": tokens[pos_idx + 3][0],
        "Topic": tokens[pos_idx + 4][0],
        "Status": tokens[pos_idx + 5][0],
        "DateAdded": tokens[pos_idx + 6][0],
    }

    # Инициализация всех полей
    for k in ("ID", "Word", "Pronunciation", "Translation",
              "Example", "ExampleTranslation", "Image", "Audio"):
        result.setdefault(k, "")

    # 3. Всё что левее POS — это первые 8 колонок (ID..Audio)
    # Определяем, где начинается POS
    boundary = tokens[pos_idx][1]
    left_str = line[:boundary].strip()

    if not left_str:
        return None

    # Разбиваем левую часть на 2+ пробелов
    left_parts = re.split(r"\s{2,}", left_str)
    n = len(left_parts)

    # 4. Image и Audio: ищем "-" в left_parts (Image всегда "-")
    # Всё что после "-" (до POS) — это Audio
    img_idx = -1
    for i in range(n - 1, -1, -1):
        if left_parts[i] == "-":
            img_idx = i
            break

    if img_idx >= 0:
        result["Image"] = "-"
        # Всё что между Image и POS — это Audio (может быть несколько слов)
        if img_idx + 1 < n:
            result["Audio"] = " ".join(left_parts[img_idx + 1:])
        # Удаляем Image и Audio из left_parts
        left_parts = left_parts[:img_idx]
        n = len(left_parts)
    else:
        result["Image"] = ""
        result["Audio"] = ""

    if n == 6:
        for k, v in zip(("ID", "Word", "Pronunciation", "Translation",
                         "Example", "ExampleTranslation"), left_parts):
            result[k] = v
    elif n == 5:
        # [ID, W+Pr+Tr, Ex, ExTr] или [ID+W+Pr+Tr, Ex, ExTr]
        sub0 = left_parts[0].split()
        if len(sub0) == 1:
            result["ID"] = sub0[0]
            sub1 = left_parts[1].split()
            if len(sub1) >= 3:
                result["Word"] = sub1[0]
                result["Pronunciation"] = sub1[1]
                result["Translation"] = " ".join(sub1[2:])
            elif len(sub1) == 2:
                result["Word"] = sub1[0]
                result["Pronunciation"] = sub1[1]
            else:
                result["Word"] = sub1[0] if sub1 else ""
            result["Example"] = left_parts[2]
            result["ExampleTranslation"] = left_parts[3] if n >= 4 else ""
        else:
            for i, k in enumerate(("ID", "Word", "Pronunciation", "Translation")):
                if i < len(sub0):
                    result[k] = sub0[i]
            if len(sub0) > 4:
                result["Translation"] = " ".join(sub0[4:])
            result["Example"] = left_parts[1]
            result["ExampleTranslation"] = left_parts[2] if n >= 3 else ""
    elif n == 4:
        sub0 = left_parts[0].split()
        if len(sub0) == 1:
            result["ID"] = sub0[0]
            sub1 = left_parts[1].split()
            if len(sub1) >= 3:
                result["Word"] = sub1[0]
                result["Pronunciation"] = sub1[1]
                result["Translation"] = " ".join(sub1[2:])
            elif len(sub1) == 2:
                result["Word"] = sub1[0]
                result["Pronunciation"] = sub1[1]
            else:
                result["Word"] = sub1[0] if sub1 else ""
            result["Example"] = left_parts[2]
            result["ExampleTranslation"] = left_parts[3]
        else:
            for i, k in enumerate(("ID", "Word", "Pronunciation", "Translation")):
                if i < len(sub0):
                    result[k] = sub0[i]
            if len(sub0) > 4:
                result["Translation"] = " ".join(sub0[4:])
            result["Example"] = left_parts[1]
            result["ExampleTranslation"] = left_parts[2] if n >= 3 else ""
    elif n == 3:
        result["ID"] = left_parts[0].split()[0] if left_parts[0].split() else ""
        sub1 = left_parts[1].split()
        if len(sub1) >= 3:
            result["Word"] = sub1[0]
            result["Pronunciation"] = sub1[1]
            result["Translation"] = " ".join(sub1[2:])
        elif len(sub1) == 2:
            result["Word"] = sub1[0]
            result["Pronunciation"] = sub1[1]
        else:
            result["Word"] = sub1[0] if sub1 else ""
        ee = re.split(r"\s{2,}", left_parts[2])
        if len(ee) >= 2:
            result["Example"] = ee[0]
            result["ExampleTranslation"] = ee[1]
        else:
            result["Example"] = left_parts[2]
    elif n == 2:
        # 2 части: [ID+W+P+T+Ex, ExTr] или [ID, W+P+T+Ex+ExTr]
        first_tokens = left_parts[0].split()
        second_tokens = left_parts[1].split() if len(left_parts) > 1 else []
        # Проверяем: если во второй части есть кириллица — это ExTrans,
        # а первая часть содержит ID+W+P+T+Ex
        has_cyrillic_2 = any(ord(ch) > 0x0400 for ch in " ".join(second_tokens))
        if has_cyrillic_2 and len(first_tokens) >= 4:
            # [ID+W+P+T+Ex, ExTr]
            result["ID"] = first_tokens[0]
            result["Word"] = first_tokens[1]
            result["Pronunciation"] = first_tokens[2]
            result["Translation"] = first_tokens[3]
            result["Example"] = " ".join(first_tokens[4:]) if len(first_tokens) > 4 else ""
            result["ExampleTranslation"] = " ".join(second_tokens)
        else:
            # [ID, W+P+T+Ex+ExTr]
            result["ID"] = first_tokens[0] if first_tokens else ""
            merged = left_parts[1] if len(left_parts) > 1 else left_parts[0]
            all_tokens = merged.split()
            if len(all_tokens) >= 3:
                result["Word"] = all_tokens[0]
                result["Pronunciation"] = all_tokens[1]
                result["Translation"] = all_tokens[2]
                remaining = all_tokens[3:]
            else:
                remaining = all_tokens
            # Ищем первый кириллический токен = начало ExTrans
            ex_tokens = []
            extr_tokens = []
            found_cyrillic = False
            for t in remaining:
                has_cyrillic = any(ord(ch) > 0x0400 for ch in t)
                if has_cyrillic and not found_cyrillic:
                    found_cyrillic = True
                if found_cyrillic:
                    extr_tokens.append(t)
                else:
                    ex_tokens.append(t)
            result["Example"] = " ".join(ex_tokens) if ex_tokens else ""
            result["ExampleTranslation"] = " ".join(extr_tokens) if extr_tokens else ""
    elif n == 1:
        # 1 часть: ID+Word+Pron+Trans+Ex+ExTrans (всё слито)
        all_tokens = left_parts[0].split()
        if len(all_tokens) >= 4:
            result["ID"] = all_tokens[0]
            result["Word"] = all_tokens[1]
            result["Pronunciation"] = all_tokens[2]
            result["Translation"] = all_tokens[3]
            remaining = all_tokens[4:]
        else:
            remaining = all_tokens
        # Ищем первый кириллический токен = начало ExTrans
        ex_tokens = []
        extr_tokens = []
        found_cyrillic = False
        for t in remaining:
            has_cyrillic = any(ord(ch) > 0x0400 for ch in t)
            if has_cyrillic and not found_cyrillic:
                found_cyrillic = True
            if found_cyrillic:
                extr_tokens.append(t)
            else:
                ex_tokens.append(t)
        result["Example"] = " ".join(ex_tokens) if ex_tokens else ""
        result["ExampleTranslation"] = " ".join(extr_tokens) if extr_tokens else ""
    else:
        return None

    return result


def parse_level(tags_str: str) -> Level | None:
    """Извлечь CEFR уровень из тегов (A1, A2, B1, B2, C1, C2)."""
    parts = tags_str.split(";")
    for p in parts:
        p = p.strip().upper()
        try:
            return Level(p)
        except ValueError:
            continue
    return None


def parse_tags(tags_str: str) -> list[str]:
    """Разбить 'A1;noun;home;stue' → ['noun', 'home', 'stue'],
    исключая уровень (он уйдёт в отдельное поле)."""
    parts = tags_str.split(";")
    return [p.strip() for p in parts if not p.strip().upper().startswith(("A1", "A2", "B1", "B2", "C1", "C2"))]


def map_pos(raw: str) -> POS:
    """Сконвертировать норвежское название POS в английское."""
    clean = raw.strip().lower()
    return POS_MAP.get(clean, POS.OTHER)


def parse_status(raw: str) -> Status:
    """'active' → 'pending' (чтобы enrich добьёт остальное)."""
    clean = raw.strip().lower()
    if clean == "active":
        return Status.PENDING
    try:
        return Status(clean)
    except ValueError:
        return Status.PENDING


def row_to_card(row: dict) -> Card | None:
    """Создать Card из одной строки TSV."""
    word = row.get("Word", "").strip()
    if not word:
        return None

    pos = map_pos(row.get("POS", ""))
    tags_str = row.get("Tags", "").strip()
    level = parse_level(tags_str) or None

    card = Card(
        id=str(uuid4()),
        word=word,
        pronunciation=row.get("Pronunciation", "").strip() or None,
        translation=row.get("Translation", "").strip() or "",
        example=row.get("Example", "").strip() or None,
        example_translation=row.get("ExampleTranslation", "").strip() or None,
        pos=pos,
        forms=None,  # enrich доберёт
        level=level,
        topic=row.get("Topic", "").strip() or None,
        source="manual",
        image=None,
        audio=None,
        tags=parse_tags(tags_str),
        status=parse_status(row.get("Status", "active")),
        anki_note_id=None,
    )
    return card


async def main() -> None:
    tsv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not tsv_path or not tsv_path.exists():
        print(f"Использование: python {sys.argv[0]} <файл.tsv>")
        print(f"Файл не найден: {tsv_path}")
        sys.exit(1)

    cfg = get_config()
    db = Database(cfg.paths.db)

    # Читаем файл строк за строкой
    with tsv_path.open(encoding="utf-8") as f:
        cards: list[Card] = []
        for line in f:
            row = parse_line(line)
            if row:
                card = row_to_card(row)
                if card:
                    cards.append(card)

    print(f"📄 Прочитано карточек из файла: {len(cards)}")

    if not cards:
        print("❌ Нет карточек для импорта")
        return

    # Вставляем все карточки в БД со статусом pending
    count = 0
    for card in cards:
        db.insert_card(card)
        db.log_action("import", card_id=card.id, details={"word": card.word, "pos": card.pos.value})
        count += 1

    print(f"✅ Импортировано в БД: {count} карточек (статус: pending)")
    print(f"   Теперь запусти: python scripts/process_pending.py")


if __name__ == "__main__":
    asyncio.run(main())