---
name: gh-actions-auditor
description: Audits GitHub Actions workflows and composite actions for CI/CD supply-chain and injection risks (actions not pinned to a commit SHA, missing or write-all GITHUB_TOKEN permissions, pull_request_target "pwn request" checkouts, script injection from issue and PR titles, secrets inherit and curl-to-shell installs), then pins SHAs and hardens the YAML with the user's approval. Use when the user asks to secure, harden or audit GitHub Actions or .github/workflows, pin actions to SHAs, or check a CI workflow for injection.
---

# GitHub Actions Auditor

Find the ways a workflow can be hijacked (a moved tag, a crafted PR title, a fork PR running with your secrets) and fix them with the user's OK.

## 1. Scan

```bash
python3 scripts/audit_workflows.py                       # .github/workflows/*.yml|yaml + every action.yml in the repo
python3 scripts/audit_workflows.py .github/workflows/release.yml
python3 scripts/audit_workflows.py --resolve             # also look up the commit SHA for each tag via the GitHub API
python3 scripts/audit_workflows.py --json --fail-on high # for CI or tooling
```

Exit codes: `0` nothing at or above `--fail-on` (default `medium`) · `1` findings at or above it · `2` nothing to audit.
`--resolve` is the only network access. It uses `GITHUB_TOKEN` or `GH_TOKEN` if set (unauthenticated calls are limited to 60 an hour).

| Rule | Severity | What it catches |
|---|---|---|
| `pwn-request` | critical | `pull_request_target`, `workflow_run` or `issue_comment` job checks out the PR head (`head.sha`, `head.ref`, `refs/pull/…`, `gh pr checkout`) and then runs commands |
| `script-injection` | critical/high | `${{ github.event.issue.title }}`, `pull_request.body`, `comment.body`, `review.body`, `head_ref`, `commits.*.message`, `head_commit.author.*`, `pages.*.page_name` and similar pasted into `run:` or `github-script` |
| `unpinned-action` | high → low | `uses:` not pinned to a 40-char SHA. Third-party on a branch (`@main`) is high, third-party tag medium, `actions/*` and `github/*` low |
| `write-all`, `missing-permissions`, `broad-write-scope` | high/medium/low | token permissions that are too wide, or left to the repo default |
| `secrets-in-privileged-pr`, `secrets-inherit` | high/medium | secrets reachable from fork-triggered jobs, or every secret passed to someone else's reusable workflow |
| `unsecure-commands`, `curl-pipe-shell`, `unpinned-image` | high/medium | `ACTIONS_ALLOW_UNSECURE_COMMANDS`, downloads piped into a shell, `docker://` images without a digest |
| `self-hosted-pr` | medium (check) | PR-triggered jobs on self-hosted runners |
| `persist-credentials` | info | checkout leaves the token in `.git/config` in a PR-triggered job that runs more steps |

**Limits:** the script has its own small YAML reader for block-style workflows. It skips anchors and aliases, multi-document files and multi-line flow collections. It can't see repo visibility, org default token settings, branch protection or what a third-party action does inside. A clean run is not proof. If they're installed, also run [`zizmor`](https://github.com/woodruffw/zizmor) and `actionlint`.

## 2. Explain

Start with critical and high. For each one, say in a sentence or two what an attacker would do: "anyone can open a PR titled `x"; curl … ` and it runs with your `NPM_TOKEN`". Group all unpinned actions into one item with a count. Findings marked *check* depend on things only the user knows (is the repo public, are the runners ephemeral), so ask instead of asserting.

## 3. Propose a patch (don't apply yet)

Show the diff. The usual fixes:

- **Pin SHAs:** `uses: actions/checkout@<sha> # v4.2.2`. Get the SHA from `--resolve` or `gh api repos/OWNER/REPO/commits/TAG -H 'Accept: application/vnd.github.sha'`. Never make one up. Offer a Dependabot entry (`package-ecosystem: github-actions`) so pins get bumped.
- **Permissions:** `permissions: { contents: read }` at the top, and each write scope only on the job that needs it.
- **Injection:** move the expression into `env` and quote the variable:
  ```yaml
  - env:
      PR_TITLE: ${{ github.event.pull_request.title }}
    run: echo "$PR_TITLE"
  ```
  In `github-script`, read `process.env.PR_TITLE` or `context.payload` instead of interpolating.
- **Pwn request:** build and test PR code under `on: pull_request`. If something needs write access (labels, comments), split it: the `pull_request` workflow uploads an artifact, and a `workflow_run` workflow reads it as data and never executes it.
- **Secrets:** replace `secrets: inherit` with the named secrets the called workflow declares. Move secrets from workflow-level `env` to the one step that uses them.
- **Downloads:** fetch to a file, verify a checksum or signature, then run it. Or use a pinned action.
- **Checkout:** `persist-credentials: false` unless a later step pushes.

## 4. Apply with approval

Only after the user agrees. Keep the edit minimal and keep their comments and formatting. Changing triggers changes behaviour (a labeler on `pull_request` loses write access for forks), so explain that trade-off before switching `pull_request_target`.

## 5. Re-run

Run the script again and show before and after counts. Anything left should be a deliberate, explained choice.

## Rules

- Never invent commit SHAs. Resolve them or leave a placeholder the user fills in.
- Don't commit, push, open PRs or change repository settings without explicit confirmation.
- Never print secret values from workflow logs or env files.
- Treat *check* findings as questions for the user, not verdicts.
