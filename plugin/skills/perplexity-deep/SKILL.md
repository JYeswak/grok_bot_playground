---
name: perplexity-deep
description: Produce deep-research briefs through the Perplexity surface with every claim graded against its sources. Use when asked to research a topic deeply, compare two briefs, or feed graded findings into a Bot routine.
---

# Perplexity deep research

Turn a research question into a short brief where every substantive claim carries
a source grade. This skill exists because Bots that summarize research fail
silently: an ungraded claim reads as settled, and a single weak source can carry
a whole brief without anyone noticing.

## When to use

- The owner asks for a deep-research brief on a topic answerable through the
  Perplexity surface.
- A Bot routine needs graded findings as inputs (decisions, comparisons, memos).
- Do not use for breaking-news verification, legal or medical advice, or anything
  needing primary-source certainty alone — this skill grades sources, not
  replaces them.

## Inputs and access

- **Research ask** — the question, scope bounds, and what the two briefs cover
  (same question twice for convergence, or two sub-questions for coverage).
- **Perplexity surface** — the Bot's configured Perplexity tool or API client,
  keyed by `${PERPLEXITY_API_KEY}` (variable name only, never the value).
  Read-only: query and read briefs only.
- **Two briefs minimum** — run the ask twice (or two adjacent asks) so grading
  is demonstrated, not asserted. Single-brief answers are drafts.
- If no Perplexity surface is configured, say so and stop. Do not improvise a
  brief from memory presented as Perplexity output.

## The rule that matters more than the brief

**A claim without a graded source is not a finding.** A claim that came only
from the model's memory or from an ungraded snippet is an unverified lead, never
a verified result. Community patterns — blog roundups, forum threads, "best of"
lists — are community-claim: useful for discovering angles, never sufficient to
confirm a claim. Only a sourced statement, graded strong / adequate / weak with
the source named inline, counts.

## Sequence

1. **Parse the ask** — question, scope, exclusions, and what the two briefs
   compare. If the scope is ambiguous, name the ambiguity and pick the most
   likely reading explicitly.
2. **Brief one** — run the first query, keep the top claims with their cited
   sources. Drop clear mismatches (wrong topic, stale year) with one line each.
3. **Brief two** — run the second query under the same surface. Note what
   converges and what conflicts, one line per point.
4. **Grade sources** — mark each source strong (vendor docs, primary data),
   adequate (reputable secondary), or weak (single blog, forum, stale). Weak
   sources never carry a claim alone: pair or downgrade the claim to a lead.
5. **Synthesize** — merge into one brief: settled points, open conflicts with
   both sides named, and explicit gaps where neither brief delivered.

## Validation

- Every substantive claim has a named source plus a grade, or is explicitly
  marked unconfirmed.
- Two briefs are named with timestamps; a single-brief answer is a draft error.
- Claims resting only on weak or community sources are labeled as leads, with
  the source named and the missing stronger confirmation stated.
- Re-read the output before yielding: any claim lacking a grade is a draft
  error, not a minor gap. Fix or mark it.

## Output

A brief (two runs minimum) with: question restated, settled claims each with
source plus grade, open conflicts with both sides, and gaps. End with the
single most decision-relevant claim and what would overturn it.

## Boundaries

- Read-only. Never publish, post, or act on findings while researching. Findings
  go back to the owner or into a routine with its own approval boundary.
- Never present an unconfirmed claim as settled, and never merge "could not
  read" with "no evidence" — an unread source is a gap, not a negative.
- Never paste credentials, tokens, or account identifiers into a finding; refer
  to `${PERPLEXITY_API_KEY}` by name only.
- If the surface needs access you were not given, record it as unreachable and
  move on. Do not improvise access.
