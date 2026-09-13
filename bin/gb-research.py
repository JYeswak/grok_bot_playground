#!/usr/bin/env python3
"""gb-research — one question in, a receipted answer out.

WHY THIS EXISTS. The daily fleet (gb-x/feeds/github/sources/links/discover) sweeps and
produces rows but answers no question: every tick re-derives the same joins by hand. This
verb is the question-driven half — it takes one research question, checks whether it was
already answered (vein check), collects across specialty sources inside explicit budgets,
labels every row with its trust tier, and refuses to promote community claims into
capability answers without Tier-1 evidence. A row that cannot name its dereference command
is not printed; it is counted in `suppressed`.

DEPTH IS BUDGETS, NOT ADJECTIVES:
  sweep      disk + cache only. Zero network. Answers "did we already know this".
  deep       sweep + live reddit + live arxiv inside call ceilings.
  exhaustive deep with raised ceilings, and the tier gate goes strict: a capability-shaped
             question with no Tier-1 row is NEEDS_T1 (exit 1), never a guess.

TIERS (AGENTS.md §5): vendor=T1, code=T2, x/reddit/arxiv=T3. T3 motivates; T1 decides.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import arg, parse  # noqa: E402
from gbtypes import (  # noqa: E402
    CallBudgetLedger,
    CallState,
    atomic_write_json,
    main,
    read_json_capped,
    validate_call_budget,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-research/2"
SUMMARY = "ask one research question across every specialty source, receipted"

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2
EXIT_ENVIRONMENT, EXIT_UPSTREAM = 3, 4

# Every threshold lives here and ships in --capabilities (repo conv 4: no magic numbers).
MAX_LIVE_CALLS = 20  # mirrors the X binding guard: quota is invisible until 429
MAX_REDDIT_CALLS = 8
MAX_ARXIV_CALLS = 8
LIVE_SOURCE_CEILINGS: Dict[str, int] = {
    "reddit": MAX_REDDIT_CALLS,
    "arxiv": MAX_ARXIV_CALLS,
}
CAPABILITY_SHAPED = re.compile(
    r"\b(can\b|supports?\b|available\b|able to\b|does\b.{0,20}\bsupport|is\b.{0,20}\b(enabled|supported))",
    re.IGNORECASE,
)
FETCH_TIMEOUT_S: float = 25
LIVE_BUDGET_S = 300
CACHE_TTL_S = 7 * 24 * 3600  # the gb-links 7-day fetch cache, reused not re-chosen
MAX_SWEEP_FILES_PER_ROOT = 3
MAX_ROWS = 50
REDDIT_PACK_VERSION = 1  # site-wide search.json, sort=top, t=month; no subreddit names
ARXIV_PACK_VERSION = 1  # export.arxiv.org api/query, max 20, sortBy=submittedDate
USER_AGENT = "grokbot-research/1.0 (+local-operator-tool)"
REDDIT_SEARCH_ENDPOINT = "https://www.reddit.com/search.json"
ARXIV_QUERY_ENDPOINT = "https://export.arxiv.org/api/query"
STOPWORDS = frozenset(
    "what how when where which who does does the a an and or of to in on for with can is are be by as at from that this it its into over under than then there their our your his her they them we you i".split()
)

SOURCE_NAMES: Tuple[str, ...] = (
    "vendor",
    "x",
    "reddit",
    "arxiv",
    "code",
    "practitioner",
)
# Disk corpus each sweep source reads (newest files). reddit/arxiv have no daily producer;
# at sweep depth they answer from cache or record SKIPPED — never fake a corpus.
SWEEP_ROOTS: Dict[str, str] = {
    "vendor": "sources",
    "x": "x",
    "code": "github",
    "practitioner": "feeds",
}
TIER_OF: Dict[str, str] = {
    "vendor": "T1",
    "code": "T2",
    "x": "T3",
    "reddit": "T3",
    "arxiv": "T3",
    "practitioner": "T3",
}
TIER_RANK = {"T1": 0, "T2": 1, "T3": 2}


@dataclasses.dataclass(frozen=True)
class Row:
    source: str
    tier: str
    title: str
    ref: str
    says: str
    deref: str
    score: int

    def to_json(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def derive_keywords(question: str, limit: int = 6) -> List[str]:
    toks = re.findall(r"[a-z0-9]{4,}", question.lower())
    seen: List[str] = []
    for t in toks:
        if t not in STOPWORDS and t not in seen:
            seen.append(t)
        if len(seen) >= limit:
            break
    return seen


def order_rows(rows: Sequence[Row]) -> List[Row]:
    """Deterministic rank: tier, then source, then ref. Two runs over unchanged inputs
    diff clean — research becomes watchable (the gb-advise lexicographic rule)."""
    return sorted(rows, key=lambda r: (TIER_RANK.get(r.tier, 9), r.source, r.ref))


def tier_gate(
    question: str, rows: Sequence[Row], strict: bool
) -> Tuple[str, List[str]]:
    """Returns (verdict, suppressed). A capability-shaped question answered only by T3
    rows is NEEDS_T1, never an answer — the NE-6 failure mode (confident claim from a
    cache of unknown freshness) at scale. Non-capability questions pass with a caveat."""
    if not any(r.tier == "T1" for r in rows):
        t3 = [r for r in rows if r.tier == "T3"]
        if CAPABILITY_SHAPED.search(question) and t3:
            return (
                "NEEDS_T1",
                [
                    f"tier-gate: capability-shaped question with {len(t3)} T3 row(s) and "
                    "0 T1 rows — community leads, not an answer. Next: confirm against "
                    "docs.x.ai or cursor.com/docs and this account's enabled_ids.",
                ],
            )
        if strict and t3:
            return (
                "NEEDS_T1",
                [
                    f"tier-gate(strict): {len(t3)} T3 row(s), 0 T1 rows at exhaustive depth — "
                    "refusing to ship a guess. Next: run the vendor join first.",
                ],
            )
    return ("OK", [])


def _ne_hits(keywords: Sequence[str]) -> List[str]:
    """NE blocks with >=2 keyword hits. One refuted vein named is one fresh chase avoided."""
    ne = ROOT / "NEGATIVE_EVIDENCE.md"
    if not ne.is_file():
        return []
    blocks = re.split(r"(?m)^## (NE-\d+)", ne.read_text(errors="replace"))
    hits: List[str] = []
    for i in range(1, len(blocks), 2):
        body = blocks[i + 1] if i + 1 < len(blocks) else ""
        n = sum(1 for k in keywords if k in body.lower())
        if n >= 2:
            hits.append(f"NEGATIVE_EVIDENCE.md {blocks[i].strip()} ({n} keyword hits)")
    return hits


def _bundle_hits(keywords: Sequence[str]) -> List[str]:
    """Prior bundles asking the same thing. A re-run graduates depth, never re-spends sweep."""
    rdir = ROOT / "research"
    if not rdir.is_dir():
        return []
    hits: List[str] = []
    for b in sorted(rdir.glob("*/bundle.json")):
        try:
            doc = json.loads(b.read_text(errors="replace"))
        except Exception:
            continue
        q = str(doc.get("question", "")).lower()
        n = sum(1 for k in keywords if k in q)
        if n >= 2:
            hits.append(
                f"{b.parent.name}/bundle.json asks '{doc.get('question','')[:80]}'"
            )
    return hits


def vein_scan(keywords: Sequence[str]) -> List[str]:
    """Past answers before fresh spend: refuted veins plus prior bundles, in that order."""
    return _ne_hits(keywords) + _bundle_hits(keywords)


def _fetch_json(url: str, request_id: str) -> Tuple[bool, Any, str]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "X-Grokbot-Request-Id": request_id},
    )
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as fh:
            return (True, json.loads(fh.read().decode("utf-8", "replace")), "")
    except urllib.error.HTTPError as e:
        # 401/403 = behind auth or bot-walled (the NE-10 class: a 403 from a default
        # signature is not a permissions fact). Recorded, never retried in-run.
        return (False, None, f"HTTP {e.code}")
    except Exception as e:
        return (False, None, f"{type(e).__name__}: {e}"[:120])


def _cache_path(kind: str, key: str) -> pathlib.Path:
    import hashlib

    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return ROOT / "state" / "research-cache" / kind / f"{h}.json"


def _cache_get(kind: str, key: str) -> Optional[Any]:
    p = _cache_path(kind, key)
    try:
        doc = json.loads(p.read_text(errors="replace"))
    except Exception:
        return None
    import time

    if time.time() - float(doc.get("fetched_at_epoch", 0)) > CACHE_TTL_S:
        return None
    return doc.get("payload")


def _cache_put(kind: str, key: str, payload: Any) -> None:
    import time

    p = _cache_path(kind, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        # atomic_write_json, not write_text: g22-durable-io judges every producer by
        # static scan, and a torn cache entry is a lie the next run would read as truth.
        atomic_write_json(
            p, {"fetched_at_epoch": time.time(), "key": key, "payload": payload}
        )
    except Exception as exc:
        # Best-effort cache, but a silent one hides a full disk. stderr, never swallowed.
        print(
            f"gb-research: cache write failed for {kind}: {exc}"[:160], file=sys.stderr
        )


def _reddit_url(keywords: Sequence[str]) -> str:
    q = " ".join(keywords[:4]) or "grok bot"
    params = urllib.parse.urlencode(
        {"q": q, "sort": "top", "t": "month", "limit": "25", "restrict_sr": "0"}
    )
    return f"{REDDIT_SEARCH_ENDPOINT}?{params}"


def fetch_reddit(
    keywords: Sequence[str], budget: CallBudgetLedger
) -> Tuple[List[Row], str]:
    """Search Reddit once, charging the shared ledger before any upstream I/O."""
    url = _reddit_url(keywords)
    cached = _cache_get("reddit", url)
    if cached is not None:
        budget.cache_decision("reddit", hit=True)
        data, note = cached, "cache-hit"
    else:
        budget.cache_decision("reddit", hit=False)
        request_id = budget.attempt("reddit")
        if request_id is None:
            return ([], "BUDGET_EXHAUSTED — no aggregate calls remaining")
        ok, data, err = _fetch_json(url, request_id)
        if not ok:
            budget.complete("reddit", CallState.UNREACHABLE, error=err)
            return ([], f"UNREACHABLE — reddit {err}")
        note = "live"
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        budget.complete(
            "reddit", CallState.FAILED, error="malformed response: data object absent"
        )
        return ([], f"{note}: malformed response (not EMPTY)")
    children = data["data"].get("children")
    if not isinstance(children, list):
        budget.complete(
            "reddit", CallState.FAILED, error="malformed response: children list absent"
        )
        return ([], f"{note}: malformed response (not EMPTY)")
    rows: List[Row] = []
    for ch in children[:25]:
        raw = ch.get("data") if isinstance(ch, dict) else None
        d = raw if isinstance(raw, dict) else {}
        title = str(d.get("title", ""))[:160]
        if not title:
            continue
        score = sum(1 for k in keywords if k in title.lower())
        rows.append(
            Row(
                source="reddit",
                tier="T3",
                title=title,
                ref=f"reddit:{d.get('id', '?')}",
                says=f"r/{d.get('subreddit', '?')} score={d.get('score', '?')} "
                f"comments={d.get('num_comments', '?')}",
                deref=f"https://www.reddit.com{d.get('permalink', '')}",
                score=score,
            )
        )
    if note == "live":
        _cache_put("reddit", url, data)
    budget.complete(
        "reddit", CallState.MEASURED if rows else CallState.EMPTY, rows=len(rows)
    )
    return (rows, f"{note}: {len(rows)} post(s) pack=v{REDDIT_PACK_VERSION}")


def _arxiv_url(keywords: Sequence[str]) -> str:
    q = (
        "+AND+".join(f"all:{urllib.parse.quote(k)}" for k in keywords[:3])
        or "all:agent"
    )
    return (
        f"{ARXIV_QUERY_ENDPOINT}?search_query={q}"
        f"&start=0&max_results=20&sortBy=submittedDate&sortOrder=descending"
    )


def fetch_arxiv(
    keywords: Sequence[str], budget: CallBudgetLedger
) -> Tuple[List[Row], str]:
    """Search arXiv once, charging the shared ledger before any upstream I/O."""
    url = _arxiv_url(keywords)
    cached = _cache_get("arxiv", url)
    if cached is not None:
        budget.cache_decision("arxiv", hit=True)
        body, note = cached, "cache-hit"
    else:
        budget.cache_decision("arxiv", hit=False)
        request_id = budget.attempt("arxiv")
        if request_id is None:
            return ([], "BUDGET_EXHAUSTED — no aggregate calls remaining")
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "X-Grokbot-Request-Id": request_id},
        )
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as fh:
                body = fh.read().decode("utf-8", "replace")
        except Exception as exc:
            error = type(exc).__name__
            budget.complete("arxiv", CallState.UNREACHABLE, error=error)
            return ([], f"UNREACHABLE — arxiv {error}")
        note = "live"
    import xml.etree.ElementTree as ET

    rows: List[Row] = []
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, TypeError):
        budget.complete("arxiv", CallState.FAILED, error="unparseable Atom feed")
        return ([], f"{note}: unparseable feed (not EMPTY)")
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(f"{ns}entry")[:20]:
        title = re.sub(r"\s+", " ", (entry.findtext(f"{ns}title") or "")).strip()[:160]
        ident = (entry.findtext(f"{ns}id") or "").strip()
        if not title:
            continue
        score = sum(1 for k in keywords if k in title.lower())
        rows.append(
            Row(
                source="arxiv",
                tier="T3",
                title=title,
                ref=f"arxiv:{ident.rsplit('/', 1)[-1]}",
                says="technique exists in the literature — never proof this deployment exposes it",
                deref=ident,
                score=score,
            )
        )
    if note == "live":
        _cache_put("arxiv", url, body)
    budget.complete(
        "arxiv", CallState.MEASURED if rows else CallState.EMPTY, rows=len(rows)
    )
    return (rows, f"{note}: {len(rows)} paper(s) pack=v{ARXIV_PACK_VERSION}")


def _scan_file(src: str, f: pathlib.Path, keywords: Sequence[str]) -> Optional[Row]:
    """One artifact, one row or none. >=2 keyword hits earns a row; anything less is
    noise and stays out of the bundle rather than flattering the count."""
    doc = read_json_capped(f)
    if doc is None:
        return None
    blob = json.dumps(doc)[:200000].lower()
    n = sum(1 for k in keywords if k in blob)
    if n < 2:
        return None
    return Row(
        source=src,
        tier=TIER_OF[src],
        title=f"{n} keyword hits in {f.name}",
        ref=f"{SWEEP_ROOTS[src]}/{f.name}",
        says=f"keywords present ({n}/{len(keywords)}); open the artifact, not a summary",
        deref=f"jq . {SWEEP_ROOTS[src]}/{f.name}",
        score=n,
    )


def _sweep_files(src: str) -> List[pathlib.Path]:
    """Newest artifacts for one disk source. The date prefix is the sort key and the
    filter (gblib.dated_children's rule, reused not re-chosen): undated files are not
    tick artifacts and never enter a census."""
    d = ROOT / SWEEP_ROOTS[src]
    if not d.is_dir():
        return []
    return sorted(
        (c for c in d.glob("*.json") if len(c.name) >= 10 and c.name[4] == "-"),
        key=lambda c: c.name,
    )[-MAX_SWEEP_FILES_PER_ROOT:]


def sweep_disk(
    keywords: Sequence[str], wanted: Sequence[str]
) -> Tuple[List[Row], List[str]]:
    """Keyword-scan the newest artifacts of each wanted disk source. A source with no
    artifacts is UNREACHABLE (never measured here); one scanned with zero hits is EMPTY
    (measured zero). Collapsing those is the defect this census refuses."""
    rows: List[Row] = []
    notes: List[str] = []
    for src in wanted:
        if src not in SWEEP_ROOTS:
            continue
        files = _sweep_files(src)
        if not files:
            notes.append(f"{src}: UNREACHABLE — no artifacts under {SWEEP_ROOTS[src]}/")
            continue
        hits = 0
        for f in files:
            row = _scan_file(src, f, keywords)
            if row is not None:
                hits += 1
                rows.append(row)
        if not hits:
            notes.append(
                f"{src}: EMPTY — {len(files)} artifact(s) scanned, 0 keyword hits"
            )
        else:
            notes.append(f"{src}: {hits} matching artifact(s)")
    return (rows, notes)


def capabilities_dict() -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "depths": ["sweep", "deep", "exhaustive"],
        "sources": list(SOURCE_NAMES),
        "thresholds": {
            "MAX_LIVE_CALLS": MAX_LIVE_CALLS,
            "MAX_REDDIT_CALLS": MAX_REDDIT_CALLS,
            "MAX_ARXIV_CALLS": MAX_ARXIV_CALLS,
            "FETCH_TIMEOUT_S": FETCH_TIMEOUT_S,
            "LIVE_BUDGET_S": LIVE_BUDGET_S,
            "CACHE_TTL_S": CACHE_TTL_S,
            "MAX_SWEEP_FILES_PER_ROOT": MAX_SWEEP_FILES_PER_ROOT,
            "MAX_ROWS": MAX_ROWS,
            "REDDIT_PACK_VERSION": REDDIT_PACK_VERSION,
            "ARXIV_PACK_VERSION": ARXIV_PACK_VERSION,
        },
        "budget_contract": {
            "aggregate_absolute_ceiling": MAX_LIVE_CALLS,
            "per_source_absolute_ceilings": dict(LIVE_SOURCE_CEILINGS),
            "source_order_policy": "requested order; default SOURCE_NAMES order",
            "allocation": "cache-before-budget; charge-before-io",
            "cache_hit_cost": 0,
            "attempt_failure_cost": 1,
            "receipt_fields": [
                "correlation_id",
                "requested_source_order",
                "budget.aggregate",
                "budget.sources",
                "retry_argv",
                "exit_code",
            ],
        },
    }


# -------------------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class ResearchArgs:
    """Ask one research question across every specialty source, receipted."""

    question: Optional[str] = arg(
        default=None,
        positional=True,
        help="the research question (required unless --resume/--selftest/--capabilities)",
    )
    depth: str = arg(
        default="sweep",
        help="sweep (disk+cache, zero network) | deep (+live reddit/arxiv) | "
        "exhaustive (raised ceilings, strict tier gate)",
    )
    source: Optional[List[str]] = arg(
        default=None,
        metavar="STR",
        help="restrict to these sources; repeatable. Known: " + ", ".join(SOURCE_NAMES),
    )
    max_calls: int = arg(
        default=MAX_LIVE_CALLS,
        metavar="N",
        help=f"hard ceiling on live calls across all sources (default {MAX_LIVE_CALLS})",
    )
    resume: Optional[str] = arg(
        default=None,
        metavar="STAMP",
        help="re-run the brief stored at research/<STAMP>/brief.json (graduate sweep->deep)",
    )
    dry_run: bool = arg(
        help="print the plan: sources, queries, budgets, cache hits. No fetch, no write."
    )
    json: bool = arg(help="machine-readable envelope on stdout")
    selftest: bool = arg(help="run the offline inline-fixture selftest and exit")
    capabilities: bool = arg(help="print thresholds and source registry and exit")


def selftest() -> int:
    """No-internet unit and public-command proof of the aggregate budget contract."""
    import contextlib
    import copy
    import http.server
    import io
    import tempfile
    import threading
    import time

    global ROOT, REDDIT_SEARCH_ENDPOINT, ARXIV_QUERY_ENDPOINT, FETCH_TIMEOUT_S
    fails: List[str] = []
    legs = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal legs
        legs += 1
        print(
            f"  [{'PASS' if cond else 'FAIL'}] {name}"
            + (f" — {detail}" if detail else "")
        )
        if not cond:
            fails.append(name)

    t3rows = [Row("x", "T3", "t", "x:1", "says", "deref", 1)]
    verdict, suppressed = tier_gate(
        "can routines trigger on Slack keywords?", t3rows, strict=False
    )
    check("t3-only-capability-refused", verdict == "NEEDS_T1" and len(suppressed) == 1)
    ok_rows = t3rows + [Row("vendor", "T1", "t", "sources/f", "says", "deref", 2)]
    check(
        "t1-present-passes",
        tier_gate("can routines trigger on Slack keywords?", ok_rows, strict=False)[0]
        == "OK",
    )
    check(
        "strict-refuses-t3-only",
        tier_gate("what do builders automate most?", t3rows, strict=True)[0]
        == "NEEDS_T1",
    )
    a = Row("x", "T3", "b", "x:2", "", "", 0)
    b = Row("vendor", "T1", "a", "sources/1", "", "", 0)
    c = Row("code", "T2", "c", "github/1", "", "", 0)
    check(
        "row-order-deterministic",
        [r.ref for r in order_rows([a, b, c])]
        == [r.ref for r in order_rows([c, a, b])]
        == ["sources/1", "github/1", "x:2"],
    )

    fixture: Dict[str, Any] = {"requests": [], "modes": {}}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def do_GET(self) -> None:
            source = "reddit" if self.path.startswith("/reddit") else "arxiv"
            fixture["requests"].append(
                {
                    "source": source,
                    "path": self.path,
                    "request_id": self.headers.get("X-Grokbot-Request-Id"),
                    "user_agent": self.headers.get("User-Agent"),
                }
            )
            mode = fixture["modes"].get(source, "success")
            if mode == "timeout":
                time.sleep(0.08)
            if mode == "failure":
                self.send_error(503)
                return
            body_bytes = (
                json.dumps({"data": {"children": []}}).encode()
                if source == "reddit"
                else b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            )
            try:
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/json"
                    if source == "reddit"
                    else "application/atom+xml",
                )
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    old_root = ROOT
    old_reddit = REDDIT_SEARCH_ENDPOINT
    old_arxiv = ARXIV_QUERY_ENDPOINT
    old_timeout = FETCH_TIMEOUT_S
    old_argv = sys.argv[:]

    def invoke(
        root: pathlib.Path,
        max_calls: int,
        order: Sequence[str],
        modes: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, Any], List[Dict[str, Any]]]:
        global ROOT
        ROOT = root
        fixture["requests"] = []
        fixture["modes"] = dict(modes or {})
        argv = [
            str(pathlib.Path(__file__)),
            "what agent research patterns exist",
            "--depth",
            "deep",
            "--max-calls",
            str(max_calls),
            "--json",
        ]
        for source in order:
            argv += ["--source", source]
        sys.argv = argv
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = body()
        document = json.loads(stdout.getvalue())
        return code, document, list(fixture["requests"])

    table: Tuple[
        Tuple[str, int, Tuple[str, ...], Dict[str, str], int, Tuple[str, ...]],
        ...,
    ] = (
        ("n0", 0, ("reddit", "arxiv"), {}, 0, ("BUDGET_EXHAUSTED", "BUDGET_EXHAUSTED")),
        ("n1", 1, ("reddit", "arxiv"), {}, 1, ("EMPTY", "BUDGET_EXHAUSTED")),
        ("order-reverse", 1, ("arxiv", "reddit"), {}, 1, ("EMPTY", "BUDGET_EXHAUSTED")),
        ("exact-total", 2, ("reddit", "arxiv"), {}, 2, ("EMPTY", "EMPTY")),
        ("above-source-caps", 99, ("reddit", "arxiv"), {}, 2, ("EMPTY", "EMPTY")),
        ("source-restriction", 1, ("arxiv",), {}, 1, ("EMPTY",)),
        (
            "first-failure",
            1,
            ("reddit", "arxiv"),
            {"reddit": "failure"},
            1,
            ("UNREACHABLE", "BUDGET_EXHAUSTED"),
        ),
        (
            "first-timeout",
            1,
            ("reddit", "arxiv"),
            {"reddit": "timeout"},
            1,
            ("UNREACHABLE", "BUDGET_EXHAUSTED"),
        ),
    )
    documents: Dict[str, Dict[str, Any]] = {}
    request_logs: Dict[str, List[Dict[str, Any]]] = {}
    try:
        REDDIT_SEARCH_ENDPOINT = base + "/reddit"
        ARXIV_QUERY_ENDPOINT = base + "/arxiv"
        FETCH_TIMEOUT_S = 0.02
        with tempfile.TemporaryDirectory(prefix="gb-research-selftest-") as tmp:
            tmp_root = pathlib.Path(tmp)
            for (
                name,
                max_calls,
                order,
                modes,
                expected_requests,
                expected_states,
            ) in table:
                try:
                    code, document, requests = invoke(
                        tmp_root / name, max_calls, order, modes
                    )
                    documents[name] = document
                    request_logs[name] = requests
                    source_rows = document["budget"]["sources"]
                    actual_states = tuple(
                        source_rows[source]["state"] for source in order
                    )
                    aggregate = document["budget"]["aggregate"]
                    request_ids_ok = all(
                        req["request_id"]
                        and req["request_id"].startswith(document["correlation_id"])
                        for req in requests
                    )
                    expected_attempt_order = [
                        source
                        for source, state in zip(order, expected_states)
                        if state in ("EMPTY", "UNREACHABLE")
                    ]
                    timings_ok = (
                        type(aggregate.get("elapsed_ms")) in (int, float)
                        and aggregate["elapsed_ms"] >= 0
                        and all(
                            type(source_rows[source].get("elapsed_ms")) in (int, float)
                            and source_rows[source]["elapsed_ms"] >= 0
                            for source in order
                        )
                    )
                    per_source_counts_ok = all(
                        all(
                            type(source_rows[source].get(field)) is int
                            for field in (
                                "ceiling",
                                "planned",
                                "spent",
                                "remaining",
                                "attempted",
                                "succeeded",
                                "failed",
                                "skipped",
                            )
                        )
                        and source_rows[source]["spent"]
                        == source_rows[source]["attempted"]
                        for source in order
                    )
                    check(
                        f"budget-table-{name}",
                        len(requests) == expected_requests
                        and len(requests) <= min(max_calls, MAX_LIVE_CALLS)
                        and [request["source"] for request in requests]
                        == expected_attempt_order
                        and aggregate["spent"] == expected_requests
                        and per_source_counts_ok
                        and timings_ok
                        and actual_states == expected_states
                        and document["requested_source_order"] == list(order)
                        and document["budget"]["valid"] is True
                        and document["exit_code"] == code
                        and isinstance(document.get("rows"), list)
                        and document.get("verdict") in ("OK", "ERROR", "NEEDS_T1")
                        and document.get("readiness", {}).get("status")
                        and document["retry_argv"]
                        == _retry_argv(document["question"], "deep", order, max_calls)
                        and request_ids_ok,
                        f"requests={len(requests)} states={actual_states}",
                    )
                except Exception as exc:
                    check(f"budget-table-{name}", False, f"{type(exc).__name__}: {exc}")

            exact = documents.get("exact-total", {})
            print(
                "  E2E-RECEIPT "
                + json.dumps(
                    {
                        "correlation_id": exact.get("correlation_id"),
                        "requested_source_order": exact.get("requested_source_order"),
                        "budget": exact.get("budget"),
                        "observed_requests": request_logs.get("exact-total", []),
                        "rows": exact.get("rows"),
                        "readiness": exact.get("readiness"),
                        "verdict": exact.get("verdict"),
                        "exit_code": exact.get("exit_code"),
                        "retry_argv": exact.get("retry_argv"),
                    },
                    sort_keys=True,
                )
            )
            check(
                "measured-empty-distinct",
                exact.get("verdict") == "OK"
                and exact.get("readiness", {}).get("status") == "EMPTY"
                and exact.get("readiness", {}).get("adoptable") is False,
            )
            above = documents.get("above-source-caps", {}).get("budget", {})
            check(
                "absolute-and-per-source-caps-retained",
                above.get("aggregate", {}).get("ceiling") == MAX_LIVE_CALLS
                and above.get("aggregate", {}).get("planned")
                == MAX_REDDIT_CALLS + MAX_ARXIV_CALLS
                and above.get("sources", {}).get("reddit", {}).get("ceiling")
                == MAX_REDDIT_CALLS
                and above.get("sources", {}).get("arxiv", {}).get("ceiling")
                == MAX_ARXIV_CALLS,
            )
            partial = documents.get("n1", {})
            check(
                "budget-exhaustion-blocks-readiness",
                partial.get("verdict") == "ERROR"
                and partial.get("readiness", {}).get("adoptable") is False,
            )
            failure = documents.get("first-failure", {})
            timeout = documents.get("first-timeout", {})
            check(
                "failures-and-timeouts-are-charged",
                failure.get("budget", {}).get("aggregate", {}).get("spent") == 1
                and timeout.get("budget", {}).get("aggregate", {}).get("spent") == 1,
            )
            uncharged_failure = copy.deepcopy(failure.get("budget", {}))
            uncharged_aggregate = uncharged_failure.get("aggregate", {})
            if uncharged_aggregate:
                uncharged_aggregate["spent"] = 0
                uncharged_aggregate["remaining"] = uncharged_aggregate.get("planned", 0)
            check(
                "known-bad-uncharged-failure-rejected",
                failure.get("budget", {})
                .get("sources", {})
                .get("reddit", {})
                .get("attempted")
                == 1
                and bool(validate_call_budget(uncharged_failure)),
            )

            cache_root = tmp_root / "cache"
            _, first, first_requests = invoke(cache_root, 1, ("reddit",))
            _, cached, cached_requests = invoke(cache_root, 0, ("reddit",))
            check(
                "cache-hit-zero-charge",
                len(first_requests) == 1
                and len(cached_requests) == 0
                and first["budget"]["sources"]["reddit"]["cache_decision"] == "MISS"
                and cached["budget"]["sources"]["reddit"]["cache_decision"] == "HIT"
                and cached["budget"]["aggregate"]["spent"] == 0
                and cached["budget"]["sources"]["reddit"]["state"] == "EMPTY",
            )
            _, replayed, replay_requests = invoke(cache_root, 0, ("reddit",))

            def stable_budget(document: Dict[str, Any]) -> Dict[str, Any]:
                stable: Dict[str, Any] = copy.deepcopy(document["budget"])
                stable["aggregate"].pop("elapsed_ms", None)
                for source in stable["sources"].values():
                    source.pop("elapsed_ms", None)
                return stable

            check(
                "deterministic-replay",
                not replay_requests
                and stable_budget(cached) == stable_budget(replayed)
                and cached["retry_argv"] == replayed["retry_argv"]
                and cached["correlation_id"] == replayed["correlation_id"],
            )
            malformed = copy.deepcopy(cached["budget"])
            malformed["aggregate"]["spent"] = 1
            check(
                "malformed-accounting-rejected", bool(validate_call_budget(malformed))
            )
            missing_spec = RunSpec(
                question="q",
                keywords=["question"],
                wanted=["reddit"],
                disk_sources=[],
                live=True,
                strict=False,
                max_calls=0,
                vein=[],
                depth="deep",
                retry_argv=_retry_argv("q", "deep", ["reddit"], 0),
            )
            malformed_verdict, _, _, malformed_readiness, _ = decide(
                missing_spec, [], [], malformed
            )
            check(
                "malformed-accounting-blocks-readiness",
                malformed_verdict == "ERROR"
                and malformed_readiness["adoptable"] is False,
            )
            missing_verdict, _, _, missing_readiness, _ = decide(
                missing_spec, [], [], None
            )
            check(
                "absent-accounting-blocks-readiness",
                missing_verdict == "ERROR" and missing_readiness["adoptable"] is False,
            )
    finally:
        ROOT = old_root
        REDDIT_SEARCH_ENDPOINT = old_reddit
        ARXIV_QUERY_ENDPOINT = old_arxiv
        FETCH_TIMEOUT_S = old_timeout
        sys.argv = old_argv
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    independent = min(MAX_REDDIT_CALLS, 1) + min(MAX_ARXIV_CALLS, 1)
    check("known-bad-independent-allocation-rejected", independent > 1)
    contract = capabilities_dict()["budget_contract"]
    check(
        "capabilities-budget-contract",
        contract["aggregate_absolute_ceiling"] == MAX_LIVE_CALLS
        and contract["cache_hit_cost"] == 0
        and contract["attempt_failure_cost"] == 1,
    )
    print(f"gb-research selftest: {legs - len(fails)}/{legs} legs passed")
    return EXIT_FINDINGS if fails else EXIT_OK


@dataclasses.dataclass(frozen=True)
class RunSpec:
    """Everything execute() needs beyond flags, including its replay contract."""

    question: str
    keywords: List[str]
    wanted: List[str]
    disk_sources: List[str]
    live: bool
    strict: bool
    max_calls: int
    vein: List[str]
    depth: str
    retry_argv: List[str]


def _new_budget(spec: RunSpec) -> CallBudgetLedger:
    import hashlib

    source_caps = LIVE_SOURCE_CEILINGS
    source_order = [source for source in spec.wanted if source in source_caps]
    ceiling = min(spec.max_calls, MAX_LIVE_CALLS)
    planned = min(ceiling, sum(source_caps[source] for source in source_order))
    if not spec.live:
        planned = 0
    correlation_id = (
        "research-"
        + hashlib.sha256(
            json.dumps(spec.retry_argv, separators=(",", ":")).encode()
        ).hexdigest()[:16]
    )
    return CallBudgetLedger(
        requested=spec.max_calls,
        ceiling=ceiling,
        planned=planned,
        source_ceilings=source_caps,
        source_order=source_order,
        correlation_id=correlation_id,
    )


def collect(spec: RunSpec) -> Tuple[List[Row], List[str], Dict[str, Any]]:
    """Collect all selected sources through one aggregate live-call ledger."""
    rows: List[Row] = []
    notes: List[str] = []
    budget = _new_budget(spec)
    if spec.vein and spec.depth == "sweep":
        notes.append(
            f"vein-hit: already answered — {spec.vein[0]} "
            "(re-run --depth deep to spend fresh)"
        )
    disk_rows, disk_notes = sweep_disk(spec.keywords, spec.disk_sources)
    rows += disk_rows
    notes += disk_notes
    if not spec.live:
        for source in budget.source_order:
            budget.skip(source, reason="depth=sweep performs zero live I/O")
    else:
        fetchers = {"reddit": fetch_reddit, "arxiv": fetch_arxiv}
        for source in budget.source_order:
            source_rows, note = fetchers[source](spec.keywords, budget)
            rows += source_rows
            notes.append(f"{source}: {note}")
    return (rows, notes, budget.snapshot())


def _budget_problems(spec: RunSpec, budget: Any) -> List[str]:
    problems = list(validate_call_budget(budget))
    if not isinstance(budget, dict):
        return problems
    expected_caps = LIVE_SOURCE_CEILINGS
    expected_order = [source for source in spec.wanted if source in expected_caps]
    aggregate = budget.get("aggregate") or {}
    source_rows = budget.get("sources") or {}
    expected_ceiling = min(spec.max_calls, MAX_LIVE_CALLS)
    expected_planned = (
        min(expected_ceiling, sum(expected_caps[source] for source in expected_order))
        if spec.live
        else 0
    )
    if budget.get("source_order") != expected_order:
        problems.append("source order disagrees with the requested source order")
    if aggregate.get("requested") != spec.max_calls:
        problems.append("aggregate requested count disagrees with --max-calls")
    if aggregate.get("ceiling") != expected_ceiling:
        problems.append("aggregate ceiling disagrees with MAX_LIVE_CALLS")
    if aggregate.get("planned") != expected_planned:
        problems.append("aggregate planned count disagrees with selected source caps")
    for source in expected_order:
        if (source_rows.get(source) or {}).get("ceiling") != expected_caps[source]:
            problems.append(f"source {source} ceiling disagrees with its absolute cap")
    if not spec.live:
        return problems
    measured = {CallState.MEASURED.value, CallState.EMPTY.value}
    for source in expected_order:
        state = (source_rows.get(source) or {}).get("state")
        if state not in measured:
            problems.append(
                f"required source {source} was not measured ({state or 'ABSENT'})"
            )
    return problems


def decide(
    spec: RunSpec,
    rows: List[Row],
    notes: List[str],
    budget: Any,
) -> Tuple[str, List[Dict[str, Any]], List[str], Dict[str, Any], int]:
    """Rank, gate, and refuse adoption when live accounting is incomplete."""
    ordered = order_rows(rows)[:MAX_ROWS]
    budget_problems = _budget_problems(spec, budget)
    if budget_problems:
        notes = notes + [
            f"budget-accounting: ERROR — {problem}" for problem in budget_problems
        ]
        readiness = {
            "status": "ERROR",
            "adoptable": False,
            "proposal": None,
            "needs_human": True,
            "why": budget_problems[0],
        }
        return (
            "ERROR",
            [r.to_json() for r in ordered],
            notes,
            readiness,
            EXIT_UPSTREAM,
        )

    verdict, gate_notes = tier_gate(spec.question, ordered, spec.strict)
    notes = notes + gate_notes
    if verdict == "NEEDS_T1":
        readiness = {
            "status": "NEEDS_T1",
            "adoptable": False,
            "proposal": None,
            "needs_human": True,
            "why": gate_notes[0] if gate_notes else "tier gate",
        }
        code = EXIT_FINDINGS
    else:
        top = ordered[0] if ordered else None
        has_t1 = any(r.tier == "T1" for r in ordered)
        readiness = {
            "status": "READY"
            if has_t1 and top
            else ("EMPTY" if top is None else "LEADS_ONLY"),
            "adoptable": bool(has_t1 and top),
            "proposal": (f"confirm in app: {top.title} — {top.deref}" if top else None),
            "needs_human": spec.depth != "exhaustive" or top is None,
            "why": "T1 evidence present; human applies the change, next audit observes it"
            if has_t1
            else (
                "all required sources measured; zero matching rows"
                if top is None
                else "no T1 rows; leads only"
            ),
        }
        code = EXIT_OK
    return (verdict, [r.to_json() for r in ordered], notes, readiness, code)


def execute(spec: RunSpec) -> Tuple[Dict[str, Any], int]:
    """Collect, gate, and build one fully-accounted research receipt."""
    t0 = dt.datetime.now(dt.timezone.utc)
    rows, notes, budget = collect(spec)
    verdict, row_json, notes, readiness, code = decide(spec, rows, notes, budget)
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "correlation_id": budget.get("correlation_id"),
        "question": spec.question,
        "keywords": spec.keywords,
        "depth": spec.depth,
        "sources": spec.wanted,
        "requested_source_order": [
            source for source in spec.wanted if source in LIVE_SOURCE_CEILINGS
        ],
        "researched_at": t0.isoformat(timespec="seconds"),
        "vein_hits": spec.vein,
        "verdict": verdict,
        "exit_code": code,
        "rows": row_json,
        "suppressed_count": max(0, len(rows) - len(row_json)),
        "notes": notes,
        "budget": budget,
        "retry_argv": spec.retry_argv,
        "readiness": readiness,
        "artifact": None,
    }
    return (payload, code)


def render(payload: Dict[str, Any], code: int, as_json: bool) -> int:
    """Stdout is data: the envelope or one human page. Diagnostics already went to
    stderr at their source; nothing here logs."""
    if as_json:
        print(json.dumps(payload, indent=1))
        return code
    ordered = payload["rows"]
    aggregate = payload["budget"]["aggregate"]
    print(
        f"gb-research · {payload['verdict']} · {len(ordered)} row(s) · "
        f"calls planned={aggregate['planned']} spent={aggregate['spent']} "
        f"remaining={aggregate['remaining']} · {payload['artifact']}"
    )
    for source in payload["budget"]["source_order"]:
        row = payload["budget"]["sources"][source]
        print(
            f"  budget: {source} cap={row['ceiling']} cache={row['cache_decision']} "
            f"attempted={row['attempted']} succeeded={row['succeeded']} "
            f"failed={row['failed']} skipped={row['skipped']} state={row['state']}"
        )
    for r in ordered[:10]:
        print(f"  [{r['tier']}] {r['source']:<12} {r['title'][:100]}")
        print(f"         {r['deref'][:110]}")
    for note in payload["notes"]:
        print(f"  note: {note[:130]}")
    if payload["readiness"]["proposal"]:
        print(f"  next: {payload['readiness']['proposal'][:130]}")
    print("  retry argv: " + json.dumps(payload["retry_argv"]))
    return code


def _wanted_sources(args: ResearchArgs) -> Optional[List[str]]:
    """Validate the spend flags. None means a usage error, already printed to
    stderr — the caller returns EXIT_USAGE without knowing which flag failed."""
    if args.depth not in ("sweep", "deep", "exhaustive"):
        print(
            f"gb-research: unknown --depth {args.depth!r} (sweep|deep|exhaustive)",
            file=sys.stderr,
        )
        return None
    requested = tuple(s for s in (args.source or []) if s.strip())
    unknown = [s for s in requested if s not in SOURCE_NAMES]
    if unknown:
        print(
            f"gb-research: unknown --source {', '.join(unknown)}. "
            f"Known: {', '.join(SOURCE_NAMES)}",
            file=sys.stderr,
        )
        return None
    if args.max_calls < 0:
        print("gb-research: --max-calls must be >= 0", file=sys.stderr)
        return None
    ordered: List[str] = []
    for source in requested or SOURCE_NAMES:
        if source not in ordered:
            ordered.append(source)
    return ordered


def _load_question(args: ResearchArgs) -> Tuple[Optional[str], int]:
    """Positional question, or the brief --resume replays. Returns (question, exit).
    A missing question is USAGE; an unreadable brief is ENVIRONMENT — different days."""
    question = args.question
    if args.resume:
        brief_path = ROOT / "research" / args.resume / "brief.json"
        try:
            brief = json.loads(brief_path.read_text(errors="replace"))
            question = brief.get("question", question)
        except Exception:
            print(f"gb-research: cannot read brief {brief_path}", file=sys.stderr)
            return (None, EXIT_ENVIRONMENT)
    if not question or not question.strip():
        print(
            "gb-research: a question is required (positional) unless --resume replays one",
            file=sys.stderr,
        )
        return (None, EXIT_USAGE)
    return (question.strip(), EXIT_OK)


def _retry_argv(
    question: str, depth: str, wanted: Sequence[str], max_calls: int
) -> List[str]:
    argv = ["bin/gb", "research", question, "--depth", depth]
    for source in wanted:
        argv += ["--source", source]
    argv += ["--max-calls", str(max_calls), "--json"]
    return argv


def _budget_plan(spec: RunSpec) -> Dict[str, Any]:
    budget = _new_budget(spec)
    remaining = budget.planned
    sources: Dict[str, Any] = {}
    for source in budget.source_order:
        url = (
            _reddit_url(spec.keywords)
            if source == "reddit"
            else _arxiv_url(spec.keywords)
        )
        hit = _cache_get(source, url) is not None
        if not spec.live:
            state, attempts = "SKIPPED", 0
        elif hit:
            state, attempts = "CACHE_HIT", 0
        elif remaining > 0 and budget.source_ceilings[source] > 0:
            state, attempts = "PLANNED", 1
            remaining -= 1
        else:
            state, attempts = "BUDGET_EXHAUSTED", 0
        sources[source] = {
            "ceiling": budget.source_ceilings[source],
            "cache_decision": "HIT" if hit else "MISS",
            "planned_attempts": attempts,
            "state": state,
        }
    return {
        "policy": "requested-source-order; cache-before-budget; charge-before-io",
        "correlation_id": budget.correlation_id,
        "source_order": list(budget.source_order),
        "aggregate": {
            "requested": budget.requested,
            "ceiling": budget.ceiling,
            "planned": budget.planned,
            "forecast_spent": budget.planned - remaining,
            "forecast_remaining": remaining,
        },
        "sources": sources,
    }


def _dry_run(spec: RunSpec, as_json: bool) -> int:
    """Print the deterministic call and cache plan without fetches or writes."""
    plan = _budget_plan(spec)
    payload = {
        "schema": SCHEMA,
        "dry_run": True,
        "question": spec.question,
        "keywords": spec.keywords,
        "sources": spec.wanted,
        "vein_hits": spec.vein,
        "budget": plan,
        "retry_argv": spec.retry_argv,
    }
    if as_json:
        print(json.dumps(payload, indent=1))
        return EXIT_OK
    aggregate = plan["aggregate"]
    print(
        f"gb-research --dry-run · depth={spec.depth} sources={','.join(spec.wanted)}\n"
        f"  question: {spec.question}\n"
        f"  keywords: {','.join(spec.keywords) or '(none — stopword-only question)'}\n"
        f"  vein hits: {len(spec.vein)}"
        + (f" — {spec.vein[0]}" if spec.vein else "")
        + "\n"
        f"  live budget: planned={aggregate['planned']} "
        f"forecast_spent={aggregate['forecast_spent']} "
        f"remaining={aggregate['forecast_remaining']}\n"
        f"  tier gate: {'strict' if spec.strict else 'standard'}"
    )
    return EXIT_OK


def body() -> int:
    args = parse(ResearchArgs, description=SUMMARY)
    if args.selftest:
        return selftest()
    if args.capabilities:
        if args.json:
            print(json.dumps(capabilities_dict(), indent=1))
        else:
            caps = capabilities_dict()
            for k, v in caps["thresholds"].items():
                print(f"  {k}={v}")
            contract = caps["budget_contract"]
            print("sources: " + ", ".join(SOURCE_NAMES))
            print(
                "budget: aggregate<=MAX_LIVE_CALLS; "
                f"per-source={json.dumps(contract['per_source_absolute_ceilings'], sort_keys=True)}; "
                f"policy={contract['allocation']}"
            )
            print("receipt fields: " + ", ".join(contract["receipt_fields"]))
        return EXIT_OK
    wanted = _wanted_sources(args)
    if wanted is None:
        return EXIT_USAGE
    question, qcode = _load_question(args)
    if question is None:
        return qcode
    keywords = derive_keywords(question)
    strict = args.depth == "exhaustive"
    live = args.depth in ("deep", "exhaustive")
    vein = vein_scan(keywords)
    disk_sources = [s for s in wanted if s in SWEEP_ROOTS]
    if not disk_sources and not live:
        print(
            f"gb-research: nothing to run — {','.join(wanted)} "
            "have no disk corpus and depth=sweep spends no calls. "
            "Use --depth deep or pick a disk source.",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT
    spec = RunSpec(
        question=question,
        keywords=keywords,
        wanted=wanted,
        disk_sources=disk_sources,
        live=live,
        strict=strict,
        max_calls=args.max_calls,
        vein=vein,
        depth=args.depth,
        retry_argv=_retry_argv(question, args.depth, wanted, args.max_calls),
    )
    if args.dry_run:
        return _dry_run(spec, args.json)
    payload, code = execute(spec)
    stamp = (
        payload["researched_at"][:10]
        + "T"
        + payload["researched_at"][11:13]
        + payload["researched_at"][14:16]
        + payload["researched_at"][17:19]
    )
    rdir = ROOT / "research" / stamp
    rdir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        rdir / "brief.json",
        {
            "schema": SCHEMA,
            "question": question,
            "keywords": keywords,
            "depth": args.depth,
            "sources": wanted,
            "brief_at": payload["researched_at"],
            "max_calls": args.max_calls,
            "retry_argv": spec.retry_argv,
        },
    )
    payload["artifact"] = f"research/{stamp}/bundle.json"
    atomic_write_json(rdir / "bundle.json", payload)
    return render(payload, code, args.json)


if __name__ == "__main__":
    main(body)
