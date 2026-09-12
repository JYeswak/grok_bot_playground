#!/usr/bin/env python3
"""gb-links — the link-following leg of the mining ecosystem.

Every other producer answers "what exists?". This one answers "and what is actually AT the urls
those producers found?" — the second half of the operator's ask: pull every url we find, then
follow it.

WHAT IT DOES

  1. HARVEST. Reads the newest artifact under every producer root that exists — `x/`, `feeds/`,
     `github/`, `sources/`, `usecases/` — and pulls every url out of it. A root that is absent,
     unreadable, or mid-write by a sibling is RECORDED as a source with an error and skipped; it
     never aborts the run and it never silently vanishes. `usecases/` contributes twice: its 814
     curated `links[]` and each row's `source`.
  2. CANONICALISE + DEDUP. One url string per document, using the algorithm the other three
     producers agreed on 2026-09-11, so a repo url discovered by `gb-github` and again by an RSS
     mirror and again by a curated link is ONE row with three origins, not three rows.
  3. FETCH, politely and boundedly. >=1s between requests to the same host (tracked per host, not
     globally — a single global sleep both over-waits 40 distinct hosts and under-protects one
     host hit 40 times), a hard per-run budget (`--limit`), a 15s deadline, a 2MB read cap, and a
     recorded redirect chain.
  4. EXTRACT with `html.parser`. `<title>`, `og:title`, `og:description`, `<meta name=description>`,
     `<link rel=canonical>`, the first `<h1>`, and `article:published_time`. Structure comes from
     the parser, never from a regex over markup. A non-HTML body (pdf/json/zip) records its type
     and skips extraction rather than emitting the first 400 bytes of a binary as a "summary".
  5. CLASSIFY into one of eight kinds, recording WHICH RULE FIRED in `signals.classified_by`, so a
     wrong classification is a debuggable one-line fix instead of a mystery.
  6. CACHE at `links/.cache.json`, keyed by canonical url. A url fetched inside `--max-age` is
     served from cache with ZERO network requests, which is what makes the daily tick nearly free:
     the first pass pays for the corpus, every later pass pays only for what is new or stale.

THREE DELIBERATE BOUNDARIES

  * Some hosts are RECORDED, never requested (`NO_FETCH`, top-level `recorded` block). Two
    entries, and they are on the list for DIFFERENT reasons, which the first draft of this file
    got wrong and is worth keeping as a warning. `status.x.ai` is a `refusal`: 115 of 115
    incident pages answered 403, and `gb-feeds` already parses `status.x.ai/feed.xml` into a
    titled row for those exact urls. x.com/twitter.com is `ownership`, NOT a refusal — this
    entry originally cited "NE-1: a plain urllib client gets 403", inherited rather than
    measured, and `probe --refusals` FALSIFIED it on the first run the flag existed: 3 of 3 post
    urls answered 200 in ~36KB with og:title and og:description present. The real and sufficient
    argument is that `gb-x` holds the credentialed API and emits the authoritative row (full
    text, author, timestamp, engagement) for the SAME canonical url — so my row would
    content-address to the same id and the store would flip between a rich row and a poor one
    daily. Every entry therefore carries its `reason`, and `probe --refusals` reads it, because
    a 200 falsifies a refusal and merely confirms an ownership claim.
  * The fetch budget is spent by INFORMATION GAIN, not alphabetically (`FETCH_PRIORITY`). 853 of
    the corpus's fetchable urls are github.com repo landing pages that `gb-github` already
    describes from the API; at one request per second per host, an alphabetical queue spends its
    first fourteen minutes re-learning them. The long tail goes first, github goes last, nothing
    is dropped.
  * A url we have not fetched YET is still a row, marked `signals.fetch == "deferred"`. The
    artifact is the whole corpus of urls we know about, not just the slice this run could afford.
    That is what lets tomorrow's tick pick up exactly where today's budget ran out.

WHY ROWS CARRY NO CLOCK

Every clock-derived fact (`fetched_at`, `fetch` state, cache age) lives under `signals` and is
declared in top-level `volatile_fields`, so the content-addressed store can strip it before
hashing. Getting this wrong is not theoretical: a sibling producer allocated 204 blobs a day to
learn nothing because a timestamp rode along inside the hashed body.

stdlib only (plus repo-local `gbtypes`). Writes atomically. `--selftest` runs offline.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_text, main  # noqa: E402

SCHEMA = "gb-links/1"
CACHE_SCHEMA = "gb-links-cache/1"
UA = "grokbot-links/1 (+local daily link expansion; https://docs.x.ai/grok-bot)"
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
TIMEOUT = 15
PER_HOST_SLEEP = 1.0
# One retry on a transient answer, after this pause. Same value as `gb-feeds` on purpose: two
# producers that back off differently against the same host is two policies to reason about.
RETRY_SLEEP = 2.0
MAX_BYTES = 2_000_000
SUMMARY_CHARS = 400
DEFAULT_LIMIT = 150
DEFAULT_MAX_AGE_DAYS = 7.0
# A failed fetch is retried sooner than a good one is refreshed: a 500 is usually transient, a
# 200 body rarely changes inside a week. Same cache, two shelf lives.
FAIL_MAX_AGE_DAYS = 1.0

# Referral parameters: they identify who sent you, not what the document is. Dropped before
# hashing so the same page arriving from three producers hashes once. Byte-identical to the set
# `gb-feeds` uses — the ids only agree if the canonicalisers do.
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

HTML_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/xml",
    "text/xml",
)


class NoFetch(NamedTuple):
    """One reason a set of hosts is recorded rather than requested, with its evidence attached.

    Every field is here so a future reader can overturn the decision on evidence instead of
    guessing why a host is on a list: what KIND of reason it is, what was measured, when, on what
    sample, and which producer covers the content instead. `probe --refusals` re-measures them,
    and reads `reason` so that a 200 means the right thing for each kind.

    `reason` is load-bearing, not decoration. A "refusal" entry claims the host CANNOT be read,
    and a 200 falsifies it. An "ownership" entry claims someone else's row is BETTER, and a 200
    is expected and changes nothing. Collapsing the two is how a skip list acquires a false
    claim — which is exactly what happened here on the first pass, see the x.com entry.
    """

    hosts: Tuple[str, ...]
    status: str
    reason: str
    owner: str
    measured_at: str
    evidence: str
    why: str


X_HOSTS = (
    "x.com",
    "twitter.com",
    "mobile.twitter.com",
    "t.co",
    "pbs.twimg.com",
    "vxtwitter.com",
    "fxtwitter.com",
)

# Recorded, never requested — for two DIFFERENT reasons that the first draft of this table
# wrongly treated as one.
NO_FETCH: Tuple[NoFetch, ...] = (
    NoFetch(
        X_HOSTS,
        "skipped-owned-by-gb-x",
        "ownership",
        "gb-x",
        "2026-09-11",
        # CORRECTED. This entry originally claimed "NE-1: a plain urllib client gets 403",
        # inherited rather than measured. `probe --refusals` re-measured it on the first run it
        # existed and got 200, so the claim was retired the same day it was written. Measured:
        # 3 of 3 post urls answered 200 in ~36KB with og:title and og:description present, and
        # twitter.com/... 308s to x.com/... and then answers 200.
        "a plain client DOES get 200 here (3 of 3 post urls, og:title + og:description present, "
        "~36KB each) — so this is NOT a reachability refusal and must not be labelled one",
        "gb-x holds the credentialed API and emits the AUTHORITATIVE row for every post url: "
        "full text, author, timestamp and engagement, where a plain fetch yields only a "
        "truncated og:description. The canonical url is the same either way, so my row would "
        "content-address to the SAME id as gb-x's and the store would flip between a rich row "
        "and a poor one every day. 1954 requests to buy a strictly worse copy of a row we "
        "already have is the whole argument; reachability never was.",
    ),
    NoFetch(
        ("status.x.ai",),
        "skipped-403-measured",
        "refusal",
        "gb-feeds",
        "2026-09-11",
        "115 of 115 incident pages answered 403 in one live run (links/2026-09-11T1741.json), "
        "and one re-probe since",
        "gb-feeds parses status.x.ai/feed.xml (200) into a titled row for these exact urls, so "
        "the 115 requests bought nothing and cost 29% of a 400-url budget",
    ),
)

# Not a host rule and not a measurement — a url with no fetchable scheme (`mailto:`,
# `javascript:`) cannot be requested at all. Carried in the same shape so the artifact has one
# concept for "recorded, not requested" instead of three.
UNFETCHABLE = NoFetch(
    (),
    "skipped-unfetchable-scheme",
    "unfetchable",
    "none",
    "2026-09-11",
    "no http(s) scheme or no host",
    "there is nothing to request; the url is still recorded so the corpus stays complete",
)


class Root(NamedTuple):
    """One producer root we harvest urls out of.

    `links_key` is the extra top-level array some artifacts carry (`usecases` has 814 curated
    links that are NOT rows). `row_source_too` means a row's `source` is itself a document worth
    following, not just the feed it arrived on.
    """

    label: str
    directory: str
    links_key: Optional[str]
    row_source_too: bool
    why: str


ROOTS: Tuple[Root, ...] = (
    Root(
        "x",
        "x",
        None,
        False,
        "links found inside X posts — the operator's 'every url from those posts'",
    ),
    Root("feeds", "feeds", None, False, "blog/RSS item urls"),
    Root(
        "github",
        "github",
        None,
        True,
        "repo urls; gb-github rows carry the url in `source`, not `url`",
    ),
    Root("sources", "sources", None, False, "xAI docs + Cursor changelog page urls"),
    Root(
        "usecases",
        "usecases",
        "links",
        True,
        "814 curated links plus every bot's source post",
    ),
)


class Rule(NamedTuple):
    """One classification rule. First match wins; the id is recorded on the row.

    `hosts` empty means "any host" (a path-only rule). A host matches exactly or as a suffix
    (`docs.cursor.com` matches the `cursor.com` rule only via an explicit entry, so subdomains
    never classify by accident).
    """

    id: str
    hosts: Tuple[str, ...]
    paths: Tuple[str, ...]
    kind: str


RULES: Tuple[Rule, ...] = (
    Rule("xai-docs-host", ("docs.x.ai",), (), "xai-docs"),
    Rule("xai-docs-path", ("x.ai",), ("/docs",), "xai-docs"),
    Rule("xai-news", ("x.ai", "status.x.ai"), ("/news", "/blog", "/"), "blog"),
    Rule("cursor-docs-host", ("docs.cursor.com",), (), "cursor-docs"),
    Rule("cursor-docs-path", ("cursor.com",), ("/docs",), "cursor-docs"),
    Rule(
        "cursor-marketplace",
        ("cursor.com",),
        ("/marketplace", "/plugins", "/directory", "/extensions"),
        "marketplace",
    ),
    Rule("cursor-blog", ("cursor.com",), ("/blog", "/changelog"), "blog"),
    Rule(
        "github",
        ("github.com", "gist.github.com", "raw.githubusercontent.com", "github.io"),
        (),
        "github-repo",
    ),
    Rule(
        "registry",
        (
            "npmjs.com",
            "pypi.org",
            "crates.io",
            "marketplace.visualstudio.com",
            "open-vsx.org",
            "smithery.ai",
            "glama.ai",
            "mcp.so",
            "pulsemcp.com",
            "hub.docker.com",
            "apify.com",
            "zapier.com",
            "make.com",
        ),
        (),
        "marketplace",
    ),
    Rule(
        "video",
        ("youtube.com", "youtu.be", "vimeo.com", "loom.com", "twitch.tv"),
        (),
        "video",
    ),
    Rule(
        "social",
        (
            "x.com",
            "twitter.com",
            "t.co",
            "reddit.com",
            "linkedin.com",
            "news.ycombinator.com",
            "bsky.app",
            "mastodon.social",
            "threads.net",
            "discord.com",
            "discord.gg",
            "t.me",
            "facebook.com",
            "instagram.com",
            "forum.cursor.com",
        ),
        (),
        "social",
    ),
    Rule(
        "blog-host",
        (
            "substack.com",
            "medium.com",
            "dev.to",
            "hashnode.dev",
            "hashnode.com",
            "ghost.io",
            "wordpress.com",
            "blogspot.com",
            "hackernoon.com",
            "towardsdatascience.com",
            "towardsai.net",
        ),
        (),
        "blog",
    ),
    Rule(
        "blog-path",
        (),
        ("/blog", "/posts", "/post", "/news", "/articles", "/changelog"),
        "blog",
    ),
)
FALLBACK_RULE = "unmatched"
FALLBACK_KIND = "other"
KINDS = (
    "github-repo",
    "xai-docs",
    "cursor-docs",
    "marketplace",
    "blog",
    "video",
    "social",
    FALLBACK_KIND,
)

# Order the fetch budget by information gain, lowest number first. `github-repo` is LAST on
# purpose: `gb-github` already carries each repo's title, description, stars, language and
# topics from the API, so re-scraping its landing page is the one fetch in this corpus that is
# nearly guaranteed to teach us nothing — and it is 853 of 1670 fetchable urls. See `plan`.
FETCH_PRIORITY = {
    FALLBACK_KIND: 0,
    "blog": 0,
    "video": 1,
    "marketplace": 1,
    "xai-docs": 2,
    "cursor-docs": 2,
    "social": 3,
    "github-repo": 4,
}

_WS = re.compile(r"\s+")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}")


# ------------------------------------------------------------------------------------ primitives


def _silence(stream: object) -> None:
    """Point a stream's fd at /dev/null so the interpreter's shutdown flush cannot re-raise on a
    dead pipe.

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

    Swallowing EPIPE HERE rather than leaning on `gbtypes.main`'s top-level arm is deliberate and
    specific to this producer: that arm exits 0 UNCONDITIONALLY, and `gb-links` returns 2 when
    the harvest is empty or a run produced neither a fetch nor a cache hit, and 1 from a failing
    `--selftest`. A human typing `| head` must not convert any of those into a green tick.
    Cancellation (SIGINT/SIGTERM -> 130/143, no partial artifact) still belongs to the spine,
    which is why `main(body)` stays.
    """
    try:
        print(text)
    except BrokenPipeError:
        _silence(sys.stdout)


def emit_err(text: str) -> None:
    """The same guarantee for stderr — and MEASURED to be necessary, not added by symmetry.

    Before this existed: a deliberately failing `--selftest` under `2>&1 | <closed pipe>` exited
    **0**, because the EPIPE from printing the FAIL lines escaped `selftest()` into the spine's
    unconditional `except BrokenPipeError: exit(0)`. A broken producer reported healthy to
    anything that pipes it. Guarding only stdout is immunity by accident.
    """
    try:
        print(text, file=sys.stderr)
    except BrokenPipeError:
        _silence(sys.stderr)


def now_z(when: Optional[dt.datetime] = None) -> str:
    """ISO-8601 in UTC with a `Z`, to the second. One timestamp format in this artifact."""
    d = when or dt.datetime.now(dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canon_url(raw: Optional[str]) -> str:
    """One url string per document, shared byte-for-byte with `gb-feeds` and `gb-github`.

    Drops the fragment and referral parameters, sorts what survives, normalises the trailing
    slash, lowercases scheme and host, folds `http` -> `https`, strips a leading `www.`.

    This function is the whole dedup story: `.../grok-bot?utm_source=newsletter` and
    `http://www.x.ai/news/grok-bot/` are the same document, and if they hash differently the
    daily diff doubles. Leg `canon-dedups-utm` proves it, and `canon-detector-fires-on-known-bad`
    proves that leg could actually fail.
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
    """`blake2b(canonical_url.lower(), digest_size=16)` -> 32 hex. The shared content address.

    The kind is deliberately NOT in the hash: two producers that saw the same document MUST
    collide, because that collision is how the store knows they are the same thing.
    """
    key = canon_url(url).lower() or f"urn:gb-links:{fallback.strip().lower()}"
    return hashlib.blake2b(key.encode("utf-8"), digest_size=16).hexdigest()


