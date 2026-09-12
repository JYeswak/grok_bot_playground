#!/usr/bin/env python3
"""gb-sources — mine the VENDOR surfaces that move daily, and measure how fast they move.

WHY THIS EXISTS (measured 2026-09-11, do not re-derive)

  This repo polled the vendor's documentation WEEKLY. `https://docs.x.ai/sitemap.xml` carries
  183 `<loc>` entries, every one of them with a `<lastmod>`, and on 2026-09-11 **183 of 183 had
  a lastmod inside the last 7 days** (the 2026-09-08T20:17:43Z build cluster). A surface with
  100% seven-day churn read once a week is a surface we see one frame of. The sitemap is also
  the cheapest velocity instrument in the tree: one 32 KB request answers "how fast is the
  vendor moving" without fetching a single page.

  The second official tree is Cursor's. `https://cursor.com/changelog` (200, ~203 KB) and
  `https://cursor.com/blog` (200, ~422 KB) carry dated entries with permalinks and are the only
  place some Grok Bot behaviour is announced at all.

  `https://x.ai/news` answers **403 to a plain client** (5 503 bytes of bot-protection page,
  measured again today). That is a RECORDED FACT with a status code, never an empty list. See
  AGENTS.md §5 tier 2 and NEGATIVE_EVIDENCE.md NE-1: a 403 that renders as "no news" is the
  exact failure this repo exists to prevent.

WHAT IT PRODUCES

  `sources/<YYYY-MM-DDTHHMM>.json` — the shared daily-mining contract:

      schema, captured_at, velocity{...}, sources[{url,status,bytes,fetched_at,...}],
      rows[{id,source,url,title,kind,published_at,summary,signals{...}}]

  `rows[].id` is `blake2b-16(canonical url)` — a CONTENT id, not a run id. Two consecutive days
  that see the same page produce the same id, which is what lets a content-addressed store
  (`bin/gb-store.py`) fold 183 rows/day into the handful that are actually new. The id is proven
  time-independent by selftest t3: the same fixture parsed with two different `--now` values
  yields identical id sets.

  Nothing volatile lives in a row. Every fetch timestamp, byte count and status code lives in
  `sources[]`, which is per-run by construction.

  ONE DELIBERATE EXCEPTION, named here because it costs something. `rows[].signals.age_days` is
  derived from `signals.lastmod` and the snapshot's `captured_at`, so it MOVES EVERY DAY while
  the page itself is unchanged. `bin/gb-store.py` keys a blob on the BLAKE2b of the whole row,
  so a store that ingests these rows verbatim would allocate 183 new blobs a day to learn
  nothing. `age_days` is kept because the operator reads these artifacts by eye and "12 hours
  old" is the whole point — and `stable_row()` below is the mechanical answer: it strips the
  paths the artifact itself declares in `volatile_fields`, so a store can hash
  `stable_row(row, volatile_fields_of(doc))` (or just call `document_fingerprints(doc)`) and get
  real dedup without the producer having to choose between a readable artifact and a cheap one.

WHAT IT DOES NOT DO

  It does not fetch the 183 doc pages. `https://docs.x.ai/llms.txt` documents that appending
  `.md` to any docs URL returns markdown (and `llms-full.txt` returns the whole tree), so each
  row carries its `signals.markdown_url` and a body fetcher can follow up on the rows whose
  `lastmod` actually moved. Hashing page bodies is `bin/gb-surface-snapshot.py`'s job; this
  producer answers WHICH pages are worth hashing today. Do not "improve" it into a crawler: the
  politeness budget here is one request per host per second, and 183 fetches is a different tool
  with a different cadence.

  It does not call a shrunken sitemap normal. Below `--min-locs` (default 100) the run reports
  ERROR and exits 3, because a docs index that suddenly lists 4 pages is an instrument failure
  and recording it as a measurement is how a loop goes quietly blind. Selftest t8 fires that
  leg on a known-bad one-entry sitemap.

IF YOU IMPORT THIS (a store or a differ calling `document_fingerprints`)

  Register the module in `sys.modules` BEFORE `exec_module`. python3 here is 3.9.6, and the
  house file-spec idiom (`spec_from_file_location` -> `module_from_spec` -> `exec_module`, as in
  `bin/gb-market-snapshot.py`) crashes when THE MODULE BEING EXEC'D UNREGISTERED defines a
  dataclass: `AttributeError: 'NoneType' object has no attribute '__dict__'` raised inside
  `dataclasses._is_type`, because `cls.__module__` is not in `sys.modules` yet. Scope matters:
  a NORMAL `from gbtypes import ...` inside the exec'd module is fine \u2014 gbtypes' own dataclasses
  are built under a properly registered `gbtypes` \u2014 so only this file's own 3 `@dataclass`
  classes (counted over the AST, not grepped) trigger it, and `bin/gb-pull-inventory.py`,
  `bin/gb-github.py` and `bin/gb-feeds.py` declare none and import either way. The fix is one
  line:

      spec = importlib.util.spec_from_file_location("gb_sources", "bin/gb-sources.py")
      mod = importlib.util.module_from_spec(spec)
      sys.modules["gb_sources"] = mod          # <- required before exec_module on 3.9
      spec.loader.exec_module(mod)

  Raised by GitHubEcosystemMiner 2026-09-11; both legs reproduced here (without the line:
  AttributeError from dataclasses; with it: imports clean).

USAGE

  gb-sources.py --selftest                 # 13 proofs on inline fixtures; no network
  gb-sources.py collect [--json]           # fetch all four surfaces, write sources/<stamp>.json
  gb-sources.py collect --dry-run          # fetch and report, write nothing
  gb-sources.py velocity --json            # just the velocity object, for the tick to consume
  gb-sources.py velocity --fetch           # recompute from a live sitemap instead of the artifact

EXIT CODES

  0 measured · 1 findings (a non-primary source refused) · 2 usage · 3 unmeasured/ERROR
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import datetime as dt
import hashlib
import html.parser
import json
import os
import pathlib
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import Cancelled, atomic_write_json, main, read_json_capped  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-vendor-sources/1"
ARTIFACT_ROOT = "sources"
SUMMARY = "mine the vendor doc/changelog surfaces and measure how fast they move"

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE, EXIT_ERROR = 0, 1, 2, 3

# Not `gb-sources/1`: that string is already taken by the repo's GitHub watch LIST
# (`sources.json`, read by g17 and bin/gb-discover.py). Two documents with one schema name is a
# reader that has to guess, so the dated artifacts carry their own name.
SITEMAP_URL = "https://docs.x.ai/sitemap.xml"
CURSOR_CHANGELOG = "https://cursor.com/changelog"
CURSOR_BLOG = "https://cursor.com/blog"
XAI_NEWS = "https://x.ai/news"

UA = "grokbot-sources/1 (+local daily vendor-surface refresh; contact: Joshua Nowak)"
TIMEOUT_S = 20.0
# Politeness, per host. cursor.com is hit twice in one run; nothing else repeats.
HOST_DELAY_S = 1.0
MAX_FETCH_BYTES = 8_000_000
# The canary floor. Measured 183 today; 100 is "the index lost nearly half of itself", which is
# an instrument failure and not a quiet week.
MIN_SITEMAP_LOCS = 100

TITLE_CHARS = 200
SUMMARY_CHARS = 400

# Derived-at-read fields, as DOTTED PATHS into a row. `stable_row` strips these so a
# content-addressed store can dedup a row that is unchanged at the vendor but has aged one day
# on our clock. Every artifact declares its own list in the top-level `volatile_fields`, and
# `stable_row` REQUIRES the list as an argument: a helper with a baked-in field list silently
# does nothing to a sibling producer's rows and reports a clean fingerprint over an unstripped
# body, which is a false green and worse than no helper at all (raised by GitHubEcosystemMiner
# 2026-09-11; his `bin/gb-github.py` happens to declare this same path, so the hazard is the
# BAKED-IN DEFAULT, not a disagreement about spelling).
VOLATILE_FIELDS: Tuple[str, ...] = ("signals.age_days",)

VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
INTEREST_TAGS = frozenset({"h1", "h2", "h3", "h4", "p", "time"})

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split()  # noqa: E501
    )
}


# ---------------------------------------------------------------------------------------------
# Fetching — every outcome is a recorded status, including the refusals
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Fetched:
    """One HTTP attempt. `status` 0 means the request never got an answer (DNS, TLS, timeout);
    any other value is what the server actually said, which includes 403 and 404. A caller may
    never infer emptiness from this object without reading `status` first."""

    url: str
    status: int
    body: bytes
    error: str
    fetched_at: str
    elapsed_ms: int

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def to_json(self, kind: str, rows: int, note: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "url": self.url,
            "kind": kind,
            "status": self.status,
            "bytes": len(self.body),
            "fetched_at": self.fetched_at,
            "elapsed_ms": self.elapsed_ms,
            "rows": rows,
            "ok": self.status == 200,
        }
        if self.error:
            out["error"] = self.error
        if note:
            out["note"] = note
        return out


Fetcher = Callable[[str], Fetched]

_last_hit: Dict[str, float] = {}


def _throttle(url: str) -> None:
    host = urllib.parse.urlsplit(url).netloc
    prev = _last_hit.get(host)
    now = time.monotonic()
    if prev is not None and now - prev < HOST_DELAY_S:
        time.sleep(HOST_DELAY_S - (now - prev))
    _last_hit[host] = time.monotonic()


def http_fetch(url: str) -> Fetched:
    """Fetch, and never raise — EXCEPT for cancellation, which must pass straight through.

    `Accept: */*` is deliberate and load-bearing: `Accept: text/markdown` makes docs.x.ai return
    a 411-byte 404 for `.md` pages (measured 2026-09-10, AGENTS.md §0 rule 2). Do not narrow it.

    `except Cancelled: raise` is not defensive noise, it is a bug fix. `gbtypes.Cancelled`
    derives from `Exception`, so the blanket handler below used to SWALLOW a SIGINT/SIGTERM that
    landed inside a fetch: the run recorded four "cancelled by signal 2" fetch failures, wrote a
    full artifact of refusals, and exited 1. Measured 2026-09-11 (rc=1, artifact on disk) after
    GitHubEcosystemMiner flagged the signal half of the spine contract — a scheduled daily job
    would have read that as findings rather than as a cancellation, and the spine's "no partial
    artifact written" guarantee would have been defeated by this handler. Selftest t11 pins it.
    """
    _throttle(url)
    started = time.monotonic()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    stamp = iso_z(utcnow())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read(MAX_FETCH_BYTES)
            status = int(getattr(resp, "status", 0) or 200)
            err = ""
    except Cancelled:
        raise
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            body = exc.read(MAX_FETCH_BYTES)
        except Cancelled:
            raise
        except Exception:  # noqa: BLE001 — the refusal body is a nicety, the code is the fact
            body = b""
        err = f"HTTPError {exc.code} {exc.reason}"
    except Exception as exc:  # noqa: BLE001 — urllib raises ~8 unrelated types
        status, body, err = 0, b"", f"{type(exc).__name__}: {exc}"
    return Fetched(
        url=url,
        status=status,
        body=body,
        error=err,
        fetched_at=stamp,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


# ---------------------------------------------------------------------------------------------
# Time, ids, text
# ---------------------------------------------------------------------------------------------
def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_z(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp_of(when: dt.datetime) -> str:
    """The dated-artifact filename convention every producer in this repo uses."""
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H%M")


def parse_human_date(raw: str) -> Optional[dt.datetime]:
    """`Sep 10, 2026` / `September 10 2026`. Cursor renders the date as text as well as in the
    `dateTime` attribute; when a redesign drops the attribute the text is the fallback, and a
    fallback nobody tested is a fallback that does not exist (selftest t9)."""
    parts = raw.replace(",", " ").split()
    month = day = year = None
    for tok in parts:
        key = tok.lower()[:3]
        if key in MONTHS and month is None:
            month = MONTHS[key]
        elif tok.isdigit() and len(tok) == 4 and year is None:
            year = int(tok)
        elif tok.isdigit() and len(tok) <= 2 and day is None:
            day = int(tok)
    if month is None or day is None or year is None:
        return None
    try:
        return dt.datetime(year, month, day, tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def parse_ts(raw: Optional[str]) -> Optional[dt.datetime]:
    """ISO-8601 (with or without `Z`, with or without milliseconds) or a human month name."""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = dt.datetime.fromisoformat(iso)
    except ValueError:
        parsed = parse_human_date(text)  # type: ignore[assignment]
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def age_days(when: Optional[dt.datetime], now: dt.datetime) -> Optional[float]:
    if when is None:
        return None
    return round((now - when).total_seconds() / 86400.0, 3)


def row_id(url: str) -> str:
    """blake2b-16 of the canonical URL. CONTENT-addressed: no clock, no run counter, no order.

    Canonical means: LOWERCASED whole URL, no trailing slash, no fragment, no query. Two
    spellings of one page must not become two rows, or tomorrow's diff reports a change that
    never happened.

    The whole URL and not just scheme+host, because this id is a CROSS-PRODUCER convention:
    `bin/gb-github.py` and `bin/gb-feeds.py` hash `blake2b(canonical_url.lower(), 16)` too, so a
    page reached from two miners folds to one row. Path case is information we are choosing to
    discard; every surface mined here (docs.x.ai, cursor.com, GitHub) serves lowercase paths, and
    a split row is a louder bug than a collision that has never been observed.
    """
    return hashlib.blake2b(
        canonical_url(url).encode("utf-8"), digest_size=16
    ).hexdigest()


def canonical_url(url: str) -> str:
    split = urllib.parse.urlsplit(url.strip().lower())
    path = split.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((split.scheme, split.netloc, path, "", ""))


def clean(text: str) -> str:
    """One line, no runs of whitespace, no decorative separators."""
    flat = " ".join(text.split())
    return flat.strip(" \u00b7\u2022|-\u2013\u2014").strip()


def clip(text: str, limit: int) -> str:
    flat = clean(text)
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "\u2026"


def volatile_fields_of(doc: Dict[str, Any]) -> Tuple[str, ...]:
    """The dotted paths a DOCUMENT declares volatile, read from the document itself.

    Raises on a document that declares nothing. That is not pedantry: the alternative is
    returning `()` and handing back a "stable" fingerprint over an unstripped body, which is a
    false green — the reader would believe every row changed today and re-store all of them, or
    worse, believe none did. An undeclared document is UNFINGERPRINTABLE, which is a fact worth
    an exception rather than a default.
    """
    declared = doc.get("volatile_fields")
    if not isinstance(declared, list) or not all(isinstance(p, str) for p in declared):
        raise ValueError(
            "document declares no top-level volatile_fields[] (list of dotted row paths), so "
            "its rows cannot be honestly fingerprinted — a producer that emits clock-derived "
            "fields must name them"
        )
    return tuple(declared)


def _drop_path(row: Dict[str, Any], dotted: str) -> Dict[str, Any]:
    """Copy `row` without `dotted`, walking nesting. `signals.age_days` means
    `row["signals"]["age_days"]`, not a top-level key that happens to contain a dot."""
    head, _, rest = dotted.partition(".")
    if head not in row:
        return row
    out = dict(row)
    if not rest:
        out.pop(head, None)
        return out
    child = out[head]
    if isinstance(child, dict):
        out[head] = _drop_path(child, rest)
    return out


def stable_row(row: Dict[str, Any], volatile_fields: Sequence[str]) -> Dict[str, Any]:
    """The row with every declared volatile path removed — the shape a content-addressed store
    should hash. See the module docstring: `signals.age_days` moves daily while the page does
    not, so hashing the raw row defeats `bin/gb-store.py`'s whole reason for existing.

    `volatile_fields` is REQUIRED and has no default on purpose. This function is meant to be
    hoisted into a shared module and pointed at any producer's rows; a default would make the
    foreign case silently do nothing. Pass `volatile_fields_of(doc)` when the rows came from a
    document, or this module's `VOLATILE_FIELDS` for rows this producer just built.
    """
    out = row
    for dotted in volatile_fields:
        out = _drop_path(out, dotted)
    return out


def row_fingerprint(row: Dict[str, Any], volatile_fields: Sequence[str]) -> str:
    body = json.dumps(
        stable_row(row, volatile_fields),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.blake2b(body.encode("utf-8"), digest_size=16).hexdigest()


def document_fingerprints(doc: Dict[str, Any]) -> Dict[str, str]:
    """`{row id: stable fingerprint}` for a whole artifact, stripped by ITS OWN declaration.

    This is the entry point a store or a differ should use, because it cannot be called with the
    wrong producer's field list — the list comes from the document in hand.
    """
    fields = volatile_fields_of(doc)
    rows = doc.get("rows") or []
    return {
        str(r.get("id")): row_fingerprint(r, fields)
        for r in rows
        if isinstance(r, dict)
    }


# ---------------------------------------------------------------------------------------------
# Surface 1 — docs.x.ai/sitemap.xml  (regex is sanctioned HERE and nowhere else: this is XML
# with a fixed generator-emitted shape, not a page of hand-written HTML)
# ---------------------------------------------------------------------------------------------

URL_BLOCK_RE = re.compile(r"<url\b[^>]*>(.*?)</url\s*>", re.S | re.I)
LOC_RE = re.compile(r"<loc\b[^>]*>\s*(.*?)\s*</loc\s*>", re.S | re.I)
LASTMOD_RE = re.compile(r"<lastmod\b[^>]*>\s*(.*?)\s*</lastmod\s*>", re.S | re.I)
CHANGEFREQ_RE = re.compile(
    r"<changefreq\b[^>]*>\s*(.*?)\s*</changefreq\s*>", re.S | re.I
)
PRIORITY_RE = re.compile(r"<priority\b[^>]*>\s*(.*?)\s*</priority\s*>", re.S | re.I)


@dataclasses.dataclass(frozen=True)
class Page:
    """One `<url>` entry from the sitemap."""

    url: str
    lastmod: Optional[str]
    changefreq: Optional[str]
    priority: Optional[str]


def _one(pattern: "re.Pattern[str]", block: str) -> Optional[str]:
    found = pattern.search(block)
    return clean(found.group(1)) if found else None


def parse_sitemap(text: str) -> List[Page]:
    """Every `<loc>`/`<lastmod>` pair, in document order.

    Two paths on purpose: the `<url>`-block path pairs a loc with ITS lastmod, and the flat
    fallback (a sitemap index, or a generator that omits `<url>`) still yields the locs with a
    null lastmod. A parser that returned [] on the second shape would report "the vendor
    published nothing" about a document it simply could not read.
    """
    pages: List[Page] = []
    blocks = URL_BLOCK_RE.findall(text)
    if blocks:
        for block in blocks:
            loc = _one(LOC_RE, block)
            if not loc:
                continue
            pages.append(
                Page(
                    url=loc,
                    lastmod=_one(LASTMOD_RE, block),
                    changefreq=_one(CHANGEFREQ_RE, block),
                    priority=_one(PRIORITY_RE, block),
                )
            )
        return pages
    for loc in LOC_RE.findall(text):
        cleaned = clean(loc)
        if cleaned:
            pages.append(
                Page(url=cleaned, lastmod=None, changefreq=None, priority=None)
            )
    return pages


def title_from_path(path: str) -> str:
    """The sitemap carries no titles. `build/cli/headless-scripting` -> `Headless Scripting`,
    marked `title_derived` in the row so nobody cites it as the vendor's own words."""
    tail = [seg for seg in path.strip("/").split("/") if seg]
    if not tail:
        return "Overview"
    words = tail[-1].replace("_", "-").replace(".", "-").split("-")
    return " ".join(w[:1].upper() + w[1:] for w in words if w) or tail[-1]


