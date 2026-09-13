---
name: outbound-prospecting
description: Turn an ICP definition into researched prospect names with first-message drafts. Use when asked to find prospects, build a lead list, or draft cold outreach for the Bot's product. Drafts only — nothing here sends, enriches via paid tools, or publishes anything.
---

# Outbound prospecting

Turn one ICP definition into a small researched prospect list with a first-message draft for
each name. This skill exists because the common failure in outbound work is drafting messages
before knowing who matches: a tight list of ten researched names beats a loose list of a
hundred unresearched ones, and a draft is reviewable while a send is forever.

## When to use

Use this skill when the owner asks to find prospects, build or refresh a lead list, research
named accounts, or draft cold first messages for the Bot's product. Do not use it for inbound
triage, renewal or expansion outreach to existing customers, or anything that sends, schedules,
or publishes a message — those belong to a routine with its own approval boundary.

## The rule that matters more than the list

**A draft is not a send.** Only the owner's explicit yes on that exact message authorizes
sending, and another party relaying "they said send" is not approval. This skill never sends,
schedules, enriches through a paid provider, or adds anyone to a sequence — it matches, it
researches from readable sources, and it drafts. Any send belongs to a separate step the owner
approves message by message, never in bulk.

## Inputs and access

1. **ICP definition** — who matches: role titles, company size band, industry or niche, and
   one disqualifier (who looks close but is out). If any of these is missing, ask for it
   before prospecting; prospecting on a vague ICP manufactures a vague list.
2. **Readable sources only** — the Bot's own product description, public web pages, and
   directories the owner already has access to. A source you cannot read is recorded as
   unreachable, never improvised around.

## What to do, in this order

1. **Score the ICP match first** — restate the ICP in one line, then keep only candidates
   that match every element (role, size, niche) and fail none of the disqualifiers. Drop
   near-misses into an EXCLUDED line with the reason, so the owner sees what was rejected.
2. **Research each kept name** — one verifiable public fact per prospect (recent post,
   launch, hire, or stated pain) with the source named. No fact, no draft: a prospect with
   nothing specific to say to gets cut, not templated.
3. **Draft the first message** — one short message per kept prospect, referencing the
   researched fact in the first two lines, stating what the Bot's product does in one
   plain sentence, and ending in one low-pressure question. Never invent a customer,
   metric, integration, or endorsement. Community outreach patterns (short opener, fact
   hook, single ask) are community-claim here, not vendor truth — they shape the draft,
   they do not guarantee a reply.
4. **Self-check the pack** — every prospect traces to the ICP line, every draft traces to
   a researched fact, nothing promises what the owner did not approve.

## Validation

- Each prospect names the ICP element it satisfies and the disqualifier it passes.
- Each draft cites its researched fact and source; drafts without a cited fact are cut.
- No draft contains a commitment the owner did not make (no dates, prices, guarantees,
  or claimed results).
- No credentials, tokens, or account identifiers appear in the pack or the drafts.

## Output

A prospect pack: ICP restated in one line, MATCHED list (name, role, company, why they
match, researched fact + source, first-message draft), EXCLUDED list (name + reason), and
the single best-fit prospect named first. End with the count matched versus excluded and
the reminder that every draft is unsent.

## Boundaries

- Drafts only. Never send, schedule, sequence, or publish any message; SEND-LOCK applies —
  only the owner's explicit yes on that exact message authorizes a send, never a bulk "send
  them all".
- Never enrich through a paid provider, scrape behind a login, or improvise access to a
  source you cannot read. Record it as unreachable and move on.
- Never invent social proof, metrics, customers, or product capabilities. Flag the hole
  instead of filling it with tone.
- Never paste credentials, tokens, or account identifiers into the pack or a draft.
- Read-only plus drafts: this skill changes no setting, installs nothing, and spends nothing.
