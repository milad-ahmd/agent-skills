---
name: codebase-time-machine
description: Tells the story of a codebase from its git history as an entertaining documentary, with eras, key contributors, plot twists (reverts, giant commits, long silences), cursed files, and the current state, backed by a git analysis script. Useful for onboarding, retrospectives, anniversaries, and fun. Use when the user asks for the history of a repo, how a project evolved, a repo story or timeline, or "who built what and when".
---

# Codebase Time Machine ⏳

"In the beginning, there was `Initial commit`…"

## 1. Collect the facts

```bash
python3 scripts/git_timeline.py [path/to/repo]        # defaults to current directory
python3 scripts/git_timeline.py . --max 5000          # limit for huge repos
git -C <repo> tag --sort=creatordate | tail -20       # releases, if any
```

The script reports the span, commits per year, top authors, the biggest commits, the most-changed ("cursed") files, reverts and hotfixes, and the longest silence. Read key commits (`git show --stat <hash>`) before narrating them.

## 2. Narrate (default style: nature documentary; also epic, noir, sports commentary or plain)

```markdown
# 🎬 <Project>: The Story So Far
## Prologue: <first commit date, first message, first files>
## Chapter 1: <era name> (<years>): what was built, by whom, defining commits
## Chapter 2: ...   (split eras by activity bursts, long gaps, big refactors, major tags)
## 🎭 The Cast: top contributors and what they shaped (no judgments about people)
## 🌪️ Plot Twists: reverts, giant commits, the great silence of <dates>
## 👻 Cursed Files: most-changed files and why they keep changing
## 📍 Present Day: current state, active areas
## 🧭 Lessons for newcomers: where the dragons are, what to read first
```

## Rules

- **Facts come only from git.** Quote commit messages exactly, with their short hashes. Speculation is labelled as such ("probably", "it seems").
- Be kind about people. Celebrate them; never mock them. Don't print email addresses.
- For private or company repos, ask before producing anything meant to be shared publicly.