def page_row(page: Page, source: str, now: dt.datetime) -> Dict[str, Any]:
    url = canonical_url(page.url)
    split = urllib.parse.urlsplit(url)
    path = split.path or "/"
    when = parse_ts(page.lastmod)
    signals: Dict[str, Any] = {
        "path": path,
        "lastmod": iso_z(when) if when else None,
        "lastmod_raw": page.lastmod,
        "age_days": age_days(when, now),
        # Documented in https://docs.x.ai/llms.txt — every docs page has a markdown twin. This is
        # the address a body fetcher follows for the rows whose lastmod actually moved.
        "markdown_url": url + ".md",
        "changefreq": page.changefreq,
        "priority": page.priority,
        "section": path.strip("/").split("/")[0] if path.strip("/") else "",
        "title_derived": True,
    }
    return {
        "id": row_id(url),
        "source": source,
        "url": url,
        "title": title_from_path(path),
        "kind": "xai-docs-page",
        "published_at": iso_z(when) if when else None,
        "summary": f"docs.x.ai page {path}",
        "signals": signals,
    }


# ---------------------------------------------------------------------------------------------
# Surfaces 2 and 3 — cursor.com/changelog and /blog, parsed as a TREE
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Card:
    """One `<article>` and what was found inside it. Deliberately shapeless: the two Cursor
    pages nest differently (the changelog puts the permalink INSIDE the article, the blog wraps
    the article in the `<a>`), and a parser that hardcoded either layout would silently return
    zero rows the day the other one changed."""

    href: Optional[str]
    links: List[str] = dataclasses.field(default_factory=list)
    times: List[Tuple[Optional[str], str]] = dataclasses.field(default_factory=list)
    headings: List[Tuple[int, str, Optional[str]]] = dataclasses.field(
        default_factory=list
    )
    paragraphs: List[str] = dataclasses.field(default_factory=list)


