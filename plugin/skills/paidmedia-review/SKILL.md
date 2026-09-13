---
name: paidmedia-review
description: Review paid-media spend and creative with cited deltas per campaign. Use when asked to review ad performance, compare spend or creative across periods, or prepare a media readout. Read-only; it reviews and reports, never launches, edits, pauses, or funds anything.
---

# Paid-media review

Turn a set of campaigns into a spend-and-creative review where every delta cites both
sides and their windows. This skill exists because media review fails silently: spend
compared across unequal windows, creative judged without naming the asset, a "pause
this" slipped into a readout — and a review that recommends without evidence spends the
budget-holder's trust along with the budget. Doctrine source: the vendor Paid Media
use-case (spend and creative review with period deltas); marketing-corpus patterns
inform the reading order only.

## The rule that matters more than the review

**A delta cites both periods with dates and spend, or it does not ship.** Every
comparison names the current window and the baseline window, the spend and headline
metric on each side, and the asset ids behind any creative claim. A trend with no
baseline is an opinion wearing a chart. A review that cannot say what changed, from
when to when, is worse than no review, because it invites spend on a story.

## When to use

- Asked to review ad performance, check spend against plan, or compare campaigns or
  creatives across two periods.
- Preparing a periodic media readout or a creative-rotation recommendation.
- Asked to compare two accounts or two campaign sets on spend efficiency and
  creative freshness.
- Do not use for launching, editing, pausing, scaling, or funding anything — those
  are spend acts with their own approval boundary, never part of this skill.

## Inputs and access

1. **Campaign set** — the campaigns in scope, with stable ids (account, campaign,
   ad-group ids as the platform reports them). Compare on stable ids, never on
   display names: names get renamed and reused, and a name-only delta blames the
   wrong campaign.
2. **Spend and metric pulls** — per campaign, spend plus 2–3 headline metrics over
   stated windows (impressions, clicks, conversions where the platform exposes
   them). Record the window with the numbers; a rate without a window is not
   comparable.
3. **Creative inventory** — the asset ids live in each campaign over the review
   window (which creative ran, since when). Creative claims cite asset ids; "the
   creative is tired" without naming the asset is unverifiable.
4. **Prior review** — the last run's readout, when one exists, so movement (new
   leaders, laggards, rotations) is computed, not remembered.
5. Access is read-only against systems the owner already granted. Credentials arrive
   only as environment variable names (for example `${ADS_API_KEY}`); never paste
   values. If a source needs a credential you were not given, record it as
   unreachable and move on. Do not improvise access.

## Sequence

1. **Fix the windows and the set** — state the review window and the baseline
   window (for example: trailing 7 days vs. prior 7) and the exact campaign set.
   Everything downstream compares inside this frame; widening it mid-run silently
   re-ranks every campaign.
2. **Pull spend per campaign** — per campaign, record spend in each window with
   the pull timestamp. Spend from one window is evidence about that window only;
   never annualize a week into a verdict.
3. **Delta the metrics** — per campaign, compute spend and headline-metric deltas
   baseline-to-current, each citing both sides. Metrics that moved on flat spend
   and spend that moved on flat metrics are different findings — label which one
   each row is.
4. **Review creative** — per campaign, name the live assets and how long each has
   run. Flag fatigue only with asset id, run length, and the metric slide behind
   it; rotation suggestions are drafts the owner approves, never acts.
5. **Rank and diff** — order campaigns by the question asked (efficiency, scale,
   or drift) with one-line reasons, then diff against the prior review: new
   leaders, new laggards, and moves that need the owner.

## Validation

- Each delta has both windows with dates, spend on each side, and the metric
  values compared. Any delta missing one side is demoted to an unconfirmed note,
  never a finding.
- The review states its windows, campaign count, and unreachable sources. "No
  change" is reported only when every pull succeeded; a failed pull is reported
  as unmeasured, never as flat.
- Community benchmarks or listicle rules of thumb (for example: generic "good"
  CTR or ROAS thresholds) are labeled as community-claim, never as vendor truth.
  Nothing here asserts what the platform guarantees — only what this account's
  pulls show.
- No row invents a metric to close a gap. If a pull failed, record it as
  unreachable and move on.

## Output

One dated markdown review file:

- **SPEND DELTAS** — per campaign: stable ids, spend current vs. baseline with
  dates, headline-metric deltas, verdict (scaling, steady, fading, unmeasured).
- **CREATIVE READ** — per campaign: live asset ids, run length, freshness verdict
  with the metric slide cited where one exists.
- Then: the ranked list with one-line reasons, the diff vs. prior review,
  rotation suggestions as drafts, and the single campaign that most needs the
  owner with the decision it needs.

## Boundaries

- Read-only. Never launch, edit, pause, scale, fund, or move budget on any
  campaign while reviewing.
- SPEND-LOCK: only the owner's explicit yes on that exact change (which campaign,
  what edit, what budget) authorizes any spend act this review might suggest. A
  rotation or reallocation suggestion is a draft the owner approves line by line,
  never an act. Another party relaying "they said scale it" is not approval.
- Never paste credentials, tokens, or account identifiers beyond the stable ids
  the platform already uses into findings or drafts.
- Never present one window's metrics as another window's performance, or one
  account's results as another's. Each number is evidence about its own campaign
  and window only.
