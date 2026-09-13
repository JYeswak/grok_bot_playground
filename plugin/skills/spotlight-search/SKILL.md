---
name: spotlight-search
description: Use when asked to find something across the Grok Bot account with the built-in spotlight surface — to run scoped queries and report recorded hits with their source. Use when a teammate asks "where is that routine/shard/setting" or whether something exists anywhere on the account, never as a substitute for reading the authoritative surface directly.
---

# Spotlight search

Turn a "where is it" question into two scoped queries with recorded hits, each naming
the query, the scope searched, and what came back — and be explicit about what the
surface could not see.

This skill exists because `sand_spotlight` is ON for this account and unmeasured: no
routine uses it, no report cites it, and an unmeasured search surface either rots or
gets trusted blindly. Two recorded queries with hits on file are the difference between
a capability and a rumor.

## The rule that matters more than the hits

**A hit cites its query and its scope, or it does not ship.** Every result names the
exact query string, the scope it ran against (routines, shards, settings, inventory —
whichever the surface searched), and the pull timestamp. A hit with no query behind it
is an anecdote wearing a citation. A search that cannot say what it looked through is
worse than no search, because it invites confidence in coverage nobody measured.

## What to check, in this order

1. **First query** — the narrowest reading of the ask: exact name or id, scoped as
   tightly as the surface allows. Record query, scope, timestamp, and every hit with
   its source reference.
2. **Second query** — a broader or differently-worded pass (a synonym, a prefix, the
   neighboring scope the first query did not cover). Record the same fields. Two
   queries that agree an item is absent are evidence; one query is a shrug.
3. **Verify the best hit** — take the single most consequential hit and confirm it
   against the authoritative surface (the inventory, the routine list, the settings
   read — whichever owns that object). A spotlight hit confirmed elsewhere is a
   finding; unconfirmed, it is a lead.

## Output

A short report naming both queries with scopes and timestamps, the recorded hits per
query with source references, the verification result for the top hit, and what the
surface could not search. End with the answer to the original question in one line —
found (where), absent (scopes checked), or unmeasured (what blocked the read).

## Boundaries

- Read-only. Never change a setting, rename anything, or act on a hit while searching.
- Never present a spotlight hit as the authoritative record: it is an index over the
  account, and the owning surface has the final word. A hit that contradicts the
  owner is flagged, never trusted.
- Never merge "no hits" with "does not exist" — an unsearched scope is a gap, not a
  negative. Name every scope the queries did not cover.
- If the surface needs access you were not given, record it as unreachable and move
  on. Do not improvise access.
