#!/usr/bin/env python3
"""gb-feeds — the daily blogs/RSS/practitioner leg of the mining ecosystem.

Vendor docs move (183/183 `docs.x.ai` pages changed in the last 7 days) and GitHub moves; this is
the third producer, the one that catches what neither of those can see: a changelog entry, a
practitioner thread, an ecosystem post. It polls an explicit allowlist of feeds and re-reads the
use-case corpus this repo already holds, and it emits one flat row list with content-addressed ids
so a daily tick can diff today against yesterday instead of re-reading the world.

**Why an id and not a timestamp.** A feed re-serves the same item every day. If a row carried the
fetch time it would look new forever, and a daily loop built on it would report the same 30 items
each morning. `rows[].id` is `blake2b-16(kind + canonical url)` — tracking parameters and trailing
slashes stripped — so a story seen on two consecutive days, or in two different feeds, is ONE row.
Volatile per-run facts live in `sources[]`, never in `rows[]`; `--selftest` asserts that.

**X/Twitter is not scraped here, on purpose.** `x.com` is 403/ToS-hostile to plain clients (and
`https://x.ai/news` is a measured 403 — see NEGATIVE_EVIDENCE family NE-1). Our legitimate window
onto practitioners is the curated corpus `bin/gb-usecases.py` already harvests: 645 Bot definitions
each carrying a contributor, an `x.com` status permalink, and an `added_at` date. This file reads
that artifact off disk and emits "who shipped what in the last N days" from it. It issues zero
requests to x.com, and it never will — if the corpus is stale, that is a corpus problem with its
own producer and its own gate (g21), not a licence to scrape.

**Two traps this file exists to survive** (both measured 2026-09-11 against the live web):

  1. A **200 that is not a feed.** `https://cursor.com/rss.xml` returns `200 text/html` — a 139 KB
     single-page-app shell. A naive parser finds zero `<item>` elements and reports "no news".
     Every fetch is therefore classified: HTML body or a root element that is not
     `rss`/`feed`/`RDF` is recorded as `error: not-a-feed`, which is a FACT, never an empty list.
  2. A **transient 5xx.** `https://hnrss.org/newest?q=...` answered 502 on the first probe and 200
     ninety seconds later. One retry with a pause; `sources[].attempts` records how many it took.

Measured status of the allowlist on 2026-09-11 (`--probe`): `cursor.com/changelog/rss.xml` 200 /
122 KB, `news.ycombinator.com/rss` 200, both `hnrss.org` query feeds 200 (after retry),
`lobste.rs/t/ai.rss` 200, `status.x.ai/feed.xml` 200, `simonwillison.net/atom/everything/` 200
(Atom), `reddit.com/r/cursor/.rss` 200 (Atom). Dead, and deliberately NOT in the allowlist:
`cursor.com/blog/rss.xml` 404, `cursor.com/blog/rss` 404, `cursor.com/changelog/rss` 404,
`cursor.com/blog/feed.xml` 404, `x.ai/news/rss.xml` 404, `cursor.com/rss.xml` soft-404.

  gb-feeds.py collect            # write feeds/<stamp>.json, print what it found
  gb-feeds.py collect --json     # same, machine-readable summary on stdout
  gb-feeds.py probe              # status of every allowlisted feed, write nothing
  gb-feeds.py --selftest         # offline proofs over inline fixtures

Exit codes: 0 measured · 2 ERROR (no feed reachable, or the parser proved broken). An unreachable
allowlist is an instrument failure, not a quiet day — it must never render as "nothing new".
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import email.utils
import hashlib
import inspect
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any, Callable, NamedTuple, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import Cancelled, Cx, atomic_write_text, main as gbmain  # noqa: E402

SCHEMA = "gb-feeds/1"
UA = "grokbot-feeds/1 (+local daily refresh; https://docs.x.ai/grok-bot)"
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"
TIMEOUT = 20
RETRY_SLEEP = 2.0
POLITE_SLEEP = 1.1  # ≤1 req/sec to any one host
# Worst case ONE feed can cost the region: the try, the one retry, the pause between them,
# and the production politeness spacing. The region deadline in `build_document` is this
# times the feed budget, so a hung transport raises Cancelled instead of hanging the tick
# past what the budget promised. Named, not sprinkled: it moves iff the policy moves.
FEED_WORST_S: float = TIMEOUT * 2 + RETRY_SLEEP + POLITE_SLEEP
SUMMARY_CHARS = 400
PRACTITIONER_WINDOW_DAYS = 14
TOP_CONTRIBUTORS = 15


class Feed(NamedTuple):
    """One allowlisted feed. `filtered` means a broad feed whose items must match TOPIC_RE.

    Extending this list is the intended way to widen daily coverage: add a row, run `probe`, and
    keep it only if it answers 200 with a parseable feed. Every url here was measured, and its
    measured status is in this module's docstring.
    """

    url: str
    label: str
    filtered: bool
    why: str


FEEDS: tuple[Feed, ...] = (
    Feed(
        "https://cursor.com/changelog/rss.xml",
        "cursor-changelog",
        False,
        "the vendor's own shipped-changes feed — every entry is relevant by construction",
    ),
    Feed(
        "https://status.x.ai/feed.xml",
        "xai-status",
        False,
        "incidents touching Bots or the cloud computer; Tier-1 producer per AGENTS.md §5",
    ),
    Feed(
        "https://hnrss.org/newest?q=%22grok+bot%22",
        "hn-query-grok-bot",
        False,
        "HN query feed, exact phrase — already scoped, so no title filter",
    ),
    Feed(
        "https://hnrss.org/newest?q=grokbot",
        "hn-query-grokbot",
        False,
        "HN query feed, one-word spelling practitioners actually use",
    ),
    Feed(
        "https://news.ycombinator.com/rss",
        "hn-frontpage",
        True,
        "broad: front page, filtered to grok/cursor/agent/mcp titles",
    ),
    Feed(
        "https://lobste.rs/t/ai.rss",
        "lobsters-ai",
        True,
        "broad: the ai tag, filtered — smaller and more technical than HN",
    ),
    Feed(
        "https://simonwillison.net/atom/everything/",
        "simonwillison",
        True,
        "practitioner blog, Atom, posts near-daily on agent tooling; proves the Atom path live",
    ),
    Feed(
        "https://www.reddit.com/r/cursor/.rss",
        "reddit-cursor",
        True,
        "Atom; operator chatter about the client this deployment runs. Measured 2026-09-11: "
        "reddit answers 429 to a shared UA after roughly four requests in a few minutes, and "
        "the retry gets 429 too — so a 429 here means WE were impolite, not that the feed died. "
        "One request per daily tick is inside the budget; a 429 is recorded, never silent",
    ),
)


class Retired(NamedTuple):
    """A candidate that was measured DEAD and is therefore not polled daily.

    Kept in the code and emitted into `retired_sources` rather than deleted, for two reasons: a
    url nobody recorded as dead gets re-proposed every few weeks, and a feed can come back (Cursor
    has no blog feed today; it may ship one). `probe --retired` re-measures the whole list, so
    reviving one is a measurement, not a hunch.
    """

    url: str
    status: int
    measured_at: str
    why: str


RETIRED: tuple[Retired, ...] = (
    Retired(
        "https://cursor.com/blog/rss.xml",
        404,
        "2026-09-11",
        "no blog feed at this path",
    ),
    Retired(
        "https://cursor.com/blog/rss",
        404,
        "2026-09-11",
        "alternate blog path, also 404",
    ),
    Retired(
        "https://cursor.com/blog/feed.xml",
        404,
        "2026-09-11",
        "third blog path, also 404",
    ),
    Retired(
        "https://cursor.com/changelog/rss",
        404,
        "2026-09-11",
        "only the .xml suffix exists — the extensionless path is not an alias",
    ),
    Retired(
        "https://cursor.com/rss.xml",
        200,
        "2026-09-11",
        "SOFT 404: answers 200 with 139 KB of text/html app shell, zero feed items. The trap "
        "this producer's not-a-feed classifier exists for — never treat it as an empty feed",
    ),
    Retired(
        "https://x.ai/news/rss.xml", 404, "2026-09-11", "x.ai publishes no news feed"
    ),
    Retired(
        "https://x.ai/news",
        403,
        "2026-09-10",
        "bot protection refuses plain clients (NEGATIVE_EVIDENCE family NE-1); needs a "
        "browser-grade reader, which this producer deliberately is not",
    ),
)

# What counts as on-topic for a BROAD feed. Deliberately wider than "grok bot": the point of a
# daily mine is to see the ecosystem move, and `agent`/`mcp` is where that movement shows up first.
TOPIC_RE = re.compile(
    r"grok\s*bots?|grokbot|\bgrok\b|\bcursor\b|anysphere|\bagentic\b|\bagents?\b|\bmcp\b"
    r"|claude\s*code|\bcodex\b",
    re.I,
)

# Query parameters that identify a referral, not a document. Dropped before hashing so the same
# story arriving from two places hashes once. `id` is NOT here — HN item links need it.
DROP_PARAMS = {
    "ref",
    "ref_src",
    "ref_url",
    "source",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "share_id",
}
ITEM_TAGS = {"item", "entry"}
FEED_ROOTS = {"rss", "feed", "rdf"}


def now_z(when: Optional[dt.datetime] = None) -> str:
    """ISO-8601 in UTC with a `Z`, to the second. One timestamp format in this artifact."""
    d = when or dt.datetime.now(dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _silence(stream: object) -> None:
    """Point the stream's fd at /dev/null so the interpreter's shutdown flush cannot re-raise.

    Guarded: a stream with no real fd (a StringIO in a test) is left alone.
    """
    try:
        fd = stream.fileno()  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        return
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), fd)
    except OSError:
        pass


def emit(text: str) -> None:
    """`print` to stdout, except that a reader who walked away is not a failed measurement.

    Measured 2026-09-11: `gb-feeds.py probe | head -0` raised BrokenPipeError and exited **1**,
    and on `collect` that happens AFTER the artifact is already written — a completely successful
    run reporting FAILURE to the scheduler purely because a human piped it into `head`. (Surfaced
    by a sibling producer's own verification; this file had the identical wart on every stdout
    path, `--selftest` included.)

    Swallowing EPIPE *here* rather than with a top-level `except BrokenPipeError: return 0` is
    deliberate: the top-level form would also convert a genuine ERROR exit (2) into 0 whenever the
    reader happened to close early. This keeps both the verdict and the exit code honest.
    """
    try:
        print(text)
    except BrokenPipeError:
        _silence(sys.stdout)


def warn(text: str) -> None:
    """`print` to stderr, with the same guard — because STDERR carries the verdicts.

    Measured 2026-09-11 on a mutant of this file with one deliberately failing leg:
    `--selftest 2>&1 | head -0` exited **0** while the selftest was FAILING (40/41), because the
    unguarded `print(..., file=sys.stderr)` raised EPIPE, unwound past the pre-computed verdict,
    and hit `gbtypes.main`'s unconditional BrokenPipeError→0 arm. `2>/dev/null | head -0` exited 1
    correctly, which is what proved the exposure was the STDERR half specifically.

    A closed pipe may cost the MESSAGE. It must never change the VERDICT.
    """
    try:
        print(text, file=sys.stderr)
    except BrokenPipeError:
        _silence(sys.stderr)


def recover_cancellation(exc: BaseException) -> Optional[int]:
    """The exit code a `Cancelled` was about to report, or None when this EPIPE is not a cancel.

    Extracted from the entry block so it can be EXECUTED by `--selftest`: logic that only lives
    inside `if __name__ == "__main__":` is never run by any test, so its behaviour is measured
    once by hand and then unpinned forever.

    The NEGATIVE answers carry the weight. Returning a default (say 130) for an EPIPE with no
    `Cancelled` context would manufacture a cancellation that never happened — a producer that
    died for some other reason would report "cancelled by signal 2" to the scheduler. None means
    "this is not mine to interpret", and the caller re-raises instead of inventing a verdict.
    """
    ctx = exc.__context__
    return ctx.exit_code if isinstance(ctx, Cancelled) else None


def canon_url(raw: Optional[str]) -> str:
    """One url string per document, so two producers that saw it differently agree on the id.

    Drops the fragment and referral parameters, sorts what survives, normalises the trailing
    slash, lowercases scheme and host, folds `http` → `https`, and strips a leading `www.`.

    The last two exist because only THIS producer sees raw links: `gb-github` builds
    `https://github.com/<owner>/<repo>` from an API field, so it can never emit `http://` or
    `www.`, while an RSS item routinely does. Without this fold, a feed link to a repo would not
    collide with that repo's own row and the shared store would hold the same thing twice.
    Deep links (`/issues/4`, `/tree/main/x`) stay distinct — they are different resources.

    Cost of the fold: a host that serves DIFFERENT content at `www.` and bare, or a path that
    differs only in case, merges. Neither has been observed in this allowlist; both would be
    visible as one row with two `signals.feeds` labels rather than as silent data loss.
    """
    if not raw:
        return ""
    p = urllib.parse.urlsplit(raw.strip())
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in DROP_PARAMS and not k.lower().startswith("utm_")
    ]
    scheme = p.scheme.lower()
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return urllib.parse.urlunsplit(
        (
            "https" if scheme in ("", "http") else scheme,
            host,
            p.path.rstrip("/") or "/",
            urllib.parse.urlencode(sorted(kept)),
            "",
        )
    )


def content_id(url: str, *, fallback: str = "") -> str:
    """The row's content address, shared with the other daily producers.

    `blake2b(canonical_url.lower(), digest_size=16)` → 32 hex. Agreed with `gb-github`'s miner on
    2026-09-11 so that ONE downstream store keys all three legs the same way: a repo url that
    appears in an RSS mirror hashes to the same row here as it does there, which is the whole point
    of a content address. The kind is deliberately NOT in the hash — two producers seeing the same
    document must collide.

    Lowercasing the whole canonical url (not just scheme+host) is a deliberate trade: it merges
    `/Elie222/x` with `/elie222/x`, which is what practitioners actually type, at the cost of
    merging two paths that differ only in case. No such pair has been observed in these feeds.

    A row with no url at all (a feed item carrying only a guid) hashes a `urn:gb-feeds:` key, so it
    is still stable across runs and still cannot collide with a real url.
    """
    key = canon_url(url).lower() or f"urn:gb-feeds:{fallback.strip().lower()}"
    return hashlib.blake2b(key.encode("utf-8"), digest_size=16).hexdigest()


class _Strip(HTMLParser):
    """Feed summaries are HTML fragments. Take the text, leave the markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain(raw: Optional[str], limit: int = SUMMARY_CHARS) -> str:
    if not raw:
        return ""
    p = _Strip()
    p.feed(raw)
    p.close()
    text = re.sub(r"\s+", " ", "".join(p.parts)).strip()
    return (text[: limit - 1] + "\u2026") if len(text) > limit else text


