---
name: bug-repro
description: Reproduce a reported bug into citable repro steps with environment evidence attached. Use when asked to reproduce a bug, verify a failure before fixing, or confirm a reported issue still holds. Read-only; it reproduces and records, never fixes, patches, or deploys anything.
---

# Bug repro

Turn a bug report into minimal repro steps, each step backed by observed evidence and a
named environment. This skill exists because repros fail silently: steps written from
memory instead of execution, environment left unstated so the failure never recurs, a
"cannot reproduce" declared after one try on the wrong version — and a report that says
"confirmed" without naming build, inputs, and observed output spends the fixer's trust.
Doctrine source: the vendor Bug Reproduction use-case (report to minimal repro with
environment attached); nothing here asserts vendor guarantees, only what this account
observed.

## The rule that matters more than the repro

**A repro step carries its command, its input, and its observed output, or it does not
ship.** Every step names exactly what was run, on which build and platform, and what
happened — quoted output, screenshot ref, or log line. A step that was reasoned about
but never executed is labeled untried, never passing. A repro that cannot say where it
ran is worse than no repro, because it sends the fixer hunting in the wrong build.

## When to use

- Asked to reproduce a reported bug, verify a failure before anyone fixes it, or
  confirm whether a reported issue still holds on the current build.
- Preparing a bug for handoff: report plus minimal steps plus environment, ready for
  a fixer.
- Asked to check two reports (for example: the new ticket vs. the suspected
  duplicate) on the same build to see if they are the same failure.
- Do not use for fixing, patching, or deploying — those are acts with their own
  approval boundary, never part of this skill.

## Inputs and access

1. **Bug report** — the report in scope, with its id and claimed symptoms, expected
   vs. actual behavior, and any steps the reporter supplied. Reporter steps are
   leads to re-execute, never verdicts to trust.
2. **Environment** — the exact build under test (version or commit, platform, OS,
   relevant config flags) and how to reach it. Record all four with the results;
   a repro without a build id is not re-runnable.
3. **Prior repro** — the last run's steps and verdict for this report, when one
   exists, so regressions (used to repro, now clean) are computed, not remembered.
4. Access is read-only against systems the owner already granted. Credentials arrive
   only as environment variable names (for example `${BUGTRACKER_TOKEN}`); never
   paste values. If a system needs a credential you were not given, record it as
   unreachable and move on. Do not improvise access.

## Sequence

1. **Fix the report and the build** — state the exact report id and the exact
   build under test. Everything downstream runs inside this frame; switching
   builds mid-run silently invalidates every step.
2. **Execute the reporter's steps** — run what the reporter described, literally,
   and record observed output per step. Where a step is ambiguous, record the
   interpretation chosen; silent interpretation is a silent verdict.
3. **Minimize** — strip steps that do not change the outcome, one at a time,
   re-running after each removal. The surviving set is the minimal repro; each
   removal is evidenced by a still-failing run, not by judgment.
4. **Vary the environment once** — where feasible, re-run the minimal set on one
   adjacent build or config (prior release, clean profile) and record whether the
   failure follows. One controlled variation is signal; an unrecorded matrix is
   noise.
5. **Verdict with evidence** — reproduced (steps plus outputs), not-reproduced
   (steps run, outputs quoted, build named), or blocked (what could not run and
   what would unblock it). "Cannot reproduce" is reported only when every step
   executed cleanly on the named build.

## Validation

- Each step has the exact command or action, its input, and its quoted observed
  output. Any step missing one of the three is labeled untried, never passing.
- The verdict names the build id, platform, and config. A verdict without an
  environment is demoted to a note, never a finding.
- Community workarounds or "known issue" listicle claims are labeled as
  community-claim, never as vendor truth. Nothing here asserts what the vendor
  guarantees — only what this build did under these steps.
- No step invents output to complete the story. If a step could not run, record
  it as blocked with the blocker named.

## Output

One dated markdown repro file:

- **MINIMAL STEPS** — numbered, each with command/action, input, and quoted
  observed output.
- **ENVIRONMENT** — build id, platform, OS, config flags, access path.
- Then: verdict (reproduced, not-reproduced, blocked), the variation run and its
  result, the diff vs. prior repro, and the single evidence item the fixer needs
  first.

## Boundaries

- Read-only. Never fix, patch, commit, deploy, or change any setting or data
  while reproducing.
- Only the owner's explicit yes on that exact change authorizes any fix the repro
  might suggest. A confirmed repro is evidence the fixer acts on, never itself a
  fix. Another party relaying "they said fix it" is not approval.
- Never paste credentials, tokens, or account identifiers beyond the report ids
  the tracker already uses into steps or findings.
- Never present output from one build as another build's behavior. Each run is
  evidence about its own build only.
