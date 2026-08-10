# Generate Russian transliteration for English words

For each input word, write its pronunciation using **Russian letters only** (no IPA, no Latin).

## Rules

- Transcribe the word as closely as possible to its actual English pronunciation (not its spelling).
- Use only Russian Cyrillic letters (а б в г д е ё ж з и й к л м н о п р с т у ф х ц ч ш щ ъ ы ь э ю я).
- Keep it simple and readable for a Russian speaker — this is a pronunciation aid, not a formal transcription.
- Common reference sounds:
  - `th` (voiceless, "think") → с
  - `th` (voiced, "this") → з
  - `w` → у (as in "уиндоу")
  - `r` (English approximant) → р, lightly
  - short `i` ("bit") → и
  - `æ` ("cat") → э
  - `ʌ` ("cup") → а
  - `ə` (unstressed schwa) → reduce to the nearest neutral vowel, or omit
  - `ŋ` ("sing") → нг

## Input

```json
{words_json}
```

## Output

JSON array, same order as input:

```json
[
  {{"id": "...", "pronunciation": "брэд"}},
  {{"id": "...", "pronunciation": "ит"}}
]
```
