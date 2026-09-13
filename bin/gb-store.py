#!/usr/bin/env python3
"""gb-store - content-addressed snapshot store for the daily diff ecosystem.

WHY THIS EXISTS (measured 2026-09-11, do not re-derive):

  The repo writes one dated JSON per producer run. At the daily cadence the fleet now runs, that
  is 50-87% redundant at the ROW level:

      usecases      2 snaps   1 290 row-instances    645 unique   50.0% redundant
      market        4 snaps       8 row-instances      2 unique   75.0% redundant
      deployment   19 snaps     207 row-instances     58 unique   72.0% redundant
      inventory    34 snaps      92 row-instances     12 unique   87.0% redundant
      utilization   7 snaps      70 row-instances     31 unique   55.7% redundant

  FILE-level dedup does not help: only 0.2% of bytes are byte-identical files, because every
  snapshot carries a fresh `captured_at`. The redundancy is INSIDE the files, not between them.

WHAT THIS IS

  A row is stored ONCE, keyed by the BLAKE2b hash of its canonical JSON. A snapshot is a manifest
  of hashes. "No duplicates" is a property of the SCHEMA, not of a cleanup job that must keep
  being run:

      blob(h PK, kind, json)        every distinct row, exactly once
      snap(id PK, kind, captured_at, device, source)
      member(snap, h)               the manifest

  A diff is a set difference over an index, not a parse of two JSON trees. That is what makes a
  daily cadence affordable.

VOLATILE FIELDS - the property the whole design rests on

  A row holding anything derived from `now` defeats content addressing: the body changes daily
  while the information does not. Measured by the vendor-docs producer: +1 day of clock changed
  204/204 raw row bodies but 0/204 fingerprints once `signals.age_days` was stripped. Hashing
  verbatim would allocate 204 blobs/day to learn nothing.

  Every producer artifact MAY declare top-level `volatile_fields` (dotted paths); this store
  strips them before hashing. The list is read FROM THE DOCUMENT, never defaulted here - a
  hard-coded list rots silently the day a producer renames a field.

ENGINE: fsqlite 0.3.9 (operator's call, 2026-09-11)

  Installed from the SIGNED darwin_arm64 release asset (SHA256 verified before unpack, `Mach-O
  64-bit executable arm64` confirmed) rather than built: the RCH cross-build would have synced a
  multi-GiB target dir onto a volume with 11 GiB free.

  Transport is the CLI in batch mode, so no python dependency is added and
  `packaging/pyproject.toml` keeps declaring `dependencies = []` - a claim `gb-export-public.py`
  mechanically checks. The schema was probed on 0.3.9 before adoption: `WITHOUT ROWID`,
  `ON CONFLICT DO NOTHING` and secondary indexes all work, and the dedup primitive is exact.

  `--engine sqlite3` selects the stdlib fallback. Both engines run the SAME DDL and SQL, and
  every result carries the engine that produced it: an unlabelled measurement from an unknown
  engine is not evidence.

  NOT a performance claim. fsqlite exists for page-level MVCC across concurrent writers; this
  workload has one writer. No benchmark was run. See NEGATIVE_EVIDENCE.md.

TYPING

  On the strict floor (`types-floor.json`): mypy --strict + pyright, zero errors. The JSON tree is
  a recursive `JsonValue`, not `Any`; every command returns a frozen dataclass, not a bare dict;
  engines are a `Protocol`, not a base class with `NotImplementedError` stubs. The one unavoidable
  `Any` is at the `json.loads` boundary, narrowed immediately by `as_obj`.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from typing import (
    Dict,
    Final,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
    cast,
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gbargs  # noqa: E402
import gbtypes  # noqa: E402

SCHEMA_VERSION: Final[int] = 2
SENTINEL: Final[str] = "__gbstore_eof__"

# A JSON document, typed rather than `Any`. Recursive aliases are supported by both checkers.
JsonValue = Union[
    None, bool, int, float, str, List["JsonValue"], Dict[str, "JsonValue"]
]
JsonObj = Dict[str, JsonValue]

# Producer roots ingested. A root absent on disk is skipped, never an error: the fleet grows.
INGEST_ROOTS: Final[Tuple[str, ...]] = (
    "sources",  # gb-sources.py - docs.x.ai sitemap + Cursor changelog/blog
    "github",  # gb-github.py  - repos, MCP servers, templates
    "feeds",  # gb-feeds.py   - RSS/Atom + corpus practitioners
    "grokbotdev",  # gb-grokbotdev.py — grokbot.dev feed.json+status.json (community-claim)
    "x",  # gb-x.py       - X firehose + outbound URLs
    "links",  # gb-links.py   - fetched/titled/classified URLs
    "usecases",
    "market",
    "deployment",
    "inventory",
    "utilization",
    "discovery",
    "mcp",
    "typecheck",
    "gaps",
)

DDL: Final[Tuple[str, ...]] = (
    "CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);",
    "CREATE TABLE IF NOT EXISTS blob(h TEXT PRIMARY KEY, kind TEXT NOT NULL, "
    "json TEXT NOT NULL) WITHOUT ROWID;",
    "CREATE TABLE IF NOT EXISTS snap(id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
    "captured_at TEXT NOT NULL, device TEXT, source TEXT NOT NULL);",
    "CREATE TABLE IF NOT EXISTS member(snap TEXT NOT NULL, h TEXT NOT NULL, "
    "PRIMARY KEY (snap, h)) WITHOUT ROWID;",
    "CREATE INDEX IF NOT EXISTS member_by_h ON member(h);",
    "CREATE INDEX IF NOT EXISTS snap_by_kind ON snap(kind, captured_at);",
)


class Exit(enum.IntEnum):
    """Exit codes as a dictionary, per the repo's CLI contract."""

    OK = 0
    FINDINGS = 1
    USAGE = 2
    ENGINE = 4