def norm_date(raw: Optional[str]) -> Optional[str]:
    """RFC-822 (RSS) or ISO-8601 (Atom) → `YYYY-MM-DDTHH:MM:SSZ`. Unparseable → None, never a guess.

    ISO is tried first when the string starts with a date, because `parsedate_to_datetime` will
    accept some ISO strings and read them wrong.
    """
    if not raw:
        return None
    s = raw.strip()
    parsed: Optional[dt.datetime] = None
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
    if parsed is None:
        try:
            parsed = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError):
            parsed = None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return now_z(parsed)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, *names: str) -> Optional[str]:
    for el in node:
        if _local(el.tag) in names:
            text = "".join(el.itertext()).strip()
            if text:
                return text
    return None


def _child_link(node: ET.Element) -> Optional[str]:
    """RSS puts the url in `<link>`'s text; Atom puts it in `<link href=… rel=alternate>`."""
    fallback: Optional[str] = None
    for el in node:
        if _local(el.tag) != "link":
            continue
        text = (el.text or "").strip()
        if text:
            return text
        href = (el.get("href") or "").strip()
        if not href:
            continue
        if (el.get("rel") or "alternate") == "alternate":
            return href
        fallback = fallback or href
    return fallback


def parse_feed(body: bytes) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Parse RSS `<item>` and Atom `<entry>` into normalised dicts. Returns (items, error).

    An error is returned — not raised and not swallowed — so the caller can record it in
    `sources[]`. Zero items plus no error means the feed really is empty; zero items plus an
    error means we learned nothing. Those two must never look the same.
    """
    head = body[:512].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        return [], "not-a-feed: html document"
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return [], f"not-xml: {exc}"
    if _local(root.tag) not in FEED_ROOTS:
        return [], f"not-a-feed: root element <{_local(root.tag)}>"
    items: list[dict[str, Any]] = []
    for node in root.iter():
        if _local(node.tag) not in ITEM_TAGS:
            continue
        title = plain(_child_text(node, "title"), 300)
        link = _child_link(node)
        guid = _child_text(node, "guid", "id")
        items.append(
            {
                "title": title,
                "url": link or (guid if (guid or "").startswith("http") else "") or "",
                "guid": guid or "",
                "published_at": norm_date(
                    _child_text(node, "pubdate", "published", "updated", "date")
                ),
                "summary": plain(
                    _child_text(node, "description", "summary", "content", "subtitle")
                ),
            }
        )
    return items, None


def topic_hits(*fields: str) -> list[str]:
    """Every distinct on-topic term in the given text, lowercased. Empty list means no match."""
    seen: list[str] = []
    for field in fields:
        for m in TOPIC_RE.findall(field or ""):
            term = re.sub(r"\s+", " ", m.lower())
            if term not in seen:
                seen.append(term)
    return seen


Fetcher = Callable[[str], "tuple[dict[str, Any], Optional[bytes]]"]


def fetch(
    url: str, *, timeout: int = TIMEOUT, retries: int = 1
) -> tuple[dict[str, Any], Optional[bytes]]:
    """One feed, with a deadline and one retry on a transient failure.

    The returned meta ALWAYS carries a status field: an int for an HTTP answer, None only when the
    connection itself never produced one. `error` names what happened. A non-200 is recorded, never
    dropped — `https://x.ai/news` answering 403 is data about the source, not an absence of news.
    """
    meta: dict[str, Any] = {
        "url": url,
        "status": None,
        "bytes": 0,
        "content_type": "",
        "fetched_at": now_z(),
        "attempts": 0,
        "error": None,
    }
    attempt = 0
    while True:
        attempt += 1
        meta["attempts"] = attempt
        meta["fetched_at"] = now_z()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": ACCEPT})
        transient = False
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                meta["status"] = getattr(resp, "status", None) or resp.getcode()
                meta["bytes"] = len(body)
                meta["content_type"] = resp.headers.get("Content-Type", "") or ""
                meta["error"] = None
                return meta, body
        except urllib.error.HTTPError as exc:
            meta["status"] = exc.code
            meta["bytes"] = 0
            meta["error"] = f"http {exc.code} {exc.reason}"
            transient = exc.code >= 500 or exc.code == 429
        except urllib.error.URLError as exc:
            meta["error"] = f"{type(exc).__name__}: {exc.reason}"
            transient = True
        except (OSError, ValueError) as exc:
            meta["error"] = f"{type(exc).__name__}: {exc}"
            transient = True
        if transient and attempt <= retries:
            time.sleep(RETRY_SLEEP)
            continue
        return meta, None


def polite_fetch(url: str) -> tuple[dict[str, Any], Optional[bytes]]:
    """The production fetcher: `fetch` plus a pause, so a run never bursts a host."""
    out = fetch(url)
    time.sleep(POLITE_SLEEP)
    return out


def feed_rows(
    feed: Feed, fetcher: Fetcher
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch and parse one feed. Returns (source-record, rows). Never raises on a dead source."""
    meta, body = fetcher(feed.url)
    source = dict(meta)
    source.update(
        {"label": feed.label, "kind": "feed", "filtered": feed.filtered, "items": 0}
    )
    if body is None:
        source["rows"] = 0
        return source, []
    items, err = parse_feed(body)
    source["items"] = len(items)
    if err:
        source["error"] = err
        source["rows"] = 0
        return source, []
    rows: list[dict[str, Any]] = []
    for item in items:
        # TITLE only. Body text is the wrong evidence for relevance: a sourdough post whose blurb
        # says "no agents were involved" matches `agents?` and would enter the artifact as an
        # ecosystem signal. Body hits are still recorded — as a signal to look at, never as the
        # admission criterion. (Caught by the `filter-rejects-offtopic` selftest leg.)
        hits = topic_hits(item["title"]) if feed.filtered else ["unfiltered"]
        if not hits:
            continue
        fallback = item["guid"] or item["title"]
        if not (item["url"] or fallback):
            continue
        rows.append(
            {
                "id": content_id(item["url"], fallback=fallback),
                "source": feed.url,
                "kind": "feed-item",
                "title": item["title"] or "(untitled)",
                "url": item["url"],
                "published_at": item["published_at"],
                "summary": item["summary"],
                "signals": {
                    "feeds": [feed.label],
                    "matched": hits,
                    "body_hits": topic_hits(item["summary"]),
                    "guid": item["guid"],
                },
            }
        )
    source["rows"] = len(rows)
    return source, rows


