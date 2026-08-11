# Generate IPA transcription for Spanish words

For each input word, write its pronunciation using the **International Phonetic Alphabet (IPA)**.

## Rules

- Use broad phonemic transcription, enclosed in slashes: `/…/`.
- Reflect standard Latin American Spanish pronunciation (seseo: `c`/`z` before e/i → /s/, not /θ/).
- Mark primary stress with `ˈ` before the stressed syllable, following the written accent mark or standard Spanish stress rules if unmarked.
- Common reference sounds:
  - `j`, `g` before e/i → /x/
  - `h` → silent, omit
  - `ll`, `y` → /ʝ/ (or /j/ in fast/casual speech)
  - `ñ` → /ɲ/
  - `z`, `c` before e/i → /s/
  - `rr` and word-initial `r` → /r/ (trilled)
  - single `r` between vowels → /ɾ/ (tap)
  - `qu` → /k/
  - `b`/`v` → /b/ (word-initial or after nasal) or /β/ (elsewhere) — both spellings are the same sound
  - `d` between vowels → /ð/
  - `g` between vowels (not before e/i) → /ɣ/

## Input

```json
{words_json}
```

## Output

JSON array, same order as input:

```json
[
  {{"id": "...", "pronunciation": "/pan/"}},
  {{"id": "...", "pronunciation": "/koˈmeɾ/"}}
]
```
