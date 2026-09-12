---
name: vector-retrieval
description: Retrieve top-k passages from a local corpus by vector similarity, with a score shown per hit. Use when asked to find what a local corpus says about a question, ground an answer in local docs, or rank candidate passages before drafting. Read-only — it retrieves and ranks, never edits, sends, or publishes.
---

# Vector retrieval

Turn a question into a ranked list of the k most similar passages in a local corpus, each with its
similarity score visible. This skill exists because retrieval is the cheapest way to ground an
answer in what was actually written, and because a ranked list without scores is unverifiable —
the reader cannot tell a strong match from a weak one.

## The rule that matters more than the ranking

**A score is evidence, not a verdict.** Never present a top hit as the answer without showing its
score and what would change your mind (a higher-scoring passage you excluded, a corpus gap you
hit). A low top score means the corpus does not contain the answer — say so instead of dressing
up the least-bad passage as a finding. Retrieval that hides weak scores manufactures confidence
the corpus never earned.

## What to retrieve, in this order

1. **Corpus + access** — name the local corpus (which docs, which snapshot) and confirm you can
   read it. If the corpus needs a credential you were not given, record it as unreachable and
   stop. Do not improvise access.
2. **Query** — restate the question as a single retrieval query, plus any filters (date range,
   doc type, section). One query per pass; a second angle gets its own pass with its own scores.
3. **Top-k retrieval** — embed the query the same way the corpus was embedded, score every
   passage by similarity, and return the top k (default 5) ordered by score descending. Query
   and corpus must share one embedder — mixed embedders make scores incomparable.
4. **Validation** — check before reporting: every hit shows its numeric score; all scores share
   one scale; the top score clears your stated floor for "the corpus answers this" (state the
   floor); near-duplicate passages are flagged, not double-counted; anything below the floor is
   labeled weak, never silently dropped.
5. **Community patterns** — chunking sizes, k values, and score floors seen in community writeups
   are leads, never truth: label them community-claim and verify against this corpus before
   adopting any of them.

## Output

A ranked hit list: rank, passage (or excerpt with source pointer), similarity score, and source
(which doc, which section, which snapshot). Every hit carries its score on one shared scale;
weak hits are labeled weak. End with the single best passage and whether the top score justifies
answering from the corpus — or the statement that the corpus does not contain the answer.

## Boundaries

- Read-only. Never edit the corpus, re-embed anything into it, send, or publish while retrieving.
- Never report a score without its scale, and never compare scores across different embedders or
  corpora as if they were one ranking.
- Never paste credentials, tokens, or account identifiers into a query, a finding, or a hit list.
- Never present community retrieval lore (chunk sizes, k defaults, score thresholds) as vendor
  truth — it is community-claim until measured here.
- If the corpus cannot be read, record it as unreachable and move on. Do not improvise access.