class EngineError(RuntimeError):
    """A store operation failed. Carries the engine name so a report cannot attribute one
    engine's failure to the other."""


# ---------------------------------------------------------------- typed results


@dataclasses.dataclass(frozen=True)
class KindStat:
    kind: str
    snapshots: int
    row_instances: int
    unique_rows: int

    @property
    def redundant_pct(self) -> float:
        if self.row_instances <= 0:
            return 0.0
        return round(100.0 * (1.0 - self.unique_rows / self.row_instances), 1)

    def as_json(self) -> JsonObj:
        return {
            "kind": self.kind,
            "snapshots": self.snapshots,
            "row_instances": self.row_instances,
            "unique_rows": self.unique_rows,
            "redundant_pct": self.redundant_pct,
        }


@dataclasses.dataclass(frozen=True)
class IngestResult:
    engine: str
    files_ingested: int
    files_already_known: int
    rows_per_kind: Mapping[str, int]
    unique_rows: int
    row_instances: int

    @property
    def dedup_ratio(self) -> float:
        if self.row_instances <= 0:
            return 0.0
        return round(1.0 - self.unique_rows / self.row_instances, 4)

    def as_json(self) -> JsonObj:
        return {
            "engine": self.engine,
            "files_ingested": self.files_ingested,
            "files_already_known": self.files_already_known,
            "rows_per_kind": dict(sorted(self.rows_per_kind.items())),
            "unique_rows": self.unique_rows,
            "row_instances": self.row_instances,
            "dedup_ratio": self.dedup_ratio,
        }


@dataclasses.dataclass(frozen=True)
class StatsResult:
    """MEASURED 2026-09-11 - why three byte counts and not one.

    The first version compared the .db FILE against the artifacts and reported `saved -61.2%`,
    i.e. the store looked WORSE than the JSON it replaced. That number was true and useless: the
    file was 34.8 MB of which a 30.3 MB WAL had never been checkpointed, while the actual row
    content was 5.8 MB against 21.6 MB of artifacts. One number hid a 73% win behind an
    unflushed log. So the honest report carries content (what we store), file (what it costs on
    disk today), and WAL (what a `compact` would reclaim) separately.
    """

    engine: str
    store_bytes: int
    wal_bytes: int
    content_bytes: int
    artifact_bytes: int
    kinds: Tuple[KindStat, ...]

    @property
    def content_vs_artifacts(self) -> float:
        """The real dedup win: unique row content against the artifact bytes it replaces."""
        if self.artifact_bytes <= 0:
            return 0.0
        return round(1.0 - self.content_bytes / self.artifact_bytes, 4)

    @property
    def file_vs_artifacts(self) -> float:
        """What it costs on disk right now, WAL included. Negative until a compact runs."""
        if self.artifact_bytes <= 0:
            return 0.0
        return round(1.0 - (self.store_bytes + self.wal_bytes) / self.artifact_bytes, 4)

    def as_json(self) -> JsonObj:
        return {
            "engine": self.engine,
            "schema_version": SCHEMA_VERSION,
            "content_bytes": self.content_bytes,
            "store_bytes": self.store_bytes,
            "wal_bytes": self.wal_bytes,
            "artifact_bytes": self.artifact_bytes,
            "content_vs_artifacts": self.content_vs_artifacts,
            "file_vs_artifacts": self.file_vs_artifacts,
            "kinds": [k.as_json() for k in self.kinds],
        }


@dataclasses.dataclass(frozen=True)
class SnapRef:
    id: str
    captured_at: str
    source: str


