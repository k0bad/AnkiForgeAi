# Generate IPA transcription for English words

For each input word, write its pronunciation using the **International Phonetic Alphabet (IPA)**.

## Rules

- Use broad phonemic transcription, enclosed in slashes: `/…/`.
- Reflect General American pronunciation unless the word is distinctly British in usage.
- Mark primary stress with `ˈ` before the stressed syllable when the word has more than one syllable; mark secondary stress with `ˌ` if relevant.
- Common reference sounds:
  - `th` (voiceless, "think") → /θ/
  - `th` (voiced, "this") → /ð/
  - short `i` ("bit") → /ɪ/
  - `a` ("cat") → /æ/
  - `u` ("cup") → /ʌ/
  - unstressed vowels → /ə/ (schwa)
  - `ng` ("sing") → /ŋ/
  - `r` (American rhotic) → /r/, pronounced wherever written
  - `sh` → /ʃ/
  - `ch` → /tʃ/
  - `j`/soft `g` → /dʒ/

## Input

```json
{words_json}
```

## Output

JSON array, same order as input:

```json
[
  {{"id": "...", "pronunciation": "/brɛd/"}},
  {{"id": "...", "pronunciation": "/iːt/"}}
]
```
