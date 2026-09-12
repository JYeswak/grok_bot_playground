---
name: maps-local-intel
description: Look up places and local services with address and opening-hours evidence attached to every candidate. Use when asked where something is, what is near an area, or for a place's address or hours before routing it into a Bot routine.
---

# Maps local intel

Turn a place or local-service question into a short ranked candidate list where every
candidate carries its street address and opening hours with the source named inline.
This skill exists because Bots that answer "where" questions fail silently: a name
without a verified address routes nowhere, and hours copied from a stale snippet send
the owner to a closed door.

## When to use

- The owner asks where a place or local service is, what is near an area, or which
  option to pick among nearby candidates.
- A Bot routine needs a place's address or hours as an input (directions, reminders,
  visit planning).
- Do not use for route planning itself, delivery tracking, or anything needing
  live position data — this skill resolves places, not movement.

## Inputs and access

- **Query** — what kind of place or service, and the area to search (neighborhood,
  city, or "near" an anchor the owner names).
- **Maps search surface** — the Bot's configured place-search tool or maps index.
  Read-only: search and read place details only.
- **Place detail pages** — the vendor's own listing for each candidate (address,
  hours, current status). These are evidence; everything else is a lead.
- If no maps surface is configured, say so and stop. Do not improvise lookups from
  memory or from general web results presented as map data.

## The rule that matters more than the lookup

**An address or hours value without a named source is not a finding.** A candidate
whose address came only from the model's memory, a review snippet, or a listicle is
an unverified lead, never a verified result. Community patterns — review sites,
"best of" lists, forum threads — are community-claim: useful for discovering
candidates, never sufficient to confirm an address or hours. Only the vendor's own
listing (or the place's own published page, checked live) confirms them.

## Sequence

1. **Parse the ask** — place or service type, area anchor, and any constraints
   (open now, open Sunday, walking distance). If the area is ambiguous, name the
   ambiguity and pick the most likely reading explicitly rather than guessing
   silently.
2. **Search** — run the query against the maps surface. Keep the top three to five
   candidates; drop clear mismatches (wrong city, wrong category) with one line on
   why each was dropped.
3. **Verify the address** — open each surviving candidate's listing and record the
   street address exactly as shown, with the listing named as source. No listing,
   no address: mark it unverified, never fill it in from memory.
4. **Verify the hours** — read each candidate's published hours and current open
   status from the same listing. Note holiday or special-hours flags where shown.
   If hours are absent from the listing, say "hours not published" rather than
   substituting a sister location's hours or a generic schedule.
5. **Rank** — order by fit to the stated constraints (open-now first when asked),
   breaking ties by distance to the anchor. One line per candidate on why it sits
   where it does.

## Validation

- Every candidate in the output has an address line with its source, or is
  explicitly marked unverified.
- Every candidate has an hours line with its source, "hours not published," or
  "could not read" — never a silent blank, never an unsourced guess.
- Candidates found only via community sources are labeled as leads, with the
  community source named and the missing vendor confirmation stated.
- Re-read the output before yielding: if any address or hours value lacks a
  source label, it is a draft error, not a minor gap. Fix or mark it.

## Output

A ranked candidate list, three to five entries, each with: name, street address
plus source, hours plus source (or the explicit gap statement), distance or area
relative to the anchor, and one line on fit. End with the single best pick for the
stated constraints and what would change the pick (for example, "if Sunday hours
matter, pick candidate two").

## Boundaries

- Read-only. Never place orders, book tables, call places, or write reviews while
  researching. Findings go back to the owner or into a routine with its own
  approval boundary.
- Never present an unverified address or hours as fact, and never merge "could
  not read" with "closed" or "does not exist" — an unread listing is a gap, not
  a negative.
- Never paste credentials, tokens, or account identifiers into a finding.
- If a listing needs access you were not given, record it as unreachable and move
  on. Do not improvise access.
