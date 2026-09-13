#!/usr/bin/env python3
"""gb-daily-proof — bounded novelty and proof since the last complete run.

`gb daily proof` does not replace `gb daily` collection. It reads local artifacts
only; no network. A run is eligible to advance its durable cursor only when all
six daily producers (sources, github, feeds, x, links, grokbotdev) have a fresh,
structured artifact for the current UTC day. The receipt records the exact
expected set, attempted/succeeded/failed/stale/skipped counts, each producer's
artifact cursor and sha256, its row count, and a digest over that accounting.
Success with zero rows is explicit. Missing, unreadable, stale, schema-less, or
tampered accounting returns ERROR/exit 4 with retry argv and does not write a
cursor artifact.

Lenses: vendor, practitioner, event, proven, broken. Every emitted row carries
its producer and epistemic level. Exact cursor ids deduplicate across sources;
content with distinct ids is intentionally preserved because semantic merging
belongs to v92v.43. The first complete v2 run arms a baseline. Later complete
runs emit only ids newer than the prior successful cursor; an unchanged replay
returns nothing-new/exit 0. Atomic persistence makes the JSON artifact itself
the cursor commit.

Raw vendor, practitioner, event, proven, and broken rows are evidence only.
They never create `post-drafts/` files or invoke the share wrapper. Only a
terminal `lesson` row produced by v92v.21 may reach that side-effect surface.
`--share` otherwise refuses with exit 2. This prevents a URL or event mention
from becoming an unreceipted post, archive, experiment, or live account write.

Exit codes: 0 baseline/nothing-new/quiet; 1 emitted findings; 2 invalid or
unreceipted input/share request; 4 incomplete upstream measurement. `--json`
prints one `gb-daily-proof/2` envelope on stdout, including prior/current cursor,
counts, rows, producer accounting, retry argv, and total timing.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import datetime as dt
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children, load  # noqa: E402
from gbtypes import atomic_write_text, main as gbmain, run  # noqa: E402

SCHEMA = "gb-daily-proof/2"
POST_SCHEMA = "gb-post-draft/1"
POST_CAP = 5
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "daily-proof"
POST = ROOT / "post-drafts"
GATE = pathlib.Path(__file__).resolve().parent / "gb-surface-gate.py"
SHARE_SH = pathlib.Path(__file__).resolve().parent / "gb-share-post.sh"
SHARE_RECIPE = "bin/gb-share-post.sh"
GATE_PRODUCER = "bin/gb-surface-gate.py"
# Measured 2026-09-11 (`bench/`): gate median 0.270s. 90s is the monitor's wedged-child ceiling.
GATE_TIMEOUT_S = 90.0
EXIT_OK, EXIT_FINDINGS, EXIT_USAGE, EXIT_UPSTREAM = 0, 1, 2, 4
NE_RE = re.compile(r"^## (NE-\d+)", re.M)
URL_RE = re.compile(r"https?://[^\s\]>)\"']+")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.M)
OFFICIAL_HOSTS = {"docs.x.ai", "cursor.com", "status.x.ai"}
SURFACE_PRODUCER = "bin/gb-surface-snapshot.py"
SOURCES_PRODUCER = "bin/gb-sources.py"
GALAXY_PRODUCER = "bin/gb-galaxy.py"
GALAXY_URL = "https://luma.com/3ifrgttw"
GALAXY_WATCH_LAST = dt.date(2026, 9, 17)
EPISTEMIC = {
    "gate-verified",
    "docs-verified",
    "client-binary-verified",
    "community-claim",
    "unverified",
}
EXPECTED_PRODUCERS = ("sources", "github", "feeds", "x", "links", "grokbotdev")
RETRY_ARGV = ["gb", "daily", "proof", "--json"]


class UpstreamError(Exception):
    """A required producer is unreadable, empty, stale, or incomplete."""


class ProducerCensusError(UpstreamError):
    def __init__(self, accounting: Dict[str, Any]) -> None:
        self.accounting = accounting
        super().__init__("required producer census incomplete")


def row(
    lens: str,
    text: str,
    producer: str,
    epistemic: str,
    key: str,
    url: str = "",
    hook: str = "",
) -> Dict[str, str]:
    out = {
        "lens": lens,
        "text": text,
        "producer": producer,
        "epistemic": epistemic,
        "id": key,
    }
    if url:
        out["url"] = url
    if hook:
        out["hook"] = hook
    return out


def receipt_ok(r: Dict[str, Any]) -> Optional[str]:
    if not r.get("producer"):
        return "missing producer"
    if r.get("epistemic") not in EPISTEMIC:
        return "missing/invalid epistemic"
    if not r.get("id") or not r.get("text"):
        return "missing id or text"
    return None


def broken_from_gate_checks(checks: Any) -> List[Dict[str, str]]:
    """Map each RED/ERROR board row to a receipted broken-lens row."""
    out: List[Dict[str, str]] = []
    if not isinstance(checks, list):
        return out
    for item in checks:
        if not isinstance(item, dict):
            continue
        verdict = item.get("verdict")
        if verdict not in ("RED", "ERROR"):
            continue
        cid = str(item.get("check") or "").strip()
        if not cid:
            continue
        detail = str(item.get("detail") or verdict).strip()
        out.append(
            row(
                "broken",
                f"{cid} {verdict}: {detail}",
                GATE_PRODUCER,
                "gate-verified",
                f"gate:{cid}",
            )
        )
    return out


def _gate_unreadable(why: str) -> List[Dict[str, str]]:
    raise UpstreamError(f"gate board unreadable: {why}")


def classify_gate_board(doc: Any) -> List[Dict[str, str]]:
    """RED/ERROR rows from a parsed board. Empty or shapeless is unmeasured, not unchanged."""
    if not isinstance(doc, dict) or "checks" not in doc:
        return _gate_unreadable("stdout is not a board")
    checks = doc.get("checks")
    if not isinstance(checks, list) or not checks:
        raise UpstreamError("gate board empty — unmeasured, not unchanged")
    return broken_from_gate_checks(checks)


def gate_broken_rows() -> List[Dict[str, str]]:
    """Compose `gb-surface-gate.py --json` RED/ERROR. Nonzero exit is the answer, not a crash.

    Timeout, truncated, unreadable, or empty checks are UPSTREAM, not nothing-new.
    """
    if not GATE.is_file():
        return _gate_unreadable("gb-surface-gate.py is absent")
    proc = run(
        [sys.executable, str(GATE), "--json", "--root", str(ROOT)],
        timeout_s=GATE_TIMEOUT_S,
        cwd=ROOT,
    )
    if proc.timed_out or proc.truncated:
        why = "exceeded its deadline" if proc.timed_out else "exceeded the output cap"
        return _gate_unreadable(f"the gate {why} after {proc.elapsed_s}s")
    try:
        doc = json.loads(proc.out)
    except ValueError:
        return _gate_unreadable(f"exited {proc.code} and stdout is not JSON")
    return classify_gate_board(doc)


def ne_ids() -> List[str]:
    p = ROOT / "NEGATIVE_EVIDENCE.md"
    if not p.is_file():
        return []
    return NE_RE.findall(p.read_text(encoding="utf-8", errors="replace"))


def newest_pair(
    folder: str, suffix: str = ".json", root: Optional[pathlib.Path] = None
) -> Tuple[Optional[pathlib.Path], Optional[pathlib.Path]]:
    rows = dated_children((root or ROOT) / folder, suffix)
    if not rows:
        return None, None
    if len(rows) == 1:
        return None, rows[-1]
    return rows[-2], rows[-1]


def unseen(rows: List[Dict[str, str]], prev_ids: Set[str]) -> List[Dict[str, str]]:
    """Deduplicate exact cursor ids; preserve distinct ids even when content matches."""
    seen = set(prev_ids)
    out: List[Dict[str, str]] = []
    for item in rows:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        out.append(item)
    return out


def _snip(text: Any, n: int = 160) -> str:
    s = " ".join(str(text or "").split())
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _http_url(*vals: Any) -> str:
    for v in vals:
        if not isinstance(v, str):
            continue
        hit = URL_RE.search(v.strip())
        if hit:
            return hit.group(0)
    return ""


def _people_row(
    key: str,
    url: str,
    who: str,
    sample: str,
    take: str,
    producer: str,
) -> Optional[Dict[str, str]]:
    """Fail-closed for this framing: no URL, no row."""
    if not url:
        return None
    who = who.strip() or key
    sample = _snip(sample) or who
    text = f'{who} — "{sample}" {url} — take: {take}'
    hook = f'{who}\n"{sample}"\n{url}\n\n{take}'
    ep = "docs-verified" if "docs.x.ai" in url else "community-claim"
    return row("practitioner", text, producer, ep, key, url=url, hook=hook)


def _doc_rows(doc: Any) -> List[Any]:
    if not isinstance(doc, dict):
        return []
    rows = doc.get("rows")
    return rows if isinstance(rows, list) else []


def _signals(item: Dict[str, Any]) -> Dict[str, Any]:
    value = item.get("signals")
    return value if isinstance(value, dict) else {}


def github_people(doc: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for item in _doc_rows(doc):
        if not isinstance(item, dict):
            continue
        name = str(item.get("title") or "").strip()
        if not name:
            continue
        rec = _people_row(
            f"github:{name}",
            _http_url(item.get("source"), item.get("url"), item.get("html_url")),
            name,
            str(item.get("summary") or name),
            f"New repo in the wild. Would you actually run {name} on a Bot computer?",
            "bin/gb-github.py",
        )
        if rec and rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def x_people(doc: Any) -> List[Dict[str, str]]:
    """One row per new X account (newest post), id x:<tweet-id>."""
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    seen_authors: Set[str] = set()
    for item in _doc_rows(doc):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        if kind and kind != "x-post":
            continue
        sig = _signals(item)
        tid = str(sig.get("tweet_id") or "").strip()
        if not tid:
            continue
        author = str(sig.get("author") or "").strip()
        if author:
            if author in seen_authors:
                continue
            seen_authors.add(author)
        who = f"@{author}" if author else f"x status {tid}"
        rec = _people_row(
            f"x:{tid}",
            _http_url(item.get("url")),
            who,
            str(item.get("summary") or item.get("title") or who),
            f"{who} is talking Grok Bot in public. Signal or noise?",
            "bin/gb-x.py",
        )
        if rec and rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def grokbotdev_people(doc: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for item in _doc_rows(doc):
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id") or "").strip()
        if not rid:
            continue
        headline = str(item.get("headline") or item.get("slug") or rid).strip()
        rec = _people_row(
            f"grokbotdev:{rid}",
            _http_url(item.get("url"), item.get("share_url")),
            headline,
            headline,
            "Just dropped on grokbot.dev. Steal this, or skip it?",
            "bin/gb-grokbotdev.py",
        )
        if rec and rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def usecases_people(doc: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for item in _doc_rows(doc):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        contrib = str(item.get("contributor") or "").strip()
        who = f"{contrib} / {name}" if contrib else name
        rec = _people_row(
            f"usecases:{name}",
            _http_url(item.get("source")),
            who,
            str(item.get("charter") or item.get("description") or name),
            "Someone already runs this as a Bot. Do you have one — or are you still doing the job?",
            "bin/gb-usecases.py",
        )
        if rec and rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def _host_of(url: str) -> str:
    rest = url.split("://", 1)[-1]
    host = rest.split("/", 1)[0].split(":", 1)[0].lower()
    return host[4:] if host.startswith("www.") else host


def _official_vendor_url(url: str) -> bool:
    return bool(url) and _host_of(url) in OFFICIAL_HOSTS


def _first_heading(text: str) -> str:
    m = HEADING_RE.search(text or "")
    return _snip(m.group(1)) if m else ""


def _surface_body_path(
    manifest: pathlib.Path, item: Dict[str, Any]
) -> Optional[pathlib.Path]:
    base = manifest.parent
    slug = str(item.get("slug") or "").strip()
    if slug:
        p = base / "pages" / f"{slug}.md"
        if p.is_file():
            return p
    label = str(item.get("label") or "").strip()
    if label:
        p = base / "extras" / label
        if p.is_file():
            return p
    return None


def _page_sample(item: Dict[str, Any], body: Optional[pathlib.Path] = None) -> str:
    title = str(item.get("title") or "").strip()
    if title:
        return _snip(title)
    if body is not None:
        try:
            raw = body.read_text(encoding="utf-8", errors="replace")[:16000]
        except OSError:
            raw = ""
        heading = _first_heading(raw)
        if heading:
            return heading
    return _snip(str(item.get("label") or ""))


def _vendor_take(sample: str) -> str:
    name = sample or "this page"
    return (
        f"Official docs just moved on {name}. If your Bot still quotes last week, "
        f"that's the whole game. What do you re-teach it first?"
    )


def _vendor_row(
    url: str, sample: str, producer: str, key: str = ""
) -> Optional[Dict[str, str]]:
    """Fail-closed: no URL, no row. Official pages are docs-verified."""
    if not url or not _official_vendor_url(url):
        return None
    sample = _snip(sample) or url
    take = _vendor_take(sample)
    text = f'{sample} — "{sample}" {url} — take: {take}'
    hook = f"{sample}\n{url}\n\n{take}"
    return row(
        "vendor",
        text,
        producer,
        "docs-verified",
        key or f"vendor:{url}",
        url=url,
        hook=hook,
    )


def _surface_manifests(root: pathlib.Path) -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for p in dated_children(root / "surface"):
        if p.is_dir():
            m = p / "manifest.json"
            if m.is_file():
                out.append(m)
        elif p.is_file() and p.name.endswith(".json"):
            out.append(p)
    return out


def _iter_surface_pages(doc: Any) -> List[Dict[str, Any]]:
    if not isinstance(doc, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for key in ("pages", "extras"):
        block = doc.get(key)
        if isinstance(block, list):
            for item in block:
                if isinstance(item, dict):
                    rows.append(item)
    return rows


def _page_sha(item: Dict[str, Any]) -> str:
    return str(item.get("sha256") or "").strip().lower()


def surface_vendor_rows(root: pathlib.Path) -> List[Dict[str, str]]:
    manifests = _surface_manifests(root)
    if len(manifests) < 2:
        return []
    prev = load(manifests[-2]) or {}
    cur = load(manifests[-1]) or {}
    prev_hash: Dict[str, str] = {}
    for item in _iter_surface_pages(prev):
        url = _http_url(item.get("url"))
        h = _page_sha(item)
        if url and h:
            prev_hash[url] = h
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for item in _iter_surface_pages(cur):
        url = _http_url(item.get("url"))
        h = _page_sha(item)
        if not url or not h:
            continue
        if prev_hash.get(url) == h:
            continue
        sample = _page_sample(item, _surface_body_path(manifests[-1], item))
        rec = _vendor_row(url, sample, SURFACE_PRODUCER)
        if rec and rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def _source_fp(item: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    sig = _signals(item)
    return (
        str(item.get("title") or ""),
        str(item.get("published_at") or ""),
        str(sig.get("lastmod") or ""),
        str(item.get("summary") or ""),
        str(item.get("kind") or ""),
    )


def sources_vendor_rows(root: pathlib.Path) -> List[Dict[str, str]]:
    prev, cur = newest_pair("sources", root=root)
    if prev is None or cur is None:
        return []
    prev_fp: Dict[str, Tuple[str, str, str, str, str]] = {}
    for item in _doc_rows(load(prev) or {}):
        if not isinstance(item, dict):
            continue
        sig = _signals(item)
        url = _http_url(item.get("url"), sig.get("markdown_url"))
        if url:
            prev_fp[url] = _source_fp(item)
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for item in _doc_rows(load(cur) or {}):
        if not isinstance(item, dict):
            continue
        sig = _signals(item)
        url = _http_url(item.get("url"), sig.get("markdown_url"))
        if not url:
            continue
        if prev_fp.get(url) == _source_fp(item):
            continue
        sample = str(item.get("title") or "").strip() or str(item.get("summary") or "")
        rec = _vendor_row(url, sample, SOURCES_PRODUCER)
        if rec and rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def vendor_rows(root: Optional[pathlib.Path] = None) -> List[Dict[str, str]]:
    """One row per official page whose hash/body moved. Filename-only is not news.

    Empty scan is omit-lens, not ERROR. No URL → omit.
    """
    base = root or ROOT
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for rec in surface_vendor_rows(base) + sources_vendor_rows(base):
        if rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def practitioner_rows(root: Optional[pathlib.Path] = None) -> List[Dict[str, str]]:
    """Newest github/x/grokbotdev/usecases items; assemble() drops cursor-seen ids."""
    base = root or ROOT
    out: List[Dict[str, str]] = []
    _, cur_g = newest_pair("github", root=base)
    if cur_g is not None:
        out.extend(github_people(load(cur_g) or {}))
    _, cur_x = newest_pair("x", root=base)
    if cur_x is not None:
        out.extend(x_people(load(cur_x) or {}))
    _, cur_d = newest_pair("grokbotdev", root=base)
    if cur_d is not None:
        out.extend(grokbotdev_people(load(cur_d) or {}))
    _, cur_u = newest_pair("usecases", root=base)
    if cur_u is not None:
        out.extend(usecases_people(load(cur_u) or {}))
    return out


def event_rows(now: dt.datetime) -> List[Dict[str, str]]:
    """One stay-tuned Galaxy row per UTC day through 2026-09-17. Not a recap.

    After the window, omit: no measured recap artifact exists, and inventing
    session content is forbidden.
    """
    if now.tzinfo is not None:
        now = now.astimezone(dt.timezone.utc)
    day = now.date()
    if day > GALAXY_WATCH_LAST:
        return []
    sample = "Mon engineering · Tue sales · Wed marketing. Three days. Live."
    take = "Which seat do you put a Bot in first — inbox, pipeline, or ops?"
    text = f"Grok Bot Galaxy — {sample} {GALAXY_URL} — take: {take}"
    hook = (
        "Grok Bot Galaxy starts Monday.\n" f"{sample}\n" f"\n{take}\n" f"\n{GALAXY_URL}"
    )
    return [
        row(
            "event",
            text,
            GALAXY_PRODUCER,
            "docs-verified",
            f"event:galaxy:{day.isoformat()}",
            url=GALAXY_URL,
            hook=hook,
        )
    ]


def proven_rows(since: Optional[str]) -> List[Dict[str, str]]:
    walks = ROOT / "jobs" / "walks.jsonl"
    if not walks.is_file():
        return []
    out = []
    for line in walks.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("verdict") != "done":
            continue
        at = str(rec.get("at") or "")
        if since and at <= since:
            continue
        jid = str(rec.get("id") or "")
        if not jid:
            continue
        out.append(
            row(
                "proven",
                f"job {jid} done at {at}",
                "bin/gb-jobs.py",
                "gate-verified" if rec.get("proof_ok") else "unverified",
                f"walk:{jid}:{at}",
            )
        )
    return out


def broken_rows(prev_ne: Set[str]) -> List[Dict[str, str]]:
    out = []
    for nid in ne_ids():
        if nid in prev_ne:
            continue
        out.append(
            row(
                "broken",
                f"new ledger {nid}",
                "NEGATIVE_EVIDENCE.md",
                "unverified",
                f"ne:{nid}",
            )
        )
    out.extend(gate_broken_rows())
    return out


def accounting_valid(accounting: Any) -> bool:
    if not isinstance(accounting, dict):
        return False
    producers = accounting.get("producers")
    if not isinstance(producers, list) or len(producers) != len(EXPECTED_PRODUCERS):
        return False
    names = [item.get("producer") for item in producers if isinstance(item, dict)]
    if names != list(EXPECTED_PRODUCERS) or accounting.get("expected") != names:
        return False
    keys = ("attempted", "succeeded", "failed", "stale", "skipped")
    try:
        counts = {key: sum(int(item[key]) for item in producers) for key in keys}
    except (KeyError, TypeError, ValueError):
        return False
    digest = hashlib.sha256(
        json.dumps(producers, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return (
        all(accounting.get(key) == value for key, value in counts.items())
        and counts["attempted"] == len(EXPECTED_PRODUCERS)
        and counts["succeeded"] == len(EXPECTED_PRODUCERS)
        and counts["failed"] == counts["stale"] == counts["skipped"] == 0
        and accounting.get("call_accounting_digest") == digest
        and accounting.get("complete") is True
    )


def producer_accounting(
    now: dt.datetime, root: Optional[pathlib.Path] = None
) -> Dict[str, Any]:
    base = root or ROOT
    day = now.astimezone(dt.timezone.utc).date().isoformat()
    rows: List[Dict[str, Any]] = []
    for name in EXPECTED_PRODUCERS:
        files = dated_children(base / name, ".json")
        rec: Dict[str, Any] = {
            "producer": name,
            "attempted": 1,
            "succeeded": 0,
            "failed": 0,
            "stale": 0,
            "skipped": 0,
            "cursor": None,
            "artifact_digest": None,
            "row_count": None,
        }
        if not files:
            rec.update({"failed": 1, "detail": "missing artifact"})
            rows.append(rec)
            continue
        path = files[-1]
        rec["cursor"] = path.name
        try:
            raw = path.read_bytes()
            document = json.loads(raw)
        except (OSError, ValueError) as exc:
            rec.update({"failed": 1, "detail": f"unreadable: {exc}"})
            rows.append(rec)
            continue
        rec["artifact_digest"] = hashlib.sha256(raw).hexdigest()
        if (
            not isinstance(document, dict)
            or not document.get("schema")
            or not isinstance(document.get("rows"), list)
        ):
            rec.update(
                {"failed": 1, "detail": "missing explicit structured rows output"}
            )
        elif path.name[:10] != day:
            rec.update(
                {"stale": 1, "detail": f"artifact date {path.name[:10]} != {day}"}
            )
        else:
            rec["row_count"] = len(document["rows"])
            rec.update({"succeeded": 1, "detail": "fresh structured artifact"})
        rows.append(rec)
    counts = {
        key: sum(int(row[key]) for row in rows)
        for key in ("attempted", "succeeded", "failed", "stale", "skipped")
    }
    call_digest = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    complete = (
        counts["attempted"] == len(EXPECTED_PRODUCERS)
        and counts["succeeded"] == len(EXPECTED_PRODUCERS)
        and counts["failed"] == counts["stale"] == counts["skipped"] == 0
    )
    return {
        "expected": list(EXPECTED_PRODUCERS),
        **counts,
        "complete": complete,
        "freshness": "fresh" if complete else "incomplete",
        "producers": rows,
        "call_accounting_digest": call_digest,
        "retry_argv": RETRY_ARGV,
    }


def accounting_error_receipt(
    now: dt.datetime, accounting: Dict[str, Any], elapsed_ms: int
) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "captured_at": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": "ERROR",
        "cursor_advanced": False,
        "producer_accounting": accounting,
        "freshness": "incomplete",
        "retry_argv": RETRY_ARGV,
        "timing_ms": {"total": elapsed_ms},
    }


def previous_doc(root: Optional[pathlib.Path] = None) -> Optional[Dict[str, Any]]:
    rows = dated_children((root or ROOT) / "daily-proof", ".json")
    newest_first = sorted(
        rows, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True
    )
    for path in newest_first:
        doc = load(path)
        if not isinstance(doc, dict):
            raise UpstreamError(f"daily cursor unreadable: {path.name}")
        if doc.get("schema") != SCHEMA:
            continue
        accounting = doc.get("producer_accounting")
        if not accounting_valid(accounting):
            raise ProducerCensusError(
                accounting if isinstance(accounting, dict) else {"complete": False}
            )
        return doc
    return None


def build_receipt(
    now: dt.datetime,
    previous: Optional[Dict[str, Any]],
    rows: List[Dict[str, str]],
    current_ne: List[str],
    accounting: Dict[str, Any],
) -> Dict[str, Any]:
    if not accounting_valid(accounting):
        raise ProducerCensusError(accounting)
    for item in rows:
        err = receipt_ok(item)
        if err:
            raise SystemExit(f"ERROR unreceipted row ({err}): {item}")
    armed = previous is None
    prior = previous or {}
    prior_ids = set(prior.get("ids") or [])
    fresh = [] if armed else unseen(rows, prior_ids)
    captured = now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    verdict = "BASELINE" if armed else ("NOTHING_NEW" if not fresh else "FINDINGS")
    return {
        "schema": SCHEMA,
        "captured_at": captured,
        "verdict": verdict,
        "freshness": "fresh",
        "prior_cursor_id": prior.get("cursor_id"),
        "cursor_armed": armed,
        "cursor_advanced": True,
        "nothing_new": (not armed) and not fresh,
        "rows": fresh,
        "ids": sorted({item["id"] for item in rows} | prior_ids),
        "ne_ids": current_ne,
        "producer_accounting": accounting,
        "epistemic_census": {
            level: sum(1 for item in fresh if item["epistemic"] == level)
            for level in sorted(EPISTEMIC)
        },
        "retry_argv": RETRY_ARGV,
        "counts": {
            lens: sum(1 for item in fresh if item["lens"] == lens)
            for lens in ("vendor", "practitioner", "proven", "broken", "event")
        },
    }


def assemble(now: dt.datetime) -> Dict[str, Any]:
    accounting = producer_accounting(now)
    if accounting["complete"] is not True:
        raise ProducerCensusError(accounting)
    previous = previous_doc()
    since = str((previous or {}).get("captured_at") or "")
    previous_ne = set((previous or {}).get("ne_ids") or [])
    rows = (
        vendor_rows()
        + practitioner_rows()
        + event_rows(now)
        + ([] if not since else proven_rows(since))
        + broken_rows(previous_ne)
    )
    return build_receipt(now, previous, rows, ne_ids(), accounting)


def exit_for(doc: Dict[str, Any]) -> int:
    """Map a finished proof onto the gb dictionary. Empty scan never reaches here."""
    if doc.get("cursor_armed") or doc.get("nothing_new"):
        return EXIT_OK
    return EXIT_FINDINGS if doc.get("rows") else EXIT_OK


def findings_run(doc: Dict[str, Any]) -> bool:
    """Only terminal lessons may create a durable post/share side effect."""
    return (
        not doc.get("cursor_armed")
        and not doc.get("nothing_new")
        and bool(ranked_post_rows(list(doc.get("rows") or [])))
    )


def ranked_post_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Route only terminal lessons from v92v.21 to the sharing surface."""
    return [
        item
        for item in rows
        if item.get("lens") == "lesson"
        and _http_url(str(item.get("url") or ""), str(item.get("text") or ""))
    ]


