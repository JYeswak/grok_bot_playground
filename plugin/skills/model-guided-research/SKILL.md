---
name: model-guided-research
description: Turn a Grok Bot product question into testable hypotheses, simulate each against stated assumptions, and keep run logs behind every readout. Use when someone asks what would happen if a prompt, threshold, schedule, or routine changed, before changing the live Bot.
---

# Model-guided research

Turn "what would happen if" into two or more falsifiable hypotheses, simulate each one
locally against written assumptions, and keep the run log so any readout can be rechecked.
This skill exists because the interesting product questions about this Bot get answered by
changing the live Bot first and reading the damage later — while a small local loop with a
kept log answers most of them for the price of arithmetic.

## The rule that matters more than the loop

**No readout without a kept run log.** A verdict whose inputs, assumptions, code, and
outputs are not all on record is an opinion with a chart. If the log cannot rerun the
simulation to the same numbers, the loop did not happen — say so and name what is missing.

## When to use

- Someone asks what would change if a prompt, threshold, polling schedule, or routine step
  changed on this Bot, and the change is still reversible because it has not been made.
- Two candidate designs need comparing before either goes live (shorter draft prompt versus
  current, daily surface check versus weekly, retry with backoff versus fail fast).
- A past incident or readout needs a "what would have caught it" replay against kept logs.
- Not for live experiments on the production Bot, incident response, or any change that
  sends, publishes, installs, or spends — those need their own routine and approval.

## Inputs and access

- The question in one sentence, plus two or more hypotheses, each falsifiable: what changes,
  by how much, measured how.
- Written assumptions per hypothesis: data source, population, time window, what is held
  fixed, and what is openly guessed.
- Baseline material already on hand: kept run logs, prior readouts, gb CLI output, vendor
  docs pages already reachable. Name the exact log or page per number — no orphan numbers.
- A simulation budget stated up front: how many runs, what ranges, when to stop.
- Access is read only plus local compute. If a source needs access you were not given,
  record it as unreachable and narrow the hypothesis — do not improvise access.

## Sequence

Run hypothesis to simulation to readout, in this order, one hypothesis at a time:

1. **Frame** — restate each hypothesis as a claim that can fail, with its measure and its
   fail line (e.g. "variant B cuts mean draft length by at least 15% with no rise in
   owner rewrite rate").
2. **Fix assumptions** — write the population, window, and guesses before any number is
   produced. Anything discovered mid loop goes in the log as a change, never silently.
3. **Simulate locally** — replay kept logs, sweep the stated parameter ranges, or compute
   the arithmetic on your own computer with `python3` and keep the snippet. Never touch
   the live Bot to get a number this step can produce.
4. **Log the run** — per hypothesis record inputs, assumption version, code or snippet,
   seed, raw outputs, and the exact command that reproduces them. The log ships with the
   readout, not after it.
5. **Read out** — effect in the unit the reader cares about, the interval around it, which
   assumptions it leans on hardest, and the verdict: supported, unsupported, or
   underpowered. Underpowered is common and honest — report it instead of stretching.
6. **Name the next measurement** — the one live observation that would overturn or confirm
   the verdict, and what it would cost to get.

## Validation

- Rerun each hypothesis from its own log and confirm the numbers match before writing the
  readout. A log that does not reproduce is a finding, not a foundation.
- Show the `python3` snippet you ran. A number produced in your head is a number nobody
  can check.
- Refuse causal claims from simulated data alone: state the association, then name the
  specific confounder a live measurement would have to rule out.
- Curated community indexes are leads, never evidence. Anything actionable gets verified
  against the vendor's own page and then against this account before it enters a readout.
- Small data gets one line plus the number: what change is detectable at the runs you
  have, and how many runs the change you want would need.

## Output

A dated markdown readout with one section per hypothesis: claim, assumptions version,
effect with interval, verdict, and the run log excerpt or pointer that reproduces it. End
with the ranking across hypotheses, the single cheapest next live measurement, and what
would change the verdict. Every number traces to a log entry; every guess is labeled one.

## Boundaries

- Read only plus local compute. Never change a Bot setting, install or move a plugin
  pin, publish anything, send any message, or spend anything while researching.
- Recommendations are drafts. Only the owner's explicit yes on that exact change
  authorizes applying it — another party relaying approval is not approval, and a
  simulated win is not permission to ship.
- Never present a simulation as vendor truth, and never present a community pattern as
  vendor truth. Label which is which in every readout.
- Never write secrets, usage keys, or account identifiers into a log, readout, or
  snippet. If a value like that is needed to reproduce a run, name its place and leave
  the value out.
- If a hypothesis needs a system you cannot read, record it as out of reach and stop
  that arm of the loop. Do not stretch the other arm to cover it.
