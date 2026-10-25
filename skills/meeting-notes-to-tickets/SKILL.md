---
name: meeting-notes-to-tickets
description: Turns raw meeting notes, transcripts, or Slack threads into well-formed, de-duplicated tickets for GitHub Issues, Linear, or Jira, each with a title, context, acceptance criteria, owner, priority, and labels, after user confirmation. Use when the user pastes meeting notes or a transcript and asks to create tasks, issues, tickets, or action items.
---

# Meeting Notes to Tickets

Meetings end, action items vanish. This skill makes sure they don't.

## 1. Extract

From the notes or transcript, pull out:
- **Action items**: someone will do something. Note the owner and the due date if they're mentioned.
- **Decisions**: record them in the ticket context. They are not tickets themselves.
- **Bugs and requests** that were mentioned in passing.
- **Open questions**: these become follow-up or "spike" tickets, or a list for the user.

Ignore small talk. Don't turn vague ideas ("we should think about X someday") into tickets. List them separately as *parked ideas*.

## 2. Draft tickets

```markdown
### [Bug] Checkout fails for coupons with 100% discount
**Context:** Raised by Sara in the 2026-10-25 product sync; ~12 support tickets this week.
**Acceptance criteria:**
- [ ] Orders with a 100% coupon complete without a payment step
- [ ] Order confirmation email shows €0.00
**Owner:** @sara · **Priority:** High · **Labels:** bug, checkout · **Due:** 2026-11-01
**Source quote:** "the free coupon thing is blocking people"
```

- Titles are action-oriented and specific (verb + object), 70 characters or fewer, with a type prefix.
- Acceptance criteria must be testable checkboxes.
- Priority comes from what was said (urgency, customer impact). If unclear, mark it `needs triage`; don't guess.
- Only assign owners who were named in the notes.

## 3. De-duplicate

Before creating anything, search the tracker for similar open tickets:
- GitHub: `gh issue list --state open --search "<keywords>"`
- Linear or Jira: use the available integration, MCP tool or CLI.
If a match exists, propose a **comment on the existing ticket** instead of creating a new one.

## 4. Confirm, then create

Show the table of proposed tickets (new or update, title, owner, priority) and **wait for approval**. Then create them:

```bash
gh issue create --title "..." --body-file ticket.md --label bug,checkout --assignee sara
```

Use only labels and assignees that already exist in the repo (`gh label list`), or ask before creating new ones.

## 5. Summarise

Post a recap with links to every created or updated ticket, the decisions log, the open questions and the parked ideas, ready to paste into Slack or email.
