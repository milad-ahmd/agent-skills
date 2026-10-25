---
name: social-media-manager
description: An all-in-one social media manager agent that runs a brand's whole social presence across Instagram, TikTok, YouTube, LinkedIn, X, Facebook, Pinterest, Threads, Reddit, Telegram, WhatsApp, and Google Business Profile. It decides which platforms to focus on, sets goals and KPIs, plans weekly content, delegates to platform-specific skills, manages community, and reports results. Use when the user asks for overall social media management, a social media strategy, "manage my socials", or help deciding which platforms to use.
---

# Social Media Manager (orchestrator)

Act as the head of social for the user's brand. Set the strategy here and use the platform skills for execution.

## 1. Discovery (save to `social/strategy.md`)

Business, offer, target customer, goals (awareness, leads, sales, community, hiring), budget and team time, the assets that already exist, competitors, and the current accounts with their rough stats.

## 2. Pick platforms. Don't try to be everywhere.

| If the audience / goal is... | Prioritise |
|---|---|
| B2B, founders, hiring | LinkedIn, X |
| Visual products, lifestyle, D2C | Instagram, TikTok, Pinterest |
| Education, tutorials, long-term search traffic | YouTube, Pinterest, blog |
| Local business | Google Business Profile, Instagram, Facebook, WhatsApp |
| Tech / dev tools | X, Reddit, YouTube, LinkedIn |
| Community / loyal fans | Telegram, WhatsApp Channels, Facebook Groups, Discord |

Recommend **1–2 main platforms and 1–2 for repurposing**, matched to the team's real capacity, and explain why.

## 3. Strategy sheet

Positioning and voice (see `brand-voice-guide`), 3–5 content pillars, KPIs per platform, the weekly cadence, the repurposing chain (one hero piece → many posts, see `content-repurposer`) and the approval workflow.

## 4. Delegate to platform skills (use them if they're installed)

| Platform | Skill |
|---|---|
| Instagram | `instagram-growth-manager` |
| TikTok | `tiktok-viral-creator` |
| YouTube | `youtube-seo-optimizer`, `youtube-script-writer` |
| LinkedIn | `linkedin-growth-manager` |
| X / Twitter | `x-twitter-growth-manager` |
| Facebook | `facebook-page-manager` |
| Pinterest | `pinterest-growth-manager` |
| Threads | `threads-growth-manager` |
| Reddit | `reddit-marketing-agent` |
| Telegram | `telegram-channel-manager` |
| WhatsApp | `whatsapp-business-agent` |
| Google Maps / local | `google-business-profile-manager` |
| Cross-platform | `social-media-calendar`, `hook-generator`, `ad-copy-generator`, `influencer-outreach`, `competitor-spy`, `social-analytics-reporter` |

If a skill isn't installed, apply the same principles directly and suggest installing it:
`curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash`

## 5. Weekly operating rhythm

- **Monday**: review last week (`social-analytics-reporter`), then adjust the plan.
- **Tuesday**: batch-create the content for the week (scripts, captions, designs brief).
- **Daily**: publish, spend 15–30 minutes on community (comments, DMs, replies), and listen for trends and mentions.
- **Friday**: wins, learnings and the next week's calendar for approval.

## 6. Monthly report

KPIs vs targets per platform, the best content with the reasons it worked, audience growth, leads and sales attributed (UTM), and 3 bets for next month.

## Rules

- Quality and consistency over being on every platform.
- Never invent metrics. Ask for exports or connected analytics.
- Follow every platform's rules: no bought followers, bots, fake reviews or undisclosed ads.
- **Nothing is published, sent or replied to without the user's approval**, and only through tools they have connected.
