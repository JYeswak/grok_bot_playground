<p align="center">
  <img src="visual/hero.jpg" alt="gb — an operator CLI for a Grok Bot deployment" width="820">
</p>

<h1 align="center">gb</h1>

<p align="center">
  <em>One entry point for operating a Grok Bot deployment: what is wrong right now, is the
  measuring apparatus itself healthy, and what command fixes it.</em>
</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-black"></a>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-black">
  <img alt="zero required runtime dependencies" src="https://img.shields.io/badge/required%20deps-0-black">
</p>

```sh
curl -fsSL https://raw.githubusercontent.com/JYeswak/grok_bot_playground/main/install.sh | bash
```

```console
$ gb triage
verdict GREEN · 25 checks · 0 not green

next:
  gb health --json

$ gb-walk cli            # a guided tour of the tool, running the read-only verbs live
$ gb-walk bots           # the 12 Bots this repo proposes, and the charter to paste
```

---

## What this is

A Grok Bot deployment is a moving target: the vendor changes the surface, the account changes
its entitlements, and the Bots, plugins and MCP servers you attached last month may or may not
still be reachable. This repository is the instrument that watches it — twenty-odd producers
that each measure one facet and write a dated artifact, a 25-check gate that judges the result,
and `gb`, which is the surface that makes the family legible without reading `bin/`.

`gb` is deliberately thin. It does not reimplement a producer; it dispatches to them through a
typed spine with a mandatory deadline and a bounded capture, and composes their JSON. Every
producer stays independently runnable.

## Install

| | | |
|---|---|---|
| **`install.sh` (recommended)** | `curl -fsSL https://raw.githubusercontent.com/JYeswak/grok_bot_playground/main/install.sh \| bash` | own venv, `gb` + `gb-walk` on PATH, idempotent, self-verifying, `--uninstall` |
| **clone, then `install.sh`** | `git clone https://github.com/JYeswak/grok_bot_playground && cd grok_bot_playground && bash install.sh` | the same, **plus** the `templates/` the Bot tour walks |
| **pipx** | `pipx install git+https://github.com/JYeswak/grok_bot_playground` | the CLI only |
| **pip** | `pip install git+https://github.com/JYeswak/grok_bot_playground` | the CLI only |

`install.sh` is a wrapper around that one `pip install` line, not a second mechanism. What it
adds: it refuses in prose rather than a traceback when python3 is missing or older than 3.9,
isolates itself in `~/.local/share/grok-bot-playground`, tells you when `~/.local/bin` is not on
your `PATH`, is safe to re-run, **verifies itself by running `gb capabilities --json` and printing
what it answered**, and removes only what it created on `--uninstall`. `bash install.sh --dry-run`
prints the exact plan and touches nothing.

New here? **[QUICKSTART.md](QUICKSTART.md)** is the five-minute path: install, walk the CLI, walk
the proposed Bots, paste one charter into your own Grok Bot, verify it fired.

Python 3.9 or newer. **No required runtime dependencies** — the tool is stdlib only, and the
exporter that builds this tree resolves every import in it against the interpreter's standard
library and refuses to publish a tree that reaches outside it.

There is exactly one declared exception. `bin/gb-pull-inventory.py` decrypts the desktop's own
safeStorage v10 blob, which needs AES-CBC, which the standard library does not provide. The
import is deferred into the one function that decrypts — verified, not asserted: the exporter
also refuses a tree where an optional dependency is imported at module level.

```sh
pipx install "grok-bot-ops[inventory] @ git+https://github.com/JYeswak/grok_bot_playground"
```

