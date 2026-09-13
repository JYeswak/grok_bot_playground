---
name: experiment-prereg
description: Write experiment preregistrations before any run. Use when asked to preregister a study, lock a hypothesis with method and stop rules, or put two preregistrations on record before data is collected.
---

# Experiment prereg

Write the preregistration before the run: hypothesis, method, and stop rules locked on
record while the data is still uncollected. This skill exists because the sibling
experiment-readout skill can only judge a result honestly when the plan it judges
against was written before the numbers arrived — a hypothesis reconstructed after the
fact is indistinguishable from a story about noise. No local execution capability is
assumed here (no mirror counterpart): this skill produces documents, not runs.

## The rule that matters more than the document

**A plan written after the data is a rumor.** Every preregistration carries its lock
date and the explicit statement that no outcome data was seen — and any analysis
choice left open, any rule added mid-run, any peek at the data is labeled as such,
never folded silently into "the plan all along".

## When to use

- Asked to preregister an experiment, lock a hypothesis, or put a study plan on
  record before running.
- Asked to write two preregistrations (compare designs, or lock two studies
  separately) with each document's chain kept distinct.
- Asked whether a planned analysis can support the claim it wants to make.
- Do not use for analyzing already-collected data, for salvaging a run that
  already peeked, or for running the experiment itself — this skill writes the
  locked plan only.

## Inputs and access

- **Research question(s)** — one question for a single preregistration, two for
  two documents. Freeze each first: the claim to test, stated in the unit the
  reader cares about, and what decision rides on it.
- **Design sketch** — arms or conditions, outcome measure, planned sample, and any
  constraints (population, window, measurement method) the owner provides.
- **Access** — nothing beyond what the Bot can already read. Preregistration
  needs no data access; if background numbers are needed to size the study, cite
  them as context, never fetch on credit. If sizing needs a credential you were
  not given, record it as unreachable and move on. Name any configuration only
  as `${VAR}`-style variable names, never values.

## Sequence — what to do, in this order

1. **Freeze the question** — claim, unit, decision at stake, lock date. With two
   preregistrations, keep the blocks and everything downstream separate.
2. **Write the hypothesis** — directional claim plus the effect size that would
   matter, in the reader's unit. A claim without a size is untestable; say so
   and fix it before proceeding.
3. **Write the method** — arms, outcome measure, sample per arm with the sizing
   arithmetic shown, assumptions required (independence, stable population, no
   mid-test change, same measurement in all arms), and which assumptions cannot
   be verified yet.
4. **Write the stop rules** — planned sample, what early stopping does to the
   error rate, and the explicit rule for peeking: no peeking, or a named
   correction with its cost stated.
5. **Validate the document** — check the validation section below before locking.
   A document that fails validation gets fixed or stays unlocked, never quietly
   filed as "preregistered".

## Validation

- Hypothesis present with a mattering effect size in the reader's unit; a vague
  claim fails, never silently narrows, the document.
- Method complete: arms, measure, per-arm sample with sizing shown, assumptions
  named including the unverifiable ones.
- Stop rules explicit: planned sample, early-stopping cost, peeking rule. "Stop
  when it looks good" fails the document.
- Data-unseen statement present: lock date plus confirmation no outcome data was
  seen. Anything already observed is labeled as pilot or background, never as
  uncollected.
- Two-document writes keep chains separate: no hypothesis from study A filed
  under study B, no shared stop rule without a copy in each document.

## Output

Per study: a locked block (question, hypothesis with effect size, method with
sizing arithmetic, assumptions, stop rules, lock date, data-unseen statement).
With two studies, deliver two blocks plus a short note on where the designs
differ and why. End with the single measurement that would change the planned
verdict — not a wall of caveats.

## Boundaries

- Write-only plus sizing. Never collect data, run the experiment, or analyze
  outcomes while preregistering.
- Approval boundary: this skill locks plans for the owner to use. Running the
  study, changing a locked rule mid-run, or claiming a result was predicted
  belongs to a routine with its own approval — nothing here runs or confirms.
- Never present community methodology lore (which corrections to prefer, which
  designs "always work") as vendor truth. State what this plan assumes, for this
  question, on this date.
- Never paste credentials, tokens, or account identifiers into a question,
  document, or finding. If sizing needs access you lack, record it as
  unreachable and move on.
