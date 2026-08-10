# Generate Russian transliteration for Spanish words

For each input word, write its pronunciation using **Russian letters only** (no IPA, no Latin).

## Rules

- Transcribe the word as closely as possible to its actual Spanish pronunciation.
- Use only Russian Cyrillic letters (а б в г д е ё ж з и й к л м н о п р с т у ф х ц ч ш щ ъ ы ь э ю я).
- Keep it simple and readable for a Russian speaker.
- Common reference sounds:
  - `j`, `g` before e/i → х
  - `h` → silent, omit
  - `ll` → й (general Latin American / most common pronunciation)
  - `ñ` → нь
  - `z`, `c` before e/i → с
  - `rr` and word-initial `r` → р (trilled)
  - `qu` → к
  - `v` → б (Spanish v and b sound the same)
  - stress follows the written accent mark, or standard Spanish stress rules if unmarked — reflect it through natural vowel placement, no stress marks in output

## Input

```json
{words_json}
```

## Output

JSON array, same order as input:

```json
[
  {{"id": "...", "pronunciation": "пан"}},
  {{"id": "...", "pronunciation": "комэр"}}
]
```
