---
name: homedepot-project
description: Look up Home Depot materials and project supplies by SKU with links for a grokbot project. Use when asked for material prices, product availability, SKU comparisons, or a project supply list tied to Home Depot listings.
---

# Home Depot project

Look up project materials on Home Depot by SKU, compare options, and produce a
supply list where every item carries its SKU and product link.

This skill exists because material names are ambiguous across brands and
stores: a "2x4 stud" is a dozen different SKUs at different prices, and only
the SKU plus link pins down exactly which item the list means.

## The rule that matters more than the list

**No SKU, no item.** A material without a verifiable SKU and product link is a
guess, not a recommendation. If a lookup fails or a price cannot be confirmed,
say so and name what would settle it — never invent a SKU or a price.

## When to use

- A teammate asks for material prices, options, or availability at Home Depot.
- A project needs a supply list tied to purchasable SKUs with links.
- Two candidate products need a side-by-side comparison on price and spec.
- Do not use for contractor quotes, labor estimates, or store inventory
  guarantees. Community DIY advice is community-claim — only the vendor's own
  listing page is evidence for price and spec.

## Inputs and access

- Two lookups minimum: each names the material, the SKU under review, and the
  product URL being compared.
- The project context for the list: quantities, dimensions, or constraints that
  decide between candidate SKUs.
- Access is read-only through the vendor's public listings or the product
  pages the operator provides. If a page cannot be read, record it as
  unreachable and stop — do not improvise availability or scrape behind access
  controls.

## Sequence

1. **Fix the candidates** — record each material with its SKU and product URL
   before comparing anything. A lookup without a SKU never enters the list.
2. **Pull lookup one** — price, spec, and availability as shown on the listing
   page, with the URL and the date checked.
3. **Pull lookup two** — same fields for the second material or candidate SKU,
   from its own listing page.
4. **Compare on stable keys** — match on SKU, never on product names or
   thumbnails. Names shift; SKUs identify.
5. **Build the supply list** — quantities against confirmed SKUs, each row with
   SKU, link, unit price, and date checked.
6. **Flag the unconfirmed** — anything without a readable listing is marked
   unmeasured with what would settle it, never priced by analogy.

## Validation

- Both lookups returned readable listing pages; an unreadable page is a failed
  input, not a cheap item.
- Every listed item carries a SKU and a product link that resolves to that SKU.
- Prices are quoted as-of a date, never as standing facts.
- Spot-check the two most expensive rows against a re-read before treating the
  list as final.

## Output

A markdown supply list with: one row per material (SKU, link, unit price, date
checked); the side-by-side comparison behind any either/or choice; items that
could not be confirmed and why. End with the total for confirmed items only —
never blend guesses into the total.

## Boundaries

- Read-only. Never place orders, hold inventory, or publish anything while
  researching.
- Never present community DIY claims or forum prices as listing findings.
  Community claims are community-claim; only the vendor's listing page is
  evidence.
- If a listing requires access you were not given, record it as unreachable
  and move on. Do not improvise access.
- No secrets, tokens, or account identifiers are stored, logged, or pasted
  into reports. Access is provided per session by the operator, never embedded
  in files.