def load_corpus(root: pathlib.Path) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Newest `usecases/<stamp>.json` off disk, plus its source record. No network, ever."""
    d = root / "usecases"
    files = sorted(
        p for p in d.glob("*.json") if re.match(r"^\d{4}-\d{2}-\d{2}", p.name)
    )
    meta: dict[str, Any] = {
        "url": "usecases/",
        "kind": "local-corpus",
        "label": "usecases",
        "status": None,
        "bytes": 0,
        "fetched_at": now_z(),
        "attempts": 1,
        "error": "absent: no usecases/<stamp>.json — run bin/gb-usecases.py",
        "items": 0,
        "rows": 0,
    }
    if not files:
        return None, meta
    path = files[-1]
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        meta["error"] = f"unreadable: {type(exc).__name__}: {exc}"
        return None, meta
    meta.update(
        {
            "url": f"usecases/{path.name}",
            "status": 200,
            "bytes": len(path.read_bytes()),
            "error": None,
            "items": len(doc.get("rows") or []),
        }
    )
    return doc, meta


def practitioner_rows(
    corpus: dict[str, Any], *, now: dt.datetime, days: int
) -> list[dict[str, Any]]:
    """Who shipped what, from the corpus we already hold — recent entries plus top contributors.

    Two row shapes, one kind. `signals.role == "entry"` is one Bot definition added inside the
    window; `signals.role == "contributor"` is a rollup of one practitioner's whole footprint, so
    the daily tick can see the people as well as the artefacts. Both ids are content-addressed, so
    tomorrow's run re-emits the same id for the same entry.
    """
    rows: list[dict[str, Any]] = []
    cutoff = (now - dt.timedelta(days=days)).date()
    entries = corpus.get("rows") or []
    by_contributor: collections.Counter[str] = collections.Counter()
    latest: dict[str, str] = {}
    for e in entries:
        who = (e.get("contributor") or "").strip()
        added = (e.get("added_at") or "").strip()
        if who:
            by_contributor[who] += 1
            if added > latest.get(who, ""):
                latest[who] = added
        try:
            when = dt.date.fromisoformat(added)
        except ValueError:
            continue
        if when < cutoff:
            continue
        url = e.get("source") or ""
        name = e.get("name") or "(unnamed Bot)"
        integrations = list(e.get("integrations") or [])
        rows.append(
            {
                # The identity of this row is the BOT DEFINITION, not the tweet that announced it.
                # Measured on the live corpus 2026-09-11: 327 in-window entries share only 292
                # distinct `source` urls — one x.com status announces up to SIX different Bots,
                # and 35 entries carry no source at all. Hashing the url collapsed 63 real Bot
                # definitions into other rows. So an entry is addressed by contributor/name and
                # keeps the permalink in `url` for provenance; only the contributor ROLLUP below
                # is url-addressed, because a profile url really is that person's identity.
                "id": content_id(
                    "", fallback=f"practitioner:{who}/{name}:{canon_url(url)}"
                ),
                "source": "usecases",
                "kind": "practitioner",
                "title": f"{name} — {e.get('category') or 'uncategorised'}",
                "url": url,
                "published_at": f"{added}T00:00:00Z",
                "summary": (
                    f"{who or 'unknown'} published a {e.get('category') or 'uncategorised'} Bot"
                    + (f" using {', '.join(integrations[:6])}" if integrations else "")
                    + (
                        " (prompt carries approval language)"
                        if e.get("has_approval_language")
                        else ""
                    )
                ),
                "signals": {
                    "role": "entry",
                    "contributor": who,
                    "category": e.get("category"),
                    "integrations": integrations,
                    "has_approval_language": bool(e.get("has_approval_language")),
                },
            }
        )
    for who, count in by_contributor.most_common(TOP_CONTRIBUTORS):
        rows.append(
            {
                "id": content_id(f"https://x.com/{who}"),
                "source": "usecases",
                "kind": "practitioner",
                "title": f"{who} — {count} Bot definition(s) in the corpus",
                "url": f"https://x.com/{who}",
                "published_at": (
                    f"{latest[who]}T00:00:00Z" if latest.get(who) else None
                ),
                "summary": (
                    f"Top contributor: {count} Bot definition(s), most recent "
                    f"{latest.get(who) or 'unknown'}. Curated corpus only — x.com is never scraped."
                ),
                "signals": {"role": "contributor", "contributor": who, "bots": count},
            }
        )
    return rows


def merge(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedup by content id. A story in two feeds keeps one row and both feed labels."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        prior = out.get(row["id"])
        if prior is None:
            out[row["id"]] = row
            continue
        feeds = prior.get("signals", {}).get("feeds")
        extra = row.get("signals", {}).get("feeds") or []
        if isinstance(feeds, list):
            for label in extra:
                if label not in feeds:
                    feeds.append(label)
    return sorted(out.values(), key=lambda r: (r["published_at"] or "", r["title"]))


def build_document(
    *,
    feeds: tuple[Feed, ...],
    fetcher: Fetcher,
    corpus: Optional[dict[str, Any]],
    corpus_meta: dict[str, Any],
    now: Optional[dt.datetime] = None,
    days: int = PRACTITIONER_WINDOW_DAYS,
) -> dict[str, Any]:
    """The whole artifact, from injectable parts — which is what makes `--selftest` offline."""
    when = now or dt.datetime.now(dt.timezone.utc)
    sources: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    # The region this run spends: one budget unit per feed. Built from the same timeout the
    # production fetcher enforces, so a healthy run never trips it — every checkpoint below
    # sees a positive budget and a live deadline and is a no-op. A cancelled run unwinds to
    # gbmain, which exits 130/143 with no artifact written, exactly like a SIGINT today.
    budget = len(feeds)
    cx = Cx(deadline=time.monotonic() + budget * FEED_WORST_S, budget=budget)
    for feed in feeds:
        # The spine probe: cancel/deadline unwind here; the budget leg never fires past the
        # one-fetch-per-feed loop, by construction.
        cx.checkpoint(cx.deadline, budget)
        budget -= 1
        source, got = feed_rows(feed, fetcher)
        source["why"] = feed.why
        sources.append(source)
        rows.extend(got)
    pr: list[dict[str, Any]] = []
    if corpus is not None:
        pr = practitioner_rows(corpus, now=when, days=days)
    corpus_meta = dict(corpus_meta)
    corpus_meta["rows"] = len(pr)
    sources.append(corpus_meta)
    rows.extend(pr)
    merged = merge(rows)
    kinds = collections.Counter(r["kind"] for r in merged)
    alive = [s for s in sources if s.get("status") == 200 and not s.get("error")]
    return {
        "schema": SCHEMA,
        "captured_at": now_z(when),
        "window_days": days,
        # Declared for the downstream content-addressed store, mirroring `gb-github`'s key.
        # EMPTY here by construction: no gb-feeds row carries a clock-derived field, so a row body
        # may be hashed whole. Every volatile fact (fetched_at, attempts) lives in `sources[]`.
        "id_algorithm": "blake2b-16(canonical_url.lower()) → 32 hex",
        "volatile_fields": [],
        "sources": sources,
        "counts": {
            "sources": len(sources),
            "alive": len(alive),
            "dead": len(sources) - len(alive),
            "rows": len(merged),
            "by_kind": dict(sorted(kinds.items())),
        },
        # Candidates measured dead, carried so nobody re-proposes them and `probe --retired` can
        # revive one on evidence. Not fetched by `collect`: a daily request to a known 404 buys
        # nothing, and its status is recorded here with the date it was measured.
        "retired_sources": [r._asdict() for r in RETIRED],
        "rows": merged,
    }


def writer_functions(src: str) -> set[str]:
    """Names of the functions in `src` that can write a file. A STRUCTURAL property, over the AST.

    Adopted from `gb-github`'s write-free-by-construction argument, which is stronger than the
    convention it replaces: `build_document` takes no root and calls no writer, so a store can
    import this module and build an artifact in memory with no flag to get wrong. No behavioural
    test can prove the ABSENCE of a write path that a future edit adds — only a check over the
    source can, so that check lives in `--selftest` (legs `one-writer-only`,
    `build-document-takes-no-root`, `writer-detector-fires-on-known-bad`).
    """
    import ast as _ast

    out: set[str] = set()
    for node in _ast.walk(_ast.parse(src)):
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        for inner in _ast.walk(node):
            if not isinstance(inner, _ast.Call):
                continue
            f = inner.func
            if isinstance(f, _ast.Attribute):
                name = f.attr
                base = getattr(f.value, "id", "")
            else:
                name = getattr(f, "id", "")
                base = ""
            args = inner.args
            if name in (
                "atomic_write_text",
                "atomic_write_bytes",
                "write_text",
                "write_bytes",
            ):
                out.add(node.name)
            elif name == "dump" and base == "json":
                out.add(node.name)
            elif name == "open" and any(
                isinstance(a, _ast.Constant)
                and isinstance(a.value, str)
                and "w" in a.value
                for a in args[1:]
            ):
                out.add(node.name)
    return out


def render(doc: dict[str, Any]) -> str:
    lines = [
        f"feeds {doc['captured_at']}: {doc['counts']['rows']} rows "
        f"({', '.join(f'{v} {k}' for k, v in doc['counts']['by_kind'].items()) or 'none'}) "
        f"from {doc['counts']['alive']}/{doc['counts']['sources']} live sources"
    ]
    for s in doc["sources"]:
        status = s.get("status")
        flag = "ok " if status == 200 and not s.get("error") else "DEAD"
        lines.append(
            f"  {flag} {str(status or '---'):>4}  {s.get('items', 0):>3} items → "
            f"{s.get('rows', 0):>3} rows  {s.get('label', '?'):<18} {s['url']}"
            + (f"  [{s['error']}]" if s.get("error") else "")
            + (f"  ({s['attempts']} attempts)" if (s.get("attempts") or 1) > 1 else "")
        )
    for r in doc.get("retired_sources") or []:
        lines.append(
            f"  ret {r['status']:>4}    not polled  (measured {r['measured_at']})  {r['url']}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------------------- selftest

RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Fixture Feed</title>
  <item>
    <title>Grok Bot routines now fire unattended</title>
    <link>https://example.test/grok-routines?utm_source=rss&amp;utm_medium=feed</link>
    <guid>tag:example.test,2026:grok-routines</guid>
    <pubDate>Tue, 09 Sep 2026 14:00:00 +0000</pubDate>
    <description>&lt;p&gt;A &lt;b&gt;routine&lt;/b&gt; finally ran.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Sourdough starter tips for autumn</title>
    <link>https://example.test/sourdough/</link>
    <pubDate>Mon, 08 Sep 2026 09:30:00 +0000</pubDate>
    <description>No agents were involved.</description>
  </item>
  <item>
    <title>Cursor changelog: MCP allowlist modes</title>
    <link>https://example.test/cursor-mcp</link>
    <pubDate>Wed, 10 Sep 2026 01:02:03 +0000</pubDate>
    <description>Tool allowlists.</description>
  </item>
</channel></rss>
"""

