---
name: citation-verify
description: Use when asked to verify the citations behind a brief, memo, or research pack — claim by claim, hit or miss per claim. Use when a teammate asks "do the sources actually say this" before anything ships or sends.
---

# Citation verifier

Turn a brief's claims into a claim-by-claim ledger: **hit** (source says it) or **miss**
(source does not, or no source). This skill exists because research packs need provenance —
a claim that travels without its source becomes lore by the second forward — and because
a verification that skims is worse than none, since it spends the reader's trust.

Limits, stated plainly: this checks that cited sources say what the brief claims they
say. It does not judge whether the sources themselves are right, and it is not legal
review. Consequential claims still get a qualified human.

## The rule that matters more than the ledger

**Every claim carries its source, or it is a miss.** A claim with no cited source, a
source that cannot be opened, or a source that says something nearby but not the thing
claimed — each of those is a miss, not a partial hit. "Unverified" and "verified" are
different answers and must never be merged.

## When to use

- Asked to verify the citations in a brief, memo, slide draft, or research pack.
- Asked whether the sources behind a recommendation actually support it.
- Before a brief ships, sends, or gets quoted publicly.
- Do not use for statistical review of the underlying analysis (that is stat-review)
  or for legal interpretation of what a source obligates — those belong elsewhere.

## Inputs and access

1. **Briefs** — two briefs per run, each with its claims numbered or otherwise stable
   (claim ids survive rewording; positions do not). Claims are checked on stable ids,
   never on paragraph order.
2. **Cited sources** — per claim, the exact source as cited (link, doc ref, page or
   section). A claim whose citation is "various" or missing is logged as a miss
   before any reading starts.
3. Access is read-only against sources the owner already granted. If a source needs
   a credential you were not given, record it as unreachable and move on — the claim
   is a miss (unopened), not a hit. Credentials are referenced as `${VAR}` names
   only, never pasted. Do not improvise access.

## Sequence

1. **Extract the claims** — list every factual claim in each brief with a stable id
   and the claim in one sentence. Opinions and recommendations are labeled as such
   and excluded from the hit/miss ledger; only checkable claims are checked.
2. **Map claim to source** — per claim, record the cited source exactly as given.
   Claims sharing one vague citation each get their own row; a shared footnote does
   not verify three claims at once.
3. **Open and compare** — open each source and compare what it actually says to the
   claim. Quote or closely paraphrase the supporting passage; a source that supports
   a neighboring fact but not the claimed one is a miss with the gap named.
4. **Mark hit or miss** — hit: the source says the claimed thing. Miss: no source,
   unopenable source, or source that does not say it. State which of the three for
   every miss.
5. **Diff the pair** — when two briefs cover one topic, flag claims where they
   disagree and which brief's sourcing holds up.

## Validation

- Every checkable claim has a row: claim id, one-line claim, cited source, hit or
  miss, and the supporting passage or the miss reason. Any claim missing one of
  these is a miss until completed.
- Both briefs state their claim count, hit/miss totals, and unreachable sources.
  "Fully sourced" is reported only when every source opened; an unopened source is
  reported as unmeasured, never as supporting.
- Source quality judgments (whether a source is authoritative) are labeled as
  community-claim unless the vendor's own page settles it. Nothing here asserts
  what a source guarantees — only what it says.
- No row rewrites the brief's claims to match the sources. Flag the mismatch; do
  not quietly fix it.

## Output

A dated markdown file with one claim-by-claim ledger per brief: each row holds the
claim id, the claim in one line, the cited source, hit or miss (with miss reason),
and the supporting passage. End with the single most consequential miss and the
decision it needs.

## Boundaries

- Read-only. Never edit the brief, the sources, or any doc while verifying, and
  never publish the ledger as a public correction without the owner's explicit yes.
- Never present verification as legal or factual sign-off: a hit means the source
  says it, not that it is true, and consequential claims get a qualified human.
- Never paste credentials, tokens, or private doc contents beyond the cited passages
  into findings or drafts.
- Never report a single "trust score" with the method hidden: the per-claim ledger
  stays visible and every hit stays quotable.
