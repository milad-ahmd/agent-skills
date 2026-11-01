---
name: agent-guardrails
description: Installs and tests guardrails that stop a coding agent from running destructive or leaking shell commands. Adds a Claude Code PreToolUse hook on Bash that denies or asks before force pushes to main/master, pushes and commits on main, git reset --hard and git clean -fdx, rm -rf of /, ~, .. or paths outside the project, reading .env files, SSH keys and cloud credentials, curl or wget piped to bash, DROP TABLE and TRUNCATE through psql/mysql/sqlite3, chmod -R 777, and --no-verify. Parses chains, pipes, sudo/env wrappers, bash -c and $(...) and ships a self-test table. Merges into settings.json with a backup and a clean uninstall, and gives the same hook for Cursor and Codex or a plain rule list for other agents. Use when the user asks to add git guardrails or safety hooks to Claude Code, block dangerous or destructive commands, stop the agent from force pushing, deleting files with rm -rf or reading .env secrets, protect the main branch from the agent, or set up a PreToolUse hook that blocks commands.
---

# Agent Guardrails

A seatbelt for coding agents: a hook that reads each shell command before it runs and denies or asks on the handful of commands that cause real damage. It reads command text only, so it is not a sandbox. Treat it as one layer next to permission rules, branch protection and backups.

## 1. Show the rules and run the self-test

```bash
python3 scripts/guardrails.py rules                 # id, decision (deny or ask), what it blocks, why
python3 scripts/guardrails.py test                  # ~60 sample commands, dangerous ones and harmless look-alikes
python3 scripts/guardrails.py check "git push -f origin main"   # one command: exit 0 allow, 3 ask, 4 deny
```

`deny` blocks the call and the reason goes to the agent. `ask` shows the user a permission prompt with the reason. Harmless commands get no output, so Claude Code's normal permission flow still applies (the hook never auto-approves anything).

Tell the user which rules will be on, and ask about the ones that may be noisy for them: `commit-protected` asks on every commit while on main, which suits teams but annoys solo repos; `rm-outside-project` denies `rm -rf ../sibling-repo`.

## 2. Install (Claude Code)

```bash
python3 scripts/guardrails.py install --dry-run                    # show the plan, write nothing
python3 scripts/guardrails.py install                              # .claude/settings.json (shared with the team)
python3 scripts/guardrails.py install --scope local                # .claude/settings.local.json (just this user)
python3 scripts/guardrails.py install --scope user                 # ~/.claude/settings.json (every project)
python3 scripts/guardrails.py install --disable commit-protected --protect develop,release/*
```

`install` copies the script to `.claude/hooks/guardrails.py` (or `~/.claude/hooks/`) and adds one entry under `hooks.PreToolUse` with `"matcher": "Bash"` and the command `python3 "${CLAUDE_PROJECT_DIR}"/.claude/hooks/guardrails.py hook` (the user scope uses an absolute path). Existing settings and hooks are kept, the old file is saved as `settings.json.bak-<timestamp>`, and a re-run with the same options changes nothing. A settings file that is not valid JSON is left untouched (exit 2). `uninstall` removes only this entry; `--remove-script` also deletes the copy.

Claude Code reloads settings files when they change. Have the user open `/hooks` to see the entry, then try one blocked command.

## 3. Add the permission rules the hook can't cover

The hook only sees Bash. Claude's own Read tool can still open `.env`, so suggest these deny rules in the same settings file:

```json
"permissions": { "deny": ["Read(./.env)", "Read(./.env.*)"] }
```

Mention that `.env.example` is then also blocked from Read; add an `allow` for it if the user needs it.

## 4. Other agents

Cursor and Codex both have documented command hooks, so the same script works there with `hook --format cursor` or `hook --format codex`. The setup steps are in [references/other-agents.md](references/other-agents.md). For Cline, Windsurf, Copilot, Aider or anything else, `python3 scripts/guardrails.py rules --text` prints the rules as plain instructions to paste into `AGENTS.md`, `.clinerules` or the agent's rules file. Say clearly that this is advice the model can ignore, not enforcement.

## 5. Report

Which rules are on, the scope and files changed (settings path, backup path, script path), the `test` result line, and the known gaps below.

## Known gaps (tell the user)

It parses `;`, `&&`, `||`, `|`, newlines, quotes, heredocs, `sudo`/`env`/`timeout`/`xargs` wrappers, `bash -c`, `eval`, `$(...)`, backticks and `<(...)`. It errs toward allowing: anything it can't resolve is allowed. It does not see what happens inside scripts, Makefiles, npm scripts or git aliases (`npm run clean`, `./deploy.sh`), `find -delete` or `find -exec rm`, paths built from unset variables (`rm -rf "$DIR/"`), recursive grep that happens to pass through `.env`, `printenv`, interpreters reading files (`python -c "open('.env')"`), a download saved to disk and then run, or remote commands over `ssh`. A user who wants a hard boundary needs a container or VM, branch protection on the remote, and scoped credentials.

Security scanners (prompt-injection-scanner included) flag `scripts/`: its rule table and tests have to contain the commands the hook blocks. SKILL.md and `references/` scan clean.

## Don't


Don't install at user scope or into the shared `.claude/settings.json` without saying so, because the shared file affects everyone who pulls it. Don't hand-edit settings.json to add the hook when `install` can merge it, and never delete the user's other hooks. Don't present the hook as a security boundary, and don't weaken a rule quietly to get a command through: if a deny is wrong, say which rule fired and let the user decide on `--disable`.
