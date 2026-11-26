---
name: dev-wrapped
description: Builds a "Wrapped"-style year in review of a developer's own coding from local git history, filtered to their author emails and one calendar year. A read-only script counts commits, active days, the longest streak, the busiest day and month, hour-of-day and weekday patterns, night-owl and weekend share, lines added and removed without lockfiles or vendored code, top languages and repos, the biggest commit, the most edited file and favourite commit verbs, then picks an archetype such as The Night Owl or The Streak Machine and shows the rule behind it. It writes a shareable 1200x630 SVG card (dark or light) and a short markdown recap that leave out emails, repo names, paths and commit messages unless asked, can add public GitHub stats, and comes with a caption and goals for next year. Use when the user asks for their year in code, a GitHub or git wrapped, a coding year in review or recap, their commit stats for 2026, or a card to post about their year.
---

# Dev Wrapped

A fun recap of someone's year, built only from their real commits. Every number on the card must come from the script.

## 1. Find the repos and the emails

Ask where their code lives (one folder like `~/code`, or a few repos) and which emails they commit with: work, personal, and the GitHub noreply address (`<id>+<user>@users.noreply.github.com`). Without `--email`, each repo's own `git config user.email` is used, which misses commits made under an old address.

## 2. Run it

```bash
python3 scripts/wrapped.py --repos ~/code ~/work/api --email me@home.dev --email me@work.com
python3 scripts/wrapped.py --repos ~/code --year 2026 --json            # every stat
python3 scripts/wrapped.py --repos ~/code --svg wrapped.svg --theme light
python3 scripts/wrapped.py --repos ~/code --md                          # recap for a README or post
python3 scripts/wrapped.py --repos ~/code --github <user>               # add public GitHub stats
```

Folders are searched 4 levels deep (`--depth`), skipping hidden dirs, `node_modules`, `vendor`, venvs and build output. The year uses each commit's author date in its own timezone, so 23:30 in Tehran counts as 23:30. Merge commits are skipped. Lockfiles, vendored, minified, generated and binary files are left out of lines, languages and the biggest commit; JSON, YAML, TOML and XML count as lines but not as languages.

If the biggest commit is an asset dump or an imported project, rerun with `--exclude 'assets/*'` and tell the user why the numbers moved.

`--github USER` uses public endpoints only. GitHub currently answers 401 to the stargazer list without a token, so stars received this year need `GITHUB_TOKEN`. Without it the card shows all-time stars and labels them that way. Offline or rate limited, the rest of the report still works.

Exit codes: `0` report written · `1` git missing or output file not writable · `2` no repos found, or no commits by these emails in that year.

## 3. Show the card and the story

Open the SVG for the user, then walk through the five or six numbers that make the year interesting: the archetype and its rule, the streak, the busiest month and what shipped then (ask, don't guess), the hour chart, the top language. Point out anything odd, like 219 commits on one day, and ask whether it was a bulk import before celebrating it.

## 4. Privacy

By default the card and `--md` show only counts, languages, the archetype and the favourite verb. Repo names appear only with `--show-repos`; commit subjects and file paths only with `--show-details`. The text and JSON output stay local and do include repo names and subjects. Emails never appear in any output. Ask before adding names: client and employer repos are often confidential.

## 5. Caption for X or LinkedIn

Offer one or two short drafts in the user's own voice, built from one specific number and one honest detail:

> 1,334 commits this year, 35% of them after 10pm. The card calls me a night owl. My sleep tracker agrees.

Keep it plain: no "thrilled to share", no "journey", no stacked emoji, no hashtags beyond one or two. Let the user pick and edit.

## 6. What to do more of next year

Three suggestions, each tied to a number: a 2% early-morning share and 35% weekends might mean protecting weekends; `fix` as the top verb might mean more tests up front; one language at 80% might mean trying the one they keep mentioning. Frame them as options, not a scorecard.

## Do not

- Inflate or round numbers up, merge in other people's commits, or count a repo the user didn't confirm as theirs.
- Put repo names, file paths or commit messages on the card or in a post before the user agrees to it.
- Present stars or lines as a measure of skill. Lines added says little; say so if the user leans on it.
- Read private data beyond the local repos given. `--github` uses only public endpoints.
- Post or publish anything yourself. Hand the user the card and the caption.