class CardParser(html.parser.HTMLParser):
    """A tolerant `<article>` reader built on `html.parser`.

    NOT a regex. Measured 2026-09-11: cursor.com/changelog renders each entry TWICE (one layout
    per breakpoint), the title is an `<h1>` while the date line is an `<h3>` ABOVE it, and the
    blog cards carry no heading at all — their title is a `<p>`. Those are structural facts
    about a tree; a regex that appeared to handle them would be a coincidence with a maintenance
    cost. Only the sitemap (real XML, generator-emitted) is parsed by pattern.

    Tolerance means: an unclosed tag never desynchronises the element stack (closing a tag pops
    back to the nearest match and discards what was left open), and an unclosed `<article>` at
    EOF still yields its card.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: List[Tuple[str, Dict[str, str]]] = []
        self.cards: List[Card] = []
        self._open: List[Card] = []
        self._cap: List[Tuple[str, Dict[str, str], List[str]]] = []

    # -- element stack -------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag not in VOID_TAGS:
            self.stack.append((tag, attr))
        if tag == "article":
            href = next(
                (
                    a.get("href")
                    for name, a in reversed(self.stack)
                    if name == "a" and a.get("href")
                ),
                None,
            )
            card = Card(href=href)
            self.cards.append(card)
            self._open.append(card)
            return
        if not self._open:
            return
        if tag == "a" and attr.get("href"):
            self._open[-1].links.append(attr["href"])
        if tag in INTEREST_TAGS:
            self._cap.append((tag, attr, []))

    def handle_startendtag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        # `<time ... />` self-closed: record the attribute, no text.
        if tag == "time" and self._open:
            attr = {k.lower(): (v or "") for k, v in attrs}
            self._open[-1].times.append((attr.get("datetime"), ""))

    def handle_data(self, data: str) -> None:
        if self._cap and data.strip():
            self._cap[-1][2].append(data)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._cap) - 1, -1, -1):
            if self._cap[i][0] == tag:
                frame = self._cap[i]
                self._cap = self._cap[:i]
                self._commit(frame)
                break
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                if tag == "article" and self._open:
                    self._open.pop()
                del self.stack[i:]
                break

    def _commit(self, frame: Tuple[str, Dict[str, str], List[str]]) -> None:
        if not self._open:
            return
        tag, attr, chunks = frame
        text = clean("".join(chunks))
        card = self._open[-1]
        if tag == "time":
            card.times.append((attr.get("datetime") or None, text))
        elif tag.startswith("h"):
            if text:
                card.headings.append((int(tag[1]), text, attr.get("id") or None))
        elif text:
            card.paragraphs.append(text)


def parse_cards(markup: str) -> List[Card]:
    parser = CardParser()
    parser.feed(markup)
    parser.close()
    return parser.cards


def _card_url(card: Card, page_url: str) -> Optional[str]:
    """The entry's permalink: a link one level below the index page's own path.

    `/changelog/page/2` is pagination, not an entry — a row for it would be a row for a
    navigation control, and it would arrive every single day.
    """
    prefix = urllib.parse.urlsplit(page_url).path.rstrip("/") + "/"
    candidates = list(card.links) + ([card.href] if card.href else [])
    for href in candidates:
        if not href:
            continue
        path = urllib.parse.urlsplit(urllib.parse.urljoin(page_url, href)).path
        if not path.startswith(prefix):
            continue
        tail = path[len(prefix) :].strip("/")
        if not tail or "/" in tail or tail.isdigit() or tail == "page":
            continue
        return canonical_url(urllib.parse.urljoin(page_url, href))
    heading_id = next((hid for _, _, hid in card.headings if hid), None)
    return f"{canonical_url(page_url)}#{heading_id}" if heading_id else None


def _card_title(card: Card) -> str:
    """The most prominent heading, else the first paragraph.

    "Most prominent" is the lowest level present, not the first in document order: the changelog
    puts an `<h3>` date line ABOVE the `<h1>` title, so first-in-order picks the date.
    """
    if card.headings:
        level = min(lvl for lvl, _, _ in card.headings)
        for lvl, text, _ in card.headings:
            if lvl == level and text:
                return clip(text, TITLE_CHARS)
    for para in card.paragraphs:
        if para:
            return clip(para, TITLE_CHARS)
    return ""


def _card_published(card: Card) -> Optional[dt.datetime]:
    for attr, text in card.times:
        when = parse_ts(attr) or parse_ts(text)
        if when:
            return when
    return None


def rows_from_cards(
    cards: Sequence[Card], page_url: str, kind: str, now: dt.datetime
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for card in cards:
        title = _card_title(card)
        url = _card_url(card, page_url)
        when = _card_published(card)
        if not title or url is None:
            continue  # chrome: a nav or layout <article> with no entry identity
        summary = next(
            (p for p in card.paragraphs if clean(p) and clean(p) != title), ""
        )
        rows.append(
            {
                "id": row_id(url),
                "source": canonical_url(page_url),
                "url": url,
                "title": title,
                "kind": kind,
                "published_at": iso_z(when) if when else None,
                "summary": clip(summary, SUMMARY_CHARS),
                "signals": {
                    "path": urllib.parse.urlsplit(url).path,
                    "lastmod": iso_z(when) if when else None,
                    "lastmod_raw": next(
                        (a or t for a, t in card.times if (a or t)), None
                    ),
                    "age_days": age_days(when, now),
                    "slug": urllib.parse.urlsplit(url).path.rstrip("/").split("/")[-1],
                    "title_derived": False,
                },
            }
        )
    return rows


# ---------------------------------------------------------------------------------------------
# Velocity — the number that makes a daily cadence justifiable, or does not
# ---------------------------------------------------------------------------------------------
def velocity_of(
    rows: Sequence[Dict[str, Any]], now: dt.datetime, docs_kind: str = "xai-docs-page"
) -> Dict[str, Any]:
    """Measured churn. Every field here is counted, never estimated.

    `cadence_justified` is the decision this object exists for: if at least half the docs
    surface moved inside 7 days, a weekly poll sees one frame of a film.
    """
    docs = [r for r in rows if r.get("kind") == docs_kind]
    ages = [
        r["signals"]["age_days"]
        for r in docs
        if isinstance(r.get("signals"), dict)
        and r["signals"].get("age_days") is not None
    ]
    stamps = [
        r["signals"]["lastmod"]
        for r in docs
        if isinstance(r.get("signals"), dict) and r["signals"].get("lastmod")
    ]
    others = [r for r in rows if r.get("kind") != docs_kind]
    other_ages = [
        r["signals"]["age_days"]
        for r in others
        if isinstance(r.get("signals"), dict)
        and r["signals"].get("age_days") is not None
    ]
    in_days = lambda limit: sum(1 for a in ages if a <= limit)  # noqa: E731
    changed_7d = in_days(7)
    total = len(docs)
    return {
        "measured_at": iso_z(now),
        "pages_total": total,
        "pages_with_lastmod": len(ages),
        "changed_1d": in_days(1),
        "changed_7d": changed_7d,
        "changed_30d": in_days(30),
        "newest_lastmod": max(stamps) if stamps else None,
        "oldest_lastmod": min(stamps) if stamps else None,
        "newest_age_days": min(ages) if ages else None,
        "median_age_days": round(statistics.median(ages), 3) if ages else None,
        "churn_7d_pct": round(100.0 * changed_7d / total, 1) if total else None,
        "entries_total": len(others),
        "entries_changed_7d": sum(1 for a in other_ages if a <= 7),
        "entries_newest_published": max(
            (r["published_at"] for r in others if r.get("published_at")),
            default=None,
        ),
        "cadence_justified": "daily"
        if total and changed_7d / total >= 0.5
        else "weekly",
        "why": (
            f"{changed_7d}/{total} docs pages carry a lastmod inside 7 days"
            if total
            else "no docs page carried a lastmod — velocity is UNMEASURED, not zero"
        ),
    }


# ---------------------------------------------------------------------------------------------
# collect — the whole run, with the fetcher injected so the selftest can drive it offline
# ---------------------------------------------------------------------------------------------
SURFACES: Tuple[Tuple[str, str], ...] = (
    (CURSOR_CHANGELOG, "cursor-changelog"),
    (CURSOR_BLOG, "cursor-blog"),
)


def dedup(rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """First occurrence wins, keyed by the content id. This is not hygiene: cursor.com renders
    every changelog entry twice, so without it half the Cursor rows are a layout artifact."""
    seen: Dict[str, Dict[str, Any]] = {}
    dropped = 0
    for row in rows:
        rid = str(row.get("id"))
        if rid in seen:
            dropped += 1
            continue
        seen[rid] = row
    return list(seen.values()), dropped


def collect(
    fetch: Fetcher,
    now: Optional[dt.datetime] = None,
    *,
    min_locs: int = MIN_SITEMAP_LOCS,
) -> Tuple[Dict[str, Any], List[str]]:
    """Fetch every vendor surface and build the artifact. Returns `(payload, problems)`.

    A problem is a sentence for a human; `payload["sources"]` is the machine-readable truth and
    always carries a row per surface, refusals included.
    """
    when = now or utcnow()
    sources: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    problems: List[str] = []

    # --- 1. the velocity instrument -------------------------------------------------------
    got = fetch(SITEMAP_URL)
    pages = parse_sitemap(got.text()) if got.status == 200 else []
    doc_rows = [page_row(p, canonical_url(SITEMAP_URL), when) for p in pages]
    rows.extend(doc_rows)
    note = ""
    if got.status != 200:
        note = "PRIMARY SURFACE UNREACHABLE — velocity is unmeasured, not zero"
        problems.append(
            f"{SITEMAP_URL} answered {got.status or 'no response'}"
            + (f" ({got.error})" if got.error else "")
            + " — the velocity instrument is DOWN; this run measured nothing about docs churn"
        )
    elif not pages:
        note = "200 with ZERO <loc> entries — parse failure or a surface rewrite"
        problems.append(
            f"{SITEMAP_URL} returned {len(got.body)} bytes and parsed ZERO <loc> entries — "
            "an empty scan set is an ERROR, never a quiet day"
        )
    elif len(pages) < min_locs:
        note = f"canary: {len(pages)} < {min_locs} entries"
        problems.append(
            f"{SITEMAP_URL} lists only {len(pages)} pages (floor {min_locs}) — the docs index "
            "shrank; a human must look before this is recorded as truth"
        )
    sources.append(got.to_json("xai-docs-sitemap", len(doc_rows), note))

    # --- 2/3. Cursor's two dated surfaces -------------------------------------------------
    for url, kind in SURFACES:
        got = fetch(url)
        cards = parse_cards(got.text()) if got.status == 200 else []
        found = rows_from_cards(cards, url, kind, when)
        rows.extend(found)
        note = ""
        if got.status != 200:
            note = "unreachable — entries unmeasured, not absent"
            problems.append(
                f"{url} answered {got.status or 'no response'}"
                + (f" ({got.error})" if got.error else "")
            )
        elif not found:
            note = f"200 with {len(cards)} <article> element(s) and ZERO dated entries"
            problems.append(
                f"{url} returned {len(got.body)} bytes, {len(cards)} article element(s) and "
                "ZERO parseable entries — the page shape changed; the parser is blind, not the "
                "vendor silent"
            )
        sources.append(got.to_json(kind, len(found), note))

    # --- 4. the documented refusal --------------------------------------------------------
    got = fetch(XAI_NEWS)
    if got.status == 403:
        note = (
            "DOCUMENTED REFUSAL (NE-1, AGENTS.md §5 tier 2): x.ai serves 403 to plain clients. "
            "Recorded as a status, never as an empty news list. Needs a browser-grade reader."
        )
    elif got.status == 200:
        note = (
            "200 to a plain client — the refusal may have been lifted; wire a parser for this "
            "surface before treating it as read"
        )
    else:
        note = f"unexpected status {got.status or 'no response'} — neither the known 403 nor 200"
    # Zero rows, on purpose and by construction: nothing here parses x.ai/news, so fabricating
    # a row from a bot-protection page is the one thing this producer must never do.
    sources.append(got.to_json("xai-news", 0, note))
    if got.status not in (200, 403):
        problems.append(
            f"{XAI_NEWS} answered {got.status or 'no response'} — expected the documented 403"
        )

    rows, dropped = dedup(rows)
    rows.sort(
        key=lambda r: (r.get("published_at") or "", str(r.get("url"))), reverse=True
    )
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "captured_at": iso_z(when),
        "generator": "bin/gb-sources.py",
        "velocity": velocity_of(rows, when),
        "counts": {
            "rows_total": len(rows),
            "duplicates_dropped": dropped,
            "by_kind": {
                kind: sum(1 for r in rows if r.get("kind") == kind)
                for kind in sorted({str(r.get("kind")) for r in rows})
            },
            "sources_ok": sum(1 for s in sources if s["ok"]),
            "sources_refused": sum(1 for s in sources if not s["ok"]),
        },
        # `volatile_fields` is the cross-producer spelling agreed with `bin/gb-github.py` and
        # `bin/gb-feeds.py`: a downstream content-hash strips these paths before diffing a row
        # body. `dedup` is the same fact with the reasoning attached.
        "volatile_fields": list(VOLATILE_FIELDS),
        "dedup": {
            "key": "rows[].id = blake2b-16(canonical url, lowercased)",
            "note": "a store should hash gb_sources.stable_row(row, volatile_fields_of(doc)), "
            "or call gb_sources.document_fingerprints(doc) — the paths come from THIS "
            "document's volatile_fields[], never from a baked-in list",
        },
        "sources": sources,
        "problems": problems,
        "rows": rows,
    }
    return payload, problems


# ---------------------------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------------------------
def newest_artifact(root: pathlib.Path) -> Optional[pathlib.Path]:
    folder = root / ARTIFACT_ROOT
    if not folder.is_dir():
        return None
    rows = sorted(p for p in folder.glob("*.json") if p.is_file())
    return rows[-1] if rows else None


def render(payload: Dict[str, Any]) -> None:
    vel = payload["velocity"]
    counts = payload["counts"]
    print(f"gb-sources {payload['captured_at']}  schema {payload['schema']}")
    print(
        f"  velocity   {vel['pages_total']} docs pages · "
        f"{vel['changed_1d']} moved <=1d · {vel['changed_7d']} <=7d · "
        f"{vel['changed_30d']} <=30d · churn7d {vel['churn_7d_pct']}% · "
        f"cadence {vel['cadence_justified']}"
    )
    print(f"  newest     {vel['newest_lastmod']} ({vel['newest_age_days']}d old)")
    print(
        f"  rows       {counts['rows_total']} unique "
        f"(-{counts['duplicates_dropped']} duplicate render(s)) · "
        + " · ".join(f"{k} {v}" for k, v in counts["by_kind"].items())
    )
    for src in payload["sources"]:
        flag = "ok " if src["ok"] else "!! "
        print(
            f"  {flag}{src['status']:>3} {src['bytes']:>7}B {src['rows']:>4} rows  "
            f"{src['url']}"
        )
        if src.get("note"):
            print(f"        {clip(src['note'], 150)}")
    for problem in payload.get("problems") or []:
        print(f"  PROBLEM  {problem}")


# ---------------------------------------------------------------------------------------------
# Selftest — inline fixtures, no network. Every leg fires on a known-bad input.
# ---------------------------------------------------------------------------------------------
FIXTURE_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://docs.x.ai/overview</loc><lastmod>2026-09-11T00:00:00.000Z</lastmod>
<changefreq>weekly</changefreq><priority>0.7</priority></url>
<url><loc>https://docs.x.ai/build/cli/reference</loc><lastmod>2026-09-08T12:00:00.000Z</lastmod></url>
<url><loc>https://docs.x.ai/grok-bot/security</loc><lastmod>2026-08-22T12:00:00.000Z</lastmod></url>
<url><loc>https://docs.x.ai/legacy/ancient</loc><lastmod>2025-12-01T00:00:00.000Z</lastmod></url>
<url><loc>https://docs.x.ai/no-date-page</loc></url>
</urlset>
"""

