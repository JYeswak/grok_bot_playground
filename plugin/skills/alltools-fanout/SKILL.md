---
name: alltools-fanout
description: Fan a question out across multiple tools and return per-tool results with citations. Use when asked to compare tool answers, cross-check a claim across tools, or run one question through several tools at once. Read-only fan-out — nothing here sends, posts, or publishes anything.
---

# All-tools fan-out

Turn one question into a cited multi-tool result where every answer carries its tool
and source. This skill exists because single-tool answers hide disagreement: a corpus
measure finds 5 of 645 Bots building on All Tools Verse with no dedicated plugin, so
the practical move is a fan-out over readable tools — one question, several answers,
citations attached — not a new aggregator.

Use when asked to compare what tools say, cross-check a claim across tools, or run one
question through several tools at once. When-to-use is deliberately narrow: one
question in, per-tool cited results out, nothing written back.

## The rule that matters more than the fan-out

**Uncited or it didn't answer.** Every per-tool result names the tool, the date, and
the source it came from. A result without a citation is a lead, not a finding — label
it as such or drop it. Community tool rankings and "best tool" lore are
community-claim: never present aggregator consensus as vendor or product truth. If the
tool's own output does not say it, the fan-out does not say it.

## Inputs and access

1. **Question** — the single question every tool gets, plus which tools to fan out to
   (at most a named handful). At most two fan-outs per run. If the question is vague,
   sharpen it first; fanning out a vague question manufactures vague disagreement.
2. **Readable tools only** — the tools the owner already has access to, queried as
   `${VAR}`-named credentials only, never values. A tool you cannot reach is recorded
   as unreachable, never improvised around.

## What to do, in this order

Follow this sequence; each step feeds the next.

1. **Scope** — restate the question verbatim, name the tools, and record what each
   tool can actually answer (some tools abstain on some questions; an abstention is a
   finding, not a failure).
2. **Fan-out** — run the question once per tool, newest-first: tool name, date,
   answer in its own words, source link or reference. Skip status-page noise and
   duplicate syndication on the first pass; they are a count of their own, not
   answers to silently merge.
3. **Validation** — before writing the result, check every per-tool answer against
   its citation: each answer backed by at least one source, each date current, each
   quote faithful to the tool's output. Anything that fails validation becomes an
   UNCITED LEAD or is cut — never promoted into a result.

## Output

A dated result with one section per tool: tool name, answer, citation, and where it
agrees or disagrees with the others. End with the agreement map (what most tools
said, who dissented with citation) and the single disagreement that most needs the
reader's attention. Two fan-out shapes the skill was built for: a cross-check run
(one claim tested across tools, verdict with citations) and a comparison run (one
question, ranked per-tool answers each cited).

## Boundaries

- Approval boundary: read-only fan-out. This skill never sends, posts, publishes,
  subscribes, or changes any setting in any tool; SEND-LOCK applies to anything
  downstream — only the owner's explicit yes on an exact message authorizes a send,
  and no send can be authorized from this skill. Any outreach belongs to a separate
  routine with its own approval.
- Never paste credentials, tokens, or account identifiers into a result or a
  finding; `${VAR}` names only.
- Never report private-tool contents beyond what the reader can already open. If a
  tool needs access you cannot get, record it as unreachable and move on.
