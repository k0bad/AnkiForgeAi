# Generate grammatical forms for Norwegian words

You are given a list of Norwegian (bokmål) words. For each, produce all grammatical forms appropriate to its part of speech.

## Forms by POS

**noun** — produce 5 fields:
- `gender`: "m" (en), "f" (ei — rare, treat as m if unsure), or "n" (et)
- `indefinite_singular`: e.g. "bil"
- `definite_singular`: e.g. "bilen"
- `indefinite_plural`: e.g. "biler"
- `definite_plural`: e.g. "bilene"

**verb** — produce 4 fields:
- `infinitive`: with "å" prefix, e.g. "å gå"
- `present`: e.g. "går"
- `past`: e.g. "gikk"
- `perfect`: with "har", e.g. "har gått"

**adj** — produce up to 5 fields:
- `positive_common`: e.g. "stor" (m/f form)
- `positive_neuter`: e.g. "stort" (n form)
- `positive_plural`: e.g. "store"
- `comparative` (optional): e.g. "større" — null if not applicable
- `superlative` (optional): e.g. "størst" — null if not applicable

For other POS values (`adv`, `prep`, `conj`, etc.) — return `null` for forms.

## Input

```json
{words_json}
```

## Output

Return ONLY a JSON array in the same order as input. Each entry has `id` (echo from input) and `forms` (the form object or `null`):

```json
[
  {{"id": "...", "forms": {{"gender": "m", "indefinite_singular": "bil", ...}}}},
  {{"id": "...", "forms": null}}
]
```
