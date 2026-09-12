---
name: search-console-watch
description: Pull weekly Search Console query and page position data with week-over-week deltas for a grokbot site. Use when asked how search traffic changed this week, which queries gained or lost position, or on a weekly SEO check routine.
---

# Search Console watch

Pull one week of Search Console query and page performance, compare it against the prior
week, and report what moved — clicks, impressions, average position — with deltas.

This skill exists because raw Search Console exports answer "what happened" but never
"what changed": a query at position 8 looks fine until last week shows it at position 3.

## The rule that matters more than the report

**A delta is only as honest as the two windows behind it.** Both weekly pulls must cover
full comparable 7-day windows with the same dimensions, filters, and site property.
A week-over-week comparison built on mismatched windows, different filters, or a partial
current week is not a finding — it is arithmetic on noise. If the two pulls are not
comparable, say so and name what would make them comparable.

## When to use

- A teammate asks "how did search perform this week" or "which queries moved".
- A weekly routine pulls query and page position data for a grokbot site.
- A position drop or click loss needs a before/after read, not a single-week snapshot.
- Do not use for keyword research, content planning, or rank tracking outside Search
  Console data. Community SEO listicles are leads at best — anything actionable gets
  verified against this account's own Search Console numbers.

## Inputs and access

- The site property to query (exact property as registered in Search Console).
- Two date windows: current week and prior week, each a full 7 days, same weekday span.
- Dimensions per pull: query and page, with clicks, impressions, CTR, and average
  position. Same filters on both pulls (country, device, search type, query/page filter).
- Access is read-only through the Search Console API or the performance export the
  operator provides. If access is missing, record the pull as unreachable and stop —
  do not improvise credentials or scrape.

## Sequence

1. **Fix the windows** — confirm both weeks are full 7-day spans with the same weekday
   alignment, and record the exact dates. A partial current week gets labeled partial;
   it never silently stands in for a full week.
2. **Pull the current week** — query dimension and page dimension, with clicks,
   impressions, CTR, and average position. Record row counts.
3. **Pull the prior week** — identical dimensions, metrics, property, and filters.
   Confirm the filter set matches pull one field for field.
4. **Join on stable keys** — match rows on query string (or page URL). New rows this
   week are gains in coverage; rows gone this week are losses. Never match on rounded
   position or CTR values.
5. **Compute deltas** — per query and per page: click delta, impression delta, and
   position delta in absolute places. Sort movers by position delta and by click delta
   separately; the two sorts surface different problems.
6. **Sanity-check before reporting** — a site-wide impression collapse usually means a
   broken pull, a property change, or a date error, not a penalty. Verify row counts
   and totals before calling it a traffic event.

## Validation

- Both pulls returned rows; an empty pull is a failed input, not a quiet week.
- Dimensions, filters, property, and window lengths match between pulls. Any mismatch
  is disclosed in the report, with the affected deltas marked unreliable.
- Spot-check two or three large movers against the Search Console UI or a re-pull
  before treating them as real.
- Numbers that cannot be verified are labeled unmeasured, never averaged away.

## Output

A dated markdown report with: the two windows and filter set; top query gainers and
losers by position delta and by click delta; top page movers; new and lost
queries/pages; rows that could not be compared and why. End with the single most
consequential mover and the decision it needs — not a list of fifty.

## Boundaries

- Read-only. Never change site settings, submit URLs, request indexing, or publish
  anything while auditing.
- Never present community SEO advice or listicle patterns as Search Console findings.
  Community claims are community-claim; only this account's pulled numbers are evidence.
- If a pull requires access you were not given, record it as unreachable and move on.
  Do not improvise access.
- No secrets, tokens, or account identifiers are stored, logged, or pasted into
  reports. Access is provided per session by the operator, never embedded in files.
