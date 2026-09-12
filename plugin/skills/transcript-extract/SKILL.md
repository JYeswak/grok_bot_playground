---
name: transcript-extract
description: Turn video URLs into timestamped transcripts with a short brief each, provenance chain intact. Use when asked to summarize a video, brief two videos side by side, or quote what a video actually says. Read-only — fetches captions and drafts briefs, never downloads, uploads, or publishes anything.
---

# Transcript extract

Turn a video URL into a transcript plus a short brief, with the chain from URL to
transcript to brief unbroken and inspectable. This skill exists because video
briefing is a repeat community pattern in the corpus (a small cluster of Bots
build on transcript fetching with no shared plugin) and because a brief whose
quotes cannot be traced back to a timestamped line is indistinguishable from
invention.

## The rule that matters more than the brief

**A brief without a chain is a rumor.** Every claim in the brief traces to
timestamped transcript lines, every transcript traces to its source URL plus
retrieval method plus retrieval date, and anything unretrievable, inaudible, or
auto-translated is labeled as such — never filled in from memory, from a
related video, or from listicle knowledge about the topic. Community transcript
patterns are community-claim; only the retrieved lines are evidence.

## When to use

- Asked to summarize, brief, or quote a video given its URL.
- Asked to brief two videos (compare, contrast, or brief each separately) with
  each video's chain kept distinct.
- Asked what a video actually says versus what its title or comments claim.
- Do not use for audio-only uploads you own, for transcribing a private
  meeting, or for editing or publishing video — this skill reads public
  captions and drafts briefs only.

## Inputs and access

- **Video URL(s)** — one URL for a single brief, two URLs for a side-by-side.
  Normalize each first: canonical video id, watch URL as given, uploader and
  title as displayed on the day of retrieval.
- **Caption track choice** — prefer human-uploaded captions for the requested
  language; fall back to auto-generated only when labeled as auto-generated in
  the chain. Never silently mix two tracks.
- **Access** — public caption endpoints only, with whatever the Bot can already
  read. If a video needs a login, a rental, or a credential you were not
  given, record it as unreachable and move on. Do not improvise access.

## Sequence — what to do, in this order

1. **Normalize the URL** — canonical id, full URL as given, uploader, title,
   duration, retrieval date. One source block per video; with two videos, keep
   the blocks and everything downstream separate.
2. **Fetch the transcript** — human captions first, auto-generated only as a
   labeled fallback. Record method (which track, which language, auto or
   human) and timestamp range covered.
3. **Validate the lines** — check the validation section below before writing
   a single brief sentence. A transcript that fails validation is a finding
   ("no captions", "track is machine-translated", "only first half covered"),
   not a quiet gap to write around.
4. **Draft the brief** — short: what the video argues or shows, in the owner's
   words where it matters, with every load-bearing claim carrying a timestamp
   citation. Quote verbatim; paraphrase only with the cited lines alongside.
5. **Mark the gaps** — inaudible spans, uncaptioned music or demo segments,
   and anything the brief deliberately omits. End with the single most
   consequential point per video and what it would take to verify it further.

## Validation

- Transcript present for the claimed span; missing captions fail, never
  silently narrow, the brief.
- Quotes verbatim against the retrieved lines — no cleaned-up wording passed
  off as a quote, no timestamps invented or rounded beyond the line they came
  from.
- Track identity stated: human or auto-generated, which language, and whether
  machine-translated. An auto track never briefs with the confidence of a
  human one.
- Two-video briefs keep chains separate: no quote from video A cited under
  video B, no shared claim without a citation to each side.
- Anything unheard, uncaptioned, or unretrieved is labeled UNKNOWN with the
  reason, never smoothed over.

## Output

Per video: a source block (canonical id, URL as given, uploader, title,
duration, track identity, retrieval date), the timestamped transcript excerpt
that carries the brief, the brief itself with inline timestamp citations, and
a gaps list. With two videos, deliver two chains plus a short comparison that
cites each side separately. End with the single most consequential point and
the decision or follow-up it needs — not a wall of quotes.

## Boundaries

- Read-only plus drafts. Never download the video, upload or publish anything,
  post a comment, or spend on transcription or enrichment while briefing.
- Approval boundary: this skill drafts briefs for the owner to use. Publishing
  a brief, quoting it publicly, or acting on a consequential claim belongs to
  a routine with its own approval — nothing here sends or publishes.
- Never present community transcript lore (which endpoints work, which videos
  "always have captions") as vendor truth. State what worked today, for this
  URL, with this method.
- Never paste credentials, tokens, or account identifiers into a transcript,
  brief, or finding. If a source needs access you lack, record it as
  unreachable and move on.
