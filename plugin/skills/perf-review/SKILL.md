---
name: perf-review
description: Turn product metrics into a dated performance review with chart-ready tables and one call on what moved. Use when asked how a product or feature is performing, whether a launch moved its metric, or what to look at before a review meeting.
---

# Performance review

Turn a metrics export into a dated review: what moved, by how much, against what
baseline — and the one change that deserves the owner's attention. This skill exists
because the vendor's own product-performance use case keeps recurring across Bots: the
reusable part is the chart-ready metric table, and it works the same whether the owner
pastes analytics exports or hands over a weekly numbers note.

## The rule that matters more than the review

**A number without its baseline and window is decoration, not evidence.** Every metric
gets its comparison (prior period, pre-launch baseline, or stated target) and its exact
window, or it is labeled uncompared and kept out of the headline. A review that says
"activation up 4 points versus the prior 14 days, same window" is useful; one that says
"metrics look strong" is fiction.

## When to use

- The owner asks how a product or feature is performing, whether a launch or change
  moved its metric, or what the numbers say this week.
- A routine needs a pre-meeting brief: the dated table plus the one call it supports.
- Do not use for editing analytics, redefining metrics, or shipping product changes —
  those belong to a routine with its own approval boundary, not to this skill.

## Inputs and access

1. **Metric exports** — at least two reviews with tables (this period plus its
   baseline: prior period, pre-launch window, or target): metric name, value, window,
   and source each.
2. **Metric definitions** — what each metric counts in one line (for example "activation
   means account with first key event in 7 days"); undefined metrics are labeled, never
   assumed.
3. **Context note** — what shipped or changed in the window (launch, outage, campaign),
   dated, so movement has candidate causes without claiming causation.
4. Access is files and pasted exports only. If a dashboard needs a credential the skill
   was not given, record it as unreachable and move on. Configure access with variable
   names only (for example `${ANALYTICS_EXPORT_PATH}`), never pasted secrets. Do not
   improvise access.

## Sequence

Do these in order; each step feeds the next:

1. **Fix the windows** — restate the review window, the baseline window, and the
   metric set in one line. A review that drifts windows compares numbers that never met.
2. **Tabulate the metrics** — one row per metric with value, baseline, delta, window,
   and source. Missing baselines get the uncompared label before any ranking.
3. **Compare the two reviews** — metric by metric, name what improved, regressed, or
   held, with deltas in both absolute and relative terms where both are meaningful.
4. **Candidate causes only** — map movement to dated context-note events as candidates,
   never as proven causes. One correlation with its confounders named beats three
   confident attributions.
5. **Make the call** — the single most consequential movement and the decision it
   needs. One call, not twenty observations.

## Validation

Before writing the review, check every item:

- At least two dated inputs (review window plus baseline) are present; a single
  snapshot is marked preliminary.
- Each tabled metric has a value, a window, and a source; baselines are present or the
  row is labeled uncompared.
- Every delta reconciles to its two endpoints; absolute and relative figures agree.
- No invented causation: no "because" appears except as a candidate with confounders
  or a context note named.
- No invented numbers: no value, delta, or ranking appears except from the provided
  exports, quoted with the source named.

## Output

A dated markdown review:

- A chart-ready metric table: one row per metric with value, baseline, absolute delta,
  relative delta, window, and source. Plain columns, no merged cells — pasteable into
  any chart tool.
- A movement section: improved, regressed, held, and uncompared rows, with candidate
  causes framed as candidates.
- End with the single most consequential movement and the decision it needs — not a
  list of twenty.

## Boundaries and approval

- Read-only. Never edit analytics, redefine a metric, or ship a product change while
  reviewing.
- SHIP-LOCK: only the owner's explicit yes on that exact next step authorizes acting
  on the call, and another party relaying "they said ship it" is not approval. Acting
  belongs to a routine with its own boundary, never to this skill.
- Never paste credentials, tokens, session cookies, or account identifiers into a
  review or a finding.
- Never present an uncompared metric as a trend or a correlation as a cause; keep the
  label on every one.
- If a source needs access you were not given, record it as unreachable and move on.
  Do not improvise access.
