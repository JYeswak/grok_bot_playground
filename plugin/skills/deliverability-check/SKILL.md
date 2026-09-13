---
name: deliverability-check
description: Check outbound email deliverability before any send: inbox warmup state, bounce and spam-trap hygiene, cap-aware queue sizing. Use when asked if mail will land, before a cold sequence, or when bounces spike. Read-only audit, never sends.
---

# Deliverability check

Audit whether an outbound batch is safe to send, and say plainly when it is not.

## The rule that matters more than the audit

**A sendable list beats a big list.** Inbox warmup, bounce history, and spam-trap exposure decide landing more than copy does. This skill never sends, warms, or cleans anything — it reads and verdicts. A FAIL here stops the send; override needs an explicit human yes on the exact batch.

## What to check, in this order

1. **Warmup state** — sending identity age, volume ramp, recent complaint rate. A cold identity with a big batch is the top failure mode.
2. **List hygiene** — bounce rate on recent sends, hard vs soft split, known trap patterns (role addresses, aged imports, scraped domains).
3. **Queue caps** — per-day and per-domain caps against the planned batch size; burst pacing vs steady pacing.
4. **Authentication posture** — SPF/DKIM/DMARC presence for the sending domain, stated as present/missing/unchecked (unchecked when DNS is unreadable — never assumed).

## Output

Per-check PASS/FAIL/WARN with the number behind it, then one verdict: SEND, SEND-CAPPED (with the cap), or HOLD (with the blocker named first). Every row carries its source and date.

## Boundaries

- Read-only audit. Never send, schedule, warm, clean, or modify any list, domain record, or campaign.
- Never invent rates: a metric without a source is recorded as unmeasured, and unmeasured fails closed to HOLD.
- No credentials, tokens, or account identifiers in outputs or findings.
