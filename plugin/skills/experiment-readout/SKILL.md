---
name: experiment-readout
description: Use when someone reports a test result, an A/B outcome, a conversion change, a before/after, or any number they are about to make a decision on. Use before agreeing that something "worked".
---

# Experiment readout

Turn a reported result into a decision that is actually supported.

## What to read, in this order

The reported result, sample sizes, window, and outcome definition. Then:

## Sequence


1. **Effect** — the difference, in the unit the reader cares about (percentage points, dollars,
   minutes), not in standard deviations.
2. **Interval** — a 95% interval around that effect. An effect without one is an anecdote.
3. **Assumptions** — what has to be true for the interval to mean anything: independent
   observations, stable population, no mid-test change, outcome measured the same way in both
   arms. Name the ones you cannot verify from what you were given.
4. **Verdict** — supported / unsupported / **underpowered**. Underpowered is the most common
   honest answer and the one people most want skipped.

## Validation


| asked for | say |
|---|---|
| "is it significant?" | the effect and its interval, then whether the interval excludes no-effect |
| a p-value on its own | the p-value WITH the effect size, or decline — a p-value alone cannot size a decision |
| "can we stop the test early?" | what stopping early does to the error rate, and the observations needed for the planned power |
| a causal claim from observational data | the association, and the specific confounder that would have to be ruled out |

## When the data is too small

Say so in one line, then give the number: "n=40 per arm detects a 15pp change at 80% power; you
are looking for 3pp, which needs ~1,700 per arm." A required sample size is actionable. "Not
significant" is not.

## Output

Four lines, then the python you ran. If a decision is riding on it, end with the one measurement
that would change your verdict.

## Boundaries

- Read-only analysis. Never start, stop, resize, or reconfigure a test — report the verdict and stop.
- Never upgrade an association into a cause. The confounder line is the boundary, not a formality.
- If the data cannot support any verdict, say underpowered and stop. Do not shop for a method that flatters the number.