ATOM_FIXTURE = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Fixture Atom</title>
  <entry>
    <title>An agent shipped something</title>
    <link rel="alternate" href="https://atom.test/agent-shipped"/>
    <id>https://atom.test/agent-shipped</id>
    <published>2026-09-10T08:15:00-07:00</published>
    <summary type="html">&lt;p&gt;Atom summary.&lt;/p&gt;</summary>
  </entry>
  <entry>
    <title>Unrelated woodworking log</title>
    <link rel="alternate" href="https://atom.test/wood"/>
    <id>https://atom.test/wood</id>
    <updated>2026-09-07T00:00:00Z</updated>
    <summary>Planes and chisels.</summary>
  </entry>
</feed>
"""

HTML_FIXTURE = b"<!DOCTYPE html><html><head><title>Cursor</title></head><body>app shell</body></html>"

# The SAME first story, re-served by a second feed with tracking parameters and a trailing slash.
# If the id is right, this dedups to one row; if it is wrong, the daily diff doubles every entry.
RSS_DUPLICATE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Grok Bot routines now fire unattended (mirror)</title>
    <link>https://EXAMPLE.test/grok-routines/?ref=newsletter</link>
    <pubDate>Tue, 09 Sep 2026 14:00:00 +0000</pubDate>
  </item>
</channel></rss>
"""

CORPUS_FIXTURE = {
    "rows": [
        {
            "name": "Recent Bot",
            "category": "Productivity",
            "integrations": ["Gmail"],
            "contributor": "alice",
            "source": "https://x.com/alice/status/1",
            "added_at": "2026-09-09",
            "has_approval_language": True,
        },
        {
            "name": "Also Recent",
            "category": "Personal",
            "integrations": [],
            "contributor": "alice",
            "source": "https://x.com/alice/status/2",
            "added_at": "2026-09-05",
        },
        {
            "name": "Ancient Bot",
            "category": "Personal",
            "integrations": ["Slack"],
            "contributor": "bob",
            "source": "https://x.com/bob/status/3",
            "added_at": "2026-01-01",
        },
        # TWO Bots announced in ONE tweet — the live shape that collapsed 63 rows when an entry
        # was addressed by its url. Both MUST survive as separate rows.
        {
            "name": "Twin A",
            "category": "Productivity",
            "integrations": ["Notion"],
            "contributor": "carol",
            "source": "https://x.com/carol/status/9",
            "added_at": "2026-09-10",
        },
        {
            "name": "Twin B",
            "category": "Productivity",
            "integrations": ["Linear"],
            "contributor": "carol",
            "source": "https://x.com/carol/status/9",
            "added_at": "2026-09-10",
        },
        # No permalink at all — 35 live entries look like this.
        {
            "name": "Sourceless Bot",
            "category": "Personal",
            "integrations": [],
            "contributor": "dave",
            "source": "",
            "added_at": "2026-09-08",
        },
    ]
}

