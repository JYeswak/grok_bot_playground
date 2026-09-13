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

- On command: `gb market bots` (default live pull) and `gb market refresh --corpus`.
- On exit: none.
- Timer/throttle: none required. `--offline` reads cache only.
- One-way: catalog → sqlite → optional JSON stamp. Never sqlite → catalog. Never JSON → sqlite unless `--offline` and sqlite missing (rebuild cache from newest stamp, then integrity_check).

## Versioning

- DB marker: `PRAGMA user_version` (schema cookie). Bump on every DDL. Selftest fails if a row writes without the current version.
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
3. Two taxonomies smashed (`Personal` vs `personal-admin`) stay labeled until a map ships; do not invent one category from a keyword.
4. `gb market bots` first row must not be used as a persona pick list until that map exists.

## Fail

A tick that ships persona rank on top of smashed categories and missing share_id keys.
