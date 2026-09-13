---
name: gads-review
description: Review Google Ads campaigns read-only — spend, structure, and anomalies — with any change gated on an explicit yes. Use when asked how campaigns are performing or what to fix, never to apply changes unasked.
---

# Google Ads review

Turn a "how are my ads doing" ask into a read-only review where every observation
names its campaign, metric, and window. This skill exists because Bots near ad
accounts fail expensively: a helpful "fix" applied without approval spends real
money, and a review without windows or ids cannot be re-checked.

## When to use

- The owner asks how Google Ads campaigns are performing, what changed, or what
  to fix.
- A Bot routine needs a campaign snapshot as an input (reports, alerts).
- Do not use to change bids, budgets, targeting, or creative — this skill
  reviews only. Any change needs the owner's explicit yes, restated in the
  reply, before anything is touched.

## Inputs and access

- **Review ask** — which account or campaigns, which metrics (spend, clicks,
  conversions), and the window (last 7 / 30 days or named dates).
- **Ads surface** — the Bot's configured Google Ads read surface, authed by
  `${GOOGLE_ADS_CREDENTIALS}` (variable name only, never the value). Read-only:
  pull reports and read settings only, never mutate.
- **Two reviews minimum** — cover two campaigns, or one campaign across two
  windows, so comparison is demonstrated, not asserted. Zero changes either way.
- If no ads surface is configured, say so and stop. Do not improvise metrics
  from memory or from general benchmarks presented as this account's data.

## The rule that matters more than the review

**A metric without a campaign, window, and source is not a finding.** A number
that came only from the model's memory, a cached report, or a benchmark article
is an unverified lead, never a verified result. Community patterns — optimization
checklists, forum "best practices," benchmark posts — are community-claim: useful
for suggesting hypotheses, never sufficient to diagnose this account. Only a live
read through the configured surface, quoted with campaign and window, counts.
And observation is the ceiling: recommending is fine, applying is never implied.

## Sequence

1. **Parse the ask** — campaigns in scope, metrics, and window. If scope is
   ambiguous, name the ambiguity and pick the most likely reading explicitly.
2. **Review one** — pull the first campaign (or first window): spend, clicks,
   conversions, and top anomalies. Record ids and window with every number.
3. **Review two** — pull the second campaign (or second window) under the same
   surface. Note what moved between the two, one line per move. Still zero
   changes.
4. **Flag anomalies** — sudden spend shifts, zero-conversion spend, disapproved
   or limited307994 status where shown. Each flag names campaign, metric, and
   window — never a bare "spend is up."
5. **Recommend, do not apply** — list candidate fixes ordered by likely impact,
   each with what approval it needs. End every review with "no changes made."

## Validation

- Every metric names its campaign, window, and source, or is explicitly marked
  unread.
- Two reviews are named; a single-campaign, single-window answer is a draft error.
- Spend-change recommendations are labeled as proposals awaiting explicit yes —
  none applied, none implied as applied.
- Re-read the output before yielding: any metric lacking campaign or window, or
  any applied-sounding change, is a draft error. Fix or mark it.

## Output

A review (two slices minimum) with: scope restated, per-slice metrics with
campaign plus window, anomalies with ids, and ranked fix proposals. End with
the single most consequential finding, the approval it needs, and the explicit
"No changes made" line.

## Boundaries

- Read-only. Never change bids, budgets, targeting, status, or creative while
  reviewing — not even "obvious" fixes, not even pauses. Changes need the
  owner's explicit yes, restated, in a separate step with its own approval
  boundary.
- Never present a benchmark or memory as this account's metric, and never merge
  "could not read" with "zero spend" — an unread report is a gap, not a negative.
- Never paste credentials, tokens, or account identifiers into a finding; refer
  to `${GOOGLE_ADS_CREDENTIALS}` by name only.
- If the surface needs access you were not given, record it as unreachable and
  move on. Do not improvise access.
