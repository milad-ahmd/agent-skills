---
name: bus-factor
description: Measures a repository's bus factor (truck factor) and plans the knowledge transfer. A read-only script runs git blame and git log on tracked source files, skipping lockfiles, vendored and generated code, to see who owns each file's lines and who touched it recently. It flags knowledge silos where one person owns 80% or more, scores knowledge at risk per file and directory, lists what only each person knows, and finds the fewest people whose departure would leave over half the code with nobody active who knows it. It honours .mailmap, keeps people anonymous (Author A, B, C) unless asked, and writes JSON and a shareable SVG card. The agent then proposes a handover for the riskiest areas: pairing, walkthrough docs, CODEOWNERS review rotation and tests. Use when the user asks for the bus factor or truck factor, which code only one person knows, knowledge silos or single points of failure, code ownership concentration, what happens if someone leaves or quits, or a knowledge transfer or handover plan.
---

# Bus Factor

"Our bus factor is 1" is a joke until that person goes on leave. This skill finds the code only one person knows, puts a number on it, and turns the number into a handover plan.

## 1. Run the scan

```bash
python3 scripts/bus_factor.py                      # headline, riskiest files and dirs, "only you know"
python3 scripts/bus_factor.py src/ services/api    # limit to paths
python3 scripts/bus_factor.py --svg bus-factor.svg # shareable card, no names
python3 scripts/bus_factor.py --json               # every file, dir and person
python3 scripts/bus_factor.py --days 180 --alias "jdoe@laptop.local=Jane Doe" --max-files 5000
```

The script only reads. It blames each text file at HEAD with `git blame --line-porcelain -w` (whitespace-only changes keep the original author) and reads `git log --use-mailmap` for the last `--days` (default 365). Git applies `.mailmap` to both. `--alias FROM=TO` merges an email or name into the person named TO. If the repo sets `blame.ignoreRevsFile`, git skips those formatting commits too. It blames the 2,000 largest files by default and prints a `Capped:` line when it stops early. `--now YYYY-MM-DD` fixes the window end so runs can be compared. Docs are skipped unless `--include-docs`.

Exit codes: `0` report written · `2` not a git repository, no commits, a git error, or nothing to scan.

## 2. Explain the headline

The rules are also printed in the JSON under `rules`:

| Term | Meaning |
|---|---|
| knows a file | owns at least 20% of its lines and committed anywhere in the repo during the window, or made 2 or more commits to that file in the window (one typo fix doesn't count) |
| single owner | one person owns at least 80% of the lines and at most one other person touched the file in the window; knowledge at risk = lines x owner share |
| orphaned | nobody active knows the file, so the knowledge has already left |
| bus factor | the smallest number of people whose departure leaves more than 50% of the scanned lines orphaned |

Small teams get an exact search over combinations of people. Bigger ones fall back to the greedy loop of Avelino et al., "A novel approach for estimating truck factors" (ICPC 2016): remove the top author until less than half the code is covered. Their top author is whoever authors the most files and authorship comes from a degree-of-authorship model; here it is lines known, from blame plus recency. The report names the method; a greedy result is an upper bound.

Say what the number means for this repo in one or two sentences, for example "if Author A and Author B both left, 58% of the code would have nobody active who knows it". Point out the "already orphaned" share and any owners with no commits in the window, because that knowledge is already gone. Mention when the scan was capped.

## 3. Pick the 3-5 riskiest areas

Rank directories and files by knowledge at risk x churn (commits in the window, in the JSON). Code that is risky and changes often comes first. Risky code nobody touches can wait for a walkthrough doc. Read the top files before you plan anything: what they do, how they are tested, and what breaks if they are wrong (payments, auth, data migrations, deploy scripts).

## 4. Propose a handover per area

For each area, give concrete steps that fit in the next sprint:

1. Pairing: two or three sessions where the owner and a second person (the user picks who) change something real in that area together. Suggest a small open ticket to use.
2. A walkthrough doc, one page per area, written by the second person after pairing and reviewed by the owner. It covers what the code does, its entry points and invariants, the known traps, and how to test it locally.
3. Review rotation through a CODEOWNERS change that adds a second reviewer. Show the diff and do not apply it.
4. Tests where behaviour lives only in the owner's head (no tests, or only happy-path tests). List the cases the owner should explain so someone else can write them.

```
-/billing/      @owner-handle
+/billing/      @owner-handle @second-handle
```

GitHub, GitLab and Bitbucket all read a CODEOWNERS file, but where it lives and its syntax differ between them, so check the host's docs before suggesting the exact path.

Then say how to measure progress: re-run in a quarter with the same `--days` and compare the bus factor, the single-owner share and the riskiest directories.

## Report

The headline and the method, the 3-5 areas with their numbers (lines, owner share, churn, knowers), the handover steps per area, and the CODEOWNERS diff. Use the Author labels unless the user asked for names. If they want the card, give the SVG path.

## Do not

- Name people unless the user asks with `--show-authors`, and never publish a named report or card without their go-ahead.
- Rank, blame or shame individuals. A single owner usually means someone built something nobody else was given time to learn; the fix is time and pairing.
- Treat line counts or blame share as performance. Blame counts surviving lines, not effort, design work, reviews or mentoring.
- Apply CODEOWNERS or branch-protection changes, or open tickets, without the user asking.
- Read a high bus factor as proof that knowledge is shared. Recent commits are a proxy for knowing the code, not a test of it.
