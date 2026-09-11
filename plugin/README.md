# grok-bot-surface-watch

Two skills that teach a Grok Bot to audit **its own deployment** every week and to report what it
could not measure instead of guessing.

| skill | what it does |
|---|---|
| `weekly-surface-watch` | a dated report of what the vendor changed about this account — client, capabilities, parameters, docs, plugin catalog — with "unmeasured" kept distinct from "unchanged" |
| `capability-delta-review` | turns a feature-gate change into adopt / ignore-and-why / not-our-capability, and refuses to report movement the vendor did not make |

## Why it exists

The interesting changes are invisible in the app. Entitlements flip server-side. Experiment
parameters are retuned with no client update. Docs pages are edited in place. An installed plugin
ships new code under the same name. None of that produces a notification.

These skills were extracted from a working private watch loop on a real Ultra account, where each
rule below cost a measured mistake before it was written down:

- **Diff hashed ids, never names.** A client upgrade once "lost" 38 capabilities that were all
  still enabled — the string literals had left the bundle, the entitlements had not.
- **A capability you cannot name is usually not yours.** 1212 gates enabled, 250 nameable from
  this client; the rest belong to other products on the same backend.
- **`400` proves a method exists.** `400 invalid_argument`, resource-`404`, and route-`404` are
  three different answers; collapsing them into "no API for this" turned two readable facts into
  human chores for days.
- **Read the whole object.** Five approval rules sat unread in a settings response that was being
  parsed for one boolean beside them.

## Install

Marketplace install once the catalog entry lands; until then, clone and point Grok Bot at the
`plugin/` directory via **Settings → Plugins**.

## License

MIT. No network access, no credentials, no writes: both skills are read-and-report only.
