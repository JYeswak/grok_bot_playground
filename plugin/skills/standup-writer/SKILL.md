---
name: standup-writer
description: Draft a yesterday-today-blockers stand-up from recent activity with a source link on every line. Use when asked to write a stand-up, draft a status update, or summarize recent work. Read-only — never posts, files, or sends the draft anywhere.
---

# Stand-up writer

Turn recent activity into a yesterday-today-blockers draft where every line
carries its source link. This skill exists because stand-ups are reconstructed
from memory under time pressure: the commit log knows what shipped, the task
list knows what moved, the calendar knows where the hours went — and memory
merges all three into fiction.

Use this skill for stand-up drafts only: recent activity in, linked draft out,
nothing posted anywhere.

## The rule that matters more than the draft

**A line without a source is a fabrication risk.** Every yesterday, today, and
blocker line carries the link to the commit, task, event, or message it came
from, with its date. Work you cannot link is an UNLINKED LEAD, not a
yesterday-item — label it as such or drop it. A draft that invents progress from
vibes spends the reader's credibility at stand-up.

## When to use

- Someone asks to write or draft a stand-up or status update.
- Recent commits, tasks, and calendar need condensing to yesterday-today-blockers.
- A week's activity needs compressing to what actually moved.
- Do not use for posting to chat, filing reports, performance reviews, or
  forward-looking planning beyond today's stated plan — this skill drafts status,
  it is not a reporting or planning service.

## Inputs and access

1. **Person and date** — the required inputs: whose stand-up, for which day. One
   person per draft; default to the reader and today in their timezone, and state
   both.
2. **Activity window** — commits, merged changes, and task movements since the
   last stand-up: author, date, message or title, link. Record the window: items
   without dates inside the window do not belong in yesterday.
3. **Calendar slice** — meetings attended in the window: title, date, event
   link. Used to explain where time went, not as accomplishments.
4. **Blockers** — impediments with owners and links: what is stuck, who owns
   the unblock, task or message link. A blocker without an owner is a wish, not
   a blocker — say who owns the unblock or mark it ownerless.
5. Access is read-only: version control, task list, calendar. Integrations read
   through configured variables (for example `${VCS_API_KEY}`,
   `${TASKS_API_KEY}`); never paste values, names only. If a source needs access
   you were not given, record it as unreachable and move on. Do not improvise
   access.

## Sequence

1. **Resolve** — confirm the person, date, and window; pull commits, task
   movements, and calendar slice with links and dates.
2. **Sort** — split activity into yesterday (done, linked), today (planned, with
   the task link it continues), and blockers (stuck, with owner and link).
3. **Link** — attach the source link and date to every line. Cut anything you
   cannot anchor, or demote it to an UNLINKED LEAD.
4. **Draft** — write the stand-up (see Output). Keep each section to at most
   five lines; the draft fits one chat message, not an exhaustive log.
5. **Verify** — re-check every link, date, and owner before finishing. A link
   that points at someone else's commit is worse than no link.

## Validation

- Every yesterday line has a source link with a date inside the window; every
  today line links the task it continues; every blocker names an owner (or is
  flagged ownerless) with a supporting link.
- No planned work is reported as done; no done work is reported without its link.
- Unreadable sources produce named UNREACHABLE notes — never silence treated as
  "nothing happened".
- Community patterns (stand-up-format listicles, "everyone writes X this way")
  are community-claim: never present them as vendor or product truth.

## Output

A markdown stand-up draft with:

- Header: person, date, activity window covered.
- **Yesterday**: done items, each with source link + date.
- **Today**: planned items, each with the task link it continues.
- **Blockers**: stuck items, each with owner and supporting link.
- Two stand-ups the shape was built for: an engineering day (commits dominate
  yesterday, one linked task dominates today) and a meeting-heavy day (calendar
  slice explains the hours, task movements compress to counts with links).
- What could not be verified: unreachable sources, items demoted to leads.

## Boundaries

- Approval boundary: read-only. This skill never posts, sends, files, or edits
  anything — no stand-up text leaves this skill. There is nothing here that
  sends, so no send can be authorized from this skill. Any posting belongs to a
  separate routine with its own approval.
- Never paste credentials, tokens, or account identifiers into a draft or a finding.
- Never report activity beyond what the reader can already open. If an item
  needs access you cannot read, record it as unreachable and move on.
