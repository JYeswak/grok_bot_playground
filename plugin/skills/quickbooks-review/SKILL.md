---
name: quickbooks-review
description: Run a read-only QuickBooks books review surfacing discrepancies with report links for a grokbot account. Use when asked to review the books, reconcile accounts, or check invoices and expenses — never to change anything without explicit approval.
---

# QuickBooks review

Review the books read-only — accounts, invoices, expenses — and report what
does not reconcile, with report links and as-of dates. Zero writes.

This skill exists because books drift silently: uncategorized expenses,
duplicate invoices, and stale reconciliations compound until a review catches
them, and a review that also edits the books destroys the evidence trail it
was meant to create.

## The rule that matters more than the report

**Review and write are different jobs and must never share a session.** This
skill reads, compares, and reports. It never creates, edits, voids, deletes,
or reconciles anything. Any change the review suggests becomes a written
recommendation the operator approves with an explicit yes — and the change
itself happens outside this skill.

## When to use

- A teammate asks "review the books" or "what does not reconcile".
- A month-end routine checks invoices, expenses, and account balances for a
  grokbot account.
- A discrepancy needs a before/after read across two reviews, not a single
  snapshot.
- Do not use for tax advice, payroll changes, or anything that edits the
  books. Community bookkeeping tips are community-claim — only this account's
  own QuickBooks numbers are evidence.

## Inputs and access

- The company file or account scope under review, named exactly.
- Two reviews minimum: each names the reports pulled (profit and loss,
  balance sheet, transaction lists), their as-of dates, and the report links.
  Zero writes across both — a write during review invalidates the review.
- Access is read-only through the QuickBooks reports or exports the operator
  provides. Credentials arrive per session as `${VAR}` names only, never as
  values. If access is missing, record the review as unreachable and stop —
  do not improvise credentials.

## Sequence

1. **Fix the scope** — record the company, the accounts in scope, and the
   as-of date before pulling anything. A review without a scope is not
   comparable to the next one.
2. **Run review one** — pull the reports, record balances, and flag
   uncategorized, duplicate, or stale items with report links.
3. **Run review two** — a second pass over a different report set or period
   that independently confirms or questions review one's flags.
4. **Join on stable keys** — match items on transaction id or account id,
   never on amounts or memo text. Amounts collide; ids identify.
5. **Write recommendations, not changes** — each flag becomes a written
   recommendation with the evidence behind it, queued for the operator's
   explicit yes. Nothing is applied here.
6. **Sanity-check before reporting** — an account-wide balance collapse
   usually means a wrong as-of date, a wrong company file, or a broken
   export, not a real loss. Verify scope and dates before calling it a
   finding.

## Validation

- Both reviews ran with recorded reports and as-of dates; an empty pull is a
  failed input, not a clean book.
- Zero writes occurred during the review. Any write invalidates the session
  and is disclosed, not hidden.
- Every flag carries its report link and as-of date.
- Spot-check the two largest flags against the QuickBooks UI or a re-pull
  before treating them as real.

## Output

A dated markdown report with: scope and as-of dates; flags with report links
(one row per discrepancy: account, amount, why it flagged); recommendations
queued for approval, each needing an explicit yes; items that could not be
reviewed and why. End with the single most consequential flag and the decision
it needs — not a list of fifty.

## Boundaries

- Read-only review with an explicit-yes gate on any write. Never create,
  edit, void, delete, reconcile, or reclassify anything while reviewing. Any
  write or configuration change needs the operator's explicit yes first,
  stated per change — silence is not approval.
- Never present community bookkeeping advice as books findings. Community
  claims are community-claim; only this account's pulled numbers are evidence.
- If a report requires access you were not given, record it as unreachable
  and move on. Do not improvise access.
- No secrets, tokens, or account identifiers are stored, logged, or pasted
  into reports. Access is provided per session by the operator as `${VAR}`
  names only, never embedded as values in files.
