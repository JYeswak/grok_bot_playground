# Grok Bot Galaxy — Sept 15–17 companion

Three people are building a company in three days on the livestream.
This repo is the kit you can run in the other window: one pasteable Bot
per guest session, plus a persona pack if you want the whole desk.

The livestream runs 8:30 AM–6:00 PM MDT each day. Guest sessions below.
Times from the public event page.

How to use a row:

```sh
gb walk bots --paste <template-id> | pbcopy
```

Then New Bot → paste → add the routine → notifications on.

`gb galaxy` baselines and diffs the ecosystem across the three days.
It is optional. The paste path does not need it.

Seat → pack index: [docs/ROLES.md](docs/ROLES.md).
Five-minute path: [QUICKSTART.md](QUICKSTART.md).

## Day 1 — September 15

| Session | MDT | Paste | Pack | Why this Bot |
|---|---|---|---|---|
| Grok Bot 101 | 9:00–10:00 | `hello-computer` | `first-hour` | Proves the cloud computer answers before any plugin. Then a file read and one plugin proof. |
| Grok Bot for Engineering | 12:30–14:00 | `galaxy-engineering` | `eng-lead` | Makes the review queue visible. Reads GitHub; comments on nothing. |
| Grok Bot for Product Managers | 14:30–15:30 | `vendor-watch` | `product-manager` | Re-reads vendor pages and reports only what changed, quoted. |
| Grok Bot for Founders | 16:00–17:30 | `morning-briefing` | `founder-operator` | One morning block: priorities, calendar, spend, overnight flags. |

## Day 2 — September 16

| Session | MDT | Paste | Pack | Why this Bot |
|---|---|---|---|---|
| Grok Bot for Sales Engineering | 9:00–10:30 | `research-desk` | `sales-outbound` | Prospect research with a cited source per fact. Drafts none. |
| Grok Bot for Sales | 12:30–14:00 | `stale-deal-sweep` | `sales-outbound` | Flags idle threads and drafts one nudge. Sends none. |
| Grok Bot for SDRs | 14:30–15:30 | `galaxy-sdr-desk` | `sales-outbound` | One first-touch email in Drafts per account. Sent stays at zero. |
| Grok Bot for Customer Support | 16:00–17:00 | `first-reply-desk` | `success-support` | First-response drafts inside SLA. Human sends. |

## Day 3 — September 17

| Session | MDT | Paste | Pack | Why this Bot |
|---|---|---|---|---|
| Grok Bot for Marketing Operations | 9:00–10:30 | `galaxy-marketing-ops` | `marketing-content` | Monday block: what the plan promised vs what shipped. |
| Grok Bot for Post-Sales | 12:30–13:30 | `qbr-prep` | `success-support` | Review pack with evidence per claim. Missing numbers stay missing. |
| Grok Bot for Marketing | 14:30–16:00 | `one-post-a-week` | `marketing-content` | One flagship draft a week. Humans hold every publish button. |

## After you paste

Every template names a `verify` line — an observable and the number that should move.
That is the whole point. A Bot you cannot verify is a Bot you are guessing about.

```sh
gb triage            # what is wrong, and the command that addresses it
gb why <check-id>    # what a verdict read
gb galaxy            # optional: baseline / diff the three-day window
```

## What this repo will not do for you

No connector installs, no sign-in, no UI driving. Routines have no create RPC — a Bot
asked in chat to schedule itself creates a real one (proven live; the record is read
back for its prompt content, never its count). Bots ARE creatable over the API
(`CreateGrokBotAgentFromTemplate`, then `UpdateGrokBotAgent` — the Create-alone path
registers a durable identity the desktop never shows), always from the neutral seed,
always with a manifest and a rollback. `gb` sends DMs and group messages (`gb dm`,
`gb group`) and wires MCP servers (`gb mcp`) — and will not send as you.
