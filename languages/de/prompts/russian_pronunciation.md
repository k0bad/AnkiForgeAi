# Generate Russian transliteration for German words

For each input word, write its pronunciation using **Russian letters only** (no IPA, no Latin).

## Rules

- Transcribe the word as closely as possible to its actual German pronunciation.
- Use only Russian Cyrillic letters (а б в г д е ё ж з и й к л м н о п р с т у ф х ц ч ш щ ъ ы ь э ю я).
- Keep it simple and readable for a Russian speaker.
- Common reference sounds:
  - `ch` (after a/o/u, "ach-Laut") → х
  - `ch` (after e/i, "ich-Laut") → хь / щ
  - `sch` → ш
  - `sp`/`st` at the start of a word → шп / шт
  - `ü` → ю
  - `ö` → ё
  - `ä` → э
  - `ei` → ай
  - `ie` → и (long)
  - `eu`/`äu` → ой
  - `w` → в
  - `v` → ф (native words) or в (loanwords)
  - `z` → ц
  - final `-ig` → ихь

## Input

```json
{words_json}
```

## Output

JSON array, same order as input:

```json
[
  {{"id": "...", "pronunciation": "брот"}},
  {{"id": "...", "pronunciation": "эсн"}}
]
```
