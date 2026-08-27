# Generate grammatical forms for Norwegian words

You are given a list of Norwegian (bokmål) words. For each, produce all grammatical forms appropriate to its part of speech.

## Forms by POS

**noun** — produce 5 fields:
- `gender`: "m" (en), "f" (ei), or "n" (et)
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

## Pre-supplied forms

An entry may carry a `known_forms` object. Those values come from a dictionary, not
from inference — treat them as given. Do not contradict them: echo each one back
unchanged, and make every other form agree with it.

This matters most for `gender`. A noun given `"gender": "f"` declines as a feminine:
`ei jente → jenta, jenter, jentene` — not `jenten`. Never "round" a supplied `f` to
`m`; if the gender is supplied, it is not a guess.

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

Entries with `known_forms` still need every field — the supplied ones echoed back,
the rest filled in:

```json
[
  {{"id": "...", "forms": {{"gender": "f", "indefinite_singular": "jente",
    "definite_singular": "jenta", "indefinite_plural": "jenter",
    "definite_plural": "jentene"}}}}
]
```
