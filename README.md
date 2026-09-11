# grok_bot_playground

A place to build Grok Bots properly: **skills for the things the catalog has no plugin for**,
**Bot templates you can deploy in one command**, and a CLI that verifies the manual steps
actually took instead of trusting a tick-box.

Install it as a Cursor/Grok Bot plugin, or clone it and deploy templates with `gbx`. Both work;
they solve different halves.

---

## Why these things and not others

Measured 2026-09-11 against the live plugin catalog (322 entries), a corpus of 645 attributed
community Bot definitions (294 distinct integrations), and the curated Bot marketplace (71
listings):

| gap | measurement | what is here |
|---|---|---|
| math / statistics | **0** of 322 plugins | `experiment-readout` skill |
| OCR / document extraction | **0** of 322 plugins | `doc-extract` skill |
| auditing your own deployment | **0** of 322 plugins | `weekly-surface-watch`, `capability-delta-review` |
| Productivity templates | 146 Bots built by the community, **0** curated listings | `daily-brief` template |

**263 of the 322 catalog plugins are SaaS connectors.** The catalog sells *access*. Almost
nothing sells *ability* — and ability is the half that needs no hosted service, because every
Bot already has a Linux computer that almost nobody uses for compute.

## Install as a plugin

**Settings → Plugins → Add**, pointed at this repo. Then run the setup command, which checks
rather than assumes:

```
/gap-kit-setup
```

It confirms the skills loaded, proves the Bot's computer can actually run `python3` before you
trust any number it produces, and ends by asking the Bot to *refuse* something — because a skill
that is installed but not in context will happily answer anyway.

## Deploy a Bot from a template

```bash
git clone https://github.com/JYeswak/grok_bot_playground && cd grok_bot_playground
./bin/gbx list                       # what is here, and which gap each fills
./bin/gbx show experiment-readout    # the charter and the manual steps, before you commit
export GBX_SEED_SHARE_ID=<your own template's share id>
./bin/gbx new experiment-readout --deploy
./bin/gbx doctor                     # did it actually land
```

`GBX_SEED_SHARE_ID` is a template **you** own — any Bot → *Share as template* → copy the id.
Seeding from your own template matters: a Bot created from someone else's marketplace template
inherits that author's terms and lineage.

## What "one command" actually covers

Measured against the API, not assumed:

| step | automated |
|---|---|
| create a Bot with a durable identity | **yes** — `CreateGrokBotAgentFromTemplate` |
| set its name, title, charter | **yes** — `UpdateGrokBotAgent` |
| attach skills | **yes** — they travel with the template |
| install a connector | **no RPC exists** — printed, then verified by `doctor` |
| create a routine | automations are read-only over the API — same |
| store a key | deliberately manual (see below) |

`gbx doctor` re-reads your roster, plugin installs and routines, and reports which manual steps
landed. "I did that" is a claim about your own account, and a claim about your own account is
checkable.

## Keys

This repo never stores, prints or transmits a key.

- **For a plugin**, use the platform's own mechanism: declare the *name* under `variables` in
  the manifest, set the value in the dashboard, and reference it as `${VAR}`. The Cursor plugin
  reference is explicit that secret values do not belong in a plugin repo.
- **For a Bot**, use the secret card in Settings. Never paste a key into a chat message — it
  survives in the transcript, the local replica, and any share link made from that Bot.
- `gbx secret set NAME` prints the two commands to keep a value in your OS keychain instead.

`gbx` reads your Grok Bot desktop session rather than asking for a token: macOS will show a
keychain prompt the first time, which is exactly the consent step it should be.

## Templates

| template | job | connectors |
|---|---|---|
| `experiment-readout` | turns a result into effect / interval / assumptions / verdict, computed on the Bot's machine and shown as code | none |
| `pdf-extract` | pulls tables, totals and dates out of documents with page-level provenance, and emits `null` rather than a plausible guess | none |
| `daily-brief` | one short morning brief from calendar and inbox: today, waiting on you, slipped, most likely to go wrong | Gmail, Google Calendar |

Every template states its approval boundary in its charter. Two of the three touch nothing
outside the Bot's own computer.

## Contributing a template

```
templates/<name>/
  bot.yaml                  identity, job, approval boundary, gap evidence, manual steps
  skills/<skill>/SKILL.md   frontmatter with `name` and a description that says WHEN to use it
```

```bash
./bin/gbx validate      # schema + routing checks
./bin/gbx-selftest      # proves those checks fire on a deliberately broken template
```

Two rules the validator enforces, because both were learned the hard way:

1. **A charter under 200 characters, or one with no stated approval boundary, is rejected.** A
   Bot's description is not documentation — it is the behaviour.
2. **A skill description must say when to use it.** A skill nobody can route to is a skill
   nobody runs.

## License

MIT. No telemetry. The only network calls are to the Grok Bot API you are already signed in to.
