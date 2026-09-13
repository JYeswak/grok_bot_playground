---
name: churn-watch
description: Score an account set for churn risk with per-account evidence links. Use when asked which accounts are at risk, to review account health, or to rank a book of business by risk. Read-only — never contacts accounts, changes plans, or files tickets.
---

# Churn watch

Turn an account set into a ranked risk list where every score carries its
per-account evidence links. This skill exists because risk hides across surfaces:
usage drops, support load spikes, and billing friction each look benign alone —
together, with links, they name which accounts need attention first.

Use this skill for risk scoring only: account signals in, ranked evidence-backed
list out, nothing acted upon.

## The rule that matters more than the list

**A score without evidence is a rumor.** Every risk score carries the linked
signals that produced it — which usage window, which ticket, which invoice — with
dates. An account without readable signals is UNSCORED, not healthy: "unmeasured"
and "healthy" are different answers and must never be merged. A list that calls
an unreadable account healthy spends the reader's trust.

## When to use

- Someone asks which accounts are at risk, for a churn review, or to rank a book
  of business by risk.
- Usage, support, and billing signals need combining into one per-account view.
- A renewal or check-in needs its evidence assembled first.
- Do not use for contacting accounts, changing plans, filing tickets, or
  predicting exact churn dates — this skill scores and evidences risk, it does
  not intervene or forecast.

## Inputs and access

1. **Account set** — the required input: the accounts to score, named by stable
   id. One set per run; state which set was scored.
2. **Usage signals** — per account: active-use trend over a stated window,
   feature breadth, last-active date. Record the window: scores without windows
   cannot be compared.
3. **Support signals** — per account: open and recent ticket counts, severity,
   escalation flags, each with ticket links and dates.
4. **Billing signals** — per account: overdue status, failed-charge flags,
   plan-change history, each with invoice links and dates.
5. **Credentials** — usage, support, and billing integrations are read through
   configured variables (for example `${USAGE_API_KEY}`, `${SUPPORT_API_KEY}`,
   `${BILLING_API_KEY}`). Never paste values into a list or a finding; names
   only. If a surface needs access you were not given, record those accounts as
   UNSCORED for that surface and move on. Do not improvise access.

## Sequence

1. **Resolve** — confirm the account set and pull all three signal surfaces per
   account, with links and dates. Note which surface each signal came from.
2. **Score** — combine signals per account into low, watch, or at-risk tiers.
   Rank by consequence (revenue at stake times signal agreement), not by any
   single surface's volume.
3. **Evidence** — attach the supporting links and dates to every score. Cut
   anything you cannot anchor, or demote it to an UNEVIDENCED LEAD.
4. **List** — write the risk list (see Output). Keep tiers small: the list fits
   one screen of ranked accounts, not an exhaustive dump.
5. **Verify** — re-check every link, date, and tier before finishing. A link
   that points at the wrong account is worse than no link.

## Validation

- Every scored account has at least one linked signal with a date inside the
  stated window; scores built only on stale signals are flagged as stale.
- Unreadable accounts are marked UNSCORED with the unreachable surface named —
  never defaulted to healthy.
- Tiers reflect combined evidence; no single noisy surface promotes an account
  alone without corroboration.
- Community patterns (benchmark churn rates, listicle "top churn signals") are
  community-claim: never present them as vendor or product truth about these
  accounts.

## Output

A dated markdown risk list with:

- Header: account set scored, signal windows used, which surfaces were read and
  which were unreachable.
- **At-risk**: accounts needing action now, each with tier, evidence links +
  dates, and the single sharpest signal.
- **Watch**: accounts trending wrong, each with evidence links + dates.
- **Healthy**: accounts with fresh supporting signals, compressed to counts.
- **Unscored**: accounts unreadable on one or more surfaces, with what would
  settle them.
- Two account sets the shape was built for: a small high-touch set (few
  accounts, deep per-account evidence) and a large low-touch set (many accounts,
  tier counts dominate with links on the top risks only).

## Boundaries

- Approval boundary: read-only. This skill never contacts accounts, changes
  plans, issues credits, files tickets, or edits account records. There is
  nothing here that intervenes — no draft-contact step exists, so no outreach
  can be authorized from this skill. Any intervention belongs to a separate
  routine with its own approval.
- Never paste credentials, tokens, or account identifiers beyond the stable ids
  the reader already uses into a list or a finding.
- Never report account contents beyond what the reader can already open. If an
  account needs access you cannot read, record it as UNSCORED and move on.
