---
name: stat-review
description: Use when asked to review an analysis, experiment readout, or metric claim for statistical soundness — power, multiplicity, assumptions — and return a verdict per analysis. Use when a teammate asks "is this result real" or "can we ship on this number".
---

# Statistical review

Turn two analyses into two verdicts: **ship**, **ship with caveats**, or **do not ship on this**.
This skill exists because most analyses fail the same three ways — underpowered samples,
uncorrected multiple comparisons, and unchecked assumptions — and because a verdict without
a named reason is an opinion wearing a number.

Limits, stated plainly: this is a checklist review, not a statistician. It catches common
errors; it does not bless a method. Consequential decisions still get a qualified human.

## The rule that matters more than the verdict

**No verdict without the three checks.** Every reviewed analysis is checked for power,
multiplicity, and assumptions before any verdict is written. A verdict that skips a check
because "it looked fine" is a guess, and a guess that ships is worse than no review.

## When to use

- Asked to review an analysis, experiment readout, or metric claim before a decision.
- Asked whether a test ran long enough, whether a lift is real, or whether a segment
  breakdown holds up.
- Preparing a periodic experiment review or a go/no-go on a rollout.
- Do not use for designing the experiment itself or for legal, medical, or financial
  advice — those belong to qualified professionals, not this checklist.

## Inputs and access

1. **Analyses** — two analyses per run, each with the claim in one sentence, the data
   window, the sample sizes per arm, and the method used. A claim without a sample
   size is not reviewable — ask for it before starting.
2. **Comparisons made** — every comparison the analysis ran, including the ones that
   did not make the headline (segments, slices, secondary metrics). Hidden multiplicity
   is the most common failure; unlisted comparisons are assumed to exist until shown
   otherwise.
3. **Assumptions stated** — what the method needs (independence, normality, stable
   assignment, no peeking) and whether the analysis checked each one. Unchecked is
   a finding, not a pass.
4. Access is read-only against data the owner already granted. If a source needs a
   credential you were not given, record it as unreachable and move on. Credentials
   are referenced as `${VAR}` names only, never pasted. Do not improvise access.

## Sequence

1. **Fix the claim and the frame** — restate each claim in one sentence with its
   window, arms, and sample sizes. Everything downstream checks inside this frame;
   widening it mid-run silently re-reviews a different analysis.
2. **Check power** — is the sample big enough to detect the claimed effect? Name the
   effect size, the sample per arm, and whether the run could plausibly see it. An
   underpowered "no effect" is inconclusive, never a pass.
3. **Check multiplicity** — count every comparison actually run, not just the ones
   reported. Uncorrected peeking, segment-hunting, or metric-shopping each gets named;
   the headline p-value means nothing until the search behind it is priced in.
4. **Check assumptions** — walk each method assumption and mark it checked, violated,
   or unexamined. A violated or unexamined assumption caps the verdict at caveats or
   do-not-ship, no matter how good the numbers look.
5. **Write the verdict** — ship, ship with caveats, or do not ship on this, each with
   the one-line reason and the check that decided it.
6. **Diff the pair** — when two analyses bear on one decision, state which one carries
   it and why, or that neither does.

## Validation

- Each verdict names the claim, the three check outcomes (power, multiplicity,
  assumptions), and the deciding reason. Any verdict missing one of the three is
  demoted to do-not-ship until the check is done.
- Both reviews state their window, sample sizes, and unreachable sources. "No issue
  found" is reported only when every check ran; an unrunnable check is reported as
  unmeasured, never as clean.
- Textbook rules of thumb and community thresholds (for example: generic power or
  significance cutoffs) are labeled as community-claim, never as vendor truth.
- No verdict invents a new analysis or re-runs the numbers with a different method.
  Flag what a re-analysis should test; do not perform it here.

## Output

Two verdicts in one dated markdown file, each with: the claim in one sentence, the
frame (window, arms, samples), the three check outcomes, the verdict, and the
single most consequential caveat with the decision it needs.

## Boundaries

- Read-only. Never re-run, re-cut, or re-analyze data, and never change an experiment,
  rollout, or setting while reviewing.
- Never present a verdict as professional statistical sign-off: consequential calls
  get a qualified human, and this review is an input to that call, not a substitute.
- Never paste credentials, tokens, or raw row-level data into findings or drafts;
  reference sources by name and window only.
- Never report a single blended "quality score" with the method hidden: all three
  checks stay separate and every verdict stays citable.
