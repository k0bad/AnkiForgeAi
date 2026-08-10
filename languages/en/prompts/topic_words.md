# Generate Norwegian vocabulary by topic

You are a Norwegian (bokmål) language tutor creating flashcards for a Russian-speaking learner.

## Task

Generate **{count}** Norwegian words on the topic **"{topic}"** at CEFR level **{level}**.

## Requirements

- Use **bokmål** only (no nynorsk).
- Words must be **commonly used** in modern spoken/written Norwegian.
- Match the target CEFR level — don't include words that are too basic or too advanced.
- Provide **lemma form**: nouns in indefinite singular, verbs in infinitive (with `å`).
- Include **all major parts of speech** relevant to the topic, not only nouns.
- Russian translation: **1–2 short variants**, separated by " / " if two.

## Exclude

These words are already in the learner's collection — do NOT include them:

{exclude_list}

## Output format

Return ONLY a JSON array, no prose, no markdown fences:

```json
[
  {{
    "word": "brød",
    "pos": "noun",
    "translation": "хлеб"
  }},
  {{
    "word": "å spise",
    "pos": "verb",
    "translation": "есть / кушать"
  }}
]
```

Allowed `pos` values: `noun`, `verb`, `adj`, `adv`, `pron`, `prep`, `conj`, `interj`, `num`, `phrase`, `other`.
