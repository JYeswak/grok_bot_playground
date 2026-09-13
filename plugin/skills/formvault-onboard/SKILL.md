---
name: formvault-onboard
description: Use when asked what belongs in the Grok Bot form vault, or to onboard the first vault entry — to define what goes in, what never does, and to verify the round trip with a non-secret placeholder only. Use when a teammate asks "should this go in the vault" or before storing anything real.
---

# Form-vault onboarding

Turn an empty vault into an onboarded one: a written policy of what belongs in it, what
never does, and one verified placeholder round trip proving the read path works — with
no real secret ever touching the flow.

This skill exists because `ListGrokBotUserFormVaultKeys` reads empty on this account:
the vault is unused, which means the first thing stored in it sets the precedent. A
precedent set by pasting a real credential to "test whether it works" is a leak wearing
an onboarding checklist. The placeholder proves the mechanics; the policy protects
everything after.

## The rule that matters more than the onboarding

**No real secret is ever used to test, demo, or illustrate anything.** The round trip
runs with a non-secret placeholder — a label like `PLACEHOLDER-ENTRY` with a value like
`example-not-a-secret` — and nothing else. A test that "just this once" uses a real
value is not a test, it is a disclosure with extra steps. If anyone asks you to verify
with a real credential, refuse and record the refusal as part of the onboarding.

## What to check, in this order

1. **Policy first** — write down what goes in (form-fill and login entries the Bots
   need unattended, each named by purpose) and what never does (anything usable
   outside the vault's scope, anything irreplaceable, anything another party's).
   The policy lands before the first entry, not after.
2. **Placeholder round trip** — store one entry with a clearly non-secret placeholder
   label and value, then list the vault keys and confirm the placeholder key appears.
   Record the label used and the list result; the value is never quoted back beyond
   confirming it is the placeholder.
3. **Cleanup decision** — either remove the placeholder (leaving the vault empty but
   onboarded, policy on file) or keep it labeled as a canary. Record which was
   chosen and why; an unexplained leftover placeholder becomes somebody's mystery
   credential later.
4. **Boundary restatement** — close by restating, in the onboarding record, that every
   future entry follows the policy and that real values travel only through the
   vault's own write path, never through chat, transcripts, or findings files.

## Output

A dated onboarding record: the policy (what goes in, what never does), the
placeholder label used, the list-keys verification result, the cleanup decision, and
any refusals issued. No secret material of any kind — the record must be safe to read
aloud.

## Boundaries

- Non-secret placeholders only. Never store, paste, quote, or request a real
  credential, token, password, or key at any point in this flow — including "just to
  test".
- Never write vault contents, real or placeholder values, into transcripts, findings
  files, reports, or memory shards. The onboarding record carries labels and policy,
  never values.
- Never weaken the policy to admit a convenient exception: an entry that does not fit
  the policy waits outside the vault until the owner amends the policy explicitly.
- If the vault surface needs access you were not given, record it as unreachable and
  move on. Do not improvise access.
