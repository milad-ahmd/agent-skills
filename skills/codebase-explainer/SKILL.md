---
name: codebase-explainer
description: Produces an onboarding guide for an unfamiliar repository with an architecture overview, Mermaid diagrams, entry points, data flow, key modules, and the "dangerous" files to touch carefully. Use when the user asks to explain a codebase, onboard to a repo, map the architecture, or "where do I start?".
---

# Codebase Explainer

Turn "I just cloned this and I'm lost" into "I know where everything lives" in 10 minutes.

## 1. Explore (breadth first, read-only)

- Folder tree (2–3 levels, ignoring `node_modules`, `.git`, `dist`, `vendor`, `.venv`).
- Manifests, README, docs, CI workflows, Dockerfiles, env examples.
- **Entry points**: `main`, `index`, `app`, `server`, CLI bins, route definitions, `cmd/`, mobile `App`/`main.dart`.
- Data layer: ORM models, schema or migrations, API clients.
- Hotspots: `git log --since=1.year --name-only --pretty=format: | sort | uniq -c | sort -rn | head -20`.

## 2. Write `ARCHITECTURE.md` (or show it inline if the user prefers)

```markdown
# Architecture of <project>

## TL;DR
3 sentences: what it does, main tech, how it's deployed.

## Map
| Path | Responsibility |

## How a request flows
​```mermaid
sequenceDiagram
  Client->>API: POST /orders
  API->>Service: createOrder()
  Service->>DB: insert
​```

## Components
​```mermaid
flowchart LR
  web[Web app] --> api[API] --> db[(Postgres)]
​```

## Key concepts & glossary
Domain terms and what they mean in code.

## ⚠️ Handle with care
Files that are complex, central, frequently changed, or poorly tested — and why.

## Getting started
Run, test, debug commands (verified from the repo).

## Where to make common changes
"Add an API endpoint → ...", "Add a screen → ...", "Add a DB column → ..."
```

## Rules

- Every claim must point to real paths. Never guess at structure you haven't read.
- Diagrams must be valid Mermaid and stay small (around 12 nodes or fewer). Use several small diagrams rather than one huge one.
- Explain *why* things are structured this way when the code or commit history shows it.
- List open questions you couldn't answer instead of making things up.
