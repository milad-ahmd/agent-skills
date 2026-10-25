---
name: social-analytics-reporter
description: Turns raw social media analytics exports (Instagram Insights, TikTok Analytics, YouTube Studio, LinkedIn, X, Meta Ads CSV or screenshots) into a clear performance report with KPIs, trends, top and bottom content analysis, the reasons behind them, and prioritized recommendations. Includes a Python script to summarize CSV exports. Use when the user shares social media analytics, asks for a monthly social report, or asks "what's working on my socials".
---

# Social Analytics Reporter

From a pile of numbers to "here's what to do next week".

## 1. Collect data

Ask for exports: CSV from YouTube Studio, TikTok, Meta Business Suite, LinkedIn or a scheduler, or screenshots.
For CSVs, run the helper to get a quick numerical summary:

```bash
python3 scripts/summarize.py export.csv                # auto-detects numeric columns
python3 scripts/summarize.py export.csv --sort views   # rank posts by a metric
```

## 2. KPIs by goal

| Goal | Primary KPIs |
|---|---|
| Awareness | Reach, impressions, non-follower reach %, views, follower growth |
| Engagement | Engagement rate ((likes+comments+shares+saves)/reach), saves, shares, comments |
| Video | Avg watch time, % completion, retention, rewatches |
| Traffic / conversion | Profile visits, link clicks, CTR, signups, sales, CPA/ROAS (ads) |

Calculate rates, not just totals. Compare with the **previous period and the account's own average**, not with generic benchmarks.

## 3. Report (`social-report-<period>.md`)

```markdown
# Social Report — <period>
## TL;DR (3 bullets: win, problem, next action)
## KPI scorecard (metric | this period | last period | Δ% | status 🟢🟡🔴)
## Platform breakdown
## Top 5 & bottom 5 posts — and WHY (hook, format, topic, timing, length)
## Patterns (what formats/topics/times over-perform)
## Recommendations — do more / stop / test (with hypothesis + success metric)
```

## Rules

- Never invent or extrapolate numbers. If data is missing, say so.
- Mark correlations as correlations, and suggest a test to confirm the cause.
- Keep it short enough that a busy founder reads it.
