---
name: stripe-read
description: Report Stripe balance and invoices through stripe-mcp. Use when asked whether payouts look healthy, which invoices are open, or for the stripe-watch weekly card. Never refund, payout, or change a customer.
---

# Stripe read

Read money. Do not move it. This account's `stripe-mcp` is connected
2026-09-12 with exactly two tools (*client-binary-verified* via
`gb inventory mcp`):

- `stripe_get_balance`
- `stripe_list_invoices`

There is **no** refund / payout / customer-update tool on this server
today. Do not "make up for that" in the Dashboard browser.

Pairs with template `stripe-watch`.

## The rule that matters more than the balance

**Two tools, both reads.** A request to refund, retry a charge, or
change a customer is REFUSED. Virtual card / `RaiseGrokBotVirtualCard`
is a different, Tier-3 surface — not this skill.

## When to use

- Weekly failed-payment / open-invoice card.
- Asked for current Stripe balance or invoice list.
- Do **not** use to refund, issue payouts, or open the Stripe Dashboard
  as a logged-in human.

## Inputs and access

1. **Question** — balance, invoices, or both.
2. **Tools on this Bot** — the two above. Missing →
   `plugin-enable-verify`.
3. No secret keys in chat. MCP is already connected.

## Sequence

1. **Probe** `stripe_get_balance` or `stripe_list_invoices` once.
2. **Report** amounts, currencies, invoice statuses, dates. No
   customer PII beyond what the tool already returned that the owner
   asked for.
3. Flag failed/open invoices as a list, not a collection email.

## Validation

- Only the two read tools were called.
- No Dashboard click, no refund, no payout.
- Figures come from the tool result, not memory.

## Output

Dated card: balance, invoice counts by status, the one row that needs
the owner. PATH `PLUGIN`.

## Boundaries

- Never refund, payout, capture, void, or email a customer.
- SEND-LOCK: this report is not a dunning email.
- Never paste restricted keys. Never open Stripe as a browser fallback
  to do a write the MCP lacks.
