---
name: sheets-reconciliation
description: Reconcile a sheet range against the source of truth it mirrors and report every mismatch with cell-level citations. Use when asked to check a sheet against an export, verify a rollup against its detail rows, or confirm a sheet still matches the system it claims to track. Read-only — proposes fixes, never writes them.
---

# Sheets reconciliation

Compare a sheet range against the source it claims to mirror and report every row that
disagrees, each cited to its cell and its source line. This skill exists because sheets
drift silently from their sources: a row edited by hand, an export re-pulled without
re-import, a rollup formula pointing at a stale range — and a report that says "all match"
without naming both sides and when each was read spends the reader's trust.

## The rule that matters more than the reconciliation

**A cited mismatch is a finding; an applied fix is an act.** Only the owner's explicit yes
on that exact cell change authorizes writing it back, and a relayed "they said fix it" is
not approval. This skill reads and reports — it never writes, overwrites, appends, or
deletes a cell. Any write belongs to a routine with its own approval boundary, not to
this skill.

## What to reconcile, in this order

1. **Sides and ranges** — name side A (workbook, tab, exact range, read timestamp) and side
   B (second tab and range, CSV export with filename and line count, or system pull with
   its timestamp). If either side cannot be read, record it as unreachable and stop: a
   reconciliation with one side missing is not "all match".
2. **Keys and normalization** — pick the join key (one column present on both sides) and
   normalize case, whitespace, and date formats before comparing. Name the key and every
   normalization applied; silent normalization is a silent verdict.
3. **Row-by-row compare** — walk every key present on either side. Each mismatch cites the
   side-A cell (Tab!A4:D4 form) and the side-B record (line number or record id). Keys
   missing on one side are findings, not skips.
4. **Rollup check** — where a summary cell claims a total over detail rows, recompute it
   from the cited detail range and compare. Cite both the summary cell and the range it
   claims to cover.
5. **Validation** — re-read every mismatch cell before reporting to rule out a stale read,
   and quarantine unparseable cells (error values, mixed types) as unreadable rather than
   forcing them into a verdict.

## Output

A mismatch table: key, side-A value with cell citation, side-B value with source citation,
verdict (missing-A, missing-B, value-drift, stale-read, unreadable). Then proposed fixes
as drafts, each naming the exact cell and the new value. End with match, mismatch, and
unreadable counts plus the single mismatch that most needs the owner.

## Boundaries

- Read-only. Never write, overwrite, append, or delete any cell while reconciling.
  Proposed fixes are drafts the owner confirms cell by cell.
- Never invent a source value to close a gap. If a side is unreadable, record it as
  unreachable and move on. Do not improvise access.
- Never paste credentials, tokens, or account identifiers into a finding or a proposed fix.
- A value read from one side is evidence about that side only. Never present an export's
  claim as the sheet's state, or the sheet's state as the source's.
