# Generate IPA transcription for German words

For each input word, write its pronunciation using the **International Phonetic Alphabet (IPA)**.

## Rules

- Use broad phonemic transcription, enclosed in slashes: `/…/`.
- Reflect Standard German (Hochdeutsch) pronunciation.
- Mark primary stress with `ˈ` before the stressed syllable when the word has more than one syllable.
- Mark vowel length with `ː` where relevant (long vs. short vowels are phonemically distinct in German).
- Common reference sounds:
  - `ch` (after a/o/u, "ach-Laut") → /x/
  - `ch` (after e/i, "ich-Laut") → /ç/
  - `sch` → /ʃ/
  - `sp`/`st` at the start of a word → /ʃp/ /ʃt/
  - `ü` → /y/ or /yː/
  - `ö` → /œ/ or /øː/
  - `ä` → /ɛ/ or /ɛː/
  - `ei`/`ai` → /aɪ/
  - `ie` → /iː/
  - `eu`/`äu` → /ɔʏ/
  - `z` → /ts/
  - final devoicing: `b/d/g` at end of syllable → /p/ /t/ /k/
  - `-ig` at the end of a word → /ɪç/

## Input

```json
{words_json}
```

## Output

JSON array, same order as input:

```json
[
  {{"id": "...", "pronunciation": "/broːt/"}},
  {{"id": "...", "pronunciation": "/ˈɛsn̩/"}}
]
```
