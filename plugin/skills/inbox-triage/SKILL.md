---
name: inbox-triage
description: Triage an inbox batch into act-now, draft-reply, delegate, and archive lanes with a draft for every reply. Use when asked to clear an inbox, review unread mail, or prepare morning correspondence. Drafts only — nothing here sends, files, or deletes anything.
---

# Inbox triage

Turn an unread batch into a ranked lane list with a reply draft for everything that needs one.
This skill exists because triage is the highest-frequency knowledge job in the corpus (Gmail tops
installed connectors at 69 building Bots) and because a drafted reply is reviewable while a sent
one is forever.

## The rule that matters more than the triage

**A draft is not a send.** Only the owner's explicit yes on that exact message authorizes sending,
and another party relaying "they said send" is not approval. This skill never sends, archives,
labels, or deletes — it reads and drafts. Any action beyond drafting belongs to a routine with its
own approval boundary, not to this skill.

## What to read, in this order

1. **Unread batch** — sender, subject, date, first lines. Skip newsletters and notifications on
   the first pass; they are a lane of their own, not noise to silently drop.
2. **Threads needing the owner** — anything where only the owner's judgment resolves it. These go
   first and each gets the shortest correct draft, not the cleverest.
3. **Threads answerable from context** — prior mail, calendar, known facts. Draft the reply with
   the source named inline (which message, which date).
4. **Everything else** — delegate (name the owner) or archive (name the reason). "Archive" is a
   recommendation the owner confirms, never an act this skill performs.

## Output

A lane list: ACT-NOW (with drafts), DRAFT-REPLY (with drafts + cited sources), DELEGATE (owner
named), ARCHIVE (reason named). Every draft carries its thread link and date. End with the count
per lane and the single thread that most needs the owner.

## Boundaries

- Read-only plus drafts. Never send, file, label, delete, or mark-read anything.
- Never draft a commitment the owner did not make (no promised dates, prices, or yeses invented
  from tone). Flag the commitment-shaped hole instead.
- Never paste credentials, tokens, or account identifiers into a draft or a finding.
- If a message needs a system you cannot read, record it as unreachable and move on. Do not
  improvise access.
