---
name: zendesk-triage
description: Use when asked to triage a Zendesk ticket batch — rank tickets by priority with a rationale per ticket, and draft next actions. Reads and drafts only; status changes, assignments, and replies need the owner's explicit yes.
---

# Zendesk triage

Turn a Zendesk ticket batch into a ranked priority list with a rationale per ticket and a
drafted next action for everything that needs one. This skill exists because triage is
where support quality is won or lost, and because a drafted reply is reviewable while a
sent one is forever.

## The rule that matters more than the triage

**A draft is not a send, and a recommendation is not a status change.** Only the owner's
explicit yes on that exact message or change authorizes sending, assigning, merging, or
closing anything, and another party relaying "they said send" is not approval. This skill
never sends, assigns, merges, closes, or changes a field — it reads and drafts. Any write
belongs to a routine with its own approval boundary, not to this skill.

## When to use

- Someone asks to triage a Zendesk batch, clear a queue view, or review new tickets.
- Tickets need prioritizing with a stated rationale each: urgency, impact, age, SLA risk.
- A next action is needed per ticket (draft reply, suggested assignee, suggested macro)
  and must stay a draft until approved.
- Do not use for bulk automation design, for SLA policy changes, or for systems beyond
  the ticket batch you were given.

## Inputs and access

1. **Ticket batch** — the required input: ticket ids with subject, requester type,
   created date, current status, and priority field. Two batches are the comparison
   input: batch A and batch B, each with its own tickets.
2. **Queue context** — which view or filter produced the batch, and the SLA clocks that
   apply. Needed to weigh age and breach risk honestly.
3. **Angle** — fresh triage (rank an untriaged batch) or re-triage (re-rank a batch
   against a prior triage). Default to fresh when unclear; state which you chose.
4. Access is read-only: ticket reads through the credentials you were given, named as
   `${ZENDESK_SUBDOMAIN}`, `${ZENDESK_USER}`, `${ZENDESK_TOKEN}` only — never pasted
   values. If a ticket needs a system you cannot read, record it as unreachable and move
   on. Do not improvise access.

## Sequence

1. **Inventory** — confirm the batch: count tickets, note date range and statuses, flag
   entries missing fields. If the batch is unusable, stop and say so.
2. **Score** — rank each ticket by urgency, impact, age, and SLA risk, writing the
   rationale per ticket as you go. A rank without a rationale is a guess, not a triage.
3. **Draft** — for everything needing action, draft the next step: reply text, suggested
   assignee, or suggested macro — each labeled as a draft awaiting approval.
4. **Reconcile** — with two batches, diff ticket by ticket: agreements, priority
   conflicts, tickets in neither batch's ranking. Resolve each with a stated reason.
5. **Verify** — re-check every ticket id against the batch before finishing. A rationale
   attached to the wrong ticket is worse than no rationale.

## Validation

- Every ticket in the batch has a priority rank and a written rationale citing urgency,
  impact, age, or SLA risk.
- Every ticket needing action has a drafted next step labeled as a draft, never as done.
- With two batches, every conflict and orphan is named with its resolution and reason.
- Customer history from elsewhere ("this account always escalates") is a lead, never
  evidence: anything actionable gets verified against the ticket itself.

## Output

A markdown triage with:

- Header: batch size, date range, queue view, angle chosen (fresh or re-triage).
- Ranked list: ticket id, subject, rank, rationale, drafted next action with thread link
  and date where applicable.
- Delegate/escalate lane: tickets needing another owner, with the owner named.
- What could not be triaged: tickets unreachable or missing fields, and the single
  ticket that most needs the owner.

## Boundaries

- Read-only plus drafts. Never send a reply, change a status, assign, merge, close,
  delete, or edit any ticket field.
- Writes of any kind need the owner's explicit yes on the exact message or change — and
  the write itself happens outside this skill.
- Never draft a commitment the owner did not make (no promised dates, refunds, or yeses
  invented from tone). Flag the commitment-shaped hole instead.
- Never paste credentials, tokens, or account identifiers into a triage or a finding.
