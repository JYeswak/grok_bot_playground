# Quickstart — four-command first hour

Scan the live marketplace, deploy the Bots you name, swarm a persona pack,
then read the stack before you copy a weak Bot.

```sh
gb market refresh --corpus
gb market bots
gb templates deploy <id> --apply
gb swarm <persona>
gb stack
```

`gb market bots` reprints the official + public-corpus union (name, source,
category, builder) with official / corpus / overlap / union denominators, plus
curated URL finds (counts; `--urls` dumps them). `gb market new` is names in
the current identity set that were not in the previous stamps. `deploy --apply`
creates each named Bot via CreateGrokBotAgentFromTemplate + Update. `gb swarm
founder-operator` plans that pack's Bot list. `gb stack` reprints the plugin
catalog total from the newest market snapshot, lists attachable skills, and
runs `gb x` methods ranked by distinct authors. This path never redirects to
`gb role "first hour"`.

**Here for Galaxy week (Sept 15-17)?** Find your session in
[GALAXY.md](GALAXY.md) and paste that row's Bot.
[docs/ROLES.md](docs/ROLES.md) maps packs to templates.

Two things you will actually do in the next five minutes:

1. get the CLI, and scan official listings + the public corpus + URL finds
2. deploy the Bots you name, or swarm one persona, then read the stack

### Honest limits

Official marketplace rows often lack `share_id`. One-click deploy is blocked
until the scan carries it. Plugin install is still human: Settings → Plugins.
There is no install RPC. usecases/ is a public-source refresh a clone can run.

`gb` does not drive the Grok Bot app, cannot sign in, and never asks for a credential.
There is no connector-install call — connectors are installed in the app. What it CAN do,
over the account API and always receipted: create a Bot from the seed template
(`gb templates deploy <id>`, `--plan` by default, manifest + `--rollback`), ask a Bot in
chat to schedule its own routine (the only routine-create path; proven 2026-09-11 and
2026-09-12, prompt content verified back, never the count), send DMs and group messages
(`gb dm`, `gb group`), and wire MCP servers plus cut their tool allowlists (`gb mcp`).
Every other mutation is dry-run until `--apply`, and the last step of every proposal is
still verified by reading the server back — never by the Bot's reply.

**Firecrawl is not an MCP `gb` can see:** marketplace skill-pack, 0 MCP tools, CLI
authenticated on the *cloud computer* (2026-09-12). Probe there with `firecrawl --status`;
house skill `firecrawl-read`. Studio has no CLI. Never paste `FIRECRAWL_API_KEY`.

---

## 1. Install (30 seconds)

**Recommended — clone, then install.** This gets you both halves: the CLI on your `PATH` *and*
the Bot templates on disk, so both tours work.

```sh
git clone https://github.com/JYeswak/grok_bot_playground
cd grok_bot_playground
bash install.sh
```

**Or one line, no clone.** You get the CLI and the tool tour; the Bot tour will tell you it needs
the repository and name the fix.

```sh
curl -fsSL https://raw.githubusercontent.com/JYeswak/grok_bot_playground/main/install.sh | bash
```

**Or straight pip, if you would rather not run someone's shell script.** This is exactly what
`install.sh` wraps — measured working in a clean venv on 2026-09-11: zero dependencies resolved, a
226 KB wheel, `gb capabilities --json` answering `gb 1.0.0`.

```sh
pip install git+https://github.com/JYeswak/grok_bot_playground
```

What `install.sh` adds over that one pip line, and nothing else: it refuses in prose (not a
traceback) when python3 is missing or older than 3.9, isolates itself in its own venv under
`~/.local/share/grok-bot-playground`, puts `gb` and `gb-walk` in `~/.local/bin` and tells you if
that directory is not on your `PATH`, is safe to re-run, **verifies itself by running the tool and
printing what it answered**, and uninstalls cleanly.

```sh
bash install.sh --dry-run     # the exact plan, touching nothing
bash install.sh --uninstall   # removes only what it created
```

Requirements: python3 >= 3.9 and `git`. Nothing else — the package declares zero dependencies, and
the exporter that publishes this repo mechanically verifies that claim against the standard
library on every export.

---

## 2. Walk the tool (2 minutes)

```sh
gb-walk cli
```

Eleven stops covering the core verbs (`gb capabilities --json` lists every one), ordered by the question you actually have rather than
alphabetically. The spine is `platform`/`setup` → `triage`/`health` → `work` → `doctor` → `why`
→ `repair` → `validate` → `audit` → `monitor` → `mine`, then the seven reference surfaces
(`info`, `quickstart`, `examples`, `help`, `robot-docs`, `capabilities`, `completion`) last,
because nobody reads reference material before they have a problem. It **runs the read-only verbs
live** and shows you their real output inline.

It also refuses to run anything that writes an artifact, mutates your account, or reaches the
network — those are printed as `[would run]` with the measured reason. Three of them are worth
knowing before you type them yourself, because their own descriptions do not warn you:

| command | what it actually does |
|---|---|
| `gb setup` | writes nothing, but its `network` row GETs `https://docs.x.ai/llms.txt` |
| `gb doctor` (no `--scope`) | probes your MCP servers over the network and writes `mcp/<stamp>.json` |
| `gb monitor` | writes `monitor/<stamp>.json`, despite reading "over the artifacts already on disk" |

All three were measured on 2026-09-11 by manifesting the repository before and after a full tour.
With them classified correctly, `gb-walk cli` adds, removes, and modifies **zero** files — verified
over three consecutive runs.