def host_of(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def tidy(raw: Optional[str], limit: int = SUMMARY_CHARS) -> str:
    """Collapse whitespace and cap length. Page titles arrive with newlines and tab indentation."""
    if not raw:
        return ""
    text = _WS.sub(" ", raw).strip()
    return (text[: limit - 1] + "\u2026") if len(text) > limit else text


def norm_iso(raw: Optional[str]) -> Optional[str]:
    """ISO-8601 (with `Z` or an offset) -> `...Z`. Unparseable -> None, never a guessed date."""
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return now_z(parsed)


def host_matches(host: str, patterns: Tuple[str, ...]) -> bool:
    """Exact host or a dotted subdomain of it. `xcom.test` must never match `x.com`."""
    return any(host == h or host.endswith("." + h) for h in patterns)


def path_matches(path: str, prefixes: Tuple[str, ...]) -> bool:
    """Path-prefix match on SEGMENT boundaries, with `/` meaning the homepage and nothing else.

    MEASURED BUG, caught by the first wide live run: the old form normalised each prefix with
    `pref.rstrip("/")` and then asked `path.startswith(...)`, which turns the prefix `/` into the
    empty string — so a rule listing `/` matched EVERY path on that host. `status.x.ai/api-…/INC…`
    was therefore classified `blog` by the `xai-news` rule, took the highest fetch priority, and
    spent 115 requests on incident pages. A prefix meant to catch a homepage matched an entire
    site.
    """
    for pref in prefixes:
        if pref == "/":
            if path in ("", "/"):
                return True
            continue
        stem = pref.rstrip("/")
        if path == stem or path.startswith(stem + "/"):
            return True
    return False


def classify(url: str) -> Tuple[str, str]:
    """(kind, rule_id). First matching rule wins; the rule id rides on the row.

    A classification with no provenance is a number nobody can argue with. Recording the rule
    turns "why is this a blog?" from an investigation into a grep — which is exactly how the
    `/`-prefix bug above was found.
    """
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower()
    path = (p.path or "/").lower()
    for rule in RULES:
        if rule.hosts and not host_matches(host, rule.hosts):
            continue
        if rule.paths and not path_matches(path, rule.paths):
            continue
        return rule.kind, rule.id
    return FALLBACK_KIND, FALLBACK_RULE


def fetch_rank(link: "Link") -> Tuple[int, str]:
    """Queue position for one url: `(information-gain priority, url)`.

    Total and deterministic — the url tiebreak means two runs over the same corpus produce the
    same queue, so which urls a budget of N reaches is reproducible rather than incidental.
    """
    return FETCH_PRIORITY.get(classify(link.url)[0], 9), link.url


def no_fetch_for(url: str) -> Optional[NoFetch]:
    """The measured reason this url is recorded rather than requested, or None.

    Computed every run, never cached: these are RULES about hosts, not observations about pages,
    and a rule that got stale in a cache would be invisible.
    """
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return UNFETCHABLE
    host = p.netloc.lower()
    for entry in NO_FETCH:
        if host_matches(host, entry.hosts):
            return entry
    return None


# --------------------------------------------------------------------------------------- harvest


class Link(NamedTuple):
    """One distinct url the corpus knows about, plus every artifact that mentioned it."""

    url: str
    origins: Tuple[str, ...]
    hint: str


def harvest_doc(doc: dict, root: Root) -> List[Tuple[str, str]]:
    """(canonical_url, title_hint) for every url in ONE parsed artifact. Pure; no disk, no network.

    `gb-github` rows put the url in `source` and have no `url` key; `gb-feeds` rows put the ITEM
    url in `url` and the FEED url in `source`. Preferring `url` and falling back to `source`
    therefore reads both correctly, and `row_source_too` adds `source` as a SECOND url only where
    it is a document (a bot's originating post) rather than a feed we already poll daily.
    """
    out: List[Tuple[str, str]] = []
    for row in doc.get("rows") or []:
        if not isinstance(row, dict):
            continue
        primary = row.get("url") or row.get("source")
        hint = tidy(row.get("title") or row.get("name") or "", 200)
        if isinstance(primary, str):
            out.append((primary, hint))
        extra = row.get("source")
        if root.row_source_too and isinstance(extra, str) and extra != primary:
            out.append((extra, hint))
    if root.links_key:
        for link in doc.get(root.links_key) or []:
            if isinstance(link, dict) and isinstance(link.get("url"), str):
                out.append((link["url"], tidy(link.get("title") or "", 200)))
    return [(canon_url(u), h) for u, h in out if canon_url(u)]


def newest(directory: pathlib.Path) -> Optional[pathlib.Path]:
    files = sorted(p for p in directory.glob("*.json") if _STAMP.match(p.name))
    return files[-1] if files else None


def harvest(
    root_dir: pathlib.Path, roots: Tuple[Root, ...] = ROOTS
) -> Tuple[List[Link], List[dict]]:
    """Every url in every producer root, deduplicated, plus a source record for each root.

    Defensive by construction: an absent root (a sibling has not shipped yet), an unreadable one,
    and one caught mid-write all produce a source record with an explicit error and zero rows.
    None of them stop the run, and none of them look like "that producer found nothing".
    """
    merged: Dict[str, Link] = {}
    sources: List[dict] = []
    for root in roots:
        directory = root_dir / root.directory
        record = {
            "id": root.label,
            "url": f"{root.directory}/",
            "kind": "local-artifact",
            "status": None,
            "items": 0,
            "rows": 0,
            "error": None,
            "note": root.why,
        }
        path = newest(directory) if directory.is_dir() else None
        if path is None:
            record["error"] = f"absent: no {root.directory}/<stamp>.json yet"
            sources.append(record)
            continue
        try:
            doc = json.loads(path.read_text())
            size = path.stat().st_size
        except (OSError, ValueError) as exc:
            record["url"] = f"{root.directory}/{path.name}"
            record["error"] = f"unreadable: {type(exc).__name__}: {exc}"
            sources.append(record)
            continue
        found = harvest_doc(doc, root)
        label = f"{root.directory}/{path.name}"
        fresh = 0
        for url, hint in found:
            prior = merged.get(url)
            if prior is None:
                merged[url] = Link(url, (label,), hint)
                fresh += 1
            elif label not in prior.origins:
                merged[url] = Link(
                    url, tuple(sorted(prior.origins + (label,))), prior.hint or hint
                )
        record.update(
            {
                "url": label,
                "status": 200,
                "bytes": size,
                "items": len(found),
                "rows": fresh,
                "error": None,
            }
        )
        sources.append(record)
    return sorted(merged.values(), key=lambda link: link.url), sources


# ----------------------------------------------------------------------------------- html + fetch


class _Extract(HTMLParser):
    """Structure out of markup with the stdlib parser. Never a regex — `<title>` inside a comment,
    an attribute order nobody expected, and an unclosed tag all defeat a pattern and none of them
    defeat a parser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og_title = ""
        self.og_desc = ""
        self.meta_desc = ""
        self.canonical = ""
        self.published = ""
        self.h1 = ""
        self._title_parts: List[str] = []
        self._h1_parts: List[str] = []
        self._in_title = False
        self._in_h1 = False
        self._h1_done = False
        self._svg = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "svg":
            self._svg += 1
        elif tag == "title" and self._svg == 0 and not self.title:
            self._in_title = True
        elif tag == "meta":
            prop = (a.get("property") or a.get("name") or "").strip().lower()
            content = a.get("content", "").strip()
            if not content:
                return
            if prop == "og:title" and not self.og_title:
                self.og_title = content
            elif prop in ("og:description", "twitter:description") and not self.og_desc:
                self.og_desc = content
            elif prop == "description" and not self.meta_desc:
                self.meta_desc = content
            elif (
                prop in ("article:published_time", "og:article:published_time", "date")
                and not self.published
            ):
                self.published = content
        elif tag == "link":
            rel = a.get("rel", "").strip().lower()
            if "canonical" in rel.split() and not self.canonical:
                self.canonical = a.get("href", "").strip()
        elif tag == "h1" and not self._h1_done:
            self._in_h1 = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "svg" and self._svg:
            self._svg -= 1
        elif tag == "title" and self._in_title:
            self._in_title = False
            self.title = tidy("".join(self._title_parts), 300)
        elif tag == "h1" and self._in_h1:
            self._in_h1 = False
            self._h1_done = True
            self.h1 = tidy("".join(self._h1_parts), 300)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_h1:
            self._h1_parts.append(data)


def is_htmlish(content_type: str) -> bool:
    base = (content_type or "").split(";", 1)[0].strip().lower()
    return base in HTML_TYPES or base.endswith("+xml")


def charset_of(content_type: str) -> str:
    for part in (content_type or "").split(";")[1:]:
        k, _, v = part.partition("=")
        if k.strip().lower() == "charset" and v.strip():
            return v.strip().strip("\"'")
    return "utf-8"


def extract(body: bytes, content_type: str) -> dict:
    """Title/description/canonical/h1 from an HTML body. A non-HTML body extracts NOTHING.

    Returning empty fields for a pdf is the honest answer; running an HTML parser over a binary
    and shipping whatever text fell out would be a summary that looks real and is not.
    """
    blank = {
        "title": "",
        "og_title": "",
        "og_desc": "",
        "meta_desc": "",
        "canonical": "",
        "h1": "",
        "published": None,
        "parsed": False,
    }
    if body is None or not is_htmlish(content_type):
        return blank
    text = body.decode(charset_of(content_type), errors="replace")
    parser = _Extract()
    try:
        parser.feed(text)
        parser.close()
    except (AssertionError, ValueError):
        # A pathological document must cost one page, not the run.
        return dict(blank, parsed=False)
    return {
        "title": parser.title,
        "og_title": tidy(parser.og_title, 300),
        "og_desc": tidy(parser.og_desc),
        "meta_desc": tidy(parser.meta_desc),
        "canonical": parser.canonical,
        "h1": parser.h1,
        "published": norm_iso(parser.published),
        "parsed": True,
    }


class _CountingRedirects(urllib.request.HTTPRedirectHandler):
    """Counts hops so a row can say `3 redirects` instead of quietly reporting the wrong url,
    and teaches this interpreter about **308 Permanent Redirect**.

    MEASURED, twice, on the real corpus — and the first fix was wrong, which is why the second
    one is spelled out here. `https://ayautomate.com/blog/...` 308s to its `www.` host (and our
    canonicaliser strips `www.`, so this is a shape we WILL keep meeting). On 3.9.6 that needs
    TWO repairs, not one:

      1. `HTTPRedirectHandler` has `http_error_301/302/303/307` and no `http_error_308`, so the
         status never reaches a redirect handler at all. Hence the alias — the same one CPython
         itself adds in 3.11.
      2. Adding only that still fails, and fails QUIETLY LOOKING FIXED: the hop is counted and
         the page is still recorded 308 with zero bytes. `redirect_request` whitelists
         `(301, 302, 303, 307)` and RAISES `HTTPError` for anything else, so 308 is refused one
         layer deeper. Presenting it to `super()` as a 301 is exactly right for a GET-only
         client: both are permanent and both preserve the method.

    Without both, every host that normalises to `www.` with a 308 is recorded as dead — and the
    row would carry `redirects: 1`, which reads like the redirect was followed.
    """

    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_301

    def __init__(self) -> None:
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        self.count += 1
        return super().redirect_request(
            req, fp, 301 if code == 308 else code, msg, headers, newurl
        )


def _request(url: str, timeout: int, max_bytes: int) -> dict:
    """ONE HTTP request. No retry, no fallback, no policy — just the observation.

    The result ALWAYS carries `status`: an int for an HTTP answer, None only when the connection
    never produced one. A 403 is a measurement of the host, not an absence of a page. Split out
    of `fetch` so the retry and scheme-fallback policy above it is testable offline by injecting
    a fake transport rather than by standing up a server.
    """
    result: dict = {
        "url": url,
        "final_url": url,
        "status": None,
        "bytes": 0,
        "content_type": "",
        "etag": "",
        "redirects": 0,
        "truncated": False,
        "error": None,
        "fetched_at": now_z(),
        "body": None,
    }
    handler = _CountingRedirects()
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": ACCEPT})
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(max_bytes + 1)
            result["truncated"] = len(body) > max_bytes
            body = body[:max_bytes]
            result["status"] = getattr(resp, "status", None) or resp.getcode()
            result["final_url"] = resp.geturl() or url
            result["bytes"] = len(body)
            result["content_type"] = resp.headers.get("Content-Type", "") or ""
            result["etag"] = resp.headers.get("ETag", "") or ""
            result["body"] = body
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        result["content_type"] = (
            exc.headers.get("Content-Type", "") if exc.headers else ""
        )
        result["error"] = f"http {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        result["error"] = f"{type(exc).__name__}: {exc.reason}"
    except (OSError, ValueError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["redirects"] = handler.count
    return result


def is_tls_failure(error: Optional[str]) -> bool:
    """True when a transport error is TLS-level rather than the host being absent.

    Distinguishing the two is what makes the http fallback below safe: a DNS failure means there
    is nothing there, and retrying it over http is a wasted request. A TLS failure means
    something answered on 443 and could not speak TLS, which is the shape a plain-http site has.
    """
    if not error:
        return False
    upper = error.upper()
    return any(
        marker in upper
        for marker in (
            "SSL",
            "CERTIFICATE",
            "TLSV1",
            "WRONG_VERSION_NUMBER",
            "SSLERROR",
        )
    )


def fetch(
    url: str,
    *,
    timeout: int = TIMEOUT,
    max_bytes: int = MAX_BYTES,
    retries: int = 1,
    request: Optional[Callable[[str, int, int], dict]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """One page, with a deadline, a byte cap, one retry on a transient answer, and one http
    fallback when TLS itself fails. Both repairs are MEASURED, not speculative.

    RETRY (429 / 5xx, once, after `RETRY_SLEEP`): the first wide pass recorded two 429s and a
    503 out of 845 pages. Same policy and same constant as `gb-feeds`, so the two producers
    treat a rate-limited host the same way.

    HTTP FALLBACK: 13 urls failed with `[SSL: WRONG_VERSION_NUMBER]` — 11 of them one host —
    which means the host serves PLAIN HTTP on port 443. They arrive here as `https://` because
    the shared canonicaliser folds `http` -> `https`, and that fold is load-bearing for id
    agreement across producers, so it cannot be relaxed. Requesting the http url instead while
    KEEPING the canonical https url (and therefore the id) recovers the page without touching
    the content address. Recorded as `signals.scheme_fallback` with the TLS error that caused
    it, so the repair is visible rather than silently papering over a broken site.

    The fallback fires ONLY on a TLS-level failure, never on DNS: a host that does not resolve
    has nothing to offer over http either, and 8 of the 32 failures were exactly that.
    """
    send = request or _request
    attempt = 0
    while True:
        attempt += 1
        out = send(url, timeout, max_bytes)
        status = out.get("status")
        transient = status is not None and (status >= 500 or status == 429)
        if transient and attempt <= retries:
            sleep(RETRY_SLEEP)
            continue
        break
    out["attempts"] = attempt
    if (
        out.get("status") is None
        and is_tls_failure(out.get("error"))
        and url.startswith("https://")
    ):
        alt = send("http://" + url[len("https://") :], timeout, max_bytes)
        if alt.get("status") is not None:
            alt["url"] = url
            alt["attempts"] = attempt + 1
            alt["scheme_fallback"] = "http"
            alt["tls_error"] = out.get("error")
            return alt
    return out


class PoliteFetcher:
    """`fetch` plus a PER-HOST spacing of >=1s.

    Per host, not global: one global sleep makes 40 distinct hosts take 40 seconds for no reason
    while still allowing a burst against a single host if the order happens to interleave. The
    clock is injectable so the selftest can prove the spacing without spending real seconds.
    """

    def __init__(
        self,
        *,
        gap: float = PER_HOST_SLEEP,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        inner: Callable[[str], dict] = fetch,
    ) -> None:
        self.gap = gap
        self.clock = clock
        self.sleep = sleep
        self.inner = inner
        self.last: Dict[str, float] = {}
        self.waits: List[Tuple[str, float]] = []

    def __call__(self, url: str) -> dict:
        host = host_of(url)
        prev = self.last.get(host)
        now = self.clock()
        if prev is not None:
            wait = self.gap - (now - prev)
            if wait > 0:
                self.waits.append((host, round(wait, 3)))
                self.sleep(wait)
        out = self.inner(url)
        self.last[host] = self.clock()
        return out


# ----------------------------------------------------------------------------------------- cache


def empty_cache() -> dict:
    return {"schema": CACHE_SCHEMA, "updated_at": None, "entries": {}}


def load_cache(root_dir: pathlib.Path) -> Tuple[dict, Optional[str]]:
    """The fetch cache off disk, or an empty one. A corrupt cache is a warning, never a crash —
    the worst it can cost is one run of re-fetching."""
    path = root_dir / "links" / ".cache.json"
    if not path.is_file():
        return empty_cache(), None
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return empty_cache(), f"{type(exc).__name__}: {exc}"
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), dict):
        return empty_cache(), "cache has no entries map"
    return doc, None


def cache_age_days(entry: dict, now: dt.datetime) -> Optional[float]:
    stamp = norm_iso(entry.get("fetched_at"))
    if not stamp:
        return None
    when = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )
    return (now - when).total_seconds() / 86400.0


def cache_fresh(entry: dict, now: dt.datetime, max_age_days: float) -> bool:
    """Fresh means: inside the shelf life for its outcome. A failure has a shorter one."""
    age = cache_age_days(entry, now)
    if age is None or age < 0:
        return False
    limit = (
        max_age_days
        if entry.get("status") == 200
        else min(max_age_days, FAIL_MAX_AGE_DAYS)
    )
    return age <= limit


# ------------------------------------------------------------------------------------ row + build


def row_from_result(link: Link, result: dict, *, fetch_state: str) -> dict:
    """One artifact row from one fetch outcome (live or cached). No clock outside `signals`."""
    kind, rule = classify(link.url)
    extracted = result.get("extracted") or {}
    title = (
        extracted.get("og_title")
        or extracted.get("title")
        or extracted.get("h1")
        or link.hint
        or ""
    )
    summary = extracted.get("og_desc") or extracted.get("meta_desc") or ""
    final = canon_url(result.get("final_url") or link.url)
    signals = {
        "status": result.get("status"),
        "classified_by": rule,
        "origins": list(link.origins),
        "origin_count": len(link.origins),
        "fetch": fetch_state,
        "fetched_at": result.get("fetched_at"),
        "content_type": (result.get("content_type") or "").split(";", 1)[0].strip(),
        "bytes": result.get("bytes", 0),
        "redirects": result.get("redirects", 0),
        "host": host_of(link.url),
    }
    if result.get("error"):
        signals["error"] = result["error"]
    if result.get("truncated"):
        signals["truncated"] = True
    if result.get("scheme_fallback"):
        # Visible on the row, not silently repaired: the page came from http because https
        # could not complete a handshake, and the canonical url (and the id) stayed https.
        signals["scheme_fallback"] = result["scheme_fallback"]
        signals["tls_error"] = result.get("tls_error")
    if (result.get("attempts") or 1) > 1:
        signals["attempts"] = result["attempts"]
    if extracted.get("canonical"):
        signals["canonical_link"] = extracted["canonical"]
    if extracted.get("h1") and extracted["h1"] != title:
        signals["h1"] = extracted["h1"]
    if final and final != link.url:
        signals["final_url"] = final
    if result.get("content_type") and not is_htmlish(result["content_type"]):
        signals["extraction"] = "skipped-non-html"
    return {
        "id": content_id(link.url),
        "source": link.origins[0] if link.origins else "links/",
        "url": link.url,
        "title": tidy(title, 300),
        "kind": kind,
        "published_at": extracted.get("published"),
        "summary": tidy(summary),
        "signals": signals,
    }


def deferred_row(link: Link) -> dict:
    """A url we know about and have not spent budget on yet. Still a row: the artifact is the
    whole corpus, so tomorrow's tick can see exactly what today's budget did not reach."""
    kind, rule = classify(link.url)
    return {
        "id": content_id(link.url),
        "source": link.origins[0] if link.origins else "links/",
        "url": link.url,
        "title": link.hint,
        "kind": kind,
        "published_at": None,
        "summary": "",
        "signals": {
            "status": None,
            "classified_by": rule,
            "origins": list(link.origins),
            "origin_count": len(link.origins),
            "fetch": "deferred",
            "fetched_at": None,
            "host": host_of(link.url),
        },
    }


def entry_from_result(result: dict) -> dict:
    """What survives into the cache: enough to rebuild a row without touching the network."""
    extracted = result.get("extracted") or {}
    return {
        "status": result.get("status"),
        "fetched_at": result.get("fetched_at"),
        "final_url": result.get("final_url"),
        "content_type": result.get("content_type", ""),
        "bytes": result.get("bytes", 0),
        "redirects": result.get("redirects", 0),
        "etag": result.get("etag", ""),
        "error": result.get("error"),
        "truncated": bool(result.get("truncated")),
        "attempts": result.get("attempts", 1),
        "scheme_fallback": result.get("scheme_fallback"),
        "tls_error": result.get("tls_error"),
        "title": extracted.get("title", ""),
        "og_title": extracted.get("og_title", ""),
        "og_desc": extracted.get("og_desc", ""),
        "meta_desc": extracted.get("meta_desc", ""),
        "canonical": extracted.get("canonical", ""),
        "h1": extracted.get("h1", ""),
        "published": extracted.get("published"),
        "parsed": bool(extracted.get("parsed")),
    }


def result_from_entry(entry: dict) -> dict:
    """The inverse: a cached entry back into the shape `row_from_result` consumes."""
    return {
        "status": entry.get("status"),
        "final_url": entry.get("final_url"),
        "content_type": entry.get("content_type", ""),
        "bytes": entry.get("bytes", 0),
        "redirects": entry.get("redirects", 0),
        "error": entry.get("error"),
        "truncated": bool(entry.get("truncated")),
        "attempts": entry.get("attempts", 1),
        "scheme_fallback": entry.get("scheme_fallback"),
        "tls_error": entry.get("tls_error"),
        "fetched_at": entry.get("fetched_at"),
        "extracted": {
            "title": entry.get("title", ""),
            "og_title": entry.get("og_title", ""),
            "og_desc": entry.get("og_desc", ""),
            "meta_desc": entry.get("meta_desc", ""),
            "canonical": entry.get("canonical", ""),
            "h1": entry.get("h1", ""),
            "published": entry.get("published"),
            "parsed": bool(entry.get("parsed")),
        },
    }


def plan(
    links: List[Link], cache: dict, *, now: dt.datetime, max_age_days: float
) -> Tuple[List[Tuple[NoFetch, List[Link]]], List[Link], List[Link], List[Link]]:
    """Split the corpus into (recorded, fetch-now, cache-hit, stale) before spending anything.

    Two orderings, both deliberate.

    COVERAGE BEFORE FRESHNESS: never-fetched urls come before stale ones. A budget that refreshes
    yesterday's 150 pages forever never reaches url 151, and the operator asked for far and wide
    before fresh and narrow.

    INFORMATION GAIN BEFORE ALPHABET, inside the never-fetched set (`FETCH_PRIORITY`). Measured
    on the real corpus: 853 of 1670 fetchable urls are github.com, and at one request per second
    per host a plain alphabetical queue spends its first FOURTEEN MINUTES re-scraping repo
    landing pages whose title, description, stars and language `gb-github` already has from the
    API — while a blog post nobody has ever read waits until tomorrow. Fetching an already-known
    page is not politeness-neutral either: it is 853 requests at a host that owes us nothing.
    So the long tail goes first and github goes last. Nothing is dropped; only the order changes,
    and the order is deterministic (`(priority, url)`) so two runs of the same corpus agree.
    """
    groups: Dict[str, List[Link]] = {}
    hits: List[Link] = []
    unseen: List[Link] = []
    stale: List[Link] = []
    entries = cache.get("entries") or {}
    for link in links:
        reason = no_fetch_for(link.url)
        if reason is not None:
            groups.setdefault(reason.status, []).append(link)
            continue
        entry = entries.get(link.url)
        if entry is None:
            unseen.append(link)
        elif cache_fresh(entry, now, max_age_days):
            hits.append(link)
        else:
            stale.append(link)
    recorded = [
        (reason, groups[reason.status])
        for reason in NO_FETCH + (UNFETCHABLE,)
        if reason.status in groups
    ]
    return (
        recorded,
        sorted(unseen, key=fetch_rank) + sorted(stale, key=fetch_rank),
        hits,
        stale,
    )


def build_document(
    *,
    links: List[Link],
    sources: List[dict],
    fetcher: Callable[[str], dict],
    cache: dict,
    now: Optional[dt.datetime] = None,
    limit: int = DEFAULT_LIMIT,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    cache_error: Optional[str] = None,
) -> Tuple[dict, dict, dict]:
    """The whole artifact from injectable parts -> (document, updated_cache, counters).

    Takes NO root and calls NO writer, by construction: a store can import this module and build
    an artifact in memory with no flag to get wrong, and `--selftest` can run the entire pipeline
    offline. The AST legs (`one-writer-only`, `build-document-takes-no-root`) enforce that a
    future edit cannot quietly add a write path here.
    """
    when = now or dt.datetime.now(dt.timezone.utc)
    recorded, queue, hits, stale = plan(
        links, cache, now=when, max_age_days=max_age_days
    )
    entries = dict(cache.get("entries") or {})

    rows: List[dict] = []
    counters = {
        "distinct_urls": len(links),
        "recorded_not_fetched": sum(len(group) for _, group in recorded),
        "cache_hits": 0,
        "fetched": 0,
        "network_requests": 0,
        "deferred": 0,
        "stale_eligible": len(stale),
        "errors": 0,
    }

    for link in hits:
        result = result_from_entry(entries[link.url])
        rows.append(row_from_result(link, result, fetch_state="cache"))
        counters["cache_hits"] += 1

    budget = max(0, int(limit))
    for link in queue:
        if budget <= 0:
            rows.append(deferred_row(link))
            counters["deferred"] += 1
            continue
        budget -= 1
        counters["network_requests"] += 1
        result = fetcher(link.url)
        result["extracted"] = extract(
            result.pop("body", None), result.get("content_type", "")
        )
        entries[link.url] = entry_from_result(result)
        rows.append(row_from_result(link, result, fetch_state="fetched"))
        counters["fetched"] += 1
        if result.get("status") != 200:
            counters["errors"] += 1

    rows.sort(key=lambda r: r["url"])
    statuses = collections.Counter(
        str(
            r["signals"].get("status")
            or (r["signals"].get("error") and "error")
            or "none"
        )
        for r in rows
    )
    kinds = collections.Counter(r["kind"] for r in rows)
    domains = collections.Counter(r["signals"]["host"] for r in rows)
    updated = {
        "schema": CACHE_SCHEMA,
        "updated_at": now_z(when),
        "entries": entries,
    }
    doc = {
        "schema": SCHEMA,
        "captured_at": now_z(when),
        "id_algorithm": "blake2b-16(canonical_url.lower()) -> 32 hex",
        # Every clock-derived fact lives under `signals` and is named here, so the
        # content-addressed store strips it before hashing. Without this the artifact would
        # allocate a new blob for every row on every run and measure nothing.
        "volatile_fields": [
            "signals.fetched_at",
            "signals.fetch",
        ],
        "budget": {
            "limit": int(limit),
            "max_age_days": max_age_days,
            "fail_max_age_days": FAIL_MAX_AGE_DAYS,
            "per_host_sleep_s": PER_HOST_SLEEP,
            "timeout_s": TIMEOUT,
            "max_bytes": MAX_BYTES,
        },
        "cache": {
            "path": "links/.cache.json",
            "entries_before": len(cache.get("entries") or {}),
            "entries_after": len(entries),
            "error": cache_error,
        },
        "counts": dict(
            counters,
            rows=len(rows),
            by_kind=dict(sorted(kinds.items())),
            by_status=dict(sorted(statuses.items())),
            top_domains=dict(domains.most_common(15)),
        ),
        "sources": sources,
        # Recorded, not fetched, and deliberately NOT rows. Every group is a MEASURED refusal
        # whose content another producer already holds, so a stub row here would content-address
        # to the same id as that producer's titled row and the store would flip between a real
        # row and an empty one every single day. See `NO_FETCH`.
        "recorded": {
            "count": sum(len(group) for _, group in recorded),
            "groups": [
                dict(
                    reason._asdict(),
                    count=len(group),
                    urls=[link.url for link in group],
                )
                for reason, group in recorded
            ],
        },
        "rows": rows,
    }
    return doc, updated, counters


# ---------------------------------------------------------------------------------------- writers


def write_outputs(root_dir: pathlib.Path, doc: dict, cache: dict) -> pathlib.Path:
    """THE ONLY writer in this module. Both files go through `gbtypes.atomic_write_text`, so a
    crash mid-write leaves the previous artifact and the previous cache exactly as they were —
    a truncated cache would silently re-fetch the world, and a truncated artifact would report a
    measurement that never happened."""
    out = root_dir / "links" / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(doc, indent=1) + "\n")
    atomic_write_text(
        root_dir / "links" / ".cache.json", json.dumps(cache, indent=1) + "\n"
    )
    return out


def writer_functions(src: str) -> set:
    """Names of the functions in `src` that can write a file. STRUCTURAL, over the AST.

    No behavioural test can prove the ABSENCE of a write path a future edit adds — only a check
    over the source can. Adopted from `gb-github`/`gb-feeds` so all four producers make the same
    argument the same way. `os.replace` is excluded for the reason the siblings excluded it: it is
    the atomic primitive's own move, not a raw write.
    """
    import ast as _ast

    out: set = set()
    for node in _ast.walk(_ast.parse(src)):
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        for inner in _ast.walk(node):
            if not isinstance(inner, _ast.Call):
                continue
            f = inner.func
            attr = isinstance(f, _ast.Attribute)
            name = f.attr if attr else getattr(f, "id", "")
            base = getattr(f.value, "id", "") if attr else ""
            args = inner.args
            if name in (
                "atomic_write_text",
                "atomic_write_bytes",
                "atomic_write_json",
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


def takes_no_root(src: str, fname: str) -> bool:
    """True when `fname` has no parameter that looks like a filesystem root. A builder that cannot
    be handed a directory cannot write to one by accident."""
    import ast as _ast

    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.FunctionDef) and node.name == fname:
            spec = node.args
            names = [a.arg for a in spec.args + spec.kwonlyargs + spec.posonlyargs]
            return not any(
                n in ("root", "root_dir", "path", "out", "outdir") for n in names
            )
    return False


def unguarded_prints(src: str) -> set:
    """Functions that call bare `print` instead of `emit`/`emit_err`. STRUCTURAL, over the AST.

    Why a source check and not a behavioural one: the failure mode is that a NEW print, added
    later on some rarely-taken path, raises EPIPE and erases a verdict. No test exercises a path
    that does not exist yet, so only a property over the source can hold the line. The two
    sanctioned writers are exempt because they ARE the guard.
    """
    import ast as _ast

    sanctioned = {"emit", "emit_err"}
    out: set = set()
    for node in _ast.walk(_ast.parse(src)):
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        if node.name in sanctioned:
            continue
        for inner in _ast.walk(node):
            if (
                isinstance(inner, _ast.Call)
                and isinstance(inner.func, _ast.Name)
                and inner.func.id == "print"
            ):
                out.add(node.name)
    return out


def unguarded_parse_args(src: str) -> int:
    """How many `parse_args` calls are NOT inside a `try` that handles `BrokenPipeError`.

    argparse writes usage and help on our behalf and only then exits, so an unguarded call is a
    verdict-erasing write we did not author. Measured: before the guard, `--bogus 2>&1 | <closed
    pipe>` exited 0 while the same command unpiped exited 2.
    """
    import ast as _ast

    tree = _ast.parse(src)

    def calls(node: object) -> int:
        return sum(
            1
            for n in _ast.walk(node)  # type: ignore[arg-type]
            if isinstance(n, _ast.Call)
            and isinstance(n.func, _ast.Attribute)
            and n.func.attr == "parse_args"
        )

    guarded = 0
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Try):
            continue
        handles_epipe = any(
            (
                isinstance(h.type, _ast.Name)
                and h.type.id == "BrokenPipeError"
                or isinstance(h.type, _ast.Tuple)
                and any(
                    isinstance(e, _ast.Name) and e.id == "BrokenPipeError"
                    for e in h.type.elts
                )
            )
            for h in node.handlers
        )
        if handles_epipe:
            guarded += sum(calls(stmt) for stmt in node.body)
    return calls(tree) - guarded


def render(doc: dict) -> str:
    c = doc["counts"]
    lines = [
        f"links {doc['captured_at']}: {c['rows']} rows from {c['distinct_urls']} distinct urls "
        f"({c['fetched']} fetched, {c['cache_hits']} cache hits, {c['deferred']} deferred, "
        f"{c['recorded_not_fetched']} recorded-not-fetched, "
        f"{c['network_requests']} requests)"
    ]
    for s in doc["sources"]:
        flag = "ok " if s.get("status") == 200 and not s.get("error") else "MISS"
        lines.append(
            f"  {flag} {s['id']:<9} {s.get('items', 0):>5} urls -> {s.get('rows', 0):>5} new   "
            f"{s['url']}" + (f"  [{s['error']}]" if s.get("error") else "")
        )
    for group in doc["recorded"]["groups"]:
        lines.append(
            f"  rec  {group['status']:<24} {group['count']:>5} urls  -> {group['owner']}"
            f"   [{group['evidence']}, measured {group['measured_at']}]"
        )
    lines.append(
        "  status   " + ", ".join(f"{k}={v}" for k, v in c["by_status"].items())
    )
    lines.append("  kinds    " + ", ".join(f"{k}={v}" for k, v in c["by_kind"].items()))
    lines.append(
        "  domains  "
        + ", ".join(f"{k}={v}" for k, v in list(c["top_domains"].items())[:10])
    )
    lines.append(
        f"  cache    {doc['cache']['entries_before']} -> {doc['cache']['entries_after']} entries"
        + (f"  [{doc['cache']['error']}]" if doc["cache"].get("error") else "")
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------------------- selftest

HTML_FULL = b"""<!DOCTYPE html><html><head>
<title>  Raw   title
tag </title>
<meta property="og:title" content="Grok Bot ships unattended routines">
<meta property="og:description" content="An always-on agent finally fired without a human.">
<meta name="description" content="fallback description nobody should see">
<link rel="canonical" href="https://example.test/post">
<meta property="article:published_time" content="2026-09-09T14:00:00Z">
</head><body><h1>Heading One</h1><p>body</p></body></html>"""

HTML_MINIMAL = b"""<!DOCTYPE html><html><head>
<title>Only a title</title>
<meta name="description" content="the meta description is the only summary here">
</head><body><svg><title>icon label</title></svg><h1>Real H1</h1></body></html>"""

HTML_NO_TITLE = (
    b"<html><body><p>plenty of body text but no title element at all</p></body></html>"
)

# The real hazard the svg guard exists for: a document whose ONLY `<title>` lives inside an
# inline svg sprite. `is_htmlish` admits `image/svg+xml` (it ends `+xml`), and curated link
# corpora do point at badge svgs, so without the guard the extracted "page title" would be the
# name of an icon. Leg `svg-title-does-not-hijack` fails if the guard is removed.
HTML_SVG_TRAP = b"""<html><head></head><body>
<svg style="display:none"><symbol id="i"><title>icon label</title></symbol></svg>
<h1>Real H1</h1></body></html>"""

PDF_BODY = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>"

X_FIXTURE = {
    "schema": "gb-x/1",
    "rows": [
        {
            "id": "0" * 32,
            "source": "https://x.com/bot/status/1",
            "url": "https://example.test/post?utm_source=x&utm_campaign=launch",
            "title": "shared from a post",
            "kind": "x-link",
        },
        {
            "id": "1" * 32,
            "source": "https://x.com/bot/status/2",
            "url": "https://x.com/bot/status/2",
            "title": "the post itself",
            "kind": "x-post",
        },
    ],
}

FEEDS_FIXTURE = {
    "schema": "gb-feeds/1",
    "rows": [
        {
            "id": "2" * 32,
            "source": "https://blog.test/rss.xml",
            "url": "http://www.example.test/post/",
            "title": "the same story, differently spelled",
            "kind": "feed-item",
        },
        {
            "id": "3" * 32,
            "source": "https://blog.test/rss.xml",
            "url": "https://docs.x.ai/docs/grok-bot",
            "title": "docs page",
            "kind": "feed-item",
        },
    ],
}

GITHUB_FIXTURE = {
    "schema": "gb-github/1",
    "rows": [
        {
            "id": "4" * 32,
            "source": "https://github.com/awslabs/mcp",
            "title": "awslabs/mcp",
            "kind": "mcp-server",
        }
    ],
}

USECASES_FIXTURE = {
    "schema": "gb-usecases/1",
    "rows": [{"name": "A bot", "source": "https://x.com/someone/status/9"}],
    "links": [
        {"title": "Cursor changelog", "url": "https://cursor.com/changelog"},
        {"title": "A spec pdf", "url": "https://example.test/spec.pdf"},
        {"title": "A redirecting page", "url": "https://example.test/old"},
        {"title": "A dead page", "url": "https://example.test/gone"},
        {"title": "not a url", "url": "mailto:nobody@example.test"},
    ],
}

_FIXTURE_PAGES: Dict[str, Tuple[int, str, Optional[bytes], str, int]] = {
    # url -> (status, content-type, body, final_url, redirects)
    "https://example.test/post": (
        200,
        "text/html; charset=utf-8",
        HTML_FULL,
        "https://example.test/post",
        0,
    ),
    "https://docs.x.ai/docs/grok-bot": (
        200,
        "text/html",
        HTML_MINIMAL,
        "https://docs.x.ai/docs/grok-bot",
        0,
    ),
    "https://github.com/awslabs/mcp": (
        200,
        "text/html",
        HTML_MINIMAL,
        "https://github.com/awslabs/mcp",
        0,
    ),
    "https://cursor.com/changelog": (
        200,
        "text/html",
        HTML_NO_TITLE,
        "https://cursor.com/changelog",
        0,
    ),
    "https://example.test/spec.pdf": (
        200,
        "application/pdf",
        PDF_BODY,
        "https://example.test/spec.pdf",
        0,
    ),
    "https://example.test/old": (
        200,
        "text/html",
        HTML_MINIMAL,
        "https://example.test/new",
        2,
    ),
    "https://example.test/gone": (
        404,
        "text/html",
        None,
        "https://example.test/gone",
        0,
    ),
}


class FixtureFetcher:
    """Offline fetcher that records every call, so a cache leg can prove ZERO requests happened."""

    def __init__(self, at: str = "2026-09-11T12:00:00Z") -> None:
        self.calls: List[str] = []
        self.at = at

    def __call__(self, url: str) -> dict:
        self.calls.append(url)
        status, ctype, body, final, redirects = _FIXTURE_PAGES.get(
            url, (None, "", None, url, 0)
        )
        return {
            "url": url,
            "final_url": final,
            "status": status,
            "bytes": len(body or b""),
            "content_type": ctype,
            "etag": "",
            "redirects": redirects,
            "truncated": False,
            "error": None
            if status == 200
            else (f"http {status}" if status else "unreachable"),
            "fetched_at": self.at,
            "body": body,
        }


BAD_WRITER_SNIPPETS = (
    "def collect(root):\n    (root / 'x.json').write_text('{}')\n",
    "def collect(root):\n    with open(root / 'x.json', 'w') as fh:\n        json.dump({}, fh)\n",
)


def _fixture_links() -> Tuple[List[Link], List[dict]]:
    merged: Dict[str, Link] = {}
    sources: List[dict] = []
    pairs = (
        ("x/2026-09-11T1200.json", X_FIXTURE, ROOTS[0]),
        ("feeds/2026-09-11T1200.json", FEEDS_FIXTURE, ROOTS[1]),
        ("github/2026-09-11T1200.json", GITHUB_FIXTURE, ROOTS[2]),
        ("usecases/2026-09-11T1200.json", USECASES_FIXTURE, ROOTS[4]),
    )
    for label, doc, root in pairs:
        found = harvest_doc(doc, root)
        fresh = 0
        for url, hint in found:
            prior = merged.get(url)
            if prior is None:
                merged[url] = Link(url, (label,), hint)
                fresh += 1
            elif label not in prior.origins:
                merged[url] = Link(
                    url, tuple(sorted(prior.origins + (label,))), prior.hint or hint
                )
        sources.append(
            {
                "id": root.label,
                "url": label,
                "kind": "local-artifact",
                "status": 200,
                "items": len(found),
                "rows": fresh,
                "error": None,
                "note": root.why,
            }
        )
    sources.append(
        {
            "id": "sources",
            "url": "sources/",
            "kind": "local-artifact",
            "status": None,
            "items": 0,
            "rows": 0,
            "error": "absent: no sources/<stamp>.json yet",
            "note": ROOTS[3].why,
        }
    )
    return sorted(merged.values(), key=lambda link: link.url), sources


def selftest() -> int:
    """Offline proofs on inline fixtures. Every leg names the failure it prevents, and the legs
    that could silently pass forever are paired with a known-bad that MUST fire."""
    legs: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    # ---- extraction -----------------------------------------------------------------------
    full = extract(HTML_FULL, "text/html; charset=utf-8")
    check(
        "title-whitespace-collapsed",
        full["title"] == "Raw title tag",
        repr(full["title"]),
    )
    check(
        "og-title-and-description",
        full["og_title"] == "Grok Bot ships unattended routines"
        and full["og_desc"] == "An always-on agent finally fired without a human.",
        f"{full['og_title']!r} / {full['og_desc']!r}",
    )
    check(
        "canonical-and-h1-and-published",
        full["canonical"] == "https://example.test/post"
        and full["h1"] == "Heading One"
        and full["published"] == "2026-09-09T14:00:00Z",
        f"{full['canonical']!r} {full['h1']!r} {full['published']!r}",
    )
    minimal = extract(HTML_MINIMAL, "text/html")
    check(
        "meta-description-fallback",
        minimal["meta_desc"] == "the meta description is the only summary here"
        and not minimal["og_desc"],
        repr(minimal["meta_desc"]),
    )
    check(
        "svg-title-does-not-hijack",
        minimal["title"] == "Only a title"
        and minimal["h1"] == "Real H1"
        and extract(HTML_SVG_TRAP, "text/html")["title"] == ""
        and extract(HTML_SVG_TRAP, "text/html")["h1"] == "Real H1",
        f"{minimal['title']!r} / trap={extract(HTML_SVG_TRAP, 'text/html')['title']!r}",
    )
    # Known-bad for the extractor: a page with no <title> must yield an EMPTY title, never the
    # body text. Without this leg, an extractor that fell back to `handle_data` would look fine
    # on every fixture above and ship page bodies as titles in production.
    none = extract(HTML_NO_TITLE, "text/html")
    check(
        "extractor-fires-on-known-bad",
        none["title"] == ""
        and none["og_title"] == ""
        and "plenty of body" not in none["title"],
        repr(none["title"]),
    )
    pdf = extract(PDF_BODY, "application/pdf")
    check(
        "non-html-extracts-nothing",
        pdf["parsed"] is False
        and not any(pdf[k] for k in ("title", "og_title", "meta_desc")),
        repr(pdf),
    )
    check(
        "htmlish-classifier",
        is_htmlish("text/html; charset=utf-8")
        and is_htmlish("application/xhtml+xml")
        and not is_htmlish("application/pdf")
        and not is_htmlish("image/png"),
        "content-type gate must admit html and refuse binaries",
    )
    # MEASURED on the real corpus, and the reason this leg is BEHAVIOURAL rather than a
    # `hasattr` check: the first fix added `http_error_308` alone, every structural assertion
    # went green, and the live page was STILL recorded 308 with zero bytes because
    # `redirect_request` refuses the status one layer deeper. A leg that only proves dispatch
    # exists would have certified a broken fix.
    missing = [
        code
        for code in (301, 302, 303, 307, 308)
        if not hasattr(_CountingRedirects, f"http_error_{code}")
    ]
    check(
        "every-redirect-status-dispatched",
        not missing,
        f"unhandled redirect statuses {missing} never reach a redirect handler",
    )
    followed = {}
    for code in (301, 302, 307, 308):
        handler = _CountingRedirects()
        try:
            new = handler.redirect_request(
                urllib.request.Request("https://old.test/page"),
                None,
                code,
                "Permanent Redirect",
                {},
                "https://new.test/page",
            )
            followed[code] = (
                getattr(new, "full_url", None) == "https://new.test/page"
                and handler.count == 1
            )
        except urllib.error.HTTPError as exc:
            followed[code] = f"raised {exc.code}"
    check(
        "redirects-are-actually-followed",
        all(v is True for v in followed.values()),
        f"{followed} — a counted hop that then raises reads like a followed redirect and is not",
    )

    # ---- transport policy: retry and the https->http fallback ------------------------------
    def scripted(answers: Dict[str, List[dict]]) -> Callable[[str, int, int], dict]:
        """A fake transport: pops the next scripted answer for a url, recording every call."""

        def send(url: str, timeout: int, max_bytes: int) -> dict:
            send.calls.append(url)  # type: ignore[attr-defined]
            queue = answers.get(url) or []
            base = {
                "url": url,
                "final_url": url,
                "status": None,
                "bytes": 0,
                "content_type": "",
                "etag": "",
                "redirects": 0,
                "truncated": False,
                "error": "unreachable",
                "fetched_at": "2026-09-11T12:00:00Z",
                "body": None,
            }
            return dict(base, **(queue.pop(0) if queue else {}))

        send.calls = []  # type: ignore[attr-defined]
        return send

    naps: List[float] = []
    flaky = scripted(
        {
            "https://slow.test/a": [
                {"status": 503, "error": "http 503"},
                {
                    "status": 200,
                    "error": None,
                    "content_type": "text/html",
                    "body": HTML_FULL,
                },
            ]
        }
    )
    got = fetch("https://slow.test/a", request=flaky, sleep=naps.append)
    check(
        "transient-status-retried-once",
        got["status"] == 200
        and got["attempts"] == 2
        and len(flaky.calls) == 2
        and naps == [RETRY_SLEEP],
        f"status={got['status']} attempts={got.get('attempts')} calls={len(flaky.calls)} naps={naps}",
    )
    stubborn = scripted(
        {
            "https://gone.test/a": [
                {"status": 500, "error": "http 500"},
                {"status": 500, "error": "http 500"},
                {"status": 500, "error": "http 500"},
            ]
        }
    )
    got = fetch("https://gone.test/a", request=stubborn, sleep=naps.append)
    check(
        "retry-is-bounded-to-one",
        got["status"] == 500 and got["attempts"] == 2 and len(stubborn.calls) == 2,
        f"{len(stubborn.calls)} calls, attempts={got.get('attempts')} — a retry loop must not "
        "become a retry storm (a third scripted 500 is available and must go unused)",
    )
    permanent = scripted(
        {"https://nope.test/a": [{"status": 404, "error": "http 404"}]}
    )
    fetch("https://nope.test/a", request=permanent, sleep=naps.append)
    check(
        "permanent-status-not-retried",
        len(permanent.calls) == 1,
        f"{len(permanent.calls)} calls — a 404 is an answer, not a transient failure",
    )
    tls = scripted(
        {
            "https://plainhttp.test/a": [
                {"error": "URLError: [SSL: WRONG_VERSION_NUMBER] wrong version number"}
            ],
            "http://plainhttp.test/a": [
                {
                    "status": 200,
                    "error": None,
                    "content_type": "text/html",
                    "body": HTML_FULL,
                }
            ],
        }
    )
    got = fetch("https://plainhttp.test/a", request=tls, sleep=naps.append)
    check(
        "tls-failure-falls-back-to-http",
        got["status"] == 200
        and got["url"] == "https://plainhttp.test/a"
        and got["scheme_fallback"] == "http"
        and "WRONG_VERSION_NUMBER" in (got["tls_error"] or "")
        and tls.calls == ["https://plainhttp.test/a", "http://plainhttp.test/a"],
        f"url={got.get('url')} fallback={got.get('scheme_fallback')} calls={tls.calls}",
    )
    check(
        "fallback-keeps-the-content-address",
        content_id(got["url"]) == content_id("https://plainhttp.test/a"),
        "recovering a page over http must not move its id, or the row splits in two",
    )
    dns = scripted(
        {
            "https://absent.test/a": [
                {
                    "error": "URLError: [Errno 8] nodename nor servname provided, or not known"
                }
            ]
        }
    )
    fetch("https://absent.test/a", request=dns, sleep=naps.append)
    check(
        "dns-failure-does-not-fall-back",
        dns.calls == ["https://absent.test/a"],
        f"{dns.calls} — a host that does not resolve has nothing to offer over http either",
    )
    check(
        "tls-classifier-separates-the-two",
        is_tls_failure("URLError: [SSL: WRONG_VERSION_NUMBER] x")
        and is_tls_failure("[SSL: CERTIFICATE_VERIFY_FAILED]")
        and is_tls_failure("TLSV1_ALERT_PROTOCOL_VERSION")
        and not is_tls_failure("URLError: [Errno 8] nodename nor servname provided")
        and not is_tls_failure("timed out")
        and not is_tls_failure(None),
        "the fallback trigger must not widen to every transport error",
    )

    # ---- canonicalisation + ids ------------------------------------------------------------
    a = canon_url("https://example.test/post?utm_source=x&utm_campaign=launch")
    b = canon_url("http://www.example.test/post/")
    check("canon-dedups-utm", a == b == "https://example.test/post", f"{a!r} vs {b!r}")
    check(
        "canon-keeps-real-query",
        canon_url("https://news.test/item?id=42&ref=hn")
        == "https://news.test/item?id=42",
        canon_url("https://news.test/item?id=42&ref=hn"),
    )
    check(
        "id-matches-spec",
        content_id("https://example.test/post")
        == hashlib.blake2b(b"https://example.test/post", digest_size=16).hexdigest()
        and len(content_id("https://example.test/post")) == 32,
        content_id("https://example.test/post"),
    )
    check(
        "id-agrees-with-siblings",
        content_id("https://github.com/awslabs/mcp")
        == content_id("http://www.github.com/awslabs/mcp/"),
        "a repo url spelled two ways must be one row across producers",
    )
    # Known-bad for the dedup leg: with an identity canonicaliser the utm pair produces TWO ids.
    # This proves `canon-dedups-utm` is load-bearing rather than trivially true.
    naive = {
        hashlib.blake2b(u.encode(), digest_size=16).hexdigest()
        for u in (
            "https://example.test/post?utm_source=x&utm_campaign=launch",
            "http://www.example.test/post/",
        )
    }
    check(
        "canon-detector-fires-on-known-bad",
        len(naive) == 2
        and len(
            {
                content_id(u)
                for u in (
                    "https://example.test/post?utm_source=x&utm_campaign=launch",
                    "http://www.example.test/post/",
                )
            }
        )
        == 1,
        f"{len(naive)} naive ids vs 1 canonical id",
    )

    # ---- classification ---------------------------------------------------------------------
    cases = (
        ("https://github.com/awslabs/mcp", "github-repo", "github"),
        ("https://docs.x.ai/docs/grok-bot", "xai-docs", "xai-docs-host"),
        ("https://x.ai/docs/overview", "xai-docs", "xai-docs-path"),
        ("https://x.ai/news/introducing-grok-bot", "blog", "xai-news"),
        ("https://docs.cursor.com/agent", "cursor-docs", "cursor-docs-host"),
        ("https://cursor.com/docs/bots", "cursor-docs", "cursor-docs-path"),
        ("https://cursor.com/changelog", "blog", "cursor-blog"),
        ("https://cursor.com/marketplace/thing", "marketplace", "cursor-marketplace"),
        ("https://npmjs.com/package/x", "marketplace", "registry"),
        ("https://youtube.com/watch", "video", "video"),
        ("https://x.com/bot/status/1", "social", "social"),
        ("https://someone.test/blog/a-post", "blog", "blog-path"),
        ("https://random.test/thing", FALLBACK_KIND, FALLBACK_RULE),
    )
    wrong = [(u, classify(u), (k, r)) for u, k, r in cases if classify(u) != (k, r)]
    check("classification-table", not wrong, f"{len(wrong)} wrong: {wrong[:3]}")
    check(
        "every-kind-reachable",
        {classify(u)[0] for u, _, _ in cases} == set(KINDS),
        str(sorted({classify(u)[0] for u, _, _ in cases} ^ set(KINDS))),
    )

    # ---- no-fetch rules -----------------------------------------------------------------------
    def reason_status(url: str) -> Optional[str]:
        found = no_fetch_for(url)
        return found.status if found else None

    check(
        "x-hosts-recorded-not-fetched",
        reason_status("https://x.com/bot/status/1") == "skipped-owned-by-gb-x"
        and reason_status("https://twitter.com/a/status/2") == "skipped-owned-by-gb-x"
        and reason_status("https://mobile.twitter.com/a") == "skipped-owned-by-gb-x",
        "x/twitter belongs to gb-x, whose row for the same url is richer and shares its id",
    )
    # This leg exists because the x.com entry SHIPPED with an inherited "plain clients get 403"
    # claim that `probe --refusals` falsified on its first run (3 of 3 post urls answered 200
    # with og tags). An entry whose stated reason is reachability must be labelled `refusal`;
    # one whose reason is that someone else's row is better must be labelled `ownership`. The
    # label is what `probe --refusals` reads to decide whether a 200 is good news or a
    # contradiction, so mislabelling is how a false claim survives a re-measurement.
    check(
        "no-fetch-reasons-are-labelled-honestly",
        {e.status: e.reason for e in NO_FETCH}
        == {
            "skipped-owned-by-gb-x": "ownership",
            "skipped-403-measured": "refusal",
        }
        and all(e.reason in ("ownership", "refusal") for e in NO_FETCH)
        and UNFETCHABLE.reason == "unfetchable",
        str({e.status: e.reason for e in NO_FETCH}),
    )
    check(
        "ownership-entry-makes-no-reachability-claim",
        not any(
            token in e.evidence.upper()
            for e in NO_FETCH
            if e.reason == "ownership"
            for token in ("403", "FORBIDDEN", "CANNOT BE READ", "UNREACHABLE")
        ),
        "an ownership entry that cites a status code is claiming something it did not measure",
    )
    check(
        "measured-refusal-host-recorded",
        reason_status("https://status.x.ai/api-us-east-1/INC1bc684df")
        == "skipped-403-measured",
        "115 of 115 measured 403s, and gb-feeds already holds these urls titled",
    )
    check(
        "non-http-recorded",
        reason_status("mailto:nobody@example.test") == "skipped-unfetchable-scheme"
        and reason_status("javascript:void(0)") == "skipped-unfetchable-scheme",
        "an unfetchable scheme is a recorded skip, not a crash",
    )
    check(
        "ordinary-host-not-recorded",
        reason_status("https://example.test/post") is None
        and reason_status("https://xcom.test/a") is None
        and reason_status("https://x.ai/news/a") is None
        # `reddit.com` CONTAINS the substring `t.co`. If host matching ever degrades from
        # "exact or dotted subdomain" to a substring test, reddit silently stops being fetched
        # and starts being handed to gb-x, which cannot read it either.
        and reason_status("https://reddit.com/r/x/comments/1") is None
        and reason_status("https://nott.com/a") is None,
        "the host rule must not swallow a lookalike host, a sibling subdomain, or a host that "
        "merely contains a listed one as a substring",
    )
    check(
        "every-no-fetch-entry-carries-its-evidence",
        all(
            e.hosts and e.status and e.owner and e.measured_at and e.evidence and e.why
            for e in NO_FETCH
        ),
        "a host on a skip list with no measurement behind it is folklore",
    )
    # The `/`-prefix over-match that cost 115 requests, pinned so it cannot come back.
    check(
        "homepage-prefix-does-not-match-every-path",
        path_matches("/", ("/",))
        and not path_matches("/api-us-east-1/inc1bc", ("/",))
        and path_matches("/news/thing", ("/news",))
        and not path_matches("/newsletter", ("/news",)),
        "a `/` prefix must mean the homepage, and `/news` must not match `/newsletter`",
    )
    check(
        "status-page-incident-is-not-a-blog",
        classify("https://status.x.ai/api-us-east-1/INC1bc684df")[0] == FALLBACK_KIND
        and classify("https://x.ai/news/introducing-grok-bot")[0] == "blog"
        and classify("https://x.ai/")[0] == "blog",
        str(
            [
                classify("https://status.x.ai/api-us-east-1/INC1bc684df"),
                classify("https://x.ai/news/introducing-grok-bot"),
                classify("https://x.ai/"),
            ]
        ),
    )

    # ---- harvest ------------------------------------------------------------------------------
    links, sources = _fixture_links()
    urls = [link.url for link in links]
    check(
        "harvest-reads-every-root",
        "https://example.test/post" in urls
        and "https://docs.x.ai/docs/grok-bot" in urls
        and "https://github.com/awslabs/mcp" in urls
        and "https://cursor.com/changelog" in urls,
        f"{len(urls)} urls: {urls[:4]}",
    )
    check(
        "harvest-records-absent-root",
        any(
            s["id"] == "sources"
            and s["status"] is None
            and "absent" in (s["error"] or "")
            for s in sources
        ),
        "a root a sibling has not shipped yet must be RECORDED, not silently missing",
    )
    merged_origins = {link.url: link.origins for link in links}
    check(
        "cross-artifact-dedup",
        len(merged_origins["https://example.test/post"]) == 2
        and urls.count("https://example.test/post") == 1,
        f"origins={merged_origins.get('https://example.test/post')}",
    )
    check(
        "github-source-fallback-harvested",
        "https://github.com/awslabs/mcp" in urls,
        "gb-github rows carry the url in `source`; missing it loses 450 urls",
    )
    check(
        "usecases-links-array-harvested",
        "https://example.test/spec.pdf" in urls and "https://example.test/old" in urls,
        "the curated links[] array is 814 urls that are not rows",
    )

    # ---- build: first (cold) run ----------------------------------------------------------------
    when = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.timezone.utc)
    f1 = FixtureFetcher()
    doc1, cache1, c1 = build_document(
        links=links,
        sources=sources,
        fetcher=f1,
        cache=empty_cache(),
        now=when,
        limit=50,
        max_age_days=7.0,
    )
    by_url = {r["url"]: r for r in doc1["rows"]}
    check(
        "cold-run-fetches",
        c1["fetched"] == len(f1.calls) > 0 and c1["cache_hits"] == 0,
        f"fetched={c1['fetched']} calls={len(f1.calls)} hits={c1['cache_hits']}",
    )
    x_group = next(
        (
            g
            for g in doc1["recorded"]["groups"]
            if g["status"] == "skipped-owned-by-gb-x"
        ),
        None,
    )
    check(
        "x-urls-never-requested",
        not any(host_matches(host_of(u), X_HOSTS) for u in f1.calls)
        and x_group is not None
        and x_group["count"] >= 2
        and x_group["owner"] == "gb-x",
        f"group={x_group and x_group['count']} "
        f"calls touching x={[u for u in f1.calls if host_matches(host_of(u), X_HOSTS)]}",
    )
    check(
        "x-urls-not-rows",
        not any(host_matches(host_of(r["url"]), X_HOSTS) for r in doc1["rows"]),
        "a stub x row would collide with gb-x's real row and churn the store daily",
    )
    check(
        "recorded-count-agrees-with-groups",
        doc1["counts"]["recorded_not_fetched"]
        == doc1["recorded"]["count"]
        == sum(g["count"] for g in doc1["recorded"]["groups"]),
        f"{doc1['counts']['recorded_not_fetched']} vs {doc1['recorded']['count']}",
    )
    check(
        "row-title-prefers-og",
        by_url["https://example.test/post"]["title"]
        == "Grok Bot ships unattended routines",
        by_url["https://example.test/post"]["title"],
    )
    check(
        "row-records-classification-rule",
        by_url["https://github.com/awslabs/mcp"]["signals"]["classified_by"] == "github"
        and by_url["https://github.com/awslabs/mcp"]["kind"] == "github-repo",
        str(by_url["https://github.com/awslabs/mcp"]["signals"]["classified_by"]),
    )
    redirected = by_url["https://example.test/old"]
    check(
        "redirect-chain-recorded",
        redirected["signals"]["redirects"] == 2
        and redirected["signals"]["final_url"] == "https://example.test/new"
        and redirected["id"] == content_id("https://example.test/old"),
        f"redirects={redirected['signals']['redirects']} final={redirected['signals'].get('final_url')}",
    )
    pdf_row = by_url["https://example.test/spec.pdf"]
    check(
        "non-html-row-marked",
        pdf_row["signals"]["extraction"] == "skipped-non-html"
        and pdf_row["summary"] == ""
        and pdf_row["signals"]["content_type"] == "application/pdf",
        str(pdf_row["signals"]),
    )
    gone = by_url["https://example.test/gone"]
    check(
        "404-recorded-not-dropped",
        gone["signals"]["status"] == 404 and bool(gone["signals"].get("error")),
        str(gone["signals"]),
    )
    check(
        "status-distribution-counted",
        doc1["counts"]["by_status"].get("200", 0) >= 4
        and doc1["counts"]["by_status"].get("404") == 1,
        str(doc1["counts"]["by_status"]),
    )
    scheme_group = next(
        (
            g
            for g in doc1["recorded"]["groups"]
            if g["status"] == "skipped-unfetchable-scheme"
        ),
        None,
    )
    # The old form of this leg was `in the group OR not in the rows`, which a broken
    # implementation satisfies by dropping the url entirely. Both halves are now required.
    check(
        "unfetchable-scheme-recorded-and-never-requested",
        scheme_group is not None
        and "mailto:nobody@example.test" in scheme_group["urls"]
        and not any(r["url"].startswith("mailto:") for r in doc1["rows"])
        and not any(u.startswith("mailto:") for u in f1.calls),
        f"group={scheme_group and scheme_group['urls']}",
    )

    # ---- build: second (warm) run ------------------------------------------------------------
    f2 = FixtureFetcher()
    doc2, cache2, c2 = build_document(
        links=links,
        sources=sources,
        fetcher=f2,
        cache=cache1,
        now=when + dt.timedelta(hours=6),
        limit=50,
        max_age_days=7.0,
    )
    check(
        "warm-run-is-all-cache",
        c2["cache_hits"] == c1["fetched"]
        and c2["network_requests"] == 0
        and not f2.calls,
        f"hits={c2['cache_hits']} requests={c2['network_requests']} calls={len(f2.calls)}",
    )
    check(
        "cache-preserves-rows",
        {r["id"] for r in doc2["rows"]} == {r["id"] for r in doc1["rows"]}
        and {r["title"] for r in doc2["rows"]} == {r["title"] for r in doc1["rows"]},
        "a cached row must be the same row, or the cache is a data-loss mechanism",
    )
    check(
        "cache-marks-provenance",
        all(
            r["signals"]["fetch"] == "cache"
            for r in doc2["rows"]
            if r["signals"]["status"] is not None
        ),
        "a row served from cache must say so",
    )

    # ---- build: expiry ------------------------------------------------------------------------
    f3 = FixtureFetcher()
    _, _, c3 = build_document(
        links=links,
        sources=sources,
        fetcher=f3,
        cache=cache1,
        now=when + dt.timedelta(days=9),
        limit=50,
        max_age_days=7.0,
    )
    check(
        "stale-cache-refetches",
        c3["network_requests"] == c1["fetched"] and c3["cache_hits"] == 0,
        f"requests={c3['network_requests']} hits={c3['cache_hits']}",
    )
    # `.get` not `[...]`: a mutant that stops populating the cache must report FAIL from this
    # leg, not die with a KeyError three legs later. A selftest that crashes instead of failing
    # tells you something broke but not what, which is the whole thing these legs exist to avoid.
    failed_entry = (cache1.get("entries") or {}).get("https://example.test/gone")
    ok_entry = (cache1.get("entries") or {}).get("https://example.test/post")
    check(
        "failures-expire-sooner",
        bool(failed_entry)
        and bool(ok_entry)
        and cache_fresh(failed_entry, when + dt.timedelta(days=3), 7.0) is False
        and cache_fresh(ok_entry, when + dt.timedelta(days=3), 7.0) is True,
        "a 404 is retried inside a day; a 200 body is not re-read for a week"
        if failed_entry and ok_entry
        else "the cold run did not populate the cache at all",
    )

    # ---- build: budget ------------------------------------------------------------------------
    f4 = FixtureFetcher()
    doc4, _, c4 = build_document(
        links=links,
        sources=sources,
        fetcher=f4,
        cache=empty_cache(),
        now=when,
        limit=2,
        max_age_days=7.0,
    )
    deferred = [r for r in doc4["rows"] if r["signals"]["fetch"] == "deferred"]
    check(
        "budget-is-respected",
        c4["network_requests"] == 2
        and len(f4.calls) == 2
        and c4["deferred"] == len(deferred) > 0,
        f"requests={c4['network_requests']} deferred={c4['deferred']}",
    )
    check(
        "deferred-rows-still-classified",
        all(r["kind"] in KINDS and r["signals"]["classified_by"] for r in deferred)
        and all(r["id"] == content_id(r["url"]) for r in deferred),
        "a url we have not reached yet is still a known url with a stable id",
    )
    check(
        "deferred-then-fetched-keeps-id",
        {r["id"] for r in doc4["rows"]} == {r["id"] for r in doc1["rows"]},
        "filling in a deferred row must update it, not create a second one",
    )
    # Row order must not depend on harvest order. MEASURED SUBTLETY: a COLD run cannot prove
    # this any more. Once `plan` started ranking the queue by information gain, a cold run's
    # rows are `sorted(unseen, key=fetch_rank)` and are already order-independent — so a mutant
    # that deletes `rows.sort` in `build_document` passed this leg for the wrong reason. Only a
    # WARM run exposes it: cache hits are emitted in the order `plan` walked `links`, so without
    # the final sort the artifact's row order follows whatever order the harvester produced and
    # the daily diff reports pure reordering as change. Both runs below are warm.
    f5 = FixtureFetcher()
    doc5, _, c5 = build_document(
        links=list(reversed(links)),
        sources=sources,
        fetcher=f5,
        cache=cache1,
        now=when + dt.timedelta(hours=6),
        limit=50,
        max_age_days=7.0,
    )
    check(
        "row-order-independent-of-harvest-order",
        c5["cache_hits"] > 0
        and [r["id"] for r in doc5["rows"]] == [r["id"] for r in doc2["rows"]],
        f"hits={c5['cache_hits']} — reversing the harvested corpus must not reorder a row, and "
        "a leg with zero cache hits cannot detect it",
    )

    # The fetch budget is the scarce resource in this producer, so WHICH urls it buys is a
    # product decision and gets pinned like one.
    mixed = [
        Link(u, ("t/1.json",), "")
        for u in (
            "https://github.com/a/b",
            "https://random.test/thing",
            "https://docs.x.ai/docs/x",
            "https://youtube.com/watch",
            "https://reddit.com/r/x/comments/1",
            "https://someone.test/blog/p",
        )
    ]
    _, ranked, _, _ = plan(mixed, empty_cache(), now=when, max_age_days=7.0)
    order = [classify(link.url)[0] for link in ranked]
    alphabetical = [classify(u)[0] for u in sorted(link.url for link in mixed)]
    check(
        "queue-ranks-by-information-gain",
        order == ["other", "blog", "video", "xai-docs", "social", "github-repo"],
        f"{order} — the long tail first, github (already described by gb-github) last",
    )
    # Load-bearing check on the leg above: if the ranked order happened to equal the plain
    # alphabetical order, it would prove nothing about the ranking.
    check(
        "ranking-changes-the-order-at-all",
        order != alphabetical,
        f"ranked={order} alphabetical={alphabetical}",
    )
    check(
        "priority-table-covers-every-kind",
        set(FETCH_PRIORITY) == set(KINDS),
        f"uncovered kinds fall to 9 silently: {sorted(set(KINDS) ^ set(FETCH_PRIORITY))}",
    )

    # ---- per-host politeness -------------------------------------------------------------------
    clock = {"t": 0.0}
    slept: List[float] = []

    def fake_clock() -> float:
        return clock["t"]

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    polite = PoliteFetcher(clock=fake_clock, sleep=fake_sleep, inner=FixtureFetcher())
    polite("https://a.test/1")
    polite("https://b.test/1")
    polite("https://a.test/2")
    check(
        "per-host-spacing",
        len(slept) == 1 and abs(slept[0] - PER_HOST_SLEEP) < 1e-6,
        f"slept={slept} — the second host must not wait, the repeat host must",
    )
    clock["t"] += 5.0
    slept.clear()
    polite("https://a.test/3")
    check(
        "no-sleep-when-gap-elapsed",
        not slept,
        f"slept={slept} — a host untouched for 5s must not be delayed again",
    )

    # ---- structural: writes ---------------------------------------------------------------------
    src = pathlib.Path(__file__).resolve().read_text()
    writers = writer_functions(src)
    check(
        "one-writer-only",
        writers == {"write_outputs"},
        f"writers={sorted(writers)} — exactly one function may touch the disk",
    )
    check(
        "build-document-takes-no-root",
        takes_no_root(src, "build_document") and takes_no_root(src, "collect"),
        "a builder handed a root is one edit away from writing to it",
    )
    bad_hits = [sorted(writer_functions(s)) for s in BAD_WRITER_SNIPPETS]
    check(
        "writer-detector-fires-on-known-bad",
        all(h == ["collect"] for h in bad_hits),
        f"{bad_hits} — the detector must see a raw write_text AND an open(...,'w')+json.dump",
    )
    check(
        "root-detector-fires-on-known-bad",
        takes_no_root(
            "def build_document(*, root, links):\n    return root\n", "build_document"
        )
        is False,
        "the no-root check must actually reject a root parameter",
    )

    # ---- structural: output guards ---------------------------------------------------------
    # All three of these were REAL, MEASURED defects in this file, each found only by running
    # the command rather than reading it. `gbtypes.main`'s EPIPE arm exits 0 unconditionally,
    # which is correct for a producer whose exit code carries no verdict and wrong for this one
    # (2 = broken harvest / silent run, 1 = failing selftest). Any unguarded write is therefore
    # a verdict-erasure bug waiting for someone to type `| head`.
    check(
        "every-write-goes-through-a-guard",
        not unguarded_prints(src),
        f"bare print() in {sorted(unguarded_prints(src))} — use emit/emit_err",
    )
    check(
        "print-detector-fires-on-known-bad",
        unguarded_prints(
            "def emit(t):\n    print(t)\n\n\ndef body():\n    print('hi')\n    return 2\n"
        )
        == {"body"},
        "the detector must see a bare print in body and exempt emit itself",
    )
    check(
        "argparse-writes-are-guarded",
        unguarded_parse_args(src) == 0,
        f"{unguarded_parse_args(src)} unguarded parse_args — argparse writes usage on our "
        "behalf and carries exit 2 with it",
    )
    check(
        "parse-args-detector-fires-on-known-bad",
        unguarded_parse_args("def body():\n    args = ap.parse_args()\n    return 0\n")
        == 1
        and unguarded_parse_args(
            "def body():\n    try:\n        args = ap.parse_args()\n"
            "    except BrokenPipeError:\n        return 2\n"
        )
        == 0
        # A try that handles something ELSE must NOT count as guarded.
        and unguarded_parse_args(
            "def body():\n    try:\n        args = ap.parse_args()\n"
            "    except ValueError:\n        return 2\n"
        )
        == 1,
        "the guard check must key on BrokenPipeError, not on the presence of any try",
    )

    # ---- structural: volatility -------------------------------------------------------------------
    volatile = set(doc1["volatile_fields"])
    leaked = sorted(
        k
        for r in doc1["rows"]
        for k in r
        if k
        not in (
            "id",
            "source",
            "url",
            "title",
            "kind",
            "published_at",
            "summary",
            "signals",
        )
    )
    check(
        "rows-have-no-extra-top-level-fields",
        not leaked,
        f"unexpected row keys: {leaked}",
    )
    check(
        "clock-fields-declared-volatile",
        "signals.fetched_at" in volatile and "signals.fetch" in volatile,
        f"volatile_fields={sorted(volatile)}",
    )

    # The real proof: strip the declared volatile fields and the SAME corpus must hash identically
    # across two runs at different wall-clock times. Without it the store allocates a blob a day.
    def strip(doc: dict) -> str:
        out = []
        for r in sorted(doc["rows"], key=lambda x: x["id"]):
            s = {
                k: v
                for k, v in r["signals"].items()
                if k not in ("fetched_at", "fetch")
            }
            out.append({**{k: v for k, v in r.items() if k != "signals"}, "signals": s})
        return json.dumps(out, sort_keys=True)

    check(
        "stripped-rows-are-stable-across-runs",
        strip(doc1) == strip(doc2),
        "two runs of the same corpus must hash identically once volatile fields are stripped",
    )
    check(
        "volatility-check-fires-on-known-bad",
        json.dumps(
            [
                {k: v for k, v in r.items()}
                for r in sorted(doc1["rows"], key=lambda x: x["id"])
            ],
            sort_keys=True,
        )
        != json.dumps(
            [
                {k: v for k, v in r.items()}
                for r in sorted(doc2["rows"], key=lambda x: x["id"])
            ],
            sort_keys=True,
        ),
        "without stripping, the two runs MUST differ — otherwise the leg above proves nothing",
    )
    check(
        "counters-are-measured-not-asserted",
        doc1["counts"]["rows"] == len(doc1["rows"])
        and doc1["counts"]["distinct_urls"] == len(links)
        and sum(doc1["counts"]["by_kind"].values()) == len(doc1["rows"]),
        str(doc1["counts"]),
    )
    check(
        "render-does-not-crash",
        bool(render(doc1)) and "links 2026-09-11" in render(doc1),
        render(doc1).splitlines()[0] if doc1 else "",
    )

    passed = sum(1 for _, ok, _ in legs if ok)
    for name, ok, detail in legs:
        if not ok:
            emit_err(f"FAIL {name} {detail}")
    emit(f"SELFTEST {'PASS' if passed == len(legs) else 'FAIL'} - {passed}/{len(legs)}")
    return 0 if passed == len(legs) else 1


# ------------------------------------------------------------------------------------------- main


def collect(
    *,
    links: List[Link],
    sources: List[dict],
    cache: dict,
    cache_error: Optional[str],
    limit: int,
    max_age_days: float,
) -> Tuple[dict, dict, dict]:
    """The live pipeline: a polite fetcher over the harvested corpus. Takes no root, writes
    nothing — `write_outputs` is the only thing in this module that touches the disk."""
    return build_document(
        links=links,
        sources=sources,
        fetcher=PoliteFetcher(),
        cache=cache,
        limit=limit,
        max_age_days=max_age_days,
        cache_error=cache_error,
    )


def body(argv: Optional[List[str]] = None) -> int:
    repo = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(
        prog="gb-links.py",
        description="Follow every url the other producers found. Writes links/<stamp>.json.",
    )
    ap.add_argument(
        "command",
        nargs="?",
        default="collect",
        choices=("collect", "probe"),
        help="collect: fetch, extract, write. probe: show the plan, no network, no writes.",
    )
    ap.add_argument(
        "--root", default=str(repo), help="artifact root (default: repo root)"
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"max network requests this run (default {DEFAULT_LIMIT})",
    )
    ap.add_argument(
        "--max-age",
        type=float,
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"serve from cache if fetched within N days (default {DEFAULT_MAX_AGE_DAYS})",
    )
    ap.add_argument(
        "--refusals",
        action="store_true",
        help="probe only: re-measure one url per recorded-not-fetched host, to take a host off "
        "the NO_FETCH list on evidence",
    )
    ap.add_argument(
        "--json", action="store_true", help="machine-readable summary on stdout"
    )
    ap.add_argument(
        "--selftest", action="store_true", help="offline proofs; no network"
    )
    try:
        args = ap.parse_args(argv)
    except BrokenPipeError:
        # MEASURED: `gb-links.py --bogus 2>&1 | <closed pipe>` exited **0** while the same
        # command unpiped exits 2. argparse writes usage on OUR behalf and only THEN calls
        # `sys.exit(2)`; when that write hits a dead pipe the library never reaches its
        # SystemExit, so the EPIPE escapes to the spine's unconditional `exit(0)` and a typo'd
        # invocation reports success. "I grepped for my own prints" is not an audit of what a
        # process writes — argparse, print_help and any formatting library are writers too, and
        # they carry verdicts. The code is recovered from argv because argparse never got to
        # raise it: `-h`/`--help` is a success, anything argparse rejects is 2.
        _silence(sys.stdout)
        _silence(sys.stderr)
        wanted = argv if argv is not None else sys.argv[1:]
        return 0 if any(a in ("-h", "--help") for a in wanted) else 2

    if args.selftest:
        return selftest()

    root_dir = pathlib.Path(args.root).expanduser().resolve()
    links, sources = harvest(root_dir)
    cache, cache_error = load_cache(root_dir)
    if cache_error:
        emit_err(f"WARN cache unusable ({cache_error}) — this run re-fetches")
    if not links:
        emit_err(
            "ERROR harvested zero urls from every producer root — that is a broken harvester, "
            "not an empty ecosystem; verify with `--selftest` (leg harvest-reads-every-root)"
        )
        return 2

    if args.command == "probe":
        when = dt.datetime.now(dt.timezone.utc)
        recorded, queue, hits, stale = plan(
            links, cache, now=when, max_age_days=args.max_age
        )
        kinds = collections.Counter(classify(link.url)[0] for link in links)
        domains = collections.Counter(host_of(link.url) for link in links)
        emit(
            f"probe: {len(links)} distinct urls — {len(queue)} to fetch "
            f"({len(queue) - len(stale)} never fetched, {len(stale)} stale), "
            f"{len(hits)} cached, "
            f"{sum(len(g) for _, g in recorded)} recorded-not-fetched"
        )
        emit("  kinds   " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
        emit("  domains " + ", ".join(f"{k}={v}" for k, v in domains.most_common(12)))
        for source in sources:
            flag = "ok " if source.get("status") == 200 else "MISS"
            emit(
                f"  {flag} {source['id']:<9} {source.get('items', 0):>5} urls  {source['url']}"
                + (f"  [{source['error']}]" if source.get("error") else "")
            )
        for reason, group in recorded:
            emit(
                f"  rec  {reason.status:<24} {len(group):>5} urls  -> {reason.owner}"
                f"   [{reason.evidence}]"
            )
        if args.refusals:
            # Re-measure one url per recorded host so an entry lives or dies on evidence rather
            # than inertia. Mirrors `gb-feeds probe --retired`. The verdict reads `reason`
            # because a 200 means opposite things for the two kinds: it FALSIFIES a refusal and
            # it CONFIRMS an ownership entry. Collapsing them is how the x.com entry carried an
            # inherited 403 claim for one run before this flag retired it.
            emit("re-measuring recorded hosts (not requested by `collect`):")
            prober = PoliteFetcher()
            for reason, group in recorded:
                if reason.reason == "unfetchable" or not group:
                    continue
                sample = group[0]
                got = prober(sample.url)
                got.pop("body", None)
                answered = got["status"] == 200
                if reason.reason == "refusal":
                    verdict = (
                        "FALSIFIED — it answers now; re-measure the host and remove this entry"
                        if answered
                        else f"still refused: {got['error'] or got['status']}"
                    )
                else:
                    verdict = (
                        f"reachable as expected; still owned by {reason.owner}, whose row is "
                        "richer and shares this url's id, so still not fetched"
                        if answered
                        else f"unreachable too ({got['error'] or got['status']}); the ownership "
                        "argument is unaffected either way"
                    )
                emit(
                    f"  {str(got['status'] or '---'):>4}  {reason.reason:<9} measured "
                    f"{reason.measured_at}  {sample.url}  -> {verdict}"
                )
        return 0

    doc, updated_cache, counters = collect(
        links=links,
        sources=sources,
        cache=cache,
        cache_error=cache_error,
        limit=args.limit,
        max_age_days=args.max_age,
    )
    out = write_outputs(root_dir, doc, updated_cache)

    if args.json:
        emit(json.dumps({k: v for k, v in doc.items() if k != "rows"}, indent=1))
    else:
        emit(render(doc))
        emit(str(out))

    if counters["fetched"] == 0 and counters["cache_hits"] == 0:
        emit_err(
            "ERROR the corpus produced neither a fetch nor a cache hit — a budget of "
            f"{args.limit} against {counters['distinct_urls']} urls should never be silent"
        )
        return 2
    return 0


if __name__ == "__main__":
    main(body)
