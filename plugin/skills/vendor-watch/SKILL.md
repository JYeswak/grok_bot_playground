---
name: vendor-watch
description: Use when asked what the vendor changed, on a weekly vendor-watch routine, or when proving the vendor-watch job. Fetch the vendor's own pages and report only what moved.
---

# Vendor watch

Read the vendor's own pages. Report only what changed. Do the job. Do not ping.

## When to use

Use when the operator asks what moved on docs.x.ai / changelog / limits, or when a prove run asks you to work the vendor-watch job.

## Required inputs and access

- The URL list (default: `https://docs.x.ai/llms.txt` and the `grok-bot/` pages it names).
- Network. `Accept: */*` on docs.x.ai (docs-verified).
- Last week's text, if you have it. If you do not, say so — a first run cannot produce a diff.

## Sequence

1. Fetch the named pages. Do not paraphrase a page you did not fetch.
2. Quote the vendor. A grok-bot path from `llms.txt` is quoted verbatim.
3. If you have last week's text, report new / gone / what it changes. If you do not, say `first run` and quote what is there now.
4. If nothing moved, one line: `no change`.
5. If a page will not load, name the page and say it did not load. That is not "no news".

## Validation

A passing answer quotes a string that is on a page fetched this run. A greeting, a guessed path, a capability inferred from a screenshot, or repeating the operator's instructions / a SKILL.md dump is a failed job.

## What to return

Exactly two lines, nothing else:

1. one `https://docs.x.ai/grok-bot/...` URL copied from `llms.txt`
2. `first run` or `no change` or what moved



## What requires approval

Read-only. Never change a setting, never install a plugin, never publish, never invent a vendor capability.
