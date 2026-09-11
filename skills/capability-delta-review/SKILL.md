---
name: capability-delta-review
description: Use when a Grok Bot capability, feature gate, or account entitlement appears to have changed, or before claiming a feature is unavailable. Turns a hashed gate diff into a decision, and refuses to report movement the vendor did not make.
---

# Capability delta review

Turn a change in the account's entitlement set into one of three honest answers: **adopt this**,
**ignore this and why**, or **this is not our capability at all**.

## Diff on ids, never on names

Gate names are recovered from string literals in the client bundle. Measured: a client upgrade
once "lost" 38 named capabilities that were all still enabled — the literals had simply left the
new bundle. A name-basis diff reports the vendor moving when only your decoding moved.

So: diff the hashed ids. Use names only to *describe* a row the id-diff already found.

## A capability you cannot name is usually not yours

The same backend evaluates gates for several products. Measured on one account: 1212 gates
enabled, 250 nameable from the Grok Bot client's own bundle. The other ~960 are other surfaces'
and cannot be invoked from here. When a gained gate has no name, the honest verdict is normally
"not a Grok Bot capability" — record the id and the watch condition (it acquires a name in a
future bundle) rather than inventing a meaning.

## Before you write "no read RPC exists"

Three server answers mean three different things and are constantly confused:

| answer | meaning |
|---|---|
| `400 invalid_argument` | the method **exists**; you called it wrong |
| `404 not_found` | the method **exists**; that resource does not |
| `404 Route POST:/… not found` | the method genuinely is not there |

Also check every service prefix, not just the obvious one. Measured: two capabilities were filed
as human tasks for days because a method was probed on one service and lived on another, and
because a method the shipped UI never calls was assumed not to exist.

## Read the whole object

When an endpoint answers, read every field it returned, not the one you came for. Measured: a
settings call was read for its `isEnabled` boolean four weeks running while the five approval
rules sat unread in the same dict — and the operator was asked to type them in by hand.

## Output

For each changed capability: the id, the name if honestly known, which of the three verdicts
applies, and — for "adopt" — the single smallest action that turns it on and how you will verify
it took effect.
