# Classify part of speech

You are given a list of words in the target language. Each item carries a
translation into another language purely to disambiguate homographs — classify
the **target-language word**, not the translation.

## Allowed values

`noun`, `verb`, `adj`, `adv`, `pron`, `prep`, `conj`, `interj`, `num`, `phrase`, `other`

Rules:
- Plural-only nouns (*foreldre*, *briller*) and mass nouns (*melk*, *sukker*) are still `noun`.
- A word that names a state or quality of something (*glad*, *syk*, *kald*) is `adj`,
  even when the translation is a noun in the other language.
- A fixed multi-word expression that is not one lexical unit (*vondt i halsen*) is `phrase`.
- If a word fits several parts of speech, pick the one the translation implies.

## Input

```json
{words_json}
```

## Output

Return ONLY a JSON array, one entry per input item, in the same order:

```json
[
  {{"id": "...", "pos": "adj"}},
  {{"id": "...", "pos": "noun"}}
]
```

No prose, no explanation, no text outside the array.
