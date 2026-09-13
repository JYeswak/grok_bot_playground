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

## Machine contract (v92v.3.1) — the locked fields

Every locked plan carries EXACTLY these machine fields. Prose may explain them;
only the fields lock. The fail-closed validator (v92v.3.2) enforces this table —
this skill names each field and its handoff, and claims no enforcement itself.

| field | meaning | enforcement handoff |
|---|---|---|
| `null_hypothesis`, `alternative_hypothesis` | the two competing claims, both stated | both present, non-empty, distinct |
| `experimental_unit` | the single randomized/assigned entity | present; one unit only |
| `target_population`, `target_account`, `target_version` | who/what/version the claim covers | all present; version pinned, never "latest" |
| `estimand`, `direction` | what is estimated and which way matters | present; direction one of increase/decrease/difference |
| `baseline_source`, `baseline_window`, `baseline_freshness` | authoritative baseline: where, which window, how fresh at lock | source named, window closed before lock, freshness dated |
| `control` OR `no_control_justification` | the control design, or why no control is honest | exactly one of the two present |
| `assignment`, `order` | randomization/assignment method and run order | present; "haphazard" fails |
| `inclusions`, `exclusions` | who/what gets in and what is cut, decided now | both present (may be explicit empty with reason) |
| `independence_unit` | what counts as one independent observation | present; retries/duplicates of one unit never count twice |
| `carryover`, `washout` | cross-condition contamination and how it clears | present; "none" needs a reason, not silence |
| `confounders` | known confounders and how each is handled | list present (may be empty with reason) |
| `observation_channel` | the exact read path for outcomes | present; must name the artifact/command, never "the dashboard" |
| `decision_mode` | `deterministic` or `statistical` | one of the two; statistical needs the analysis below |
| `primary_score`, `primary_analysis` | the one score and the exact analysis that reads it | both present; exactly one primary |
| `falsifier` | the observation that kills the claim | present and concrete |
| `repeat_plan` | repeats/replication before adoption | present; count, spacing, and what varies |
| `adopt_rule`, `reject_rule`, `inconclusive_rule` | mutually exclusive terminal rules | all three present; every outcome lands in exactly one |
| `lever`, `lever_target` | EXACTLY ONE mutable intervention and its singular target | one lever, one target; zero/two levers fail; bundled targets fail |
| `candidate_id`, `qualification_receipt_id`, `source_receipt_digests` | lineage: canonical candidate, its qualification receipt, every source digest | all present; digests verifiable at readback |
| `non_lever_reads`, `guardrails`, `rollback_plan` | reads that change nothing, tripwires, and how to undo | present; reads never mutate, guardrails name thresholds, rollback names argv |
| `locked_at`, `data_unseen_statement` | lock timestamp and the no-data-seen attestation | present; lock precedes all outcome access |
| `plan_id`, `plan_hash` | identity: hash over the canonical lock fields | `plan_hash` = sha256 of the canonical field set; ANY changed lock field creates a new id/hash (frankensim campaign-identity doctrine, fh STALE 125.1h) |

## Worked shapes (valid vs refused)

- **Complete (locks):** one lever (`subject` line of one draft), one target (one
  named Bot), control (draft held back on alternate days), falsifier (open rate
  down ≥2pts over 200 sends), all digests verifiable.
- **Zero-lever (refused):** an observation plan with no intervention names no
  `lever` — that is a measurement, not an experiment; file it as one.
- **Two-lever (refused):** subject line AND send time change together — effects
  cannot be attributed; split into two plans.
- **Bundled-target (refused):** "all CFS Bots" as one target — units must be
  individually addressable; one plan per Bot or a named sampling rule in
  `assignment` with the unit still singular.
- **Changed-after-lock (new id):** the window moves after lock — the old
  `plan_id` is dead; re-lock under a new id/hash, never edit in place.
- **Digest-mismatch (refused):** a `source_receipt_digest` that does not verify
  at readback — the lineage is broken; re-establish provenance, never proceed.

## Output

Per study: a locked block carrying the full machine-contract field set above
(question, hypotheses, unit/population/version, estimand, baseline, control,
assignment, inclusions/exclusions, independence, carryover/washout, confounders,
channel, decision mode, primary score/analysis, falsifier, repeat plan,
three-way rules, the one lever and target, lineage digests, reads/guardrails/
rollback, lock attestation, plan id/hash). With two studies, deliver two blocks
plus a short note on where the designs differ and why. End with the single
measurement that would change the planned verdict — not a wall of caveats.
Field enforcement belongs to the v92v.3.2 validator; this skill writes plans
the validator can lock, and never claims a plan is locked.

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
