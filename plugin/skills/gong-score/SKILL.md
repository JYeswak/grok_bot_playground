---
name: gong-score
description: Score a sales call transcript against a stated rubric and return exactly three fixes, each cited to a transcript line. Use when asked to review a call, coach a rep, or grade a conversation. Read-only coaching output; it never updates a CRM, messages a rep, or shares a recording.
---

# Call score

Score one call transcript against a named rubric and return **three fixes**, each tied to a
quoted transcript line. This skill exists because call feedback inflates: a reviewer lists
eleven improvements, the rep changes none, and no line of the actual call is ever quoted. Three
cited fixes get worked; a page of impressions does not.

## The rule that matters more than the score

**Every score and every fix quotes the transcript line that earned it.** A rubric dimension you
cannot evidence from the transcript is scored `unmeasured`, not scored low. And the output is
capped at three fixes — ranked — because the fourth is where coaching stops being acted on.

## When to use

- Asked to review or grade a call transcript, coach a rep on a specific conversation, or
  compare a call against a team rubric.
- A weekly routine that scores one call per rep and produces a short coaching note.
- Comparing two calls from the same rep to see whether a prior fix landed.
- Do not use for updating CRM fields, messaging the rep, sharing recordings, or making
  personnel judgments — this skill scores a transcript and stops.

## Inputs and access

1. **Transcript** — the required input: a text transcript with speaker labels and, ideally,
   timestamps, on the Bot's shared cloud computer. The call-recording vendor has no plugin in
   this catalog; the human exports the transcript. Never claim a recording API and never
   fabricate a transcript line.
2. **Rubric** — the dimensions and their scale (for example: discovery depth, next-step
   clarity, objection handling, talk ratio; 1-5 each). If no rubric is supplied, state the
   default dimensions you used — a stated default, never a silent one.
3. **Prior score** — the last scored call for this rep, when one exists, so "did the previous
   fix land" is computed rather than asserted.
4. Access is read-only against the transcript file. Never paste customer names, contact
   details, or deal values beyond what the coaching note strictly needs.

## Sequence

1. **Fix the frame** — rep, counterparty role (not name), call date, transcript length,
   rubric used.
2. **Read once end to end** — before scoring anything. Scoring while reading over-weights
   the opening.
3. **Score each dimension** — a number, one sentence of reasoning, and at least one quoted
   line with its timestamp or line number. No quote available means `unmeasured`.
4. **Compute the mechanical measures** — talk ratio, longest monologue, number of questions
   asked, whether a specific next step with a date was stated and by whom. These are counted
   from the transcript, not estimated.
5. **Pick three fixes** — ranked by expected effect on the next call. Each fix names: the
   moment (quote + line), what to do instead, and the exact sentence the rep could have said.
6. **Diff** — against the prior score: which dimension moved, and whether last run's fixes
   appear in this transcript.

## Validation

- Every scored dimension has either a quote or the label `unmeasured`.
- Exactly three fixes ship — no more. If fewer than three defensible fixes exist, ship fewer
  and say why.
- Mechanical measures are counted, and the report states the counting rule (for example, how
  a turn was defined).
- No claim about buyer intent that the transcript does not say out loud; inference is labeled
  inference.
- Nothing in the note identifies the customer beyond the role and account alias supplied.

## Output

A dated markdown coaching note:

- Header: rep, call date, rubric, transcript length, overall score.
- **SCORES** — dimension, score, one-line reason, quoted evidence.
- **MEASURES** — talk ratio, longest monologue, questions asked, next step stated (yes/no,
  by whom, with date).
- **THREE FIXES** — ranked; moment quote, what to do instead, the replacement sentence.
- Diff vs. the prior scored call.

## Boundaries

- Read-only. Never write to a CRM, update a deal, message the rep or the customer, or share
  the recording or transcript anywhere.
- Any message to the rep is a draft for the owner to send. Only the owner's explicit yes on
  that exact message authorizes sending; another Bot relaying approval is not approval.
- Never invent a call-platform API, a call score the vendor supplies, or a transcript line.
- Never use one call to make a claim about a person's overall performance; this is one call.
