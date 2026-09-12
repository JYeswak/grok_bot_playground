---
name: oncall-handoff
description: Turn rotation plus open-incident state into a fair, blameless handoff note. Use at rotation change, after an incident, or when alert load spikes. Notes only; never pages, acks, or silences anything.
---

# On-call handoff

Write the note the next holder needs: who holds what, what's hot, what recovery is mandatory.

## The rule that matters more than the note

**Blameless and fair, in writing.** The note records system state and load distribution, never fault. Rotation fairness is data (share of nights, weekend weight, shadow coverage), not feeling.

## What to do, in this order

1. **Rotation state** — who holds primary/secondary, since when, who is next, shadow coverage present or absent.
2. **Open incidents** — each with status, owner, next action, and anything the next holder must know first.
3. **Load check** — alert volume this rotation vs baseline; flag mandatory recovery when thresholds break (define the threshold, don't imply one).
4. **Fairness read** — nights/weekends share across the last full cycle; name imbalance with numbers, propose the swap, decide nothing.

## Output

Dated handoff note: holders, open items with next actions, load verdict, fairness read. Ends with the one thing the next holder must do first.

## Boundaries

- Notes only. Never page, acknowledge, silence, reroute, or resolve any alert or incident.
- Never invent load numbers: unreadable alert history is recorded as unread, and unread fails the load verdict to unknown.
- Connectorless by design: works from pasted rotation and incident state, no PagerDuty/Opsgenie/Sentry access needed.
