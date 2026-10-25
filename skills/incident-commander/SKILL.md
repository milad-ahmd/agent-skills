---
name: incident-commander
description: Guides a production incident from first alert to blameless postmortem, covering severity triage, an incident channel and roles, structured investigation (recent deploys, logs, metrics, dependencies), hypothesis tracking, safe mitigation and rollback, status-page and stakeholder updates, a timeline, and a postmortem with action items. Use when the user says production is down, there's an outage, errors are spiking, the site is slow, or asks for help writing a postmortem.
---

# Incident Commander 🚨

Stay calm, write things down, **restore service first**, and find the root cause second.

## 1. Triage (first 5 minutes)

- **What's broken, for whom, since when?** Get the impact in numbers (error rate, affected users or regions, money at risk).
- **Severity**: SEV1 = full outage or data loss/security · SEV2 = major feature down or heavy degradation · SEV3 = minor or partial issue with a workaround.
- Roles (even if one person holds several): **Incident commander** (decides), **Ops/investigator**, **Comms**.
- Start the **timeline** right away (UTC timestamps) in `incidents/<date>-<slug>.md`.

## 2. Investigate (most likely causes first)

1. **What changed?** Recent deploys, config or feature-flag changes, migrations, infrastructure changes, dependency or provider incidents (check status pages).
   `git --no-pager log --since="6 hours ago" --oneline`, plus the deploy history.
2. **Signals**: the error logs (group by message), the metrics (the RED method: rate, errors, duration), saturation (CPU, memory, DB connections, queue depth).
3. **Scope**: all users or some? One region, endpoint, tenant or app version?

Keep a **hypothesis table** and update it as you go:

| Hypothesis | Evidence for | Evidence against | Test | Status |
|---|---|---|---|---|

## 3. Mitigate (stop the bleeding)

Prefer **reversible** actions: roll back the deploy, turn off the feature flag, scale up, fail over, rate-limit, or put up a maintenance page.
**Every command that changes production needs the user's explicit confirmation.** Say what it does and how to undo it.

## 4. Communicate (SEV1/2: update every 30 minutes, even with "no new info")

Templates: **Investigating** → **Identified** → **Monitoring** → **Resolved**. State the impact plainly, without blame and without speculating about the cause in public. Write one internal update for stakeholders and one external update for the status page.

## 5. Resolve and postmortem (blameless)

```markdown
# Postmortem: <title> (SEV2, <date>)
## Summary · Impact (duration, users, $) · Detection (how/when, time-to-detect)
## Timeline (UTC)
## Root cause (5 whys — systems, not people) · Contributing factors
## What went well · What went poorly · Where we got lucky
## Action items (owner, due date, priority): prevent / detect faster / mitigate faster
```

## Rules

- Focus on systems, not blame. Never name individuals as the cause.
- Don't guess the root cause. Separate what is confirmed from what is suspected.
- Protect secrets and personal data when pasting logs.
