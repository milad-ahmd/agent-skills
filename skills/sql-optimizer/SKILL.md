---
name: sql-optimizer
description: Explains slow SQL queries in plain English, reads EXPLAIN plans, and suggests concrete rewrites and indexes for PostgreSQL, MySQL, and SQLite, including ORM-generated N+1 problems. Use when the user shares a slow query, an EXPLAIN output, asks why a query is slow, or wants index recommendations.
---

# SQL Optimizer

Turn "this query takes 8 seconds" into a measured fix.

## 1. Understand

- Identify the engine and version, then explain in plain English what the query returns.
- Gather the schema: the tables, the indexes (`\d table` in Postgres, `SHOW INDEX FROM t` in MySQL) and rough row counts.
- Get the real plan. Ask before running anything against production:
  - Postgres: `EXPLAIN (ANALYZE, BUFFERS) <query>`. Note that ANALYZE executes the query, so wrap writes in `BEGIN; ... ROLLBACK;`.
  - MySQL: `EXPLAIN ANALYZE <query>` (8.0.18+) or `EXPLAIN FORMAT=JSON`.
  - SQLite: `EXPLAIN QUERY PLAN <query>`.

## 2. Diagnose. Common culprits:

- Seq scan or full table scan on a large table with a selective filter → missing index.
- Functions or casts on indexed columns (`WHERE lower(email) = ...`, `WHERE created_at::date = ...`) → not sargable.
- Leading wildcard `LIKE '%x'`, `OR` across different columns, or `NOT IN` with NULLs.
- Large estimate-vs-actual row mismatches → stale statistics (`ANALYZE`).
- `SELECT *` on wide tables, unnecessary `DISTINCT`, or `ORDER BY` with no supporting index plus `LIMIT`.
- Deep `OFFSET` pagination → use keyset pagination.
- Correlated subqueries → use JOIN or `EXISTS`; also look for an N+1 pattern in ORM code (loops that issue queries).

## 3. Recommend

For each fix give: the change, why it helps, the expected plan change and the trade-offs.

```sql
-- Composite index: equality columns first, then range/sort columns
CREATE INDEX CONCURRENTLY idx_orders_user_created ON orders (user_id, created_at DESC);
```

- Prefer query rewrites before adding indexes. Every index slows down writes and uses storage.
- Consider partial indexes (`WHERE deleted_at IS NULL`), covering indexes (`INCLUDE`) and expression indexes.
- For ORMs, show the fix in the ORM's own syntax: eager loading (`include`, `select_related`, `preload`) and selecting only the needed columns.
- Use `CONCURRENTLY` (Postgres) or online DDL for production tables, and ship index changes as a migration in the project's migration tool.

## 4. Verify

Compare the plan and timing before and after the change. Never claim a speedup you didn't measure; label unmeasured numbers as estimates.
