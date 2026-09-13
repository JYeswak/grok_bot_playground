# Roles — pick a seat, paste one Bot

This is the human index for `personas/` and `templates/`.
It does not invent Bots. Every id below is a file that already ships.

Choose from the bounded catalog, then ask for its canonical no-write plan:

```sh
gb role --list
gb role "<role phrase>"
```

The catalog preserves all 14 pack ids. `first-hour` and `founder-operator`
are active version-one personas; the other 12 remain visible as
`legacy-unavailable` and cannot produce apply argv until migrated.

## Livestream seats (Sept 15–17)

| Seat | Session | First paste | Persona pack | Why this one, not a department Bot |
|---|---|---|---|---|
| New | Grok Bot 101 | `hello-computer` | `first-hour` | Proves the VM is alive before any plugin. Then a file read and one plugin proof. |
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

`gb role --list` is the authority for whether a pack is runnable. The table
below remains the complete template mapping; a row is not an install claim.

| Persona | Who it is for | First paste | Then these Bots |
|---|---|---|---|
| `first-hour` | New user, first hour | `hello-computer` | `first-file-desk`, `plugin-proof` |
| `founder-operator` | Solo founder / owner-operator | `morning-briefing` | `chief-of-staff`, `workforce-check`, `refusal-desk`, `decision-ledger`, `allowance-watch`, `approval-desk`, `maker-checker`, `hiring-screen`, `alfred-desk`, `cap-watch`, `calendar-owner`, `plugin-watch` |
| `eng-lead` | Engineering lead | `galaxy-engineering` | `incident-commander`, `release-notes`, `routine-proof`, `nightly-pipeline`, `plugin-watch`, `alfred-desk`, `cap-watch` |
| `product-manager` | Product manager | `vendor-watch` | `qbr-prep`, `practitioner-diff`, `plugin-watch`, `meeting-prep` |
| `sales-outbound` | Sales, SDR, sales-eng | `galaxy-sdr-desk` | `delegate-outbound`, `linkedin-drafts`, `research-desk`, `meeting-prep`, `stale-deal-sweep`, `friday-close`, `plugin-watch` |
| `success-support` | Support and post-sales | `first-reply-desk` | `qbr-prep`, `inbox-sweep`, `meeting-prep`, `approval-desk`, `plugin-watch` |
| `marketing-content` | Marketing and marketing ops | `one-post-a-week` | `youtube-brief`, `repurpose-desk`, `discovery-digest`, `search-console-diff`, `galaxy-marketing-ops`, `plugin-watch` |
| `exec-cos` | Chief of staff / exec ops | `morning-briefing` | `chief-of-staff`, `decision-ledger`, `manager-desk`, `inbox-sweep`, `delegate-worker`, `plugin-watch` |
| `finance-ops` | Finance / ops | `allowance-watch` | `maker-checker`, `sheet-ledger`, `cap-watch`, `grant-tracker`, `plugin-watch` |
| `personal-productivity` | One person, one machine | `morning-briefing` | `home-ops`, `fitness-coach`, `alfred-desk`, `errand-run`, `partner-list`, `plugin-watch` |
| `home-services-operator` | Home-services owner | `inbox-sweep` | `errand-run`, `first-reply-desk`, `approval-desk`, `plugin-watch` |
| `recruiter` | Recruiting | `hiring-screen` | `plugin-watch`, `inbox-sweep` |
| `researcher-analyst` | Research / analysis | `research-desk` | `discovery-digest`, `tunable-digest`, `grant-tracker`, `vendor-watch`, `plugin-watch` |

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

Start with `hello-computer`. If the kernel string never appears, nothing else in
the pack will either — and that is cheaper to learn on a canary than on a CRM.
`routine-proof` is the next canary: one dated line a week, so you know scheduled
runs fire. Use it after the computer has answered, not instead of it.
