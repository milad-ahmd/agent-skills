---
name: domain-modeler
description: Builds a domain model from the real code and the user's words before a feature is built. A read-only script lists the business terms declared in code (classes, structs, enums and their values, SQL tables, ORM and Prisma models) in Python, TS/JS, Go, Java, Kotlin, C# and Ruby and flags one concept under several names (Customer, Client, User), one name declared in two folders (Order in checkout and fulfilment) and near-identical spellings, with file:line. The agent asks only what the code can't answer, then writes a GLOSSARY.md (one name per concept, its meaning, what it is not, words to avoid) and a docs/domain.md: entities, value objects, aggregates and invariants, bounded contexts, domain events. It says when DDD is overkill, proposes renames without applying them, and a --glossary mode fails CI on avoided words. Use when the user asks for a domain model, DDD or ubiquitous language, a glossary of business terms, which name to use for a concept, aggregates or invariants, bounded contexts, or domain events.
---

# Domain Modeler

Get the names and rules right before the code, so the code says what the business says. Ground every entry in the code and the user's words. A model nobody's code follows is decoration.

## 1. Decide how deep to go

Read the README, the models or schema and the feature request. If the app is forms over tables with one team and rules that fit in validators, say "DDD is overkill here", do steps 2, 3 and 5 for the glossary only, and stop. Go further when rules span several objects, one word means different things to two teams, a status field has many contested transitions, or money or concurrent edits are involved. [references/domain-doc.md](references/domain-doc.md) has the full checklist.

## 2. Scan the names the code already uses

```bash
python3 scripts/domain_terms.py src/                 # terms, synonym groups, multi-defined names, near-duplicates
python3 scripts/domain_terms.py . --exclude 'legacy/*' --top 80
python3 scripts/domain_terms.py . --json             # every declaration with file, line, kind, words
```

It reads declarations only: class, interface, type, struct, record and enum names, enum values, `CREATE TABLE`, `CREATE TYPE ... AS ENUM`, Prisma models, and table names in `__tablename__`, `db_table`, `@Entity("...")`, `@Table(name = ...)`, `[Table("...")]` and `create_table`. Names are split into words, technical suffixes and prefixes are dropped (Service, Controller, Repository, Dto, Impl, Handler, Model, Entity, Request, Base, Abstract, the `I` of `IOrderRepository`), plurals become singular, and names with words like Http, Api, Json or Redis are skipped. Test folders, vendored and build folders are skipped.

Exit codes: `0` report · `1` glossary violations (with `--glossary`) · `2` nothing to scan or unreadable glossary.

The output is candidates, not truth. `customer / client / user` in one group may be one concept under three names, or three real concepts. Two `Order` classes may be two bounded contexts or a copy that drifted.

## 3. Read, then ask only what the code can't answer

For each synonym group and multi-defined term, open the declarations and compare fields, callers and the tables they map to. Same fields and same callers usually means one concept. Different fields, lifecycles or owners usually means two. Write what the code settles in a few lines ("`ClientAccount` and `Customer` share `email` and `tax_id`; billing/ calls one, checkout/ the other").

Then ask the user, one question at a time, each with a recommended answer they can accept with "yes":

```
Q2. checkout/models.py:20 Order has prices and a customer; fulfilment/order.go:4 Order has lines
and a warehouse, no prices. Recommend: two contexts, keep "Order" in checkout and call the
fulfilment one "PickList", because warehouse staff already say pick list. OK?
```

Use the user's words for names, not textbook ones. Ask what a term means to the people who use it, what it is not, when one is created and when it ends, and which rules must always hold.

## 4. Model

Work through the tests in the reference: entity or value object (identity and lifecycle, or defined by its values), aggregates (start from an invariant, name the root that enforces it, reference other aggregates by ID), bounded contexts (where one word means different things, and who translates), and domain events (past tense, raised by the aggregate that changed, who reacts). For each invariant, say whether the code enforces it today and where (file:line), or that it doesn't.

## 5. Write the documents

Write `GLOSSARY.md` and, if you went past the glossary, `docs/domain.md`, in the formats in the reference. Use an existing glossary or docs folder if the repo has one. Each glossary entry has: the name as the code should spell it, a one- or two-line definition in the user's words, `Not:` (what it is easily confused with), `Avoid:` (synonyms to stop using), and where it lives in code. Then check the code against it:

```bash
python3 scripts/domain_terms.py src/ --glossary GLOSSARY.md   # exit 1 lists file:line, avoided word, the term to use
```

Offer this as a CI step if the user wants the glossary enforced. Exempt third-party or frozen names with an `Allow:` line, not by dropping the `Avoid:` word.

## 6. Propose renames, don't apply them

List renames as a table: current name, new name, files affected, and risk. Mark separately anything that leaves the codebase: database tables and columns (need a migration), public API fields, event names other services consume, and serialized data. Apply nothing until the user picks which renames to do.

## Report

Depth chosen and why; the synonym groups and multi-defined names with your verdict and its evidence; the questions asked and the answers; the files written; the `--glossary` result; the rename table.

## Do not

- Invent terms, rules or contexts the code and the user don't support. Mark guesses as assumptions.
- Build aggregates, repositories and event buses for a CRUD app.
- Merge two concepts because their names are synonyms, or split one because the script grouped words differently. Check fields and callers.
- Rename code, tables, API fields or events without the user choosing the renames.
- Ask the user what the code already shows.
