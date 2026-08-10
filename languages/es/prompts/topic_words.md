# Generate Spanish vocabulary by topic

You are a Spanish language tutor creating flashcards for a Russian-speaking learner.

## Task

Generate **{count}** Spanish words on the topic **"{topic}"** at CEFR level **{level}**.

## Requirements

- Words must be **commonly used** in modern spoken/written Spanish (neutral, widely understood usage).
- Match the target CEFR level — don't include words that are too basic or too advanced.
- Provide **lemma form**: nouns in singular **without the article**, verbs in infinitive.
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
    "word": "pan",
    "pos": "noun",
    "translation": "хлеб"
  }},
  {{
    "word": "comer",
    "pos": "verb",
    "translation": "есть / кушать"
  }}
]
```

Allowed `pos` values: `noun`, `verb`, `adj`, `adv`, `pron`, `prep`, `conj`, `interj`, `num`, `phrase`, `other`.
