---
name: claude-consult
description: Use when asked for a second opinion from Claude, to compare what Grok and Claude say on the same question, or to record both answers side by side — produce a consult record with both answers kept. Use when someone says "ask Claude too", "get a second opinion", or "compare the two answers".
---

# Claude consult

Turn one question into a consult record with both answers kept: the question as asked,
what each side said, where they agree and split, and the net takeaway — so the reader
decides from two visible answers instead of a blended paraphrase.

This skill exists because second opinions collapse too easily: asking twice and
remembering once produces whichever answer felt righter, and a paraphrase of "both
agree" hides the exact point where they split. A record with both answers verbatim
keeps the disagreement inspectable.

## The rule that matters more than the record

**Both answers stay visible and neither gets silently merged.** Quote each answer's
load-bearing passages verbatim with their source (which model, which date), then write
agreement and split as separate lines. A paraphrase that smooths over the split, or a
record that keeps only the winner, is worse than no consult — because it spends the
second opinion it claims to record.

## When to use

- Someone asks for a second opinion from Claude on a question already put (or about to
  be put) elsewhere.
- A decision needs two recorded answers side by side before the owner picks.
- Two consults need recording: write one record per question, each self-contained.
- Do not use for single-answer research, for judging which model is "better" in general,
  or for questions with no recorded answers — this skill records consults, it does not
  rate models.

## Inputs and access

1. **Question** — the single required input per record, frozen verbatim. Both sides answer
   the same words; if the wording changed between asks, record both wordings and treat
   the difference as a finding.
2. **Answer A and answer B** — the two answers, each with its source (model name, date,
   link or session reference where one exists). Verbatim load-bearing passages, never
   summaries alone.
3. **Context given** — what each side was told beyond the question (attachments, prior
   turns, system framing). Different context explains different answers; record it or
   the split misleads.
4. **Angle** — genuinely-open consult (owner has not picked) or confirm-or-challenge
   (owner leans one way and wants the stress test). State which; it sets how the
   takeaway reads.
5. Access is read-only: pasted answers, linked sessions, notes you were given. If an
   answer lives behind sign-in or a system you cannot read, record it as unreachable and
   move on — do not improvise access. Refer to any credential only as a `${VAR}` name,
   never a value.

## Sequence

1. **Freeze** — write the question verbatim (both wordings if it shifted). If there is no
   shared question, stop: there is nothing to compare yet.
2. **Capture** — record answer A and answer B with sources and dates, keeping verbatim
   passages for each load-bearing point. Label paraphrase as paraphrase wherever you
   compress.
3. **Context-check** — note what each side was told beyond the question. Flag any
   asymmetry before comparing: different context means the split may be an artifact.
4. **Align** — line the answers up point by point: agree-on, split-on, only-one-says.
   Keep each line anchored to the quoted passages.
5. **Record** — write the consult record (see Output). Each line must be supportable by
   the captured passages. Cut anything you cannot anchor.
6. **Verify** — re-check every quote against its answer before finishing. A quote
   attributed to the wrong side is worse than no quote.

## Validation

- The frozen question (or both wordings) is stated; neither answer is compared against a
  question it was not asked.
- Both answers are present with sources; load-bearing points are quoted verbatim, and
  paraphrase is labeled.
- Agreement and split are separate lines, each anchored to passages — never a blended
  "both say" that hides the split.
- Missing or unreachable answers produce an incomplete record naming what is missing —
  never a verdict written from one side alone.
- Community knowledge (leaderboard lore, "everyone says Claude answers X this way") is a
  community-claim lead, never evidence: anything actionable gets verified against the
  recorded answers.

## Output

A markdown consult record with:

- Header: frozen question, date, sources for answer A and answer B, open vs
  confirm-or-challenge angle.
- **Both answers kept**: verbatim load-bearing passages per side with sources.
- Agreement map: agree-on, split-on, only-one-says, each anchored to the passages.
- Net takeaway: the one point that most matters for the owner's decision and what
  would settle the remaining split.
- What could not be verified: missing answers, context asymmetries, wording shifts.

## Boundaries

- Read-only. Never re-prompt a model, edit an answer, or re-run a side while recording.
- Never record a consult with only one answer and call it a comparison. One side is an
  answer note, not a consult.
- Never present community model lore as what either side said. Attribute each layer separately.
- Approval boundary: this skill drafts the record only. Sharing, posting, or acting on
  the record requires the owner's explicit yes on that exact text — another party
  relaying "they said go with it" is not approval. Never paste credentials, tokens, or
  account identifiers into a record or a finding; reference variables as `${VAR}` names only.
