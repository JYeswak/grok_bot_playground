# Market catalog sync strategy

Filled from rust-cli-with-sqlite before any schema code.
Process gates stolen from frankensqlite-mega-skill (Studio).
Engine is stock sqlite3 (Python stdlib). fsqlite is not involved.

## Source of Truth

- Primary: live public catalogs (elie222/botdirectory.ai tarball + kydlikebtc/awesome-grokbot catalog.json). Official xAI listings when a desktop stamp exists.
- SQLite (`usecases/market.sqlite`): rebuildable catalog cache. Never the oracle. Never the posterior.
- Bandit store (`usecases/bandit.sqlite`): recorded outcomes only. Separate file, own `user_version`. Catalog rebuild must not wipe it (C49).
- Dated JSON (`usecases/<stamp>.json`): inspect/export only. Gitignored.
- Rationale: a stranger clone must live-pull. A local file that can go stale is not the market.

## Status honesty

- Live: live HTTP pull, stamp write, `PRAGMA integrity_check`, `user_version`.
- Dormant: official desktop snapshot on Linux (skip, exit 3, no traceback).
- Design: persona shortlist, swarm-from-live-shares. Not this file.
- Issue-limited: RongleCat link harvest still 0 on a clone (local index only).

## Sync Triggers

- On command: `gb market bots` (default live pull), `gb market jobs` (same live pull, then one Thompson draw per job), and `gb market refresh --corpus`. `gb market keep|skip|ban <share_id>` write the bandit store only. They do not import, deploy, or invent a share_id.
- On exit: none.
- Timer/throttle: none required. `--offline` reads cache only. `gb market jobs --offline` refuses if the catalog `integrity_check` or `user_version` fail. A planted-bad bandit store is also refused. A missing bandit store is a cold start (explore), not a refuse.
- One-way: catalog → sqlite → optional JSON stamp. Never sqlite → catalog. Never JSON → sqlite unless `--offline` and sqlite missing (rebuild cache from newest stamp, then integrity_check). Never catalog rebuild → bandit store.

## Versioning

- Catalog DB marker: `PRAGMA user_version` on `market.sqlite`. Now 3 (job column). Bump on every catalog DDL. A v1 cache is refused, then rebuilt from live catalogs.
- Bandit DB marker: `PRAGMA user_version` on `bandit.sqlite`. Now 2 (impressions, banned, outcome kind). Own cookie. A stale or planted-bad file is refused; it is not reconstructed from the catalog.
- JSONL/JSON marker: `schema` field `gb-usecases/1` until the sqlite layer ships `gb-usecases/2`.
- Catalog row identity: `name_key` (casefold collapsed space) plus optional `share_id`. share_id is passed through from the catalog, never invented.
- Arm identity: `job + name_key + share_id` (C106). Same name, different share_id is a different arm.

## Concurrency

- Catalog lock: `usecases/market.sqlite.lock` (exclusive around rebuild).
- Bandit lock: `usecases/bandit.sqlite.lock` (exclusive around outcome writes).
- Busy timeout: 5s.
- One writer per file. Catalog is full-file rebuild + replace. Bandit appends outcomes in place. journal_mode=DELETE (WAL sidecars would orphan on replace). Not fsqlite MVCC.

## Failure Handling

- DB locked: fail out loud, do not wait forever.
- integrity_check ≠ ok: refuse the file. Do not reconstruct from a same-size backup (C94). Catalog: rebuild from live catalogs, or from the newest JSON stamp if `--offline`. Bandit: refuse; do not invent an empty posterior over a corrupt file.
- JSON parse error: refuse. Empty corpus after a failed fetch is an error, not zero Bots.
- Silent PRAGMA: not proof. Read the value back.

## Validation (planted known-bad)

1. integrity_check on a planted malformed catalog or bandit file → refuse.
2. Merge: directory charter wins; share_id fills in; name_key join only.
3. Upstream `category` is preserved. `taxonomy` is an explicit alias table only: Personal=personal-admin→personal, Sales=customer-sales→sales. Unknown → unmapped. Do not guess from charter text.
4. `gb market bots` first row is not a persona pick list. `gb market jobs` is one Thompson draw per job (`method=thompson`), honest about pulls.

## Fail

A tick that ships persona rank on top of smashed categories and missing share_id keys, or that ships a frozen winner sort — lexicographic or a feature prior (`both` / approval / `prompt_chars` / recency) dressed as Thompson.

## Taxonomy aliases (user_version 2)

See `TAXONOMY_MAP` in `bin/gb-market-db.py`. Only those keys map. Everything else is `unmapped`.

## Jobs (user_version 3)

`job` is assigned only from `taxonomy` via `JOB_FROM_TAXONOMY`. personal / productivity / success / unmapped → `none`. Name and charter never assign a job. decide and refuse have no taxonomy yet.

## Job draws (`gb market jobs`)

One deployable arm per job. Thompson sampling, Beta(1,1) per arm. Not a frozen sort, not a linear prior, not a persona ranker.

- Skip job `none`. Do not invent decide/refuse.
- An arm MUST have a real `share_id` passed through from the catalog. Never invent one.
- If a job has rows but none are deployable, list it as blocked (count + reason). Do not fill it with a no-share row.
- Each deployable row (real `share_id`, job ≠ none) is an arm.
- Catalog fields (`origin`, `has_approval_language`, `prompt_chars`, `added_at`, name, charter) are display only. They are not ranking keys and not a prior mean.
- Selection: two Gamma(shape, 1) draws, θ = Ga(α,1) / (Ga(α,1) + Ga(β,1)), pick max. Ties are a random choice among equals, not name-alpha. UCB1 is the wrong first-hour algorithm (it walks every arm first; `brief` has 100+ deployable arms).
- Posterior starts uninformative: α=1, β=1. With no outcomes the draw must explore. Select does not invent a reward.
- `gb market jobs` records an impression (α/β unchanged). A keep increments α; a skip increments β. Those writes are `record_outcome` on `usecases/bandit.sqlite`. Ban sets `banned=1` and is a hard filter on the next draw, not a silent β bump.
- Product state lives under `usecases/`. Never under /tmp.

A Thompson draw without a recorded keep or skip is only an impression: the arm was shown, not judged. There is no champion and no WINNER until outcomes exist. `gb market jobs` prints the full catalog `share_id` (never a 16-char slice) after recording the impression, so IMPR is the post-draw count and PULLS stays keep/skip outcomes. `gb market keep <share_id>` and `gb market skip <share_id>` are the verdicts for that subject's latest or only matching arm; they accept a unique prefix of one stored share_id so a copied truncated id still records, and they refuse if the share_id is missing, not in the store, or the prefix matches two or more arms. They never invent one. `gb market ban <share_id>` marks the arm ineligible so constrained select never draws it again. Ban is a hard filter, not a posterior tweak. Charter text and likes are not rewards.

## Bandit store (`usecases/bandit.sqlite`, user_version 2)

Separate file from `market.sqlite`. Own `integrity_check` and `user_version`. Gitignored. Catalog rebuild replaces only the catalog cache (C49).
