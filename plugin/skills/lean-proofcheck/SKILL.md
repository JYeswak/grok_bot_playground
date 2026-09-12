---
name: lean-proofcheck
description: Run Lean proof-checks with pass/fail evidence. Use when asked to check a proof, verify two proofs side by side, or confirm a formal claim actually checks before anyone relies on it.
---

# Lean proofcheck

Run a Lean proof through the checker and report pass or fail with the evidence intact, the
chain from statement to result unbroken and inspectable. This skill exists because
"it typechecks" is a load-bearing claim that too often travels without the log that would
prove it, and because a proof whose statement, dependencies, and result cannot be traced
is indistinguishable from assertion. The local proof-checking capability this assumes is
mirror-backed (community-claim): a machine on the owner's side that runs the Lean
checker over a proof file and returns pass/fail plus diagnostics — never a vendor
endpoint, and no specific tool or API is assumed beyond "given a proof file, it reports
whether it checks".

## The rule that matters more than the result

**A verdict without a log is a rumor.** Every pass/fail traces to the exact proof text
checked, the dependencies it was checked against, and the checker output on the day of
the run — and anything unchecked, pinned to an old version, or run with extra assumptions
is labeled as such, never upgraded to "verified".

## When to use

- Asked to check whether a Lean proof passes, or to verify a formal claim before it is
  relied on.
- Asked to check two proofs (compare, or check each separately) with each proof's
  chain kept distinct.
- Asked what a proof actually establishes versus what its name or comment claims.
- Do not use for writing proofs from scratch, for auditing informal math, or for
  certifying anything beyond what the checker output shows — this skill runs checks
  and reports evidence only.

## Inputs and access

- **Proof file(s)** — one file for a single check, two files for two checks. Freeze
  each first: statement being proved, file version, and declared dependencies
  (imports, prior lemmas, sorry-free status claimed).
- **Checker context** — which checker and version ran, and the dependency set it ran
  against. A different version or a different dependency set is a different check.
- **Access** — the local checking capability plus whatever the Bot can already read.
  If a proof needs a dependency, a library, or a credential you were not given,
  record it as unreachable and move on. Do not improvise access. Name any
  configuration only as `${VAR}`-style variable names, never values.

## Sequence — what to do, in this order

1. **Freeze the proof** — statement, file version, declared dependencies, and whether
   `sorry` or placeholders are present. With two proofs, keep the blocks and
   everything downstream separate.
2. **Fix the context** — checker version and dependency set. Record both before
   running; a check with unknown context proves nothing.
3. **Run the check** — proof file through the checker in the fixed context. Record
   method (which version, which dependencies) and the raw verdict plus diagnostics.
4. **Validate the run** — check the validation section below before reporting a
   single verdict. A run that fails validation is a finding ("dependency missing",
   "checked against a different library version", "sorry present"), not a quiet
   gap to report around.
5. **Mark the gaps** — unchecked spans, assumed lemmas, and anything the verdict
   deliberately excludes. End with the single most consequential item per proof
   and what it would take to verify it further.

## Validation

- Checker output present for the claimed proof and context; a missing log fails,
  never silently narrows, the report.
- Verdict quoted against the raw output — no "basically passes", no warnings
  passed off as clean, no diagnostics trimmed to flatter the result.
- Context identity stated: checker version and dependency set. A check under a
  different context never reports with the confidence of the fixed one.
- Two-proof reports keep chains separate: no verdict from proof A cited under
  proof B, no shared claim without evidence on each side.
- Anything unchecked, assumed, or version-mismatched is labeled UNKNOWN with the
  reason, never smoothed over.

## Output

Per proof: a source block (statement, file version, dependencies, checker version,
run date), the raw verdict with diagnostics excerpt, and a gaps list. With two
proofs, deliver two chains plus a short comparison that cites each side
separately. End with the single most consequential point and the decision or
follow-up it needs — not a wall of logs.

## Boundaries

- Check-only plus reports. Never edit the proof to make it pass, upgrade
  dependencies, or publish a verification claim while checking.
- Approval boundary: this skill checks proofs for the owner to use. Certifying a
  result, shipping code on the strength of a check, or asserting correctness
  publicly belongs to a routine with its own approval — nothing here certifies.
- Never present community prover lore (which tactics work, which proofs "always
  check") as vendor truth. State what checked today, for this file, in this
  context.
- Never paste credentials, tokens, or account identifiers into a proof, report,
  or finding. If a check needs access you lack, record it as unreachable and
  move on.