Two forms, one implementation: the console script runs the very same `bin/gb` the clone does.
The difference is what is on disk around it — see [what it does not do](#what-it-does-not-do).

```sh
gb quickstart          # the orientation page
gb robot-docs          # the same thing for an agent
gb capabilities --json # the machine-readable contract
```

## Walk it

`gb-walk` is a guided tour with two tracks, and it is the fastest way to understand either half
of this repository.

```sh
gb-walk cli      # the tool: 11 stops covering every verb, in the order a new operator needs them
gb-walk bots     # the 12 Bots this repo proposes, each with a charter you can paste
gb-walk bots --paste routine-proof | pbcopy    # just the charter, clean
```

The `cli` track **runs the read-only verbs live** and shows their real output inline. It runs
nothing that writes an artifact, mutates the account, or reaches the network — those print as
`[would run]` with the measured reason. Three verbs are in that second group despite descriptions
that do not warn you, all measured 2026-09-11 by manifesting the tree before and after a tour:
`gb setup` GETs `docs.x.ai`, `gb doctor` with no `--scope` probes your MCP servers and writes
`mcp/<stamp>.json`, and `gb monitor` writes `monitor/<stamp>.json`. With those classified, a full
`gb-walk cli` adds, removes and modifies zero files.

The `bots` track reads `templates/*.json` off disk — it carries no copies, so an empty or absent
`templates/` is reported as a failure with the fix rather than rendered as a tour of nothing.
Both tracks take `--json`, `--step` (which never blocks in a pipe) and respect `NO_COLOR`.

`templates/` travels with the repository and not inside the wheel, so `gb-walk bots` wants a
clone. Run `install.sh` from one and the `gb-walk` launcher is pointed at it automatically.

## The Bots this repo proposes

Twelve templates, in three tiers, each a single narrow job with a charter, one routine, at most
one integration, an explicit approval boundary, and a `verify` line naming the number that should
change once it runs. Median charter 757.5 characters against a 900 cap.

The tiers are the argument. The deployment this was built against runs **ten department Bots**
with a median charter of **1,029 characters**, of which **2 have a routine and 0 have ever run**,
and **0 memory shards carry content**. The 645 attributed Bots built by other people have a median
charter of **625 characters** and **1.9 integrations** each. A narrow job can be scheduled because
"run this job" is a sentence; a department cannot — which is exactly why no routine ever fired.
Tier A closes those two zeros, tier B narrows a department, tier C is corpus-proven.

```sh
python3 bin/gb-templates.py stats           # ours vs the corpus vs the live account
python3 bin/gb-templates.py show <id> --json
```

This repository **proposes** Bots; it cannot create one. There is no public write API for Bots,
routines are read-only over the API, and there is no connector-install call, so the last step is
always a human in the app. `templates/SCHEMA.md` states the whole boundary, including what is
still unverified.

<!-- gb:derived:begin -->
<!-- Everything between these markers is GENERATED by `gb readme --write`. Do not hand-edit:
     `gb readme --check` exits 1 when it drifts, and `gb-gatesdoc.py` fails on a stale count.
     Prose outside the markers is handwritten and this generator never touches it. -->

## The verbs — 58

Derived from `gb capabilities --json`. Each line is the verb's own docstring, so this table cannot describe a verb the tool does not have, or miss one it does.

| verb | what it answers |
|---|---|
| `gb advise` | Rank what to do next by joining every producer this repo already runs. |
| `gb audit` | Recent mutations to this repo's artifacts, with provenance. |
| `gb bench` | Measure the latency of the hot operator surface. Measures only; changes nothing. |
| `gb blast` | What your Bots can do UNATTENDED, and how bad it could get. |
| `gb bot` | Talk to Bots and read them back — dispatch turns, read previews, full ask lifecycle. |
| `gb capabilities` | The machine-readable contract: version, commands, exit codes, subsystems. |
| `gb completion` | Print a shell completion script. |
| `gb corpus` | The 645-Bot corpus, with every figure's DENOMINATOR stated. |
| `gb daily` | The daily tick: run the collection fleet, ingest, compact, then diff against yesterday. |
| `gb demand` | Integrations builders ask for that no installable connector serves. |
| `gb deployment` | The deployment audit: offline by default, --live reads the account. |
| `gb digest` | What changed, in prose, with an executable action on every row. |
| `gb dm` | Bot-to-Bot DM (confirmed server router, not a private room). Dry run unless --yes. |
| `gb doctor` | Diagnose every subsystem (or one), report PASS/FAIL per subsystem. |
| `gb dogfood` | Capability this repo has that `gb` cannot reach — a missing verb is a defect. |
| `gb examples` | No arguments. |
| `gb export` | Split the publishable TOOL out of this INSTANCE repository, repeatably. |
| `gb feeds` | Daily-tick feed collection. Probe by default (never touches network). |
| `gb findings` | The claim SUPPLY: every teachable thing the verbs can prove, bound to its command. |
| `gb fleet` | Talk to the live fleet: roster and routines from the SERVER, or one message to one Bot. |
| `gb galaxy` | Baseline and diff the ecosystem across the Sep 15-17 Grok Bot Galaxy livestream. |
| `gb gates` | What this account can use, by family, with the unnameable remainder stated. |
| `gb gatesdoc` | Fail when GATES.md and the gate producer disagree about the gate. |
| `gb github` | Daily-tick GitHub collection. Dry-run unless --apply writes. |
| `gb goldens` | Capture or verify the CLI's observable behaviour (golden baselines). |
| `gb group` | Rooms (multi-Bot groups): list, create, members, send. Dry run unless --yes. |
| `gb handover` | Give a rebuilt Bot its predecessor's context back (dry run unless --send). |
| `gb health` | Single-shot state of the deployment — lighter than doctor, safe in a loop. |
| `gb help` | Topic-based manual. `gb help` lists the topics. |
| `gb info` | No arguments. |
| `gb inventory` | Read the Grok Bot account surface, or pull a fresh per-device inventory. |
| `gb links` | Daily-tick link fetcher. Probe plans only; collect fetches. |
| `gb matrix` | Can we use X? Computed from the shipped client, not remembered from a doc. |
| `gb mcp` | Add MCP servers, proof-call their tools, and cut tool allowlists — programmatic, receipted. |
| `gb mine` | Mine franken-harvest and the local mirror for capability this deployment could adopt. |
| `gb mirror` | Read YOUR Grok Bot deployment off this machine and rank it against the corpus. |
| `gb monitor` | Named thresholds over the artifacts already on disk, for a scheduler to branch on. |
| `gb platform` | Which OS this is, whether this tool is verified on it, and what works here. |
| `gb post` | Posts that RUN their own numbers before they ship, and refuse themselves if they fail. |
| `gb quickstart` | No arguments. |
| `gb readme` | Derive the public README's factual half from the running tool. |
| `gb record` | Turn the per-desktop hand audit into a machine-checkable artifact. |
| `gb repair` | Idempotently rebuild a derived artifact. Dry-run unless --apply is given. |
| `gb roadmap` | The open product gaps, each re-proved on every run so the list cannot go stale. |
| `gb robot-docs` | No arguments. |
| `gb setup` | Take this machine from a fresh clone to a measured instance. Dry-run unless --apply. |
| `gb skill-coverage` | Operations × skills coverage: every op reachable, every skill earning. |
| `gb sources` | Daily-tick source collection. Velocity reads; collect fetches. |
| `gb spend` | Per-Bot spend ledger from live usage-event reads (read-only). |
| `gb store` | Content-addressed store ops. Diff/stats read; ingest/compact write. |
| `gb teach` | What form a Grok Bot teaching post actually takes, measured over the collected corpus. |
| `gb templates` | The Bot templates this repo proposes, and the rules that keep them runnable. |
| `gb triage` | What is wrong right now, and the exact command that addresses it. |
| `gb validate` | Verify a thing without changing it. Pure read. |
| `gb walk` | Guided tour: walk the CLI, or walk the Bot templates this repo proposes. |
| `gb why` | Provenance for one check or facet: what it reads, and what it last said. |
| `gb work` | What to pick up next here, and whether that ranking can be trusted. |
| `gb x` | What practitioners are ACTUALLY doing with Grok Bot, ranked by distinct authors. |

## Exit codes

Read from the CLI's own table. Every verb obeys it; the gate asserts agreement per fixture.

| code | meaning |
|---:|---|
| 0 | OK — ran, and the findings are clean |
| 1 | FINDINGS — ran correctly, and the answer is bad (doctor FAIL / health RED) |
| 2 | USAGE — bad flag, unknown command, or no command |
| 3 | ENVIRONMENT — this machine cannot run the check (missing producer or toolchain) |
| 4 | UPSTREAM — the vendor or network failed; nothing local is broken |
| 5 | REFUSED — a mutation was requested without the gate that permits it |
| 130 | CANCELLED — SIGINT arrived; no partial artifact was written |

## The gate — 25 checks, 55 fixtures

`bin/gb-surface-gate.py` judges artifacts already on disk: pure stdlib, no network. Every check ships a known-bad fixture proven to make it RED, and `--selftest --disable <check>` must FAIL for each one — a check with no exclusive known-bad is carried by the suite, not proven by it.

```
  g1-surface-fetch-integrity    g2-surface-canary             g3-snapshot-freshness
  g4-client-update-applied      g5-bot-charter-present        g6-capability-delta-reviewed
  g7-desktop-parity             g8-desktop-inventory          g9-routine-health
  g10-loop-scheduled            g11-tunable-delta-reviewed    g12-ondemand-spend-bounded
  g13-practitioner-index-reviewed  g14-coverage-ratchet          g15-market-delta-reviewed
  g16-doc-delta-reviewed        g17-discovery-triaged         g18-mcp-surface-healthy
  g19-fleet-earning-its-keep    g20-context-archived          g21-usecase-corpus-reviewed
  g22-durable-io                g23-types-ratcheted           g24-cli-contract
  g25-routine-liveness
```

## Producers that prove themselves — 28

Each row was RUN to produce this table. A count here is the producer's own report, not a promise made on its behalf.

| producer | selftest |
|---|---|
| `bin/gb-advise.py` | 21/21 |
| `bin/gb-blast.py` | 20/20 |
| `bin/gb-corpus.py` | 23/23 |
| `bin/gb-demand.py` | 16/16 |
| `bin/gb-digest.py` | 10/10 |
| `bin/gb-dogfood.py` | 48/48 |
| `bin/gb-feeds.py` | 48/48 |
| `bin/gb-findings.py` | 22/22 |
| `bin/gb-fleet.py` | 11/11 |
| `bin/gb-galaxy.py` | 23/23 |
| `bin/gb-gaps.py` | 28/28 |
| `bin/gb-gatesdoc.py` | 19/19 |
| `bin/gb-github.py` | 42/42 |
| `bin/gb-inventory.py` | 59/59 |
| `bin/gb-links.py` | 77/77 |
| `bin/gb-mirror.py` | 69/69 |
| `bin/gb-plugin-validate.py` | pass |
| `bin/gb-post.py` | 45/45 |
| `bin/gb-readme.py` | 25/25 |
| `bin/gb-sources.py` | 13/13 |
| `bin/gb-teach.py` | 28/28 |
| `bin/gb-templates.py` | 56/56 |
| `bin/gb-triage-check.py` | 9/9 |
| `bin/gb-usecases.py` | 16/16 |
| `bin/gb-walk.py` | 52/52 |
| `bin/gb-x-sweep.py` | 39/39 |
| `bin/gb-x.py` | 55/55 |
| `bin/gbrpc.py` | 9/9 |

## What travels in this tree

The public tree ships the TOOLING, not the corpus. Directories below are inputs the verbs operate on. Measurements of one live account — and the scraped corpus, the teaching-form analysis, and the claim set — are withheld, so the corpus-dependent verbs refuse here by naming the command that fetches their input. `bin/gb-usecases.py` pulls its upstream without auth: build your own.

| directory | why it travels |
|---|---|
| `fixtures/` | the gate's known-good/known-bad corpus — synthetic, and a gate with no known-bad is not a gate |
| `library/` | Bot templates and `gbx`, the template CLI |
| `personas/` | the 12 persona packs `gb setup --persona` installs |
| `plugin/` | the publishable Cursor/Grok plugin and its skills |
| `templates/` | the gb-template/1 Bot templates `gb-walk.py bots` walks, and their schema |

## This build

```sh
gb --version      # version, a DERIVED build stamp, and the verb count
```

This tree is **gb 1.0.0**, 58 verbs over 58 producers. The build STAMP is deliberately not printed here: it is a hash over every producer on disk, so it changes on any edit, and a generated document that carries a value which rots on every commit is a document that reports itself stale every day until everyone learns to ignore the alarm. The command is the current answer; this file is not.

The stamp exists because nothing else could catch a stale publish. `version` is a hand-edited constant that three files merely agree on, so a months-old export and today's export produce identical metadata and `pip install -U` sees no upgrade — and nothing could catch it. Two exports of different trees cannot agree on the derived stamp: a published mirror previously sat at 18 verbs, and it used to be undetectable.

<!-- generated 2026-09-12 by `gb readme --write` -->
<!-- gb:derived:end -->

## What it does not do

Stated rather than implied, because the gap between a tool and a running instance is where
tools usually lie about themselves.

- **It does not ship a deployment.** This tree is the INSTRUMENT. It contains no account, no
  inventory, no deployment snapshot and no fleet specification — those are the operator's, and
  they were removed deliberately by an allowlist-driven exporter, not trimmed by hand.
- **A fresh install has nothing to measure yet, and says so once.** `gb capabilities`,
  `gb platform`, `gb quickstart`, `gb examples`, `gb help`, `gb robot-docs`, `gb completion`
  and `gb info` answer immediately. `gb triage`, `gb doctor`, `gb health` and `gb work` read
  artifact roots that do not exist until you have run the producers. Measured on a fresh
  clone: they print ONE route — `state: "UNCONFIGURED"`, `next_command: "gb setup --apply"` —
  and exit `3`. They deliberately do NOT print one ERROR per unmeasured check: that was 22
  rows for one fact, indistinguishable from a broken tool. Branch on `.state` before
  `.verdict`; on an unconfigured root `verdict` is `null` on purpose, and an empty board is
  never reported green.
- **It is measured on macOS, and honest about the other four platforms.** The vendor ships a
  desktop app for macOS, Windows and Linux and a companion app for iOS and Android
  ([docs](https://docs.x.ai/grok-bot/faq)). `gb platform` reports one of three statuses and
  never rounds up:

  | status | platforms | what it means |
  |---|---|---|
  | `SUPPORTED` | macOS | measured — every path and capability is read off a live install |
  | `UNVERIFIED` | Windows, Linux | the client exists, but nobody here has run one. The support directory is inferred from Electron's `userData` rule (`%APPDATA%\Grok Bot`, `$XDG_CONFIG_HOME/Grok Bot`), the credential read and the scheduler install are `NOT_IMPLEMENTED`, and all four facts are reported rather than discovered |
  | `UNSUPPORTED` | iOS, Android | companion clients of a cloud computer: no local session, no support directory, nothing here to audit |

  If the inferred path is wrong on your machine, `GB_SUPPORT_DIR=<path>` moves it — and does
  not move the status, because pointing this tool at a directory does not verify an operating
  system. `gb platform --selftest` resolves six planted platforms from `fixtures/platform/`
  and proves the Windows and Linux branches are implemented and refuse honestly; it does not
  prove the inferred paths are correct, and only a real install can. The CI matrix
  (`.github/workflows/ci.yml`) runs the suite on Ubuntu, Windows and macOS × Python 3.9 and
  3.12, and asserts each runner classifies itself correctly. Windows cancel-correctness is
  unmeasured: the spine's SIGINT proof has no Windows equivalent, so that step is scoped to
  POSIX rather than weakened until it passes.
- **`pip install` installs the CLI and the producers, not the corpus.** The gate's 55-case
  fixture corpus, the plugin manifest, the Bot template library and the hero art ship in this
  repository but are not copied into `site-packages` — they are ~9 MB of material that grades
  repository content, not a running deployment. `gb validate fixtures` and `gb validate
  plugin` therefore want a clone; `gb doctor` reports those two subsystems DOWN/DEGRADED on a
  bare install and says why. `templates/` is in the same position for the same reason, which is
  why `gb-walk bots` names a clone as its remediation instead of walking an empty directory —
  and why `install.sh`, run from a clone, points the `gb-walk` launcher at that clone rather than
  at the wheel.
- **Producers write beside the tool.** Every producer writes its artifact relative to the
  tool root, so on a `pip install` that root is inside `site-packages`. If you intend to keep
  measurements, run from a clone.
- **It does not fetch from the vendor on its own.** `bin/gb-weekly.sh` is the only thing that
  reaches the network, and it is a scheduled tick you install deliberately. Everything `gb`
  does is a read of artifacts already on disk, except `repair --apply`.
- **It is not a secret store.** It reads no credentials and writes none; `gb validate mcp`
  reports whether a server *would* be reachable, from configuration, without holding a key.
- **It does not decide whether an answer is acceptable.** The gate raises the floor and makes
  the residue visible. The human remains the trust root.

## License

MIT. See [LICENSE](LICENSE).
