# grok-bot-library

Deployable Grok Bot templates for the parts of the market nobody has built, with a CLI that
does every step the API allows and walks you through the steps it does not.

## Why these templates and not others

Measured 2026-09-11 against the live catalog (322 plugins), the population's Bot corpus (645
attributed definitions, 294 integrations) and the curated marketplace (71 listings):

| gap | measurement | template here |
|---|---|---|
| **Productivity has no shelf** | 146 Bots built, **0** curated listings | `daily-brief` |
| **math / statistics** | **0** of 322 plugins | `experiment-readout` |
| **OCR / document extraction** | **0** of 322 plugins | `pdf-extract` |

The catalog is 263 SaaS connectors out of 322 — it sells *access*, almost never *ability*. Two
of the three templates here need **no connector at all**: they use the Bot's own Linux computer,
which every Bot already has and almost nobody uses for compute.

## What one-touch actually means

Honest boundary, measured against the API rather than assumed:

| step | automated? |
|---|---|
| create the Bot with a real durable identity | **yes** — `CreateGrokBotAgentFromTemplate` |
| set its name, title, description, avatar | **yes** — `UpdateGrokBotAgent` |
| attach skills | **yes** — they travel in the template |
| install a connector | **no** — no install RPC exists; the CLI prints the exact click path |
| create a routine | **no** — `automations` is read-only over the API; same |
| store a secret | **no, deliberately** — it goes to your OS keychain and then the Bot's secret card, never through this repo or a chat message |

`gbx doctor` re-reads the account afterwards and tells you which manual steps actually landed,
so "I did that" is verified rather than believed.

## Install

```bash
git clone https://github.com/<you>/grok-bot-library && cd grok-bot-library
./bin/gbx list                  # what is available, and which gap it fills
./bin/gbx show experiment-readout
./bin/gbx new experiment-readout --deploy
./bin/gbx doctor
```

`gbx` needs the Grok Bot desktop app signed in on this machine: it reuses that session rather
than asking for a key. No token is ever written to disk by this tool.

## Template shape

```
templates/<name>/
  bot.yaml            identity, job, approval boundary, gap evidence, manual steps
  skills/<skill>/SKILL.md   frontmatter with `name` + a description that says WHEN to use it
```

`./bin/gbx validate` enforces the schema and refuses a template whose skill descriptions do not
say when to use them — a skill nobody can route to is a skill nobody runs.

## License

MIT. No telemetry, no network calls except to the Grok Bot API you are already signed in to.
