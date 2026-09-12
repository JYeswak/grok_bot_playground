# Quickstart — five minutes, start to finish

This repository is a **playground**, and it is worth being blunt about what that means before you
type anything.

`gb` **measures** a Grok Bot deployment from artifacts on your own disk, and it **proposes** Bots
as text you paste in by hand. It does **not** drive the Grok Bot app. It cannot create a Bot,
cannot sign in, cannot read your Bots, and never asks for a credential. There is no public write
API for Bots, routines are read-only over the API, and there is no connector-install call — so the
last step of every proposal here is a human in the app, on purpose, not as a limitation we hope to
remove later.

**Here for Galaxy week (Sept 15-17)? Start here instead.** Find your session in
[GALAXY.md](GALAXY.md), then run the three lines under *Install* below and paste one charter:

```sh
gb walk bots --paste routine-proof | pbcopy   # Linux: | xclip -sel c
```

`routine-proof` is the Bot to start with whatever your role, because it answers the one
question nobody can answer yet: it posts a single dated line once a week, so if the line
appears, scheduled runs fire on your account, and if it never appears, the Bot is fine and the
routine never ran. Swap the id for the one your session uses, or install the whole desk with
`gb setup --persona <pack>`; [docs/ROLES.md](docs/ROLES.md) maps every pack to its templates.
The rest of this page is the operator path, and it keeps.

Two things you will actually do in the next five minutes:

1. get the CLI, and walk it
2. read a proposed Bot, paste its charter into your own Grok Bot, and verify it works

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

Then type the one verb that works before you have configured anything:

```sh
gb mirror
```

It reads the Grok Bot desktop client's own state off this machine — no token, no network, no
account setup — and answers in **0.18s** with six ranked findings about *your* fleet: how many
Bots are cached, how your charter length sits against a 487-builder corpus, how many Bots are
unschedulable, and which uuids exist on disk that the roster does not list. Measured 2026-09-12
on a clean run.

`gb triage` is the second stop, not the first. It judges a configured deployment, so on a fresh
clone it answers `UNCONFIGURED` and exits 3 — the tool telling you it has nothing to judge yet,
not a failure. `gb setup` shows you the plan; `gb setup --apply` does it. Once that has run,
`gb triage` becomes the verb you use every day.

---

## 3. Walk the proposed Bots (2 minutes)

```sh
gb-walk bots
```

Twelve templates, walked in tier order. For each one: the single narrow job, the charter with its
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

These twelve have a median charter of 757.5 characters (range 727–806, all under a 900 cap) and
0.5 integrations each — deliberately closer to the corpus than to the account they are proposed
for.

Sanity-check the corpus yourself:

```sh
gb-walk bots --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])'
python3 bin/gb-templates.py stats      # ours vs the 645-Bot corpus vs the live account
```

---

## 4. Paste one charter into your own Grok Bot (1 minute)

Pick one. If you are unsure, pick `routine-proof`: it is the smallest possible test of whether
scheduled runs on your account fire at all, and it cannot do any harm because it only ever posts
one line.

```sh
gb-walk bots --paste routine-proof | pbcopy       # macOS
gb-walk bots --paste routine-proof | xclip -sel c # Linux
```

`--paste` writes the charter and **nothing else** to stdout — no header, no banner, no trailing
note — so what lands in your clipboard is exactly what belongs in the Bot's description field.

Then, in the Grok Bot app:

1. **New Bot** → name it → paste the charter into the description
2. **Add the routine by hand.** Read the `routine` block the walk printed for that template: the
   cadence, the time, and the exact prompt. Routines cannot be created over the API, so this step
   is yours.
3. **Attach the integration** if the template lists one. At most one per template, on purpose.
4. **Leave notifications as you found them.** All ten Bots on the reference account have
   notifications off, which is a large part of why nothing there was ever noticed running.

> **Unverified, and labelled as such:** whether Grok Bot can import a Bot from a file at all, what
> schema `CreateGrokBotAgentFromTemplate` accepts, and whether a pasted charter is stored
> byte-for-byte. We do not know, so nothing here claims a one-click deploy. `templates/SCHEMA.md`
> states the full vendor boundary.

---

## 5. Verify it actually works

Every template ships a `verify` line naming an **observable** and the number that should change.
That is the whole discipline of this repo: a Bot you cannot verify is a Bot you are guessing about.

For `routine-proof`, the observable is one dated line appearing in its thread each week. Measured
on the reference account, `routines_with_runs` was 0; if it becomes 1, the Bot worked. If the line
never appears, the Bot is fine and your routine never fired — which is exactly the thing worth
knowing, and exactly what nobody knew before.

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
| the whole surface, written for an agent | `gb robot-docs` |
| the machine contract (verbs, exit codes, subsystems) | `gb capabilities --json` |
| the template schema and the vendor boundary | `templates/SCHEMA.md` |
| one template in full, as data | `python3 bin/gb-templates.py show <id> --json` |
| everything else | `README.md` |

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
