# Spec: <feature name>

Status: draft · Author: <name> · Date: <YYYY-MM-DD> · Related: <issue, PR or doc links>

## Problem

<What is wrong or missing today, who feels it, and the evidence: tickets, metrics, quotes. Two to five sentences.>

## Goals

- <Outcome this change must achieve, measurable if possible>

## Non-goals

- <Something a reader might expect this change to do that it will not do, and why>

## Users and actors

- <Role or system>: <what they do in this flow and what they are allowed to do>

## Requirements

<!-- One behaviour per line. Name the actor and the observable result. -->

- **R1** <Actor> must be able to <action> when <condition>; the system <observable result>.
- **R2** The system must <behaviour>, within <limit>.

## Acceptance criteria

<!-- One or more per requirement. Cite the requirement in parentheses. -->

- **AC1** (R1) Given <starting state>, when <action>, then <observable result>.
- **AC2** (R1) Given <a different starting state>, when <action>, then <result>.
- **AC3** (R2) Given <state>, when <action>, then <result>.

## Edge cases and error states

<!-- Empty state, permissions, double submit, concurrency, limits, time zones, partial failure. Each one says what the user sees. -->

- <Case>: <what happens and what the user sees>

## Data and API changes

<!-- Tables, columns, enums, indexes, endpoints, events, payloads. Mark breaking changes. -->

- <Change>: <type, default, nullability, who reads it>

## Rollout, migration and flags

- Flag: <name, default, who gets it first>
- Migration: <what happens to existing rows or clients, backfill plan, how long it takes>
- Rollback: <how to undo it and what is lost>
- Observability: <log, metric or alert that shows it working or failing>

## Risks

- <Risk>: <likelihood, impact, mitigation>

## Open questions

<!-- Each with an owner and, if possible, a date. Write "None." when everything is decided. -->

- **Q1** <question> (owner: <name>, by <date>)

## Decisions

<!-- What was decided during grilling, and why. Keeps the reasoning when people ask later. -->

- <Decision>, because <reason>.

## Test plan

| Criterion | Test | Level | Where |
|---|---|---|---|
| AC1 | <test name> | unit | <path> |
| AC2 | <test name> | integration | <path> |
| AC3 | <test name> | e2e | <path> |