FIXTURE_SMALL_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset><url><loc>https://docs.x.ai/overview</loc><lastmod>2026-09-11T00:00:00Z</lastmod></url></urlset>
"""

# One changelog entry, rendered TWICE the way cursor.com really renders it (two breakpoints),
# with the <h3> date line sitting ABOVE the <h1> title.
_CHANGELOG_CARD = """
<article class="entry">
  <div><h3 class="meta">Sep 10, 2026</h3><time dateTime="2026-09-10T00:00:00.000Z">Sep 10, 2026</time>
  <span>&middot;</span><a href="/changelog">Changelog</a></div>
  <h1 class="type-lg" id="cursor-projects">Cursor Projects</h1>
  <p>Today we&#39;re launching Projects &mdash; a home for agents.</p>
  <a href="/changelog/projects">permalink</a>
</article>
"""
FIXTURE_CHANGELOG = (
    '<html><body><nav><article><a href="/changelog/page/2">Older</a></article></nav>'
    + _CHANGELOG_CARD
    + _CHANGELOG_CARD
    + "</body></html>"
)

# A blog card: no heading at all, the title is a <p>, and the permalink is the ENCLOSING <a>.
FIXTURE_BLOG = """<html><body>
<a class="row" href="/blog/self-hosted-machines"><article>
  <div><time dateTime="2026-09-02T12:00:00.000Z">Sep 2, 2026</time><span>&middot;</span><span>product</span></div>
  <div><p>Run cloud agents on machines you manage</p></div>
  <div><span>Jack Pertschuk</span></div>
</article></a>
<a class="row" href="/blog/no-attr-date"><article>
  <div><time>Sep 4, 2026</time></div><div><p>Date only in the text</p></div>
