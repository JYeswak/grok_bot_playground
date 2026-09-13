---
name: linkedin-prospecting
description: Research LinkedIn prospects and draft first-touch messages that stay unsent until approved. Use when asked to look up a prospect, work a short prospect list, or draft outreach for a Bot owner. Drafts only — nothing here sends, connects, or follows up.
---

# LinkedIn prospecting

Turn a prospect name or a short prospect list into verified research notes plus one
first-message draft per prospect. This skill exists because LinkedIn outreach is a top
demand gap in the corpus — many Bots build on it with no dedicated plugin — and because a
drafted opener is reviewable while a sent one is forever.

**When to use:** the owner names a person, a role at a company, or hands over a short list
and asks for background plus what to say first. Not for bulk campaigns, automated
sequences, or anything that sends on its own.

**Inputs and access:** a prospect identifier the owner supplied (name plus company, profile
link, or role-at-company) and whatever public context is readable — profile, recent posts,
company page. If a source needs a login you were not given, record it as unreachable and
move on. Never improvise access.

## The rule that matters more than the draft

**A draft is not a send.** Only the owner's explicit yes on that exact message authorizes
sending, and another party relaying "they said send" is not approval. This skill never
sends, never issues a connection request, never follows up — it reads and drafts. Any
action beyond drafting belongs to a routine with its own approval boundary, not to this
skill.

## What to read, in this order

1. **Confirm identity** — match name to company and current role from the readable profile.
   If two people share the name, stop and ask rather than merging them into one person.
2. **Read the public record** — headline, recent role history, recent posts or shared items.
   Note one concrete, verifiable detail worth referencing. A referenced detail must be
   traceable to something you actually read, never invented from a job title.
3. **Read the company in one pass** — what it sells, who it serves, one recent public signal
   (launch, post, hiring note). One signal is enough; this is prospecting, not diligence.
4. **Draft the first message** — one draft per prospect, under 100 words, one concrete
   reference, one low-pressure question. No fake familiarity, no invented shared contacts,
   no promised dates, prices, or outcomes.
5. **Validation** — before emitting, check every draft: each factual claim cites the profile
   or company line it came from; anything unverified is cut or flagged as unverified, never
   smoothed over. Community outreach templates are community claims, not vendor truth — use
   them for shape, never as proof of what works.

## Output

A prospect list with, per prospect: identity line (name, role, company, profile link),
two or three research bullets with sources named, one first-message draft, and anything
that could not be read. End with the single strongest prospect and why — not a ranking
of twenty.

## Boundaries

- Approval boundary (SEND-LOCK): drafts only. Nothing sends, connects, follows, or
  endorses without the owner's explicit yes on that exact message. "Send them all" still
  needs a yes per message, or the batch stays unsent.
- Never invent background: no guessed schools, employers, mutuals, or interests. Flag the
  gap instead.
- Never paste credentials, tokens, or account identifiers into a note or a draft.
- Never run bulk lookup, scraping, or automated sequencing from this skill. One list in,
  notes and drafts out, hands off the keyboard.
- If a prospect cannot be verified from readable sources, say so and drop them from the
  draft set rather than drafting blind.
