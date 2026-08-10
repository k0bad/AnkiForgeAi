# Generate example sentences for Spanish words

For each input word, write **one** short example sentence in Spanish and its Russian translation.

## Rules

- Sentence length: **6–12 words**.
- Use **everyday vocabulary** matching CEFR level **{level}**.
- The target word must appear in **a natural form** (conjugated/declined as needed).
- Avoid contrived sentences; aim for something a learner might actually say or read.
- No proper nouns (use "él"/"ella" instead of names).

## Input

```json
{words_json}
```

## Output

JSON array, same order as input:

```json
[
  {{
    "id": "...",
    "example": "Como pan con mantequilla en el desayuno.",
    "example_translation": "Я ем хлеб с маслом на завтрак."
  }}
]
```
