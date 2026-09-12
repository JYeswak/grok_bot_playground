# AGENTS.md

You are an agent in a clone of `grok_bot_playground`. Read this file before your first tool
call. It is short on purpose. When it and any other file disagree, run the command and believe
the command.

## What this repo is, and is not

`gb` **measures** a Grok Bot deployment from artifacts on the local disk and **proposes** Bots as
text a human pastes. It does not drive the Grok Bot app. It cannot sign in, cannot send as the
user, and never asks for a credential.

This clone ships the tooling, not the operator's account. `gb corpus`, `gb demand`, `gb findings`
and `gb x` need a collected corpus that is deliberately withheld, so **they refuse here and that
is correct** — a refusal naming the missing input, not a crash. Do not treat their exit codes as
breakage and do not try to synthesise the corpus.

## Start here, in this order

```sh
gb capabilities --json    # the contract: every verb, its exit codes, what it needs
gb platform --json        # SUPPORTED | UNVERIFIED | UNSUPPORTED for this OS
gb mirror                 # reads the desktop client's own state. No token, no network
gb templates              # the shelf: job, charter length, cadence
gb walk bots --paste <id> # one charter, ready to paste. Not the whole catalog
gb setup --persona <id>   # prints a PLAN; changes nothing without --apply
```

`gb capabilities --json` is the only surface you should route from. Do not hardcode a verb list
and do not read `bin/`.

**Do not open with `gb triage`.** `gb robot-docs` still tells you to, and on a clone with no
artifacts it has been measured taking seconds or hanging; it judges a *configured* deployment.
Reach it after `gb setup --apply`, not before.

## Exit codes mean something

`0` ok · `1` findings · `2` usage · `3` environment (a real thing is missing; the message names
it) · `4` upstream · `5` refused · `130` cancelled. **Exit 3 is usually the tool being honest,
not failing.** Read the sentence before you retry.

## Steps no API will do — stop trying to automate these

| Step | Automated today | Who does it |
|---|---|---|
| Install the Grok Bot desktop app | no | human |
| Sign in / link the subscription | no | human |
| Create a Bot from a template | yes, with a live desktop session (`gb templates deploy`) | agent |
| Paste a charter | yes as text; a human clicks **New Bot** without a session | either |
| Connect Gmail / Notion / GitHub | **no** | human, in Settings -> Plugins |
| 2FA or an account takeover prompt | **no** | human, on the Agent Computer |
| Create a routine | not over the API | human, or the Bot itself when asked in chat |
| Enable a plugin for one Bot | **no** — installed is not enabled | human |
| Verify it actually ran | yes | agent (`verify` line, then `gb triage`) |

Two of those are measured facts in this tree, not guesses. `gb templates deploy` maps exactly
five fields — name, title, avatar shape, avatar colour, description — and **drops the routine**,
which is the most valuable thing a template encodes. The way a routine gets created is a chat
message asking the Bot to schedule itself; that path is proven live in this repo's history at
`0d60a49`. So "deployed" never means "scheduled", and an agent that reports a template deployed
has not delivered a working Bot.

## Platform reality

macOS on Apple silicon and Intel is what this repo measures. Linux and Windows report
`UNVERIFIED`, and credential read and scheduler install are `NOT_IMPLEMENTED` there. `gb setup
--apply` on Linux walks a path nobody here has measured — run `gb platform --json` first and
believe it.

## Four products share a name and do not share config

Getting this wrong is the most common first mistake. Grok Bot runs on a **persistent cloud VM**,
so a server on your `localhost` is not reachable from it; its MCP/plugin surface is the Cursor
marketplace and the in-app Plugins panel. Cursor IDE, grok.com connectors, and the `grok` CLI are
three other surfaces with their own config. If a plan depends on a local stdio MCP server, it can
work in Cursor IDE and will not work in Grok Bot. Vendor documentation for the boundary lives at
`docs.x.ai/grok-bot/computer-and-apps` and `docs.x.ai/grok-bot/private-networks`; reachability
for a private service is Team Setup, not localhost.

One more consequence of that VM, from the vendor's own docs: **one computer per user, shared by
every Bot on the account** — files, browser sessions and CLI credentials are shared. Isolation is
per user, not per Bot. Never write a plan that assumes one Bot cannot see another's logins.

## Where the payload is

`plugin/` is the drop for Grok Bot and Cursor: skills under `plugin/skills/`, commands under
`plugin/commands/`. `templates/` holds the pasteable charters and `personas/` the packs that
group them. `library/` prints the Settings -> Plugins path for an integration and re-reads
whether it landed.

## House rules for working in this repo

- Never claim a number you did not just measure. Every count in the README is derived by
  `gb readme --write`; hand-editing it is what `gb gatesdoc` exists to refuse.
- An empty result is not evidence of absence until a positive control proves the probe looked in
  the right place.
- Prefer `--json` and route from `gb capabilities --json`.
- `gb robot-docs` is the long-form agent handbook. This file is the part you must read first.
