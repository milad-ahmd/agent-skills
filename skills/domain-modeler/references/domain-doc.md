# Domain documents: formats and modelling tests

## GLOSSARY.md

One `##` heading per term, in the singular, spelled the way the code should spell it. `domain_terms.py --glossary` reads only the headings and the `Avoid:` and `Allow:` lines; everything else is for people.

```markdown
# Glossary

## Customer
A person or company that has paid for at least one Order. Created at first checkout.
- Not: a User, which is a login identity (accounts/user.rb:1). One User can act for several Customers.
- Avoid: client, buyer, shopper
- Allow: StripeClient
- Code: `Customer` (checkout/models.py:4), table `customers`
- Context: Checkout, Billing

## Order (Checkout)
A Customer's confirmed intent to buy, with prices fixed at the moment of confirmation.
- Not: a fulfilment Order, which is a pick-and-ship task for one warehouse.
- Avoid: purchase, basket order
```

- `Avoid:` lists words that must not appear in new names for this concept. Comma-separated, singular or plural, multi-word phrases allowed (`line item`). A word listed under one term must not also be a heading; the script skips such a word and warns.
- `Allow:` lists exact identifiers that may keep an avoided word: third-party names (`StripeClient`), names fixed by a public API or a database you can't migrate yet.
- When one word means different things in two contexts, give each its own heading with the context in brackets, and say in `Not:` how they differ.

## docs/domain.md

```markdown
# Domain model

## Scope and depth
What this covers, and why it is a glossary only / a full model (see "When DDD is overkill").

## Bounded contexts
| Context | Owns | Code | Its meaning of shared words |
|---|---|---|---|
| Checkout | Cart, Order, Payment | checkout/ | Order = what the Customer agreed to pay for |
| Fulfilment | Shipment, Parcel | fulfilment/ | Order = one warehouse's pick list |

How the contexts talk: events, API calls, or a shared table (say which, and which side translates).

## Entities and value objects
| Name | Kind | Identity / equality | Lifecycle or why immutable |

## Aggregates and invariants
### Order (root: Order)
Contains: OrderLine, ShippingAddress (value). Other aggregates referenced by ID only: CustomerId.
| Invariant (always true after any command) | Enforced by | Today in code |
|---|---|---|
| Total equals the sum of line totals | Order.add_line / remove_line | not enforced: totals set in OrderService (file:line) |

## Domain events
| Event (past tense) | Raised by | When | Consumed by | Payload |

## Open questions
Each with an owner.
```

## Modelling tests

**Entity or value object.** Ask: if two of these have identical fields, are they the same thing? A Money of 10 EUR equals any other 10 EUR, so it is a value object: immutable, compared by value, replaced rather than edited. Two Customers named Ana are still two Customers, so Customer is an entity with an ID and a lifecycle. Addresses are usually values; if users edit "their address" and other records must follow the edit, it is an entity in that context.

**Aggregate.** Start from an invariant, not from a noun. "An order's total equals its lines" puts Order and OrderLine in one aggregate whose root (Order) is the only way to change lines. Keep aggregates small: one transaction changes one aggregate; rules that span aggregates ("a customer may have at most 3 unpaid orders") are checked with a query, a domain service or eventual consistency, and you write down which. Other aggregates are referenced by ID.

**Bounded context.** Two teams or modules that use one word with different fields, rules or lifecycles. Signs in code: the same class name in two folders with different fields, a "god" class with nullable fields that only some callers set, or a status enum whose values belong to two workflows (`PAID` and `PICKED` on one Order).

**Domain event.** Something the business cares about that already happened: `OrderPlaced`, `PaymentCaptured`, `ShipmentDelivered`. Past tense, named in glossary words, raised by the aggregate that changed. Not `OrderUpdated` (says nothing) and not technical (`RowInserted`).

## When DDD is overkill

Keep it to a glossary when most of these hold: the app is forms over tables (create, read, update, delete with validation); one team; rules fit in a field validator; no state machines, money flows or concurrent edits on the same record. Aggregates and contexts there add files and indirection with nothing to protect. Say so, write the glossary, and stop.

Go further when you see rules that span several objects, a word that means different things to two teams, bugs from two writers changing one record, or a status field with more than five values and transitions people argue about.
