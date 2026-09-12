---
name: analytics-insights
description: Pull Google Analytics metrics with week-over-week deltas and report links for a grokbot site. Use when asked how traffic changed this week, which pages or channels moved, or on a weekly analytics review routine.
---

# Analytics insights

Pull one week of Google Analytics metrics, compare against the prior week, and
report what moved — sessions, users, conversions — with deltas and links back
to the reports the numbers came from.

This skill exists because single-week analytics exports answer "what happened"
but never "what changed": a page with 400 sessions looks fine until last week
shows 1,200.

## The rule that matters more than the report

**A delta is only as honest as the two windows behind it.** Both pulls must
cover full comparable 7-day windows with the same property, dimensions, and
filters. A week-over-week comparison built on mismatched windows or a partial
current week is arithmetic on noise. If the two pulls are not comparable, say
so and name what would make them comparable.

## When to use

- A teammate asks "how did traffic change this week" or "which pages moved".
- A weekly routine pulls sessions, users, and conversions for a grokbot site.
- A traffic drop or conversion change needs a before/after read, not a
  single-week snapshot.
- Do not use for attribution modeling, audience building, or cross-property
  rollups. Community growth advice is community-claim — only this account's
  own Analytics numbers are evidence.

## Inputs and access

- The property to query (exact property as registered in Analytics).
- Two date windows: current week and prior week, each a full 7 days, same
  weekday span.
- Two metric pulls minimum: dimensions per pull (page, channel, or session
  source) with sessions, users, and conversions. Same filters on both pulls.
- Access is read-only through the Analytics API or the report exports the
  operator provides. Credentials arrive per session as `${VAR}` names only,
  never as values. If access is missing, record the pull as unreachable and
  stop — do not improvise credentials.

## Sequence

1. **Fix the windows** — confirm both weeks are full 7-day spans with the same
   weekday alignment, and record the exact dates. A partial current week gets
   labeled partial; it never silently stands in for a full week.
2. **Pull the current week** — sessions, users, conversions by the chosen
   dimension. Record row counts and the report link for each pull.
3. **Pull the prior week** — identical dimensions, metrics, property, and
   filters. Confirm the filter set matches pull one field for field.
4. **Join on stable keys** — match rows on page path or channel name. New rows
   this week are gains in coverage; rows gone this week are losses. Never
   match on rounded metric values.
5. **Compute deltas** — per row: session delta, user delta, conversion delta
   in absolute and percentage terms. Sort movers by each delta separately;
   the sorts surface different problems.
6. **Sanity-check before reporting** — a site-wide session collapse usually
   means a broken pull, a property change, or a date error, not a traffic
   event. Verify row counts and totals before calling it real.

## Validation

- Both pulls returned rows; an empty pull is a failed input, not a quiet week.
- Dimensions, filters, property, and window lengths match between pulls. Any
  mismatch is disclosed, with affected deltas marked unreliable.
- Every metric carries its report link and pull date.
- Spot-check two or three large movers against the Analytics UI or a re-pull
  before treating them as real.

## Output

A dated markdown report with: the two windows and filter set; top gainers and
losers by session delta and conversion delta, each with its report link; new
and lost rows; rows that could not be compared and why. End with the single
most consequential mover and the decision it needs — not a list of fifty.

## Boundaries

- Read-only review. Never change property settings, goals, filters, audiences,
  or publish anything while auditing. Any write or configuration change needs
  the operator's explicit yes first, stated per change — silence is not
  approval.
- Never present community growth advice as Analytics findings. Community
  claims are community-claim; only this account's pulled numbers are evidence.
- If a pull requires access you were not given, record it as unreachable and
  move on. Do not improvise access.
- No secrets, tokens, or account identifiers are stored, logged, or pasted
  into reports. Access is provided per session by the operator as `${VAR}`
  names only, never embedded as values in files.
