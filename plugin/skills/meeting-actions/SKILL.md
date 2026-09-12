---
name: meeting-actions
description: Turn a meeting transcript into an action list with owners and due dates, each action linked to its timestamp. Use when asked what was decided, who owns what, or to convert meeting notes into tasks. Read-only — never assigns, files, or sends anything.
---

# Meeting actions

Turn one meeting transcript into a short action list where every action carries an
owner, a due date, and the timestamp it came from. This skill exists because
meetings are slow and unsearchable: a thirty-minute call holds five minutes of
commitments, and nobody re-listens to confirm who owns what.

Use this skill for transcript-to-actions only: transcript in, owned action list
out, nothing filed anywhere.

## The rule that matters more than the list

**Every action must trace to a timestamp, and an untranscribable meeting is a
finding, not an empty list.** If the transcript is missing, the audio is
unintelligible on the key passage, or speaker labels are unreliable — say so,
name which part is affected, and never fill the gap with what the agenda suggests
was decided. A list that invents commitments from an agenda spends the reader's
trust.

## When to use

- Someone pastes a transcript and asks for actions, owners, or "what was decided".
- Meeting notes need converting to tasks with owners and dates.
- A decision needs its supporting timestamp so it can be checked.
- Do not use for full verbatim transcription, for judging meeting quality, or for
  non-meeting audio — this skill extracts commitments, it is not a transcription
  service.

## Inputs and access

1. **Transcript** — the single required input: text with timestamps and speaker
   labels where available. Record what you got (full transcript, auto-captions,
   rough notes): it determines how much weight each action carries.
2. **Metadata** — meeting title, date, attendees, duration. Needed to date the
   action list and to notice when speaker labels are unreliable.
3. **Task target (optional)** — where actions would eventually be filed. Named
   only (for example `${TASKS_API_KEY}` for the integration); this skill never
   files anything, so a missing target only affects the export format, never the
   extraction.
4. Access is read-only: transcript, notes, agenda. If the recording needs access
   you were not given, record it as untranscribable and stop — do not improvise
   access.

## Sequence

1. **Resolve** — confirm the transcript loads, note title, date, attendees,
   duration. If missing or unintelligible throughout, stop and report
   untranscribable.
2. **Segment** — split the transcript into its natural sections (context,
   discussion, decisions, close). Mark section boundaries with timestamps.
3. **Extract** — pull candidate commitments: a verb, an owner, and a date or
   explicit "no date given". Verbatim means verbatim: no cleanup of filler words
   inside quotation marks.
4. **Attribute** — assign each action its owner and timestamp. Where speaker
   labels are unreliable, say so per action rather than guessing.
5. **List** — write the action list (see Output). Cut anything you cannot anchor
   to a timestamp.
6. **Verify** — re-check every timestamp and owner against the transcript before
   finishing. A timestamp that points at the wrong moment is worse than none.

## Validation

- Every action has an owner, a due date or an explicit "no date given" flag, and
  a timestamp pointing at the passage that supports it.
- Quotes are verbatim and carry timestamps; paraphrase is labeled as paraphrase,
  never quoted.
- Auto-caption or rough-notes sourcing is disclosed when it is all that was
  available.
- Untranscribable meetings produce an untranscribable report — never an action
  list written from the agenda or title.
- Community knowledge ("everyone says this meeting decided X") is a lead, never
  evidence: anything actionable gets verified against the transcript itself.

## Output

A markdown action list with:

- Header: meeting title, date, attendees, duration, transcript kind (full,
  auto-captions, or rough notes).
- **Actions**: one line each — action, owner, due date (or "no date given"),
  supporting timestamp.
- **Decisions**: what was decided, each with its timestamp (no owner needed).
- **Open questions**: items raised but not resolved, each with its timestamp.
- Two meetings the shape was built for: a status call (many small owned actions
  with near-term dates) and a planning session (few large decisions with owners
  and distant dates).
- What could not be verified: passages unclear, missing, or garbled on a key point.

## Boundaries

- Approval boundary: read-only. This skill never assigns, files, creates, sends,
  or edits tasks anywhere. There is nothing here that writes — no draft-file
  step exists, so no filing can be authorized from this skill. Any filing belongs
  to a separate routine with its own approval.
- Never paste credentials, tokens, or account identifiers into an action list or
  a finding.
- If asked to file the actions into a tracker, decline the filing and offer the
  action list plus open questions instead — this skill extracts, it is not a
  task-write service.