</article></a>
</body></html>"""

FIXTURE_403_BODY = b"<html><body>Just a moment...</body></html>"


def _fixture_fetcher(
    *, sitemap: str = FIXTURE_SITEMAP, news_status: int = 403
) -> Fetcher:
    """An offline `Fetcher`. Every response is a real `Fetched`, including the refusal, so the
    selftest drives the SAME `collect()` the network path drives."""

    def fetch(url: str) -> Fetched:
        stamp = iso_z(utcnow())
        if url == SITEMAP_URL:
            return Fetched(url, 200, sitemap.encode(), "", stamp, 1)
        if url == CURSOR_CHANGELOG:
            return Fetched(url, 200, FIXTURE_CHANGELOG.encode(), "", stamp, 1)
        if url == CURSOR_BLOG:
            return Fetched(url, 200, FIXTURE_BLOG.encode(), "", stamp, 1)
        if url == XAI_NEWS:
            return Fetched(
                url,
                news_status,
                FIXTURE_403_BODY if news_status == 403 else b"",
                f"HTTPError {news_status} Forbidden" if news_status == 403 else "",
                stamp,
                1,
            )
        return Fetched(url, 0, b"", "fixture: unknown url", stamp, 0)

    return fetch


T0 = dt.datetime(2026, 9, 11, 12, 0, 0, tzinfo=dt.timezone.utc)


def _t1_sitemap() -> List[str]:
    pages = parse_sitemap(FIXTURE_SITEMAP)
    bad = []
    if len(pages) != 5:
        bad.append(f"expected 5 pages, parsed {len(pages)}")
    if pages and pages[0].url != "https://docs.x.ai/overview":
        bad.append(f"first loc wrong: {pages[0].url!r}")
    if pages and pages[0].lastmod != "2026-09-11T00:00:00.000Z":
        bad.append(f"lastmod not carried: {pages[0].lastmod!r}")
    if pages and pages[0].priority != "0.7":
        bad.append(f"priority not carried: {pages[0].priority!r}")
    if len(pages) == 5 and pages[4].lastmod is not None:
        bad.append("a <url> with no <lastmod> must parse as None, not be dropped")
    # fires-on-known-bad: a loc-only sitemap must still yield its locs
    flat = parse_sitemap("<urlset><loc>https://docs.x.ai/a</loc></urlset>")
    if len(flat) != 1:
        bad.append("flat fallback lost a <loc> — a sitemap index would read as empty")
    return bad


def _t2_buckets() -> List[str]:
    rows = [page_row(p, "s", T0) for p in parse_sitemap(FIXTURE_SITEMAP)]
    vel = velocity_of(rows, T0)
    bad = []
    want = {
        "pages_total": 5,
        "pages_with_lastmod": 4,
        "changed_1d": 1,
        "changed_7d": 2,
        "changed_30d": 3,
    }
    for key, value in want.items():
        if vel[key] != value:
            bad.append(f"{key}: want {value}, got {vel[key]}")
    if vel["newest_lastmod"] != "2026-09-11T00:00:00Z":
        bad.append(f"newest_lastmod wrong: {vel['newest_lastmod']}")
    if vel["cadence_justified"] != "weekly":
        bad.append("2/5 inside 7d must NOT justify a daily cadence")
    hot = velocity_of(
        [
            page_row(
                Page(f"https://docs.x.ai/p{i}", "2026-09-11T00:00:00Z", None, None),
                "s",
                T0,
            )
            for i in range(4)
        ],
        T0,
    )
    if hot["cadence_justified"] != "daily" or hot["churn_7d_pct"] != 100.0:
        bad.append("a 100%-churn surface must justify daily")
    return bad


def _t3_id_stability() -> List[str]:
    later = T0 + dt.timedelta(days=9)
    a, _ = collect(_fixture_fetcher(), T0, min_locs=1)
    b, _ = collect(_fixture_fetcher(), later, min_locs=1)
    ids_a = {r["id"] for r in a["rows"]}
    ids_b = {r["id"] for r in b["rows"]}
    bad = []
    if ids_a != ids_b:
        bad.append(f"id sets diverged across runs: {ids_a ^ ids_b}")
    if not ids_a:
        bad.append("no rows produced — an empty proof proves nothing")
    if any(len(i) != 32 for i in ids_a):
        bad.append("id is not a 16-byte blake2b hex digest")
    if row_id("https://docs.x.ai/Overview/") != row_id(
        "https://docs.x.ai/overview?x=1#y"
    ):
        bad.append("two spellings of one URL produced two ids")
    return bad


def _t4_changelog() -> List[str]:
    rows = rows_from_cards(
        parse_cards(FIXTURE_CHANGELOG), CURSOR_CHANGELOG, "cursor-changelog", T0
    )
    rows, dropped = dedup(rows)
    bad = []
    if len(rows) != 1:
        bad.append(f"the doubly-rendered entry yielded {len(rows)} rows, want 1")
    if dropped != 1:
        bad.append(f"dedup dropped {dropped} duplicate render(s), want 1")
    if rows:
        row = rows[0]
        if row["title"] != "Cursor Projects":
            bad.append(
                f"title picked the <h3> date line, not the <h1>: {row['title']!r}"
            )
        if row["url"] != "https://cursor.com/changelog/projects":
            bad.append(f"permalink wrong: {row['url']!r}")
        if row["published_at"] != "2026-09-10T00:00:00Z":
            bad.append(f"published_at wrong: {row['published_at']!r}")
        if "launching Projects" not in row["summary"]:
            bad.append(f"summary lost the body: {row['summary']!r}")
        if row["signals"]["age_days"] != 1.5:
            bad.append(f"age_days wrong: {row['signals']['age_days']}")
    return bad


def _t5_blog() -> List[str]:
    rows = rows_from_cards(parse_cards(FIXTURE_BLOG), CURSOR_BLOG, "cursor-blog", T0)
    bad = []
    if len(rows) != 2:
        bad.append(f"expected 2 blog rows, got {len(rows)}")
    if rows:
        first = rows[0]
        if first["title"] != "Run cloud agents on machines you manage":
            bad.append(f"title not taken from <p>: {first['title']!r}")
        if first["url"] != "https://cursor.com/blog/self-hosted-machines":
            bad.append(f"enclosing <a> permalink lost: {first['url']!r}")
        if first["published_at"] != "2026-09-02T12:00:00Z":
            bad.append(f"published_at wrong: {first['published_at']!r}")
    return bad


def _t6_refusal() -> List[str]:
    payload, problems = collect(_fixture_fetcher(), T0, min_locs=1)
    news = [s for s in payload["sources"] if s["url"] == XAI_NEWS]
    bad = []
    if len(news) != 1:
        bad.append("the refused source is missing from sources[] entirely")
        return bad
    entry = news[0]
    if entry["status"] != 403:
        bad.append(f"status not recorded as 403: {entry['status']}")
    if entry["ok"] is not False or entry["rows"] != 0:
        bad.append("a 403 must record ok=false and ZERO rows")
    if entry["bytes"] != len(FIXTURE_403_BODY):
        bad.append("the refusal body size was not recorded")
    if "NE-1" not in (entry.get("note") or ""):
        bad.append("the refusal is not labelled as the documented one")
    if any(
        XAI_NEWS in str(r.get("source")) or "x.ai/news" in str(r.get("url"))
        for r in payload["rows"]
    ):
        bad.append("a row was FABRICATED from the bot-protection page")
    if payload["counts"]["sources_refused"] != 1:
        bad.append("the refusal is not counted")
    if problems:
        bad.append(f"the documented 403 must not raise a problem: {problems}")
    # fires-on-known-bad: an UNDOCUMENTED status must raise one
    _, odd = collect(_fixture_fetcher(news_status=500), T0, min_locs=1)
    if not any("500" in p for p in odd):
        bad.append("a 500 from x.ai/news went unreported")
    return bad


def _t7_no_volatile_fields() -> List[str]:
    a, _ = collect(_fixture_fetcher(), T0, min_locs=1)
    b, _ = collect(_fixture_fetcher(), T0 + dt.timedelta(days=3), min_locs=1)
    bad = []
    for row in a["rows"]:
        for key in ("captured_at", "fetched_at", "measured_at"):
            if key in row or key in (row.get("signals") or {}):
                bad.append(f"row carries the volatile field {key!r}")
    fa = document_fingerprints(a)
    fb = document_fingerprints(b)
    if fa != fb:
        moved = [k for k in fa if fa[k] != fb.get(k)]
        bad.append(f"{len(moved)} stable fingerprint(s) moved with the clock alone")
    raw_a = {r["id"]: json.dumps(r, sort_keys=True) for r in a["rows"]}
    raw_b = {r["id"]: json.dumps(r, sort_keys=True) for r in b["rows"]}
    if raw_a == raw_b:
        bad.append(
            "the RAW rows did not move with the clock — age_days is missing, so this proof "
            "would pass vacuously"
        )
    if a.get("volatile_fields") != ["signals.age_days"]:
        bad.append(f"the declaration is wrong: {a.get('volatile_fields')!r}")

    # A SYNTHETIC sibling row whose volatile paths sit somewhere else entirely — one top-level,
    # one nested, one undeclared field that must survive. Synthetic on purpose: no live
    # producer's shape is pinned here, so a peer changing his row layout cannot turn this leg
    # red. (Real cross-producer interop was MEASURED separately: gb-github.py's 451-row
    # artifact through `document_fingerprints` -> 451 fingerprints, 0 changed under a +1 day
    # clock. That is a measurement, not a fixture, and it does not belong in a unit proof.)
    # A helper with this module's field list baked in returns an unstripped body and calls it
    # stable, which is the false green this leg exists to catch.
    foreign = {
        "volatile_fields": ["signals.stars_delta", "seen_at"],
        "rows": [
            {
                "id": "beef",
                "url": "https://github.com/x/y",
                "seen_at": "2026-09-11T00:00:00Z",
                "signals": {"stars": 451, "stars_delta": 3, "age_days": 1.0},
            }
        ],
    }
    stripped = stable_row(foreign["rows"][0], volatile_fields_of(foreign))
    if "seen_at" in stripped:
        bad.append("a foreign top-level volatile path was not stripped")
    if "stars_delta" in stripped["signals"]:
        bad.append("a foreign NESTED volatile path was not stripped")
    if (
        stripped["signals"].get("stars") != 451
        or stripped["signals"].get("age_days") != 1.0
    ):
        bad.append("stripping a declared path removed a field nobody declared")
    if foreign["rows"][0].get("seen_at") is None:
        bad.append("stable_row mutated the caller's row instead of copying it")
    try:
        document_fingerprints({"rows": foreign["rows"]})
        bad.append(
            "a document with NO volatile_fields[] was fingerprinted anyway — that is the "
            "false green this argument exists to prevent"
        )
    except ValueError:
        pass
    return bad


def _t8_canary() -> List[str]:
    payload, problems = collect(_fixture_fetcher(sitemap=FIXTURE_SMALL_SITEMAP), T0)
    bad = []
    if not any("floor" in p for p in problems):
        bad.append("a 1-entry docs index did not trip the canary")
    entry = [s for s in payload["sources"] if s["kind"] == "xai-docs-sitemap"][0]
    if "canary" not in (entry.get("note") or ""):
        bad.append("the canary is not recorded in the artifact")
    ok, _ = collect(_fixture_fetcher(sitemap=FIXTURE_SMALL_SITEMAP), T0, min_locs=1)
    if [s for s in ok["sources"] if s["kind"] == "xai-docs-sitemap"][0].get("note"):
        bad.append("the canary fired below its own floor")
    dead: Fetcher = lambda url: Fetched(url, 0, b"", "fixture: dead", iso_z(T0), 0)  # noqa: E731
    down, down_problems = collect(dead, T0, min_locs=1)
    if down["velocity"]["pages_total"] != 0:
        bad.append("a dead network reported pages")
    if "UNMEASURED" not in down["velocity"]["why"]:
        bad.append(
            "a dead primary surface reported velocity as zero rather than unmeasured"
        )
    if not down_problems:
        bad.append("a dead network produced no problem")
    return bad


def _t9_date_fallback() -> List[str]:
    bad = []
    if parse_ts("Sep 10, 2026") != dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc):
        bad.append("human date text did not parse")
    if parse_ts("2026-09-08T20:17:43.503Z") != dt.datetime(
        2026, 9, 8, 20, 17, 43, 503000, tzinfo=dt.timezone.utc
    ):
        bad.append("millisecond ISO with Z did not parse")
    if parse_ts("not a date") is not None or parse_ts(None) is not None:
        bad.append("garbage parsed as a date")
    rows = rows_from_cards(parse_cards(FIXTURE_BLOG), CURSOR_BLOG, "cursor-blog", T0)
    textual = [r for r in rows if r["url"].endswith("no-attr-date")]
    if not textual or textual[0]["published_at"] != "2026-09-04T00:00:00Z":
        bad.append("a <time> with no dateTime attribute lost its date")
    return bad


# --- the structural half: `collect()` CANNOT write -------------------------------------------
# Adopted from GitHubEcosystemMiner 2026-09-11. My `collect()` is write-free today, and my
# earlier report CLAIMED "write-free by construction" with nothing enforcing it. No behavioural
# test can prove the ABSENCE of a write path that a later edit adds, so this one reads this
# file's own AST: collect must call no writer and take no root, and exactly one function in the
# module may touch the filesystem. A claimed invariant with no check is a comment.
WRITER_CALLS = frozenset(
    {
        "atomic_write_json",
        "atomic_write_text",
        "atomic_write_bytes",
        "write_text",
        "write_bytes",
        "mkdir",
        "touch",
        "unlink",
        "rmdir",
    }
)
SELF_PATH = pathlib.Path(__file__).resolve()


def _writer_calls_in(node: ast.AST) -> List[str]:
    """Filesystem-mutating calls reachable inside one function body, by NAME.

    Deliberately excludes `replace`: `str.replace` is used twice in this module for text, and a
    rule that fires on correct code teaches people to add exemptions instead of keeping the
    property. `os.replace` never appears here — it lives inside `gbtypes._atomic_write`.
    """
    found = []
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        func = inner.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in WRITER_CALLS:
            found.append(name)
    return sorted(set(found))


def write_path_audit(tree: ast.Module) -> Dict[str, List[str]]:
    """Who can write, can `collect` reach a writer, and does `collect` even know a root."""
    writers: List[str] = []
    collect_writes: List[str] = []
    root_params: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        hits = _writer_calls_in(node)
        if hits:
            writers.append(node.name)
        if node.name == "collect":
            collect_writes = hits
            args = node.args
            names = [
                a.arg
                for a in list(getattr(args, "posonlyargs", []))
                + list(args.args)
                + list(args.kwonlyargs)
            ]
            root_params = [n for n in names if n in ("root", "out", "path", "write")]
    return {
        "writers": sorted(set(writers)),
        "collect_writes": collect_writes,
        "collect_root_params": sorted(root_params),
    }


def _t10_collect_cannot_write() -> List[str]:
    source = SELF_PATH.read_text(errors="replace")
    audit = write_path_audit(ast.parse(source))
    bad = []
    if audit["collect_writes"]:
        bad.append(f"collect() can write: {audit['collect_writes']}")
    if audit["collect_root_params"]:
        bad.append(
            f"collect() takes a destination/flag {audit['collect_root_params']} — a default "
            f"nobody reads is how a pure function starts writing"
        )
    if audit["writers"] != ["cmd_collect"]:
        bad.append(
            f"the single writer is no longer cmd_collect alone: {audit['writers']}"
        )

    # fires-on-known-bad #1: a write injected into collect must be caught.
    anchor = "    when = now or utcnow()"
    mutated = source.replace(
        anchor, anchor + "\n    atomic_write_json(pathlib.Path('/tmp/x'), {})", 1
    )
    if mutated == source:
        bad.append("the mutation anchor moved — this proof would pass vacuously")
    elif not write_path_audit(ast.parse(mutated))["collect_writes"]:
        bad.append("a write injected into collect() went undetected")

    # fires-on-known-bad #2: a second writer anywhere in the module must be caught.
    extra = source + "\n\ndef sneaky(p: Any) -> None:\n    atomic_write_json(p, {})\n"
    if "sneaky" not in write_path_audit(ast.parse(extra))["writers"]:
        bad.append("a second writer function went undetected")
    return bad


def _t11_cancellation_propagates() -> List[str]:
    """A signal mid-fetch must UNWIND, not become a recorded fetch failure.

    `gbtypes.Cancelled` derives from `Exception`, so a blanket `except Exception` in a fetcher
    silently converts a cancellation into data: the run continues, writes a complete artifact of
    refusals and exits 1 (findings). Measured exactly that before the fix. A scheduled job
    cannot tell that apart from a bad-network day, and the spine's "no partial artifact written"
    guarantee dies inside the handler rather than in `main`.
    """
    bad = []

    def dying(url: str) -> Fetched:
        raise Cancelled(2)

    try:
        collect(dying, T0, min_locs=1)
        bad.append("collect() swallowed a cancellation and returned a payload")
    except Cancelled:
        pass

    # The REAL handler, not just the injected fetcher: make urlopen itself cancel.
    real = urllib.request.urlopen

    def cancelling_urlopen(*_a: Any, **_k: Any) -> Any:
        raise Cancelled(15)

    urllib.request.urlopen = cancelling_urlopen  # type: ignore[assignment]
    try:
        got = http_fetch("https://example.invalid/x")
        bad.append(
            f"http_fetch turned a cancellation into a recorded failure: "
            f"status={got.status} error={got.error!r}"
        )
    except Cancelled:
        pass
    finally:
        urllib.request.urlopen = real  # type: ignore[assignment]

    # ...and it must still record an ordinary network failure as data (the property the
    # re-raise must not have broken).
    def failing_urlopen(*_a: Any, **_k: Any) -> Any:
        raise OSError("nodename nor servname provided")

    urllib.request.urlopen = failing_urlopen  # type: ignore[assignment]
    try:
        got = http_fetch("https://example.invalid/x")
        if got.status != 0 or "OSError" not in got.error:
            bad.append(f"a real network failure stopped being recorded: {got.error!r}")
    except Exception as exc:  # noqa: BLE001
        bad.append(f"http_fetch raised on an ordinary network failure: {exc!r}")
    finally:
        urllib.request.urlopen = real  # type: ignore[assignment]
    return bad


VERDICT_EXITS = frozenset({"EXIT_ERROR", "EXIT_FINDINGS", "EXIT_USAGE"})


def verdict_functions(tree: ast.Module) -> List[str]:
    """Every function that can return a NON-ZERO exit code, transitively.

    Derived, not listed. A hand-kept list of "the commands" is exactly how `selftest` escaped
    the first version of this check: `body()` does `return selftest()`, so the verdict is
    carried by a CALL and a grep for `return 1` — or a rule keyed on a `cmd_` prefix — never
    sees it. The closure runs to a fixpoint so a chain three calls deep is still caught.

    Three ways in, and the narrowness of each was paid for: (1) a return that MENTIONS an
    `EXIT_*` name; (2) a return of a local name ASSIGNED from an `EXIT_*` expression, which is
    how `selftest`'s `code = EXIT_OK if ... else EXIT_ERROR; return code` reads; (3) a return of
    a bare non-zero int literal, for a future `return 1`. Rule 3 checks the WHOLE returned
    expression, not any int inside it — the first draft flagged `round(x, 3)` and
    `flat[: limit - 1]`, i.e. seventeen functions including `clip` and `row_id`, which is a
    check nobody would keep.
    """
    direct: Dict[str, List[str]] = {}
    calls: Dict[str, List[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        aliases = {
            target.id
            for stmt in ast.walk(node)
            if isinstance(stmt, ast.Assign)
            for part in ast.walk(stmt.value)
            if isinstance(part, ast.Name) and part.id in VERDICT_EXITS
            for target in stmt.targets
            if isinstance(target, ast.Name)
        }
        hits: List[str] = []
        called: List[str] = []
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Return) or inner.value is None:
                continue
            value = inner.value
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, int)
                and value.value not in (0, False)
            ):
                hits.append(str(value.value))
                continue
            for part in ast.walk(value):
                if isinstance(part, ast.Name) and (
                    part.id in VERDICT_EXITS or part.id in aliases
                ):
                    hits.append(part.id)
                elif isinstance(part, ast.Call):
                    name = getattr(part.func, "id", "")
                    if name:
                        called.append(name)
        direct[node.name] = hits
        calls[node.name] = called
    carriers = {name for name, hits in direct.items() if hits}
    changed = True
    while changed:
        changed = False
        for name, called in calls.items():
            if name not in carriers and any(c in carriers for c in called):
                carriers.add(name)
                changed = True
    return sorted(carriers)


WRITE_CALLS = (
    # Mine.
    "print",
    "write",
    "writelines",
    # argparse's, performed on my behalf AND carrying its own exit code.
    "print_help",
    "print_usage",
    "parse_args",
    "parse_known_args",
    "error",
)


def unguarded_writes(tree: ast.Module, carriers: Sequence[str]) -> List[str]:
    """Writes on a verdict's path that a closed pipe could raise through.

    GUARDED means one of three positions, and the definition is the whole check:

      1. inside a nested `def report()` handed to `emit` (stdout), or a `lambda` handed to
         `_guarded` (stderr) — the guard catches the EPIPE around the call;
      2. inside the TRY BODY of a `try` whose handlers include `BrokenPipeError` — the only
         available position for a write a LIBRARY performs for me, e.g. `parse_args`;
      3. not a write at all.

    Note 2 says TRY BODY. The handler holds the recovered `return`, never the write. Defining
    it the other way round inverts the check — correct code reads as unguarded and the real
    defect waves through — which is why `_t12` probes this predicate with a synthetic pair
    instead of trusting it.

    KNOWN OVER-STRICTNESS, kept deliberately: a write reachable only on a path that returns 0
    needs no guard, because the spine's EPIPE->0 arm is then exactly right. Proving "only
    reachable on the zero path" needs reachability analysis this file does not need — and it
    costs nothing here, because every write in all four carriers is genuinely on a non-zero
    path. If a zero-only write ever appears, weaken the rule with the returns-based invariant
    rather than adding an exemption list. (Trade-off named by GitHubEcosystemMiner 2026-09-11,
    whose file HAS such writes and therefore correctly uses the weaker rule.)
    """
    found: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in carriers:
            continue
        nested = {
            id(inner)
            for child in ast.walk(node)
            if (isinstance(child, ast.FunctionDef) and child is not node)
            or isinstance(child, ast.Lambda)
            for inner in ast.walk(child)
        }
        in_try_body = {
            id(inner)
            for block in ast.walk(node)
            if isinstance(block, ast.Try)
            and any(
                isinstance(h.type, ast.Name) and h.type.id == "BrokenPipeError"
                for h in block.handlers
            )
            for stmt in block.body
            for inner in ast.walk(stmt)
        }
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or id(call) in nested | in_try_body:
                continue
            name = getattr(call.func, "id", "") or getattr(call.func, "attr", "")
            if name not in WRITE_CALLS:
                continue
            stream = (
                "stdout"
                if name == "print" and not any(kw.arg == "file" for kw in call.keywords)
                else "stderr"
            )
            found.append(
                f"{node.name}:{call.lineno} `{name}(...)` writes to {stream} outside a guard "
                f"— a closed pipe there raises past the verdict (use emit()/warn(), or wrap it "
                f"in `except BrokenPipeError` and return the code it was carrying)"
            )
    return found


def _t12_verdict_survives_a_closed_pipe() -> List[str]:
    """A closed pipe must lose the REPORT, never the VERDICT.

    Two halves. Behavioural: the guard swallows a BrokenPipeError on BOTH streams and returns,
    so the caller reaches its own `return EXIT_*`. Structural: every verdict-carrying function
    — derived by `verdict_functions`, never a hardcoded prefix — must route EVERY write through
    a guard: stdout through the nested `report` it hands to `emit`, stderr through `warn` (or a
    lambda handed to `_guarded`). `file=sys.stderr` is NOT an exemption; that assumption is what
    left the stderr door open after the stdout one was shut, and `velocity`'s unmeasured path
    reports only to stderr, so that door was the whole verdict.
    """
    bad = []

    def raiser() -> None:
        raise BrokenPipeError(32, "Broken pipe")

    saved = os.dup(sys.stdout.fileno())
    try:
        emit(raiser)
    except BrokenPipeError:
        bad.append(
            "emit() let a BrokenPipeError past — the verdict would be overwritten"
        )
    finally:
        os.dup2(saved, sys.stdout.fileno())
        os.close(saved)
    printed: List[str] = []
    emit(lambda: printed.append("ran"))
    if printed != ["ran"]:
        bad.append("emit() stopped running the report at all")
    saved_err = os.dup(sys.stderr.fileno())
    try:
        _guarded(raiser, sys.stderr)
    except BrokenPipeError:
        bad.append("the stderr guard let a BrokenPipeError past")
    finally:
        os.dup2(saved_err, sys.stderr.fileno())
        os.close(saved_err)

    tree = ast.parse(SELF_PATH.read_text(errors="replace"))
    carriers = verdict_functions(tree)
    for expected in ("cmd_collect", "cmd_velocity", "selftest", "body"):
        if expected not in carriers:
            bad.append(
                f"{expected}() is no longer recognised as verdict-carrying — the derivation "
                f"broke, so this check is now blind: {carriers}"
            )
    bad.extend(unguarded_writes(tree, carriers))

    # PIN THE PREDICATE ITSELF. "Guarded" means the write sits in the TRY BODY of a `try` whose
    # handlers include BrokenPipeError — NOT in the handler, where only the recovered return
    # belongs. Defining it the other way round inverts the check: it reports correct code as
    # unguarded and would wave through the real thing. (Exact mistake made and caught by
    # GitHubEcosystemMiner 2026-09-11 while auditing his own file; cheap to make, invisible
    # without a probe, so the probe is permanent.)
    good_probe = (
        "def probe_ok() -> int:\n"
        "    try:\n"
        "        ap.parse_args()\n"
        "    except BrokenPipeError:\n"
        "        return EXIT_USAGE\n"
        "    return EXIT_OK\n"
    )
    bad_probe = (
        "def probe_bad() -> int:\n"
        "    try:\n"
        "        pass\n"
        "    except BrokenPipeError:\n"
        "        print('a write in the HANDLER is not a guarded write')\n"
        "    return EXIT_USAGE\n"
    )
    if unguarded_writes(ast.parse(good_probe), ["probe_ok"]):
        bad.append(
            "the guard predicate is INVERTED: a write inside the try body read as unguarded"
        )
    if not unguarded_writes(ast.parse(bad_probe), ["probe_bad"]):
        bad.append(
            "a write inside the except handler read as guarded — the predicate keys on the "
            "handler instead of the try body"
        )
    return bad


def _t13_cancellation_code_survives_a_closed_stderr() -> List[str]:
    """A BrokenPipeError raised while REPORTING a cancellation must not eat the signal.

    In-process, on the exact exception shape the spine produces: raise a BrokenPipeError from
    inside an `except Cancelled` handler, which is what `print(..., file=sys.stderr)` does when
    stderr is a closed pipe, and check the code is recovered from `__context__`. The end-to-end
    half is measured with a real closed pipe (see `recover_cancellation`); this leg is what
    stops a refactor from quietly dropping the recovery.
    """
    bad = []
    for signum, want in ((2, 130), (15, 143)):
        try:
            try:
                raise Cancelled(signum)
            except Cancelled:
                raise BrokenPipeError(32, "Broken pipe")
        except BrokenPipeError as exc:
            got = recover_cancellation(exc)
            if got != want:
                bad.append(f"signal {signum}: recovered {got}, want {want}")
    # ...and it must NEVER invent a code for an EPIPE that is not a cancellation.
    if recover_cancellation(BrokenPipeError(32, "Broken pipe")) is not None:
        bad.append("a bare BrokenPipeError was treated as a cancellation")
    try:
        try:
            raise ValueError("something else entirely")
        except ValueError:
            raise BrokenPipeError(32, "Broken pipe")
    except BrokenPipeError as exc:
        if recover_cancellation(exc) is not None:
            bad.append("a non-Cancelled context was treated as a cancellation")
    return bad


SELFTESTS: Tuple[Tuple[str, Callable[[], List[str]]], ...] = (
    ("t1 sitemap parse: loc/lastmod pairing, no-date page, flat fallback", _t1_sitemap),
    ("t2 velocity: 1d/7d/30d buckets, newest, cadence decision", _t2_buckets),
    ("t3 stable ids: identical across runs and across clocks", _t3_id_stability),
    ("t4 changelog: h1 over h3, permalink, double-render dedup", _t4_changelog),
    ("t5 blog: title from <p>, permalink from enclosing <a>", _t5_blog),
    ("t6 refusal: 403 recorded, ZERO rows fabricated", _t6_refusal),
    (
        "t7 rows carry no volatile field; stable fingerprint holds",
        _t7_no_volatile_fields,
    ),
    ("t8 canary + dead network: unmeasured is never zero", _t8_canary),
    ("t9 dates: ISO-with-Z, millis, human text, garbage", _t9_date_fallback),
    (
        "t10 collect() cannot write: one writer, no root, AST-enforced",
        _t10_collect_cannot_write,
    ),
    (
        "t11 cancellation unwinds instead of becoming a recorded failure",
        _t11_cancellation_propagates,
    ),
    (
        "t12 a closed pipe loses the report, never the verdict",
        _t12_verdict_survives_a_closed_pipe,
    ),
    (
        "t13 a closed stderr cannot eat a cancellation's exit code",
        _t13_cancellation_code_survives_a_closed_stderr,
    ),
)


def selftest() -> int:
    """Run every proof, THEN report. The order is the point.

    `body()` returns this value, so `--selftest` is a verdict-carrying path \u2014 and a verdict
    -carrying path that prints outside `emit` can have its verdict erased by a closed pipe:
    `gbtypes.main`'s EPIPE arm exits 0 unconditionally. Measured 2026-09-11 with one leg forced
    to fail: unpiped -> rc 3, `| head -1` -> rc 0 with empty stderr. A BROKEN instrument
    reporting healthy to any hook or CI step that pipes it, which is the single worst version of
    this class here \u2014 AGENTS.md's session-start SOP is "if the selftest is not N/N the
    instrument is broken". (Class raised by GitHubEcosystemMiner 2026-09-11, who hit it in his
    own `body -> selftest` path; note a grep for `return 1` never finds it, because the verdict
    is carried by a CALL.)

    So: every leg runs first, the verdict is computed from all of them, the printing happens
    inside `emit`, and the pre-computed code is returned. A closed pipe loses the rows; it can
    never turn a FAIL into a pass, and it can never stop a later leg from running.
    """
    rows: List[Tuple[str, List[str]]] = []
    for name, fn in SELFTESTS:
        try:
            problems = fn()
        except Cancelled:
            # A cancelled selftest is not a failing selftest. Let it unwind to gbtypes.main,
            # which exits 130/143 instead of printing a FAIL row that nobody signalled for.
            raise
        except Exception as exc:  # noqa: BLE001 — a raising proof is a failing proof
            problems = [f"{type(exc).__name__}: {exc}"]
        rows.append((name, problems))
    passed = sum(1 for _, problems in rows if not problems)
    total = len(rows)
    code = EXIT_OK if passed == total else EXIT_ERROR

    def report() -> None:
        for name, problems in rows:
            if problems:
                print(f"  [FAIL] {name}")
                for problem in problems:
                    print(f"         {problem}")
            else:
                print(f"  [PASS] {name}")
        print(f"SELFTEST {'PASS' if passed == total else 'FAIL'} - {passed}/{total}")

    emit(report)
    return code


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
def _guarded(report: Callable[[], None], stream: Any) -> None:
    """Run a report against `stream`, and NEVER let a closed pipe overwrite this command's
    verdict.

    `gbtypes.main` catches `BrokenPipeError` and exits 0 UNCONDITIONALLY. That is right for a
    producer whose exit code carries no verdict, and wrong here: `collect` exits 3 when the
    velocity instrument is unmeasured or shrunken, and 1 on findings.

    Measured 2026-09-11, each door found only after the previous one was shut:

        collect --dry-run --min-locs 100000                       -> rc 3   correct
        collect --dry-run --min-locs 100000 | head -1              -> rc 0   FALSE GREEN (stdout)
        collect --dry-run --min-locs 100000 2>&1 | head -1         -> rc 0   FALSE GREEN (stderr)
        velocity --root <empty> --json 2>&1 | true                 -> rc 0   FALSE GREEN (stderr)
        --selftest with one leg failing | head -1                  -> rc 0   FALSE GREEN (verdict
                                                                             carried by a CALL)

    The stderr door is the nastier one: `velocity`'s unmeasured path reports ONLY to stderr, so
    with both streams pointed at a closed pipe there was nothing left to break except the
    verdict. Both streams are therefore guarded by this one function, which swallows the pipe,
    points the fd at /dev/null (killing the interpreter's shutdown complaint and making later
    writes harmless) and RETURNS so the caller reaches its own `return EXIT_*`.

    PRECONDITION, written down because it is what makes a local guard non-redundant next to the
    spine's: this producer's exit code carries a verdict. If that ever stops being true these
    guards may go — and if a verdict is added to a path that writes outside them, the guard must
    come with it. Selftest t12 enforces that half over the AST, against a DERIVED set of
    verdict-carrying functions rather than a hand-kept list. (Class raised by
    FeedsAndPractitionerMiner, relayed and re-measured with GitHubEcosystemMiner, 2026-09-11.)
    """
    try:
        report()
        stream.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), stream.fileno())


def emit(report: Callable[[], None]) -> None:
    """The stdout half of `_guarded`: a command's report."""
    _guarded(report, sys.stdout)


