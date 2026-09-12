---
name: backup-runbook
description: Use when asked to back up, verify, or restore a Bot's working state — configs, data, credentials map — with a walked checklist and evidence per step. Use when a teammate asks "could we rebuild this if the machine died" or before a risky migration.
---

# Backup runbook

Turn two runbook walks into checklist evidence: **backed up and verified**, or **gap
named with owner and date**. This skill exists because replicas sit at client caps and
disk-saver surfaces keep no runbook — a backup nobody restored is a hope, not a plan —
and because a checklist step without evidence is a rumor of safety.

Limits, stated plainly: this walks the runbook and verifies the artifacts; it does not
replace qualified recovery planning for regulated or high-stakes data, and restores are
rehearsals in a sandbox unless the owner explicitly orders a live restore.

## The rule that matters more than the checklist

**A step counts only with evidence.** Every checked step names what was observed (file
listed, hash matched, restore rehearsed, error seen). A step marked done from memory,
or a verify skipped because the backup "surely worked," is marked unmeasured — and an
unmeasured step fails the run, because a runbook that says "fine" on broken inputs is
worse than no runbook.

## When to use

- Asked whether a Bot's state (configs, skills, routines, credentials map) could be
  rebuilt after machine loss, disk pressure, or a bad migration.
- Before a risky migration, OS upgrade, or provider move.
- As a periodic rehearsal: two runbooks walked per run with checks.
- Do not use for live incident recovery command (that is an incident routine with its
  own approval) or for regulated-data retention advice — those belong elsewhere.

## Inputs and access

1. **Runbooks** — two runbook walks per run, each naming its scope (what state is
   covered: configs, data dirs, credentials map, schedules) and its target (where
   the backup lands and where a restore rehearses). A walk with no named scope is
   not a walk — define it first.
2. **Prior evidence** — the last run's checklists and hashes, when they exist, so
   completeness and drift are computed, not remembered.
3. Access is read and rehearse-only against systems the owner already granted. Restores
   rehearse in a sandbox or temp dir, never over live state, unless the owner gives an
   explicit yes for a live restore on that exact target. Credentials are referenced
   as `${VAR}` names only, never pasted. If a source needs a credential you were not
   given, record it as unreachable and move on. Do not improvise access.

## Sequence

1. **Fix the scope and the target** — per runbook, state what is covered and where
   the backup lands plus where the restore rehearses. Everything downstream checks
   inside this frame; widening it mid-run silently re-scopes the promise.
2. **Back up** — copy the in-scope state to the target. List what was copied (paths,
   count, sizes) and record content hashes where the tooling supports them; a copy
   with no listing is unverifiable.
3. **Verify the backup** — read it back: files open, hashes match the source listing,
   configs parse, the credentials map names every variable without containing any
   value. A backup that cannot be read back is a gap, not a backup.
4. **Rehearse the restore** — restore into the sandbox target and confirm the state
   comes up (configs load, schedules parse, Bot starts or dry-runs). Record what was
   observed, not what was expected.
5. **Diff against the prior run** — new gaps, closed gaps, hash moves, and scope
   changes each get one line: what moved and why.
6. **Name the gaps** — anything unbacked, unverified, or unrestorable becomes a gap
   with an owner and a date, not a quiet omission.

## Validation

- Each walk has a named scope, a target, a copy listing, a verify result, a restore
  rehearsal observation, and a gap list (possibly empty). Any walk missing one of
  these is incomplete, not passing.
- Both walks state their window, artifact counts, hashes where available, and
  unreachable sources. "Fully backed up" is reported only when every step evidenced;
  a skipped verify is reported as unmeasured, never as safe.
- Community backup lore (for example: generic retention rules) is labeled as
  community-claim, never as vendor truth. Nothing here asserts what any provider
  retains — only what this run observed.
- No walk deletes source state, prunes old backups, or restores over live state on
  the strength of this skill alone. Destructive steps need the owner's explicit yes
  on that exact target.

## Output

A dated markdown file with one evidenced checklist per runbook: scope, target, copy
listing, verify result, restore observation, diff vs. prior run, and gaps with owner
and date. End with the single gap that most needs the owner and the decision it needs.

## Boundaries

- Rehearse-only by default. Never restore over live state, delete source data, prune
  backups, or migrate anything while walking — those need the owner's explicit yes
  on that exact target, and "clean up while you're in there" is never approval.
- Never paste credentials, tokens, or secret values into backups logs, findings, or
  drafts; the credentials map holds `${VAR}` names only.
- Never report "backed up" from a copy listing alone: unverified means unmeasured,
  and unmeasured fails the run.
- Never present a rehearsed restore as a guarantee: it proves the drill worked on
  this date against this scope, not that every future failure is covered.
