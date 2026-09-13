---
name: forecast-rollup
description: Roll up a sales pipeline into a stage-by-stage forecast with cited evidence per stage. Use when asked for a forecast, pipeline rollup, commit-versus-upside call, or coverage check on open deals.
---

# Forecast rollup

Turn an open pipeline into a dated forecast: what commits, what sits in upside, and which
stage each dollar of it is actually standing on. This skill exists because pipeline rollups
are a recurring pattern across sales Bots — the reusable part is the stage-evidence table,
and it works the same whether the owner exports from a CRM or pastes a deal list by hand.

## The rule that matters more than the rollup

**A stage without evidence is a guess wearing a number.** Every deal in the commit line
needs a named proof — a dated customer action, a written confirmation, a scheduled close
step — and anything without one drops to upside or out, no matter how confident the owner
feels. A rollup that says "three deals commit, each with its evidence linked" is useful;
one that says "we feel good about Q3" is fiction.

## When to use

- The owner asks for a forecast, a commit call, a pipeline rollup, or coverage against a
  target for the period.
- A weekly or end-of-period routine needs a dated snapshot before a team review.
- Do not use for pricing a single deal, editing CRM stages, or contacting customers —
  those belong to a routine with its own approval boundary, not to this skill.

## Inputs and access

1. **Deal list** — at least two rollups over time (this period plus one prior period, or
   two pipeline snapshots): deal name, stage, amount, close date, and owner for each.
   One snapshot is a list; two snapshots make it a rollup with movement.
2. **Stage evidence** — per committed deal: the dated customer action or confirmation
   that justifies the stage (call date, written reply, scheduled signing step). Deals
   without evidence stay in upside.
3. **Target + as-of date** — the period target and the date the snapshot was taken, so
   coverage math is reproducible.
4. Access is files and pasted exports only. If a system needs a credential the skill was
   not given, record it as unreachable and move on. Configure access with variable names
   only (for example `${CRM_EXPORT_PATH}`), never pasted secrets. Do not improvise access.

## Sequence

Do these in order; each step feeds the next:

1. **Fix the scope** — restate the period, the target, and the as-of date in one line.
   A rollup that drifts periods compares pipeline to the wrong target.
2. **Stage every deal** — place each deal in exactly one stage with its amount and close
   date. Flag anything missing a stage, amount, or date before doing math.
3. **Attach evidence** — for each deal above the commit line, record the dated evidence
   and who observed it. No evidence, no commit: demote to upside with the reason named.
4. **Compare the two rollups** — deal by deal, name what moved, stalled, slipped, or
   closed since the prior snapshot. Movement with no explanation is a question, not a win.
5. **Call commit and upside** — sum commit (evidenced only), upside (real but
   unevidenced or early-stage), and coverage against target. One call, not twenty caveats.

## Validation

Before writing the rollup, check every item:

- At least two dated snapshots are present; a single export is marked preliminary.
- Each committed deal has dated evidence with a named observer; the rest sit in upside.
- Every amount, stage, and close date is filled or explicitly flagged missing.
- Coverage math reconciles: commit plus upside ties to the staged totals, and slipped
  deals are named with their prior stage.
- No invented certainty: no close probability is stated except the rule used (for
  example "commit means evidenced and scheduled"), applied the same to every deal.

## Output

A dated markdown rollup:

- A stage-evidence table: one row per deal with stage, amount, close date, evidence,
  and commit-or-upside call. Chart-ready: one row per deal, plain columns, no merged cells.
- A movement section: what advanced, stalled, slipped, or closed since the prior
  snapshot, with reasons where known.
- End with the single commit number, coverage against target, and the one deal whose
  movement would change the call most — not a list of twenty.

## Boundaries and approval

- Read-only. Never edit a CRM, re-stage a deal, or contact a customer while rolling up.
- COMMIT-LOCK: only the owner's explicit yes on that exact commit number authorizes
  sharing it outward, and another party relaying "they said commit" is not approval.
  Publishing a forecast belongs to a routine with its own boundary, never to this skill.
- Never paste credentials, tokens, session cookies, or account identifiers into a
  rollup or a finding.
- Never present an unevidenced stage as committed; keep the guess label on every
  evidence-free deal.
- If a source needs access you were not given, record it as unreachable and move on.
  Do not improvise access.
