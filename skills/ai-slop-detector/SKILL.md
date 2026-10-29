---
name: ai-slop-detector
description: Finds "AI slop" writing patterns, such as cliché phrases ("delve", "in today's fast-paced world"), "not just X, it's Y" frames, sycophancy, emoji bullets, em-dash overload, uniform sentence rhythm and empty conclusions, using a scoring script with a 120-pattern catalog, then rewrites the text to sound specific and human while keeping the author's meaning and voice. Use when the user asks to humanize text, remove AI-sounding phrasing, de-slop a draft, check whether writing sounds robotic, or flag AI-sounding prose in docs or pull requests in CI.
---

# AI Slop Detector

Make writing sound like a specific human wrote it, not an average of the internet.

## 1. Scan

```bash
python3 scripts/slop_scan.py draft.md                       # one file
python3 scripts/slop_scan.py 'docs/**/*.md' --exclude 'docs/archive/**'
pbpaste | python3 scripts/slop_scan.py -                    # stdin
python3 scripts/slop_scan.py --json draft.md                # machine-readable
python3 scripts/slop_scan.py --format github --fail-above 50 '**/*.md'   # CI annotations + exit code
```

Stdlib only, Python 3.10+. Texts under 50 words are skipped (`--min-words`). Code blocks, inline code, URLs and front matter are ignored.

For each text the script prints a **slop-signal score from 0 to 100** (low < 20, moderate 20-29, high >= 30), six component signals, and every pattern hit with its line number, id and a plainer alternative. The score is a weighted sum:

| signal | weight | what it measures |
|---|---:|---|
| patterns | 0.70 | weighted hits per 1,000 words from the catalog, `patterns.json` in `scripts/` |
| lists | 0.10 | share of lines that are bullets or headings |
| paragraphs | 0.06 | same-length paragraphs (low length variation) |
| triads | 0.06 | "X, Y, and Z" lists per 1,000 words |
| rhythm | 0.04 | same-length sentences (low length variation) |
| em-dashes | 0.04 | em-dashes per 1,000 words |

The exact formula is in the script's docstring. The 120 patterns, with why each reads as AI and what to write instead, are in `references/patterns.md`. Single words weigh 0.3-1.5; templates such as "It's not about X. It's about Y." weigh 3.

Benchmark: on a small held-out set (15 LLM-written vs 15 pre-2022 human passages) it scored ROC-AUC 0.98 and precision/recall 0.93 at 30, but one model wrote both the LLM samples and the patterns, so expect less on real text (`bench/README.md`).

## 2. Read the result

- **The hits are the evidence; the score is a summary.** Start with the heaviest hits, not the number.
- A high score means "reads like default assistant prose". A low score does not mean a human wrote it: casual or edited LLM text often scores low.
- Formal or corporate human writing (mission statements, press releases) can score moderate. Lists and checklists raise the score even when they are the right format.
- Short texts are noisy: one heavy hit in 150 words can push a score past 30.

## 3. Diagnose what the script can't catch

- Generic claims with no specifics (no names, numbers, examples).
- Reflexive "X, Y, and Z" triads that pad rather than list.
- Hedging stacks ("may potentially help to") and bullet lists where prose would read better.
- The same paragraph shape repeated over and over: claim, explanation, "This means...".
- Praise words standing in for evidence ("powerful", "robust", "seamless").

## 4. Rewrite

- Keep the meaning, the facts and the author's voice. Ask for a sample of their own writing if you can.
- Replace abstractions with concrete details **the author supplies**. Never invent anecdotes or data. Mark the gaps as `[add a real example]`.
- Vary sentence length, use plain verbs, and cut filler, sign-offs and most em-dashes.
- Show the top 5 changes as before -> after, then the full rewrite.
- Re-run the scanner and report both scores. Don't chase zero: a rewrite that reads well at 15 beats a contorted one at 0.

See `examples/` for three before -> after pairs (LinkedIn post, README intro, cover letter) with real scanner output.

## 5. Use it in CI

The standalone mirror [milad-ahmd/ai-slop-detector](https://github.com/milad-ahmd/ai-slop-detector) is a GitHub Action that annotates pull requests:

```yaml
- uses: actions/checkout@v4
- uses: milad-ahmd/ai-slop-detector@main
  with:
    files: "docs/**/*.md README.md"
    pr-body: true        # also scan the PR description
    fail-above: ""       # empty = annotate only; e.g. 50 to fail the job
```

Prefer annotate-only. If you set `fail-above`, use it as a nudge on docs, never as a gate on people.

## 6. Change the catalog

Edit `patterns.json` in `scripts/` (each pattern needs `id`, `category`, `weight`, `regex`, `why`, `instead` and a matching `example`), then:

```bash
python3 scripts/gen_patterns_md.py                     # regenerate references/patterns.md
python3 -m unittest discover -s scripts                # every example must match; false-positive guards must not
python3 scripts/bench.py --split dev                   # tune on dev; quote test numbers only
```

## Rules

- This measures **style signals, not authorship**. Never claim it proves text was written by AI, and don't use it to accuse students, employees or writers.
- Don't strip technical terms that are correct in context ("robust" in statistics, "leverage" in finance). The catalog already skips the common ones.
- Never fabricate specifics during a rewrite; leave a visible placeholder instead.
