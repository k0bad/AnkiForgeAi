# Generate IPA transcription for Norwegian Bokmål words

For each input word, write its pronunciation using the **International Phonetic Alphabet (IPA)**.

## Rules

- Use broad phonemic transcription, enclosed in slashes: `/…/`.
- Reflect standard Urban East Norwegian (Oslo) pronunciation.
- Mark primary stress with `ˈ` before the stressed syllable when the word has more than one syllable.
- Mark tonal accent (accent 1 `˩` vs accent 2 `˥˩`) only if you are confident; otherwise omit tone marks and give a plain segmental transcription — accuracy matters more than completeness.
- Common reference sounds:
  - `æ` → /æ/
  - `ø` → /ø/ or /œ/ depending on length
  - `å` → /oː/ or /ɔ/
  - retroflex `rd/rt/rl/rn/rs` → /ɖ ʈ ɭ ɳ ʂ/
  - `kj`/soft `k` → /ç/
  - `skj`/`sj`/soft `sk` → /ʃ/
  - `gj`/`j` → /j/
  - silent `d/g/h` in endings → omit

## Input

```json
{words_json}
```

## Output

JSON array, same order as input:

```json
[
  {{"id": "...", "pronunciation": "/mɛlk/"}},
  {{"id": "...", "pronunciation": "/dɑɡ/"}}
]
```