def warn(message: str) -> None:
    """The stderr half of `_guarded`: a diagnostic that must not cost the verdict."""
    _guarded(lambda: print(message, file=sys.stderr), sys.stderr)


def cmd_collect(ns: argparse.Namespace) -> int:
    now = parse_ts(ns.now) or utcnow()
    payload, problems = collect(http_fetch, now, min_locs=ns.min_locs)
    root = pathlib.Path(ns.root).resolve()
    written: Optional[pathlib.Path] = None
    if not ns.dry_run:
        folder = root / ARTIFACT_ROOT
        folder.mkdir(parents=True, exist_ok=True)
        written = folder / f"{stamp_of(now)}.json"
        atomic_write_json(written, payload)

    def report() -> None:
        if ns.json:
            print(json.dumps(payload, indent=1))
        else:
            render(payload)
            print(
                f"  wrote      {written}"
                if written
                else "  wrote      nothing (--dry-run)"
            )

    emit(report)
    if payload["velocity"]["pages_total"] == 0 or any(
        "floor" in p or "ZERO <loc>" in p for p in problems
    ):
        warn(
            "gb-sources: ERROR — the velocity instrument is unmeasured or shrunken; do not "
            "record this as a quiet day"
        )
        return EXIT_ERROR
    return EXIT_FINDINGS if problems else EXIT_OK


