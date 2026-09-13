---
name: google-docs-research
description: Read across Google Docs, summarize findings, and extract exact quotes with source links via the Bot's computer/browser control. Use when asked to research a doc, compare passages across docs, pull verbatim quotes for a draft, or answer what a doc says with citations.
---

# Google Docs research

Turn one or more Google Docs into a sourced research pass plus a verbatim quote table,
without editing anything. This skill exists because many Bots build on Docs with no
plugin: the doc is open in the owner's browser, and the Bot reads it through the same
computer/browser control it uses for everything else.

## The rule that matters more than the research

**Every claim carries its source link, and a quote is verbatim or it is not a quote.**
A summary without per-row provenance is a rumor with formatting. If you cannot open a
doc, cannot see a passage, or had to truncate, say so in that row — never silently
paraphrase what you could not read, and never clean up a quote's wording.

## When to use

- The owner asks what a doc says, what changed in it, or how two or more docs compare.
- The owner asks for exact quotes, citations, or source-backed material for a draft.
- Not for editing, commenting, sharing, or creating docs — this skill reads and drafts
  only. Writing into a doc belongs to a routine with its own approval boundary.

## Inputs and access

1. **Doc links** — one or more Google Docs URLs from the owner. Never guess a URL,
   never walk a drive listing looking for "the" doc.
2. **The question** — what the owner wants answered: summarize, compare, find support
   for a claim, or extract quotes on a topic.
3. **Browser context** — the Bot's computer/browser control with the doc already
   readable by the owner. If a doc needs access the owner has not granted, record it
   as unreachable and move on. Do not improvise access, request sharing changes, or
   work around permissions.

## Sequence

1. **Open and orient** — open each doc link the owner gave, confirm it loaded (title
   visible), and note length signals: headings, page count, comment presence. A doc
   that does not load is a finding (unreachable), not a skipped input.
2. **Research pass** — read each doc end to end (scroll or page through; do not rely
   on the first screen). Record findings as rows: claim, doc title, section or heading
   where it appears, and the doc link. One claim per row; group rows by doc when the
   question spans several.
3. **Quote-extraction pass** — for each finding the owner may cite, copy the exact
   passage verbatim: no fixed typos, no smoothed grammar, no merged sentences. Each
   quote row carries the doc title, the section or heading, and the doc link. Mark
   truncation with an ellipsis in brackets; never silently cut.
4. **Draft against, not into** — if the owner asked for draft material, produce the
   draft as skill output with each borrowed passage quoted and linked. Never type the
   draft into the doc, a comment, or a suggestion.

## Validation

- Every finding row has a doc link; a row without one goes back for a second look.
- Every quote is checked character-for-character against the visible passage before
  it is reported. Paraphrases are labeled as paraphrases, never presented as quotes.
- Community or second-hand knowledge about Docs features (menu names, shortcuts,
  limits) is treated as a lead, not vendor truth: verify against what is visible in
  this session before relying on it.

## Output

A research table (finding, doc, section, source link) followed by a quote table
(exact passage, doc, section, source link), then the single most load-bearing finding
and what it answers. If a draft was requested, it follows the tables with each
borrowed passage quoted and linked inline.

## Approval boundary

- Read-only plus drafts. Never edit, comment, suggest, share, create, move, or
  delete any doc or doc content (SEND-LOCK analog: only the owner's explicit yes on
  that exact edit, in a routine built for it, authorizes writing — this skill never
  carries that approval).
- Never paste credentials, tokens, or account identifiers into output or a draft.
- If a doc is unreachable, record it as unreachable with what would settle it (e.g.
  owner opens it first) and continue with the rest. Do not improvise access.