SELFTEST_FEEDS: tuple[Feed, ...] = (
    Feed("https://rss.test/feed.xml", "rss-fixture", True, "RSS leg"),
    Feed("https://atom.test/atom.xml", "atom-fixture", True, "Atom leg"),
    Feed("https://dead.test/gone.xml", "dead-fixture", True, "404 leg"),
    Feed("https://soft404.test/rss.xml", "soft404-fixture", True, "200-but-html leg"),
    Feed("https://mirror.test/feed.xml", "mirror-fixture", True, "dedup leg"),
)

_FIXTURE_BODIES: dict[str, tuple[int, str, Optional[bytes]]] = {
    "https://rss.test/feed.xml": (200, "application/rss+xml", RSS_FIXTURE),
    "https://atom.test/atom.xml": (200, "application/atom+xml", ATOM_FIXTURE),
    "https://dead.test/gone.xml": (404, "text/html", None),
    "https://soft404.test/rss.xml": (200, "text/html; charset=utf-8", HTML_FIXTURE),
    "https://mirror.test/feed.xml": (200, "application/rss+xml", RSS_DUPLICATE),
}


def fixture_fetcher(url: str) -> tuple[dict[str, Any], Optional[bytes]]:
    status, ctype, body = _FIXTURE_BODIES[url]
    meta = {
        "url": url,
        "status": status,
        "bytes": len(body or b""),
        "content_type": ctype,
        "fetched_at": "2026-09-11T12:00:00Z",
        "attempts": 1,
        "error": None if body is not None else f"http {status} Not Found",
    }
    return meta, body


