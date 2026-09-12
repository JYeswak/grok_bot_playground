---
name: update-scheduler
description: Check whether a staged Grok Bot client build sits unapplied on each desktop, and walk the stage-quit-verify sequence. Use when asked if the fleet runs the latest client, after a vendor release note, or on the weekly tick. Never force-quits anything.
---

# Update scheduler

Keep every desktop on the client the vendor shipped, without ever forcing a quit on anyone.

## The rule that matters more than the update

**A staged build is not an applied build.** Squirrel `ShipIt` stages the new version and waits;
only a quit applies it, and `sand_auto_update_when_idle` being on did not apply it last time
(0.24.0 sat staged for days while 0.47.0 was the shipped truth). "Latest staged" and "latest
running" are different answers and must never be merged.

## What to check, in this order

1. **Running version per desktop** — the app bundle on Studio and on Brain, plus the host of
   record. Record all three; assume nothing is configured until checked on that device.
2. **Staged version** — Squirrel `ShipIt` state: is a newer build downloaded and waiting?
3. **Vendor truth** — the newest staged/shipped version known to the audit. A desktop behind it
   is the finding.
4. **Quit window** — who is using the machine and when a quit is cheap. Propose the window;
   never take it.

## Output

Per desktop: running version, staged version (or none), behind-or-current verdict, proposed quit
window. End with the single oldest desktop and the sentence to send its human.

## Boundaries

- Check legs only. Never quit, restart, or update any app — the quit is a human act on their
  own machine, proposed here, taken there.
- Never report "current" from the staged version. Running version is the only version.
- If a desktop is unreachable over Tailscale, record it unreachable and move on. Do not
  improvise access.
