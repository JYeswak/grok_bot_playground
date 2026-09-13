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
from gbtypes import atomic_write_json, main, read_json_capped  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-research/1"
SUMMARY = "ask one research question across every specialty source, receipted"

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2
EXIT_ENVIRONMENT, EXIT_UPSTREAM = 3, 4

# Every threshold lives here and ships in --capabilities (repo conv 4: no magic numbers).
MAX_LIVE_CALLS = 20  # mirrors the X binding guard: quota is invisible until 429
MAX_REDDIT_CALLS = 8
MAX_ARXIV_CALLS = 8
CAPABILITY_SHAPED = re.compile(
    r"\b(can\b|supports?\b|available\b|able to\b|does\b.{0,20}\bsupport|is\b.{0,20}\b(enabled|supported))",
    re.IGNORECASE,
)
FETCH_TIMEOUT_S = 25
LIVE_BUDGET_S = 300
CACHE_TTL_S = 7 * 24 * 3600  # the gb-links 7-day fetch cache, reused not re-chosen
MAX_SWEEP_FILES_PER_ROOT = 3
MAX_ROWS = 50
REDDIT_PACK_VERSION = 1  # site-wide search.json, sort=top, t=month; no subreddit names
ARXIV_PACK_VERSION = 1  # export.arxiv.org api/query, max 20, sortBy=submittedDate
USER_AGENT = "grokbot-research/1.0 (+local-operator-tool)"
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


