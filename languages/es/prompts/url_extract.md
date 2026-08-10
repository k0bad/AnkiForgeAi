# Extract vocabulary from a Spanish text

You are given the main text content of a webpage in Spanish. Extract useful vocabulary for a learner at CEFR level **{level}**.

## Rules

- Skip too basic words (es, y, el, la — unless level is A1).
- Skip too rare words above the target level.
- Skip proper nouns (names, brands) unless culturally important.
- Limit to **15–25 words** total.
- Provide lemma form (infinitive for verbs, singular without article for nouns).
- For each word, provide: `word`, `pos`, `translation`.
- If a word appears in a useful context, also include a short `example` (≤ 12 words) **taken from the source text** and your translation as `example_translation`.

## Source text

```
{text}
```

## Output

JSON array, no prose:

```json
[
  {{
    "word": "...",
    "pos": "...",
    "translation": "...",
    "example": "...",
    "example_translation": "..."
  }}
]
```

Allowed `pos`: `noun`, `verb`, `adj`, `adv`, `pron`, `prep`, `conj`, `interj`, `num`, `phrase`, `other`.
