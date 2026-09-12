---
name: cos-digest
description: Draft a daily chief-of-staff digest from calendar, inbox, and task inputs with a source link on every item. Use when asked for a morning brief, a daily digest, or "what needs me today". Read-only — never sends, reschedules, or acts on anything.
---

# Chief-of-staff digest

Turn one day's calendar, inbox, and task inputs into a short morning brief where every
item carries its source link. This skill exists because the day's load is scattered
across three surfaces: the calendar knows where time goes, the inbox knows what
arrived, and the task list knows what is owed — and nobody re-opens all three to
decide what matters first.

Use this skill for daily briefs only: past and scheduled items in, ranked digest
out, nothing acted upon.

## The rule that matters more than the brief

**A link or it didn't happen.** Every item carries the link to the calendar event,
message, or task it came from, plus its date. An item without a link is a lead, not
a finding — label it as such or drop it. "Unmeasured" and "unchanged" are different
answers and must never be merged: a source you could not read is a finding, not a
quiet day. A digest that says "nothing needs you" because its inputs broke spends
the reader's trust.

## When to use

- Someone asks for a morning brief, daily digest, or "what needs me today".
- A day's calendar plus inbox plus tasks need condensing to what is load-bearing.
- Priorities need ranking across sources, with each item traceable to its source.
- Do not use for acting on anything (sending, rescheduling, assigning), for weekly
  or historical reviews, or for sources outside calendar, inbox, and task list —
  this skill briefs the day, it is not an assistant that does the day.

## Inputs and access

1. **Date** — the single required input. One day per digest; default to today in
   the reader's timezone and state which date was briefed.
2. **Calendar window** — that day's events: title, time, attendees, event link.
   Past events carry outcomes; future events carry preparation needs.
3. **Inbox window** — unread and recent messages for the same day: sender, date,
   subject, message link. Skip automated-status noise on the first pass; it is a
   count of its own, not items to silently drop.
4. **Task list** — open items with owners and due dates: title, owner, due date,
   task link. Overdue items rank by consequence, not by age alone.
5. **Credentials** — calendar, mail, and task integrations are read through
   configured variables (for example `${CALENDAR_API_KEY}`, `${MAIL_API_KEY}`,
   `${TASKS_API_KEY}`). Never paste values into a digest or a finding; names only.
   If a source needs access you were not given, record it as unreachable and move
   on. Do not improvise access.

## Sequence

1. **Resolve** — confirm the date and pull all three surfaces for that day:
   events with links, messages with links, tasks with links. Note which surface
   each item came from.
2. **Triage** — split items into needs-decision, needs-preparation, and
   background. Rank by consequence to the reader, not by volume per surface.
3. **Link** — attach the source link and date to every item. Cut anything you
   cannot anchor, or demote it to an UNLINKED LEAD.
4. **Brief** — write the digest (see Output). Keep at most five items per
   section; the digest fits one screen of ranked items, not an exhaustive dump.
5. **Verify** — re-check every link and date before finishing. A link that points
   at the wrong item is worse than no link.

## Validation

- Every item in the digest has a source link pointing at the event, message, or
  task that supports it, with a date inside the briefed day (or an explicit
  overdue flag for tasks).
- Unreadable sources produce named UNREACHABLE sections — never silence treated
  as a quiet day.
- Rankings reflect consequence to the reader; no surface dominates just because
  it had more items.
- Community patterns (productivity-listicle advice, "everyone briefs X this way")
  are community-claim: never present them as vendor or product truth.

## Output

A dated markdown digest with:

- Header: briefed date, timezone, which surfaces were read and which were
  unreachable.
- **Needs a decision**: items blocked on the reader, each with source link + date.
- **Needs preparation**: upcoming events with what to read first, each linked.
- **Background**: FYI items worth one glance, each linked.
- Two digests the shape was built for: a heavy-meeting day (decisions and prep
  dominate, inbox compressed to counts) and a heavy-inbox day (messages dominate,
  calendar compressed to the day's skeleton).
- What could not be verified: surfaces unreachable, items demoted to leads.

## Boundaries

- Approval boundary: read-only. This skill never sends, replies, reschedules,
  assigns, completes, or deletes anything. There is nothing here that acts — no
  draft-send step exists, so no send can be authorized from this skill. Any action
  belongs to a separate routine with its own approval.
- Never paste credentials, tokens, or account identifiers into a digest or a finding.
- Never report calendar, inbox, or task contents beyond what the reader can
  already open. If an item needs access you cannot read, record it as unreachable
  and move on.
