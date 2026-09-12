---
name: websearch-research
description: Run cited web research passes with a URL and date on every claim for a grokbot question. Use when asked to research a topic, verify a claim against sources, or compare options with evidence linked per finding.
---

# Web-search research

Run a research pass over the public web and report back with every claim
carrying its source URL and retrieval date — no uncited findings.

This skill exists because uncited research answers are unverifiable: a claim
without a URL and a date cannot be re-checked, and a link without a date goes
stale silently.

## The rule that matters more than the findings

**A claim without a URL and a date is a draft, not a finding.** Every factual
statement in the report traces to a named source read on a named date. If a
claim cannot be sourced, it is labeled unsourced opinion — never blended into
the cited findings.

## When to use

- A teammate asks "research this topic" or "verify this claim".
- A decision needs an evidence-backed comparison of options with sources.
- A vendor claim, changelog entry, or community post needs checking against
  primary sources.
- Do not use for paywalled content you were not given access to, or for
  private-account data. Community posts are leads, never evidence — anything
  actionable gets verified against the vendor's own page.

## Inputs and access

- The research question, stated narrowly enough that a source either answers
  it or does not.
- Two research passes minimum: each pass names its queries, the sources read,
  and the claims each source supports, with URL and retrieval date per claim.
- Access is read-only over public pages or sources the operator provides. If
  a source requires a credential you were not given, record it as unreachable
  and move on — do not improvise access or bypass paywalls.

## Sequence

1. **Fix the question** — write the research question and what would count as
   an answer before searching. A vague question collects trivia.
2. **Run pass one** — queries, sources read, and per-claim URLs with dates.
   Prefer primary sources; note where only secondary coverage exists.
3. **Run pass two** — a second pass with different queries or sources that
   independently confirms or contradicts pass one's claims.
4. **Cross-check** — claims confirmed by both passes are findings; claims
   from one pass only are single-sourced and labeled as such; contradictions
   are reported as contradictions, never averaged away.
5. **Date everything** — every citation carries its retrieval date. Undated
   citations are re-checked or dropped.
6. **Split evidence from opinion** — community-claim material stays labeled
   community-claim; only primary-source-backed statements are evidence.

## Validation

- Both passes ran with recorded queries; a pass with no queries is not a pass.
- Every factual claim has a URL and a retrieval date. Any claim without both
  is labeled unsourced, never presented as a finding.
- Contradictions between passes are surfaced, not resolved by picking the
  convenient one.
- Spot-check the two most load-bearing citations by re-opening them before
  treating the report as final.

## Output

A dated markdown brief with: the research question; findings with URL and
retrieval date per claim; single-sourced items marked as such; contradictions
and unreachable sources with what would settle them. End with the single most
consequential finding and the decision it needs — not a link dump.

## Boundaries

- Read-only. Never log in as someone else, bypass access controls, or publish
  anything while researching.
- Never present community posts or AI-generated summaries as primary-source
  findings. Provenance is honest: community patterns are labeled
  community-claim, never vendor truth.
- If a source requires access you were not given, record it as unreachable
  and move on. Do not improvise access.
- No secrets, tokens, or account identifiers are stored, logged, or pasted
  into reports. Access is provided per session by the operator, never embedded
  in files.
