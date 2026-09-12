---
name: zillapi-data
description: Pull structured data records through the Zillapi surface with field-level provenance on every value. Use when asked to fetch Zillapi records, compare two pulls, or feed Zillapi fields into a Bot routine.
---

# Zillapi data

Turn a data question into a sourced field table where every value names the pull and
record it came from. This skill exists because Bots that quote API fields fail
silently: a value without provenance cannot be re-pulled, diffed, or trusted when
the vendor schema drifts.

## When to use

- The owner asks for records or field values available through the Zillapi surface.
- A Bot routine needs Zillapi fields as inputs (syncs, comparisons, briefs).
- Do not use for writes, mutations, or anything needing live streaming data — this
  skill pulls and cites records, never changes them.

## Inputs and access

- **Data ask** — which records and which fields, plus the comparison or use (one
  pull vs two pulls with provenance).
- **Zillapi surface** — the Bot's configured Zillapi tool or API client, keyed by
  `${ZILLAPI_KEY}` (variable name only, never the value). Read-only: pull and
  read records only.
- **Two pulls minimum** — run the ask twice (or two adjacent asks) so provenance
  is demonstrated, not asserted. Single-pull answers are drafts.
- If no Zillapi surface is configured, say so and stop. Do not improvise records
  from memory or from general web results presented as Zillapi data.

## The rule that matters more than the pull

**A field value without a named pull and record is not a finding.** A value that
came only from the model's memory, a cached snippet, or a forum thread is an
unverified lead, never a verified result. Community patterns — wrapper-library
docs, "example response" gists, forum threads — are community-claim: useful for
discovering field names, never sufficient to confirm a value. Only a live pull
through the configured surface, quoted with its record id, confirms it.

## Sequence

1. **Parse the ask** — record selector, field list, and what the two pulls
   compare (same record twice for freshness, or two records for diff). If the
   selector is ambiguous, name the ambiguity and pick the most likely reading
   explicitly.
2. **Pull one** — run the first pull, record the timestamp, endpoint or method,
   and record ids returned. Keep the raw field set; drop nothing yet.
3. **Pull two** — run the second pull under the same surface. Note what moved
   between pulls (value changed, record added or gone) with one line per move.
4. **Cite fields** — build the field table: each value paired with pull number
   and record id. No pull, no value: mark it unpulled, never fill it from memory.
5. **Summarize the diff** — one line per field on whether it is stable, moved,
   or missing, and what that means for the downstream use.

## Validation

- Every value in the output has a pull number and record id, or is explicitly
  marked unpulled.
- Two pulls are named with timestamps; a single-pull answer is a draft error.
- Fields found only via community sources are labeled as leads, with the
  community source named and the missing live-pull confirmation stated.
- Re-read the output before yielding: any value lacking provenance is a draft
  error, not a minor gap. Fix or mark it.

## Output

A field table (two pulls minimum), each row with: field name, value, pull number
plus timestamp, and record id. End with the single most consequential
stable-or-moved field and what depends on it.

## Boundaries

- Read-only. Never write, mutate, or delete records while pulling. Findings go
  back to the owner or into a routine with its own approval boundary.
- Never present an unpulled value as fact, and never merge "could not read"
  with "field does not exist" — an unread pull is a gap, not a negative.
- Never paste credentials, tokens, or account identifiers into a finding; refer
  to `${ZILLAPI_KEY}` by name only.
- If the surface needs access you were not given, record it as unreachable and
  move on. Do not improvise access.