def selftest() -> int:
    """Offline proofs. Each leg names the failure it prevents; a positive control is mandatory."""
    legs: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    rss_items, rss_err = parse_feed(RSS_FIXTURE)
    atom_items, atom_err = parse_feed(ATOM_FIXTURE)
    check(
        "rss-parses",
        rss_err is None and len(rss_items) == 3,
        f"{len(rss_items)} items, {rss_err}",
    )
    check(
        "atom-parses",
        atom_err is None
        and len(atom_items) == 2
        and atom_items[0]["url"] == "https://atom.test/agent-shipped",
        f"{len(atom_items)} entries, {atom_err}",
    )
    check(
        "rfc822-normalises",
        bool(rss_items and rss_items[0]["published_at"] == "2026-09-09T14:00:00Z"),
        str(rss_items[0]["published_at"]) if rss_items else "no items",
    )
    check(
        "iso-offset-normalises",
        bool(atom_items and atom_items[0]["published_at"] == "2026-09-10T15:15:00Z"),
        str(atom_items[0]["published_at"]) if atom_items else "no entries",
    )
    check(
        "summary-html-stripped",
        bool(rss_items and rss_items[0]["summary"] == "A routine finally ran."),
        repr(rss_items[0]["summary"]) if rss_items else "no items",
    )
    check(
        "unparseable-date-is-none",
        norm_date("next Tuesday-ish") is None and norm_date(None) is None,
        "a guessed date would silently fake recency",
    )

    doc_a = build_document(
        feeds=SELFTEST_FEEDS,
        fetcher=fixture_fetcher,
        corpus=CORPUS_FIXTURE,
        corpus_meta={
            "url": "usecases/2026-09-11T0620.json",
            "kind": "local-corpus",
            "label": "usecases",
            "status": 200,
            "bytes": 123,
            "fetched_at": "2026-09-11T12:00:00Z",
            "attempts": 1,
            "error": None,
            "items": 3,
        },
        now=dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.timezone.utc),
        days=14,
    )
    doc_b = build_document(
        feeds=SELFTEST_FEEDS,
        fetcher=fixture_fetcher,
        corpus=CORPUS_FIXTURE,
        corpus_meta=dict(doc_a["sources"][-1]),
        now=dt.datetime(2026, 9, 12, 6, 30, tzinfo=dt.timezone.utc),
        days=14,
    )
    ids_a = [r["id"] for r in doc_a["rows"]]
    ids_b = [r["id"] for r in doc_b["rows"]]
    check(
        "ids-stable-across-runs",
        ids_a == ids_b and len(ids_a) == len(set(ids_a)),
        f"{len(ids_a)} ids, {len(set(ids_a))} distinct",
    )

    # POSITIVE CONTROL. This item MUST be in the output; if the filter ever stops matching it, an
    # empty result is a broken pattern, not a quiet news day. Without this leg the whole filter
    # could match nothing forever and every run would look successful.
    titles = [r["title"] for r in doc_a["rows"]]
    positive = "Grok Bot routines now fire unattended"
    check(
        "filter-positive-control",
        positive in titles,
        f"MUST-match fixture item absent from {len(titles)} rows"
        if positive not in titles
        else "",
    )
    check(
        "filter-rejects-offtopic",
        not any("Sourdough" in t or "woodworking" in t for t in titles),
        "off-topic fixture leaked through the filter",
    )
    # The negative control is only worth having if it could actually fail. The sourdough item's
    # BODY says "No agents were involved", which matches TOPIC_RE — so this leg proves the
    # rejection above came from title-scoped matching, not from a fixture that could never match.
    check(
        "negative-control-is-load-bearing",
        bool(topic_hits("No agents were involved."))
        and not topic_hits("Sourdough starter tips for autumn"),
        "the off-topic fixture must match on body text and not on title",
    )
    check(
        "filter-keeps-second-positive",
        any("MCP allowlist modes" in t for t in titles)
        and any("An agent shipped something" in t for t in titles),
        "a second on-topic item (RSS and Atom) must also survive",
    )

    by_url = {s["url"]: s for s in doc_a["sources"]}
    dead = by_url["https://dead.test/gone.xml"]
    soft = by_url["https://soft404.test/rss.xml"]
    check(
        "dead-feed-recorded",
        dead["status"] == 404 and dead["rows"] == 0 and bool(dead["error"]),
        f"status={dead['status']} rows={dead['rows']} error={dead['error']}",
    )
    check(
        "soft-404-recorded",
        soft["status"] == 200
        and soft["rows"] == 0
        and "not-a-feed" in (soft.get("error") or ""),
        f"status={soft['status']} error={soft.get('error')}",
    )
    check(
        "every-source-carries-a-status-key",
        all("status" in s and "url" in s and "rows" in s for s in doc_a["sources"]),
        "a source with no status field is an unrecorded fetch",
    )
    live_urls = {f.url for f in FEEDS}
    check(
        "retired-candidates-are-not-polled",
        not (live_urls & {r.url for r in RETIRED}) and len(RETIRED) >= 5,
        f"overlap: {sorted(live_urls & {r.url for r in RETIRED})}",
    )
    check(
        "retired-candidates-carry-a-measured-status",
        all(
            isinstance(r["status"], int)
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["measured_at"])
            and r["why"]
            for r in doc_a["retired_sources"]
        ),
        "a pruned candidate without a status and a date is folklore, not evidence",
    )

    mirror_row = [
        r
        for r in doc_a["rows"]
        if r["url"].endswith("grok-routines") or "grok-routines" in r["url"]
    ]
    check(
        "duplicate-story-dedups",
        len(mirror_row) == 1
        and sorted(mirror_row[0]["signals"]["feeds"])
        == ["mirror-fixture", "rss-fixture"],
        f"{len(mirror_row)} rows for one story: "
        + str([r["signals"].get("feeds") for r in mirror_row]),
    )

    pr = [r for r in doc_a["rows"] if r["kind"] == "practitioner"]
    entry_titles = [r["title"] for r in pr if r["signals"]["role"] == "entry"]
    roll = {
        r["signals"]["contributor"]: r
        for r in pr
        if r["signals"]["role"] == "contributor"
    }
    check(
        "practitioner-window-includes-recent",
        any("Recent Bot" in t for t in entry_titles)
        and any("Also Recent" in t for t in entry_titles),
        str(entry_titles),
    )
    check(
        "practitioner-window-excludes-old",
        not any("Ancient Bot" in t for t in entry_titles),
        "a 2026-01-01 entry is outside a 14-day window",
    )
    check(
        "practitioner-rollup-counts",
        roll.get("alice", {}).get("signals", {}).get("bots") == 2
        and roll.get("bob", {}).get("signals", {}).get("bots") == 1,
        str({k: v["signals"]["bots"] for k, v in roll.items()}),
    )
    # The regression this fix exists for: two Bots in one tweet must be two rows. Url-addressing
    # merged them, and on the live corpus that silently lost 63 of 327 in-window Bot definitions.
    twins = [t for t in entry_titles if t.startswith("Twin ")]
    check(
        "two-bots-in-one-tweet-stay-two-rows",
        sorted(twins) == ["Twin A — Productivity", "Twin B — Productivity"],
        f"{len(twins)} row(s) for two Bots sharing one permalink: {sorted(twins)}",
    )
    check(
        "sourceless-entry-survives",
        any("Sourceless Bot" in t for t in entry_titles),
        "an entry with no permalink must still be addressable, not dropped",
    )
    check(
        "practitioner-does-not-fetch-x",
        all(
            s.get("kind") != "feed" or "x.com" not in s["url"] for s in doc_a["sources"]
        ),
        "x.com is 403/ToS-hostile; the corpus is the only sanctioned window",
    )

    volatile = doc_a["captured_at"]
    check(
        "rows-carry-no-fetch-timestamp",
        all(
            "fetched_at" not in r
            and "captured_at" not in r
            and volatile not in json.dumps(r)
            for r in doc_a["rows"]
        ),
        "a fetch timestamp inside a row would defeat dedup and make every item look new",
    )
    check(
        "volatile-fields-declared-and-empty",
        doc_a["volatile_fields"] == []
        and all(volatile not in json.dumps(r) for r in doc_a["rows"]),
        "volatile_fields must stay [] only while no row carries a clock-derived field",
    )
    # Pins the CROSS-PRODUCER key. gb-github, gb-vendor-docs and gb-feeds must all hash the same
    # url to the same id, or the downstream store dedups nothing and the daily diff repeats itself
    # forever. Restating the algorithm inline is the point: a drift in either side breaks this leg.
    expected = hashlib.blake2b(
        b"https://example.test/grok-routines", digest_size=16
    ).hexdigest()
    check(
        "id-convention-blake2b-16-of-canonical-url",
        content_id("https://EXAMPLE.test/Grok-Routines/?utm_source=rss#frag")
        == expected
        and len(expected) == 32,
        f"{content_id('https://EXAMPLE.test/Grok-Routines/?utm_source=rss#frag')} != {expected}",
    )
    check(
        "id-falls-back-without-a-url",
        content_id("", fallback="Only A Guid")
        == content_id("", fallback="only a guid")
        != content_id("", fallback="something else"),
        "a guid-only item must still be stable, and must not collide with another",
    )
    # LIVE vectors handed over by the gb-github miner on 2026-09-11, each already present in
    # `github/2026-09-11T1654.json`. The variants are the shapes only a raw feed link produces —
    # http, www., mixed case, trailing slash — and each MUST fold onto the same id, or a repo
    # mentioned in an RSS item lands in the store a second time under a different key.
    vectors = {
        "https://github.com/awslabs/mcp": "9b1702d7481f7878b260bd8166feb038",
        "https://github.com/modelcontextprotocol/servers": "048a31cad66dfa5d71865f36b8890512",
    }
    variants = {
        "https://github.com/awslabs/mcp": "http://www.GitHub.com/AWSLabs/MCP/?utm_source=rss",
        "https://github.com/modelcontextprotocol/servers": (
            "http://www.github.com/ModelContextProtocol/Servers#readme"
        ),
    }
    vector_fails = [
        f"{u}: canonical={content_id(u)} variant={content_id(variants[u])} expected={want}"
        for u, want in vectors.items()
        if not (content_id(u) == want == content_id(variants[u]))
    ]
    check(
        "id-matches-gb-github-live-vectors",
        not vector_fails,
        "; ".join(vector_fails),
    )
    check(
        "deep-links-stay-distinct",
        len(
            {
                content_id("https://github.com/awslabs/mcp"),
                content_id("https://github.com/awslabs/mcp/issues/4"),
                content_id("https://github.com/awslabs/mcp/tree/main/src"),
            }
        )
        == 3,
        "a repo, an issue and a subtree are three resources, not one",
    )
    check(
        "document-contract",
        doc_a["schema"] == SCHEMA
        and bool(
            re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", doc_a["captured_at"])
        )
        and isinstance(doc_a["sources"], list)
        and isinstance(doc_a["rows"], list)
        and all(
            {
                "id",
                "source",
                "kind",
                "title",
                "url",
                "published_at",
                "summary",
                "signals",
            }
            <= set(r)
            for r in doc_a["rows"]
        ),
        "schema/captured_at/sources/rows plus the per-row contract",
    )
    check(
        "json-serialisable",
        isinstance(json.dumps(doc_a, indent=1), str),
        "the artifact must round-trip through json",
    )
    check(
        "absent-corpus-is-an-error-not-an-empty-list",
        (lambda m: m["error"] and m["status"] is None)(
            load_corpus(pathlib.Path("/nonexistent-grokbot-root"))[1]
        ),
        "a missing corpus must be recorded as absent, never as zero practitioners",
    )

    # WRITE-FREE BY CONSTRUCTION. Proven over this file's own source, plus a known-bad snippet in
    # the same leg set so the detector itself is shown to fire. Order matters: a detector that
    # cannot fail would make `one-writer-only` a decoration.
    own_src = pathlib.Path(__file__).read_text(errors="replace")
    writers = writer_functions(own_src)
    check(
        "one-writer-only",
        writers == {"main"},
        f"functions able to write: {sorted(writers)} — only main() may touch the disk",
    )
    check(
        "build-document-takes-no-root",
        "root" not in inspect.signature(build_document).parameters
        and "root" not in inspect.signature(feed_rows).parameters,
        "a builder that knows an artifact root will eventually write to it",
    )
    bad_atomic = (
        "def build_document(*, feeds, fetcher):\n"
        "    atomic_write_text(pathlib.Path('x.json'), '{}')\n"
        "    return {}\n"
    )
    bad_dump = (
        "def feed_rows(feed, fetcher):\n"
        "    with open('x.json', 'w') as fh:\n"
        "        json.dump({}, fh)\n"
        "    return {}, []\n"
    )
    check(
        "writer-detector-fires-on-known-bad",
        writer_functions(bad_atomic) == {"build_document"}
        and writer_functions(bad_dump) == {"feed_rows"},
        f"{writer_functions(bad_atomic)} / {writer_functions(bad_dump)}",
    )

    # A READER WHO WALKED AWAY IS NOT A FAILURE. `probe | head -0` exited 1 with a traceback
    # before `emit` existed, and on `collect` that happens after the artifact is already on disk —
    # a successful measurement reporting failure to the scheduler. Both legs are offline: a fake
    # stdout raises EPIPE on write, and it has no fileno(), so `_silence_stdout` leaves the real
    # fd 1 alone and the rest of this selftest still prints.
    class _DeadPipe:
        def write(self, _: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

    real_stdout, real_stderr = sys.stdout, sys.stderr
    swallowed_out = swallowed_err = True
    try:
        sys.stdout = _DeadPipe()
        emit("this write cannot land")
    except BrokenPipeError:
        swallowed_out = False
    finally:
        sys.stdout = real_stdout
    try:
        sys.stderr = _DeadPipe()
        warn("this verdict must survive")
    except BrokenPipeError:
        swallowed_err = False
    finally:
        sys.stderr = real_stderr
    check(
        "emit-swallows-broken-pipe",
        swallowed_out,
        "a piped-into-head run must not raise; the artifact is already written",
    )
    check(
        "warn-swallows-broken-pipe",
        swallowed_err,
        "an EPIPE on STDERR unwinds past the pre-computed verdict and the spine then exits 0 — "
        "measured: a FAILING selftest reported rc 0 under `2>&1 | head -0`",
    )

    # ARGPARSE IS A WRITER WE DO NOT OWN. These legs call `main` directly with a dead stream, so
    # they exercise the real parse_args guard: a usage error must still be 2 and a help request
    # must still be 0, even though the message itself can never land. (Measured before the guard:
    # `--bogus 2>&1 | true` -> rc 0, i.e. a typo reported success to any piped caller.)
    # BOTH streams are swapped for fileno-less fakes, not just the one under test: the guard
    # calls `_silence` on stdout AND stderr, and `_silence` on a REAL stream dup2s /dev/null over
    # its fd — which, the first time this leg was written, silenced this selftest's own PASS line
    # for the rest of the process. A fake with no fileno() makes `_silence` a no-op.
    def _rc_with_dead(argv: list[str]) -> object:
        real_out, real_err = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = _DeadPipe(), _DeadPipe()
            return main(argv)
        except (
            BaseException
        ) as exc:  # SystemExit too: it would mean the guard is absent
            return type(exc).__name__
        finally:
            sys.stdout, sys.stderr = real_out, real_err

    bogus_rc = _rc_with_dead(["--bogus"])
    help_rc = _rc_with_dead(["--help"])
    check(
        "usage-error-survives-a-dead-stderr",
        bogus_rc == 2,
        f"expected 2 (usage error), got {bogus_rc!r} — argparse's own write must not become "
        f"a success verdict",
    )
    check(
        "help-request-survives-a-dead-stdout",
        help_rc == 0,
        f"expected 0 (help is a success), got {help_rc!r}",
    )
    import ast as _ast

    own_tree = _ast.parse(own_src)
    guarded_spans = [
        (n.lineno, n.end_lineno or n.lineno)
        for n in _ast.walk(own_tree)
        if isinstance(n, _ast.FunctionDef) and n.name in ("emit", "warn")
    ]
    unguarded_prints = [
        n.lineno
        for n in _ast.walk(own_tree)
        if isinstance(n, _ast.Call)
        and getattr(n.func, "id", "") == "print"
        and not any(lo <= n.lineno <= hi for lo, hi in guarded_spans)
    ]
    check(
        "no-unguarded-print-on-either-stream",
        not unguarded_prints and len(guarded_spans) == 2,
        f"unguarded print() at line(s) {unguarded_prints} — stdout goes through emit(), stderr "
        f"through warn(); both survive a closed pipe without changing the exit code",
    )
    # The entry point must stay on the house spine. Measured 2026-09-11 on this file: SIGTERM
    # mid-run under `gbmain` exits 143 with "cancelled by signal 15 — no partial artifact
    # written" and leaves no artifact and no .tmp behind; a bare `sys.exit(main())` gives a
    # traceback and exit 1, which a scheduled tick records as a real failure.
    entry_calls = {
        getattr(n.func, "id", "")
        for n in _ast.walk(own_tree)
        if isinstance(n, _ast.Call)
    }
    check(
        "entry-point-uses-the-cancel-spine",
        "gbmain" in entry_calls and "gbmain" in own_src.split("__main__")[-1],
        "bin/gb-feeds.py must end through gbtypes.main (imported as gbmain), not sys.exit(main())",
    )

    # The spine's cancel arm reports on stderr UNGUARDED, so on a dead pipe its EPIPE escapes and
    # `sys.exit(130/143)` never runs. Measured through the real `__main__` on 2026-09-11: with the
    # wrapper, SIGTERM/SIGINT under a closed pipe give 143/130 in both the corpus-present and
    # corpus-absent cases; with the entry block reduced to a bare `gbmain(main)`, both give 1.
    #
    # This was FIRST written as `all(t in entry_src for t in (...))` and that leg was worthless:
    # deleting the guard while keeping the explanatory comment above it left the selftest GREEN,
    # because the comment contains every token the check looked for. It is now a structural
    # predicate, and it is fired every run against synthetic probes so an INVERTED predicate
    # cannot pass by agreeing with the real source.
    def cancel_guard_present(tree: object) -> bool:
        """True when `__main__` calls gbmain inside a try whose BrokenPipeError handler re-raises
        SystemExit(<exc>.exit_code). Comments and docstrings cannot satisfy it."""
        for node in _ast.walk(tree):  # type: ignore[arg-type]
            if not isinstance(node, _ast.If):
                continue
            block = _ast.dump(node.test)
            if "__name__" not in block or "__main__" not in block:
                continue
            for tried in _ast.walk(node):
                if not isinstance(tried, _ast.Try):
                    continue
                calls_spine = any(
                    getattr(c.func, "id", "") == "gbmain"
                    for c in _ast.walk(tried)
                    if isinstance(c, _ast.Call)
                )
                for handler in tried.handlers:
                    names = _ast.dump(handler.type) if handler.type else ""
                    if "BrokenPipeError" not in names:
                        continue
                    # The recovery must go through `recover_cancellation` and must have a bare
                    # `raise` for the case it declines. A handler that raises SystemExit with a
                    # literal, or with no way to decline, is inventing a verdict.
                    calls_recovery = any(
                        getattr(c.func, "id", "") == "recover_cancellation"
                        for c in _ast.walk(handler)
                        if isinstance(c, _ast.Call)
                    )
                    exits_with_it = any(
                        isinstance(r, _ast.Raise)
                        and isinstance(r.exc, _ast.Call)
                        and getattr(r.exc.func, "id", "") == "SystemExit"
                        and r.exc.args
                        and not isinstance(r.exc.args[0], _ast.Constant)
                        for r in _ast.walk(handler)
                    )
                    declines = any(
                        isinstance(r, _ast.Raise) and r.exc is None
                        for r in _ast.walk(handler)
                    )
                    if calls_spine and calls_recovery and exits_with_it and declines:
                        return True
        return False

    probe_good = (
        'if __name__ == "__main__":\n'
        "    try:\n"
        "        gbmain(main)\n"
        "    except BrokenPipeError as e:\n"
        "        code = recover_cancellation(e)\n"
        "        if code is None:\n"
        "            raise\n"
        "        raise SystemExit(code)\n"
    )
    # Every token the old substring check looked for, in a COMMENT, with no guard at all.
    probe_comment_only = (
        'if __name__ == "__main__":\n'
        "    # BrokenPipeError / Cancelled / exit_code / recover_cancellation, only mentioned\n"
        "    gbmain(main)\n"
    )
    probe_wrong_exception = probe_good.replace("BrokenPipeError", "ValueError")
    probe_invents_a_code = (
        'if __name__ == "__main__":\n'
        "    try:\n"
        "        gbmain(main)\n"
        "    except BrokenPipeError:\n"
        "        raise SystemExit(130)\n"
    )
    probe_cannot_decline = (
        'if __name__ == "__main__":\n'
        "    try:\n"
        "        gbmain(main)\n"
        "    except BrokenPipeError as e:\n"
        "        raise SystemExit(recover_cancellation(e) or 130)\n"
    )
    probes = {
        "good": (probe_good, True),
        "comment-only": (probe_comment_only, False),
        "wrong-exception": (probe_wrong_exception, False),
        "invents-a-code": (probe_invents_a_code, False),
        "cannot-decline": (probe_cannot_decline, False),
    }
    probe_fails = [
        name
        for name, (text, want) in probes.items()
        if cancel_guard_present(_ast.parse(text)) is not want
    ]
    check(
        "cancel-guard-detector-fires-on-probes",
        not probe_fails,
        f"detector disagrees with its own probes: {probe_fails} — an inverted predicate would "
        f"otherwise pass by agreeing with the real source",
    )
    check(
        "entry-preserves-cancel-code-on-a-dead-pipe",
        cancel_guard_present(own_tree),
        "the entry block must recover the signal from the EPIPE's __context__; without it a "
        "cancelled tick is indistinguishable from a real failure whenever output is piped",
    )

    # EXECUTE the recovery, all four branches, with no signal involved. The two NEGATIVES are the
    # point: a recovery that defaulted to 130 would manufacture a cancellation that never
    # happened, and both positive legs would still pass.
    def _epipe_with(ctx: Optional[BaseException]) -> BrokenPipeError:
        exc = BrokenPipeError(32, "Broken pipe")
        exc.__context__ = ctx
        return exc

    recovery = {
        "sigint": recover_cancellation(_epipe_with(Cancelled(2))),
        "sigterm": recover_cancellation(_epipe_with(Cancelled(15))),
        "bare-epipe": recover_cancellation(_epipe_with(None)),
        "other-context": recover_cancellation(_epipe_with(ValueError("unrelated"))),
    }
    check(
        "recovery-maps-cancelled-to-its-exit-code",
        recovery["sigint"] == 130 and recovery["sigterm"] == 143,
        f"expected 130/143, got {recovery['sigint']}/{recovery['sigterm']}",
    )
    check(
        "recovery-declines-a-non-cancel-epipe",
        recovery["bare-epipe"] is None and recovery["other-context"] is None,
        f"expected None/None, got {recovery['bare-epipe']}/{recovery['other-context']} — a "
        f"default here would report a cancellation that never happened",
    )
    # CANCELLATION MUST NOT BE SWALLOWED. `Cancelled` derives from Exception, so any handler
    # broad enough to catch it turns a SIGTERM into "source unreachable" and lets a cancelled
    # producer carry on and report success. Every handler here is narrow today; narrow-today is
    # only a property if something checks it, so these legs exercise the REAL handler in `fetch`
    # at its own seam (urlopen), with a positive control proving the narrowing did not simply
    # break error recording.
    real_urlopen = urllib.request.urlopen
    propagated = False
    recorded: dict[str, Any] = {}
    try:

        def _cancel(*_a: object, **_k: object) -> None:
            raise Cancelled(15)

        def _boom(*_a: object, **_k: object) -> None:
            raise OSError("connection reset by fixture")

        urllib.request.urlopen = _cancel
        try:
            fetch("https://cancel.test/feed.xml", timeout=1, retries=0)
        except Cancelled:
            propagated = True
        urllib.request.urlopen = _boom
        recorded, body_none = fetch("https://boom.test/feed.xml", timeout=1, retries=0)
    finally:
        urllib.request.urlopen = real_urlopen
    check(
        "fetch-propagates-cancellation",
        propagated,
        "a Cancelled raised inside urlopen must unwind, not become a recorded source error",
    )
    check(
        "fetch-still-records-ordinary-oserror",
        (recorded.get("error") or "").startswith("OSError")
        and recorded.get("status") is None
        and body_none is None,
        f"positive control: an ordinary OSError must still be recorded, got {recorded!r}",
    )

    def _cancelling_fetcher(url: str) -> tuple[dict[str, Any], Optional[bytes]]:
        raise Cancelled(2)

    builder_propagated = False
    try:
        build_document(
            feeds=SELFTEST_FEEDS,
            fetcher=_cancelling_fetcher,
            corpus=CORPUS_FIXTURE,
            corpus_meta={"url": "x", "status": 200, "rows": 0},
            now=dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc),
        )
    except Cancelled:
        builder_propagated = True
    check(
        "build-document-propagates-cancellation",
        builder_propagated,
        "feed_rows/build_document must not swallow a cancel into a dead-source row",
    )

    # An expired region deadline must abort the loop BEFORE the first fetch, with the 143
    # path. The region `build_document` builds comes from `time.monotonic`, so this leg pins
    # the clock to a fixed past instant: construction lands the deadline before any real
    # instant, while the checkpoint's own clock still reads the live one. Zero fetcher calls
    # proves the abort happened at the per-item boundary instead of after the spend.
    expired_calls: list[str] = []

    def _expired_fetcher(url: str) -> tuple[dict[str, Any], Optional[bytes]]:
        expired_calls.append(url)
        return fixture_fetcher(url)

    def _pinned_past() -> float:
        return -1e12

    real_monotonic = time.monotonic
    time.monotonic = _pinned_past
    expired_code: Optional[int] = None
    try:
        build_document(
            feeds=SELFTEST_FEEDS,
            fetcher=_expired_fetcher,
            corpus=None,
            corpus_meta={"url": "x", "status": 200, "rows": 0},
            now=dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc),
        )
    except Cancelled as exc:
        expired_code = exc.exit_code
    finally:
        time.monotonic = real_monotonic
    check(
        "expired-deadline-aborts-before-first-fetch",
        expired_code == 143 and not expired_calls,
        f"code={expired_code} calls={len(expired_calls)} — not the SIGTERM path, or spent first",
    )

    passed = sum(1 for _, ok, _ in legs if ok)
    for name, ok, detail in legs:
        if not ok:
            warn(f"FAIL {name}: {detail}")
    if passed != len(legs):
        warn(f"SELFTEST FAIL - {passed}/{len(legs)}")
        return 1
    emit(f"SELFTEST PASS - {passed}/{len(legs)}")
    return 0