@dataclasses.dataclass(frozen=True)
class DiffResult:
    """A diff graded by IDENTITY, not just by content hash.

    MEASURED 2026-09-11, and this is why the naive version was wrong. The store keys a blob on
    the hash of the whole row, so ANY field change makes a row look like one deletion plus one
    insertion. GitHub rows carry `signals.pushed_at`, and 287 of 452 tracked repos were pushed
    within a day: tomorrow's first real diff would have reported roughly +250 and -250 for repos
    that merely received a commit. True, and useless — the signal a human wants ("3 repos are
    NEW") was about to be buried under 250 rows of "this repo got a push".

    Declaring `signals.pushed_at` volatile would be the WRONG fix: a push is real news, it just
    is not a new repo. So rows are grouped by their producer-assigned `id` (the shared
    blake2b-16 of the canonical URL that all five producers emit) and the difference is graded:

        NEW      an id present on the right and absent on the left
        GONE     an id present on the left and absent on the right
        CHANGED  the same id on both sides with a different body

    A producer whose rows carry no `id` degrades to hash-only and SAYS SO in `graded`, rather
    than silently reporting every change as new.
    """

    kind: str
    status: str
    snapshots: int
    left: Optional[SnapRef] = None
    right: Optional[SnapRef] = None
    added: int = 0
    removed: int = 0
    changed: int = 0
    graded: bool = False
    added_rows: Tuple[JsonValue, ...] = ()
    changed_rows: Tuple[JsonValue, ...] = ()
    detail: str = ""

    def as_json(self) -> JsonObj:
        out: JsonObj = {
            "kind": self.kind,
            "status": self.status,
            "snapshots": self.snapshots,
        }
        if self.detail:
            out["detail"] = self.detail
        if self.left is not None and self.right is not None:
            out["from"] = {
                "captured_at": self.left.captured_at,
                "source": self.left.source,
            }
            out["to"] = {
                "captured_at": self.right.captured_at,
                "source": self.right.source,
            }
            out["graded_by_identity"] = self.graded
            out["added"] = self.added
            out["removed"] = self.removed
            out["changed"] = self.changed
            out["added_rows"] = list(self.added_rows)
            out["changed_rows"] = list(self.changed_rows)
        return out


# ---------------------------------------------------------------- engines


class Engine(Protocol):
    """What the store needs from a database. A Protocol, not a base class: neither engine
    inherits from the other, and a stub raising NotImplementedError is a runtime landmine that
    the type system should be catching instead."""

    name: str
    version: str

    def script(self, sql: str) -> None: ...

    def query(self, sql: str) -> List[Tuple[str, ...]]: ...

    def close(self) -> None: ...


def q(value: object) -> str:
    """SQL string literal. Doubling the quote is the whole escape: values here are JSON and hex
    digests produced by this file, never text from a shell."""
    return "'" + str(value).replace("'", "''") + "'"


class FsqliteEngine:
    """fsqlite via its CLI in batch mode.

    A subprocess and not a binding because a python extension would break `dependencies = []`,
    which the public exporter verifies.

    Statements go to a temp .sql file executed with `-init`: a multi-megabyte `-c` argument would
    exceed ARG_MAX on a real ingest. Child processes run through `gbtypes.run`, which makes the
    deadline mandatory - `g22-durable-io` fails an unbounded child wait.
    """

    name = "fsqlite"

    def __init__(self, db: pathlib.Path, binary: str) -> None:
        self.db = db
        self.binary = binary
        proc = gbtypes.run([binary, "--version"], timeout_s=60.0)
        self.version = (proc.out or proc.err).strip() or "unknown"

    def _run(self, sql: str) -> str:
        body = sql if sql.endswith("\n") else sql + "\n"
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sql", prefix="gb-store-", delete=False
        ) as fh:
            fh.write(body)
            script_path = pathlib.Path(fh.name)
        try:
            proc = gbtypes.run(
                [
                    self.binary,
                    "--batch",
                    "-init",
                    str(script_path),
                    str(self.db),
                    "-c",
                    f"SELECT {q(SENTINEL)};",
                ],
                timeout_s=1800.0,
            )
            if proc.timed_out:
                raise EngineError(f"fsqlite timed out after {proc.elapsed_s:.1f}s")
            if proc.code != 0:
                raise EngineError(
                    f"fsqlite rc={proc.code}: {(proc.err or proc.out)[:400]}"
                )
            if proc.truncated:
                raise EngineError(
                    "fsqlite output hit the capture cap; refusing a partial read"
                )
            return proc.out
        finally:
            script_path.unlink(missing_ok=True)

    def script(self, sql: str) -> None:
        self._run(sql)

    @staticmethod
    def parse_line(line: str) -> Tuple[str, ...]:
        """Parse one fsqlite result row.

        MEASURED 2026-09-11 against fsqlite 0.3.9, because it does NOT match sqlite3's shell:

            SELECT id, b, 42 FROM t;   ->   'abc' | 'x' | 42

        Strings are single-quoted with inner quotes DOUBLED; numbers are bare; columns are
        separated by ` | `. Naive `split(' | ')` would corrupt any JSON payload containing that
        sequence, so this walks the line and respects quoting.

        Why not `char(31)` as a separator, which would have made splitting trivial: fsqlite
        0.3.9 evaluates `char(31)` to the EMPTY STRING and reports no error, so the fields
        silently ran together. A separator that vanishes without failing is the worst possible
        outcome, so the column list is now selected honestly and parsed here.
        """
        out: List[str] = []
        i = 0
        n = len(line)
        while i < n:
            if line[i] == "'":
                i += 1
                buf: List[str] = []
                while i < n:
                    if line[i] == "'":
                        if i + 1 < n and line[i + 1] == "'":
                            buf.append("'")
                            i += 2
                            continue
                        i += 1
                        break
                    buf.append(line[i])
                    i += 1
                out.append("".join(buf))
            else:
                nxt = line.find(" | ", i)
                if nxt < 0:
                    out.append(line[i:].strip())
                    break
                out.append(line[i:nxt].strip())
                i = nxt
            if line.startswith(" | ", i):
                i += 3
        return tuple(out)

    def query(self, sql: str) -> List[Tuple[str, ...]]:
        rows: List[Tuple[str, ...]] = []
        for line in self._run(sql).splitlines():
            text = line.strip()
            if not text or SENTINEL in text:
                continue  # the trailing sentinel SELECT, not data
            rows.append(self.parse_line(text))
        return rows

    def close(self) -> None:
        return None


