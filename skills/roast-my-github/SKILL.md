---
name: roast-my-github
description: Roasts a GitHub profile with humor, then gives genuinely useful advice. It analyzes public repos, stars, languages, missing descriptions and licenses, abandoned projects, lazy commit messages, and activity with a data script, and produces a shareable scorecard and a glow-up plan. Use when the user asks to roast, rate, review, or improve a GitHub profile, or asks "what does my GitHub say about me".
---

# Roast My GitHub 🔥

Funny enough to screenshot, useful enough to fix.

## 1. Get the facts (never roast from imagination)

```bash
python3 scripts/github_profile.py <username>          # human-readable facts
python3 scripts/github_profile.py <username> --json   # structured output
```

Set `GITHUB_TOKEN` (or `GH_TOKEN`) to avoid the 60 requests/hour anonymous rate limit. The script only uses **public** data.
If the user wants pinned repos or the profile README reviewed, ask them to paste them, because the REST API doesn't expose pins.

## 2. The roast (5–8 lines)

- Every joke must point at a **fact from the data**: "47 repos, 41 without a description. Your projects are in witness protection."
- Roast the code and the habits, **never the person**: no jokes about appearance, identity, nationality, age or employment.
- Make it playful, not cruel. End on something genuinely good you found.

## 3. Scorecard (/10 each, then an overall score)

| Area | Based on |
|---|---|
| Profile | bio, name, blog/site, profile README repo (`<user>/<user>`) |
| Repo hygiene | descriptions, licenses, topics, READMEs on top repos |
| Activity | recent pushes, stale repos, account age vs output |
| Impact | stars, forks, followers |
| Focus | language spread, clear theme vs scattered experiments |
| Commit craft | lazy commit messages ratio (if events available) |

## 4. Glow-up plan (top 5 fixes, highest impact first)

Concrete actions, e.g. "Add descriptions and topics to these 6 repos: …", "Archive these 9 stale repos", "Create a profile README (draft below)", "Pin these 4 repos".
Offer to draft a profile README, and offer to run `readme-glow-up` on the weakest README.

## 5. Share card

```text
🔥 My GitHub just got roasted: 6.4/10
"<best roast line>"
Roast yours → github.com/milad-ahmd/roast-my-github
```

## Rules

- Public data only. Never dig for personal information.
- When roasting someone else's profile, keep it friendly and suggest roasting only yourself or friends who agreed to it.
- Don't make up stats. If the API returns nothing, say so.
