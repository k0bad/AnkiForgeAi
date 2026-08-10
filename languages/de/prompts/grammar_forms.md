# Generate grammatical forms for German words

You are given a list of German words. For each, produce all grammatical forms appropriate to its part of speech.

## Forms by POS

**noun** — produce 7 fields:
- `gender`: "m", "f", or "n"
- `article`: the definite article matching gender — "der", "die", or "das"
- `nominative`: singular nominative form, e.g. "Hund"
- `genitive`: singular genitive form, e.g. "Hundes"
- `dative`: singular dative form, e.g. "Hund"
- `accusative`: singular accusative form, e.g. "Hund"
- `plural`: nominative plural form, without article, e.g. "Hunde"

**verb** — produce 6 fields:
- `infinitive`: e.g. "gehen"
- `present_ich`: e.g. "gehe"
- `present_du`: e.g. "gehst"
- `present_er`: er/sie/es form, e.g. "geht"
- `preterite_ich`: e.g. "ging"
- `perfect_hat`: full Perfekt form with the correct auxiliary, e.g. "ist gegangen" or "hat gemacht"

**adj** — produce 3 fields:
- `positive`: base form, e.g. "groß"
- `comparative` (optional): e.g. "größer" — null if not applicable
- `superlative` (optional): predicative form, e.g. "am größten" — null if not applicable

For other POS values (`adv`, `prep`, `conj`, etc.) — return `null` for forms.

## Input

```json
{words_json}
```

## Output

Return ONLY a JSON array in the same order as input. Each entry has `id` (echo from input) and `forms` (the form object or `null`):

```json
[
  {{"id": "...", "forms": {{"gender": "m", "article": "der", "nominative": "Hund", "genitive": "Hundes", "dative": "Hund", "accusative": "Hund", "plural": "Hunde"}}}},
  {{"id": "...", "forms": null}}
]
```
