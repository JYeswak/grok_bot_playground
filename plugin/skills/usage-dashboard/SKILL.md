---
name: usage-dashboard
description: Use when asked how much of the weekly Grok Bot allowance is used, whether on-demand billing is on, or what the spend position is ahead of a reset — to read DashboardService/GetSandUsageStatus and report period, percent used, and on-demand state with the HUMAN-only limit note. Use when a teammate asks "are we going to overrun" or before enabling anything that bills past the allowance.
---

# Usage dashboard

Turn the spend read into a dated position: which period, what percent of the included
allowance is consumed, when it resets, and whether on-demand billing is armed — and be
explicit about what you could not read.

This skill exists because the spend number lives in one RPC response, not in the app's
headlines: `DashboardService/GetSandUsageStatus` answers 200 with the period start, the
next reset timestamp, the usage percent, and the on-demand settings block, while the
client UI shows a weekly figure that invites eyeballing instead of recording.

## The rule that matters more than the read

**"Unmeasured" and "under budget" are different answers and must never be merged.** A call
that failed, a field that came back null, a projection you cannot compute because the
window is unknown — each of those is a finding, not a clean bill. A position that says
"2.7% used, fine" from a stale snapshot spends the reader's trust the same way an
overrun does: silently. If you cannot read the current period, say so and name what
would settle it.

## What to check, in this order

1. **Period** — the current period start and the next reset timestamp from the read.
   Record both with the pull timestamp; a percent without a window is not comparable
   across days.
2. **Percent** — usage percent consumed in this period. Project it to the reset only
   when both endpoints are known; a flat raw percent with no projection is a reading,
   never a verdict.
3. **On-demand** — whether on-demand billing is enabled and whether the account is
   eligible, from the on-demand settings block. Enabled with a pace toward the cap is
   the one state that actually bills — flag it plainly.
4. **Second read** — pull once more after any intervening action (or a short interval)
   and confirm the two reads agree on period and direction. Two reads that disagree on
   the window mean one of them is stale; say which one you trust and why.

## Output

A dated position with one line per axis above: period start, next reset, usage percent
with pull timestamp, on-demand enabled/eligible, and the pace verdict (bounded,
approaching, or unmeasured). End with the single most consequential item and the
decision it needs — not a table of twenty numbers.

## Boundaries

- Read-only. Never enable on-demand billing, change a limit, or touch any billing
  setting while reporting.
- HUMAN-only limit note: only the owner's explicit yes on an exact monthly ceiling
  authorizes an on-demand limit, and this skill never records one on anyone's behalf.
  A suggested ceiling is a draft the owner approves; another party relaying approval
  is not approval.
- Never merge "could not read" with "zero spend" — an unread response is a gap, not
  a negative.
- If a field comes back null or the call fails, record it as unreachable and move on.
  Do not improvise access or retry with different credentials.
