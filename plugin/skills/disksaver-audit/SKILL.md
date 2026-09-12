---
name: disksaver-audit
description: Use when asked whether the Grok Bot fleet is running out of local history — to run the pressure-read plus archive-verify sequence against the current replica set and report which replicas sit at the client cap and whether the archive actually holds them. Use when a teammate asks "are we losing history" or before any cleanup that deletes local state.
---

# Disk-saver audit

Turn the replica set into a pressure verdict: how many replicas exist, how many sit at
the client entry cap, and whether the archive holds everything the cap is about to
rotate away — and be explicit about what you could not verify.

This skill exists because the desktop keeps roughly the last 200 transcript entries per
Bot and then silently drops the oldest with every new turn. Four replicas measured at
exactly the cap is a cap, not a coincidence — and past the cap the archive is the only
copy, so an audit that reads pressure without verifying the archive is half a verdict.

## The rule that matters more than the audit

**"Archived" means verified present, never assumed from a manifest.** A manifest row
that says a replica was archived, a run that exited zero with no per-replica check, an
archive file whose entry count nobody compared to the replica's — each of those is a
claim, not coverage. An audit that reports "all archived" from filenames spends the
reader's history the same way rotation does: silently and permanently. If you did not
compare entry counts, say so and name the replica.

## What to check, in this order

1. **Pressure read** — list every replica in the current set with its entry count and
   flag each one at or above the client cap. Record the cap value you compared
   against and the pull timestamp; a count without a cap is not pressure.
2. **Archive verify** — for each at-cap replica, confirm the archive holds its entries
   by comparing counts (archive entries vs. replica entries), not by filename.
   Orphaned replicas from deleted Bots count double: nobody will ever re-read them
   except from the archive.
3. **Stale-archive check** — confirm the newest archive is fresh enough that the gap
   since its capture holds no at-cap replica's missing turns. A stale archive over a
   capped replica is history actively being lost — flag it as the top finding.
4. **Verdict** — per replica: safe, at-cap-and-archived, or at-cap-and-exposed. The
   exposed list is the whole point of the audit; everything else is context.

## Output

A dated audit with the replica count, the at-cap list with entry counts, the
archive-verify result per at-cap replica (counts compared, both sides cited), the
archive freshness reading, and the exposed list. End with the single most
consequential exposure and the action it needs — archive now, or accept the loss
explicitly.

## Boundaries

- Read-only. Never delete a replica, prune an archive, rebuild a Bot, or change any
  retention behavior while auditing.
- Never report a replica as archived from a manifest row, a filename, or an exit code
  alone: counts compared on both sides, or the replica stays on the exposed list.
- Never merge "could not read a replica" with "replica is fine" — an unread replica
  is a gap, not a negative. Name it and what would settle it.
- If a surface requires access you were not given, record it as unreachable and move
  on. Do not improvise access.
