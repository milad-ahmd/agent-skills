---
name: social-media-calendar
description: Builds a complete monthly cross-platform social media content calendar (Instagram, TikTok, YouTube, LinkedIn, X, Facebook, Pinterest, Threads) with themes, pillars, post ideas, drafts, formats, posting times, and campaign or holiday hooks, exported as Markdown and CSV for Notion, Google Sheets, Buffer, or Later. Use when the user asks for a content calendar, posting schedule, or monthly social media plan.
---

# Social Media Calendar

A month of content planned in one go, realistic for the team that has to produce it.

## 1. Intake

Brand, audience, goals and KPIs for the month, active platforms, team capacity (posts per week you can really produce), upcoming launches, events, promotions and the region (for holidays).

## 2. Plan the month

- A **monthly theme** plus a weekly sub-theme.
- **Pillars and their ratio**, for example 40% educate, 25% entertain, 20% community or proof, 15% promote.
- **Key dates**: launches, relevant holidays, awareness days and cultural moments for the user's region. Mark them `[VERIFY]` if the date might be wrong.
- Create one **hero piece** per week (a long video, blog post or live session) and **repurpose** it into smaller posts (see `content-repurposer`).

## 3. Calendar output

A Markdown table **and** a CSV file (`content-calendar-<yyyy-mm>.csv`):

```csv
date,time,platform,format,pillar,topic,hook,caption_draft,cta,hashtags,asset_needed,owner,status
2026-11-03,18:00,instagram,reel,educate,"...","...","...","Save this","#a #b","15s talking head",Sara,idea
```

Default posting times are only a starting point, so tell the user to replace them with the best times from their own analytics.

## 4. Production checklist

A list of assets to create grouped into a **batch-production day** (film all the Reels together, design all the carousels together), with deadlines set 2–3 days before publish dates.

## 5. Weekly check-in template

What was published, the top performer and why, what to change next week.

## Rules

- Never plan more content than the stated capacity. Consistency matters more than volume.
- Every post needs a purpose (a pillar) and a CTA.
- Import into Notion, Sheets, Buffer, Later or Metricool only through tools the user has connected, and after confirming with them.
