# Generate grammatical forms for English words

You are given a list of English words. For each, produce all grammatical forms appropriate to its part of speech.

## Forms by POS

**noun** — produce 2 fields:
- `singular`: e.g. "child"
- `plural`: e.g. "children" (irregular plurals must be correct, not just "+s")

**verb** — produce 5 fields:
- `base_form`: e.g. "go"
- `past_simple`: e.g. "went"
- `past_participle`: e.g. "gone"
- `present_participle`: -ing form, e.g. "going"
- `third_person`: he/she/it form, e.g. "goes"

**adj** — produce up to 3 fields:
- `positive`: e.g. "good"
- `comparative` (optional): e.g. "better" — null if there is no common single-word comparative
- `superlative` (optional): e.g. "best" — null if there is no common single-word superlative

For other POS values (`adv`, `prep`, `conj`, etc.) — return `null` for forms.

## Input

```json
{words_json}
```

## Output

Return ONLY a JSON array in the same order as input. Each entry has `id` (echo from input) and `forms` (the form object or `null`):

```json
[
  {{"id": "...", "forms": {{"singular": "child", "plural": "children"}}}},
  {{"id": "...", "forms": null}}
]
```
