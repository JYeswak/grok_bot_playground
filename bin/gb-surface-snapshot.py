#!/usr/bin/env python3
"""gb-surface-snapshot — freeze xAI's published Grok Bot surface into a hashed snapshot.

Oracle source 1 of 3 (see AGENTS.md §ORACLE): docs.x.ai is the vendor's own
statement of what Grok Bot can do. We do not control it, so it can falsify our
deployment. This script copies it verbatim and hashes it; gb-surface-gate.py
decides whether the copy is trustworthy and whether anything moved.

Fail-closed contract (the rule this file exists to enforce):
  an empty or short page set is an ERROR, never a pass. "Nothing new" and
  "the fetcher broke" MUST NOT look the same. Every page carries its HTTP
  status, byte count, sha256, and a required-marker flag so the gate can tell
  them apart mechanically.

Usage:
  gb-surface-snapshot.py [--out DIR] [--date YYYY-MM-DD] [--timeout S] [--json]
"""

from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import http.server
import json
import pathlib
import re
import sys
import tempfile
import threading
import urllib.parse
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_bytes, atomic_write_json, atomic_write_text  # noqa: E402
from gbhttp import HostBudget, RobotsPolicy, fetch as _http_fetch  # noqa: E402

LLMS_URL = "https://docs.x.ai/llms.txt"
# Tier-1 producers AGENTS.md §5 promises to poll. They were named in the doc for days while this
# tool fetched only the grok-bot page set — a watch list nobody watched. Measured fetchable
# 2026-09-11; x.ai/news is deliberately absent because it returns 403 to a plain client and a
# promise we cannot keep does not belong in the loop.
#
# THE SECOND OFFICIAL TREE. Added 2026-09-11 after the awesome-grok-bot index made it plain that
# `cursor.com/docs/grok-bot/*` and `cursor.com/help/grok-bot/*` carry official pages with NO
# docs.x.ai equivalent — the TLS-proxy guide exists only there, and the routines page is the only
# official source for Slack-keyword and webhook triggers. `llms.txt` indexes docs.x.ai alone, so
# every canary and freshness claim this repo made covered one of the vendor's two doc trees while
# reading as if it covered the surface.
#
# THE PRACTITIONER INDEX. Also 2026-09-11: the mission is "state of the art every week", and for
# four ticks the loop watched only the vendor. awesome-grok-bot is a 799-entry curated index that
# moves daily; it is the one community source whose CHANGE is a usable signal, so it is fetched,
# hashed, and gated by g13 like everything else here. Community claims are leads, never evidence.
EXTRA_SOURCES = [
    ("release-notes", "https://docs.x.ai/developers/release-notes.md", 4000),
    ("sitemap", "https://docs.x.ai/sitemap.xml", 8000),
    ("status-feed", "https://status.x.ai/feed.xml", 2000),
    ("plans", "https://cursor.com/help/grok-bot/plans", 20000),
    ("cursor-docs-hub", "https://cursor.com/docs/grok-bot", 50000),
    ("cursor-docs-teams", "https://cursor.com/docs/grok-bot/teams", 50000),
    ("cursor-docs-security", "https://cursor.com/docs/grok-bot/security", 50000),
    (
        "cursor-docs-security-faq",
        "https://cursor.com/docs/grok-bot/security-faq",
        50000,
    ),
    ("cursor-docs-identity", "https://cursor.com/docs/grok-bot/identity", 50000),
    (
        "cursor-docs-private-networks",
        "https://cursor.com/docs/grok-bot/private-networks",
        50000,
    ),
    # No docs.x.ai equivalent exists for this one.
    ("cursor-docs-proxies", "https://cursor.com/docs/grok-bot/proxies", 50000),
    ("help-routines", "https://cursor.com/help/grok-bot/routines", 50000),
    ("help-secrets", "https://cursor.com/help/grok-bot/secrets", 50000),
    ("help-connect-plugins", "https://cursor.com/help/grok-bot/connect-plugins", 50000),
    (
        "help-computer-recovery",
        "https://cursor.com/help/grok-bot/computer-recovery",
        50000,
    ),
    (
        "practitioner-index",
        "https://raw.githubusercontent.com/RongleCat/awesome-grok-bot/main/README.md",
        100000,
    ),
]
UA = "grokbot-surface-snapshot/1 (+local weekly refresh; contact: Joshua Nowak)"
REQUIRED_MARKER = "Grok Bot"
MIN_PAGE_BYTES = 500
# The shared HTTP effect's caps, sized against the measured surface: the largest
# committed page is 424 KB, so an 8 MB response cap sits 19x above anything ever
# seen and only ever fires on something nobody meant to snapshot. The host budget
# (64 MB) covers the whole 3.6 MB surface sixteen times over per host per run.
MAX_BODY_BYTES = 8 * 1024 * 1024
HOST_BUDGET_BYTES = 64 * 1024 * 1024
# The incremental-tick store: per-URL ETags (plus the sha256 of the bytes they
# tagged) from the last snapshot, written dated alongside the manifest it
# belongs to. Metadata only — NEVER vendor bytes. A missing or corrupt store
# degrades to a full fetch, never to a failure.
ETAG_STORE_NAME = "etags.json"
ETAG_STORE_VERSION = 1
_HOST_BUDGET = HostBudget(max_bytes_per_host=HOST_BUDGET_BYTES)
_ROBOTS: dict[str, RobotsPolicy] = {}


