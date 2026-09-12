# Roles — pick a seat, paste one Bot

This is the human index for `personas/` and `templates/`.
It does not invent Bots. Every id below is a file that already ships.

How to use a row:

```sh
gb walk bots --paste <template-id> | pbcopy   # macOS; Linux: xclip -sel c
gb setup --persona <persona-id>               # dry-run unless --apply
```

The paste path never needs a token. The persona path is optional and still
refuses to guess: `gb setup` prints the plan; `--apply` is the only write.

## Livestream seats (Sept 15–17)

| Seat | Session | First paste | Persona pack | Why this one, not a department Bot |
|---|---|---|---|---|
| New | Grok Bot 101 | `routine-proof` | — | Smallest proof that a scheduled run fires. One dated line. |
| Engineer | Engineering | `galaxy-engineering` | `eng-lead` | Makes the review queue visible. Reads GitHub; comments on nothing. |
| Product | Product Managers | `vendor-watch` | `product-manager` | Reports only what a vendor page changed, quoted. |
| Founder | Founders | `morning-briefing` | `founder-operator` | One morning block: priorities, calendar, spend, overnight flags. |
| Sales engineer | Sales Engineering | `research-desk` | `sales-outbound` | Cited source per fact. Drafts none, sends none. |
| Seller | Sales | `stale-deal-sweep` | `sales-outbound` | Idle threads get one drafted nudge. Sent stays at zero. |
| SDR | SDRs | `galaxy-sdr-desk` | `sales-outbound` | One first-touch email in Drafts per account. |
| Support | Customer Support | `first-reply-desk` | `success-support` | First-response drafts the same day. Human sends. |
| Marketing ops | Marketing Operations | `galaxy-marketing-ops` | `marketing-content` | Monday block: plan versus shipped. |
| Post-sales | Post-Sales | `qbr-prep` | `success-support` | Review pack. Missing numbers stay missing. |
| Marketer | Marketing | `one-post-a-week` | `marketing-content` | One flagship draft a week. Humans hold publish. |

Companion write-up with session times: [GALAXY.md](../GALAXY.md).

## Every persona pack

`gb setup --persona <id>` installs the pack named here. The first paste is the
Bot to run *before* the pack, so a new account has one receipt before it has
a desk.

| Persona | Who it is for | First paste | Then these Bots |
|---|---|---|---|
| `founder-operator` | Solo founder / owner-operator | `morning-briefing` | `chief-of-staff`, `workforce-check`, `refusal-desk`, `decision-ledger`, `allowance-watch`, `approval-desk` |
| `eng-lead` | Engineering lead | `galaxy-engineering` | `incident-commander`, `release-notes`, `nightly-pipeline`, `plugin-watch` |
| `product-manager` | Product manager | `vendor-watch` | `practitioner-diff`, `qbr-prep`, `meeting-prep` |
| `sales-outbound` | Sales, SDR, sales-eng | `galaxy-sdr-desk` | `stale-deal-sweep`, `research-desk`, `linkedin-drafts` |
| `success-support` | Support and post-sales | `first-reply-desk` | `inbox-sweep`, `qbr-prep` |
| `marketing-content` | Marketing and marketing ops | `one-post-a-week` | `galaxy-marketing-ops`, `repurpose-desk`, `youtube-brief`, `linkedin-drafts` |
| `exec-cos` | Chief of staff / exec ops | `morning-briefing` | `chief-of-staff`, `decision-ledger`, `meeting-prep`, `friday-close` |
| `finance-ops` | Finance / ops | `allowance-watch` | `cap-watch`, `sheet-ledger`, `grant-tracker` |
| `personal-productivity` | One person, one machine | `routine-proof` | `errand-run`, `home-ops`, `fitness-coach`, `alfred-desk` |
| `home-services-operator` | Home-services owner | `inbox-sweep` | `first-reply-desk`, `approval-desk`, `home-ops` |
| `recruiter` | Recruiting | `hiring-screen` | `inbox-sweep` |
| `researcher-analyst` | Research / analysis | `research-desk` | `discovery-digest`, `vendor-watch` |

If a name in the right-hand column is missing from `templates/`, that is a
defect in *this file*, not a Bot to invent. Check with:

```sh
ls personas/*.json templates/*.json
```

## What a good first Bot looks like

The measured failure mode on the reference account: ten department Bots,
median charter **1,029** characters, **2** routines, **0** runs, **0** memory
shards with content. A department cannot be scheduled because "run this job"
is not a sentence.

The 645 attributed Bots other people built sit at a median charter of **625**
characters. Every template in this repo is one job, under a 900-character cap,
with a `verify` line that names the number that should move.

Start with `routine-proof`. If that dated line never appears, nothing else in
the pack will either — and that is cheaper to learn on a canary than on a CRM.
