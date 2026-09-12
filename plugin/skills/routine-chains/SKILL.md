---
name: routine-chains
description: Chain routines through explicit handoffs with a declared output shape, owner, and stop rule. Use when asked to link routines together, pass one routine's result into the next, or build a multi-step routine chain. Defines the handoff contract only — never sends, posts, or publishes anything itself.
---

# Routine chains

Turn isolated routines into a chain where every handoff names what passes,
who owns the next step, and when the chain stops. This skill exists because
power users already chain Bots via handoffs, and undocumented chains fail
the same way every time: step two cannot parse step one's output, nobody
owns step three, and a loop nobody bounded runs until someone notices the
bill.

Use this skill for multi-step routine chains only: routines in, a handoff
contract out, verified by two chained runs.

## The rule that matters more than the chain

**A handoff without an owner is a dropped baton.** Every link in the chain
declares its output shape (what the next step receives), its owner (who —
human or Bot — is responsible for the next step running), and its stop rule
(when this chain ends, including failure). A step whose output the next
step cannot parse is not a handoff; it is a hope. A chain with no stop rule
is not automation; it is an incident with a schedule.

## When to use

- Someone asks to link routines together or pass one routine's result into
  the next.
- A multi-step job needs splitting across routines with clean handoffs
  between them.
- An existing informal chain (one routine's output pasted into another's
  input) needs a contract before it breaks.
- Do not use for single routines, for fan-out comparison across Bots (that
  is fanout-pattern), for failure paging (that is routine-alerts), or for
  anything that sends or publishes — each link's sends need their own
  approval, never the chain's.

## Inputs and access

1. **Steps** — the required inputs: which routines, in what order, what
   each receives and produces. At most a named handful of steps; a longer
   chain is two chains with a contract between them.
2. **Output shape per link** — the exact shape each step hands off: fields,
   evidence links, and the next action stated plainly. If the next step
   cannot validate the shape on receipt, the shape is unfinished.
3. **Owner per link** — who runs the next step and who is responsible when
   it does not run: a named human or a named Bot. A link with no owner
   stops the chain at design time, not at 3 a.m.
4. **Stop rule** — when the chain ends: success (terminal output delivered),
   failure (a step fails — the chain stops, it never improvises around the
   failure), and the hop cap (maximum links per run; hitting it ends the
   run, never silently extends it).
5. Access is each routine's own readable sources plus the handoff payloads.
   Integrations read through configured variables (for example
   `${ROUTINE_API_KEY}`); never paste values, names only. If a source needs
   access you were not given, record it as unreachable and move on.

## Sequence

1. **Map** — list the steps in order with each link's output shape, owner,
   and the chain's stop rule. Confirm every link has all three; a missing
   one is a design defect, not a detail.
2. **Shape** — write the handoff schema per link: fields, evidence links,
   next action. Keep shapes small and explicit; a handoff carrying
   everything carries nothing the next step can trust.
3. **Chain** — run step one, validate its output against the shape, hand to
   step two's owner, repeat. A step that receives an invalid handoff stops
   and reports — it never repairs the payload and continues.
4. **Stop** — end on the stop rule: success, failure, or hop cap. Log which
   rule fired and where; a chain that cannot say why it stopped cannot be
   trusted to have stopped.
5. **Verify** — confirm two chained runs completed on contract (see
   Validation). A chain verified once is a demo; twice is a pattern.

## Validation

- Two chained runs completed with every handoff matching its declared
  shape and every step owned.
- Every run can state which stop rule fired and at which link.
- A deliberately malformed handoff stops the chain with a report — never a
  repair-and-continue.
- The hop cap fired at least once in testing (or the test chain was built
  long enough to prove it fires); an untested stop rule is a wish.
- Two chains the shape was built for: a linear chain (digest routine hands
  shaped findings to a drafting routine, terminal output delivered) and a
  guarded chain (a validation step between two workers that stops the run
  on mismatch).

## Output

A handoff contract with:

- Steps in order, each with output shape, owner, and link to the next.
- **Stop rule**: success, failure, and hop-cap conditions stated plainly.
- Read-back proof: the two chained runs, with the stop rule each fired.
- Failure drill: the malformed-handoff test and what the chain did.
- What could not be verified: unreachable sources, links still ownerless.

## Boundaries

- Approval boundary: the handoff contract only. This skill never runs the
  chain on a schedule, never sends, posts, or publishes from any link, and
  never authorizes a send anywhere downstream — each link's outreach needs
  its own explicit owner approval on an exact message. Scheduling the chain
  belongs to a separate routine with its own approval.
- Never paste credentials, tokens, or account identifiers into a contract
  or a handoff; `${VAR}` names only.
- Never report routine contents beyond what the reader can already open. If
  a step needs access you cannot read, record it as unreachable and move on.
