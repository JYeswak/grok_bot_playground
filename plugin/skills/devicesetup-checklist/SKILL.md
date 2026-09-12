---
name: devicesetup-checklist
description: Use when setting up a new device — Studio, Brain, iPhone, or the next one — so its policies, permissions, and inventory record match the fleet. Use when a desktop "should already" have something or before trusting a fresh machine.
---

# Device-setup checklist

Turn a new device into fleet evidence: **policies, permissions, and inventory
record, each checked on that device**. This skill exists because the fleet is
a triple surface — Studio, Brain, iPhone — and desktops do not sync policies,
so "configured" on one machine means nothing on the next until it is checked
there.

Limits, stated plainly: this walks the checklist and records what each device
shows; it does not grant access, change security policy, or enroll the device
in anything — a check that finds a gap names it with owner and date, it does
not close it by improvisation.

## The rule that matters more than the checklist

**Assume nothing is configured until it is checked on that device.** Every
item is verified where the device shows it: the permission prompt, the
settings page, the notification that actually arrived. A step marked done
because "it worked on Studio" or "surely it carried over" is marked
unmeasured — and an unmeasured step fails the device, because a checklist
that trusts sync across machines that do not sync is worse than no checklist.

## When to use

- Setting up a new device for the fleet: fresh machine, new phone, rebuilt OS.
- Verifying an existing device after an OS upgrade, permission reset, or
  "it stopped working" report.
- Before trusting a device with approvals, notifications, or on-call duties.
- As a dry checklist: walked without changing anything, gaps named.
- Do not use for granting access, changing policy, or enrolling devices —
  those are separate changes with their own approval.

## Inputs and access

1. **Device and role** — which device, which fleet role (approvals,
   notifications, daily driver, on-call). A checklist with no named device is
   not a checklist — name it first.
2. **Prior evidence** — the last checklist for this or a sibling device, when
   it exists, so per-device drift is computed, not remembered.
3. Access is check-only on the device the owner names, with the owner present
   or explicitly permitting. Never reuse another device's grant as proof, and
   never change a setting to "test" the checklist. If an item needs access
   you were not given, record it as unreachable and move on. Credentials are
   referenced as `${VAR}` names only, never pasted.

## Sequence

1. **Fix the device and its role** — name the device, its OS, and what the
   fleet expects of it. Everything downstream checks inside this frame; a
   second device is a second checklist, never a carried-over pass.
2. **Check policies per device** — the Bot and platform policies that must
   hold here, verified in the surfaces that render them on this device. "Held
   on Studio" is a note, not a pass.
3. **Check permissions per device** — OS and mobile grants (notifications per
   Bot that can need approval, and every grant an approval path depends on),
   each verified where the device shows it, ending with one real prompt or
   event that proves the path works end to end.
4. **Write the inventory record** — device, OS, role, policy state,
   permission state, and proof event, in the place a later run reads. A check
   with no record is unverifiable.
5. **Diff against the prior checklist** — new gaps, closed gaps, OS moves,
   and role changes each get one line: what moved and why.
6. **Name the gaps** — anything unconfigured, unverified, or unreachable
   becomes a gap with an owner and a date, not a quiet omission.

## Validation

- Each device has a named role, per-item on-device evidence, a proof event
   for the approval path where one applies, an inventory record, and a gap
   list (possibly empty). Any device missing one of these is incomplete, not
   passing.
- Tested as a dry checklist first: walked without changing anything. A run
   that changed settings mid-walk is reported as a change with its own
   approval, never as a clean check.
- "Ready for the fleet" is reported only when every item evidenced on that
   device; a carried-over pass is reported as unmeasured, never as ready.
- Community setup lore (for example: generic "it syncs" claims) is labeled as
   community-claim, never as vendor truth. Nothing here asserts what any
   platform syncs — only what this device showed.

## Output

A dated markdown file with one evidenced checklist per device: role, policy
state, permission state, proof event, inventory record pointer, diff vs.
prior checklist, and gaps with owner and date. End with the single gap that
most needs the owner and the decision it needs.

## Boundaries

- Check-only by default. Never grant access, change a policy or permission,
   enroll a device, or "fix it while you're in there" — those need the
   owner's explicit yes on that exact device, and a dry checklist never
   implies it.
- Never paste credentials, tokens, or secret values into checklists,
   findings, or drafts; `${VAR}` names only.
- Never report "ready" from another device's evidence: unmeasured means
   failed, and failed means recheck on that device, not assumed.
- Never present a passed checklist as permanent: it proves the state on this
   date against this OS, not that every future update preserves it.