def cmd_velocity(ns: argparse.Namespace) -> int:
    root = pathlib.Path(ns.root).resolve()
    if ns.fetch:
        payload, _ = collect(http_fetch, utcnow(), min_locs=ns.min_locs)
        vel = payload["velocity"]
        vel["read_from"] = "live fetch"
    else:
        newest = newest_artifact(root)
        if newest is None:
            warn(
                f"gb-sources: no artifact under {root / ARTIFACT_ROOT}/ — velocity is "
                f"UNMEASURED, not zero.\n    run: python3 bin/gb-sources.py collect\n"
                f"    or:  python3 bin/gb-sources.py velocity --fetch"
            )
            return EXIT_ERROR
        doc = read_json_capped(newest)
        vel = (doc or {}).get("velocity") if isinstance(doc, dict) else None
        if not isinstance(vel, dict):
            warn(
                f"gb-sources: {newest} carries no velocity object — it was written by an older "
                f"schema or is truncated. re-run: python3 bin/gb-sources.py collect"
            )
            return EXIT_ERROR
        vel = dict(vel)
        vel["read_from"] = (
            str(newest.relative_to(root))
            if newest.is_relative_to(root)
            else str(newest)
        )

    def report() -> None:
        if ns.json:
            print(json.dumps(vel, indent=1))
        else:
            print(
                f"{vel['pages_total']} docs pages · {vel['changed_1d']} <=1d · "
                f"{vel['changed_7d']} <=7d · {vel['changed_30d']} <=30d · "
                f"churn7d {vel['churn_7d_pct']}% · cadence {vel['cadence_justified']} · "
                f"newest {vel['newest_lastmod']} · from {vel['read_from']}"
            )

    emit(report)
    return EXIT_OK


