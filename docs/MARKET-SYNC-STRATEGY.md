# Market catalog sync strategy

Filled from rust-cli-with-sqlite before any schema code.
Process gates stolen from frankensqlite-mega-skill (Studio).
Engine is stock sqlite3 (Python stdlib). fsqlite is not involved.

## Source of Truth

- Primary: live public catalogs (elie222/botdirectory.ai tarball + kydlikebtc/awesome-grokbot catalog.json). Official xAI listings when a desktop stamp exists.
- SQLite (`usecases/market.sqlite`): rebuildable cache. Never the oracle.
- Dated JSON (`usecases/<stamp>.json`): inspect/export only. Gitignored.
- Rationale: a stranger clone must live-pull. A local file that can go stale is not the market.

## Status honesty

- Live: live HTTP pull, stamp write, `PRAGMA integrity_check`, `user_version`.
- Dormant: official desktop snapshot on Linux (skip, exit 3, no traceback).
- Design: persona shortlist, swarm-from-live-shares. Not this file.
- Issue-limited: RongleCat link harvest still 0 on a clone (local index only).

## Sync Triggers

- On command: `gb market bots` (default live pull), `gb market jobs` (same live pull, then one Thompson-sampled arm per job), and `gb market refresh --corpus`.
- On exit: none.
- Timer/throttle: none required. `--offline` reads cache only. `gb market jobs --offline` refuses if integrity_check or user_version fail.
- One-way: catalog → sqlite → optional JSON stamp. Never sqlite → catalog. Never JSON → sqlite unless `--offline` and sqlite missing (rebuild cache from newest stamp, then integrity_check).

## Versioning

- DB marker: `PRAGMA user_version` (schema cookie). Now 4 (bandit posterior + outcomes). Bump on every DDL. A v1 cache is refused, then rebuilt from live catalogs.
- JSONL/JSON marker: `schema` field `gb-usecases/1` until the sqlite layer ships `gb-usecases/2`.
- Row identity: `name_key` (casefold collapsed space) plus optional `share_id`. share_id is passed through from the catalog, never invented.

## Concurrency

- Lock file path: `usecases/market.sqlite.lock` (exclusive around rebuild).
- Busy timeout: 5s.
- One writer. Full-file rebuild + replace. journal_mode=DELETE (WAL sidecars would orphan on replace). Not fsqlite MVCC.

## Failure Handling

- DB locked: fail out loud, do not wait forever.
- integrity_check ≠ ok: refuse the file. Do not reconstruct from a same-size backup (C94). Rebuild from live catalogs, or from the newest JSON stamp if `--offline`.
- JSON parse error: refuse. Empty corpus after a failed fetch is an error, not zero Bots.
- Silent PRAGMA: not proof. Read the value back.

## Validation (planted known-bad)

1. integrity_check on a planted malformed file → refuse.
2. Merge: directory charter wins; share_id fills in; name_key join only.
3. Upstream `category` is preserved. `taxonomy` is an explicit alias table only: Personal=personal-admin→personal, Sales=customer-sales→sales. Unknown → unmapped. Do not guess from charter text.
4. `gb market bots` first row is not a persona pick list. `gb market jobs` is one Thompson-sampled deployable arm per job, not a frozen sort.

## Fail

A tick that ships persona rank on top of smashed categories and missing share_id keys, or that ships a frozen lexicographic winner sort (origin / approval / chars / added_at / name_key) as if it were selection.

## Taxonomy aliases (user_version 2)

See `TAXONOMY_MAP` in `bin/gb-market-db.py`. Only those keys map. Everything else is `unmapped`.

## Jobs (user_version 3)

`job` is assigned only from `taxonomy` via `JOB_FROM_TAXONOMY`. personal / productivity / success / unmapped → `none`. Name and charter never assign a job. decide and refuse have no taxonomy yet.

## Job winners (`gb market jobs`)

One deployable arm per job from the live cache. Thompson sampling over a documented linear prior — not a frozen lexicographic sort, and not a persona ranker. Pack job lists may stay explicit later; winner pick must not ship as origin/approval/chars/added_at/name_key sort.

- Skip job `none`. Do not invent decide/refuse.
- An arm MUST have a real `share_id` passed through from the catalog. Never invent one.
- If a job has rows but none are deployable, list it as blocked (count + reason). Do not fill it with a no-share row.
- Each deployable row (real `share_id`, job ≠ none) is an arm.
- Score is the documented linear predictor in `SCORE_WEIGHTS` (`bin/gb-market-db.py`): `both`, `approval`, `log1p(prompt_chars)`, `recency_days` clipped to `[0, 365]`. Missing `added_at` is the cap. Name and charter words are not features.
- Selection is Thompson sampling: draw `s ~ N(μ, σ²)` per arm, pick the max. Ties are a random choice among equals, not name-alpha.
- Posterior starts uninformative: `μ` is the linear score, `σ² = PRIOR_VARIANCE` (4.0). With no outcomes the draw must explore — it is not the argmax of the linear score.
- Outcomes live in `bandit_outcomes`. A write updates `bandit_posterior` via a Gaussian conjugate update (obs variance 1.0). Catalog rebuild copies outcomes and recomputes the posterior; it does not invent rewards.
- Do not rank by display name alpha. Do not guess quality from charter words.

## Bandit tables (user_version 4)

`bandit_outcomes` and `bandit_posterior`. DDL bump from 3. A v3 cache is refused, then rebuilt from live catalogs (empty posterior — that is the uninformative start).
