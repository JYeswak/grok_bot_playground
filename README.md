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
pipx install git+https://github.com/JYeswak/grok_bot_playground
```

```console
$ gb triage
verdict GREEN · 24 checks · 0 not green

next:
  gb health --json
```

---

## What this is

A Grok Bot deployment is a moving target: the vendor changes the surface, the account changes
its entitlements, and the Bots, plugins and MCP servers you attached last month may or may not
still be reachable. This repository is the instrument that watches it — twenty-odd producers
that each measure one facet and write a dated artifact, a 24-check gate that judges the result,
and `gb`, which is the surface that makes the family legible without reading `bin/`.

`gb` is deliberately thin. It does not reimplement a producer; it dispatches to them through a
typed spine with a mandatory deadline and a bounded capture, and composes their JSON. Every
producer stays independently runnable.

## Install

| | |
|---|---|
| **pipx (recommended)** | `pipx install git+https://github.com/JYeswak/grok_bot_playground` |
| **pip** | `pip install git+https://github.com/JYeswak/grok_bot_playground` |
| **from a clone** | `git clone https://github.com/JYeswak/grok_bot_playground && cd grok_bot_playground && ./bin/gb quickstart` |

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

## The verbs

Sixteen canonical verbs, plus `work`. Every one takes `--json` and prints its envelope on
stdout and nothing else; diagnostics always go to stderr, so `gb <verb> --json | jq` never
needs a `grep`.

| verb | what it answers |
|---|---|
| `platform` | is this tool verified on this operating system, and what works here |
| `setup` | take a fresh clone to a measured instance; **dry-run unless `--apply`** |
| `triage` | what is wrong right now, and the exact commands that address it |
| `doctor` | is the measuring apparatus itself healthy — six subsystems, PASS/FAIL each |
| `health` | one line of deployment state, cheap enough for a `--watch` loop |
| `repair` | idempotently rebuild a derived artifact; **dry-run unless `--apply`** |
| `validate` | verify one thing — `plugin`, `mcp`, `types`, `platform`, `fixtures` — without changing it |
| `audit` | recent mutations to this tree, with provenance |
| `why` | provenance for one check: what it reads, and what it last said |
| `capabilities` | the machine-readable contract: commands, exit codes, subsystems, repair scopes |
| `robot-docs` | the agent handbook: read this instead of `bin/` |
| `quickstart` | the human orientation page |
| `examples` | worked invocations, copy-pasteable |
| `info` | version, contract version, runtime sha, producers present, artifact roots |
| `help` | topic manual — `exit-codes`, `subsystems`, `repair`, `platform`, `setup`, `tick`, `porting` |
| `completion` | a `bash` or `zsh` completion script |
| `work` | what to pick up next, and whether that ranking can be trusted |

```sh
gb platform --json | jq -r .status          # SUPPORTED | UNVERIFIED | UNSUPPORTED
gb triage --json | jq -r '.commands[]'      # what to run next
gb doctor --scope gate                      # one subsystem, not all six
gb repair --scope fixtures                  # show what regenerating would do
gb repair --scope fixtures --apply          # actually do it
gb why g8-desktop-inventory                 # what that check reads, and where it looked
```

`repair --apply` and `setup --apply` are the only verbs in the tool that write. Without the
flag you get the plan and `actual_actions: []`.

## Exit codes

Branch on them. They are a dictionary, not a mood.

| code | name | means |
|---:|---|---|
| `0` | OK | it ran, and the findings are clean |
| `1` | FINDINGS | it ran correctly, and the answer is bad (doctor FAIL, health RED) |
| `2` | USAGE | bad flag, unknown command, or no command |
| `3` | ENVIRONMENT | this machine cannot run the check — missing producer or toolchain |
| `4` | UPSTREAM | the vendor or network failed; nothing local is broken |
| `5` | REFUSED | a mutation was requested without the gate that permits it |
| `130` | CANCELLED | SIGINT arrived; no partial artifact was written |

`1` is not `3` and neither is `4`. "Your deployment is red", "this laptop cannot measure it" and
"the vendor is down" are three different mornings, and an agent that cannot tell them apart
retries the wrong one.

Cancellation is safe everywhere: artifact writes are atomic, so a SIGINT leaves either the old
artifact or the new one, never half of either, and the process exits `130`.

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
- **`pip install` installs the CLI and the producers, not the corpus.** The gate's 54-case
  fixture corpus, the plugin manifest, the Bot template library and the hero art ship in this
  repository but are not copied into `site-packages` — they are ~9 MB of material that grades
  repository content, not a running deployment. `gb validate fixtures` and `gb validate
  plugin` therefore want a clone; `gb doctor` reports those two subsystems DOWN/DEGRADED on a
  bare install and says why.
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

## Layout

```
bin/                    the CLI (`gb`), the typed spine (`gbtypes`, `gbargs`), the producers
fixtures/               the gate's known-good and known-bad corpus — synthetic, 54 cases
library/                Bot templates plus `gbx`, the template CLI
plugin/                 the publishable Cursor/Grok plugin and its skills
visual/                 the hero art and the prompt that produced it
types-floor.json        the strict-typing ratchet: files held to mypy --strict AND pyright
export-manifest.json    what was published, by sha256, and what was checked before it was
```

`export-manifest.json` is worth a minute. This repository is produced from a private one by an
allowlist-driven exporter, and the manifest is its receipt: every published file with its
digest, the allowlist that admitted it, the named exclusions and why, and the five refusal
classes the tree was scanned against before any of it was written.

Two selftests run here with no configuration and no network:

```sh
python3 bin/gb-surface-gate.py --selftest   # 54/54 fixtures — each known-bad must go RED
python3 bin/gbtypes-selftest.py             # 8/8 proofs — each has a known-bad leg that fires
```

The interesting reading is `bin/gbtypes.py` — the severity lattice, the deadline-bearing child
process, and the atomic write that everything else in the tree is required to go through.

## License

MIT. See [LICENSE](LICENSE).
