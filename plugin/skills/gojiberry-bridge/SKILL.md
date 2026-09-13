---
name: gojiberry-bridge
description: Bridge the GojiberryAI MCP tool surface into narrowed, allowlisted tool calls. Use when asked to reach a GojiberryAI-exposed tool, list what its MCP surface offers, or plan a bridged call. Plans and narrows only — never executes a state-changing call without approval.
---

# Gojiberry bridge

Turn a "reach that tool" request into a narrowed tool-call plan against an explicit
allowlist. This skill exists because MCP bridging is a top corpus gap — a corpus measure
finds 6 of 645 Bots building on GojiberryAI MCP with no dedicated plugin — and because an
unscoped bridge executes whatever the surface offers, while an allowlisted one executes
only what the owner named.

Use when asked to call a GojiberryAI-exposed tool, to list what the bridged surface
offers, or to plan a two-call sequence through it. When-to-use is deliberately narrow:
named tool in, narrowed call plan out, nothing executed beyond the allowlist.

## The rule that matters more than the bridge

**Allowlisted or it doesn't run.** Every planned call names the exact tool, the exact
arguments, and the allowlist entry that permits it. A tool not on the allowlist is
unreachable — recorded as such, never improvised around. Community descriptions of what
a GojiberryAI tool "can do" are community-claim: never present them as vendor truth; if
the live surface does not list it, the plan does not use it.

## Inputs and access

1. **Tool request** — which capability the owner wants (what done, not how called) and
   which two calls at most the plan covers. If the request names no concrete tool, list
   the surface first and ask — bridging on a vague request manufactures a vague call.
2. **Live surface only** — the tool list the MCP endpoint actually returns, plus the
   allowlist the owner approved (tool names, argument bounds). Credentials travel as
   `${VAR}` names only, never values. A surface you cannot read is recorded as
   unreachable, never improvised around.

## What to do, in this order

Follow this sequence; each step feeds the next.

1. **List** — enumerate the reachable tools: name, one-line purpose, read vs.
   state-changing. Skip deprecated or undocumented entries on the first pass; they are
   a count of their own, not calls to silently plan.
2. **Narrow** — map the request onto at most two allowlisted tools with exact
   arguments. Anything off-allowlist becomes an UNREACHABLE note with what approval
   would permit it — never a planned call.
3. **Validation** — before writing the plan, check every call against its surface
   entry: tool named on the surface, arguments within the allowlist bounds, `${VAR}`
   names only, no secret values. Anything that fails validation is cut or relabeled —
   never promoted into the plan.

## Output

A call plan with one section per call: tool name, exact arguments, the allowlist entry
permitting it, and what to verify in the result. End with the unreachable list (tools
wanted but not permitted) and the single call that most needs the owner's eyes. Two
call shapes the skill was built for: a read-shaped call (list or fetch, result quoted
back with its source tool named) and a write-shaped call (exact payload, explicit
approval gate named, result verification stated up front).

## Boundaries

- Approval boundary: plans and narrows only. This skill never executes a
  state-changing call on the owner's word alone; SEND-LOCK applies — only the owner's
  explicit yes on that exact call authorizes execution, never a bulk "run them all".
  Read-shaped calls run only against allowlisted tools.
- Never paste credentials, tokens, or account identifiers into a plan or a finding;
  `${VAR}` names only.
- Never present community claims about the tool surface as vendor truth. If the live
  surface does not list it, record it as unverified and move on.
