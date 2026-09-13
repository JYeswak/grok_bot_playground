# grokbot — Oracle Gates (G1)

## Oracle

See [`AGENTS.md` §ORACLE](./AGENTS.md#oracle) — xAI's shipped Grok Bot surface in three producers
(published docs, installed client + account entitlement, live roster). Not duplicated here; one source.

## Runner

`bin/gb-surface-gate.py` — pure python3 stdlib, no network, judges artifacts already on disk.
Version `1.9.0`, **28 checks**, **59 fixtures**. Every count in this document is mechanically
enforced against the producer by `bin/gb-gatesdoc.py`; it exits 1 on any drift.

```bash
bin/gb-weekly.sh                                  # snapshot → audit → gate (the tick)
bin/gb-audit-remote.sh brain                      # audit a registered desktop over ssh/Tailscale
bin/gb-record-inventory.py --device brain --template   # record what the audit cannot see
bin/gb-surface-gate.py                            # judge what is on disk
bin/gb-surface-gate.py --json                     # same, machine-readable
bin/gb-surface-gate.py --selftest                 # 59 fixtures, must be 59/59
bin/gb-surface-gate.py --selftest --disable g4-client-update-applied   # mutation: must FAIL
bin/gb-surface-gate.py --capabilities             # self-description, thresholds, inputs
bin/gb-gatesdoc.py                                # this document vs the producer; 1 on drift
```

### Exit-code contract

`EXIT = {GREEN: 0, RED: 1, ERROR: 2}`, and the verdict text always agrees with the code. A
normal run exits the worst verdict any enabled check returned. `--selftest` exits `0` when every
fixture matched, `1` when any fixture missed, and `2` when `fixtures/` is absent or empty — a
gate with no known-bad is not a gate. `--capabilities` and `--help` exit `0`.

| Check | RED means | ERROR means |
|---|---|---|
| `g1-surface-fetch-integrity` | a page came back short (<500B) / markerless / hash-mismatched | zero pages, the manifest is missing or unparseable, or the index did not return 200 |
| `g2-surface-canary` | — (this check has no RED leg: a collapsed index is an instrument failure, not a finding) | the doc index collapsed below 15 grok-bot / 100 total pages; the fetcher or the site changed |
| `g3-snapshot-freshness` | newest snapshot >8 days old — the weekly loop stopped | no snapshot at all |
| `g4-client-update-applied` | a newer client is staged/shipped and we are not running it | app or audit missing |
| `g5-bot-charter-present` | a Bot holds shared-computer access with no standing description | roster unreadable (empty ≠ "no bots") |
| `g6-capability-delta-reviewed` | capabilities moved and no `findings/<date>.md` carries a `reviewed-audit:` line naming the current audit | no gate names recovered (decoding broke), **or** either side of the diff predates `gates.enabled_ids` — an inexact delta is unmeasured, not a finding |
| `g7-desktop-parity` | a desktop in `desktops.json` was never audited, is >8d stale, or runs a different client than the host of record | `desktops.json` missing/empty, or the host-of-record audit came from a different hostname than registered |
| `g8-desktop-inventory` | a registered desktop has no hand record, one older than 30d, or a null `local_exec_policy` / `auto_review_rules` / `bots` field | nothing recorded at all, or `desktops.json` missing |
| `g9-routine-health` | an enabled routine whose every recorded run failed, a routine left paused, or a routine whose run history was never opened | no inventory to read (g8 owns that gap) |
| `g10-loop-scheduled` | no run recorded in the last 9 days, or none of the last 3 came from the scheduler — a loop nobody triggers is a loop that stopped | no run ever recorded, or no scheduler registered with `launchctl` |
| `g11-tunable-delta-reviewed` | a dynamic-config or layer VALUE digest moved (retuned/added/removed) and no findings file names the current audit — parameters changed with nobody reading the new numbers | the audit carries no tunable digests, **or** the baseline predates digest capture. Unmeasured is not unchanged |
| `g12-ondemand-spend-bounded` | on-demand billing is ENABLED and the period is on pace to exceed the included allowance (`usage_percent` projected to `next_reset` above 100%) — the overage bills whatever the in-app cap is | no inventory carries a `usage` block; unmeasured spend is not bounded spend |
| `g13-practitioner-index-reviewed` | the curated community index (awesome-grok-bot) moved, or was never reviewed, and `state/practitioner-reviewed.json` does not record the current hash — the practitioner half of the brief went unread | no snapshot manifest, no `practitioner-index` row, or the fetch failed. Unread is not unchanged |
| `g14-coverage-ratchet` | the facets `bin/gb-coverage.py` can measure fell below `coverage-floor.json` — a read started returning null and every check depending on it went quiet at the same time | `gb-coverage.py` missing or failing, or no floor recorded. The instrument cannot grade itself unmeasured |
| `g15-market-delta-reviewed` | the plugin catalog, Bot marketplace, or the `gitRef` an INSTALLED plugin is pinned to moved, and `market-reviewed.json` does not name the current snapshot. A moved ref is new third-party code inside a Bot approved once | no market snapshot, or the catalog read came back empty |
| `g16-doc-delta-reviewed` | a page hash moved across either official doc tree (or the practitioner index) and `surface-reviewed.json` does not name the current snapshot | no snapshot, or the manifest carries no pages |
| `g17-discovery-triaged` | a new repo at or above 50★ that no list held was never triaged into `sources.json` or dismissed with a reason | a discovery query FAILED, or every query returned zero — a broken search is not a quiet week |
| `g18-mcp-surface-healthy` | an MCP server stopped answering, answers with FEWER tools than last week, or **rejected a credential we hold** (stale secret, not a locked door) | no MCP run, or the validator listed no servers. `401/403` is reachable-behind-auth, never a failure |
| `g19-fleet-earning-its-keep` | a Bot holds a standing description and shared-computer access with NO routine and NO conversation for >14d (`FLEET_GRACE_DAYS`) — a credential nobody is watching. Give it a job or delete it; both clear this | `bin/gb-utilization.py` has never run, or the newest utilization run lists zero Bots — the measurement read nothing |
| `g20-context-archived` | the newest context archive is >8d old. RED on its own **regardless of the calendar** when a replica sits AT the client entry cap: memory cannot be written back, so past the cap the archive is the only copy and every new turn drops an old one forever | no archive at all, the manifest lists no replicas, or its filename carries no readable date |
| `g21-usecase-corpus-reviewed` | new Bot definitions or new integrations appeared since the previous corpus snapshot and `usecases-reviewed.json` does not name the current one — what the population builds moved with nobody reading the diff | `bin/gb-usecases.py` has never run, or the newest corpus holds zero Bot definitions. A failed fetch is not an empty ecosystem |
| `g22-durable-io` | a producer in `bin/` writes an artifact non-atomically (`X.write_text` / `write_bytes`, `json.dump(…, fh)`) or blocks on a child with no `timeout=` (`subprocess.run` / `check_output` / `check_call`, `.communicate()` / `.wait()`). An interrupted write does not merely fail — it ERASES a measurement and reports the erasure as an absence | `bin/` holds no python producers (an empty scan set is never a pass), or a producer does not parse |
| `g23-types-ratcheted` | a file on the type floor no longer exists, a checker reports errors on the floor, or the floor SHRANK — the ratchet only turns one way | no typecheck artifact, it records no checker run, or a checker did not run (`mypy: not installed` means UNMEASURED, which is not the same as clean) |
| `g24-cli-contract` | `bin/gb` is not executable, a canonical verb is no longer registered, a registered command has no handler (a `KeyError` on first use), or the exit-code dictionary holds fewer than 5 entries | `bin/gb` is absent — the producer family has no entry point — or it does not parse |
| `g25-routine-liveness` | an enabled routine is past its own `next_run_at_ms` and still has ZERO runs — a dead scheduler. `g9` cannot see this: a routine that never ran has never failed | no inventory to read routine liveness from (g8 owns that gap) |
| `g26-jobs-proof-calls` | a `jobs/walks.jsonl` `done` receipt has no `proof_ok` — name-match / paste-only claimed done without `gb bot ask --expect` | `jobs/walks.jsonl` absent or empty — done-claims are UNMEASURED |
| `g27-surface-drift` | the newest mined bundle under `schema/<ver>/` is unreviewed AND a method was added, removed, or changed (same name, different input/output/kind/requiredness) since the reviewed bundle | `schema/registry.json` or `schema/reviewed.json` missing/unparseable, or a named bundle unreadable — drift is UNMEASURED |
| `g28-grokbotdev-fresh` | newest `grokbotdev/<stamp>.json` is >8d old, or `mcp.used` is true (NE-25 forbids mcp.grokbot.dev) | no grokbotdev artifact, wrong schema, or `rows` missing — unmeasured community feed is not a quiet week |

Advisory rows (never gating, always printed): idle Bots >21d, unread backlog, undecoded enabled
capabilities. A fourth advisory — notifications-off count — was REMOVED 2026-09-11 (NE-6): the
field it read is not a mirror of the Notifications switch and the server roster does not carry it
at all, so there is no trustworthy source and the honest move is to say nothing.

## Ratchet rules (floors only move up)

- `min_grok_bot_pages` 15, `min_llms_total_pages` 100 — raise when the vendor publishes more; never lower.
- `max_snapshot_age_days` 8 — lower only. Raising it requires a `NEGATIVE_EVIDENCE.md` entry.
- `min_page_bytes` 500. `stale_bot_days` 21. Desktop audits share `max_snapshot_age_days`.
- `max_inventory_age_days` 30 — lower only; the hand record decays faster than the settings do.
- `MAX_RUN_GAP_DAYS` 9, `FLEET_GRACE_DAYS` 14, `MAX_ARCHIVE_AGE_DAYS` 8 — the three gating
  constants `--capabilities` does not yet expose. Same rule: lower only.
- `coverage-floor.json` `covered` 50 of 50 — raise only, in the commit that earns it. Lowering it
  requires a `NEGATIVE_EVIDENCE.md` entry naming the read that stopped working and why it is
  acceptable.
- Threshold changes re-run `--selftest` in the same commit; the new value appears in `--capabilities`.

## Fail-closed rules

- Missing oracle = FAIL, not skip. Missing snapshot, missing audit, unreadable roster → ERROR.
- An empty scan set is an ERROR. "Never checked" must not read like "passed".
- Every check disabled = ERROR, never GREEN.
- Exit code always agrees with the verdict text (0/1/2); the selftest asserts it per fixture.
- Partial never rounds up: one RED check makes the tick RED; one ERROR makes it ERROR.
- Every check ships a known-bad fixture proven to make it RED/ERROR. 57 fixtures: 5 known-good
  (a clean week; a reviewed capability delta; an acknowledged pause; a reviewed self-made routine;
  a new Bot inside the fleet grace window) keep the suite from drifting over-strict, 40 known-bad
  RED and 12 known-bad ERROR keep it honest.
- Mutation-verified 27 of 27. `--selftest --disable <check>` must FAIL for
  every check. `g25-routine-liveness` was the last exception: it appeared only inside
  `bad-no-inventory`, co-signed there by g8/g9/g12/g14, so disabling it still passed and NOTHING
  proved it. `bad-routine-never-fired` closes that gap — an enabled routine, past its own
  `next_run_at_ms` (a fixed 2026-09-10T00:00:00Z, so it cannot drift GREEN), with zero runs ever
  and an otherwise-clean root. Note what makes it distinct from `g9-routine-health`: nothing has
  FAILED there. The run list is empty, not failing — the case g9 branches past, since it tests
  "runs is None" and "every run failed" and an empty list is neither.
- `bad-surface-phantom` is `g27-surface-drift`'s exclusive proof: a GOOD root in every
  old sense (GREEN on all other 26 checks) carrying a reviewed `schema/0.47.0` and an
  unreviewed `schema/0.48.0` whose method delta is one of each kind — `+CreateBot`
  added, `-ListBots` removed, `~GetBot` changed by requiredness alone (field `verbose`
  optional -> required, same input/output/kind). CHANGED counts input, output, kind,
  and requiredness: a requiredness flip is a breaking change and gets its own RED line.
- This document is itself gated. `bin/gb-gatesdoc.py` fails when it and the producer disagree on
  any check id or on the fixture count, so the normative doc cannot drift ahead of or behind the
  code again (it did: it claimed 44 fixtures and 18 checks against a real 54 and 25).
- Determinism: fixtures must be wall-clock independent. Every maker time window is a fixed date;
  every verdict-affecting age reads the `--now` date (`FIXTURE_NOW` under `--selftest`), never the
  wall clock — regenerating fixtures on another day must diff empty.
