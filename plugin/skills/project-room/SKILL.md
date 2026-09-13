---
name: project-room
description: Use when running a project as a shared room — one channel, one roster, one board per project — so specialists, files, and chat stay in one place. Use when a teammate asks how to set up, join, or hand off work in a project room.
---

# Project room

Run each project as **one room**: one channel, one roster, one board. Everything the project
needs lives in the room; everything the room holds belongs to the project.

This skill exists because project work scatters: side channels multiply, ownership blurs, and
context lives in whoever happens to be online. A room fixes the shape — people, files, and
decisions in one place — so the project survives anyone stepping away.

## The rule that matters more than the room

**The files are the project; the chat is the exception.** Anything that must outlive the
working session — evidence, drafts, decisions, state — lives in the room's files, not in the
scrollback. If it was only said in chat, it did not happen. Chat carries questions, alerts,
and judgment calls; files carry everything the next person needs.

## What to read, in this order

1. **One room per project — channel + roster + board.** Create a group channel per project,
   with the project's roster as members and one shared board (task list, pinned notes, key
   links) attached. One project, one room: never split a project across two channels, and
   never run two projects in one room. The roster is explicit — anyone doing project work is
   in the room, anyone in the room can see the board.
2. **Specialists keep their own screens.** Each specialist works in their own view and posts
   results back to the room; nobody surrenders their working setup to a shared screen. The
   room coordinates, it does not homogenise. A specialist's draft lives in their own space
   until it is ready, then lands in the room's files.
3. **Handoffs as files — evidence, drafts, decisions, state.** Every handoff is a file in
   the room, not a message. The four kinds: *evidence* (source material, quotes, numbers),
   *drafts* (work in progress, clearly marked), *decisions* (what was chosen, why, by whom,
   when), and *state* (a small machine-readable snapshot — e.g. `state.json` — so the next
   session resumes without re-reading the scrollback). A handoff names its kind, its author,
   and its date.
4. **Chat for exceptions.** Routine progress moves through files and the board; chat is for
   what breaks the routine — blockers, surprises, judgment calls, and anything time-sensitive.
   If a chat thread produces a conclusion, that conclusion gets written back into a file the
   same day. A thread that ends without a file update is an open loop — close it or name who
   will.
5. **Seat-limit overflow rule.** When the room fills its seat limit, the external source of
   truth stays authoritative (your tracker of record — e.g. a shared doc or board outside the
   chat platform), and the room mirrors it rather than replacing it. Overflow participants
   follow the external tracker and read the room's files; the room never becomes the only
   copy of anything load-bearing. If seats free up, re-add from the tracker's roster first.
6. **@mention usage.** Mention a person only when you need *them*: a decision only they can
   make, a review only they can give, a blocker only they can clear. Never @mention the whole
   room for routine updates — the board already says that. Every @mention carries the ask in
   the same message: what you need, and by when. An @mention without an ask is noise, and
   noise trains people to ignore the room.

## Validation

- One room per project holds all three: the channel, the explicit roster, and one shared board. Setup is done only when all three exist.
- Every handoff in the room's files names its kind, author, and date. Nothing load-bearing lives only in scrollback.
- Every chat conclusion from the day is written back into a file. A thread that ends without a file update has a named owner or it is still open.

## Output

A room check: the channel link, the roster, the board state, the handoff file list with kinds named, and the open loops — each with an owner — or an explicit none.

## Boundaries

- One room per project, always. Do not special-case a "quick" project into a side channel —
  quick projects are the ones that most need a paper trail.
- Never store a secret in the room: no tokens, keys, passwords, or credentials in files,
  boards, or chat. If a handoff needs access, it names *where* the credential lives, never
  the credential itself.
- Never claim bot-to-bot direct messages as a channel: rooms are human-and-bot group spaces,
  and anything that needs a private bot conversation does not belong in this shape.
- If the platform cannot do something this skill assumes (no group channels, no @mentions,
  no file attachments), say so plainly and run the closest subset — do not pretend the full
  shape holds.
