#!/usr/bin/env python3
"""gb-github — the DAILY GitHub ecosystem read for the Grok Bot deployment.

`gb-discover.py` answers "what repo appeared that is not on our watch list", writes only the NEW
rows, and is wired to the weekly board. This answers a different question, every day: what is the
current state of the ecosystem a Grok Bot actually plugs into — the repos naming the platform, the
Bot templates, and the MCP servers a Bot could be pointed at — with `pushed_at` carried through so
the DIFF between two consecutive days is computable by a content-addressed store rather than by a
human reading two lists.

Measured 2026-09-11 on the first run of this file: `topic:mcp-server pushed:>=<14d ago>` matched
7856 repositories. That is the churn one weekly poll was sampling once. A daily read of the top of
that list is the cheapest way to see a connector candidate the day it lands.

Search terms are tuned to the PRODUCT — the xAI/Cursor "Grok Bot" agent platform, the `sand`
client, Bot templates, and MCP servers a Bot can connect to. They are deliberately NOT tuned to
the unrelated `grok` command-line tool, which shares the name and would otherwise flood every row.

Authentication is the `gh` CLI, shelled out to through `gbtypes.run` (stdlib subprocess with a
mandatory deadline). No third-party GitHub library, because `packaging/pyproject.toml` declares
`dependencies = []` and `bin/gb-export-public.py` mechanically verifies that claim.

What this structurally CANNOT see: private repos, anything published under a name none of the
eight queries contains, and any repo GitHub's search index has not yet picked up. Those are not
reported as absence — see `reliable` below.

Three rules this file will not bend, all of them consequences of AGENTS.md's evidence discipline:

  (1) A non-200 is a recorded FACT. `gh` missing, `gh` unauthenticated, a query erroring, and the
      search quota running out each land in `sources[]` with a status, and the run still writes an
      artifact. A refusal recorded is evidence; a refusal silently rendered as `"rows": []` is a
      lie that reads as "the ecosystem was quiet today".
  (2) An empty result is not absence until a POSITIVE CONTROL passes. One of the eight queries
      (`repo:modelcontextprotocol/servers`) must return exactly one row. If the control returns
      zero, the search path is broken, `reliable` goes false, and NO zero-row query in that
      artifact may be read as "nothing exists".
  (3) The daily budget is bounded before it is spent. `gh api rate_limit` is read FIRST (it is a
      free endpoint), the observed search quota is recorded, and the run stops with
      `status: "rate_limited"` rather than hammering a 30-per-minute limit.

  gb-github.py collect          # write github/<stamp>.json, print the summary
  gb-github.py collect --json   # the document on stdout
  gb-github.py collect --dry-run
  gb-github.py --selftest       # inline fixtures, no network
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
import time
from typing import Any, Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import Cancelled, atomic_write_json, main, run  # noqa: E402

SCHEMA = "gb-github/1"

# `signals.age_days` is derived from the run clock, so it moves every day for a repo that has not
# changed. `signals.pushed_at` is the stable fact it is derived from. A downstream store hashing a
# row BODY to detect change must strip the fields named here first, or every unchanged repo looks
# like a new row tomorrow. Named in the document so the store does not have to guess.
VOLATILE_FIELDS = ["signals.age_days"]

CHURN_DAYS = 14  # "recently pushed" for the ecosystem queries
TEMPLATE_DAYS = 30  # templates move slower than servers do
PER_PAGE = 50
MAX_PAGES = 3  # hard cap per query — this runs daily, it must stay cheap
QUOTA_RESERVE = 3  # leave this much search quota for whatever runs after us
SLEEP_S = 1.5  # politeness between search calls; 12 calls ≈ 18s
GH_TIMEOUT_S = 25.0

# Each query states what it is FOR, because a query nobody can justify is noise, and noise trains
# the reader to skip the whole artifact. `kind` is what a row from this query IS, absent a topic
# that says otherwise. `census` queries pull one item purely to record `total_count` — the size of
# a firehose we deliberately do not drink.
QUERIES: tuple[dict[str, Any], ...] = (
    {
        "label": "grok-bot-recent",
        "q": "grok bot pushed:>={d14}",
        "sort": "updated",
        "pages": 2,
        "kind": "repo",
        "why": "anything naming the platform that moved in the last two weeks",
    },
    {
        "label": "grokbot-stars",
        "q": "grokbot",
        "sort": "stars",
        "pages": 1,
        "kind": "repo",
        "why": "the unhyphenated spelling, by stars — the shape of the known ecosystem",
    },
    {
        "label": "grok-bot-mcp",
        "q": '"grok bot" mcp',
        "sort": "updated",
        "pages": 1,
        "kind": "repo",
        "why": "Bots already wired to MCP: the integration pattern to copy",
    },
    # MEASURED NOISE, 2026-09-11 first run: 49 rows, of which roughly two were about this
    # product (`carllippert/lots_of_agents`). The rest are generic Telegram/Discord bot bridges
    # that happen to contain the three words. Kept for one more day of evidence rather than
    # deleted on a single sample; if the second run scores the same, prune it — a query whose
    # rows nobody reads is what teaches a reader to skip the artifact.
    {
        "label": "cursor-bot-agent",
        "q": "cursor bot agent pushed:>={d14}",
        "sort": "updated",
        "pages": 1,
        "kind": "repo",
        "why": "the Cursor-side agent surface this product is descended from",
    },
    {
        "label": "bot-templates",
        "q": "grok bot template in:name,description pushed:>={d30}",
        "sort": "updated",
        "pages": 1,
        "kind": "repo",
        "why": "publishable Bot templates — the thing the marketplace listing competes with",
    },
    {
        "label": "mcp-server-notable",
        "q": "topic:mcp-server pushed:>={d14} stars:>=25",
        "sort": "updated",
        "pages": MAX_PAGES,
        "kind": "mcp-server",
        "why": "connector candidates: an MCP server a Bot could be pointed at tomorrow",
    },
    {
        "label": "mcp-protocol-topic",
        "q": "topic:modelcontextprotocol pushed:>={d14} stars:>=10",
        "sort": "updated",
        "pages": 2,
        "kind": "mcp-server",
        "why": "the other topic spelling; authors pick one and never both",
    },
    {
        "label": "mcp-server-census",
        "q": "topic:mcp-server pushed:>={d14}",
        "sort": "updated",
        "pages": 1,
        "kind": "mcp-server",
        "census": True,
        "why": "magnitude only: how much of this surface churned, without pulling the tail",
    },
)

# The positive control. It is not one of the eight ecosystem reads — it is the proof that the read
# path works at all, and it must match. `modelcontextprotocol/servers` is the reference MCP server
# collection; if a `repo:` lookup of it returns zero, search is broken, not empty.
CONTROL = {
    "label": "positive-control",
    "q": "repo:modelcontextprotocol/servers",
    "sort": "updated",
    "pages": 1,
    "kind": "mcp-server",
    "control": True,
    "why": "MUST match — an empty result here invalidates every zero in this artifact",
}

MCP_TOPICS = {
    "mcp-server",
    "modelcontextprotocol",
    "model-context-protocol",
    "mcp-servers",
}


# -------------------------------------------------------------------------------------------
# pure helpers — everything below this line is proven by --selftest without a network
# -------------------------------------------------------------------------------------------
def stable_id(full_name: str) -> str:
    """The content id for a repository row, stable across runs and independent of every field
    that moves. Case-folded because GitHub treats `Owner/Repo` and `owner/repo` as one repo and
    search has been observed to return either casing."""
    canonical = f"https://github.com/{(full_name or '').strip().strip('/').lower()}"
    return hashlib.blake2b(canonical.encode("utf-8"), digest_size=16).hexdigest()


def parse_iso(value: Optional[str]) -> Optional[dt.datetime]:
    """GitHub's `...Z` timestamps. Anything unparseable answers None rather than raising — a weird
    date on one row must not abort a run that has already spent quota."""
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_days(pushed_at: Optional[str], now: dt.datetime) -> Optional[int]:
    """Whole days since the last push. This is the daily churn signal: 0 means it moved today."""
    when = parse_iso(pushed_at)
    if when is None:
        return None
    return (now.date() - when.date()).days


def classify(item: dict[str, Any], default_kind: str) -> str:
    """A repo that tags itself an MCP server IS one, whichever query found it. Only the two
    unambiguous topics promote — `mcp` alone is worn by clients, docs and blog posts too."""
    topics = {str(t).lower() for t in (item.get("topics") or [])}
    return "mcp-server" if topics & MCP_TOPICS else default_kind


def repo_row(
    item: dict[str, Any], kind: str, now: dt.datetime
) -> Optional[dict[str, Any]]:
    """One search item, reduced to the shared producer contract. None when the item carries no
    `full_name` — without it there is no stable id, and a row with an unstable id is worse than
    no row: it re-appears as "new" every single day."""
    full = (item.get("full_name") or "").strip()
    if not full:
        return None
    lic = item.get("license") or {}
    pushed = item.get("pushed_at")
    return {
        "id": stable_id(full),
        "source": item.get("html_url") or f"https://github.com/{full}",
        "title": full,
        "kind": classify(item, kind),
        "published_at": pushed,
        "summary": (item.get("description") or "").strip()[:240],
        "signals": {
            "stars": int(item.get("stargazers_count") or 0),
            "forks": int(item.get("forks_count") or 0),
            "open_issues": int(item.get("open_issues_count") or 0),
            "pushed_at": pushed,
            "created_at": item.get("created_at"),
            "age_days": age_days(pushed, now),
            "topics": sorted(str(t) for t in (item.get("topics") or []))[:12],
            "license": (lic.get("spdx_id") or lic.get("key"))
            if isinstance(lic, dict)
            else None,
            "language": item.get("language"),
            "archived": bool(item.get("archived")),
            "fork": bool(item.get("fork")),
            "queries": [],
        },
    }


def merge(rows: dict[str, dict[str, Any]], row: dict[str, Any], label: str) -> bool:
    """De-duplicate by stable id, keeping the richer classification and recording every query that
    found the repo — a repo three queries agree on is a stronger signal than one that squeaked in.
    Returns True when the row was new to this run."""
    existing = rows.get(row["id"])
    if existing is None:
        row["signals"]["queries"] = [label]
        rows[row["id"]] = row
        return True
    if label not in existing["signals"]["queries"]:
        existing["signals"]["queries"].append(label)
    if row["kind"] == "mcp-server":
        existing["kind"] = "mcp-server"
    # Keep the freshest numbers: two queries can return the same repo minutes apart.
    if row["signals"]["stars"] > existing["signals"]["stars"]:
        existing["signals"]["stars"] = row["signals"]["stars"]
    return False


def stamp(now: dt.datetime) -> str:
    return f"{now:%Y-%m-%dT%H%M}"


def iso(now: dt.datetime) -> str:
    return (
        now.astimezone(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# The writer detector. It exists so the "collect cannot write" invariant is CHECKED rather than
# claimed in a docstring, and it lives at module scope (not inside `selftest`) so it can itself
# be fired on known-bad source. `os.replace`/`str.replace` are deliberately NOT writers here: a
# rule that fires on correct code teaches people to add exemptions instead of keeping the
# property. (Detector shape cross-checked with FeedsAndPractitionerMiner and VendorDocsMiner,
# who run the same guard over their own producers.)
WRITER_CALLS = {
    "atomic_write_json",
    "atomic_write_text",
    "atomic_write_bytes",
    "write_text",
    "write_bytes",
}


def writer_functions(tree: ast.Module) -> set[str]:
    """Names of the module-level functions in `tree` that can touch the filesystem.

    Catches three shapes, because "one writer" is only true of the writers you thought of:
    the sanctioned atomic primitives, a raw `pathlib` write, and `open(..., 'w')` — with or
    without a `json.dump` behind it."""
    found = set()
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            base = getattr(getattr(func, "value", None), "id", "")
            writes_open = name == "open" and any(
                isinstance(a, ast.Constant)
                and isinstance(a.value, str)
                and "w" in a.value
                for a in node.args
            )
            if (
                name in WRITER_CALLS
                or (name == "dump" and base == "json")
                or writes_open
            ):
                found.add(fn.name)
                break
    return found


def returns_in_pipe_guard(fn: ast.FunctionDef) -> set[int]:
    """ids of the `return` statements that ARE a pipe guard: those lexically inside an
    `except BrokenPipeError` HANDLER, not merely inside the protected `try` body.

    The distinction is the whole point and it is easy to invert. A write is guarded when it sits
    in the TRY BODY; a return is a guard when it sits in the HANDLER. A predicate that confuses
    the two reports correct code as broken (or the reverse) with equal confidence, so
    `selftest` fires it on a synthetic two-return probe every run rather than trusting it.
    (Inversion hazard hit for real on 2026-09-11 in an ad-hoc scan of this very file, and
    independently guarded against by VendorDocsMiner with the same shape of probe.)"""
    found = set()
    for handler in ast.walk(fn):
        if (
            isinstance(handler, ast.ExceptHandler)
            and getattr(handler.type, "id", "") == "BrokenPipeError"
        ):
            for node in ast.walk(handler):
                if isinstance(node, ast.Return):
                    found.add(id(node))
    return found


def recover_cancellation(exc: BaseException) -> Optional[int]:
    """The exit code a cancellation was ABOUT to report, when its own report broke the pipe.

    Returns None \u2014 meaning "re-raise, I do not know" \u2014 for every other BrokenPipeError. That
    asymmetry is the point: the only honest recovery is one that reads the code off the
    `Cancelled` instance actually being reported (`__context__`), never one that guesses 130
    because a pipe happened to break. A bare EPIPE, or one raised while handling something
    else, recovers nothing.

    Separated from the `__main__` block purely so `selftest` can construct the exact exception
    shape and prove all three branches without sending a signal."""
    context = exc.__context__
    return context.exit_code if isinstance(context, Cancelled) else None


# -------------------------------------------------------------------------------------------
# the network edge — one function, so --selftest can replace exactly one thing
# -------------------------------------------------------------------------------------------
def gh_json(argv: list[str]) -> tuple[Optional[Any], Optional[str], int]:
    """`gh <argv>` as parsed JSON. Returns (document, error, bytes). Never raises: a missing
    binary, an unauthenticated host, a timeout and a malformed body are all ERRORS TO RECORD, and
    a producer that dies on one of them writes no artifact and therefore reports nothing."""
    try:
        proc = run(["gh", *argv], timeout_s=GH_TIMEOUT_S)
    except FileNotFoundError:
        return None, "gh-absent: the GitHub CLI is not on PATH", 0
    except OSError as exc:  # permission, fork failure — still a recordable refusal
        return None, f"gh-unrunnable: {type(exc).__name__}: {exc}", 0
    nbytes = len(proc.out.encode("utf-8", "replace"))
    if proc.timed_out:
        return None, f"timeout after {GH_TIMEOUT_S}s", nbytes
    if proc.code != 0:
        err = (proc.err or "").strip().replace("\n", " ")
        tag = (
            "gh-unauthenticated" if "auth login" in err or "401" in err else "gh-error"
        )
        return None, f"{tag}: {err[:220]}", nbytes
    try:
        return json.loads(proc.out or "null"), None, nbytes
    except json.JSONDecodeError as exc:
        return None, f"unparseable-json: {exc}", nbytes


Fetch = Callable[[list[str]], "tuple[Optional[Any], Optional[str], int]"]


def probe_rate_limit(fetch: Fetch, when: str) -> tuple[dict[str, Any], Optional[int]]:
    """Read the quota BEFORE spending any of it. `rate_limit` is itself free, so this costs
    nothing and is the only honest way to write a number into `sources[]`.

    INSTRUMENT TRAP, measured 2026-09-11: this endpoint's `search` bucket did NOT move. Before
    the run it read `remaining 30 / used 0`; after 13 search requests in 29s it read
    `remaining 30 / used 0` again, and a control (one search call, then an immediate probe) also
    read `used 0`. So the vendor's number is the vendor's CLAIM, recorded as such, and it is NOT
    what enforces the budget — `budget.search_requests_spent`, counted locally on every request
    this process makes, is. A guard that trusted `remaining` here would never fire, which is the
    worst kind of guard: one that reports itself green because its instrument is stuck."""
    doc, err, nbytes = fetch(["api", "rate_limit"])
    row: dict[str, Any] = {
        "url": "gh api rate_limit",
        "kind": "rate-limit-probe",
        "when": when,
        "bytes": nbytes,
        "fetched_at": iso(dt.datetime.now(dt.timezone.utc)),
    }
    if err or not isinstance(doc, dict):
        row["status"] = err or "unexpected-shape"
        return row, None
    res = (doc.get("resources") or {}) if isinstance(doc.get("resources"), dict) else {}
    search = res.get("search") or {}
    core = res.get("core") or {}
    row["status"] = 200
    row["search_limit"] = search.get("limit")
    row["search_remaining"] = search.get("remaining")
    row["search_used"] = search.get("used")
    row["search_reset"] = search.get("reset")
    row["core_remaining"] = core.get("remaining")
    remaining = search.get("remaining")
    return row, remaining if isinstance(remaining, int) else None


def search_page(
    fetch: Fetch, q: str, sort: str, page: int, per_page: int
) -> tuple[Optional[Any], Optional[str], int]:
    return fetch(
        [
            "api",
            "-X",
            "GET",
            "search/repositories",
            "-f",
            f"q={q}",
            "-f",
            f"sort={sort}",
            "-f",
            "order=desc",
            "-f",
            f"per_page={per_page}",
            "-f",
            f"page={page}",
        ]
    )


def run_query(
    fetch: Fetch,
    spec: dict[str, Any],
    *,
    now: dt.datetime,
    rows: dict[str, dict[str, Any]],
    budget: dict[str, Any],
    sleep_s: float,
) -> dict[str, Any]:
    """One query, at most `spec['pages']` pages, always bounded by the remaining quota.

    Returns the `sources[]` row. Rows are merged into `rows` in place: the caller owns the
    accumulation across queries so de-duplication happens once."""
    since14 = (now.date() - dt.timedelta(days=CHURN_DAYS)).isoformat()
    since30 = (now.date() - dt.timedelta(days=TEMPLATE_DAYS)).isoformat()
    q = spec["q"].format(d14=since14, d30=since30)
    census = bool(spec.get("census"))
    per_page = 1 if census else PER_PAGE
    src: dict[str, Any] = {
        "url": f"gh api search/repositories?q={q}&sort={spec['sort']}",
        "query": spec["label"],
        "why": spec["why"],
        "kind": "census" if census else "search",
        "pages_requested": min(int(spec.get("pages", 1)), MAX_PAGES),
        "pages_fetched": 0,
        "bytes": 0,
        "items_returned": 0,
        "new_rows": 0,
        "fetched_at": iso(now),
    }
    if spec.get("control"):
        src["positive_control"] = True

    total_new = 0
    for page in range(1, min(int(spec.get("pages", 1)), MAX_PAGES) + 1):
        if budget.get("remaining") is not None and budget["remaining"] <= QUOTA_RESERVE:
            budget["stopped"] = True
            src["status"] = "rate_limited"
            src["note"] = (
                f"stopped before page {page}: search quota at {budget['remaining']}, "
                f"reserve is {QUOTA_RESERVE}"
            )
            return src
        if src["pages_fetched"] and sleep_s:
            time.sleep(sleep_s)
        doc, err, nbytes = search_page(fetch, q, spec["sort"], page, per_page)
        budget["spent"] = budget.get("spent", 0) + 1
        if budget.get("remaining") is not None:
            budget["remaining"] -= 1
        src["bytes"] += nbytes
        if err or not isinstance(doc, dict):
            src["status"] = err or "unexpected-shape"
            src["pages_fetched"] = page - 1
            return src
        src["pages_fetched"] = page
        src["status"] = 200
        src["total_count"] = doc.get("total_count")
        src["incomplete_results"] = bool(doc.get("incomplete_results"))
        items = doc.get("items") or []
        src["items_returned"] += len(items)
        if not census:
            for item in items:
                row = repo_row(item, spec["kind"], now)
                if row is None:
                    continue
                if merge(rows, row, spec["label"]):
                    total_new += 1
        if len(items) < per_page:
            break

    src["new_rows"] = total_new
    return src


def collect(
    *,
    fetch: Fetch = gh_json,
    now: Optional[dt.datetime] = None,
    sleep_s: float = SLEEP_S,
) -> dict[str, Any]:
    """The whole daily read. Always returns a document; never raises on a dead source.

    WRITE-FREE BY CONSTRUCTION, not by flag. This function cannot touch the filesystem: it takes
    no root and calls no writer, so an importer cannot get a boolean wrong and have a READ leave
    an artifact behind as a side effect. `write_artifact` is the only writer and `main` is its
    only caller. (Adopted 2026-09-11 from VendorDocsMiner's `collect(...) -> (payload, problems)`
    after he pointed out that my `write: bool = True` was a default a store could forget.)"""
    now = now or dt.datetime.now(dt.timezone.utc)
    sources: list[dict[str, Any]] = []
    rows: dict[str, dict[str, Any]] = {}

    pre, remaining = probe_rate_limit(fetch, "before")
    sources.append(pre)
    budget = {"remaining": remaining, "spent": 0, "stopped": False}

    refused = pre["status"] != 200
    control_row: Optional[dict[str, Any]] = None
    if refused:
        # `gh` could not answer a FREE endpoint. Every search would fail the same way, so spend
        # nothing and say so. Zero rows here means "we were refused", and the artifact says which.
        pass
    else:
        control_row = run_query(
            fetch, CONTROL, now=now, rows={}, budget=budget, sleep_s=sleep_s
        )
        sources.append(control_row)
        for spec in QUERIES:
            sources.append(
                run_query(
                    fetch, spec, now=now, rows=rows, budget=budget, sleep_s=sleep_s
                )
            )
        post, _ = probe_rate_limit(fetch, "after")
        sources.append(post)

    control_ok = bool(
        control_row
        and control_row.get("status") == 200
        and (control_row.get("items_returned") or 0) >= 1
    )
    ordered = sorted(
        rows.values(),
        key=lambda r: (
            r["signals"]["age_days"] if r["signals"]["age_days"] is not None else 10**6,
            -r["signals"]["stars"],
        ),
    )
    kinds: dict[str, int] = {}
    for r in ordered:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1

    doc = {
        "schema": SCHEMA,
        "captured_at": iso(now),
        "volatile_fields": VOLATILE_FIELDS,
        # The whole point of rule (2): a consumer may only read a zero as absence when this is
        # true. False means the search path itself is suspect, whatever the row count says.
        "reliable": control_ok and not refused,
        "control": {
            "query": CONTROL["q"],
            "must_match": True,
            "matched": control_ok,
            "status": (control_row or {}).get("status") if control_row else "not-run",
        },
        "budget": {
            "search_remaining_before": remaining,
            "search_requests_spent": budget["spent"],
            "stopped_on_quota": budget["stopped"],
            "reserve": QUOTA_RESERVE,
            # Which of the two numbers above is load-bearing, said out loud in the artifact so a
            # reader does not reason from the vendor's stuck counter. See `probe_rate_limit`.
            "enforced_by": "search_requests_spent (local); the API's remaining was observed stuck",
        },
        "window_days": {"churn": CHURN_DAYS, "templates": TEMPLATE_DAYS},
        "counts": {
            "rows": len(ordered),
            "by_kind": kinds,
            "pushed_today": sum(1 for r in ordered if r["signals"]["age_days"] == 0),
            "queries_ok": sum(1 for s in sources if s.get("status") == 200),
            "queries_failed": sum(
                1 for s in sources if s.get("status") not in (200, None)
            ),
        },
        "sources": sources,
        "rows": ordered,
    }
    return doc


def write_artifact(root: pathlib.Path, doc: dict[str, Any]) -> pathlib.Path:
    """The ONLY writer in this file. Named separately from `collect` so that reading the
    ecosystem and recording it are two decisions a caller makes on purpose, one of which a
    library consumer never has to make."""
    when = parse_iso(doc["captured_at"]) or dt.datetime.now(dt.timezone.utc)
    out = root / "github" / f"{stamp(when)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out, doc)
    return out


def report(doc: dict[str, Any], artifact: Optional[pathlib.Path] = None) -> None:
    c = doc["counts"]
    art = artifact.name if artifact else "(not written)"
    flag = (
        "" if doc["reliable"] else "  ** UNRELIABLE: positive control did not match **"
    )
    print(
        f"github {art}: {c['rows']} row(s) "
        f"({', '.join(f'{k}={v}' for k, v in sorted(c['by_kind'].items())) or 'none'}), "
        f"{c['pushed_today']} pushed today, {c['queries_failed']} source failure(s){flag}"
    )
    for s in doc["sources"]:
        if s.get("kind") == "rate-limit-probe":
            print(
                f"  quota {s['when']:<6} status={s['status']} "
                f"search={s.get('search_remaining')}/{s.get('search_limit')} "
                f"core={s.get('core_remaining')}"
            )
        else:
            mark = "  " if s.get("status") == 200 else "!!"
            print(
                f" {mark} {s.get('query', '?'):<20} status={s.get('status')} "
                f"total={s.get('total_count')} items={s.get('items_returned')} "
                f"new={s.get('new_rows')} pages={s.get('pages_fetched')}"
            )
    for r in doc["rows"][:15]:
        age = r["signals"]["age_days"]
        print(
            f"    {r['signals']['stars']:>6}★ {('d+' + str(age)) if age is not None else 'd?':>5}"
            f"  {r['kind']:<11} {r['title']:<44} {r['summary'][:60]}"
        )
    if len(doc["rows"]) > 15:
        print(f"    … {len(doc['rows']) - 15} more in {art}")


# -------------------------------------------------------------------------------------------
# --selftest: inline fixtures, no network, no writes to the artifact root
# -------------------------------------------------------------------------------------------
# A real `search/repositories` item, captured 2026-09-11 from
# `topic:mcp-server pushed:>=2026-08-28` and trimmed to the fields this producer reads. Pasted
# rather than mocked by hand so the parser is proven against the vendor's actual shape, including
# the two that bite: `license` is a nested object (and can be null), `topics` is a list that can
# be absent entirely.
FIXTURE_ITEM = {
    "id": 1304558838,
    "full_name": "kuhyx/i3wm-mcp",
    "html_url": "https://github.com/kuhyx/i3wm-mcp",
    "description": "MCP server for the i3/Sway window manager — 13 quality-tuned tools.",
    "fork": False,
    "created_at": "2026-07-18T03:15:28Z",
    "updated_at": "2026-09-11T16:49:16Z",
    "pushed_at": "2026-09-11T16:49:12Z",
    "stargazers_count": 0,
    "forks_count": 0,
    "open_issues_count": 0,
    "language": "Python",
    "license": {"key": "other", "name": "Other", "spdx_id": "NOASSERTION"},
    "topics": ["i3", "mcp", "mcp-server", "model-context-protocol", "sway"],
    "archived": False,
    "visibility": "public",
}
FIXTURE_GROK = {
    "id": 42,
    "full_name": "Acme/Grok-Bot-Starter",
    "html_url": "https://github.com/Acme/Grok-Bot-Starter",
    "description": "A starter template for a Grok Bot.",
    "fork": False,
    "created_at": "2026-01-02T00:00:00Z",
    "pushed_at": "2026-09-04T12:00:00Z",
    "stargazers_count": 7,
    "forks_count": 1,
    "open_issues_count": 0,
    "language": "TypeScript",
    "license": None,
    "archived": False,
}
FIXTURE_RATE = {
    "resources": {
        "core": {"limit": 5000, "used": 0, "remaining": 5000, "reset": 1789148884},
        "search": {"limit": 30, "used": 0, "remaining": 30, "reset": 1789145344},
    }
}
NOW = dt.datetime(2026, 9, 11, 18, 0, 0, tzinfo=dt.timezone.utc)


def fake_gh(
    *, rate: Any = FIXTURE_RATE, items: Any = None, fail: Optional[str] = None
) -> Fetch:
    """A `gh_json` stand-in. `fail` refuses everything, which is how the refusal leg is proven
    without uninstalling the CLI."""
    items = items if items is not None else [FIXTURE_ITEM, FIXTURE_GROK]

    def _fetch(argv: list[str]) -> tuple[Any, Optional[str], int]:
        if fail:
            return None, fail, 0
        if argv[:2] == ["api", "rate_limit"]:
            return rate, None, 120
        page = next((a.split("=", 1)[1] for a in argv if a.startswith("page=")), "1")
        if page != "1":
            return {"total_count": len(items), "items": []}, None, 20
        return (
            {"total_count": 7856, "incomplete_results": False, "items": items},
            None,
            900,
        )

    return _fetch


def selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    # 1 — the parser reads the vendor's real shape, including the nested license object.
    row = repo_row(FIXTURE_ITEM, "repo", NOW)
    check(
        "row-extraction",
        row is not None
        and row["title"] == "kuhyx/i3wm-mcp"
        and row["signals"]["license"] == "NOASSERTION"
        and row["signals"]["language"] == "Python"
        and row["summary"].startswith("MCP server for the i3/Sway"),
        f"{row}"[:90] if row else "None",
    )

    # 2 — a null license and absent topics must not raise. This is the crash the first draft had.
    grok = repo_row(FIXTURE_GROK, "repo", NOW)
    check(
        "null-license-and-missing-topics",
        grok is not None
        and grok["signals"]["license"] is None
        and grok["signals"]["topics"] == [],
    )

    # 3 — stable ids: same repo, two runs, different volatile fields, ONE id. This is the field
    # the whole daily-diff design rests on; if it moves, every repo looks new every day.
    tomorrow = dict(FIXTURE_ITEM, pushed_at="2026-09-12T09:00:00Z", stargazers_count=99)
    later = repo_row(tomorrow, "repo", NOW + dt.timedelta(days=1))
    check(
        "stable-id-across-runs",
        row is not None
        and later is not None
        and later["id"] == row["id"]
        and len(row["id"]) == 32,
        row["id"] if row is not None else "None",
    )
    # ... and case-folding, because search returns either casing for the same repo.
    check(
        "stable-id-case-folded",
        stable_id("Acme/Grok-Bot") == stable_id("acme/grok-bot"),
    )
    check(
        "distinct-ids-distinct-repos",
        stable_id("a/b") != stable_id("a/c"),
    )

    # 4 — age_days math, including the boundary a naive subtraction gets wrong.
    check("age-days-today", age_days("2026-09-11T16:49:12Z", NOW) == 0)
    check("age-days-week", age_days("2026-09-04T12:00:00Z", NOW) == 7)
    check("age-days-missing", age_days(None, NOW) is None)
    check("age-days-garbage", age_days("not-a-date", NOW) is None)

    # 5 — a repo that tags itself an MCP server is one, whichever query found it; `mcp` alone
    #     is not enough, because clients and blog posts wear that topic too.
    check(
        "classify-promotes-mcp-server", classify(FIXTURE_ITEM, "repo") == "mcp-server"
    )
    check(
        "classify-keeps-default", classify({"topics": ["mcp", "cli"]}, "repo") == "repo"
    )

    # 6 — de-duplication by id across two queries yields ONE row carrying both query labels.
    acc: dict[str, dict[str, Any]] = {}
    _first = repo_row(FIXTURE_ITEM, "repo", NOW)
    _second = repo_row(FIXTURE_ITEM, "mcp-server", NOW)
    assert _first is not None and _second is not None
    merge(acc, _first, "q1")
    was_new = merge(acc, _second, "q2")
    only = next(iter(acc.values()))
    check(
        "dedup-by-id",
        len(acc) == 1
        and was_new is False
        and only["signals"]["queries"] == ["q1", "q2"],
        f"{len(acc)} row(s)",
    )
    check("dedup-upgrades-kind", only["kind"] == "mcp-server")

    # 7 — a whole run against the fixture: envelope, contract fields, control. `collect` takes
    #     no root and no write flag, so "no writes" is structural here, not a setting.
    doc = collect(fetch=fake_gh(), now=NOW, sleep_s=0)
    check(
        "envelope-contract",
        doc["schema"] == SCHEMA
        and doc["captured_at"] == "2026-09-11T18:00:00Z"
        and isinstance(doc["sources"], list)
        and isinstance(doc["rows"], list),
    )
    check("run-produces-rows", len(doc["rows"]) == 2, f"{len(doc['rows'])} rows")
    check("run-is-reliable-when-control-matches", doc["reliable"] is True)
    check(
        "census-query-pulls-no-rows",
        any(
            s.get("query") == "mcp-server-census" and s.get("total_count") == 7856
            for s in doc["sources"]
        ),
    )
    check(
        "rows-carry-no-fetch-timestamp",
        all("fetched_at" not in r and "captured_at" not in r for r in doc["rows"]),
    )
    check("volatile-fields-declared", doc["volatile_fields"] == ["signals.age_days"])
    check(
        "stamp-format",
        stamp(NOW) == "2026-09-11T1800" and len(stamp(NOW)) == 15,
        stamp(NOW),
    )

    # 8 — the refusal leg. `gh` absent/unauthenticated must produce a RECORDED refusal, zero
    #     rows, an unreliable verdict, and zero spent quota — never a silent empty ecosystem.
    dead = collect(
        fetch=fake_gh(fail="gh-absent: the GitHub CLI is not on PATH"),
        now=NOW,
        sleep_s=0,
    )
    check("refusal-zero-rows", dead["rows"] == [])
    check("refusal-is-recorded", "gh-absent" in str(dead["sources"][0]["status"]))
    check("refusal-is-unreliable", dead["reliable"] is False)
    check("refusal-spends-no-quota", dead["budget"]["search_requests_spent"] == 0)

    # 9 — an exhausted quota stops the run rather than hammering, and says so.
    broke = {
        "resources": {
            "core": {"remaining": 5000},
            "search": {"limit": 30, "remaining": 1},
        }
    }
    tight = collect(fetch=fake_gh(rate=broke), now=NOW, sleep_s=0)
    check("quota-stops-cleanly", tight["budget"]["stopped_on_quota"] is True)
    check(
        "quota-stop-is-recorded",
        any(s.get("status") == "rate_limited" for s in tight["sources"]),
    )
    check("quota-stop-is-unreliable", tight["reliable"] is False)

    # 10 — a control that returns nothing invalidates the artifact even when rows exist.
    def no_control(argv: list[str]) -> tuple[Any, Optional[str], int]:
        if argv[:2] == ["api", "rate_limit"]:
            return FIXTURE_RATE, None, 120
        q = next((a.split("=", 1)[1] for a in argv if a.startswith("q=")), "")
        if q.startswith("repo:"):
            return {"total_count": 0, "items": []}, None, 40
        return {"total_count": 2, "items": [FIXTURE_ITEM]}, None, 500

    blind = collect(fetch=no_control, now=NOW, sleep_s=0)
    check(
        "control-failure-forces-unreliable",
        blind["reliable"] is False and blind["rows"] != [],
    )

    # 11 — the write-free property is STRUCTURAL, so assert it structurally. A reader of this
    #      file could "helpfully" put the write back inside `collect`; then a store importing
    #      the module to read the ecosystem would silently litter artifacts. Parsing our own
    #      source is the only way to catch that, because no behavioural test can prove the
    #      ABSENCE of a write path that a future edit introduces.
    #
    #      The detector is itself proven on known-bad source IN THIS FUNCTION (the two snippets
    #      below), not by an experiment somebody ran once in a scratch directory. A guard whose
    #      own failure mode is "silently detects nothing" has to be fired deliberately on every
    #      run, or it decays into a green light with no lamp behind it. (Pattern adopted
    #      2026-09-11 from FeedsAndPractitionerMiner, who inlined his known-bad legs first.)
    _tree = ast.parse(pathlib.Path(__file__).read_text())
    _fns = {n.name: n for n in _tree.body if isinstance(n, ast.FunctionDef)}
    check(
        "writer-detector-sees-this-file",
        writer_functions(_tree) == {"write_artifact"},
        f"{sorted(writer_functions(_tree))}",
    )
    check(
        "collect-takes-no-root",
        {a.arg for a in _fns["collect"].args.args + _fns["collect"].args.kwonlyargs}
        == {"fetch", "now", "sleep_s"},
        f"{sorted(a.arg for a in _fns['collect'].args.args + _fns['collect'].args.kwonlyargs)}",
    )
    # Known-bad #1: the exact regression this guard exists for — the write moved back inside
    # `collect`, via the sanctioned atomic primitive, which is what makes it look innocent.
    _bad_atomic = (
        "def collect(*, fetch, now=None, sleep_s=0):\n"
        "    doc = {}\n"
        "    atomic_write_json(pathlib.Path('x.json'), doc)\n"
        "    return doc\n"
    )
    # Known-bad #2: a writer the atomic rule does not name, reached through a raw handle. The
    # detector must catch this too, or "one writer" is only true of the writers we thought of.
    _bad_raw = (
        "def collect(*, fetch):\n"
        "    with open('x.json', 'w') as fh:\n"
        "        json.dump({}, fh)\n"
        "    return {}\n"
    )
    check(
        "detector-fires-on-atomic-write",
        writer_functions(ast.parse(_bad_atomic)) == {"collect"},
        f"{sorted(writer_functions(ast.parse(_bad_atomic)))}",
    )
    check(
        "detector-fires-on-raw-open",
        writer_functions(ast.parse(_bad_raw)) == {"collect"},
        f"{sorted(writer_functions(ast.parse(_bad_raw)))}",
    )

    # 12 — a cancellation must UNWIND, never become a row or a recorded refusal.
    #
    #      `gbtypes.Cancelled` derives from Exception, so any handler broad enough to catch
    #      "an error" also eats SIGINT/SIGTERM. When that happens the run does not stop: it
    #      records the cancellation as a fetch failure, keeps going, and writes a COMPLETE-
    #      looking artifact whose only trace of the kill is one odd error string. A daily
    #      scheduled job would then read a cancelled tick as an ordinary bad-network day, and
    #      the spine's "no partial artifact written" guarantee is defeated inside the handler
    #      without main() ever seeing the exception. Routing through `gbtypes.main` is
    #      necessary and NOT sufficient. (Found by VendorDocsMiner in his own producer, 2026-09-11,
    #      after my SIGINT measurement; my handlers are narrow — ValueError, FileNotFoundError,
    #      OSError, JSONDecodeError — but "narrow today" is not a property unless something
    #      checks it, which is what these three legs are.)
    def _cancelling_fetch(argv: list[str]) -> tuple[Any, Optional[str], int]:
        if argv[:2] == ["api", "rate_limit"]:
            return FIXTURE_RATE, None, 120
        raise Cancelled(2)

    try:
        collect(fetch=_cancelling_fetch, now=NOW, sleep_s=0)
        _propagated = False
    except Cancelled:
        _propagated = True
    check("collect-propagates-cancellation", _propagated)

    # The same property one layer down, where the blanket-except temptation actually lives:
    # `gh_json`'s handlers must let Cancelled through and still RECORD an ordinary OSError.
    _real_run = globals()["run"]
    try:
        globals()["run"] = lambda *a, **k: (_ for _ in ()).throw(Cancelled(15))
        try:
            gh_json(["api", "rate_limit"])
            _leaked = True
        except Cancelled:
            _leaked = False
        check("gh_json-propagates-cancellation", _leaked is False)

        globals()["run"] = lambda *a, **k: (_ for _ in ()).throw(OSError("fork failed"))
        _doc, _err, _ = gh_json(["api", "rate_limit"])
        check(
            "gh_json-still-records-ordinary-oserror",
            _doc is None and _err is not None and "gh-unrunnable" in _err,
            f"{_err}",
        )
    finally:
        globals()["run"] = _real_run

    # 13 — the PRECONDITION that makes leaning on the spine safe, checked instead of trusted.
    #
    #      `gbtypes.main`'s BrokenPipeError arm exits 0 UNCONDITIONALLY, so any exit code this
    #      file returns can be silently upgraded to success by `… | head`. VendorDocsMiner
    #      measured exactly that on his producer: rc 3 became rc 0 with an EMPTY stderr, because
    #      the pipe broke before the ERROR line was reached.
    #
    #      The first draft of this leg asserted "every return is literally 0" — and it FAILED on
    #      its first run, because `body` also does `return selftest()`, which returns 1 on
    #      failure. So a piped `--selftest` really was reporting a broken producer as healthy.
    #      The rule is therefore not "never carry a verdict" (a producer is allowed to) but
    #      "every verdict-carrying path must guard its own printing". Two assertions:
    #      every `body` return is either the constant 0 or a call to a function whose printing
    #      is pipe-guarded, and that function really does carry the handler.
    _GUARDED = {"selftest"}
    # A non-zero return is legitimate when it IS the pipe guard — `except BrokenPipeError:
    # return 2` preserves a verdict rather than losing one. So returns lexically inside such a
    # handler are allowed; everywhere else in `body` a verdict must be 0 or a call to a
    # function that guards its own printing. (This third case arrived when the argparse usage
    # path turned out to be verdict-carrying: `--bogus 2>&1 | true` was exiting 0 instead of 2,
    # because the library writes on our behalf and my audit had only grepped for MY prints.)
    # The predicate is fired on a synthetic probe BEFORE it is trusted on real source: one
    # return in the protected try body (must NOT count as a guard) and one in the handler (must).
    # Inverting `ExceptHandler` to the try body flips this set, and every verdict conclusion
    # drawn from it, while still looking like a passing check.
    _probe = ast.parse(
        "def f():\n"
        "    try:\n"
        "        return 9\n"
        "    except BrokenPipeError:\n"
        "        return 2\n"
    ).body[0]
    assert isinstance(_probe, ast.FunctionDef)
    _probe_guarded = returns_in_pipe_guard(_probe)
    check(
        "guard-predicate-not-inverted",
        {
            n.value.value
            for n in ast.walk(_probe)
            if isinstance(n, ast.Return)
            and isinstance(n.value, ast.Constant)
            and id(n) in _probe_guarded
        }
        == {2},
        "the handler return must count as the guard, the try-body return must not",
    )
    _in_guard = returns_in_pipe_guard(_fns["body"])
    _returns = [
        n
        for n in ast.walk(_fns["body"])
        if isinstance(n, ast.Return) and n.value is not None
    ]

    def _ok_return(node: ast.Return) -> bool:
        if id(node) in _in_guard:
            return True
        value = node.value
        if isinstance(value, ast.Constant):
            return value.value == 0
        return isinstance(value, ast.Call) and getattr(value.func, "id", "") in _GUARDED

    check(
        "body-returns-only-guarded-verdicts",
        bool(_returns) and all(_ok_return(r) for r in _returns),
        f"{[getattr(r.value, 'value', type(r.value).__name__) for r in _returns if not _ok_return(r)]}"
        f" — a non-zero return whose printing is unguarded is erased by the spine's EPIPE->0 arm",
    )
    check(
        "guarded-verdict-functions-really-guard",
        all(
            any(
                isinstance(h, ast.ExceptHandler)
                and getattr(h.type, "id", "") == "BrokenPipeError"
                for h in ast.walk(_fns[name])
            )
            for name in _GUARDED
        ),
        f"{sorted(_GUARDED)}",
    )

    # 14 — the cancel-code recovery, proven on the EXACT exception shape without a signal.
    #
    #      The live proof (SIGINT into a closed pipe -> 130) cannot run inside a selftest, so
    #      without this leg the recovery is a behaviour nobody re-checks. Both NEGATIVE branches
    #      matter more than the positive one: a bare EPIPE and an EPIPE raised while handling
    #      something else must recover NOTHING, because a recovery that guesses 130 whenever a
    #      pipe breaks would manufacture a cancellation that never happened.
    def _epipe_from(inner: Optional[BaseException]) -> BrokenPipeError:
        try:
            if inner is None:
                raise BrokenPipeError(32, "Broken pipe")
            try:
                raise inner
            except type(inner):
                raise BrokenPipeError(32, "Broken pipe")
        except BrokenPipeError as exc:
            return exc
        raise AssertionError("unreachable")

    check("recover-sigint", recover_cancellation(_epipe_from(Cancelled(2))) == 130)
    check("recover-sigterm", recover_cancellation(_epipe_from(Cancelled(15))) == 143)
    check(
        "recover-nothing-from-bare-epipe",
        recover_cancellation(_epipe_from(None)) is None,
    )
    check(
        "recover-nothing-from-unrelated-context",
        recover_cancellation(_epipe_from(ValueError("unrelated"))) is None,
    )

    # The verdict is computed BEFORE anything is printed, and the printing is guarded, because
    # `body` returns this value: `gb-github.py --selftest | head -1` closes the pipe mid-report,
    # and `gbtypes.main`'s BrokenPipeError arm exits 0 unconditionally — so an unguarded print
    # here would convert a FAILING selftest into a passing tick for any hook or CI step that
    # pipes it. (Found 2026-09-11 by this file's own leg 13, which flagged `return selftest()`
    # as a verdict-carrying return path; the bug class is VendorDocsMiner's, measured on his
    # producer first.) A closed pipe can lose the MESSAGE here; it can never change the VERDICT.
    passed = sum(1 for _, ok, _ in checks if ok)
    verdict = 0 if passed == len(checks) else 1
    try:
        for name, ok, detail in checks:
            if not ok:
                print(f"FAIL {name} {detail}")
        print(
            f"SELFTEST {'PASS' if passed == len(checks) else 'FAIL'} - {passed}/{len(checks)}"
        )
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return verdict


def body() -> int:
    ap = argparse.ArgumentParser(
        prog="gb-github.py",
        description="daily GitHub ecosystem read for the Grok Bot deployment",
    )
    ap.add_argument(
        "command", nargs="?", default="collect", choices=["collect", "selftest"]
    )
    ap.add_argument(
        "--selftest", action="store_true", help="run inline fixtures, no network"
    )
    ap.add_argument(
        "--json", action="store_true", help="print the document instead of a summary"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="fetch, but write no artifact"
    )
    # argparse writes on our behalf and then raises SystemExit: usage errors to stderr with
    # code 2, `--help` to stdout with code 0. If the stream is a closed pipe, that write raises
    # BrokenPipeError from INSIDE parse_args, the SystemExit is never reached, and the spine's
    # unconditional EPIPE->0 arm turns "you typed the command wrong" into a successful run.
    # Measured 2026-09-11 before this guard: `gb-github.py --bogus 2>&1 | true` -> rc 0 while
    # the same command unpiped -> rc 2. My own audit had missed this because I only grepped for
    # MY print calls; the library's writes count too. (Class found by FeedsAndPractitionerMiner
    # on stderr, and by VendorDocsMiner on `ap.print_help(sys.stderr)` specifically.)
    try:
        args = ap.parse_args()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stderr.fileno())
        # We cannot ask argparse what it was about to exit with, but its two write paths are
        # distinguishable from argv: help is requested by a token, everything else is an error.
        return 0 if {"-h", "--help"} & set(sys.argv[1:]) else 2

    if args.selftest or args.command == "selftest":
        return selftest()

    root = pathlib.Path(__file__).resolve().parents[1]
    doc = collect()
    artifact = None if args.dry_run else write_artifact(root, doc)
    # No local BrokenPipeError catch here on purpose. `gbtypes.main` (bin/gbtypes.py:637) already
    # turns a closed pipe into exit 0, AND turns SIGINT/SIGTERM into 130/143 — the codes the
    # scheduler and gb-weekly.sh already read. A per-file catch would cover the pipe and NOT the
    # signal, which is the half that matters for a daily job the scheduler can kill: a hand-rolled
    # version of a spine that exists is a second convention that is also worse. (I had written
    # that catch; VendorDocsMiner pointed at the spine, so it is deleted rather than kept as
    # belt-and-braces.) Measured after the change: `collect | head -4` -> rc 0, 0 bytes stderr.
    #
    # PRECONDITION for leaning on that spine, so nobody breaks it by accident: this producer's
    # exit code carries NO verdict — every refusal is DATA in the artifact (`reliable`, a source
    # status) and `body` returns 0 on every path. The spine's BrokenPipeError arm exits 0
    # unconditionally, so a producer that DID encode a verdict in its exit code would have a
    # failing tick silently converted into a successful one by a reader closing the pipe.
    # (FeedsAndPractitionerMiner measured exactly that on his own producer, which does return a
    # verdict code, and kept a local emit() guard for it.) If anyone ever makes this file return
    # non-zero, the local guard has to come back with it.
    if args.json:
        print(json.dumps(doc, indent=1))
        return 0
    report(doc, artifact)
    if not doc["reliable"]:
        print(
            "  refusal recorded — do NOT read any zero in this artifact as absence "
            "(AGENTS.md positive-control rule)"
        )
    return 0


if __name__ == "__main__":
    # `gbtypes.main` is the house spine: a cancel scope so the scheduler's SIGTERM exits 130/143
    # with "no partial artifact written" rather than a traceback and exit 1.
    #
    # The wrapper covers a RESIDUAL IN THE SPINE that this file cannot fix from the inside and
    # must not fix by editing `gbtypes.py`. Measured 2026-09-11: with BOTH streams on a closed
    # pipe, a SIGINT exits 1 instead of 130, because the `print("cancelled: …", file=sys.stderr)`
    # inside the spine's `except Cancelled` arm raises EPIPE and its `except BrokenPipeError` is
    # a SIBLING arm that cannot catch it — so `sys.exit(c.exit_code)` never runs. This producer
    # is MORE exposed than a chattier one, not less: having no stderr writes of its own, fd 2 is
    # never already pointed at /dev/null when the spine tries to report, so the 1 is reliable
    # rather than occasional.
    #
    # The EPIPE's `__context__` IS the `Cancelled` being reported, so the code is recovered
    # exactly and never guessed; anything else re-raises, because inventing an exit code for an
    # unexplained EPIPE is the dishonesty this file exists to avoid. (Recovery shape adopted from
    # FeedsAndPractitionerMiner, who isolated the residual on his own producer independently.)
    try:
        main(body)
    except BrokenPipeError as _epipe:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stderr.fileno())
        _code = recover_cancellation(_epipe)
        if _code is None:
            raise
        raise SystemExit(_code)
