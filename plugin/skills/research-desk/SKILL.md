---
name: research-desk
description: Use when asked a hard question that needs sources, or when proving the research-desk job. One question, one sentence, three quoted sources, then the falsifier.
---

# Research desk

Answer one hard question properly. Do the job. Do not ping.

## When to use

Use when the operator asks a question that is not already answered in this thread, or when a prove run asks you to work the research-desk job.

## Required inputs and access

- The question, in the operator's words.
- Network to vendor docs (`docs.x.ai`, `Accept: */*` — docs-verified: a `text/markdown` Accept returns 404).
- No connectors.

## Sequence

1. Read the question once. Do not restate it.
2. Fetch the vendor page that should answer it. Prefer `docs.x.ai/grok-bot/*`.
3. Return one sentence that answers it.
4. Then three sources, each with a quoted line you actually read.
5. Then the one thing that, if false, would change the answer.
6. If you cannot find it, say `not found` and list every URL you opened. Never invent a quote.

## Validation

A passing answer is either:

- one sentence + three quoted sources + a falsifier, and each quote appears on a page you fetched, or
- `not found` plus the URL list.

A greeting, a token echo, or a source you did not open is a failed job.

## What to return

The answer in the shape above. No preamble.

## What requires approval

Never send mail, never create a routine, never write a credential, never publish. This skill is read-and-report only.
