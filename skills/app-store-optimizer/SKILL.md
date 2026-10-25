---
name: app-store-optimizer
description: App Store Optimization (ASO) for the Apple App Store and Google Play, covering keyword research, title, subtitle, short description and keyword field within character limits, long description, screenshot captions and order, preview video script, localization, ratings prompts, review replies, and A/B tests. Use when the user asks to improve an app store listing, ASO, app keywords, screenshots, app descriptions, or app review responses.
---

# App Store Optimizer 📱

Rank for the right searches, and turn page visitors into installs.

## 1. Inputs

The app, its category, target users, core features, competitors (3–5 apps), current listing text (or a `store-listing*.md` / `fastlane/metadata` in the repo), current ratings and reviews, and the target countries and languages.

## 2. Keyword research

Look at competitors' titles and subtitles, App Store and Play search suggestions (the user pastes them or a tool fetches them), and review language (the words real users use).
Score each keyword on relevance (1–5), estimated competition and intent. Pick a **primary** keyword and 10–20 secondary ones.

## 3. Metadata (count the characters and show the counts)

| Field | Apple App Store | Google Play |
|---|---|---|
| Title / App name | ≤ 30 chars | ≤ 30 chars |
| Subtitle | ≤ 30 chars | — |
| Short description | — | ≤ 80 chars |
| Keyword field | ≤ 100 chars, comma-separated, no spaces, no repeats of title words | — |
| Description | ≤ 4000 (not indexed for search, written for conversion) | ≤ 4000 (indexed: use keywords naturally) |
| Promotional text | ≤ 170 (updatable without release) | — |

Write 3 options for the title, subtitle and short description. Never stuff competitor brand names (it breaks store guidelines).

## 4. Screenshots and video

- The first 2–3 screenshots decide most installs. Each one gets **one benefit-led caption** of 5 words or fewer, in large text.
- Order: core value → top feature → social proof or result → differentiators. Provide captions and layout notes for each.
- Preview video script (15–30 seconds): the app in use within the first 3 seconds, no long logo intro.

## 5. Ratings and reviews

Ask for a rating at a **happy moment** (after a success), using the native prompt APIs (SKStoreReviewController, Play In-App Review). Write reply templates for reviews: thank specifically, fix or explain, and invite them back. Mine the reviews for keywords and bugs.

## 6. Localize and test

Localize the metadata for the top markets (not just translate it; do keyword research per locale). A/B test with Apple Product Page Optimization and Google Play store listing experiments, changing **one element at a time**.

## Rules

- No fake reviews, incentivised ratings or misleading claims. Follow the App Store Review Guidelines and Google Play policies.
- Update listings only through the user's own access (App Store Connect, Play Console, fastlane) and after their confirmation.
