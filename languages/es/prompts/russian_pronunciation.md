# Generate Russian pronunciation for Norwegian words

For each input word, write its pronunciation using **Russian letters only** (no IPA, no Latin).

## Rules

- Transcribe the word as closely as possible to the actual Norwegian pronunciation.
- Use only Russian Cyrillic letters (а б в г д е ё ж з и й к л м н о п р с т у ф х ц ч ш щ ъ ы ь э ю я).
- Keep it simple and readable for a Russian speaker.
- For verbs with "å" prefix, transcribe without "å" and use the stem pronunciation.
- Common reference sounds:
  - `æ` → э
  - `ø` → ё
  - `å` → о
  - `k` before soft vowel → кь / ч
  - `g` before soft vowel → гь / й
  - `skj`/`sj` → ш
  - `rd` → р (retroflex, just р)
  - `rt` → т (retroflex, just т)
  - `rl` → л (retroflex, just л)
  - `rn` → н (retroflex, just н)
  - `rs` → ш
  - silent `d/g/h` in endings → omit if mute

## Input

```json
{words_json}
```

## Output

JSON array, same order as input:

```json
[
  {{"id": "...", "pronunciation": "мелк"}},
  {{"id": "...", "pronunciation": "дэй"}}
]
```