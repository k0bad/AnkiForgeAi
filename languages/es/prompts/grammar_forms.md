# Generate grammatical forms for Spanish words

You are given a list of Spanish words. For each, produce all grammatical forms appropriate to its part of speech.

## Forms by POS

**noun** — produce 3 fields:
- `gender`: "m" or "f"
- `singular`: e.g. "casa"
- `plural`: e.g. "casas"

**verb** — produce 12 fields (correct conjugation, including irregulars):
- `infinitive`: e.g. "hablar"
- `present_yo`: e.g. "hablo"
- `present_tu`: e.g. "hablas"
- `present_el`: él/ella/usted form, e.g. "habla"
- `present_nosotros`: e.g. "hablamos"
- `present_ellos`: ellos/ustedes form, e.g. "hablan"
- `preterite_yo`: e.g. "hablé"
- `preterite_el`: e.g. "habló"
- `imperfect_yo`: e.g. "hablaba"
- `future_yo`: e.g. "hablaré"
- `participle`: e.g. "hablado"
- `gerund`: e.g. "hablando"

**adj** — produce 4 fields:
- `masculine_singular`: e.g. "bueno"
- `feminine_singular`: e.g. "buena"
- `masculine_plural`: e.g. "buenos"
- `feminine_plural`: e.g. "buenas"
(if the adjective has one common form for both genders, e.g. "grande", repeat it in all four fields)

For other POS values (`adv`, `prep`, `conj`, etc.) — return `null` for forms.

## Input

```json
{words_json}
```

## Output

Return ONLY a JSON array in the same order as input. Each entry has `id` (echo from input) and `forms` (the form object or `null`):

```json
[
  {{"id": "...", "forms": {{"gender": "f", "singular": "casa", "plural": "casas"}}}},
  {{"id": "...", "forms": null}}
]
```
