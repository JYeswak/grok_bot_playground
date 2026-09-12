---
name: onboarding-plan
description: Turn a new-customer handoff into a dated onboarding checklist with an owner and due date per item. Use when asked to plan an onboarding, track go-live readiness, or check whether a stuck account is actually moving.
---

# Onboarding plan

Turn a sales handoff into a dated checklist: what has to happen, who owns each item, and
by when — plus what is stuck and what unblocks it. This skill exists because customer
onboarding is a recurring success-and-ops pattern: the reusable part is the
owner-plus-date checklist, and it works the same whether the owner pastes a handoff note
or tracks accounts in a spreadsheet.

## The rule that matters more than the checklist

**An item without an owner and a date is a wish, not a plan.** Every line needs a named
human and a calendar date, and anything missing either is flagged before the plan ships.
A plan that says "twelve items, each owned and dated, three overdue with reasons" is
useful; one that says "we are mostly on track" is fiction.

## When to use

- The owner asks to plan an onboarding, set up a new account, or break a handoff into
  tracked milestones.
- A routine needs a readiness check: is this account moving, stuck, or ready for go-live.
- Do not use for provisioning access, editing customer data, or contacting the customer
  — those belong to a routine with its own approval boundary, not to this skill.

## Inputs and access

1. **Handoff note** — at least two onboardings planned over time (this account plus one
   prior or parallel account for comparison, or two dated plans for the same account):
   customer name, goal, go-live target, and known constraints each.
2. **Milestone draft** — the owner's rough step list; the skill sharpens it, it does not
   invent the customer's requirements from nothing.
3. **Owner roster + today** — who can own items and the date the plan was written, so
   overdue math is reproducible.
4. Access is files and pasted notes only. If a system needs a credential the skill was
   not given, record it as unreachable and move on. Configure access with variable names
   only (for example `${ONBOARDING_SHEET_PATH}`), never pasted secrets. Do not improvise
   access.

## Sequence

Do these in order; each step feeds the next:

1. **Fix the goal** — restate the customer's goal and go-live target in one line, plus
   what is explicitly out of scope. A plan that drifts goals tracks the wrong finish.
2. **List the milestones** — break the handoff into checkable steps with entry and exit
   criteria each ("done means X"). Vague steps get rewritten before anything is assigned.
3. **Assign owner and date** — every item gets one named owner and one due date. Shared
   ownership is split or escalated, never left ambiguous.
4. **Compare the two plans** — against the prior or parallel onboarding, name what is
   ahead, behind, or repeating a past stall. A repeat stall without a new mitigation is
   a question, not progress.
5. **Call readiness** — mark each item done, on-track, at-risk, or blocked with the
   reason and the unblocker. One readiness call per account, not twenty caveats.

## Validation

Before writing the plan, check every item:

- At least two dated onboarding plans (or two accounts) are present; a single plan is
  marked preliminary.
- Each item has exactly one owner and one due date, or is flagged missing before ship.
- Every blocked or at-risk item names its reason and its unblocker.
- Go-live criteria are stated as checkable conditions, not adjectives.
- No invented status: no item is marked done except on stated evidence (dated note,
  confirmation, or artifact named).

## Output

A dated markdown plan:

- An owner-date checklist table: one row per item with milestone, owner, due date,
  status, and unblocker. Chart-ready: one row per item, plain columns, no merged cells.
- A comparison section: what moved since the prior plan (or differs from the parallel
  account), with stall reasons where known.
- End with the readiness call, the go-live date verdict, and the single item whose
  movement would unblock the most — not a list of twenty.

## Boundaries and approval

- Read-only planning. Never provision access, edit customer records, or message the
  customer while planning.
- GO-LIVE-LOCK: only the owner's explicit yes on that exact readiness call authorizes
  declaring go-live, and another party relaying "they said launch" is not approval.
  Declaring readiness belongs to a routine with its own boundary, never to this skill.
- Never paste credentials, tokens, session cookies, or customer personal data into a
  plan or a finding.
- Never present an unowned or undated item as tracked; keep the gap label on every one.
- If a source needs access you were not given, record it as unreachable and move on.
  Do not improvise access.