# ------------------------------------------------------------------------------------------- main


def main(argv: Optional[list[str]] = None) -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(
        prog="gb-feeds.py",
        description="Daily blogs/RSS/practitioner miner. Writes feeds/<stamp>.json.",
    )
    ap.add_argument(
        "command",
        nargs="?",
        default="collect",
        choices=("collect", "probe"),
        help="collect: fetch, parse, write the artifact. probe: statuses only, write nothing.",
    )
    ap.add_argument(
        "--root", default=str(root), help="artifact root (default: repo root)"
    )
    ap.add_argument(
        "--days",
        type=int,
        default=PRACTITIONER_WINDOW_DAYS,
        help=f"practitioner window in days (default {PRACTITIONER_WINDOW_DAYS})",
    )
    ap.add_argument(
        "--json", action="store_true", help="machine-readable summary on stdout"
    )
    ap.add_argument(
        "--selftest", action="store_true", help="offline proofs; no network"
    )
    ap.add_argument(
        "--retired",
        action="store_true",
        help="probe only: re-measure the retired candidates, to revive one on evidence",
    )
    try:
        args = ap.parse_args(argv)
    except BrokenPipeError:
        # ARGPARSE WRITES ON OUR BEHALF. "I guarded my own prints" is not an audit of what the
        # process writes: argparse formats usage and errors to stderr (or --help to stdout) and
        # then exits. If that write hits a dead pipe its SystemExit never happens, the EPIPE
        # unwinds past this function into `gbtypes.main`, and the spine's BrokenPipeError arm
        # exits 0 — so a TYPO reported SUCCESS. Measured 2026-09-11: `--bogus 2>&1 | true` and
        # `badverb 2>&1 | true` both gave rc 0 while unpiped gave rc 2.
        #
        # The library never gets to raise its SystemExit, so the verdict is recovered from argv:
        # a help request is a success, anything else that made argparse write is a usage error.
        _silence(sys.stdout)
        _silence(sys.stderr)
        requested = sys.argv[1:] if argv is None else list(argv)
        return 0 if any(a in ("-h", "--help") for a in requested) else 2

    if args.selftest:
        return selftest()

    root = pathlib.Path(args.root).expanduser().resolve()
    corpus, corpus_meta = load_corpus(root)
    if corpus is None:
        warn(f"WARN corpus unavailable: {corpus_meta['error']}")
    doc = build_document(
        feeds=FEEDS,
        fetcher=polite_fetch,
        corpus=corpus,
        corpus_meta=corpus_meta,
        days=args.days,
    )

    feeds_alive = sum(
        1
        for s in doc["sources"]
        if s.get("kind") == "feed" and s.get("status") == 200 and not s.get("error")
    )
    if args.command == "probe":
        emit(render(doc))
        if args.retired:
            emit("re-measuring retired candidates (not polled by `collect`):")
            for r in RETIRED:
                meta, body = polite_fetch(r.url)
                items, err = (
                    parse_feed(body) if body is not None else ([], meta["error"])
                )
                verdict = (
                    f"{len(items)} items — REVIVABLE, move it into FEEDS"
                    if (meta["status"] == 200 and not err and items)
                    else f"still dead: {err or meta['error'] or 'no items'}"
                )
                lines = (
                    f"  {str(meta['status'] or '---'):>4}  was {r.status} on "
                    f"{r.measured_at}  {r.url}  → {verdict}"
                )
                emit(lines)
        return 0 if feeds_alive else 2

    out = root / "feeds" / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(doc, indent=1) + "\n")
    if args.json:
        emit(json.dumps({k: v for k, v in doc.items() if k != "rows"}, indent=1))
    else:
        emit(render(doc))
        emit(str(out))
    if not feeds_alive:
        warn(
            "ERROR no allowlisted feed answered 200 — a broken fetcher is not a quiet news day"
        )
        return 2
    if not doc["rows"]:
        warn(
            "ERROR every source was reachable and produced zero rows — verify the filter with "
            "`--selftest` (leg filter-positive-control) before believing it"
        )
        return 2
    return 0


