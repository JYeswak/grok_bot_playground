---
name: discord-digest
description: Digest a Discord channel or thread window into ranked themes with per-item message links and dates. Use when asked what happened in a Discord channel, to catch up on a server, or to summarize a thread window. Read-only — never posts, reacts, pins, or manages anything.
---

# Discord digest

Turn a channel or thread window into a ranked theme list where every item carries its
message link and date. This skill exists because community signal lives in chat scrollback:
a corpus measure finds 3 of 645 Bots building on Discord with no plugin, so the practical
move is a read-only digest over what is already readable — not new posting machinery.

Use when asked to catch up on a channel, summarize what a server discussed this week, or
condense a long thread into what mattered. When-to-use is deliberately narrow: past
messages in, linked digest out, nothing written back.

## The rule that matters more than the digest

**A link or it didn't happen.** Every theme and every quoted claim carries the message link
and date it came from. An item without a link is a lead, not a finding — label it as such
or drop it. Community patterns are community-claim: never present chat consensus, listicle
advice, or a pinned-post rumor as vendor or product truth. If the linked message does not
say it, the digest does not say it.

## What to read, in this order

Follow this sequence; each step feeds the next.

1. **Window, inputs, and access** — the channel or thread, the time window, and what you can
   actually read. Name the inputs up front: channel link, window start and end, thread scope
   (whole channel vs. one thread). If a channel or thread needs access you were not given,
   record it as unreachable and move on. Do not improvise access.
2. **Messages** — pull the window newest-first: author, date, message link, first lines.
   Skip bots-status noise and emoji-only traffic on the first pass; they are a count of
   their own, not themes to silently drop.
3. **Themes** — cluster what remains into at most five themes, ranked by consequence to the
   reader, not by volume. Each theme names its representative messages inline (which
   message, which date, link attached).
4. **Validation** — before writing the digest, check every theme against its links: each
   theme backed by at least one linked message, each date inside the window, each quote
   faithful to the linked text. Anything that fails validation becomes an UNLINKED LEAD or
   is cut — never promoted into a theme.

## Output

A dated digest with one section per theme: theme name, two-to-four linked items (message
link + date each), and what changed or was decided. End with per-theme message counts and
the single thread that most needs the reader's attention. Two channel digests the shape
was built for: a `#announcements`-style weekly window (what shipped or changed, each item
linked), and a `#help`-style troubleshooting thread window (what broke, what fixed it,
linked to the messages that proved it).

## Boundaries

- Approval boundary: read-only. This skill never posts, replies, reacts, pins, edits, or
  deletes anything, and never manages members, roles, channels, or server settings. There
  is nothing here that sends — no draft-send step exists, so no send can be authorized
  from this skill. Any posting belongs to a separate routine with its own approval.
- Never paste credentials, tokens, or account identifiers into a digest or a finding.
- Never report server membership, private-channel existence, or unread contents beyond
  what the reader can already open. If a message needs a channel you cannot read, record
  it as unreachable and move on.
