---
name: fanout-pattern
description: Fan one question out across two Bots and merge the answers with dissent preserved. Use when asked to cross-check a claim across Bots, compare Bot answers, or run one question through two Bots at once. Read-only fan-out over documented Bots only — nothing here sends, posts, or publishes anything.
---

# Fan-out pattern

Turn one question into a merged two-Bot result where every answer still
carries its Bot. This skill exists because single-Bot answers hide
disagreement, and orchestration showcases assume a parallel-agent workflow
that is OFF here: the practical move is a fan-out over documented Bots —
one question, two answers, citations attached — not a new orchestrator.

Use this skill for two-Bot fan-out only: one question in, a merged result
with dissent preserved out, nothing written back.

## The rule that matters more than the fan-out

**Merged or it misleads — cited or it didn't answer.** Every per-Bot result
names the Bot, the date, and the source it came from, and the merge never
presents two-Bot agreement as single-source truth: agreement is "both Bots
said X, cited", never "X is true". A result without a citation is a lead,
not a finding — label it as such or drop it. Community Bot rankings and
"best Bot" lore are community-claim: never present showcase consensus as
vendor or product truth. If neither Bot's output says it, the fan-out does
not say it.

## When to use

- Someone asks to cross-check a claim across Bots or compare what two Bots
  say.
- One question needs running through two Bots at once, with the
  disagreement mapped rather than averaged away.
- A single-Bot answer needs a second opinion before anyone acts on it.
- Do not use for more than two Bots per run, for undocumented or experimental
  agent workflows, for anything that sends or publishes, or for chaining —
  chaining is routine-chains, this skill fans out and merges, then stops.

## Inputs and access

1. **Question** — the single question both Bots get, verbatim. If the
   question is vague, sharpen it first; fanning out a vague question
   manufactures vague disagreement.
2. **Two documented Bots** — which Bots, by name, and what each can actually
   answer (a Bot that abstains on a question records an abstention — a
   finding, not a failure). Only documented Bots; nothing experimental,
   nothing gated behind a workflow that is OFF.
3. **Merge rules** — agreed before the fan-out, not after: agreement counts
   only with dual citations, dissent is quoted with sources, abstention is
   recorded, and a 1–1 split with no source edge is UNRESOLVED, never
   tie-broken by vibes.
4. Access is read-only: each Bot's readable sources through configured
   variables (for example `${BOT_API_KEY}`); never paste values, names
   only. A source you cannot reach is recorded as unreachable, never
   improvised around.

## Sequence

1. **Scope** — restate the question verbatim, name the two Bots, record what
   each can answer, and state the merge rules up front.
2. **Fan-out** — run the question once per Bot: Bot name, date, answer in
   its own words, source link or reference. Same question to both; tailoring
   the question per Bot is two different questions wearing one name.
3. **Merge** — apply the pre-agreed rules: dual-cited agreement, quoted
   dissent, recorded abstentions, UNRESOLVED on a sourceless split. The
   merge adds no new claims — it only arranges what the Bots said.
4. **Spotlight** — name the single disagreement that most needs the
   reader's attention, with both citations. A merge with no spotlight is a
   transcript, not a result.
5. **Verify** — re-check every per-Bot answer against its citation before
   finishing (see Validation). Anything that fails becomes an UNCITED LEAD
   or is cut.

## Validation

- Every per-Bot answer is backed by at least one source, dated, and quoted
  faithfully to the Bot's output.
- The merge introduces zero new claims; every line traces to one of the two
  Bot answers.
- A 1–1 split with no source edge is marked UNRESOLVED, never resolved by
  preference or eloquence.
- Both Bots are documented and reachable; an unreachable Bot is recorded as
  unreachable, never substituted or simulated.
- Two runs the shape was built for: a cross-check run (one claim tested
  across two Bots, verdict with dual citations) and a comparison run (one
  question, two cited answers ranked with the dissent spotlighted).

## Output

A dated merged result with:

- Header: the question verbatim, the two Bots, the date.
- **Per-Bot answers**: Bot name, answer, citation each.
- **Agreement map**: what both Bots said (dual-cited), who dissented
  (quoted, cited), abstentions recorded.
- **Spotlight**: the one disagreement needing the reader's attention.
- **Verdict**: where the merge lands, or UNRESOLVED with why.
- What could not be verified: unreachable sources, answers demoted to
  leads.

## Boundaries

- Approval boundary: read-only fan-out over documented Bots only. This
  skill never sends, posts, publishes, subscribes, or changes any setting
  in any Bot; SEND-LOCK applies to anything downstream — only the owner's
  explicit yes on an exact message authorizes a send, and no send can be
  authorized from this skill. Any outreach belongs to a separate routine
  with its own approval.
- Never paste credentials, tokens, or account identifiers into a result or
  a finding; `${VAR}` names only.
- Never report private-Bot contents beyond what the reader can already
  open. Never simulate, substitute, or speak for an unreachable Bot.