class Sqlite3Engine:
    """stdlib sqlite3 - the fallback for a machine with no fsqlite installed."""

    name = "sqlite3"

    def __init__(self, db: pathlib.Path) -> None:
        self.db = db
        self.conn = sqlite3.connect(str(db), isolation_level=None, timeout=30.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.version = "sqlite3 " + sqlite3.sqlite_version

    def script(self, sql: str) -> None:
        self.conn.executescript(sql)

    def query(self, sql: str) -> List[Tuple[str, ...]]:
        out: List[Tuple[str, ...]] = []
        for raw in self.conn.execute(sql):
            out.append(tuple("" if col is None else str(col) for col in raw))
        return out

    def close(self) -> None:
        self.conn.close()


def open_engine(db: pathlib.Path, want: str = "auto") -> Engine:
    db.parent.mkdir(parents=True, exist_ok=True)
    binary = shutil.which("fsqlite")
    if want == "fsqlite" and binary is None:
        raise EngineError("--engine fsqlite requested but no fsqlite on PATH")
    engine: Engine
    if want in ("auto", "fsqlite") and binary is not None:
        engine = FsqliteEngine(db, binary)
    else:
        engine = Sqlite3Engine(db)
    engine.script("\n".join(DDL))
    engine.script(
        f"INSERT INTO meta(k,v) VALUES('schema_version',{q(SCHEMA_VERSION)}) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v;"
    )
    return engine


# ---------------------------------------------------------------- json handling


def as_obj(value: JsonValue) -> Optional[JsonObj]:
    """Narrow a JsonValue to an object, or None. The single place the tree is inspected."""
    return value if isinstance(value, dict) else None


def load_json(path: pathlib.Path) -> Optional[JsonValue]:
    """Read one artifact. `json.loads` is typed `Any`, so the cast here is the ONE place the
    untyped boundary is crossed — declared, not silenced. Everything downstream narrows through
    `as_obj`/`isinstance` and the checkers enforce it."""
    try:
        parsed = json.loads(path.read_text(errors="replace"))
    except (ValueError, OSError):
        return None
    return cast(JsonValue, parsed)


def canon(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def strip_path(value: JsonValue, dotted: str) -> JsonValue:
    """Remove one dotted path from a copy. A missing intermediate key is not an error: producers
    differ, and a path that does not apply must leave the row untouched."""
    obj = as_obj(value)
    if obj is None:
        return value
    parts = dotted.split(".")
    out: JsonObj = dict(obj)
    cursor: JsonObj = out
    for part in parts[:-1]:
        nxt = as_obj(cursor.get(part))
        if nxt is None:
            return out
        copied: JsonObj = dict(nxt)
        cursor[part] = copied
        cursor = copied
    cursor.pop(parts[-1], None)
    return out


def stable_row(row: JsonValue, volatile: Sequence[str]) -> JsonValue:
    for path in volatile:
        row = strip_path(row, path)
    return row


def rowhash(row: JsonValue, volatile: Sequence[str]) -> str:
    payload = canon(stable_row(row, volatile)).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


def iter_rows(doc: JsonValue) -> Iterator[JsonValue]:
    """Yield record-like rows. Producers do not share one shape: take the declared list if there
    is one, else the first list-of-objects, else the whole document as a single row. A wrong guess
    costs dedup ratio, never correctness - the blob still holds verbatim content."""
    if isinstance(doc, list):
        yield from doc
        return
    obj = as_obj(doc)
    if obj is None:
        yield doc
        return
    for key in ("rows", "bots", "entries", "listings", "items", "checks"):
        candidate = obj.get(key)
        if isinstance(candidate, list) and candidate and isinstance(candidate[0], dict):
            yield from candidate
            return
    for candidate in obj.values():
        if isinstance(candidate, list) and candidate and isinstance(candidate[0], dict):
            yield from candidate
            return
    yield doc


def volatile_of(doc: JsonValue) -> List[str]:
    """Read the producer's OWN declaration. Never a default: a baked-in guess rots silently the
    day a producer renames a field."""
    obj = as_obj(doc)
    if obj is None:
        return []
    declared = obj.get("volatile_fields")
    if not isinstance(declared, list):
        return []
    return [item for item in declared if isinstance(item, str)]


def captured_of(doc: JsonValue, path: pathlib.Path) -> str:
    obj = as_obj(doc)
    if obj is not None:
        for key in ("captured_at", "measured_at", "created_at", "generated_at"):
            value = obj.get(key)
            if isinstance(value, str) and value:
                return value
    stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def device_of(doc: JsonValue, path: pathlib.Path) -> str:
    obj = as_obj(doc)
    if obj is not None:
        for key in ("device", "label", "host", "hostname"):
            value = obj.get(key)
            if isinstance(value, str) and value:
                return value
    parts = path.name.split(".")
    return parts[1] if len(parts) >= 3 else ""


def scalar(engine: Engine, sql: str) -> int:
    rows = engine.query(sql)
    if not rows or not rows[0]:
        return 0
    try:
        return int(rows[0][0])
    except ValueError:
        return 0


# ---------------------------------------------------------------- commands


def cmd_ingest(
    engine: Engine, root: pathlib.Path, roots: Sequence[str], limit: int = 0
) -> IngestResult:
    known = {row[0] for row in engine.query("SELECT id FROM snap;") if row}
    files = 0
    skipped = 0
    per_kind: Dict[str, int] = {}
    batch: List[str] = []

    for name in roots:
        folder = root / name
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*.json")):
            if path.name.startswith("."):
                continue  # gb-links keeps a .cache.json that is not an artifact
            rel = str(path.relative_to(root))
            sid = hashlib.blake2b(rel.encode("utf-8"), digest_size=12).hexdigest()
            if sid in known:
                skipped += 1
                continue
            doc = load_json(path)
            if doc is None:
                continue
            volatile = volatile_of(doc)
            batch.append(
                "INSERT INTO snap(id,kind,captured_at,device,source) VALUES("
                f"{q(sid)},{q(name)},{q(captured_of(doc, path))},"
                f"{q(device_of(doc, path))},{q(rel)}) ON CONFLICT(id) DO NOTHING;"
            )
            seen: set[str] = set()
            for row in iter_rows(doc):
                digest = rowhash(row, volatile)
                if digest in seen:
                    continue  # a row duplicated INSIDE one snapshot is one member
                seen.add(digest)
                batch.append(
                    f"INSERT INTO blob(h,kind,json) VALUES({q(digest)},{q(name)},"
                    f"{q(canon(row))}) ON CONFLICT(h) DO NOTHING;"
                )
                batch.append(
                    f"INSERT INTO member(snap,h) VALUES({q(sid)},{q(digest)}) "
                    "ON CONFLICT DO NOTHING;"
                )
            files += 1
            per_kind[name] = per_kind.get(name, 0) + len(seen)
            if limit and files >= limit:
                break
        if limit and files >= limit:
            break

    if batch:
        # One transaction: an interrupted ingest leaves no half-manifest.
        engine.script("BEGIN;\n" + "\n".join(batch) + "\nCOMMIT;")

    return IngestResult(
        engine=engine.name,
        files_ingested=files,
        files_already_known=skipped,
        rows_per_kind=per_kind,
        unique_rows=scalar(engine, "SELECT count(*) FROM blob;"),
        row_instances=scalar(engine, "SELECT count(*) FROM member;"),
    )


def cmd_stats(engine: Engine, db: pathlib.Path, root: pathlib.Path) -> StatsResult:
    raw = engine.query(
        "SELECT s.kind, count(DISTINCT s.id), count(m.h), count(DISTINCT m.h) "
        "FROM snap s LEFT JOIN member m ON m.snap=s.id GROUP BY s.kind ORDER BY s.kind;"
    )
    kinds: List[KindStat] = []
    for row in raw:
        if len(row) != 4:
            continue
        try:
            kinds.append(KindStat(row[0], int(row[1]), int(row[2]), int(row[3])))
        except ValueError:
            continue
    artifact_bytes = sum(
        f.stat().st_size
        for name in INGEST_ROOTS
        if (root / name).exists()
        for f in (root / name).rglob("*.json")
        if not f.name.startswith(".")
    )
    return StatsResult(
        engine=f"{engine.name} ({engine.version})",
        store_bytes=db.stat().st_size if db.exists() else 0,
        wal_bytes=sum(
            p.stat().st_size for p in db.parent.glob(db.name + "-wal*") if p.is_file()
        ),
        content_bytes=scalar(engine, "SELECT sum(length(json)) FROM blob;"),
        artifact_bytes=artifact_bytes,
        kinds=tuple(kinds),
    )


def cmd_compact(engine: Engine, db: pathlib.Path) -> JsonObj:
    """Checkpoint the WAL and reclaim free pages.

    VERIFIED BY MEASUREMENT, NOT BY THE PRAGMA. fsqlite silently succeeds on PRAGMAs it does not
    recognise, so a green return proves nothing; this compares file sizes before and after and
    reports `verified` on that basis alone. Measured on the first real corpus: WAL 30 343 832 ->
    32 bytes, then VACUUM 34 807 808 -> 14 024 704 (59.7% reclaimed), row counts unchanged.
    """

    def sizes() -> Tuple[int, int]:
        main = db.stat().st_size if db.exists() else 0
        wal = sum(
            p.stat().st_size for p in db.parent.glob(db.name + "-wal*") if p.is_file()
        )
        return main, wal

    rows_before = scalar(engine, "SELECT count(*) FROM member;")
    db_before, wal_before = sizes()
    engine.script("PRAGMA wal_checkpoint(TRUNCATE);")
    engine.script("VACUUM;")
    db_after, wal_after = sizes()
    rows_after = scalar(engine, "SELECT count(*) FROM member;")
    return {
        "db_bytes_before": db_before,
        "db_bytes_after": db_after,
        "wal_bytes_before": wal_before,
        "wal_bytes_after": wal_after,
        "reclaimed_pct": round(
            100.0 * (1.0 - (db_after + wal_after) / (db_before + wal_before)), 1
        )
        if (db_before + wal_before)
        else 0.0,
        "rows_before": rows_before,
        "rows_after": rows_after,
        "verified": (db_after + wal_after) < (db_before + wal_before)
        and rows_after == rows_before,
    }


def row_identity(value: JsonValue) -> Optional[str]:
    """The producer-assigned stable id, if the row carries one.

    All five collection producers emit `id` = blake2b-16 of the canonical URL, agreed and
    cross-verified between them (987 URLs are held by two producers with 0 id disagreements).
    A row without one is not an error — it just cannot be graded.
    """
    obj = as_obj(value)
    if obj is None:
        return None
    for key in ("id", "full_name", "url", "name"):
        candidate = obj.get(key)
        if isinstance(candidate, str) and candidate:
            return f"{key}:{candidate}"
    return None


def side_rows(engine: Engine, mine: str, theirs: str) -> List[JsonValue]:
    """Every row present in `mine` and absent (by hash) from `theirs`."""
    out: List[JsonValue] = []
    for row in engine.query(
        f"SELECT b.json FROM member m JOIN blob b ON b.h=m.h WHERE m.snap={q(mine)} "
        f"AND m.h NOT IN (SELECT h FROM member WHERE snap={q(theirs)});"
    ):
        parsed = load_json_text(row[0])
        if parsed is not None:
            out.append(parsed)
    return out


def cmd_diff(
    engine: Engine, kind: str, a: Optional[str], b: Optional[str], limit: int = 40
) -> DiffResult:
    raw = engine.query(
        f"SELECT id, captured_at, source FROM snap WHERE kind={q(kind)} ORDER BY captured_at;"
    )
    refs: List[SnapRef] = []
    for row in raw:
        if len(row) == 3:
            refs.append(SnapRef(row[0], row[1], row[2]))
    if len(refs) < 2:
        return DiffResult(
            kind=kind,
            status="INSUFFICIENT",
            snapshots=len(refs),
            detail="a diff needs two snapshots of this kind; unmeasured is never zero",
        )
    left = next((r for r in refs if a and r.id.startswith(a)), refs[-2])
    right = next((r for r in refs if b and r.id.startswith(b)), refs[-1])

    only_left = side_rows(engine, left.id, right.id)
    only_right = side_rows(engine, right.id, left.id)

    left_ids = {i: r for r in only_left for i in (row_identity(r),) if i is not None}
    right_ids = {i: r for r in only_right for i in (row_identity(r),) if i is not None}
    # Grade only when identities actually cover both sides; a producer that emits no `id`
    # degrades to hash-only and says so, rather than reporting every change as new.
    graded = bool(only_right) and len(right_ids) == len(only_right)

    if graded:
        new_ids = [i for i in right_ids if i not in left_ids]
        gone_ids = [i for i in left_ids if i not in right_ids]
        changed_ids = [i for i in right_ids if i in left_ids]
        return DiffResult(
            kind=kind,
            status="OK",
            snapshots=len(refs),
            left=left,
            right=right,
            graded=True,
            added=len(new_ids),
            removed=len(gone_ids),
            changed=len(changed_ids),
            added_rows=tuple(right_ids[i] for i in new_ids[:limit]),
            changed_rows=tuple(right_ids[i] for i in changed_ids[:limit]),
        )
    return DiffResult(
        kind=kind,
        status="OK",
        snapshots=len(refs),
        left=left,
        right=right,
        graded=False,
        added=len(only_right),
        removed=len(only_left),
        added_rows=tuple(only_right[:limit]),
    )


@dataclasses.dataclass(frozen=True)
class StoreArgs:
    """Typed argument surface. The dataclass IS the parser: `gbargs.build_parser` derives flags,
    types and defaults from these fields, so a flag cannot drift from the value it fills."""

    command: str = gbargs.arg(
        default="stats",
        help="ingest | diff | stats | compact | selftest",
        choices=("ingest", "diff", "stats", "compact", "selftest"),
        positional=True,
    )
    kind: str = gbargs.arg(default="sources", help="producer root to diff")
    engine: str = gbargs.arg(
        default="auto", help="storage engine", choices=("auto", "fsqlite", "sqlite3")
    )
    db: Optional[str] = gbargs.arg(
        default=None, help="store path; default state/store.<engine>.db"
    )
    limit: int = gbargs.arg(default=0, help="max files to ingest (0 = all)")
    json_out: bool = gbargs.arg(
        default=False, help="machine-readable envelope on stdout"
    )


def body() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    parser = gbargs.build_parser(
        StoreArgs, prog="gb-store", description=(__doc__ or "").splitlines()[0]
    )
    ns = parser.parse_args()
    command = str(ns.command)

    if command == "selftest":
        return selftest()

    engine_choice = str(ns.engine)
    label = engine_choice if engine_choice != "auto" else "fsqlite"
    db = pathlib.Path(ns.db) if ns.db else root / "state" / f"store.{label}.db"
    as_json = bool(getattr(ns, "json_out", False))
    try:
        engine = open_engine(db, engine_choice)
    except EngineError as exc:
        print(f"engine unavailable: {exc}", file=sys.stderr)
        return Exit.ENGINE
    try:
        if command == "ingest":
            render(
                cmd_ingest(engine, root, INGEST_ROOTS, int(ns.limit)).as_json(), as_json
            )
        elif command == "diff":
            render(cmd_diff(engine, str(ns.kind), None, None).as_json(), as_json)
        elif command == "compact":
            result = cmd_compact(engine, db)
            render(result, as_json)
            if result.get("verified") is not True:
                return Exit.FINDINGS
        else:
            render(cmd_stats(engine, db, root).as_json(), as_json)
    except EngineError as exc:
        print(f"{engine.name}: {exc}", file=sys.stderr)
        return Exit.ENGINE
    finally:
        engine.close()
    return Exit.OK


def load_json_text(text: str) -> Optional[JsonValue]:
    """Second and last untyped boundary: a blob's stored JSON coming back out of the engine."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return cast(JsonValue, parsed)


# ---------------------------------------------------------------- selftest


def selftest() -> int:
    fails: List[str] = []
    checks = 0

    def check(ok: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            fails.append(label)

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "usecases").mkdir()
        first: List[JsonObj] = [{"name": "A", "v": 1}, {"name": "B", "v": 1}]
        second: List[JsonObj] = [*first, {"name": "C", "v": 1}]
        gbtypes.atomic_write_text(
            tmp / "usecases" / "2026-01-01T0000.json", json.dumps({"rows": first})
        )
        gbtypes.atomic_write_text(
            tmp / "usecases" / "2026-01-02T0000.json", json.dumps({"rows": second})
        )

        engines = ["sqlite3"] + (["fsqlite"] if shutil.which("fsqlite") else [])
        for want in engines:
            tag = f"[{want}]"
            engine = open_engine(tmp / f"store-{want}.db", want)
            try:
                st = cmd_ingest(engine, tmp, ("usecases",))
                check(
                    st.row_instances == 5,
                    f"{tag} row_instances={st.row_instances} want 5",
                )
                check(st.unique_rows == 3, f"{tag} unique_rows={st.unique_rows} want 3")

                diff = cmd_diff(engine, "usecases", None, None)
                check(diff.added == 1, f"{tag} added={diff.added} want 1")
                check(diff.removed == 0, f"{tag} removed={diff.removed} want 0")
                head = as_obj(diff.added_rows[0]) if diff.added_rows else None
                check(
                    head is not None and head.get("name") == "C",
                    f"{tag} added row is not C",
                )

                again = cmd_ingest(engine, tmp, ("usecases",))
                check(again.files_ingested == 0, f"{tag} re-ingest was not a no-op")

                (tmp / "market").mkdir(exist_ok=True)
                gbtypes.atomic_write_text(
                    tmp / "market" / "2026-01-01T0000.json",
                    json.dumps({"rows": [{"x": 1}]}),
                )
                cmd_ingest(engine, tmp, ("market",))
                lone = cmd_diff(engine, "market", None, None)
                check(
                    lone.status == "INSUFFICIENT",
                    f"{tag} single snapshot did not refuse",
                )

                # identity grading: a row whose body changed but whose id did not is CHANGED,
                # never one deletion plus one insertion. This is the measured GitHub case:
                # 287/452 repos are pushed within a day, so an ungraded diff would have
                # reported ~250 added and ~250 removed for repos that only got a commit.
                (tmp / "github").mkdir(exist_ok=True)
                day1: List[JsonObj] = [
                    {
                        "id": "aaa",
                        "title": "repo A",
                        "signals": {"pushed_at": "2026-09-10"},
                    },
                    {
                        "id": "bbb",
                        "title": "repo B",
                        "signals": {"pushed_at": "2026-09-10"},
                    },
                ]
                day2: List[JsonObj] = [
                    # aaa got a push: same identity, different body -> CHANGED
                    {
                        "id": "aaa",
                        "title": "repo A",
                        "signals": {"pushed_at": "2026-09-11"},
                    },
                    {
                        "id": "bbb",
                        "title": "repo B",
                        "signals": {"pushed_at": "2026-09-10"},
                    },
                    # ccc is genuinely new -> ADDED
                    {
                        "id": "ccc",
                        "title": "repo C",
                        "signals": {"pushed_at": "2026-09-11"},
                    },
                ]
                gbtypes.atomic_write_text(
                    tmp / "github" / "2026-09-10T0000.json", json.dumps({"rows": day1})
                )
                gbtypes.atomic_write_text(
                    tmp / "github" / "2026-09-11T0000.json", json.dumps({"rows": day2})
                )
                cmd_ingest(engine, tmp, ("github",))
                gd = cmd_diff(engine, "github", None, None)
                check(gd.graded, f"{tag} github diff was not graded by identity")
                check(
                    gd.added == 1,
                    f"{tag} graded added={gd.added} want 1 (only ccc is new)",
                )
                check(
                    gd.changed == 1,
                    f"{tag} graded changed={gd.changed} want 1 (aaa moved)",
                )
                check(gd.removed == 0, f"{tag} graded removed={gd.removed} want 0")
                # fires-on-known-bad: ungraded, the SAME data reads as 2 added + 1 removed.
                # If that is not true, the grading leg above is proving nothing.
                naive_added = (
                    len(side_rows(engine, gd.right.id, gd.left.id))
                    if gd.right and gd.left
                    else 0
                )
                check(
                    naive_added == 2,
                    f"{tag} known-bad: ungraded added={naive_added} want 2, so grading is vacuous",
                )
            finally:
                engine.close()

    # volatile handling - the property the daily cadence rests on
    r1: JsonObj = {"id": "x", "signals": {"age_days": 1.0, "stars": 5}}
    r2: JsonObj = {"id": "x", "signals": {"age_days": 2.0, "stars": 5}}
    check(
        rowhash(r1, ["signals.age_days"]) == rowhash(r2, ["signals.age_days"]),
        "volatile strip: age_days still changes the hash",
    )
    # fires-on-known-bad: unstripped rows MUST differ, else the leg above proves nothing
    check(
        rowhash(r1, []) != rowhash(r2, []),
        "known-bad: unstripped rows hashed equal, so the strip leg is vacuous",
    )
    check(
        rowhash({"id": "y"}, ["nope.missing"]) == rowhash({"id": "y"}, []),
        "an absent volatile path changed the hash",
    )
    check(
        volatile_of({"volatile_fields": ["a.b"]}) == ["a.b"] and volatile_of({}) == [],
        "volatile_of did not read the document's own declaration",
    )
    # a nested strip must not mutate the caller's row
    original: JsonObj = {"signals": {"age_days": 1.0}}
    stable_row(original, ["signals.age_days"])
    nested = as_obj(original.get("signals"))
    check(
        nested is not None and "age_days" in nested,
        "strip_path mutated the caller's row",
    )

    for failure in fails:
        print(f"FAIL: {failure}")
    print(
        f"SELFTEST {'FAIL' if fails else 'PASS'} - {checks - len(fails)}/{checks} properties"
    )
    return Exit.FINDINGS if fails else Exit.OK


# ---------------------------------------------------------------- cli


def render(payload: JsonObj, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"schema": "gb-store/2", "result": payload}, indent=1))
        return
    for key, value in payload.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            print(f"{key}:")
            for item in value[:12]:
                print("  " + json.dumps(item, default=str)[:118])
        elif isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, default=str)}")
        else:
            print(f"{key}: {value}")


if __name__ == "__main__":
    gbtypes.main(body)
