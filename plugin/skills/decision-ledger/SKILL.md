---
name: decision-ledger
description: Use when doing the decision-ledger job or when a prove run asks you to work it. One dated line, then stop.
---

# Decision Ledger

Keep one append-only record of what was decided, so next week starts from the record instead of from memory. Do the job. Do not ping.

## When to use

Use when the operator asks for decision ledger, or when a prove run asks you to work this job.

## Required inputs and access

- The operator's question in their words.
- Network. `Accept: */*` on docs.x.ai (docs-verified).
- No new connector. Missing plugin → say so and stop.

## Sequence

1. Read the question once. Do not restate it.
2. Do the job on the computer or an existing plugin.
3. Return one dated line. Stop.

## Validation

A passing answer is one dated line a later run can compare. A greeting or a SKILL.md dump is a failed job.

## What to return

One dated line, nothing else.

## What requires approval

Read-only. Never send mail, never publish, never write a credential.
