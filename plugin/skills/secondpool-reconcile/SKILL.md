---
name: secondpool-reconcile
description: Use when asked how usage pools really behave — linked SuperGrok allowance vs Bot allowance, separate pool vs whichever-has-more — reconciled from client reads only. Use when the separate-pool wording and the FAQ wording disagree.
---

# Second-pool reconcile

Turn the pooling contradiction into settled evidence: **the client read, quoted
verbatim, decides**. This skill exists because a linked SuperGrok subscription
is advertised as a separate pool while the FAQ says whichever-has-more usage —
both wordings live, one behavior real — and because no invented pool math has
told them apart yet.

Limits, stated plainly: this reconciles from client reads only and closes the
contradiction with evidence; it does not set billing expectations, choose a
plan, or predict future allowance — the true behavior on the read date is the
whole product.

## The rule that matters more than the reconciliation

**No invented pool math — the client read is the only arithmetic.** Every
claim about pools traces to a quoted client read: allowance figures, usage
consumed, labels as rendered. A reconciliation built from remembered numbers,
assumed rollover, or "surely it works like" reasoning is marked unmeasured —
and an unmeasured pool fails the run, because pool math that is not a client
read is fiction with units.

## When to use

- Asked whether the linked SuperGrok allowance is a separate pool or shares
  with the Bot allowance.
- Asked to close the separate-pool vs whichever-has-more contradiction.
- After a usage-page, billing-page, or FAQ wording change that touches pools.
- As a re-check when observed consumption disagrees with the recorded
  behavior.
- Do not use for plan advice, billing disputes, or spend approval — those
  belong elsewhere.

## Inputs and access

1. **Client reads** — the usage and billing surfaces as rendered (allowance
   figures, usage consumed this cycle, pool labels, FAQ wording), quoted
   verbatim with read dates. A reconcile with no fresh client read is not a
   reconcile — fetch it first.
2. **Prior evidence** — the last run's reads and verdict, when they exist, so
   wording moves and behavior moves are computed, not remembered.
3. Access is read-only against pages the owner already granted, on the device
   the owner names. Never alter billing settings, toggle on-demand, or change
   a limit to "test" pooling. If a page needs access you were not given,
   record it as unreachable and move on. Credentials are referenced as
   `${VAR}` names only, never pasted.

## Sequence

1. **Fix the contradiction in writing** — quote both wordings verbatim with
   sources and dates: the separate-pool claim and the whichever-has-more
   claim. Everything downstream checks inside these quotes; paraphrase is not
   evidence.
2. **Read the client** — record allowance figures, usage consumed this cycle,
   pool labels, on-demand state and limit, and whether unused allowance rolls
   over, exactly as rendered. Each figure gets a source and a read date.
3. **Test consumption against each wording** — state what each wording
   predicts for the observed figures and which prediction the reads match. A
   prediction neither wording makes is not tested — it is noted as open.
4. **Record the true behavior** — one paragraph: which wording the reads
   support, on what date, against what figures. If the reads support neither,
   or contradict each other, the contradiction stays open with the reads
   quoted — an open contradiction with evidence beats a closed one by
   assumption.
5. **Diff against the prior run** — wording moves, figure moves, and verdict
   moves each get one line: what moved and why.
6. **Name the gaps** — anything unreadable, unreachable, or still
   contradictory becomes a gap with an owner and a date.

## Validation

- Each run quotes both wordings with sources and dates, cites client reads
   with dates for every figure, states the per-wording prediction check, and
   carries a gap list (possibly empty). Any run missing one of these is
   incomplete, not passing.
- Zero invented figures: every number traces to a quoted read. A figure
   without a source is fiction and fails the run.
- "Contradiction closed" is reported only when the reads settle it; mixed or
   missing reads are reported as open-with-evidence, never as closed.
- Community billing lore (for example: generic pooling claims) is labeled as
   community-claim, never as vendor truth. Nothing here asserts what the
   platform bills — only what this run's reads showed.

## Output

A dated markdown file with the quoted wordings, the client reads with dates,
the per-wording prediction check, the behavior verdict (or open contradiction
with evidence), diff vs. prior run, and gaps with owner and date. End with
the single read that most needs re-checking and when.

## Boundaries

- Reads only. Never toggle on-demand, change a monthly limit, alter billing
   settings, or consume allowance to "test" pooling — those need the owner's
   explicit yes on that exact setting, and "measure while you're in there" is
   never approval.
- Never paste credentials, tokens, or secret values into reads, findings, or
   drafts; `${VAR}` names only.
- Never report pool behavior from memory, assumption, or arithmetic the client
   never rendered: unmeasured means open, and open means rerun, not assumed.
- Never present a closed contradiction as permanent: it proves the behavior on
   this read date against these figures, not that every future cycle matches.
