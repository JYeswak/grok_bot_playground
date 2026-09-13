---
name: galaxy-engineering
description: Use when doing the galaxy-engineering job or when a prove run asks you to work it. One dated line, then stop.
---

# Galaxy Engineering

Post one dated list of the pull requests waiting on a human, and touch none of them. Do the job. Do not ping.

## When to use

Use when the operator asks for galaxy engineering, or when a prove run asks you to work this job.

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
