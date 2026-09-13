---
name: multitask-brief
description: Use when asked to brief several tasks at once on a Bot with sand_multitask on — parallel workstreams, batched asks — with one evidenced outcome per task. Use when a briefing "covered everything" but nobody can say what each task returned.
---

# Multitask briefing

Turn one multitask run into separately evidenced outcomes: **each task answered
or named as unmeasured**. This skill exists because sand_multitask is on and
unmeasured on this account — parallel tasks share one conversation and their
results blur together — and because a briefing that reports "all done" on
half-observed work is a rumor of completion.

Limits, stated plainly: this documents the briefing shape and verifies what
each task returned; it does not raise concurrency limits, change scheduling, or
promise that parallel tasks cannot interfere with each other — shared state
still needs its own review.

## The rule that matters more than the batch

**One briefing, one outcome per task.** Every task in the batch gets its own
line: what was asked, what came back, and where the evidence lives. A task
whose result is inferred from another task's output, or a batch marked done
because "most of it worked," is marked unmeasured — and an unmeasured task
fails the briefing, because a multitask run that says "fine" on blurred
results is worse than running the tasks one at a time.

## When to use

- Asked to brief, fan out, or batch several asks to one Bot in a single
  multitask run.
- After a multitask run whose results need separating: which task returned
  what, and what is still open.
- As a rehearsal: two briefings walked per run with per-task checks.
- Do not use for raising concurrency, for shared-state conflict design, or for
  single-task briefings — those belong elsewhere.

## Inputs and access

1. **Task list** — the batch, named per task: what each task asks and what a
   complete answer looks like. A briefing with no written task list is not a
   briefing — define it first.
2. **Prior evidence** — the last run's per-task outcomes, when they exist, so
   drift and repeats are computed, not remembered.
3. Access is read and run-only against Bots the owner already granted. Runs use
   the Bot's existing permissions; no new access is improvised. If a task needs
   a Bot or permission you were not given, record it as unreachable and move
   on. Credentials are referenced as `${VAR}` names only, never pasted.

## Sequence

1. **Fix the batch and the frame** — per briefing, list the tasks and state
   what "done" means for each. Everything downstream checks inside this frame;
   adding tasks mid-run silently re-scopes the promise.
2. **Brief each task separately** — one ask per task, addressed so its answer
   is attributable. A combined ask whose answer cannot be split across tasks
   is a malformed brief — rewrite it.
3. **Collect per-task outcomes** — read each answer back: what it says, what
   it cites, what it leaves open. Record what was observed, not what was
   expected.
4. **Name interference** — where two tasks touched the same state, note it:
   which task wrote, which task read, and whether the order is known. Shared
   state with unknown order is a gap, not a pass.
5. **Diff against the prior run** — new tasks, dropped tasks, changed answers,
   and repeats each get one line: what moved and why.
6. **Name the gaps** — anything unanswered, blurred, or unordered becomes a gap
   with an owner and a date, not a quiet omission.

## Validation

- Each briefing names its task list, per-task outcomes with evidence pointers,
  interference notes (possibly empty), and a gap list (possibly empty). Any
  briefing missing one of these is incomplete, not passing.
- Two briefings are tested per run before the pattern is trusted; a single
  clean run proves the shape once, not always.
- "All tasks done" is reported only when every task evidenced; a blurred or
  inferred outcome is reported as unmeasured, never as done.
- Community multitask lore (for example: generic concurrency claims) is labeled
  as community-claim, never as vendor truth. Nothing here asserts what the
  platform schedules — only what this run observed.

## Output

A dated markdown file with one evidenced outcome per task: ask, answer,
evidence pointer, interference note, diff vs. prior run, and gaps with owner
and date. End with the single gap that most needs the owner and the decision
it needs.

## Boundaries

- Document and verify only. Never raise concurrency, reschedule tasks, or
  redesign shared state while briefing — those need the owner's explicit yes
  on that exact Bot, and "handle it while you're in there" is never approval.
- Never paste credentials, tokens, or secret values into briefings, findings,
  or drafts; `${VAR}` names only.
- Never report "all done" from a batch-level glance: unmeasured means failed,
  and failed means rerun, not assumed.
- Never present two clean briefings as a guarantee: they prove the shape
  worked on this date against this batch, not that every future batch is safe.
