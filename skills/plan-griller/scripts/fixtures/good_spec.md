# Spec: Partial refunds on orders

Status: approved · Author: Dana · Date: 2026-10-20 · Related: #812

<!-- This comment is ignored by the linter, even if it says TBD or fast. -->

## Problem

Support issues refunds by editing `orders.status` to the free-text value `refunded` and refunding the
whole amount in the Stripe dashboard. Customers who return one item of three get everything back or
nothing. Support logged 140 manual partial refunds in September, each taking about 9 minutes.

## Goals

- Support agents issue a refund for any subset of an order's line items from the admin order page.
- Refund state is stored in a typed table, not in `orders.status`.

## Non-goals

- Customer-initiated refunds from the storefront (planned for Q1, separate spec).
- Refunds for orders paid with gift cards; those stay manual.

## Users and actors

- Support agent: has the `refunds:write` permission; issues refunds.
- Finance viewer: has `refunds:read`; sees refund history, cannot create refunds.
- Stripe: processes the refund and sends `charge.refunded` webhooks.

## Requirements

1. An agent with `refunds:write` must be able to select one or more line items on an order and refund their total.
2. The system must reject a refund whose amount exceeds the order's remaining refundable amount.
3. The system must create at most one Stripe refund per refund request, even if the agent submits twice.
4. Each refund must be stored in `refunds` with order ID, line items, amount, agent ID and Stripe refund ID.

## Acceptance criteria

- **AC1** (R1) Given an order with 3 paid line items, when an agent refunds item 2, then a Stripe refund for item 2's amount is created and the order page lists it.
- **AC2** (R1) Given an agent without `refunds:write`, when they open the order page, then the refund button is not shown and `POST /refunds` returns 403.
- **AC3** (R2) Given an order with EUR 10.00 left to refund, when an agent requests EUR 12.00, then the API returns 422 `exceeds_refundable` and no Stripe call is made.

### R3: double submit

- **AC4** Given a refund request with idempotency key K, when it is submitted twice within 24 hours, then exactly one Stripe refund exists and both responses carry the same refund ID.

### AC5 (R4)

- Given a completed refund,
- when finance opens the refund history,
- then the row shows order ID, line items, amount, agent and Stripe refund ID.

## Edge cases and error states

- Stripe times out after 10 s: the refund is saved as `pending` and the webhook settles it; the agent sees "Refund pending".
- Order already fully refunded: the refund button is disabled with the tooltip "Nothing left to refund".

## Data and API changes

- New table `refunds` (id, order_id, line_item_ids, amount_cents, currency, agent_id, stripe_refund_id, status, created_at).
- New endpoint `POST /orders/{id}/refunds` with an `Idempotency-Key` header.
- `orders.status = 'refunded'` stays readable; it is derived from `refunds` from now on.

```sql
-- the fence keeps this out of the lint, even with words like TBD or fast
CREATE TABLE refunds (id uuid PRIMARY KEY);
```

## Rollout, migration and flags

- Flag `partial_refunds`, off by default, on for the support team first.
- Migration: backfill one `refunds` row per order whose status is `refunded` (about 2,300 rows, under a minute).
- Rollback: turn the flag off; rows in `refunds` stay and are ignored.
- Observability: counter `refunds_created_total` by outcome; alert when the Stripe error rate is over 5% for 10 minutes.

## Risks

- Backfill amounts differ from Stripe for orders refunded outside the admin: compare against the Stripe export before enabling.

## Open questions

| ID | Question | Owner | Due |
|---|---|---|---|
| Q1 | Do refunds need a reason code for finance reporting? | Priya | 2026-11-01 |
| Q2 | Should the customer get an email per partial refund? | @sam | 2026-11-03 |

## Decisions

- Refunds get their own table instead of a new `orders.status` value, because an order can have several partial refunds.

## Test plan

| Criterion | Test | Level | Where |
|---|---|---|---|
| AC1 | `refunds_selected_line_items` | integration | `tests/refunds/test_create.py` |
| AC2 | `refund_requires_permission` | integration | `tests/refunds/test_auth.py` |
| AC3 | `rejects_over_refund` | unit | `tests/refunds/test_amounts.py` |
| AC4 | `idempotent_double_submit` | integration | `tests/refunds/test_idempotency.py` |
| AC5 | `history_shows_refund_row` | e2e | `e2e/refund_history.spec.ts` |
