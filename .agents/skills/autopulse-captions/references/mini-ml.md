# Mini-ML — how the caption corrector learns

Stored in `learned_memory.json`, split into language profiles:
`profiles.en` and `profiles.id` (so Indonesian slang rules never touch English, e.g.
`main → mau yang` only lives in `id`).

## Training (revision mode)

When `generate_captions.py words_revised.json` runs, it aligns every raw Whisper word
(`<video>_raw_words.json`, referenced by `last_run.json`) with the user's revised word at
the same timestamp (midpoint distance < 0.24 s) and records:

- **unigram**: `raw word → corrected word` counts
- **bigram / trigram context**: corrections keyed by previous/next word, including cases
  where the user *kept* the word (so `masa gini` stays `masa`, but `masa aja → Mas aja`)
- **custom vocab**: names, capitalized words and censored words (`Bangs*t`) — injected
  into Whisper as `hotwords` + `initial_prompt` on the next run
- **approved words**: everything the user confirmed, never flagged by phonetic matching

The language of the revision is detected automatically (or taken from `last_run.json`).

## Inference (new video)

For every word, in order:

1. Context n-gram (trigram → next-bigram → prev-bigram), confidence ≥ 0.65
2. Unigram memory (skipped if context approved the original), ≥ 0.60
3. Phonetic similarity to custom vocab (Indonesian-aware skeleton), ≥ 0.82
4. Built-in rules: `SLANG_SUSPECT_MAP` (id) / `EN_SUSPECT_MAP` contractions (en),
   short duplicate stutters, low Whisper probability (< 0.68)

Corrections with confidence ≥ `--auto-fix-ml` (default 0.80) are applied automatically;
the rest appear in the editor's **⚠️ Mencurigakan** tab with a one-click fix.

## Reset

Delete `learned_memory.json` to start fresh, or remove one profile under `profiles`.
