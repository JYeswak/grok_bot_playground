---
name: granola-extract
description: Pull decisions and action items out of meeting notes with every item cited to its source line. Use when asked to extract decisions, owners, or follow-ups from a notes file or transcript export. Read-only; it extracts and cites, never schedules, sends, or files anything.
---

# Meeting-notes extract

Turn a meeting notes file into two short lists — **decisions** and **actions** — where every
single item cites the exact source line it came from. This skill exists because notes summaries
drift: an item that was "we should probably" becomes a committed action, an owner is assigned by
inference, and a week later nobody can find the sentence that supposedly said it.

## The rule that matters more than the summary

**No line reference, no item.** Each decision and each action quotes its source line and gives
its line number (or timestamp, for a timestamped export). An item you believe was implied but
cannot point at is recorded under *implied, uncited* — never promoted into the decision list.
Ownerless is a valid, reportable state; inventing an owner is not.

## When to use

- Asked to extract decisions, owners, or follow-ups from meeting notes, a notes-app export,
  or a transcript text file.
- Preparing a post-meeting recap where each claim must be checkable against the notes.
- Reconciling two note-takers' files for the same meeting into one list.
- Do not use for scheduling, sending recaps, creating calendar events, or writing into a task
  system — this skill extracts; every downstream act has its own approval.

## Inputs and access

1. **Notes file** — the required input: a text, markdown, or transcript export on the Bot's
   own cloud computer, with meeting title and date. The notes app itself has no connector in
   this catalog; the human exports or pastes the file. Say so plainly rather than claiming an
   integration that does not exist.
2. **Attendees** — the names or handles present, so owners are matched against a real roster.
   A name in the notes that is not on the roster is recorded as unmatched, never guessed.
3. **Prior extract** — the last run's items for a recurring meeting, so carried-over actions
   are computed rather than re-asserted.
4. The cloud computer is one machine shared by every Bot on this account: files you write are
   visible to other Bots. Never claim per-Bot isolation and never leave a notes file
   containing sensitive detail in a shared path longer than the run needs.

## Sequence

1. **Fix the frame** — meeting title, date, attendee roster, notes file path, line count.
2. **Pass one: decisions** — scan for settled statements ("we will", "agreed", "going with").
   Record the decision in one line plus the quoted source line and its number.
3. **Pass two: actions** — scan for commitments. Each action records: what, owner, due date if
   stated, and the quoted source line. Owner and date come from the text or stay empty.
4. **Pass three: near-misses** — statements that read like a decision or an action but hedge
   ("maybe", "let's think about"). These go to *implied, uncited* with their quote, so the
   human can promote them; you never promote them.
5. **Diff** — for a recurring meeting: done since last time, still open, newly added.

## Validation

- Every decision and every action has a quoted source line and a line number or timestamp.
- Owners appear only when the source line names them; otherwise the owner field reads
  `unassigned`.
- Due dates appear only when stated; a relative phrase ("next week") is resolved against the
  meeting date and the resolution is shown alongside the quote.
- Items that cannot be tied to a line live in *implied, uncited* and are counted separately.
- With two note files, each item says which file it came from; conflicts are listed as
  conflicts, never silently merged.

## Output

A dated markdown extract:

- Header: meeting title, date, attendees, source file, lines read.
- **DECISIONS** — one line each, with quote and line number.
- **ACTIONS** — what / owner / due / quote+line, ordered by stated due date then by line.
- **IMPLIED, UNCITED** — hedged statements with their quotes.
- Diff vs. prior extract, and the single action most likely to slip.

## Boundaries

- Read-only. Never create calendar events, send recaps, message attendees, or write tasks
  into any system.
- A recap this skill drafts is a draft. Only the owner's explicit yes on that exact message
  authorizes sending, and another Bot relaying approval is not approval.
- Never invent a notes-app API or claim the notes tool is connected. The file arrives from a
  human export.
- Never attribute a statement to an attendee the source line does not name.