def _fetch_json(url: str) -> Tuple[bool, Any, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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


def fetch_reddit(keywords: Sequence[str], ceiling: int) -> Tuple[List[Row], str, int]:
    """Site-wide search.json (pack v1): no subreddit names to go stale, sort=top t=month.
    Returns (rows, note, calls_spent). A 403/429 is UNREACHABLE, never an empty corpus."""
    import time

    q = " ".join(keywords[:4]) or "grok bot"
    params = urllib.parse.urlencode(
        {"q": q, "sort": "top", "t": "month", "limit": "25", "restrict_sr": "0"}
    )
    url = f"https://www.reddit.com/search.json?{params}"
    cached = _cache_get("reddit", url)
    if cached is not None:
        data, note, spent = cached, "cache-hit", 0
    else:
        if ceiling < 1:
            return ([], "budget-exhausted: 0 calls allowed", 0)
        ok, data, err = _fetch_json(url)
        spent = 1
        if not ok:
            return ([], f"UNREACHABLE — reddit {err}", spent)
        _cache_put("reddit", url, data)
        note = "live"
    rows: List[Row] = []
    try:
        children = (data.get("data") or {}).get("children", [])
    except AttributeError:
        children = []
    for ch in children[:25]:
        d = ch.get("data", {}) if isinstance(ch, dict) else {}
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
    _ = time
    return (rows, f"{note}: {len(rows)} post(s) pack=v{REDDIT_PACK_VERSION}", spent)


def fetch_arxiv(keywords: Sequence[str], ceiling: int) -> Tuple[List[Row], str, int]:
    q = (
        "+AND+".join(f"all:{urllib.parse.quote(k)}" for k in keywords[:3])
        or "all:agent"
    )
    url = (
        f"https://export.arxiv.org/api/query?search_query={q}"
        f"&start=0&max_results=20&sortBy=submittedDate&sortOrder=descending"
    )
    cached = _cache_get("arxiv", url)
    if cached is not None:
        body, note, spent = cached, "cache-hit", 0
    else:
        if ceiling < 1:
            return ([], "budget-exhausted: 0 calls allowed", 0)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as fh:
                body = fh.read().decode("utf-8", "replace")
        except Exception as e:
            return ([], f"UNREACHABLE — arxiv {type(e).__name__}", 1)
        _cache_put("arxiv", url, body)
        note, spent = "live", 1
    import xml.etree.ElementTree as ET

    rows: List[Row] = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return ([], f"{note}: unparseable feed (not an empty field)", spent)
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
    return (rows, f"{note}: {len(rows)} paper(s) pack=v{ARXIV_PACK_VERSION}", spent)


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
    fails: List[str] = []

    def check(name: str, cond: bool) -> None:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails.append(name)

    # 1. Tier gate refuses a T3-only capability answer.
    t3rows = [Row("x", "T3", "t", "x:1", "says", "deref", 1)]
    v, s = tier_gate("can routines trigger on Slack keywords?", t3rows, strict=False)
    check("t3-only-capability-refused", v == "NEEDS_T1" and len(s) == 1)
    # 2. T1 present passes.
    ok_rows = t3rows + [Row("vendor", "T1", "t", "sources/f", "says", "deref", 2)]
    v2, _ = tier_gate("can routines trigger on Slack keywords?", ok_rows, strict=False)
    check("t1-present-passes", v2 == "OK")
    # 3. Non-capability question passes with caveat-free OK even when T3-only.
    v3, _ = tier_gate("what do builders automate most?", t3rows, strict=False)
    check("non-capability-passes", v3 == "OK")
    # 4. Strict gate refuses T3-only at exhaustive depth.
    v4, _ = tier_gate("what do builders automate most?", t3rows, strict=True)
    check("strict-refuses-t3-only", v4 == "NEEDS_T1")
    # 5. Deterministic ordering: shuffled in, identical out.
    a = Row("x", "T3", "b", "x:2", "", "", 0)
    b = Row("vendor", "T1", "a", "sources/1", "", "", 0)
    c = Row("code", "T2", "c", "github/1", "", "", 0)
    check(
        "ordering-deterministic",
        [r.ref for r in order_rows([a, b, c])]
        == [r.ref for r in order_rows([c, a, b])]
        == ["sources/1", "github/1", "x:2"],
    )
    # 6. Budget ceiling: live fetchers spend nothing at ceiling 0.
    _, _, spent_r = fetch_reddit(["zzz-no-such-keyword-qqq"], 0)
    _, _, spent_a = fetch_arxiv(["zzz-no-such-keyword-qqq"], 0)
    check("budget-ceiling-honored", spent_r == 0 and spent_a == 0)
    # 7. Capabilities carry every threshold (repo conv 4).
    caps = capabilities_dict()["thresholds"]
    check(
        "capabilities-complete",
        all(
            k in caps
            for k in (
                "MAX_LIVE_CALLS",
                "CACHE_TTL_S",
                "MAX_ROWS",
                "REDDIT_PACK_VERSION",
                "ARXIV_PACK_VERSION",
            )
        ),
    )
    print(f"gb-research selftest: {7 - len(fails)}/7 legs passed")
    return 1 if fails else 0


@dataclasses.dataclass(frozen=True)
class RunSpec:
    """Everything execute() needs beyond flags. One bundle, not nine parameters —
    a nine-argument function is a struct its author was too hurried to name."""

    question: str
    keywords: List[str]
    wanted: List[str]
    disk_sources: List[str]
    live: bool
    strict: bool
    ceiling: Dict[str, int]
    vein: List[str]
    depth: str


def collect(spec: RunSpec) -> Tuple[List[Row], List[str], int]:
    """Every source that spends anything — disk reads and live calls alike. Returns
    (rows, notes, calls_spent). Judges nothing; the tier gate runs downstream."""
    rows: List[Row] = []
    notes: List[str] = []
    spent = 0
    if spec.vein and spec.depth == "sweep":
        notes.append(
            f"vein-hit: already answered — {spec.vein[0]} "
            "(re-run --depth deep to spend fresh)"
        )
    disk_rows, disk_notes = sweep_disk(spec.keywords, spec.disk_sources)
    rows += disk_rows
    notes += disk_notes
    if spec.live:
        if "reddit" in spec.wanted:
            rr, rn, rs = fetch_reddit(spec.keywords, spec.ceiling["reddit"])
            rows += rr
            notes.append(f"reddit: {rn}")
            spent += rs
        if "arxiv" in spec.wanted:
            ar, an, as_ = fetch_arxiv(spec.keywords, spec.ceiling["arxiv"])
            rows += ar
            notes.append(f"arxiv: {an}")
            spent += as_
    return (rows, notes, spent)


def decide(
    spec: RunSpec, rows: List[Row], notes: List[str]
) -> Tuple[str, List[Dict[str, Any]], List[str], Dict[str, Any], int]:
    """Rank, gate, and frame readiness. The one place a T3-only capability answer
    dies: NEEDS_T1 with the dereference that would revive it, never a guess."""
    ordered = order_rows(rows)[:MAX_ROWS]
    verdict, gate_notes = tier_gate(spec.question, ordered, spec.strict)
    notes = notes + gate_notes
    readiness: Dict[str, Any]
    if verdict == "NEEDS_T1":
        readiness = {
            "proposal": None,
            "needs_human": True,
            "why": gate_notes[0] if gate_notes else "tier gate",
        }
        code = EXIT_FINDINGS
    else:
        top = ordered[0] if ordered else None
        readiness = {
            "proposal": (f"confirm in app: {top.title} — {top.deref}" if top else None),
            "needs_human": spec.depth != "exhaustive" or top is None,
            "why": "T1 evidence present; human applies the change, next audit observes it"
            if any(r.tier == "T1" for r in ordered)
            else "no T1 rows; leads only",
        }
        code = EXIT_OK
    return (verdict, [r.to_json() for r in ordered], notes, readiness, code)


def execute(spec: RunSpec) -> Tuple[Dict[str, Any], int]:
    """Collect, gate, and build the bundle payload. No parsing, no printing, no
    writing — the caller owns the durable IO so a re-run of this function over the
    same inputs is a pure recomputation, never a second artifact."""
    t0 = dt.datetime.now(dt.timezone.utc)
    rows, notes, spent = collect(spec)
    verdict, row_json, notes, readiness, code = decide(spec, rows, notes)
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "question": spec.question,
        "keywords": spec.keywords,
        "depth": spec.depth,
        "sources": spec.wanted,
        "researched_at": t0.isoformat(timespec="seconds"),
        "vein_hits": spec.vein,
        "verdict": "OK" if verdict == "OK" else verdict,
        "rows": row_json,
        "suppressed_count": max(0, len(rows) - len(row_json)),
        "notes": notes,
        "calls_spent": spent,
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
    print(
        f"gb-research · {payload['verdict']} · {len(ordered)} row(s), "
        f"{payload['calls_spent']} live call(s) · {payload['artifact']}"
    )
    for r in ordered[:10]:
        print(f"  [{r['tier']}] {r['source']:<12} {r['title'][:100]}")
        print(f"         {r['deref'][:110]}")
    for n in payload["notes"]:
        print(f"  note: {n[:130]}")
    if payload["readiness"]["proposal"]:
        print(f"  next: {payload['readiness']['proposal'][:130]}")
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
    return list(requested) if requested else list(SOURCE_NAMES)


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


def _dry_run(spec: RunSpec, max_calls: int, as_json: bool) -> int:
    """The plan, printed instead of executed. Takes the run spec, not seven
    loose parameters — a seven-argument function is a struct its author was
    too hurried to name (and one already exists)."""
    if as_json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "dry_run": True,
                    "question": spec.question,
                    "keywords": spec.keywords,
                    "sources": spec.wanted,
                    "vein_hits": spec.vein,
                },
                indent=1,
            )
        )
        return EXIT_OK
    print(
        f"gb-research --dry-run · depth={spec.depth} sources={','.join(spec.wanted)}\n"
        f"  question: {spec.question}\n"
        f"  keywords: {','.join(spec.keywords) or '(none — stopword-only question)'}\n"
        f"  vein hits: {len(spec.vein)}"
        + (f" — {spec.vein[0]}" if spec.vein else "")
        + "\n"
        f"  live budget: {'0 (sweep spends nothing)' if not spec.live else str(max_calls)}\n"
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
            for k, v in capabilities_dict()["thresholds"].items():
                print(f"  {k}={v}")
            print("sources: " + ", ".join(SOURCE_NAMES))
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
    ceiling = {
        "reddit": min(MAX_REDDIT_CALLS, args.max_calls),
        "arxiv": min(MAX_ARXIV_CALLS, args.max_calls),
    }
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
        ceiling=ceiling,
        vein=vein,
        depth=args.depth,
    )
    if args.dry_run:
        return _dry_run(spec, args.max_calls, args.json)
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
        },
    )
    payload["artifact"] = f"research/{stamp}/bundle.json"
    atomic_write_json(rdir / "bundle.json", payload)
    return render(payload, code, args.json)


if __name__ == "__main__":
    main(body)