def body() -> int:
    ap = argparse.ArgumentParser(
        prog="gb-sources",
        description=SUMMARY,
        epilog=(
            "exit: 0 measured · 1 findings (a non-primary source refused) · 2 usage · "
            "3 unmeasured/ERROR"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="prove the parsers on inline fixtures; no network",
    )
    sub = ap.add_subparsers(dest="command", metavar="<command>")

    collect_p = sub.add_parser(
        "collect", help="fetch every vendor surface and write sources/<stamp>.json"
    )
    collect_p.add_argument(
        "--root", default=str(ROOT), help="artifact root (default: repo)"
    )
    collect_p.add_argument("--json", action="store_true", help="the artifact on stdout")
    collect_p.add_argument(
        "--dry-run", action="store_true", help="fetch but write nothing"
    )
    collect_p.add_argument(
        "--now", default=None, help="ISO-8601 instant to age against"
    )
    collect_p.add_argument(
        "--min-locs",
        type=int,
        default=MIN_SITEMAP_LOCS,
        help=f"canary floor for the docs index (default {MIN_SITEMAP_LOCS})",
    )

    vel_p = sub.add_parser("velocity", help="the velocity object alone, for the tick")
    vel_p.add_argument(
        "--root", default=str(ROOT), help="artifact root (default: repo)"
    )
    vel_p.add_argument("--json", action="store_true", help="JSON instead of one line")
    vel_p.add_argument(
        "--fetch",
        action="store_true",
        help="recompute live instead of reading the artifact",
    )
    vel_p.add_argument(
        "--min-locs", type=int, default=MIN_SITEMAP_LOCS, help=argparse.SUPPRESS
    )

    # ARGPARSE IS A WRITER ON MY BEHALF, and it carries a verdict. On a bad flag it prints the
    # usage to stderr itself and raises SystemExit(2) — but if stderr is a closed pipe, the
    # write raises BrokenPipeError from INSIDE argparse, the SystemExit never happens, and
    # gbtypes.main's unconditional EPIPE arm turns a typo into exit 0. Measured 2026-09-11:
    # `--bogus` unpiped -> rc 2; `--bogus 2>&1 | true` -> rc 0; same for a bad verb and a bad
    # `--min-locs` type. "I grepped for my own prints" is not an audit of what a process WRITES
    # (class raised by GitHubEcosystemMiner 2026-09-11, who had claimed zero stderr callsites
    # and then found argparse writing for him).
    #
    # The code has to be RECOVERED from argv because argparse never reaches its own exit: a help
    # request is a successful invocation, anything else argparse rejected is a usage error.
    try:
        ns = ap.parse_args()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stderr.fileno())
        argv = sys.argv[1:]
        return EXIT_OK if ("-h" in argv or "--help" in argv) else EXIT_USAGE
    if ns.selftest:
        return selftest()
    if ns.command == "collect":
        return cmd_collect(ns)
    if ns.command == "velocity":
        return cmd_velocity(ns)
    _guarded(lambda: ap.print_help(sys.stderr), sys.stderr)
    return EXIT_USAGE


def recover_cancellation(exc: BrokenPipeError) -> Optional[int]:
    """The exit code a BrokenPipeError STOLE from a cancellation, or None if it stole nothing.

    `gbtypes.main` reports a cancellation with `print(..., file=sys.stderr)` INSIDE its
    `except Cancelled` arm, and its `except BrokenPipeError` arm is a SIBLING — so when stderr
    is also a closed pipe, that report raises, nothing catches it, and the exit code becomes 1.
    Measured 2026-09-11 with a real closed pipe on fd 1 AND fd 2 (a pipe whose read end is shut,
    not a fifo, to avoid the open-blocking race): SIGINT -> rc 1, SIGTERM -> rc 1, zero
    artifacts. It fails SAFE (a cancellation never reads as success) so it is a fidelity defect,
    not a correctness one — but a daily scheduled job cannot tell "the operator stopped it" from
    "it broke", which is the same conflation this repo exists to refuse.

    The fix belongs in the spine, which is not this agent's file. This is the local recovery:
    Python sets `__context__` to the exception being handled when a new one is raised, so a
    BrokenPipeError whose `__context__` IS the `Cancelled` carries the signal number that was
    being reported. Anything else returns None and is re-raised \u2014 inventing a code for an
    unexplained EPIPE would be exactly the dishonesty being deleted here. (Recovery technique
    from FeedsAndPractitionerMiner via GitHubEcosystemMiner, 2026-09-11; pinned by t13.)
    """
    context = exc.__context__
    return context.exit_code if isinstance(context, Cancelled) else None


def run_cli() -> None:
    """`gbtypes.main(body)` plus the one thing it cannot do for itself."""
    try:
        main(body)
    except BrokenPipeError as exc:
        code = recover_cancellation(exc)
        if code is None:
            raise
        for stream in (sys.stdout, sys.stderr):
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), stream.fileno())
            except OSError:
                pass
        raise SystemExit(code) from None


if __name__ == "__main__":
    run_cli()