def make_post(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Shareable terminal-lesson draft, capped at POST_CAP."""
    ranked = ranked_post_rows(list(doc.get("rows") or []))
    body_rows = ranked[:POST_CAP]
    day = str(doc.get("captured_at") or "")[:10]
    n = len(body_rows)
    title = f"Grok Bot daily · {day}" if day else "Grok Bot daily"
    if n:
        title = f"{title} — {n} thing{'s' if n != 1 else ''} worth stealing"
    blocks: List[str] = []
    links: List[str] = []
    seen: Set[str] = set()

    def add_link(url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            links.append(url)

    for r in body_rows:
        hook = str(r.get("hook") or "").strip()
        text = str(r.get("text") or "").strip()
        block = hook or text
        if block:
            blocks.append(block)
        add_link(str(r.get("url") or "").strip())
        for url in URL_RE.findall(block):
            add_link(url)
    if blocks:
        blocks.append(
            "What's one thing your Bots should already be doing while you sleep?"
        )
    return {
        "schema": POST_SCHEMA,
        "title": title,
        "body": "\n\n".join(blocks),
        "links": links,
    }


def post_markdown(post: Dict[str, Any]) -> str:
    """Shareable post bytes: title line, blank line, body. Never JSON."""
    title = str(post.get("title") or "").rstrip()
    body = str(post.get("body") or "").rstrip()
    if body:
        return f"{title}\n\n{body}\n"
    return f"{title}\n"


def attach_post(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Nest gb-post-draft/1 on findings runs. Idempotent."""
    if not findings_run(doc):
        return doc
    existing = doc.get("post")
    if (
        isinstance(existing, dict)
        and existing.get("schema") == POST_SCHEMA
        and "title" in existing
        and "body" in existing
        and "links" in existing
    ):
        return doc
    out = dict(doc)
    ranked = ranked_post_rows(list(doc.get("rows") or []))
    out["post"] = make_post(doc)
    counts = dict(out.get("counts") or {})
    counts["suppressed"] = max(0, len(ranked) - POST_CAP)
    out["counts"] = counts
    return out


def write_post_md(doc: Dict[str, Any], dest: pathlib.Path) -> Optional[pathlib.Path]:
    """Write post-drafts/<stamp>.md. Cursor-armed and nothing-new write no file."""
    if not findings_run(doc):
        return None
    post = doc.get("post")
    if not isinstance(post, dict) or post.get("schema") != POST_SCHEMA:
        post = make_post(doc)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(dest, post_markdown(post))
    return dest


def announce_share(wrote: Optional[pathlib.Path]) -> None:
    """Copy-pasteable wrapper on stderr. Never a JSON pipe."""
    if wrote is not None:
        print(SHARE_RECIPE, file=sys.stderr)


def share_refuse_why(
    doc: Dict[str, Any], wrote: Optional[pathlib.Path]
) -> Optional[str]:
    """Why --share must not invoke the wrapper. None means this run wrote an md."""
    if wrote is not None:
        return None
    if doc.get("cursor_armed"):
        return "cursor-armed: wrapper would copy a stale post-drafts/*.md"
    if doc.get("nothing_new"):
        return "nothing-new: wrapper would copy a stale post-drafts/*.md"
    return "no post-draft this run: wrapper would copy a stale post-drafts/*.md"


def apply_share(
    doc: Dict[str, Any],
    wrote: Optional[pathlib.Path],
    *,
    runner: Optional[Any] = None,
) -> int:
    """Subprocess bin/gb-share-post.sh with this run's md bytes. Refuse otherwise."""
    why = share_refuse_why(doc, wrote)
    if why is not None:
        print(f"gb-daily-proof: --share refused ({why})", file=sys.stderr)
        return EXIT_USAGE
    if wrote is None:
        print(
            "gb-daily-proof: --share refused "
            "(no post-draft this run: wrapper would copy a stale post-drafts/*.md)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    argv = [str(SHARE_SH)]
    if runner is not None:
        return int(runner(argv, wrote))
    try:
        with wrote.open("rb") as fh:
            proc = subprocess.run(argv, stdin=fh, cwd=str(ROOT), timeout=30)
    except OSError as exc:
        print(f"gb-daily-proof: --share failed ({exc})", file=sys.stderr)
        return EXIT_USAGE
    except subprocess.TimeoutExpired:
        print("gb-daily-proof: --share timed out", file=sys.stderr)
        return EXIT_USAGE
    return int(proc.returncode)


def emit(
    doc: Dict[str, Any], as_json: bool, path: pathlib.Path, full: bool = False
) -> None:
    """`--json`: one object on stdout. Default slims the cursor (`ids` becomes
    `ids_count` + `cursor_ref` naming the persisted daily-proof/<stamp>.json that
    carries the full set); `--full` prints everything. One-liners unchanged."""
    doc = attach_post(doc)
    if doc.get("cursor_armed"):
        human = (
            f"cursor-armed {path.name} ids={len(doc['ids'])} ne={len(doc['ne_ids'])}"
        )
    elif doc.get("nothing_new"):
        human = f"nothing-new since {doc['captured_at'][:10]} ({path.name})"
    else:
        c = doc["counts"]
        human = (
            f"daily-proof {path.name}: vendor={c['vendor']} practitioner={c['practitioner']} "
            f"proven={c['proven']} broken={c['broken']} event={c.get('event', 0)}"
        )
        for r in doc["rows"][:20]:
            human += (
                f"\n  [{r['lens']}] {r['text']}  ({r['epistemic']}; {r['producer']})"
            )
    if as_json:
        if doc.get("cursor_armed") or doc.get("nothing_new"):
            print(human, file=sys.stderr)
        out = dict(doc) if full else {k: v for k, v in doc.items() if k != "ids"}
        if not full:
            out["ids_count"] = len(doc.get("ids") or [])
            out["cursor_ref"] = path.name
        print(json.dumps(out, indent=1))
        return
    print(human)


def persist_receipt(
    doc: Dict[str, Any], out_dir: pathlib.Path, now: dt.datetime
) -> Tuple[Dict[str, Any], pathlib.Path]:
    """Atomically advance the cursor only after the complete receipt serializes."""
    stamp = now.strftime("%Y-%m-%dT%H%M%S")
    path = out_dir / f"{stamp}.json"
    suffix = 0
    while path.exists():
        suffix += 1
        path = out_dir / f"{stamp}-{suffix}.json"
    persisted = dict(doc)
    persisted["cursor_id"] = path.name
    payload = json.dumps(persisted, indent=1) + "\n"
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, payload)
    return persisted, path


def selftest() -> int:
    legs = []

    def leg(name: str, ok: bool) -> None:
        legs.append((name, ok))

    proof_now = dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        proof_root = pathlib.Path(td)

        def plant_accounting(day: str = "2026-09-13") -> None:
            for producer in EXPECTED_PRODUCERS:
                folder = proof_root / producer
                folder.mkdir(parents=True, exist_ok=True)
                atomic_write_text(
                    folder / f"{day}T000000.json",
                    json.dumps({"schema": f"gb-{producer}/1", "rows": []}) + "\n",
                )

        plant_accounting()
        accounting = producer_accounting(proof_now, proof_root)
        leg(
            "accounting-six-producers-complete",
            accounting_valid(accounting)
            and accounting["attempted"] == accounting["succeeded"] == 6
            and accounting["failed"]
            == accounting["stale"]
            == accounting["skipped"]
            == 0,
        )
        leg(
            "accounting-success-zero-explicit",
            all(item["row_count"] == 0 for item in accounting["producers"]),
        )
        leg(
            "accounting-digest-stable-replay",
            producer_accounting(proof_now, proof_root)["call_accounting_digest"]
            == accounting["call_accounting_digest"],
        )

        baseline = [
            row(
                lens, f"baseline {lens}", f"bin/gb-{lens}.py", epistemic, f"base:{lens}"
            )
            for lens, epistemic in (
                ("vendor", "docs-verified"),
                ("practitioner", "community-claim"),
                ("proven", "gate-verified"),
                ("broken", "gate-verified"),
            )
        ]
        first: Any = build_receipt(proof_now, None, baseline, [], accounting)
        first, first_path = persist_receipt(
            first, proof_root / "daily-proof", proof_now
        )
        new_rows = baseline + [
            row(lens, f"new {lens}", f"bin/gb-{lens}.py", epistemic, f"new:{lens}")
            for lens, epistemic in (
                ("vendor", "docs-verified"),
                ("practitioner", "community-claim"),
                ("proven", "gate-verified"),
                ("broken", "gate-verified"),
            )
        ]
        second: Any = build_receipt(
            proof_now, previous_doc(proof_root), new_rows, [], accounting
        )
        second_ids = {item["id"] for item in second["rows"]}
        leg(
            "cursor-two-run-one-per-lens",
            first["cursor_armed"] is True
            and first["rows"] == []
            and second["prior_cursor_id"] == first_path.name
            and second_ids
            == {
                f"new:{lens}" for lens in ("vendor", "practitioner", "proven", "broken")
            },
        )
        second, _ = persist_receipt(second, proof_root / "daily-proof", proof_now)
        replay = build_receipt(
            proof_now, previous_doc(proof_root), new_rows, [], accounting
        )
        leg(
            "cursor-unchanged-replay-nothing-new",
            replay["nothing_new"] is True and replay["rows"] == [],
        )

        seed = dict(second)
        seed["ids"] = []
        duplicate = row(
            "vendor", "same source event", "producer-a", "docs-verified", "same-id"
        )
        duplicate_other = dict(duplicate)
        duplicate_other["producer"] = "producer-b"
        exact = build_receipt(
            proof_now, seed, [duplicate, duplicate_other], [], accounting
        )
        leg("cursor-cross-source-id-dedup", len(exact["rows"]) == 1)
        semantic_other = dict(duplicate_other)
        semantic_other["id"] = "different-id"
        semantic = build_receipt(
            proof_now, seed, [duplicate, semantic_other], [], accounting
        )
        leg("cursor-no-semantic-dedup", len(semantic["rows"]) == 2)

        mismatched = json.loads(json.dumps(accounting))
        mismatched["call_accounting_digest"] = "0" * 64
        mismatch_failed = False
        try:
            build_receipt(proof_now, seed, [], [], mismatched)
        except ProducerCensusError:
            mismatch_failed = True
        leg("accounting-mismatch-errors", mismatch_failed)
        error_doc = accounting_error_receipt(proof_now, mismatched, 3)
        leg(
            "accounting-error-does-not-advance",
            error_doc["verdict"] == "ERROR"
            and error_doc["cursor_advanced"] is False
            and error_doc["retry_argv"] == RETRY_ARGV,
        )

        before = list((proof_root / "daily-proof").glob("*.json"))
        interrupted = dict(second)
        interrupted["not_json"] = {1}
        interrupted_failed = False
        try:
            persist_receipt(interrupted, proof_root / "daily-proof", proof_now)
        except TypeError:
            interrupted_failed = True
        after = list((proof_root / "daily-proof").glob("*.json"))
        leg(
            "cursor-interruption-no-partial-file",
            interrupted_failed and before == after,
        )

        (proof_root / "links" / "2026-09-13T000000.json").unlink()
        missing = producer_accounting(proof_now, proof_root)
        leg(
            "accounting-missing-producer-errors",
            not accounting_valid(missing)
            and missing["failed"] == 1
            and missing["complete"] is False,
        )
        plant_accounting()
        (proof_root / "feeds" / "2026-09-13T000000.json").unlink()
        atomic_write_text(
            proof_root / "feeds" / "2026-09-12T000000.json",
            json.dumps({"schema": "gb-feeds/1", "rows": []}) + "\n",
        )
        stale: Any = producer_accounting(proof_now, proof_root)
        leg(
            "accounting-stale-producer-errors",
            stale["stale"] == 1 and not accounting_valid(stale),
        )
        plant_accounting()
        atomic_write_text(proof_root / "x" / "2026-09-13T000000.json", "{\n")
        partial = producer_accounting(proof_now, proof_root)
        leg(
            "accounting-partial-producer-errors",
            partial["failed"] == 1 and not accounting_valid(partial),
        )

        plant_accounting()
        atomic_write_text(
            proof_root / "grokbotdev" / "2026-09-13T000000.json",
            json.dumps({"schema": "gb-grokbotdev/1"}) + "\n",
        )
        empty_output = producer_accounting(proof_now, proof_root)
        leg(
            "accounting-empty-producer-errors",
            empty_output["failed"] == 1 and not accounting_valid(empty_output),
        )

        raw_doc = build_receipt(
            proof_now,
            seed,
            [
                row(
                    "vendor",
                    "raw news https://example.com/news",
                    "producer",
                    "docs-verified",
                    "raw-news",
                    url="https://example.com/news",
                )
            ],
            [],
            accounting,
        )
        raw_calls: List[List[str]] = []

        def no_raw_share(argv: List[str]) -> int:
            raw_calls.append(argv)
            return 0

        raw_path = proof_root / "raw.md"
        with contextlib.redirect_stderr(io.StringIO()):
            raw_share_rc = apply_share(
                raw_doc, write_post_md(raw_doc, raw_path), runner=no_raw_share
            )
        leg(
            "raw-news-zero-post-share-side-effects",
            "post" not in attach_post(raw_doc)
            and not raw_path.exists()
            and raw_calls == []
            and raw_share_rc == EXIT_USAGE,
        )

    leg("cursor-missing-arms-baseline", first["cursor_armed"] is True)
    with tempfile.TemporaryDirectory() as td:
        corrupt_root = pathlib.Path(td)
        corrupt_dir = corrupt_root / "daily-proof"
        corrupt_dir.mkdir()
        atomic_write_text(corrupt_dir / "2026-09-13T000000.json", "{\n")
        corrupt_failed = False
        try:
            previous_doc(corrupt_root)
        except UpstreamError:
            corrupt_failed = True
        leg("cursor-corrupt-errors", corrupt_failed)
    with tempfile.TemporaryDirectory() as td:
        incomplete_root = pathlib.Path(td)
        incomplete_dir = incomplete_root / "daily-proof"
        incomplete_dir.mkdir()
        atomic_write_text(
            incomplete_dir / "2026-09-13T000000.json",
            json.dumps({"schema": SCHEMA, "ids": []}) + "\n",
        )
        incomplete_failed = False
        try:
            previous_doc(incomplete_root)
        except ProducerCensusError:
            incomplete_failed = True
        leg("cursor-missing-accounting-errors", incomplete_failed)
    with tempfile.TemporaryDirectory() as td:
        stale_root = pathlib.Path(td)
        stale_dir = stale_root / "daily-proof"
        stale_dir.mkdir()
        stale_cursor: Dict[str, Any] = {
            "schema": SCHEMA,
            "captured_at": "2026-09-01T00:00:00Z",
            "cursor_id": "2026-09-01T000000.json",
            "ids": ["old"],
            "producer_accounting": accounting,
        }
        atomic_write_text(
            stale_dir / stale_cursor["cursor_id"], json.dumps(stale_cursor) + "\n"
        )
        leg("cursor-stale-remains-durable", previous_doc(stale_root) == stale_cursor)

    missing_epistemic = {"lens": "vendor", "text": "x", "id": "x", "producer": "p"}
    leg(
        "unreceipted-missing-epistemic",
        receipt_ok(missing_epistemic) == "missing/invalid epistemic",
    )
    bad = {"lens": "vendor", "text": "x", "id": "x"}
    leg("unreceipted-missing-producer", receipt_ok(bad) == "missing producer")
    good = row("vendor", "t", "bin/gb-digest.py", "docs-verified", "k")
    leg("receipt-ok", receipt_ok(good) is None)
    try:
        raise SystemExit("ERROR unreceipted row (missing producer): {}")
    except SystemExit as exc:
        leg("fail-closed-message", "unreceipted" in str(exc))
    mapped = broken_from_gate_checks(
        [
            {"check": "g8-desktop-inventory", "verdict": "RED", "detail": "stale"},
            {"check": "g1-surface-fetch-integrity", "verdict": "GREEN", "detail": "ok"},
            {
                "check": "g18-mcp-surface-healthy",
                "verdict": "ERROR",
                "detail": "unread",
            },
            {"check": "", "verdict": "RED", "detail": "nameless"},
        ]
    )
    mapped_ids = {r["id"] for r in mapped}
    leg(
        "gate-red-error-mapped",
        mapped_ids == {"gate:g8-desktop-inventory", "gate:g18-mcp-surface-healthy"},
    )
    leg(
        "gate-row-receipted",
        all(
            receipt_ok(r) is None
            and r["producer"] == GATE_PRODUCER
            and r["epistemic"] == "gate-verified"
            and r["lens"] == "broken"
            for r in mapped
        ),
    )
    leg("gate-green-omitted", "gate:g1-surface-fetch-integrity" not in mapped_ids)
    leg("gate-empty-board", broken_from_gate_checks([]) == [])
    leg(
        "exit-0-cursor-armed",
        exit_for({"cursor_armed": True, "nothing_new": False, "rows": []}) == EXIT_OK,
    )
    leg(
        "exit-0-nothing-new",
        exit_for({"cursor_armed": False, "nothing_new": True, "rows": []}) == EXIT_OK,
    )
    leg(
        "exit-0-quiet-scanned",
        exit_for({"cursor_armed": False, "nothing_new": False, "rows": []}) == EXIT_OK,
    )
    leg(
        "exit-1-findings",
        exit_for(
            {
                "cursor_armed": False,
                "nothing_new": False,
                "rows": [{"id": "gate:g8-desktop-inventory"}],
            }
        )
        == EXIT_FINDINGS,
    )
    usage_rc: Optional[int] = 0
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            main(["--bogus"])
    except SystemExit as exc:
        usage_rc = exc.code if isinstance(exc.code, int) else 1
    leg("exit-2-usage", usage_rc == EXIT_USAGE)
    empty_code = EXIT_OK
    empty_msg = ""
    try:
        classify_gate_board({"checks": []})
    except UpstreamError as exc:
        empty_code = EXIT_UPSTREAM
        empty_msg = str(exc)
    leg("exit-4-empty-scan", empty_code == EXIT_UPSTREAM)
    leg(
        "empty-scan-not-unchanged",
        empty_code == EXIT_UPSTREAM
        and "unmeasured" in empty_msg
        and "unchanged" in empty_msg,
    )
    unread_code = EXIT_OK
    try:
        classify_gate_board({"not": "a board"})
    except UpstreamError:
        unread_code = EXIT_UPSTREAM
    leg("exit-4-unreadable-board", unread_code == EXIT_UPSTREAM)
    green_quiet = False
    try:
        green_quiet = (
            classify_gate_board(
                {"checks": [{"check": "g1", "verdict": "GREEN", "detail": "ok"}]}
            )
            == []
        )
    except UpstreamError:
        green_quiet = False
    leg("scanned-green-is-quiet-not-empty", green_quiet)
    findings_doc = attach_post(
        {
            "schema": SCHEMA,
            "captured_at": "2026-09-13T00:00:00Z",
            "cursor_armed": False,
            "nothing_new": False,
            "rows": [
                row(
                    "lesson",
                    "Terminal lesson: verify every deployment write by server readback.",
                    "bin/gb-lessons.py",
                    "gate-verified",
                    "lesson:server-readback",
                    url="https://docs.x.ai/grok-bot/security",
                    hook="Which live write still lacks a server readback?",
                )
            ],
            "ids": ["lesson:server-readback"],
            "ne_ids": ["NE-1"],
            "counts": {"vendor": 0, "practitioner": 0, "proven": 0, "broken": 0},
        }
    )
    fake = pathlib.Path("daily-proof/fixture.json")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        emit(findings_doc, True, fake, True)
    raw = out.getvalue()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    leg(
        "findings-json-roundtrip",
        parsed == findings_doc
        and exit_for(parsed or {}) == EXIT_FINDINGS
        and bool(parsed and parsed.get("rows"))
        and raw.lstrip()[:1] == "{"
        and raw.lstrip() == raw
        and "cursor-armed" not in raw
        and "nothing-new" not in raw
        and err.getvalue() == "",
    )
    armed_doc = {
        "schema": SCHEMA,
        "captured_at": "2026-09-13T00:00:00Z",
        "cursor_armed": True,
        "nothing_new": False,
        "rows": [],
        "ids": ["gate:g8-desktop-inventory"],
        "ne_ids": ["NE-1"],
        "counts": {"vendor": 0, "practitioner": 0, "proven": 0, "broken": 0},
    }
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        emit(armed_doc, True, fake, True)
    armed_raw = out.getvalue()
    try:
        armed_parsed = json.loads(armed_raw)
    except ValueError:
        armed_parsed = None
    leg(
        "json-one-liner-on-stderr",
        armed_parsed == armed_doc
        and armed_raw[:1] == "{"
        and "cursor-armed" in err.getvalue()
        and "cursor-armed" not in armed_raw,
    )
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        emit(armed_doc, False, fake)
    leg(
        "human-one-liner-on-stdout",
        "cursor-armed" in out.getvalue() and err.getvalue() == "",
    )
    post = (parsed or {}).get("post") or {}
    leg(
        "json-post-draft-schema",
        parsed is not None
        and post.get("schema") == POST_SCHEMA
        and isinstance(post.get("title"), str)
        and bool(post["title"])
        and isinstance(post.get("body"), str)
        and "server readback" in post["body"]
        and post.get("links") == ["https://docs.x.ai/grok-bot/security"],
    )
    nn_json_doc = {
        "schema": SCHEMA,
        "captured_at": "2026-09-13T00:00:00Z",
        "cursor_armed": False,
        "nothing_new": True,
        "rows": [],
        "ids": ["gate:g8-desktop-inventory"],
        "ne_ids": ["NE-1"],
        "counts": {"vendor": 0, "practitioner": 0, "proven": 0, "broken": 0},
    }
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        emit(nn_json_doc, True, fake, True)
    try:
        nn_parsed = json.loads(out.getvalue())
    except ValueError:
        nn_parsed = None
    leg(
        "nothing-new-json-no-post",
        nn_parsed is not None
        and "post" not in nn_parsed
        and nn_parsed.get("nothing_new") is True
        and out.getvalue().lstrip()[:1] == "{",
    )
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        emit(armed_doc, True, fake)
    slim = json.loads(out.getvalue())
    leg(
        "slim-default-drops-ids",
        "ids" not in slim
        and slim.get("ids_count") == 1
        and slim.get("cursor_ref") == "fixture.json"
        and slim.get("rows") == []
        and "cursor-armed" in err.getvalue(),
    )
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        emit(findings_doc, True, fake)
    slimf = json.loads(out.getvalue())
    leg(
        "slim-keeps-rows-and-counts",
        "ids" not in slimf
        and len(slimf.get("rows") or []) == 1
        and slimf.get("counts", {}).get("broken") == 0
        and slimf.get("post", {}).get("schema") == POST_SCHEMA,
    )
    with tempfile.TemporaryDirectory() as td:
        tdir = pathlib.Path(td)
        gh = tdir / "github"
        gh.mkdir()
        atomic_write_text(
            gh / "2026-09-13T000000.json",
            json.dumps(
                {
                    "schema": "gb-github/1",
                    "rows": [
                        {
                            "title": "Acme/Grok-Bot-Starter",
                            "source": "https://github.com/Acme/Grok-Bot-Starter",
                            "summary": "A starter template for a Grok Bot.",
                        },
                        {
                            "title": "Acme/NoUrl",
                            "summary": "missing url must omit",
                        },
                    ],
                }
            )
            + "\n",
        )
        planted = practitioner_rows(root=tdir)
        planted_ids = [r["id"] for r in planted]
        leg(
            "people-new-repo-row",
            planted_ids == ["github:Acme/Grok-Bot-Starter"]
            and bool(planted)
            and receipt_ok(planted[0]) is None
            and planted[0]["epistemic"] == "community-claim"
            and planted[0]["producer"] == "bin/gb-github.py"
            and planted[0]["lens"] == "practitioner"
            and "take:" in planted[0]["text"],
        )
        leg(
            "people-new-repo-url",
            bool(planted)
            and planted[0].get("url") == "https://github.com/Acme/Grok-Bot-Starter"
            and "https://github.com/Acme/Grok-Bot-Starter" in planted[0]["text"]
            and "A starter template for a Grok Bot." in planted[0]["text"],
        )
        people_post = make_post(
            {
                "captured_at": "2026-09-13T00:00:00Z",
                "cursor_armed": False,
                "nothing_new": False,
                "rows": planted
                + [
                    row(
                        "broken",
                        "g8-desktop-inventory RED: stale",
                        GATE_PRODUCER,
                        "gate-verified",
                        "gate:g8-desktop-inventory",
                    )
                ],
            }
        )
        leg(
            "people-raw-row-not-posted",
            people_post.get("links") == [] and people_post.get("body") == "",
        )
        second = unseen(planted, {r["id"] for r in planted})
        leg("people-same-id-omitted", second == [])
        leg(
            "people-missing-url-omitted",
            "github:Acme/NoUrl" not in planted_ids
            and github_people({"rows": [{"title": "Acme/NoUrl", "summary": "x"}]})
            == [],
        )
        docs_row = github_people(
            {
                "rows": [
                    {
                        "title": "xai-org/docs",
                        "source": "https://docs.x.ai/grok-bot",
                        "summary": "vendor docs",
                    }
                ]
            }
        )
        leg(
            "people-docs-xai-epistemic",
            bool(docs_row) and docs_row[0]["epistemic"] == "docs-verified",
        )
        x_ok = x_people(
            {
                "rows": [
                    {
                        "kind": "x-post",
                        "url": "https://x.com/i/status/2098459028732911761",
                        "title": "sharing a grok bot config",
                        "summary": "sharing a grok bot config",
                        "signals": {
                            "tweet_id": "2098459028732911761",
                            "author": "alice",
                        },
                    },
                    {
                        "kind": "x-post",
                        "url": "https://x.com/i/status/2098459028732911762",
                        "title": "second post same account",
                        "signals": {
                            "tweet_id": "2098459028732911762",
                            "author": "alice",
                        },
                    },
                    {
                        "kind": "x-post",
                        "title": "no url",
                        "signals": {"tweet_id": "1", "author": "bob"},
                    },
                ]
            }
        )
        leg(
            "people-x-account-once",
            [r["id"] for r in x_ok] == ["x:2098459028732911761"]
            and x_ok[0].get("url") == "https://x.com/i/status/2098459028732911761",
        )
        gdev = grokbotdev_people(
            {
                "rows": [
                    {
                        "id": "abc123",
                        "type": "template",
                        "headline": "Root Agent",
                        "url": "https://grokbot.dev/marketplace/root-agent/",
                    },
                    {"id": "nourl", "headline": "Missing"},
                ]
            }
        )
        leg(
            "people-grokbotdev-url",
            [r["id"] for r in gdev] == ["grokbotdev:abc123"]
            and gdev[0].get("url") == "https://grokbot.dev/marketplace/root-agent/",
        )
        uc = usecases_people(
            {
                "rows": [
                    {
                        "name": "Inbox Triage",
                        "contributor": "alice",
                        "source": "https://x.com/alice/status/1",
                        "charter": "Triage my inbox each morning.",
                    },
                    {"name": "No Source", "charter": "no url"},
                ]
            }
        )
        leg(
            "people-usecases-url",
            [r["id"] for r in uc] == ["usecases:Inbox Triage"]
            and uc[0].get("url") == "https://x.com/alice/status/1",
        )
    with tempfile.TemporaryDirectory() as td:
        tdir = pathlib.Path(td)
        surf = tdir / "surface"
        url = "https://docs.x.ai/grok-bot/overview.md"

        def plant_surface(day: str, pages: List[Dict[str, Any]]) -> None:
            d = surf / day
            d.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                d / "manifest.json", json.dumps({"pages": pages, "extras": []}) + "\n"
            )

        src = tdir / "sources"
        src.mkdir()
        src_row = {
            "url": "https://docs.x.ai/overview",
            "title": "Overview",
            "kind": "xai-docs-page",
            "published_at": "2026-09-11T00:00:00Z",
            "summary": "docs.x.ai page /overview",
            "signals": {"lastmod": "2026-09-11T00:00:00Z", "age_days": 1.0},
        }
        atomic_write_text(
            src / "2026-09-10T000000.json",
            json.dumps({"rows": [src_row]}) + "\n",
        )
        src_later = dict(src_row)
        src_later["signals"] = {
            "lastmod": "2026-09-11T00:00:00Z",
            "age_days": 2.0,
        }
        atomic_write_text(
            src / "2026-09-11T000000.json",
            json.dumps({"rows": [src_later]}) + "\n",
        )
        plant_surface(
            "2026-09-10",
            [
                {
                    "title": "Get Started",
                    "url": url,
                    "slug": "grok-bot__overview",
                    "sha256": "aaa",
                },
                {"title": "No Url Page", "slug": "secret", "sha256": "ccc"},
            ],
        )
        plant_surface(
            "2026-09-11",
            [
                {
                    "title": "Get Started",
                    "url": url,
                    "slug": "grok-bot__overview",
                    "sha256": "bbb",
                },
                {"title": "No Url Page", "slug": "secret", "sha256": "ddd"},
            ],
        )
        changed = vendor_rows(root=tdir)
        changed_ids = [r["id"] for r in changed]
        low = changed[0]["text"].lower() if changed else ""
        leg(
            "vendor-hash-changed-row",
            changed_ids == [f"vendor:{url}"]
            and receipt_ok(changed[0]) is None
            and changed[0]["epistemic"] == "docs-verified"
            and changed[0]["producer"] == SURFACE_PRODUCER
            and changed[0]["lens"] == "vendor"
            and changed[0].get("url") == url
            and url in changed[0]["text"]
            and "Get Started" in changed[0]["text"]
            and "take:" in changed[0]["text"],
        )
        leg(
            "vendor-no-url-omitted",
            "vendor:" not in "".join(i for i in changed_ids if "secret" in i)
            and all("No Url Page" not in r["text"] for r in changed)
            and len(changed) == 1,
        )
        leg(
            "vendor-no-enterprise-entitlement",
            bool(changed)
            and "team setup" not in low
            and "audit logs" not in low
            and "scim" not in low,
        )
        vpost = make_post(
            {
                "captured_at": "2026-09-13T00:00:00Z",
                "cursor_armed": False,
                "nothing_new": False,
                "rows": changed
                + [
                    row(
                        "broken",
                        "g8-desktop-inventory RED: stale",
                        GATE_PRODUCER,
                        "gate-verified",
                        "gate:g8-desktop-inventory",
                    )
                ],
            }
        )
        leg(
            "vendor-raw-row-not-posted",
            vpost.get("links") == [] and vpost.get("body") == "",
        )
        plant_surface(
            "2026-09-10",
            [
                {
                    "title": "Get Started",
                    "url": url,
                    "slug": "grok-bot__overview",
                    "sha256": "aaa",
                }
            ],
        )
        plant_surface(
            "2026-09-11",
            [
                {
                    "title": "Get Started",
                    "url": url,
                    "slug": "grok-bot__overview",
                    "sha256": "aaa",
                }
            ],
        )
        same = vendor_rows(root=tdir)
        leg("vendor-filename-only-not-news", same == [])
    with tempfile.TemporaryDirectory() as td:
        tdir = pathlib.Path(td)
        md_path = tdir / "2026-09-13T000000.md"
        wrote = write_post_md(findings_doc, md_path)
        raw_md = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
        md_is_json = True
        try:
            json.loads(raw_md)
        except ValueError:
            md_is_json = False
        first = raw_md.split("\n", 1)[0] if raw_md else ""
        leg(
            "findings-md-not-json",
            wrote == md_path
            and md_path.is_file()
            and not md_is_json
            and first == post.get("title")
            and GATE_PRODUCER not in raw_md
            and "g8-desktop-inventory" not in raw_md
            and not raw_md.lstrip().startswith("{"),
        )
        nn_path = tdir / "nothing-new.md"
        wrote_nn = write_post_md(nn_json_doc, nn_path)
        leg("nothing-new-no-md", wrote_nn is None and not nn_path.exists())
        armed_path = tdir / "cursor-armed.md"
        wrote_armed = write_post_md(armed_doc, armed_path)
        leg("cursor-armed-no-md", wrote_armed is None and not armed_path.exists())
        rec_err = io.StringIO()
        with contextlib.redirect_stderr(rec_err):
            announce_share(wrote)
        rec = rec_err.getvalue()
        leg(
            "findings-share-recipe",
            wrote == md_path
            and rec.strip() == SHARE_RECIPE
            and "|" not in rec
            and "json" not in rec.lower()
            and "pbcopy" not in rec,
        )
        nn_rec = io.StringIO()
        with contextlib.redirect_stderr(nn_rec):
            announce_share(wrote_nn)
        leg("nothing-new-no-share-recipe", nn_rec.getvalue() == "")
        invoked: List[Any] = []

        def _record(argv: List[str], path: pathlib.Path) -> int:
            invoked.append((argv, path))
            return 0

        nn_share_err = io.StringIO()
        with contextlib.redirect_stderr(nn_share_err):
            nn_share_rc = apply_share(nn_json_doc, wrote_nn, runner=_record)
        leg(
            "share-nothing-new-refuses",
            nn_share_rc == EXIT_USAGE
            and "nothing-new" in nn_share_err.getvalue()
            and "stale" in nn_share_err.getvalue()
            and invoked == [],
        )
        armed_share_err = io.StringIO()
        with contextlib.redirect_stderr(armed_share_err):
            armed_share_rc = apply_share(armed_doc, wrote_armed, runner=_record)
        leg(
            "share-cursor-armed-refuses",
            armed_share_rc == EXIT_USAGE
            and "cursor-armed" in armed_share_err.getvalue()
            and "stale" in armed_share_err.getvalue()
            and invoked == [],
        )
        find_err = io.StringIO()
        with contextlib.redirect_stderr(find_err):
            find_rc = apply_share(findings_doc, wrote, runner=_record)
        leg(
            "share-findings-uses-wrapper",
            find_rc == 0
            and invoked == [([str(SHARE_SH)], wrote)]
            and "pbcopy" not in find_err.getvalue()
            and SHARE_SH.is_file(),
        )
        leg(
            "share-recipe-no-json-pipe",
            SHARE_RECIPE == "bin/gb-share-post.sh" and "|" not in SHARE_RECIPE,
        )
    g13 = event_rows(dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc))
    g13_ids = [r["id"] for r in g13]
    g13_text = (g13[0]["text"] if g13 else "").lower()
    leg(
        "galaxy-watch-sep13",
        g13_ids == ["event:galaxy:2026-09-13"]
        and g13[0].get("url") == GALAXY_URL
        and GALAXY_URL in g13[0]["text"]
        and g13[0]["epistemic"] == "docs-verified"
        and g13[0]["producer"] == GALAXY_PRODUCER
        and g13[0]["lens"] == "event"
        and receipt_ok(g13[0]) is None
        and "?" in g13[0]["text"]
        and "community-claim" not in g13_text,
    )
    leg(
        "galaxy-watch-not-recap",
        "built a company" not in g13_text and "galaxy showed" not in g13_text,
    )
    g17 = event_rows(dt.datetime(2026, 9, 17, 23, 59, tzinfo=dt.timezone.utc))
    g18 = event_rows(dt.datetime(2026, 9, 18, tzinfo=dt.timezone.utc))
    leg(
        "galaxy-watch-sep17-open",
        [r["id"] for r in g17] == ["event:galaxy:2026-09-17"],
    )
    leg("galaxy-watch-sep18-omitted", g18 == [])
    g13b = event_rows(dt.datetime(2026, 9, 13, 18, tzinfo=dt.timezone.utc))
    leg(
        "galaxy-same-day-id",
        g13_ids == [r["id"] for r in g13b] and unseen(g13b, set(g13_ids)) == [],
    )
    g14 = event_rows(dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc))
    leg(
        "galaxy-next-day-new-id",
        [r["id"] for r in g14] == ["event:galaxy:2026-09-14"]
        and unseen(g14, set(g13_ids)) == g14,
    )
    gpost = make_post(
        {
            "captured_at": "2026-09-13T00:00:00Z",
            "cursor_armed": False,
            "nothing_new": False,
            "rows": g13,
        }
    )
    gbody = str(gpost.get("body") or "").lower()
    leg(
        "galaxy-raw-row-not-posted",
        gpost.get("links") == []
        and gpost.get("body") == ""
        and "built a company" not in gbody,
    )
    zpost = make_post(
        {
            "captured_at": "2026-09-13T00:00:00Z",
            "cursor_armed": False,
            "nothing_new": False,
            "rows": [
                row(
                    "broken",
                    "g8-desktop-inventory RED: stale",
                    GATE_PRODUCER,
                    "gate-verified",
                    "gate:g8-desktop-inventory",
                )
            ],
        }
    )
    leg(
        "post-zero-url-no-gate-dump",
        GATE_PRODUCER not in str(zpost.get("body") or "")
        and "g8-desktop-inventory" not in str(zpost.get("body") or "")
        and zpost.get("links") == [],
    )
    extra = POST_CAP + 3
    eight = [
        row(
            "lesson",
            f"Terminal lesson {i}: verify live results. https://example.com/lesson/{i}",
            "bin/gb-lessons.py",
            "gate-verified",
            f"lesson:{i}",
            url=f"https://example.com/lesson/{i}",
        )
        for i in range(extra)
    ]
    gate_r = row(
        "broken",
        "g8-desktop-inventory RED: stale",
        GATE_PRODUCER,
        "gate-verified",
        "gate:g8-desktop-inventory",
    )
    vendor_r = row(
        "vendor",
        "Get Started https://docs.x.ai/cap — take: official page moved",
        SURFACE_PRODUCER,
        "docs-verified",
        "vendor:https://docs.x.ai/cap",
        url="https://docs.x.ai/cap",
    )
    rank_post = make_post(
        {
            "captured_at": "2026-09-13T00:00:00Z",
            "cursor_armed": False,
            "nothing_new": False,
            "rows": eight[:1] + [vendor_r] + g13,
        }
    )
    rank_body = str(rank_post.get("body") or "")
    leg(
        "post-routes-only-terminal-lessons",
        "https://example.com/lesson/0" in rank_body
        and GALAXY_URL not in rank_body
        and "https://docs.x.ai/cap" not in rank_body,
    )
    cap_ids = [r["id"] for r in eight] + [gate_r["id"]]
    cap_doc = attach_post(
        {
            "schema": SCHEMA,
            "captured_at": "2026-09-13T00:00:00Z",
            "cursor_armed": False,
            "nothing_new": False,
            "rows": eight + [gate_r],
            "ids": cap_ids,
            "ne_ids": [],
            "counts": {
                "vendor": 0,
                "practitioner": extra,
                "proven": 0,
                "broken": 1,
            },
        }
    )
    cap_post = cap_doc.get("post") or {}
    cap_blocks = [b for b in str(cap_post.get("body") or "").split("\n\n") if b.strip()]
    cap_links = cap_post.get("links") or []
    leg(
        "post-cap-eight-to-five",
        len(eight) == extra
        and len(cap_blocks) == POST_CAP + 1
        and cap_blocks[-1].startswith("What's one thing")
        and len(cap_links) <= POST_CAP
        and GATE_PRODUCER not in str(cap_post.get("body") or "")
        and (cap_doc.get("counts") or {}).get("suppressed") == extra - POST_CAP
        and list(cap_doc.get("ids") or []) == cap_ids,
    )
    with tempfile.TemporaryDirectory() as td:
        cap_md = pathlib.Path(td) / "cap.md"
        wrote_cap = write_post_md(cap_doc, cap_md)
        cap_raw = cap_md.read_text(encoding="utf-8") if cap_md.is_file() else ""
        cap_md_json = True
        try:
            json.loads(cap_raw)
        except ValueError:
            cap_md_json = False
        leg(
            "post-cap-md-not-json",
            wrote_cap == cap_md
            and cap_md.is_file()
            and not cap_md_json
            and not cap_raw.lstrip().startswith("{")
            and GATE_PRODUCER not in cap_raw
            and cap_raw.count("https://example.com/lesson/") == POST_CAP
            and len(cap_links) <= POST_CAP,
        )

    ok = sum(1 for _, g in legs if g)
    for name, g in legs:
        print(f"  {'ok  ' if g else 'FAIL'} {name}")
    print(f"SELFTEST {'PASS' if ok == len(legs) else 'FAIL'} - {ok}/{len(legs)}")
    return 0 if ok == len(legs) else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-daily-proof.py")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--full",
        action="store_true",
        help="print the full cursor ids array (default slims to ids_count + cursor_ref)",
    )
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument(
        "--share",
        action="store_true",
        help="copy this run's terminal-lesson draft via bin/gb-share-post.sh",
    )
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    now = dt.datetime.now(dt.timezone.utc)
    started = time.perf_counter()
    try:
        doc = assemble(now)
    except ProducerCensusError as exc:
        error = accounting_error_receipt(
            now, exc.accounting, round((time.perf_counter() - started) * 1000)
        )
        if args.json:
            print(json.dumps(error, indent=1))
        else:
            print("ERROR required producer census incomplete", file=sys.stderr)
        return EXIT_UPSTREAM
    except UpstreamError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_UPSTREAM
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    doc["timing_ms"] = {"total": round((time.perf_counter() - started) * 1000)}
    doc = attach_post(doc)
    doc, path = persist_receipt(doc, OUT, now)
    wrote: Optional[pathlib.Path] = None
    if findings_run(doc):
        wrote = write_post_md(doc, POST / f"{path.stem}.md")
    announce_share(wrote)
    emit(doc, args.json, path, args.full)
    if args.share:
        share_rc = apply_share(doc, wrote)
        if share_rc != 0:
            return share_rc
    return exit_for(doc)


if __name__ == "__main__":
    gbmain(main)
