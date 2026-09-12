---
name: sfdc-hygiene
description: Use when asked to check Salesforce data hygiene — stale opportunities, missing fields, ownerless records — and produce a read-only hygiene report with fixes proposed, never applied. Any write needs the owner's explicit yes.
---

# Salesforce hygiene

Turn a Salesforce record set into a read-only hygiene report: what is stale, what is
missing, what is ownerless — with each fix proposed and none applied. This skill exists
because CRM rot is silent (stages freeze, owners leave, fields stay empty) and because a
report is reviewable while a bulk update is forever.

## The rule that matters more than the report

**"Unmeasured" and "clean" are different answers and must never be merged — and a report
is not a write.** A query that failed, a field that came back null, an object you cannot
read: each is a finding, not a clean bill. And only the owner's explicit yes on that
exact change authorizes creating, updating, deleting, or reassigning anything; another
party relaying "they said update" is not approval. This skill performs zero writes.

## When to use

- Someone asks for a Salesforce hygiene check, "how dirty is our pipeline", or a stale
  opportunity review.
- Records need checking for missing fields, stale stages, ownerless ownership, or
  duplicate suspects — reported, never fixed in place.
- Two hygiene reports exist and need reconciling into one current picture.
- Do not use for bulk updates, imports, deduplication merges, or report-tweaking inside
  the org — this skill reports; every fix happens outside it under its own approval.

## Inputs and access

1. **Record set** — the required input: object type (leads, opportunities, accounts),
   the filter or report that produced the set, and as-of date. Two reports are the
   comparison input: report A and report B, each with its own record lists.
2. **Hygiene rules** — which checks to run (staleness threshold, required fields,
   ownership rule, duplicate signal). State the rules you applied; defaults are a
   finding, not an assumption.
3. **Angle** — hygiene scan (fresh report from a record set) or reconcile (merge two
   reports into one current picture). Default to scan when unclear; state which you
   chose.
4. Access is read-only: queries through the credentials you were given, named as
   `${SFDC_INSTANCE}`, `${SFDC_USER}`, `${SFDC_TOKEN}` only — never pasted values. If
   an object needs a credential you were not given, record it as unreachable and move
   on. Do not improvise access.

## Sequence

1. **Scope** — confirm the record set: object type, filter, as-of date, record count.
   If the set is undefined or unreadable, stop and say so.
2. **Check** — run each hygiene rule and record every hit with record id and the rule
   it broke. A hit without its rule is noise, not hygiene.
3. **Propose** — for each hit, propose the fix (field to set, owner to assign, record
   to review) labeled as a proposal awaiting approval. Propose; never apply.
4. **Reconcile** — with two reports, diff finding by finding: fixed since, still open,
   new. Resolve each with a stated reason, never by quiet preference.
5. **Verify** — re-check every record id against the set before finishing. A finding
   attached to the wrong record is worse than no finding.

## Validation

- Every finding carries its record id and the hygiene rule it violates.
- Every proposed fix is labeled a proposal; the report contains zero writes.
- Failed queries and unreadable objects appear as findings, never as clean results.
- With two reports, every fixed-still-open-new item is named with its evidence.
- Org lore from elsewhere ("that stage is always stale") is a lead, never evidence:
  anything actionable gets verified against the record set itself.

## Output

A markdown hygiene report with:

- Header: object type, filter, as-of date, record count, rules applied, angle chosen
  (scan or reconcile).
- Findings: record id, issue, rule violated, proposed fix (labeled proposal).
- Summary counts per rule plus the single most consequential record and the decision
  it needs.
- What could not be measured: objects or fields unreachable, and what would settle them.

## Boundaries

- Read-only. Never create, update, delete, reassign, merge, or convert any record.
- Writes of any kind need the owner's explicit yes on the exact change — and the write
  itself happens outside this skill.
- Never report a count of "our records" from a raw object total: state what matched the
  filter and what was excluded.
- Never paste credentials, tokens, or account identifiers into a report or a finding.
