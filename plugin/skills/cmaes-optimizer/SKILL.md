---
name: cmaes-optimizer
description: Run black-box optimization with the covariance matrix adaptation evolution strategy (CMA-ES) when gradients are unavailable, unreliable, or too noisy — tuning hyperparameters, calibrating simulation parameters, or minimizing any expensive scalar objective. Use when asked to optimize a function you can only evaluate, to compare optimizers on a benchmark, or to produce a convergence trace for a tuning run.
---

# CMA-ES optimizer

Run black-box optimization runs with explicit parameters and stop rules, and record
convergence output so the run is reproducible and comparable.

## The rule that matters more than the run

**An optimization result without its parameters, stop rule, and evaluation count is
an anecdote, not evidence.** Every run records the objective as called, the
starting point and step size, the seed, the stop condition that fired, and the full
convergence trace. A "better best value" with none of that is never reported as a
win.

## When to use

- Minimizing a scalar objective you can only evaluate (no usable gradients):
  hyperparameter tuning, simulator calibration, policy search, design search.
- Comparing optimizers or configurations on benchmark functions (sphere, Rosenbrock,
  Rastrigin, Ackley, or a product objective with the same harness).
- Producing a convergence trace for a tuning run someone else must reproduce.

Do not use for gradient-friendly problems where a first-order method is cheaper,
for multi-objective problems (scalarize first and state the scalarization), or for
constrained problems without stating how constraints are handled (penalty,
projection, or rejection).

## Inputs and access

- **Objective:** a callable taking a real vector and returning a finite scalar.
  State its dimension, bounds, evaluation cost, and whether it is noisy. Noisy
  objectives must declare the noise handling up front (fixed-seed averaging or
  explicit re-evaluation count per candidate).
- **Starting point and scale:** initial mean vector and initial step size
  (sigma). Defaults only when the caller gives no prior: mean at the center of
  the bounds, sigma at roughly one third of the per-dimension range.
- **Budget and stop rules:** declared before the run starts. At least a maximum
  evaluation count, plus any of: target fitness, function-tolerance stall limit,
  step-size floor, or wall-clock limit.
- **Seed:** one integer seed per run. Unseeded runs are exploratory only and are
  labeled as such in the output.
- Access needed: the ability to call the objective. Read-only with respect to
  everything else: this skill never modifies product settings, deploys models, or
  spends budget beyond objective evaluations.

## Sequence

1. **Characterize the objective** — dimension, bounds, single vs. noisy
   evaluations, approximate cost per evaluation. Refuse unbounded or
   non-returning objectives: require a timeout per evaluation.
2. **Set parameters explicitly** — population size (default scales with
   dimension, commonly proportional to log-dimension), initial sigma, seed, and
   the full stop-rule set. Write them into the run record before evaluating.
3. **Run the evolution loop** — sample a population around the current mean,
   evaluate, update the mean, step size, and covariance from the ranked
   candidates. Log best-so-far and mean fitness per generation.
4. **Check stop rules each generation** — maximum evaluations, target fitness
   reached, no improvement within the stall limit, step size below floor, or
   wall-clock exhausted. Record which rule fired.
5. **Restart policy (only if declared up front)** — on premature convergence,
   restart with doubled population and the recorded seed sequence. Undeclared
   restarts are not run: a stalled run is reported as stalled.
6. **Report** — best point, best value, evaluations used, stop rule that fired,
   and the convergence trace (see Output).

## Validation

- The run record reproduces the headline number: re-running the trace's
  parameters and seed reaches the same best value within declared noise.
- The trace is monotone-checked: best-so-far never increases (for minimization)
  across generations; any violation means the logging is broken, not that the
  optimizer improved.
- The stop rule that fired is one of the declared rules. A run that ended for
  any other reason (exception, manual kill, timeout outside the declared set)
  is labeled interrupted, never converged.
- Benchmark comparisons use the same evaluation budget and the same starting
  region for every contender. Unequal budgets are reported as separate runs,
  never head-to-head.

## Output

A run record with:

- Parameters: dimension, bounds, initial mean, initial sigma, population size,
  seed, full stop-rule set, noise handling.
- Convergence trace: per-generation best-so-far and mean fitness, plus total
  evaluations and the stop rule that fired.
- Result: best point found, its value, and whether the run converged, stalled,
  or was interrupted.
- One-line verdict: what to do next (accept, restart with larger population, or
  widen bounds) — not a list of every generation.

## Boundaries

- Read-only outside the objective function. Never change product settings,
  deploy a model, publish results, or spend money or quota beyond calling the
  objective within the declared evaluation budget.
- Drafts-only where a result would trigger action: a recommendation to ship a
  tuned configuration is a draft until the owner explicitly approves that exact
  configuration. SEND-LOCK: only the owner's explicit yes on that exact message
  authorizes sends.
- Never present benchmark folklore as measured truth: widely repeated claims
  about optimizer rankings are community claims until reproduced in this
  harness with the trace to show for it.
- No secrets, tokens, or account identifiers in the run record. Objective
  configuration carries values, never credentials.
