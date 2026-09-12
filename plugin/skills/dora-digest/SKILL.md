---
name: dora-digest
description: Turn deployment and incident exports into a dated DORA four-keys brief with a source row per number. Use when asked how delivery is doing, for throughput reviews, or before an eng review. Read-only brief, never a verdict on people.
---

# DORA digest

Compute the four keys — deployment frequency, lead time for changes, change failure rate, mean time to restore — from pasted exports, one sourced row per number.

## The rule that matters more than the brief

**Every number carries its source row or it does not ship.** A DORA metric without the underlying exports is numerology. Throughput numbers describe the system, never judge individuals — no names in verdicts.

## What to do, in this order

1. **Collect** — deployment log, incident list, restore timestamps for the window. Record what is missing before computing anything.
2. **Compute** — the four keys with window, denominator, and exclusions stated (what counted, what did not).
3. **Source rows** — one row per number: value, window, source export + date. A reader must be able to re-derive every figure.
4. **Trend** — same window last period, deltas with direction. One period is a snapshot; two is the start of a trend.

## Output

Dated brief: four keys with source rows, trend deltas, missing-data caveats. Ends with the single most-moved key and what changed, not a rating.

## Boundaries

- Read-only brief. Never access systems directly; works from pasted exports only.
- Never compute from incomplete windows without saying so — partial windows get partial keys, clearly marked.
- No individual performance claims, ever.
