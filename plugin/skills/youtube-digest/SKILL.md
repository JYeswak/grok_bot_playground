---
name: youtube-digest
description: Use when asked to turn a YouTube video into a short brief — summarize a tutorial, talk, review, or interview from its transcript with timestamped quotes. Use when someone pastes a video link and asks "what does it say", "summarize this", or "give me the key points".
---

# YouTube digest

Turn one YouTube video into a short, checkable brief: a transcript-backed summary with
timestamped quotes, so the reader can verify every claim against the video itself.

This skill exists because video is slow and unsearchable: a twenty-minute tutorial holds
five minutes of decisions, and nobody re-watches to confirm what was said. A digest with
timestamps makes the video skimmable and every quote clickable back to its moment.

## The rule that matters more than the brief

**Every claim in the brief must trace to a timestamp, and an unwatchable video is a
finding, not an empty brief.** If the transcript is unavailable, the video is private or
removed, or captions are auto-generated garbage on the key passage — say so, name which
part is affected, and never fill the gap with what the title suggests the video says.
A brief that invents content from a title spends the reader's trust.

## When to use

- Someone pastes a YouTube link and asks for a summary, key points, or "is this worth watching".
- A tutorial needs distilling to steps; a talk needs distilling to thesis plus evidence.
- A quote is needed from a video and must carry its timestamp so it can be checked.
- Do not use for non-YouTube video sources, for full verbatim transcription requests, or for
  judging production quality — this skill briefs content, not craft.

## Inputs and access

1. **Video URL** — the single required input. Must be a standard watch or share link.
2. **Transcript** — fetch captions (manual captions preferred; auto-captions acceptable with a
   note). Record which kind you got: it determines how much weight quotes carry.
3. **Metadata** — title, channel, published date, duration. Needed to date the brief and to
   notice when the video changed under a saved link.
4. **Angle** — tutorial (steps + prerequisites) or talk (thesis + supporting points). Default to
   talk when unclear; state which angle you chose.
5. Access is read-only and public: watch page, captions, description. If a video needs
   sign-in, membership, or a rental to view, record it as unwatchable and stop — do not
   improvise access.

## Sequence

1. **Resolve** — confirm the URL loads, note title, channel, date, duration. If removed,
   private, or age-gated beyond anonymous viewing, stop and report unwatchable.
2. **Transcribe** — pull captions with timestamps. Prefer manual over auto-generated; if only
   auto-captions exist, flag the brief accordingly. If no captions exist at all, say so —
   never brief from comments or the description alone.
3. **Segment** — split the transcript into its natural sections (intro, steps or arguments,
   close). Mark section boundaries with their timestamps.
4. **Quote** — pick two to four load-bearing passages and copy them verbatim with timestamps.
   Verbatim means verbatim: no cleanup of filler words inside quotation marks.
5. **Brief** — write the 5-line brief (see Output). Each line must be supportable by at least
   one timestamped section or quote. Cut anything you cannot anchor.
6. **Verify** — re-check every timestamp against the transcript before finishing. A timestamp
   that points at the wrong moment is worse than no timestamp.

## Validation

- Every factual claim in the brief has a timestamp pointing at the passage that supports it.
- Quotes are verbatim and carry timestamps; paraphrase is labeled as paraphrase, never quoted.
- Auto-caption sourcing is disclosed when it is all that was available.
- Unwatchable, captionless, or removed videos produce an unwatchable report — never a brief
   written from the title, thumbnail, or comments.
- Community knowledge (comments consensus, "everyone says this video claims X") is a lead,
   never evidence: anything actionable gets verified against the transcript itself.

## Output

A markdown brief with:

- Header: title, channel, published date, duration, caption kind (manual or auto).
- **5-line brief**: one line each for (1) what the video is, (2) the core thesis or outcome,
  (3) the two or three key steps or supporting points, (4) the main caveat or limitation
  stated, (5) who should watch it and what to skip to.
- Timestamped quotes: two to four verbatim passages with clickable timestamps.
- Section map: section titles with start timestamps for navigation.
- What could not be verified: passages where captions were unclear, missing, or auto-caption
  gibberish on a key point.

## Boundaries

- Read-only. Never comment on, like, download, re-upload, or clip the video while digesting.
- Never brief a video you could not watch: no transcript, no brief. A title plus comments is
  not a source.
- Never present community commentary (top comments, reaction videos, listicle summaries) as
  what the video itself says. Attribute each layer separately.
- If asked to transcribe a full video verbatim, decline the full dump and offer the brief
  plus section map instead — this skill summarizes, it is not a transcription service.