Useful flags: `--step` pages one stop at a time (and never blocks in a pipe), `--full` stops
clipping long output at 14 lines, `--json` gives you the whole tour as data, and `NO_COLOR=1`
strips every escape.

Then the four-command first hour:

```sh
gb market refresh --corpus
gb market bots
gb templates deploy <id> --apply
gb swarm founder-operator
gb stack
```

`gb bootstrap --for-agent` prints the same four commands. `gb walk bots --paste <id>`
still prints one charter when you want to paste instead of deploy. `gb mirror` is
the later, read-only look at *your* fleet off this machine — no token, no
network. Measured 2026-09-12 on a clean run: **0.18s**, six ranked findings.

`gb triage` is the second stop, not the first. It judges a configured deployment, so on a fresh
clone it answers `UNCONFIGURED` and exits 3 — the tool telling you it has nothing to judge yet,
not a failure. `gb setup` shows you the plan; `gb setup --apply` does it. Once that has run,
`gb triage` becomes the verb you use every day.


---

## 3. Walk the proposed Bots (2 minutes)

```sh
gb-walk bots
```

Templates, walked in tier order. For each one: the single narrow job, the charter with its
character count, the routine and what it leaves behind, the approval boundary, what it remembers
between runs, and a `verify` line naming the number that should change once it is running.


The tiers are the argument, not decoration:

- **Tier A** closes a measured deficit. On the deployment this repo was built against, 2 routines
  exist across 10 Bots and **0 have ever run**, and **0 memory shards carry content**. Tier A Bots
  exist to make those two zeros move.
- **Tier B** narrows a department Bot. That account's ten Bots are departments — CRM, Chief of
  Staff, Ops, Front Desk — with a median charter of **1,029 characters**. A department cannot be
  scheduled, because "run this job" is not a sentence you can write about one. That is *why* no
  routine has ever fired.
- **Tier C** is corpus-proven: shapes that recur across 645 attributed Bots built by real people,
  whose median charter is **625 characters** with **1.9 integrations** each.

Sanity-check the corpus yourself:

```sh
gb-walk bots --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])'
python3 bin/gb-templates.py stats      # ours vs the 645-Bot corpus vs the live account
```

---

## 4. Paste or deploy one Bot (1 minute)

Scan first (`gb market bots`), then name slugs and deploy them. `gb swarm
founder-operator` stands up that pack. `hello-computer` remains a deployable Bot
if you want a machine check; it is not the on-ramp.

```sh
gb walk bots --paste research-desk | pbcopy       # macOS
gb walk bots --paste research-desk | xclip -sel c # Linux
gb templates deploy research-desk --apply         # live desktop session
```


`--paste` writes the charter and **nothing else** to stdout — no header, no banner, no trailing
note — so what lands in your clipboard is exactly what belongs in the Bot's description field.

Then, in the Grok Bot app:

1. **New Bot** → name it → paste the charter into the description
2. **Add the routine.** By hand: read the `routine` block the walk printed — cadence,
   time, exact prompt — and enter it in the app. Or deploy it: `gb templates deploy
   <id> --apply` creates the Bot from the seed template and asks it, in chat, to
   schedule itself (there is no direct routine-create RPC; the chat path is proven
   live, prompt content verified back, never the count).
3. **Read the stack** with `gb stack` before you copy a weak Bot. Enable a
   plugin for that Bot in the app; installed is not enabled. Plugin install
   is Settings → Plugins (no install RPC).
4. **Leave notifications as you found them.** Per-Bot switches read ON over the
   authoritative echo where measured (8/8 on 2026-09-12); the old "all off" line was
   roster-blob cache (NE-19). Whether an approval actually surfaces on-device is unmeasured.

> **Now verified, formerly not:** `gb templates deploy <id> --apply` creates the Bot from
> the seed template (manifest + rollback) and asks it, in chat, to schedule its routine —
> prompt content read back, never the count. `templates/SCHEMA.md` states the full boundary.

---

## 5. Verify it actually works

Every template ships a `verify` line naming an **observable** and the number that should change.
That is the whole discipline of this repo: a Bot you cannot verify is a Bot you are guessing about.

For `research-desk`, the observable is three sources with a quoted line each. For
`routine-proof`, the observable is one dated line appearing in its thread each week.

Once you have your own deployment measured, the tool takes over:

```sh
gb triage            # what is wrong right now, and the command that addresses it
gb why <check-id>    # what a verdict read, and what it last said — never argue with an un-why'd board
gb doctor --scope <subsystem>
```

---

## Where to go next

| you want | read |
|---|---|
| the four-command first hour | `gb market bots` then `gb templates deploy` then `gb swarm` then `gb stack` |
| the whole surface, written for an agent | `gb robot-docs` |
| the machine contract (verbs, exit codes, subsystems) | `gb capabilities --json` |
| the template schema and the vendor boundary | `templates/SCHEMA.md` |
| one template in full, as data | `python3 bin/gb-templates.py show <id> --json` |
| everything else | [README.md](README.md) |


Exit codes, because they are load-bearing and `1` does not mean "crashed":

| code | meaning |
|---|---|
| 0 | ran, and the findings are clean |
| 1 | **FINDINGS** — ran correctly, and the answer is bad |
| 2 | usage |
| 3 | this machine cannot run the check |
| 4 | the vendor or network failed; nothing local is broken |
| 5 | refused — a mutation without the gate that permits it |
| 130 | cancelled; no partial artifact was written |
