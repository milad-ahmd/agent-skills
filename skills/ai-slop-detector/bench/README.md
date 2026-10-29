# Benchmark

A small labeled set for checking whether `slop_scan.py` separates assistant-style LLM prose from human prose. It is a sanity check, not proof that the scanner works on your text.

```bash
python3 scripts/bench.py              # held-out test split (the numbers to quote)
python3 scripts/bench.py --split dev  # the split the weights were chosen on
python3 scripts/bench.py --split all -v   # every sample with its score
```

## Data

- **31 human passages** published before 2022: 21 excerpts from Wikipedia revisions saved in late 2020, 7 U.S. government web pages archived 2020-2021 (NPS, NOAA, FEMA, CDC, USGS, Library of Congress, Digital.gov), and 3 Project Gutenberg essays. Sources and licenses for each one are in [SOURCES.md](SOURCES.md).
- **31 LLM-written passages**, written by an LLM for this benchmark, one per human topic. 26 imitate a default chat-assistant answer; 5 imitate an LLM prompted to sound casual.
- **Hard cases**: 5 formal, corporate or list-heavy human passages, and the 5 casual-prompted LLM passages.
- 120-400 words each, about 95 KB in total. Labels are in `labels.csv`.
- **Split** by topic: odd-numbered topics are `dev` (16 + 16), even-numbered are `test` (15 + 15). Both texts on a topic always land in the same split.

## Protocol

1. The pattern catalog was written from general knowledge of chat-model style before any benchmark text was scored.
2. The component weights, saturation points and the default threshold (30) were set by hand after looking at per-signal ROC-AUCs on **dev only**. A few regex bugs found while writing the examples and unit tests were fixed before the test split was scored.
3. The test split was then scored once with everything frozen. Afterwards two human passages from contractor-run sites (JPL, NREL) were swapped for Wikipedia text on the same topics to keep licensing simple. The re-run gave the same headline numbers.

## Results

Flagged means score >= 30. ROC-AUC is the chance that a random LLM passage outscores a random human one.

| split | n (LLM + human) | ROC-AUC | precision | recall | accuracy | human texts flagged |
|---|---|---:|---:|---:|---:|---:|
| **test** | 15 + 15 | **0.98** | **0.93** | **0.93** | 0.93 | 1 of 15 |
| dev | 16 + 16 | 0.98 | 0.93 | 0.88 | 0.91 | 1 of 16 |
| all | 31 + 31 | 0.98 | 0.93 | 0.90 | 0.92 | 2 of 31 |

Per-signal ROC-AUC on test: patterns 0.98, lists 0.78, paragraphs 0.73, triads 0.50, rhythm 0.46, em-dashes 0.43.
Almost all of the separation comes from the pattern catalog. Sentence rhythm, em-dashes and triads were close to chance or worse here, which is why their weights are small.

Hard cases, all splits: 1 of 5 formal/corporate human passages was flagged (the Library of Congress "about" page, caught by "Whether you are new to ... or"). 3 of 5 casual-prompted LLM passages were flagged (all 3 in test, neither in dev), mostly because of emoji and faux-candid lines like "here's the thing".

The two human false positives are both short texts with one heavy structural hit: h27 above, and h16, a Wikipedia paraphrase of a grandmaster saying Black plays "not just for equality, but for the advantage".

## Caveats

- **Small n.** On test, one sample moves recall or precision by about 7 points. The confidence intervals are wide.
- **One author on both sides of the circle.** A single LLM wrote both the LLM passages and the pattern catalog. The patterns describe exactly the habits that model has, so these numbers are optimistic. Text from other models, or LLM text a person has edited, will score lower.
- **Genre and format confounds.** The human side is mostly encyclopedia, government and pre-1927 essay prose. The LLM side is chat-style answers with headings and bold bullets. Some of the gap is format, not wording. The human excerpts also have fewer paragraphs (2-3, cut from longer articles) than the complete LLM answers (5-6), which inflates the paragraph signal.
- **Em-dashes are untested here.** These LLM passages use almost none, unlike many current chat models, and the older human prose uses plenty.
- **Casual or edited LLM text gets through.** Without the stock phrases there is little to catch.
- **English only.**
- **Not an authorship test.** A high score means "reads like default assistant prose", nothing more. Never use it to accuse someone of using AI.
