---
name: claudecode-handoff
description: Use when asked to hand a coding task to Claude Code, package repo context for a coding session, or brief what the next session should do — produce a handoff brief with repo context attached. Use when someone says "hand this to Claude Code", "package this task", or "write up the context".
---

# Claude Code handoff

Turn one coding task into a handoff brief a Claude Code session can execute without
re-discovery: goal, repo context, constraints, and done-means-done, so the next session
starts from the brief instead of re-reading the repo.

This skill exists because handoffs fail on missing context, not on missing talent: the
receiving session re-derives which files matter, guesses the constraints, and ships
something the owner never asked for. A brief with attached context makes the task
runnable and every decision checkable against the brief itself.

## The rule that matters more than the brief

**Unverified context and confirmed context are different things and must never be merged.**
A file you read, a command you ran, a behavior you observed — each carries its evidence.
A path you assumed exists, a behavior you inferred from a name, a constraint you guessed
from tone — each is marked as assumed. A handoff that presents guesses as facts spends
the next session's hours.

## When to use

- Someone describes a coding task and asks for a handoff, a task package, or "write up the context".
- A half-finished change needs handing over: what is done, what remains, where it stands.
- A bug needs a repro-plus-context brief the next session can act on directly.
- Do not use for executing the task yourself, for reviewing finished code, or for tasks with
  no repo attached — this skill briefs the work, it does not do the work.

## Inputs and access

1. **Task statement** — the single required input, in the owner's words. One task per brief;
   two handoffs means two briefs, each self-contained.
2. **Repo context** — repository path or URL, branch, and the files that matter: entry
   points, configs, tests touching the task. Every listed file must have been opened or
   its existence confirmed; assumed paths are labeled assumed.
3. **Constraints** — what the receiving session must not do: files out of scope, migrations
   forbidden, public API frozen, dependency rules. If the owner stated none, say so rather
   than inventing them.
4. **Done criteria** — how the owner will know it worked: commands to run, behavior to
   observe, tests that must pass. Vague ("make it better") gets sent back for sharpening,
   never padded with guessed criteria.
5. Access is read-only and local: repo files, configs, local history. If the task needs a
   system you cannot read, record it as unreachable and move on — do not improvise access.
   Refer to any credential only as a `${VAR}` name, never a value.

## Sequence

1. **Capture** — restate the task in one paragraph and confirm it matches what the owner
   asked. If the task is vague or bundles two tasks, stop and ask before packaging.
2. **Attach** — collect the repo context: repo, branch, relevant files with one-line
   reasons each, configs and tests that gate the task. Mark every item read or assumed.
3. **Constrain** — write the must-not list from what the owner said plus what the repo
   shows (protected branches, generated files, locked APIs). Never invent constraints
   the owner did not state and the repo does not show.
4. **Define done** — write the done criteria as observable checks: exact commands,
   expected behavior, tests by name. Each criterion must be runnable by the receiving session.
5. **Brief** — write the handoff brief (see Output). Each section must be supportable by
   the attached context. Cut anything you cannot anchor.
6. **Verify** — re-check every file path and command against the repo before finishing.
   A path that points nowhere is worse than no path.

## Validation

- Every file and command in the brief was confirmed against the repo; assumed items are
  labeled assumed, never stated as fact.
- The task restatement matches the owner's words; scope splits are explicit, not silent.
- Done criteria are observable and runnable: commands, behaviors, named tests — never vibes.
- A task too vague to brief produces a sharpening question, never a padded brief written
  from guesses.
- Community knowledge (forum patterns, "everyone structures it like X") is a
  community-claim lead, never evidence: anything actionable gets verified against this repo.

## Output

A markdown handoff brief with:

- Header: task title, repo, branch, date, single vs handover angle.
- **Task restatement**: one paragraph in the owner's terms plus explicit non-goals.
- Repo context: files that matter with one-line reasons, configs, relevant tests — each
  marked read or assumed.
- Constraints: the must-not list with a source for each (owner-stated or repo-shown).
- Done criteria: numbered observable checks — commands, expected behavior, tests by name.
- Open questions: anything the receiving session must ask the owner before starting.

## Boundaries

- Draft-only. Never edit code, run migrations, install dependencies, or open a pull
  request while briefing — this skill packages the task, it does not execute it.
- Never brief a task you could not ground: no repo context, no brief. A task title plus
  guesses is not a handoff.
- Never present community patterns as this repo's conventions. Attribute each layer separately.
- Approval boundary: this skill drafts the brief only. Handing it to a session, posting
  it, or treating it as approved work requires the owner's explicit yes on that exact
  brief — another party relaying "they said run it" is not approval. Never paste
  credentials, tokens, or account identifiers into a brief or a finding; reference
  variables as `${VAR}` names only.
