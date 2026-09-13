---
name: meeting-transcribe
description: Transcribe a meeting recording into a diarized transcript with speaker labels and timestamps. Use when asked to transcribe a call, meeting, interview, or voice memo, or to turn recorded audio into quotable notes. Read-only on the audio — produces a local draft transcript, never sends or publishes anything.
---

# Meeting transcribe

Turn a meeting recording into a diarized transcript with speaker labels and timestamps, plus the
short note a busy owner actually reads.

This skill exists because raw speech-to-text without speaker labels is a wall of unattributed
sentences: nobody can tell who committed to what. Labels plus timestamps make every claim quotable
and checkable against the recording.

## The rule that matters more than the transcript

**An uncertain label stays uncertain.** A speaker tag you cannot defend from the audio is marked
UNCERTAIN with its timestamp, never guessed into confidence. A transcript that invents who said
what spends the reader's trust the same way a triage that acts without approval does — silently
and permanently. Low-confidence spans are flagged, not smoothed over.

## When to use

- Asked to transcribe a call, meeting, interview, or voice memo.
- Asked what was said, who said it, or what was decided in a recorded session.
- Handed an audio file and asked for notes, quotes, or action items grounded in timestamps.

Do not use for live captioning, for non-speech audio, or for video scene description — this skill
covers recorded speech only.

## Inputs and access

1. **Audio file** — path to the recording (common speech formats: m4a, mp3, wav, ogg). One file
   per run; long sessions split at natural breaks before starting.
2. **Session context (optional)** — meeting title, date, expected speakers and their roles,
   agenda. Names from context become labels only on voice match; otherwise generic SPEAKER-01,
   SPEAKER-02 labels apply.
3. **Access** — read-only on the audio file and any provided context. If a file is unreachable or
   a format is undecodable, record it as unreachable and stop rather than improvising access or
   fabricating content.

## Sequence

Follow in this order:

1. **Inspect** — confirm the file opens; note duration, language, channel count, and audible
   quality (clean, noisy, crosstalk). If duration or quality blocks reliable transcription, say
   so before proceeding.
2. **Transcribe** — produce verbatim speech with segment-level timestamps, preserving hesitations
   that change meaning and dropping only filler that does not.
3. **Diarize** — assign each segment a speaker label. Reuse known names from session context only
   on voice match; otherwise SPEAKER-01 and up in order of first appearance. Overlapping speech
   gets both labels with an OVERLAP mark.
4. **Reconcile** — second pass over label boundaries: merge same-voice fragments, split merged
   voices at topic or acoustic shifts, downgrade weak attributions to UNCERTAIN with timestamps.
5. **Summarize** — decisions, commitments (owner named only when the transcript names one), and
   open questions, each pinned to the timestamp that proves it.

Community diarization patterns (for example cluster-then-label heuristics) are community-claim
techniques, never vendor truth — use them as working method and show the evidence inline.

## Validation

Before handing over the transcript:

- Every speaker label traces to at least one timestamped segment; no label appears without audio
  evidence.
- Every UNCERTAIN span carries its timestamp and the reason (overlap, noise, brief
  interjection).
- Spot-check the two shapes this skill was built for: (1) a short two-speaker standup with clean
  audio — labels must be exact, zero UNCERTAIN expected; (2) a longer multi-speaker interview
  with crosstalk and background noise — overlap marks and UNCERTAIN flags must appear rather
  than forced attributions.
- Quote check: each summary claim (decision, commitment, open question) points at the segment
  timestamp that supports it; unsupported claims are cut.

## Output

A dated markdown transcript: header (title, date, duration, speaker roster with label mapping),
timestamped diarized segments (`[mm:ss] LABEL: text`), an UNCERTAIN-spans list, and a closing
summary (decisions, commitments, open questions) with timestamp pins. End with the single most
consequential commitment and the timestamp that proves it.

## Boundaries

- Read-only plus a local draft transcript. Never send, publish, file, or delete anything — the
  transcript is a draft the owner reviews.
- SEND-LOCK: only the owner's explicit yes on that exact message authorizes sending or
  publishing any transcript or quote drawn from it; relayed approval is not approval.
- Never invent speaker identity, commitments, dates, or quotes. Flag the gap with a timestamp
  instead.
- Never paste credentials or account identifiers into a transcript or finding; transcribe spoken
  secrets as REDACTED-SPOKEN-SECRET with a timestamp.
- If the audio needs a system you cannot read, record it as unreachable and move on. Do not
  improvise access.