if __name__ == "__main__":
    # `gbtypes.main` is the house entry point (bin/gb, gb-advise, gb-bench, gb-mine, gb-monitor,
    # gb-goldens, gb-export-public all use it). It installs a cancel scope so a SIGTERM from the
    # scheduler exits 130/143 with "no partial artifact written" instead of a traceback and exit
    # 1, which a tick would record as a real failure. `emit`/`warn` still guard EPIPE locally so a
    # closed reader cannot turn this producer's ERROR verdict into a 0 — the spine's own
    # BrokenPipeError arm exits 0 unconditionally, which is right for a pipe that broke during a
    # successful report and wrong for one that broke during an ERROR report.
    #
    # The wrapper below covers a RESIDUAL IN THE SPINE that this file cannot fix from the inside
    # and must not fix by editing `gbtypes.py`. Measured 2026-09-11, isolated probe: with BOTH
    # streams on a genuinely closed pipe, `gbtypes.main` exits **1** on SIGINT and on SIGTERM
    # instead of 130/143, because the `print("cancelled: …", file=sys.stderr)` inside its
    # `except Cancelled` arm raises EPIPE and `except BrokenPipeError` is a SIBLING arm that
    # cannot catch it — so `sys.exit(c.exit_code)` never runs. This producer inherited it: with a
    # corpus present (no earlier `warn` to silence fd 2), SIGTERM under a closed pipe measured
    # rc 1; with the corpus absent, an early `warn` had already pointed fd 2 at /dev/null and it
    # measured 143. Immunity by accident is not immunity.
    #
    # The EPIPE's `__context__` IS the `Cancelled` that was being reported, so the signal is
    # recoverable exactly and never guessed. Anything else re-raises: a BrokenPipeError arriving
    # here from another path would mean an unguarded print exists, which the selftest forbids, and
    # inventing an exit code for it would be the very dishonesty this file is built against.
    try:
        gbmain(main)
    except BrokenPipeError as _epipe:
        _silence(sys.stdout)
        _silence(sys.stderr)
        _code = recover_cancellation(_epipe)
        if _code is None:
            raise
        raise SystemExit(_code)
