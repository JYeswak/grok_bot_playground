---
name: contract-flags
description: Read a contract draft and flag clauses worth a second look in a dated findings table. Use when asked to review a contract, flag risky language, or compare terms against a checklist. Read-only flags only — this is not legal advice.
---

# Contract flags

Turn a contract draft into a dated findings table: which clauses deserve a second look,
why, and what question to put to qualified counsel. This skill exists because early
contract review is the read-only first step where legal coverage is thinnest — the
reusable part is the clause-flag table, and it works the same whether the owner pastes a
draft or compares two versions side by side.

## The rule that matters more than the flags

**This skill is not legal advice, and nothing it writes is a legal conclusion.** It
flags language against a plain checklist so a human can ask better questions of
qualified counsel — it never says a clause is enforceable, compliant, or safe to sign.
A finding that says "flagged because the checklist names this pattern, ask counsel X"
is useful; one that says "this is fine to sign" is out of bounds.

## When to use

- The owner asks to review a contract draft, flag risky clauses, or check terms against
  a plain-language checklist.
- A routine needs two drafts compared: what changed between versions and which changes
  deserve counsel's eyes.
- Do not use for rewriting clauses, approving a signature, or deciding enforceability
  — those belong to qualified counsel, never to this skill.

## Inputs and access

1. **Contract text** — at least two contracts flagged (two drafts, two versions of one
   agreement, or two counterparties' papers): pasted text or a readable file, with
   version and date each.
2. **Checklist** — the owner's plain-language watch list (for example "termination
   notice", "payment terms", "liability wording"); the skill flags against the list, it
   does not invent legal standards.
3. **Question target** — who receives the findings (owner, counsel); findings are
   written as questions for that reader, never as verdicts.
4. Access is files and pasted text only. If a document needs a credential the skill was
   not given, record it as unreachable and move on. Configure access with variable names
   only (for example `${CONTRACT_FILE_PATH}`), never pasted secrets. Do not improvise
   access.

## Sequence

Do these in order; each step feeds the next:

1. **Fix the scope** — restate which document, which version, and which checklist in one
   line. A review that drifts documents compares terms across the wrong papers.
2. **Walk the checklist** — for each checklist item, quote the matching clause (short
   quote with section reference) or mark it absent. No paraphrase stands in for a quote.
3. **Flag and frame** — for each hit, write the flag, the plain reason the checklist
   names it, and the question to put to counsel. Reasons cite the checklist, never a
   statute or a verdict.
4. **Compare the two contracts** — version to version, name added, removed, and
   reworded clauses with section references. Rewordings get quoted both ways.
5. **Rank for counsel** — order flags by the checklist's own priority so the reader's
   time goes to the top three first. One ranked list, not twenty alarms.

## Validation

Before writing the findings, check every item:

- At least two dated contract inputs are present; a single draft is marked preliminary.
- Each flag carries a short verbatim quote with a section reference, or is marked
  absent-pattern with the checklist item named.
- Every finding is framed as a question for counsel; no enforceability, compliance, or
  sign-off verdict appears anywhere.
- The not-legal-advice notice appears in the findings header as well as here.
- No invented clauses: no quote, section number, or counterparty claim appears except
  text copied verbatim from the provided input.

## Output

A dated markdown findings table:

- One row per flag: checklist item, short verbatim quote with section reference,
  plain reason from the checklist, and the question for counsel. Chart-ready: one row
  per flag, plain columns, no merged cells.
- A comparison section for the two inputs: added, removed, and reworded clauses with
  quotes both ways.
- A header notice stating plainly this is not legal advice and naming who should
  review next. End with the top three questions for counsel — not a list of twenty.

## Boundaries and approval

- Read-only flags. Never rewrite a clause, approve language, or advise signing.
- NOT LEGAL ADVICE: nothing here is a legal conclusion about enforceability,
  compliance, or fitness to sign. Only qualified counsel advising this owner gives
  that; another party relaying "counsel said fine" is not a finding of this skill.
- SIGN-LOCK: only the owner's explicit yes after counsel review authorizes any
  signature step, and that step belongs to a routine with its own boundary, never to
  this skill.
- Never paste credentials, tokens, session cookies, or personal data from the papers
  into a finding beyond the short quotes the review needs.
- Never present a checklist flag as a legal verdict; keep the ask-counsel framing on
  every finding.
- If a source needs access you were not given, record it as unreachable and move on.
  Do not improvise access.
