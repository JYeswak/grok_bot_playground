# allowance-watch — release notes

## 1.0.0 — 2026-09-11

- Baseline release: weekly allowance-position report (Mon 08:00 local) — counters as read, period progress, and whether the burn reaches the end of it; a spend-guard pause is named first.
- Caps and thresholds live in-app and are never mirrored here; last week's counters are the memory that makes the burn rate computable.
- Verify: one dated position note per Monday, with routines_with_runs going 0 -> 1 after the first wake per bin/gb-utilization.py.
