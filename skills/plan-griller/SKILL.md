---
name: plan-griller
description: Grills the user about a planned feature or code change before any code is written. It reads the relevant code and docs first so every question is specific to the repo, asks one decision at a time with a recommended answer, works through scope, behaviour, edge cases, data, failure modes and rollout, then writes a SPEC.md with numbered requirements, Given/When/Then acceptance criteria tied to them and a test plan, checked by a linter that flags vague words and untraced requirements. Use when the user asks to grill or interview them about a feature, pressure-test an implementation plan, write a spec or PRD for a change, or turn a rough feature request into testable requirements before coding.
---

# Plan Griller

Ask the questions a good reviewer would ask before the code exists, then write the answers down as a spec someone can test.

## 1. Restate the request

Say it back in one line: "You want agents to refund single line items so customers stop getting all-or-nothing refunds." Get a yes before going further. If it is really a business idea, hand it to `startup-idea-validator`. If it is meeting notes that need tickets, use `meeting-notes-to-tickets`. A framework or version upgrade belongs to `migration-wizard`.

## 2. Read before asking

Find the code and docs the change touches: models and migrations, routes and handlers, the UI that shows the data, existing tests, ADRs, open issues. Note what the repo already settles and show it in a few lines ("From the code: `orders.status` is free text; refunds go through `StripeClient.refund`; no idempotency keys anywhere"). Don't ask anything the repo answers. When the code and the user's description disagree, say so and ask which one is right.

Grounded questions are the point. "How should refunds be stored?" could be asked about any codebase. Compare it with: "`orders.status` is a free-text column with 6 values in prod. Should `refunded` stay a status, or should refunds get their own table so an order can have more than one? I'd use a table, because partial refunds mean several per order."

## 3. Grill

Ask one question at a time. Batch up to three only when the answers don't depend on each other. Each question carries your recommended answer and the reason, so the user can reply "yes":

```
Q4 (edge cases). An agent clicks Refund twice. Today `POST /refunds` has no idempotency key.
Recommend: require an Idempotency-Key header and keep keys for 24 h, because Stripe retries and
double clicks would otherwise refund twice. OK?
```

Work through the decisions in order: scope (who it is for, what is in, what is explicitly out), then behaviour on the main path, then edge cases, then data (schema, API and events, existing rows), then failure modes (a dependency down, timeouts, partial success, retries), and last rollout (flag, migration order, rollback, how you'll know it works). Settle a decision before asking anything that depends on it. There is no point asking about the refund table's indexes before deciding refunds get a table.

Keep a running decisions log as you go: each answer, plus the reason. Push back when it matters. A new feature that shows up mid-grilling becomes a non-goal or its own spec; say what it would add. When two answers contradict, quote both and ask which one wins. When an answer is vague ("fast", "handle errors"), ask for the number or the specific case. If the user doesn't know, record an open question with an owner and move on.

Questions people forget. Treat this as your own checklist and ask only the ones that apply:
empty state · permissions and roles · double submit and concurrent edits · idempotency and retries · time zones and DST · limits, pagination and large inputs · migrating existing data · logs, metrics and alerts · undo and audit trail · i18n and currency · accessibility (keyboard, screen reader).

## 4. Write SPEC.md

Fill in [references/spec-template.md](references/spec-template.md). Save it where the user says. Otherwise use `docs/specs/<slug>.md` if that folder exists, or `SPEC.md` at the repo root. Requirements use "must" and carry IDs (R1, R2...). Each acceptance criterion cites its requirement and reads Given / When / Then. The test plan maps every criterion ID to a named test. Sections that don't apply say "None:" and the reason. Numbers beat adjectives: "p95 under 300 ms", not "fast"; "50 items per page", not "paginated".

## 5. Lint, then confirm

```bash
python3 scripts/spec_lint.py SPEC.md --fail-on warning          # what you run before calling it done
python3 scripts/spec_lint.py docs/specs/refunds.md --json
python3 scripts/spec_lint.py SPEC.md --require problem,non-goals,requirements,acceptance,test-plan   # small change
```

Exit codes: `0` nothing at or above `--fail-on` (default `error`) · `1` findings at or above it · `2` file missing or has no headings.

| Rule | Severity | Catches |
|---|---|---|
| `missing-section`, `empty-section`, `placeholder` | error | a required section absent or blank, no non-goals, template text left in |
| `uncovered-requirement`, `orphan-criterion`, `unknown-reference` | error | a requirement with no criterion, a criterion citing no requirement, IDs that don't exist |
| `not-gwt`, `untested-criterion`, `duplicate-id` | error | a criterion without Given/When/Then, a criterion missing from the test plan, a reused ID |
| `question-without-owner`, `unresolved` | error | an open question nobody owns, TBD or TODO inside a requirement |
| `vague-term`, `weasel-modal`, `missing-id` | warning | "fast", "intuitive", "etc.", "as needed", "handle errors gracefully"; should/could/might in a requirement; an unnumbered item |

Fix findings by asking the user, not by inventing numbers. Speed words like "fast" pass once the same line has a number in it. The script reads markdown structure only. It can't tell whether a requirement is right, so a clean run means the spec is complete and traceable, not that it is correct.

Grilling ends when the linter is clean and the user confirms the spec, or earlier if the user says "good enough, write it". In that case write the spec with what you have, list the unresolved findings at the end, and record the gaps as open questions.

## 6. Offer next steps

Don't start them unprompted. Offer to split the requirements into tickets with `meeting-notes-to-tickets` (pass it the spec), to write the tests in the test plan first so they fail, or to start implementing against R1.

## Rules

- Don't write or change code while grilling. Reading code is expected.
- Never ask what the code already answers.
- Record every decision with its reason. "Because the user said so" is a valid reason; write it down anyway.
- Label recommendations as recommendations until the user accepts them. If the user skips a question, write your recommendation into the spec marked "assumed".
- "Skip", "next" and "good enough, write it" all work immediately. The user decides when grilling ends.
- Don't commit the spec or open tickets without asking.