def _policy_for(url: str) -> RobotsPolicy:
    """One robots policy per host, fetched lazily and fail-open. Verified
    2026-09-12 against the live files: docs.x.ai `Allow: /`, cursor.com's
    disallows match none of our /docs/grok-bot/* or /help/grok-bot/* paths,
    status.x.ai serves no parseable robots.txt, raw.githubusercontent.com 404s —
    every snapshot URL is allowed today, so enforcement changes no byte."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if host not in _ROBOTS:
        _ROBOTS[host] = RobotsPolicy.fetch_for(
            f"{parts.scheme or 'https'}://{host}", user_agent=UA
        )
    return _ROBOTS[host]


def fetch(
    url: str, timeout: float, etag: str = "", prior_path: pathlib.Path | None = None
) -> tuple[int, bytes, str, str, bool]:
    # `Accept: text/markdown` makes docs.x.ai 404 a `.md` URL (measured 2026-09-10);
    # `*/*` is the only value proven to return the page. Do not "improve" this.
    # Transport is gbhttp now; with no stored ETag the request is wire-identical
    # to the old full fetch (no If-None-Match sent), so a run with a store miss
    # is byte-identical to before. With a stored ETag gbhttp sends
    # If-None-Match; a 304 reuses the prior snapshot's bytes for that URL.
    host = urllib.parse.urlsplit(url).hostname or ""

    def _get(tag: str) -> Any:
        return _http_fetch(
            url,
            timeout_s=timeout,
            max_bytes=MAX_BODY_BYTES,
            accept="*/*",
            user_agent=UA,
            etag=tag,
            robots=_policy_for(url),
            on_chunk=lambda n: _HOST_BUDGET.charge(host, n),
        )

    if etag:
        res = _get(etag)
        if res.not_modified:
            if prior_path is not None:
                try:
                    return 200, prior_path.read_bytes(), "", etag, True
                except OSError:
                    pass
            res = _get("")  # 304 but no prior bytes: full fetch, never empty
        return res.status, res.body, res.error, res.etag or etag, False
    res = _get("")
    return res.status, res.body, res.error, res.etag, False


def load_prior_etags(
    out_base: pathlib.Path, today: str
) -> tuple[dict[str, str], pathlib.Path | None]:
    """Return (url -> etag, prior dated dir) from the newest dated snapshot
    older than `today` that carries an etag store. ANY problem — no prior dir,
    no store, corrupt JSON, wrong shape — returns ({}, None): the tick degrades
    to a full fetch, never to a failure."""
    try:
        cands = sorted(
            p
            for p in out_base.iterdir()
            if p.is_dir() and p.name < today and (p / ETAG_STORE_NAME).is_file()
        )
        if not cands:
            return {}, None
        prior = cands[-1]
        raw = json.loads((prior / ETAG_STORE_NAME).read_text(encoding="utf-8"))
        etags = raw.get("etags") if isinstance(raw, dict) else None
        if not isinstance(etags, dict):
            return {}, None
        out = {
            u: e["etag"]
            for u, e in etags.items()
            if isinstance(u, str)
            and isinstance(e, dict)
            and isinstance(e.get("etag"), str)
            and e["etag"]
        }
        return out, prior
    except (OSError, ValueError):
        return {}, None


SECTION_RE = re.compile(r"^## (.+)$")
LINK_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<url>https://docs\.x\.ai/[^)]+)\)")


def slug(url: str) -> str:
    return url.removeprefix("https://docs.x.ai/").removesuffix(".md").replace("/", "__")


def parse_llms(text: str) -> tuple[list[dict[str, str]], int]:
    """Return (grok-bot page rows, total page count across all sections)."""
    section = ""
    rows: list[dict[str, str]] = []
    total = 0
    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            continue
        m = LINK_RE.match(line.strip())
        if not m:
            continue
        url = m.group("url")
        if not url.endswith(".md"):
            continue
        total += 1
        if "/grok-bot/" in url:
            rows.append(
                {
                    "section": section,
                    "title": m.group("title"),
                    "url": url,
                    "slug": slug(url),
                }
            )
    return rows, total


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return _selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", default=str(pathlib.Path(__file__).resolve().parents[1] / "surface")
    )
    ap.add_argument(
        "--date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    )
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    out_base = pathlib.Path(args.out)
    outdir = out_base / args.date
    (outdir / "pages").mkdir(parents=True, exist_ok=True)

    prior_etags, prior_dir = load_prior_etags(out_base, args.date)
    etag_reused = 0
    fresh_etags: dict[str, dict[str, str]] = {}

    def _prior_file(url: str, rel: pathlib.Path) -> pathlib.Path | None:
        return (prior_dir / rel) if prior_dir is not None else None

    status, body, err, index_etag, index_reused = fetch(
        LLMS_URL,
        args.timeout,
        prior_etags.get(LLMS_URL, ""),
        _prior_file(LLMS_URL, pathlib.Path("llms.txt")),
    )
    etag_reused += int(index_reused)
    if index_etag:
        fresh_etags[LLMS_URL] = {
            "etag": index_etag,
            "sha256": hashlib.sha256(body).hexdigest() if body else "",
        }
    manifest: dict[str, Any] = {
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "index_url": LLMS_URL,
        "index_status": status,
        "index_bytes": len(body),
        "index_sha256": hashlib.sha256(body).hexdigest() if body else "",
        "index_error": err,
        "index_reused": index_reused,
        "llms_total_pages": 0,
        "grok_bot_pages_listed": 0,
        "pages": [],
    }
    if status != 200 or not body:
        atomic_write_text(
            (outdir / "manifest.json"), json.dumps(manifest, indent=1) + "\n"
        )
        print(
            f"ERROR index fetch failed: status={status} err={err or 'empty body'}",
            file=sys.stderr,
        )
        return 2

    text = body.decode("utf-8", "replace")
    atomic_write_text((outdir / "llms.txt"), text)
    rows, total = parse_llms(text)
    manifest["llms_total_pages"] = total
    manifest["grok_bot_pages_listed"] = len(rows)
    for row in rows:
        st, raw, e, page_etag, page_reused = fetch(
            row["url"],
            args.timeout,
            prior_etags.get(row["url"], ""),
            _prior_file(row["url"], pathlib.Path("pages") / f"{row['slug']}.md"),
        )
        etag_reused += int(page_reused)
        if page_etag:
            fresh_etags[row["url"]] = {
                "etag": page_etag,
                "sha256": hashlib.sha256(raw).hexdigest() if raw else "",
            }
        content = raw.decode("utf-8", "replace") if raw else ""
        if st == 200 and content:
            atomic_write_text((outdir / "pages" / f"{row['slug']}.md"), content)
        manifest["pages"].append(
            {
                **row,
                "status": st,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest() if raw else "",
                "marker_ok": REQUIRED_MARKER in content,
                "reused": page_reused,
                "error": e,
            }
        )

    # The named Tier-1 sources, fetched and hashed alongside the page set.
    manifest["extras"] = []
    (outdir / "extras").mkdir(exist_ok=True)
    for label, url, floor in EXTRA_SOURCES:
        st, raw, e, extra_etag, extra_reused = fetch(
            url,
            args.timeout,
            prior_etags.get(url, ""),
            _prior_file(url, pathlib.Path("extras") / label),
        )
        etag_reused += int(extra_reused)
        if extra_etag:
            fresh_etags[url] = {
                "etag": extra_etag,
                "sha256": hashlib.sha256(raw).hexdigest() if raw else "",
            }
        if st == 200 and raw:
            atomic_write_bytes((outdir / "extras" / label), raw)
        manifest["extras"].append(
            {
                "label": label,
                "url": url,
                "status": st,
                "bytes": len(raw),
                "min_bytes": floor,
                "sha256": hashlib.sha256(raw).hexdigest() if raw else "",
                "reused": extra_reused,
                "error": e,
            }
        )

    manifest["etag_reused"] = etag_reused
    manifest["etag_store"] = ETAG_STORE_NAME
    atomic_write_text(
        (outdir / ETAG_STORE_NAME),
        json.dumps({"version": ETAG_STORE_VERSION, "etags": fresh_etags}, indent=1)
        + "\n",
    )

    atomic_write_text((outdir / "manifest.json"), json.dumps(manifest, indent=1) + "\n")
    ok = sum(
        1
        for p in manifest["pages"]
        if p["status"] == 200 and p["bytes"] >= MIN_PAGE_BYTES and p["marker_ok"]
    )
    if args.json:
        print(
            json.dumps(
                {
                    "snapshot": str(outdir),
                    "pages_ok": ok,
                    "pages_listed": len(rows),
                    "llms_total_pages": total,
                }
            )
        )
    else:
        ex_ok = sum(
            1
            for x in manifest["extras"]
            if x["status"] == 200 and x["bytes"] >= x["min_bytes"]
        )
        print(
            f"snapshot {outdir}: {ok}/{len(rows)} grok-bot pages OK, {total} pages listed in llms.txt, "
            f"{ex_ok}/{len(manifest['extras'])} extra sources OK"
        )
    return 0 if ok else 2


# Selftest: the three etag-store legs. Local loopback only — no fixture is
# hand-edited, nothing is written outside a temp dir, nothing leaves the machine.
# --------------------------------------------------------------------------------------

_SELFTEST_ETAG = '"snap-v1"'
_SELFTEST_BODY = b"Grok Bot fixture page body for the etag-store legs"


class _SelftestState:
    def __init__(self) -> None:
        self.seen_none_match = "<unset>"


class _SelftestHandler(http.server.BaseHTTPRequestHandler):
    state: _SelftestState

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - must match BaseHTTPRequestHandler's parameter name
        pass

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/page":
            type(self).state.seen_none_match = str(
                self.headers.get("If-None-Match") or ""
            )
            if self.headers.get("If-None-Match") == _SELFTEST_ETAG:
                self.send_response(304)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("ETag", _SELFTEST_ETAG)
                self.send_header("Content-Length", str(len(_SELFTEST_BODY)))
                self.end_headers()
                self.wfile.write(_SELFTEST_BODY)
        else:
            body = b"no such fixture"
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


class _Scope:
    """The selftest's shared bench: where the fixture server listens, what it
    saw, the seeded prior snapshot, and the leg rows each helper appends."""

    def __init__(
        self, base: str, state: _SelftestState, out_base: pathlib.Path
    ) -> None:
        self.base = base
        self.url = f"{base}/page"
        self.state = state
        self.out_base = out_base
        self.prior = out_base / "2026-01-01"
        self.legs: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.legs.append((name, ok, detail))


def _t_store(scope: _Scope) -> None:
    # Store load/save: seed the prior snapshot (bytes + store, the save side)
    # and prove the next tick loads its ETag back.
    (scope.prior / "pages").mkdir(parents=True)
    atomic_write_bytes((scope.prior / "pages" / "p.md"), _SELFTEST_BODY)
    atomic_write_json(
        (scope.prior / ETAG_STORE_NAME),
        {
            "version": ETAG_STORE_VERSION,
            "etags": {
                scope.url: {
                    "etag": _SELFTEST_ETAG,
                    "sha256": hashlib.sha256(_SELFTEST_BODY).hexdigest(),
                }
            },
        },
    )
    etags, prior_dir = load_prior_etags(scope.out_base, "2026-01-02")
    scope.check(
        "store-loads-prior-etag",
        etags.get(scope.url) == _SELFTEST_ETAG and prior_dir == scope.prior,
        f"got etags={etags!r} prior_dir={prior_dir}",
    )


def _t_reuse(scope: _Scope) -> None:
    # 304-reuse variants: a hit reuses the prior bytes verbatim; a hit with
    # no readable prior bytes refetches full content, never an empty page.
    etags, _ = load_prior_etags(scope.out_base, "2026-01-02")
    st, raw, err, tag, reused = fetch(
        scope.url, 5.0, etags.get(scope.url, ""), scope.prior / "pages" / "p.md"
    )
    scope.check(
        "304-hit-reuses-bytes",
        st == 200 and raw == _SELFTEST_BODY and reused and err == "",
        f"got status={st} bytes={len(raw)} reused={reused} err={err!r}",
    )
    st4, raw4, err4, _, reused4 = fetch(
        scope.url, 5.0, _SELFTEST_ETAG, scope.prior / "pages" / "gone.md"
    )
    scope.check(
        "304-without-prior-bytes-refetches",
        st4 == 200 and raw4 == _SELFTEST_BODY and not reused4,
        f"got status={st4} bytes={len(raw4)} reused={reused4} err={err4!r}",
    )


def _t_degrade(scope: _Scope) -> None:
    # Miss/corrupt degrade: no store, or an unreadable one, is a full fetch
    # that sends no validator — wire-identical to the pre-store behavior.
    scope.state.seen_none_match = "<unset>"
    etags2, prior2 = load_prior_etags(scope.out_base, "2026-01-01")
    scope.check(
        "store-miss-loads-empty",
        etags2 == {} and prior2 is None,
        f"got etags={etags2!r} prior_dir={prior2}",
    )
    st2, raw2, err2, tag2, reused2 = fetch(scope.url, 5.0)
    scope.check(
        "store-miss-fetches",
        st2 == 200
        and raw2 == _SELFTEST_BODY
        and not reused2
        and tag2 == _SELFTEST_ETAG,
        f"got status={st2} bytes={len(raw2)} reused={reused2} etag={tag2!r}",
    )
    scope.check(
        "store-miss-sends-no-validator",
        scope.state.seen_none_match == "",
        f"server saw If-None-Match={scope.state.seen_none_match!r}",
    )
    atomic_write_text((scope.prior / ETAG_STORE_NAME), "{not json")
    etags3, prior3 = load_prior_etags(scope.out_base, "2026-01-02")
    scope.check(
        "corrupt-store-loads-empty",
        etags3 == {} and prior3 is None,
        f"got etags={etags3!r} prior_dir={prior3}",
    )
    st3, raw3, err3, _, reused3 = fetch(scope.url, 5.0)
    scope.check(
        "corrupt-store-degrades",
        st3 == 200 and raw3 == _SELFTEST_BODY and not reused3,
        f"got status={st3} bytes={len(raw3)} err={err3!r}",
    )


def _t_byte_identity(scope: _Scope) -> None:
    # Byte-identity proof (asserts, not legs — the leg count stays 8): the
    # reused bytes hash exactly to the prior file, and a store-miss fetch
    # returns exactly the committed bytes. A failure raises, never passes.
    prior_body = (scope.prior / "pages" / "p.md").read_bytes()
    st, raw, err, _, reused = fetch(
        scope.url, 5.0, _SELFTEST_ETAG, scope.prior / "pages" / "p.md"
    )
    assert st == 200 and reused and err == "", (st, len(raw), reused, err)
    assert hashlib.sha256(raw).hexdigest() == hashlib.sha256(prior_body).hexdigest()
    _, miss_raw, _, _, _ = fetch(scope.url, 5.0)
    assert miss_raw == prior_body, (len(miss_raw), len(prior_body))


def _selftest() -> int:
    state = _SelftestState()
    _SelftestHandler.state = state
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SelftestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="gb-snapshot-etag-selftest-") as tmp:
            scope = _Scope(
                f"http://127.0.0.1:{server.server_address[1]}",
                state,
                pathlib.Path(tmp),
            )
            _t_store(scope)
            _t_reuse(scope)
            _t_degrade(scope)
            _t_byte_identity(scope)
    finally:
        server.shutdown()
        server.server_close()

    failed = [name for name, ok, _ in scope.legs if not ok]
    for name, ok, detail in scope.legs:
        print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
