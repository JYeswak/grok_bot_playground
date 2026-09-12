---
name: scenario-sim
description: Run scenarios with parameter logs and outputs. Use when asked what happens under different inputs, to compare two scenarios side by side, or to check a claim about a modeled outcome before anyone relies on it.
---

# Scenario sim

Run a scenario through a simulation and report what came out, with the chain from inputs
to parameters to outputs unbroken and inspectable. This skill exists because modeled
numbers travel fast and their assumptions travel slow, and because an output whose
inputs and parameters cannot be traced back is indistinguishable from invention. The
local simulation capability this assumes is mirror-backed (community-claim): a machine
on the owner's side that runs parameterized scenario models and returns logged outputs
— never a vendor endpoint, and no specific tool or API is assumed beyond "given inputs
plus parameters, it returns outputs with a log".

## The rule that matters more than the output

**An output without its inputs is a rumor.** Every number traces to the exact inputs,
the full parameter set, and the run date — and any defaulted parameter, seeded random
draw, or narrowed scope is labeled as such, never presented as "what the model says".

## When to use

- Asked what happens under different inputs, or to model an outcome before deciding.
- Asked to run two scenarios (compare, contrast, or run each separately) with each
  scenario's chain kept distinct.
- Asked what a simulation actually assumes versus what its headline number implies.
- Do not use for fitting models to data, for statistical inference on observations,
  or for certifying a real-world outcome — this skill runs scenarios and reports
  logged outputs only.

## Inputs and access

- **Scenario input(s)** — one input set for a single run, two for a comparison.
  Freeze each first: the question asked, the input values, and the horizon or
  scope claimed.
- **Parameter set** — every parameter the run uses, with defaults flagged as
  defaults and random seeds recorded. An unlogged parameter is an unknown, not a
  reasonable value.
- **Access** — the local simulation capability plus whatever the Bot can already
  read. If a run needs data, a model, or a credential you were not given, record
  it as unreachable and move on. Do not improvise access. Name any configuration
  only as `${VAR}`-style variable names, never values.

## Sequence — what to do, in this order

1. **Freeze the question** — what is being asked, input values, scope. With two
   scenarios, keep the blocks and everything downstream separate.
2. **Log the parameters** — full set, defaults flagged, seeds recorded. A run with
   unknown parameters proves nothing about anything but itself.
3. **Run the scenario** — inputs plus parameters through the capability. Record
   method (which model, which parameter set, run date) and the raw outputs.
4. **Validate the run** — check the validation section below before writing a
   single conclusion. A run that fails validation is a finding ("key parameter
   defaulted", "scope narrowed mid-run", "seed unrecorded"), not a quiet gap to
   conclude around.
5. **Mark the gaps** — defaulted parameters, unmodeled effects, and anything the
   conclusion deliberately excludes. End with the single most consequential
   output per scenario and what it would take to verify it further.

## Validation

- Logged outputs present for the claimed inputs and parameters; a missing log
  fails, never silently narrows, the report.
- Numbers quoted against the raw log — no rounded-into-agreement figures, no
  cherry-picked runs passed off as representative, no outputs cited beyond the
  scope they were run under.
- Parameter identity stated: full set with defaults flagged and seeds recorded.
  A run with defaulted parameters never concludes with the confidence of a fully
  specified one.
- Two-scenario reports keep chains separate: no output from scenario A cited
  under scenario B, no shared claim without a log on each side.
- Anything defaulted, unmodeled, or out of scope is labeled UNKNOWN with the
  reason, never smoothed over.

## Output

Per scenario: a source block (question, inputs, full parameter set with defaults
flagged, seeds, run date), the logged outputs that carry the conclusion, the
conclusion itself with inline references to the log, and a gaps list. With two
scenarios, deliver two chains plus a short comparison that cites each side
separately. End with the single most consequential point and the decision or
follow-up it needs — not a wall of numbers.

## Boundaries

- Run-only plus conclusions. Never tune the model to favor an answer, fetch
  missing data on credit, or spend on compute or enrichment while simulating.
- Approval boundary: this skill runs scenarios for the owner to use. Committing
  to a plan, quoting a modeled number publicly, or acting on a consequential
  output belongs to a routine with its own approval — nothing here decides.
- Never present community modeling lore (which parameters work, which scenarios
  "always converge") as vendor truth. State what ran today, for these inputs,
  with these parameters.
- Never paste credentials, tokens, or account identifiers into inputs, logs, or
  findings. If a run needs access you lack, record it as unreachable and move
  on.
