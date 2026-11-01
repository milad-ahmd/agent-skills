---
name: skill-finder
description: Recommends an existing agent skill for a job the user describes, so nobody rebuilds what is already published. Notes which ones are already present so it never suggests a duplicate, ranks this collection's bundled catalog with a BM25 search script, and falls back to the skills.sh directory and leaderboard with `npx skills find`. Presents the best 1 to 3 matches with the reason it fits, its publisher and popularity, and the exact command to add it, then adds it only after the user says yes. Use when the user asks "is there a skill for this", wants to find, look up, discover, browse or get a recommendation for a skill or plugin that does some job, asks which skill to use for a task, or asks which skills they already have.
---

# Skill Finder

Match the user's task to a skill that already exists, then install it only with their OK. To write a new skill, use skill-creator-pro. To lint skills the user already has, use skills-doctor.

## 1. Pin down the task

Restate the task in a few concrete words that name the job and the tools or stack involved. "Make my agent better" is too vague to search, so ask one question first. "Turn meeting notes into Jira tickets" is enough.

## 2. Check what is installed

```bash
python3 scripts/find_skill.py --installed            # name, agent, path
python3 scripts/find_skill.py --installed --json
```

It looks in the folders the `skills` CLI installs to. For Claude Code that is `~/.claude/skills` and `.claude/skills`. Codex, Cline and several others share `~/.agents/skills` and `.agents/skills`. It also checks the global folders that Cursor (`~/.cursor/skills`) and five other agents use, plus Claude Code plugin skills under `~/.claude/plugins/cache`, which can hold old versions. If a fitting skill is already installed, say so and use it instead of suggesting it again.

## 3. Search this collection

```bash
python3 scripts/find_skill.py "turn meeting notes into jira tickets"
python3 scripts/find_skill.py "my docker image is 2GB" --top 3 --json
```

The script ranks the bundled `index.json` that sits next to it, using BM25 over each skill's description, tagline and example prompts, plus a short alias list (a query with "rebase" also searches git and commit history). Each result shows a score, the matched words, and where the skill is installed or how to install it. Options: `--top N` (default 5), `--min-score` (default 2.0), `--project DIR`, `--index FILE`.

Exit codes: `0` results · `1` nothing above the minimum, and it prints an `npx skills find` query to try · `2` usage error or unreadable index.

The ranking is lexical. Read each summary and judge the fit yourself, because one shared word ("plan", "email") can score well without being a match.

## 4. Search the wider ecosystem if nothing fits

The `skills` CLI from vercel-labs searches the skills.sh directory. It needs Node.js 22.20 or later.

```bash
npx skills find meeting notes jira          # prints owner/repo@skill, install count, skills.sh link
npx skills find react --owner vercel        # only one owner's repositories
npx skills add owner/repo --list            # list a repo's skills without installing
npx skills use owner/repo@skill             # print the skill as a prompt, nothing installed
```

Prefer skills with many installs from a publisher you can identify. Call anything under about 100 installs, or from an unknown account, unvetted.

## 5. Present 1 to 3 options

Give each one a line on what it does and why it fits this task. Add its source (this collection, or `owner/repo` with the install count), whether it is installed, and the install command. Recommend one and ask before doing anything:

```
1. dockerize-anything (this collection), recommended
   Writes a small multi-stage Dockerfile and compose file; fits "my image is 2GB" directly.
   Install: curl -fsSL -o /tmp/agent-skills-install.sh https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh && bash /tmp/agent-skills-install.sh dockerize-anything

Install it?
```

## 6. Install after a yes

From this collection, use the command the script printed. The installer copies into `~/.claude/skills` by default (set `SKILLS_DIR=.claude/skills` for one project). Other agents can use `npx skills add milad-ahmd/agent-skills --skill <name>`, and Claude Code users can add the whole collection with `/plugin install all-skills --marketplace milad-ahmd/agent-skills`.

For a third-party skill, read its SKILL.md and scripts first, on GitHub or with `npx skills use`. Tell the user what it runs and what it can touch, and suggest running prompt-injection-scanner on it. Then run `npx skills add owner/repo@skill`, with `-g` to install for every project and `-a claude-code` to target one agent. Add `-y` only when the user asked to skip the prompts.

Afterwards, confirm it landed with `--installed`, and tell the user a new session may be needed before the agent picks it up.

## Rules

Never install without an explicit yes, and install one skill at a time unless the user names several. Don't call a third-party skill reviewed or safe if you haven't read it; say who publishes it and how many installs it has. If nothing fits, say so and offer skill-creator-pro rather than stretching a weak match. Ask before replacing an installed skill, because the collection's installer overwrites a folder with the same name.
