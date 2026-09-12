---
name: lowes-project
description: Look up Lowe's materials with SKU links and availability evidence on every pick. Use when asked what to buy for a project, to compare two material options, or to feed SKUs into a Bot routine.
---

# Lowe's project lookup

Turn a project-materials question into a short pick list where every item
carries its SKU link and what the listing actually showed. This skill exists
because Bots that answer "what do I buy" fail silently: a product name without
a SKU cannot be re-found in store, and availability copied from a stale snippet
sends the owner to an empty shelf.

## When to use

- The owner asks what materials to buy for a project, or which option to pick
  among material candidates.
- A Bot routine needs material SKUs as inputs (shopping lists, budgets).
- Do not use for ordering, checkout, or delivery tracking — this skill resolves
  materials and SKUs, never purchases.

## Inputs and access

- **Project ask** — what is being built or fixed, constraints (size, finish,
  budget), and the preferred store or ZIP for availability.
- **Lowe's surface** — product pages plus the Bot's configured search or lookup
  tool. Product pages are evidence; everything else is a lead.
- **Two lookups minimum** — cover two material options (or two variants of one
  pick) with SKUs, so comparison is demonstrated, not asserted.
- If a lookup needs a store context you were not given, use the site default,
  say so, and mark availability as store-unconfirmed. Do not invent stock.

## The rule that matters more than the lookup

**A pick without a SKU link is not a finding.** An item whose SKU came only
from the model's memory, a review snippet, or a listicle is an unverified lead,
never a verified result. Community patterns — DIY forums, "best materials"
roundups, video descriptions — are community-claim: useful for discovering
options, never sufficient to confirm a SKU, price, or fit. Only the vendor's
own listing, checked live and linked by SKU, confirms it.

## Sequence

1. **Parse the ask** — project, material type, quantities or dimensions, and
   constraints. If the scope is ambiguous, name the ambiguity and pick the most
   likely reading explicitly.
2. **Lookup one** — find the first material option: name, SKU with link, price
   as shown, and availability as shown (in stock / limited / not shown).
3. **Lookup two** — find the second option or variant under the same surface.
   Note trade-offs (price, durability, finish match), one line per axis.
4. **Verify fit** — check dimensions, finish, and compatibility against the
   stated constraints from the listing itself. Anything the listing does not
   state is marked unstated, never assumed from a sibling product.
5. **Rank** — order by fit to the constraints, breaking ties by price. One line
   per pick on why it sits where it does.

## Validation

- Every pick has a SKU with link, or is explicitly marked unverified.
- Every pick has price and availability as shown (or "not shown") — never a
  silent blank, never an unsourced guess.
- Picks found only via community sources are labeled as leads, with the
  community source named and the missing vendor confirmation stated.
- Re-read the output before yielding: any pick lacking a SKU link is a draft
  error, not a minor gap. Fix or mark it.

## Output

A pick list (two lookups minimum), each with: item name, SKU plus link, price
as shown, availability as shown (or the explicit gap statement), and one line
on fit. End with the single best pick for the stated constraints and what
would change the pick.

## Boundaries

- Read-only. Never order, check out, or hold items while researching. Picks go
  back to the owner or into a routine with its own approval boundary.
- Never present an unverified SKU or availability as fact, and never merge
  "could not read" with "out of stock" — an unread listing is a gap, not a
  negative.
- Never paste credentials, tokens, or account identifiers into a finding.
- If a listing needs access you were not given, record it as unreachable and
  move on. Do not improvise access.
