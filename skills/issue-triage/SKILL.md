---
name: issue-triage
description: Triages a repository's open GitHub issues the way a careful maintainer does. A read-only script pulls the open issues with gh (or reads saved gh JSON) and lists duplicate candidates by TF-IDF similarity with the score and shared terms, bugs missing a version, steps to reproduce, expected vs actual or logs, stale issues and whether each one waits on the reporter or on maintainers, suggested labels (bug, feature, question, docs, good first issue), and a P1/P2/P3 priority from severity, reactions, commenters and duplicates, ranked by what needs attention. Produces a triage summary and proposed replies. Labels, comments and closing happen only after the user approves each batch of gh commands shown first; it never closes anything on its own. Use when the user asks to triage GitHub issues, go through the issue backlog or tracker, find duplicate issues, label or prioritise open issues, find bugs that need a repro or more info, chase stale issues waiting on reporters, or decide which issues to fix first.
---

# Issue Triage

Triage means sorting the open issues so maintainers spend time where it matters: merge duplicates, get repro details for real bugs, nudge or flag stale threads, label, and rank. The script finds candidates. You confirm each one by reading the issue, and the user approves every write.

## 1. Collect the issues (read-only)

```bash
python3 scripts/triage.py --repo OWNER/NAME            # runs gh issue list itself, prints the report
python3 scripts/triage.py --repo OWNER/NAME --json     # full data for every issue
gh issue list --repo OWNER/NAME --state open --limit 500 \
  --json number,title,body,labels,comments,reactionGroups,createdAt,updatedAt,author,url > issues.json
python3 scripts/triage.py issues.json --days 60 --threshold 0.4
gh label list --repo OWNER/NAME --limit 200            # the labels this repo actually uses
```

gh names the reactions field `reactionGroups`; `--json reactions` is rejected. The script also reads files that use `reactions` (GraphQL groups or a REST `{"total_count": N}`), `user` instead of `author`, or a comment count. Options: `--days` (stale after, default 30), `--threshold` (duplicate score, default 0.35; lower it for small repos), `--top` (attention list length), `--max` (lines per text section), `--now`. A warning on stderr means `--limit` was reached and older issues are missing.

Exit codes: `0` report printed · `2` bad JSON, unreadable file, or gh failed (not installed, not logged in, no access). The script never writes to GitHub.

## 2. Check every candidate by reading it

The report is keyword and similarity based. Before you put anything in the triage report, open the issue: `gh issue view N --repo OWNER/NAME --comments`.

**Duplicates.** Two issues are duplicates when they share a root cause, not just an area. Two crashes in the same command can have different causes. Keep the older issue as the original, or the one with the repro and the discussion. A pair that shares only generic terms is a false positive, so drop it.

**Missing info.** This applies to real bugs only. The detail may sit in a screenshot or a linked gist the script can't read. Ask only for what is really missing, in the project's own terms: `acme --version` rather than "your version".

**Stale.** "waiting on reporter" means a maintainer asked something and got no answer. "never answered" and "waiting on maintainers" are the project's debt. They go in the attention list, never in a close list.

**Labels.** Map each suggestion to a name from `gh label list`, since `feature` may be called `enhancement` or `kind/feature` there. If the repo has no matching label, say it would need creating.

**Priority.** P1 is a severe bug (crash, data loss, security, regression) or a bug that many people hit. P2 has real impact or demand; a much-wanted feature tops out here. P3 is the rest. When the text says more than the score, adjust it and say why.

## 3. Write the triage report

Start with the needs-attention list: issue, priority and a one-line reason. Then the confirmed duplicates (`#117 duplicates #102`) with the evidence you read, the bugs that need a repro with what is missing, the stale issues split into waiting-on-reporter and unanswered, and the labels to add or remove.

End with one drafted reply per issue that needs a comment. Keep it short and polite. Thank the reporter, say what is needed or where the discussion continues, and promise no dates or fixes. A nudge on a stale issue asks whether the problem still happens on the latest release.

## 4. Apply only what the user approves

Group writes into batches (labels, comments, closes) and show the exact commands first:

```bash
gh issue edit 117 108 --repo OWNER/NAME --add-label "enhancement"
gh issue comment 108 --repo OWNER/NAME --body-file replies/108.md
gh issue close 117 --repo OWNER/NAME --duplicate-of 102     # only if the user asked to close duplicates
```

Run a batch only after the user says yes to that batch; approval of one batch does not cover the next. Write reply bodies to files and use `--body-file` so quoting can't break them. If `gh issue close --help` has no `--duplicate-of` (older gh), comment with the link to the original and close with `--reason "not planned"`. After running, list what changed with links.

## Do not

- Close, lock, transfer or delete issues, or remove labels, unless the user approved that exact batch.
- Close a stale issue on your own. A stale issue still waiting on maintainers is the project's debt.
- Call something a duplicate from the score alone, or close the older, better issue in favour of a newer one.
- Create labels, milestones or projects, or @mention people, unless the user agreed to it.
- Promise fixes, timelines or releases in drafted replies.
- Debug the bug itself here; triage ends at a well-described, labelled, ranked issue.
