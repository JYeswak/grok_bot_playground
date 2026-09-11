---
name: weekly-surface-watch
description: Use when asked what changed in Grok Bot this week, or on a weekly routine, to produce a dated report of what the vendor changed about this account — capabilities, parameters, docs, plugin catalog — and what is genuinely unknown. Use when a teammate asks "are we up to date" or "did anything change".
---

# Weekly surface watch

Produce a dated report of what the vendor changed **about this account** in the last week, and be
explicit about what you could not measure.

This skill exists because the interesting changes are invisible in the app: entitlements flip
server-side, experiment parameters are retuned with no client update, docs pages get edited in
place, and installed plugins ship new code under the same name.

## The rule that matters more than the report

**"Unmeasured" and "unchanged" are different answers and must never be merged.** A fetch that
failed, a field that came back null, a search that errored — each of those is a finding, not a
quiet week. If you cannot read something, say so and name what would settle it. A report that
says "no changes" because its inputs broke is worse than no report, because it spends the
reader's trust.

## What to check, in this order

1. **Client** — the installed version, and whether a newer one is staged but unapplied.
2. **Capabilities** — the account's feature-gate set. Diff on stable ids, never on names: names
   come and go with how much of the client bundle is decodable, and a name-only diff reports
   movement the vendor never made.
3. **Parameters** — dynamic configs and layers. Names are hashed; **values are plaintext**, so a
   config you cannot name still tells you exactly which knob moved.
4. **Docs** — hash every page and compare to last week. Storing a hash and never comparing it is
   an archive, not a watch. Check every official tree, not just the one an index lists.
5. **Catalog** — the plugin marketplace and, specifically, the commit each *installed* plugin is
   pinned to. A moved ref is new third-party code inside a Bot that was approved once.
6. **Community** — one or two curated indexes. Treat these as leads, never as evidence: anything
   actionable gets verified against the vendor's own page and then against this account.

## Output

A dated markdown file with one section per axis above. Each section states what changed, what did
not, and what could not be read. End with the single most consequential item and the decision it
needs — not a list of twenty.

## Boundaries

- Read-only. Never change a setting, install a plugin, or publish anything while auditing.
- Never report a count of "your features" from a raw enabled-gate total: most of those belong to
  other products on the same backend. Report what is nameable and state the remainder.
- If a source requires a credential you were not given, record it as unreachable and move on.
  Do not improvise access.
