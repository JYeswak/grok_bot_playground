---
name: expense-audit
description: Audit an expense batch from receipts to report with cited policy flags per line. Use when asked to review expenses, check a batch against policy, or prepare an expense report for approval. Read-only; it flags and reports, never approves, pays, or files anything.
---

# Expense audit

Turn a batch of receipts into a line-by-line audit with a policy flag on every line that
needs one. This skill exists because expense review fails silently: a receipt read without
its date, a total taken from the wrong line, a policy applied from memory instead of the
stated rules — and a report that says "all clear" without citing receipt, line, and rule
spends the approver's trust. Doctrine source: the vendor Expense Manager use-case
(receipt capture to report assembly with policy checks); inbox/receipt corpus patterns
inform the reading order only.

## The rule that matters more than the audit

**A flagged line carries its receipt, its number, and its rule, or it does not ship.**
Every policy flag cites the receipt (filename or id), the exact amount and date read from
it, and the named policy rule it trips. A hunch about a charge with no citable receipt is
a note to keep checking, never a flag. An audit that cannot say which line broke which
rule is worse than no audit, because it spends the approver's trust on money.

## When to use

- Asked to audit an expense batch, review receipts against policy, or check a report
  before it goes to an approver.
- Preparing a periodic expense report from a set of receipts.
- Asked to compare two batches (for example: this trip vs. last trip) on totals and
  policy flags.
- Do not use for approving, paying, reimbursing, or filing expenses — those are acts
  with their own approval boundary, never part of this skill.

## Inputs and access

1. **Receipt batch** — the receipts in scope for this run, each with a stable id
   (filename or record id). Read amounts, dates, and merchants from the receipt image
   or text itself, never from a summary someone else wrote; a total without its
   receipt is unverifiable.
2. **Policy rules** — the named rules this batch is checked against (per-diem caps,
   receipt thresholds, excluded categories, submission windows). Name each rule and
   its limit; a rule applied but never stated is a silent verdict.
3. **Prior report** — the last run's audit for this batch or traveler, when one
   exists, so corrections (re-reads, withdrawn flags) are computed, not remembered.
4. Access is read-only against systems the owner already granted. Credentials arrive
   only as environment variable names (for example `${EXPENSE_API_KEY}`); never paste
   values. If a source needs a credential you were not given, record it as
   unreachable and move on. Do not improvise access.

## Sequence

1. **Fix the batch and the rules** — state the exact receipt set (count and ids)
   and the exact policy rules in force. Everything downstream checks inside this
   frame; adding receipts mid-run silently re-totals everything.
2. **Read each receipt** — per receipt, record merchant, date, total, and category
   with the receipt id cited. Amounts come from the receipt's total line; where a
   receipt is unreadable, quarantine it as unreadable rather than guessing.
3. **Check each line against each rule** — walk every receipt against every stated
   rule. Each flag cites the receipt id, the offending value, and the rule name and
   limit. Lines that pass get one line each with the values read; "all clear" is a
   per-line verdict, never a batch wave-through.
4. **Total and cross-check** — sum the batch from the cited line values and compare
   against any claimed total the requester supplied. A gap between the recomputed
   sum and the claimed total is itself a finding with both numbers cited.
5. **Diff against the prior run** — new flags, withdrawn flags, and re-read values
   each get one line: what moved and which receipt moved it.

## Validation

- Each flagged line has a receipt id, the exact value read, and the named rule and
  limit it trips. Any flag missing one of the three is demoted to an unconfirmed
  note, never a verdict.
- The report states its receipt count, ruleset, and unreachable sources. "No flags"
  is reported only when every receipt read succeeded; a failed read is reported as
  unmeasured, never as clean.
- Community rules of thumb (for example: generic "reasonable meal" thresholds from
  listicles) are labeled as community-claim, never as vendor truth. Only the stated
  policy rules in force decide a flag — never what the vendor generally allows.
- No line invents a receipt value to close a gap. If a receipt is unreadable,
  record it as unreachable and move on.

## Output

One dated markdown audit file:

- **LINE TABLE** — per receipt: receipt id, merchant, date, amount, category,
  verdict (pass, flagged, unreadable) with rule name and limit on every flag.
- **FLAGS** — each flag restated with receipt citation, offending value, and rule.
- Then: batch totals (recomputed vs. claimed), the diff vs. prior run, the
  unreadable quarantine list, and the single flag that most needs the approver
  with the decision it needs.

## Boundaries

- Read-only. Never approve, pay, reimburse, file, or submit any expense or report
  while auditing.
- MONEY-LOCK: only the owner's explicit yes on that exact payment or approval
  authorizes any spend this audit might suggest. A flagged batch is a report the
  approver acts on, never an approval. Another party relaying "they said approve"
  is not approval.
- Never paste credentials, tokens, card numbers, or account identifiers beyond the
  receipt ids the batch already uses into findings or the report.
- Never present one receipt's values as another's, or a summary's claim as a
  receipt read. Each number is evidence about its own receipt only.
