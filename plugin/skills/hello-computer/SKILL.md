---
name: hello-computer
description: Use when doing the hello-computer job or when a prove run asks you to work it. One dated line, then stop.
---

# Hello Computer

Run one command on the cloud computer and paste its raw output, so the machine is proven alive before any plugin is connected. Do the job. Do not ping.

## When to use

Use when the operator asks for hello computer, or when a prove run asks you to work this job.

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
