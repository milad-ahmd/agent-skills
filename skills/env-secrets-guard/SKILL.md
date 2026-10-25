---
name: env-secrets-guard
description: Scans a repository and its git history for leaked secrets (API keys, tokens, passwords, private keys), fixes .gitignore and env handling, and guides safe key rotation. Use before pushing, before open-sourcing a repo, or when the user asks to check for leaked keys or secrets.
---

# Env Secrets Guard

Stop secrets from leaving the machine, and clean up properly if they already have.

## 1. Scan

Run the bundled scanner first. It needs only Python 3 and never prints full secret values:

```bash
python3 scripts/scan_secrets.py .             # tracked files
python3 scripts/scan_secrets.py . --history   # + every commit in git history
```

For deeper coverage, also use a dedicated scanner if one is installed:

```bash
gitleaks detect --source . --redact -v      # working tree + history
trufflehog git file://. --only-verified     # verified live secrets
```

Otherwise, search manually (working tree **and** history):

```bash
git grep -nIE '(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,}|xox[baprs]-[A-Za-z0-9-]+|AIza[0-9A-Za-z_-]{35}|sk_live_[0-9a-zA-Z]{24,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'
git log -p --all -S 'PRIVATE KEY' --oneline | head -50
git ls-files | grep -iE '(^|/)\.env(\..*)?$|\.pem$|\.p12$|\.keystore$|id_rsa|credentials|service-account.*\.json'
```

Also check: hard-coded `password=`/`secret=`/`token=` values, connection strings with credentials embedded, `google-services.json`/`GoogleService-Info.plist` and mobile signing files.

## 2. Report (never print full secrets)

Show at most the first 4 characters followed by `****`, plus the `file:line` and, for history hits, the commit.
Classify each hit: 🔴 live credential · 🟠 likely real · 🟢 test/example/placeholder.

## 3. Fix

- Move the values into environment variables or a secret manager, and load them through the project's existing config pattern.
- Add a `.env.example` with variable names and empty values. Add `.env*` (except the example) and key files to `.gitignore`.
- Untrack committed files without deleting them locally: `git rm --cached <file>`.

## 4. If a secret was ever pushed

1. **Rotate or revoke the key first.** History rewriting does not un-leak a key that has already been pushed.
2. Only then, if the user wants it, rewrite history with `git filter-repo` or BFG. Explain that this requires a force-push and that every collaborator must re-clone. **Never force-push without explicit confirmation.**

## 5. Prevent

Offer to add a pre-commit hook (for example `gitleaks protect --staged`) and a CI secret-scanning step.

## Rules

- Never print, log or commit full secret values. Always redact them.
- Rotate first, clean up second. Never force-push or rewrite history without explicit confirmation.
- Treat anything ambiguous as real until the user confirms it's a placeholder.
