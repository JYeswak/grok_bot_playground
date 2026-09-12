#!/usr/bin/env python3
"""gb-x — the DAILY X/Twitter read for the Grok Bot deployment, and the LINK FIREHOSE behind it.

The other three daily producers each read a surface somebody else curates: `gb-sources.py` reads
the vendor's own sitemap, `gb-github.py` reads GitHub's search index, `gb-feeds.py` reads nine
allowlisted feeds. All three are therefore blind in the same direction — they only see what a
publisher chose to publish. The practitioner corpus in `usecases/` is 645 rows of Grok Bot use
cases, and EVERY ONE of them was sourced from an x.com status. That is where this product is
actually discussed, hours before any of it reaches a blog.

So this file reads X. And the point is not the posts: it is the URLs INSIDE the posts.

  MEASURED 2026-09-11, first run of this file, `"grok bot" has:links -is:retweet`, 10 results:
  grokbotjobs.com (x3), grokbot.money, grokbot.sh, x.ai/bot/guides, x.ai/bot/<share-id>,
  github.com/dhurv0045com-spec/An-Ra-the-new-AGI (x2), alook.ai.

  Not one of those eight hosts is in `sources.json`, in the nine feeds, or reachable from the
  GitHub queries. Six of the eight are Grok Bot ecosystem sites that no other producer in this
  repo can see at all. The link harvest is not a bonus on top of the post read — it is the
  reason the post read is worth spending quota on.

HOW THE INSTRUMENT ACTUALLY BEHAVES (measured, not assumed — four probes, `x-cli` source read):

  (1) `x-cli -j tweet search Q --max N` prints a BARE LIST of posts: `output_json` in
      `x_cli/formatters.py:15` strips `includes` and `meta` unless `--verbose` is set. Adding
      `-v` turns the same call into the FULL envelope — `{data, includes, meta}` — which carries
      `includes.users[]` (so author HANDLES become available, not just opaque `author_id`s) and
      `meta.result_count` / `meta.next_token`. Same request, same quota, strictly more evidence.
      This file always sends `-j -v`. A producer that had taken the documented `-j` shape at face
      value would have emitted 100% of its rows with `author: null`.

  (2) There is NO total-count anywhere. X's v2 recent-search endpoint returns `meta.result_count`
      — how many it HANDED BACK — and a `next_token`. The "N matches exist" number that
      `gb-github.py` records as `total_count` has no equivalent here; the count endpoint that
      would provide it is not exposed by this CLI. `sources[].items` is therefore RETURNED, never
      MATCHED, and `has_more` records that a next page existed and was not taken.

  (3) There is no rate-limit endpoint to probe. `x_cli/api.py:46` turns HTTP 429 into
      `RuntimeError("Rate limited. Resets at <epoch>.")` and every other non-2xx into
      `API error (HTTP nnn)`. So the quota is INVISIBLE UNTIL IT IS HIT, and the only honest
      guard is a local one. This file counts its own calls and enforces the cap itself
      (`MAX_SEARCH_CALLS`, `MIN_SLEEP_S`) — deliberately not trusting any vendor number, because
      GitHubEcosystemMiner measured a vendor quota field that read `remaining=30/used=0` both
      BEFORE and AFTER 13 calls. A guard whose instrument is stuck is worse than no guard: it
      reports itself green. Ours cannot be stuck, because we are the one counting.

  (4) `--max` clamps to 10..100 (`api.py:104`) and pagination is not reachable: the CLI accepts no
      `next_token`. One call per query, up to 100 posts. Recorded, not worked around.

READ-ONLY, STRUCTURALLY. `x-cli` can post, reply, like, retweet, delete and bookmark from
Joshua's real account. This file builds exactly one argv shape, `ARGV_PREFIX + [query, --max, n]`,
where `ARGV_PREFIX` ends in `("tweet", "search")`, and `selftest` walks this module's own AST to
assert that no string constant anywhere in the file names a mutating verb in an argv position.
The guard is checked on every run rather than promised in this paragraph.

SECRETS. Credentials come from Infisical via the `x-cli-infisical` wrapper and are never read,
printed or stored by this file. Child stderr is truncated AND scrubbed (`scrub`) before it reaches
an artifact, so a wrapper that one day echoes a token cannot leak it into `x/<stamp>.json`.

Three rules inherited from AGENTS.md and from the other three producers:

  (1) A refusal is a recorded FACT. A 429, a 401, a missing binary and a timeout each land in
      `sources[]` with a status and the run still writes an artifact. Silence rendered as
      `"rows": []` reads as "X was quiet today", which would be a lie.
  (2) An empty result is not absence until a POSITIVE CONTROL passes. `control-positive` (`grok`,
      the broadest possible term) MUST return rows; if it returns none the search path is broken,
      `reliable` goes false, and no zero in the artifact may be read as absence. A second,
      NEGATIVE control (`-has:links`) must harvest ZERO urls — it proves the extractor is reading
      entities rather than inventing links, which a positive control alone cannot show.
  (3) No row carries a clock-derived field. `signals.age_days` is the single exception, and it is
      named in `volatile_fields` so the content-addressed store strips it before hashing. A
      sibling paid 204 blobs in one day to learn what getting this wrong costs.

  gb-x.py collect            # write x/<stamp>.json, print the summary
  gb-x.py collect --json     # the document on stdout
  gb-x.py collect --dry-run  # spend the quota, write nothing
  gb-x.py --selftest         # inline captured fixtures, no network
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import Cancelled, atomic_write_json, main, run  # noqa: E402

SCHEMA = "gb-x/1"

# The ONE volatile field. Everything else in a row is a fact about the post or the link, and two
# runs an hour apart must produce byte-identical rows once this is stripped — `selftest` leg
# `rows-are-clock-independent` proves it by collecting twice with different `now`.
VOLATILE_FIELDS = ["signals.age_days"]

SEARCH_ENDPOINT = "https://api.x.com/2/tweets/search/recent"

# The local budget. This is the binding guard — see docstring note (3).
MAX_SEARCH_CALLS = 20  # hard ceiling on requests per run, enforced by our own counter
MAX_RESULTS = 100  # the CLI's own ceiling (api.py:104)
MIN_SLEEP_S = 2.0  # politeness floor between calls; 20 calls ≈ 40s of sleep
X_TIMEOUT_S = 45.0  # a 100-result search measured ~6-8s; 45 is a deadline, not a target
X_MAX_BYTES = 12_000_000  # 100 posts with full entities measured ~120KB; 100x headroom

X_CLI = "x-cli-infisical"
# Every X call this file can make. The verb is FROZEN here, at module scope, so the read-only
# property is one line to check rather than a promise spread over the file. `-v` is load-bearing:
# without it the CLI strips `includes.users` and `meta` (see docstring note (1)).
ARGV_PREFIX = (X_CLI, "-j", "-v", "tweet", "search")
# Verbs that would take a real-world action from Joshua's account. Asserted absent from every
# argv-shaped string constant in this module, every run.
FORBIDDEN_VERBS = (
    "post",
    "reply",
    "quote",
    "like",
    "retweet",
    "delete",
    "bookmark",
    "unlike",
)

# --- url canonicalisation -------------------------------------------------------------------
# Shared verbatim with gb-feeds.py (`canon_url`, bin/gb-feeds.py:243) and gb-github.py
# (`stable_id`, bin/gb-github.py:175) so that ONE downstream store keys all four producers the
# same way: a github.com repo url cited in a post hashes to the row `gb-github` already wrote.
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
    "s",  # x.com's own share parameter: `?s=20` / `?s=46`
    "t",  # x.com's share token
}
# Every host X serves the SAME post from. All four fold to `x.com` in `canon_url`, for the reason
# gb-feeds.py:249 folds `www.`: two spellings of one resource must not be two rows. `nitter.net`
# is deliberately NOT here — it is a third-party mirror with its own availability, so a nitter
# link is an ordinary web link and is recorded as one.
X_HOSTS = {"x.com", "twitter.com", "mobile.x.com", "mobile.twitter.com"}
# `/i/status/N`, `/i/web/status/N`, `/<handle>/status/N`, `/<handle>/statuses/N`.
#
# The first segment is `[A-Za-z0-9_]+` and NOT `{1,15}` (the real length limit for a handle),
# because X ALSO renders the permalink with the author's NUMERIC USER ID in the handle slot.
# MEASURED on the first live run of this file: three rows survived as unfolded shadows —
# `https://twitter.com/1483409796531625987/status/2098306148923474360` is 19 digits where a
# handle would be, so a handle-shaped pattern skipped it and the same post would have been two
# rows under two spellings. The `/status/<digits>` suffix is what actually identifies a
# permalink; the segment before it is not worth constraining.
X_STATUS_RE = re.compile(
    r"^/(?:i/(?:web/)?status(?:es)?|[A-Za-z0-9_]+/status(?:es)?)/(\d+)"
)
# `.../status/N/photo/1`, `.../video/2` — a rendering of the post's own media, not a cited link.
X_MEDIA_RE = re.compile(r"/(?:photo|video|gif)/\d+/?$")
TCO_HOSTS = {"t.co"}
# Deliberately conservative: stops at whitespace and at the closing punctuation that wraps a url
# in prose. Used ONLY when a post carries no url entities at all (see `harvest_urls`).
URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]{}]+")
URL_TRAILING = ".,;:!?'\"”’»)]}>"
# Any run of 40+ token-shaped characters in child stderr is treated as a possible credential and
# replaced by its length. Cheap, and the only version of this that cannot leak.
SECRET_RE = re.compile(r"[A-Za-z0-9%_\-\.]{40,}")

# --- the query matrix -----------------------------------------------------------------------
# Exactly `MAX_SEARCH_CALLS` entries, so the matrix IS the budget and adding a query forces a
# deliberate choice about which one it replaces. Every entry states what it is FOR: a query
# nobody can justify is noise, and noise teaches the reader to skip the artifact.
#
# `-is:retweet` on all of them — a retweet carries the same entities as its parent and would
# inflate `citations` without adding a single distinct url.
#
# Practitioner seeds are the MEASURED top contributors of the local `usecases/` corpus (645 rows,
# `collections.Counter` over `contributor`): LBallz77283 63, ericzakariasson 58, elie2222 25,
# scheemunai 18, liam_fallen 15, kristaletz 14, BotDirectoryAI 12, mvanhorn 11, benln 9,
# lennysan 9, farzyness 8, Voxyz_ai 8, shubgaur 7, mattyp 7, coreyganim 6. They are batched three
# to a call with `OR` because each `from:` alone would cost a request out of twenty.
QUERIES: tuple = (
    # -- controls, FIRST, so reliability is known even if the run dies on call two -------------
    {
        "label": "control-positive",
        "q": "grok -is:retweet",
        "max": 10,
        "role": "control-positive",
        "why": "the broadest term that must match; zero here means the search path is broken",
    },
    {
        "label": "control-no-links",
        "q": '("grok bot" OR grokbot) -has:links -is:retweet',
        "max": 10,
        "role": "control-negative",
        "why": "posts X says have no links; the extractor must harvest ZERO urls from them",
    },
    # -- the link firehose: the reason this producer exists ------------------------------------
    {
        "label": "grok-bot-links",
        "q": '"grok bot" has:links -is:retweet',
        "max": MAX_RESULTS,
        "role": "harvest",
        "why": "the primary outbound-url source; measured 10/10 posts carried an expanded url",
    },
    {
        "label": "grokbot-links",
        "q": "grokbot has:links -is:retweet",
        "max": MAX_RESULTS,
        "role": "harvest",
        "why": "the unhyphenated spelling, which the ecosystem sites themselves use",
    },
    {
        "label": "grok-bot-vendor-links",
        "q": '("grok bot" OR grokbot) (x.ai OR github.com OR cursor.com) -is:retweet',
        "max": MAX_RESULTS,
        "role": "harvest",
        "why": "posts naming a vendor or repo host: the highest-value links, by construction",
    },
    # -- topical discourse --------------------------------------------------------------------
    {
        "label": "grok-bot-plain",
        "q": '"grok bot" -is:retweet',
        "max": MAX_RESULTS,
        "role": "topical",
        "why": "the discourse census; most rows carry no link and that is the point of comparison",
    },
    {
        "label": "grok-bot-mcp",
        "q": '"grok bot" mcp -is:retweet',
        "max": MAX_RESULTS,
        "role": "topical",
        "why": "Bots wired to MCP — the integration pattern, and the connector urls behind it",
    },
    {
        "label": "grok-bot-routine",
        "q": '"grok bot" (routine OR routines OR scheduled) -is:retweet',
        "max": MAX_RESULTS,
        "role": "topical",
        "why": "the unattended-execution surface this deployment is actually built on",
    },
    {
        "label": "grok-bot-skill",
        "q": '"grok bot" (skill OR skills OR instructions) -is:retweet',
        "max": MAX_RESULTS,
        "role": "topical",
        "why": "skill/instruction authoring: where practitioners publish reusable prompt assets",
    },
    {
        "label": "grok-bot-template",
        "q": '"grok bot" (template OR templates OR "use case") -is:retweet',
        "max": MAX_RESULTS,
        "role": "topical",
        "why": "the Bot-template economy the usecases corpus is a snapshot of",
    },
    {
        "label": "grok-bot-trouble",
        "q": '("grok bot" OR grokbot) (broken OR fails OR failing OR error OR bug) -is:retweet',
        "max": MAX_RESULTS,
        "role": "topical",
        "why": "failure discourse: the earliest warning this deployment gets about a regression",
    },
    {
        "label": "grok-bot-plans",
        "q": '("grok bot" OR grokbot) (pricing OR plan OR plans OR quota OR limits) -is:retweet',
        "max": MAX_RESULTS,
        "role": "topical",
        "why": "access and quota changes, which move without a changelog entry",
    },
    # -- vendor -------------------------------------------------------------------------------
    {
        "label": "vendor-xai",
        "q": "from:SpaceXAI -is:retweet",
        "max": MAX_RESULTS,
        "role": "vendor",
        "why": "the platform's own account: announcements land here before x.ai/news",
    },
    {
        "label": "vendor-cursor",
        "q": "(from:cursor_ai OR from:cursor) -is:retweet",
        "max": MAX_RESULTS,
        "role": "vendor",
        "why": "the Cursor side of the product; two candidate handles, one call",
    },
    {
        "label": "vendor-mentions",
        "q": '(@SpaceXAI OR @cursor_ai) ("grok bot" OR grokbot) -is:retweet',
        "max": MAX_RESULTS,
        "role": "vendor",
        "why": "what users are telling the vendor about the product, links included",
    },
    # -- practitioners (measured seeds, batched) ----------------------------------------------
    {
        "label": "prac-top3",
        "q": "(from:LBallz77283 OR from:ericzakariasson OR from:elie2222) -is:retweet",
        "max": MAX_RESULTS,
        "role": "practitioner",
        "why": "146 of the 645 corpus rows came from these three accounts",
    },
    {
        "label": "prac-next3",
        "q": "(from:scheemunai OR from:liam_fallen OR from:kristaletz) -is:retweet",
        "max": MAX_RESULTS,
        "role": "practitioner",
        "why": "47 corpus rows; consistent publishers of full Bot prompts",
    },
    {
        "label": "prac-dir3",
        "q": "(from:BotDirectoryAI OR from:mvanhorn OR from:benln) -is:retweet",
        "max": MAX_RESULTS,
        "role": "practitioner",
        "why": "32 corpus rows, and BotDirectoryAI is itself a link aggregator",
    },
    {
        "label": "prac-more3",
        "q": "(from:lennysan OR from:farzyness OR from:Voxyz_ai) -is:retweet",
        "max": MAX_RESULTS,
        "role": "practitioner",
        "why": "25 corpus rows; the commentary end of the practitioner set",
    },
    {
        "label": "prac-tail3",
        "q": "(from:shubgaur OR from:mattyp OR from:coreyganim) -is:retweet",
        "max": MAX_RESULTS,
        "role": "practitioner",
        "why": "20 corpus rows; the tail of the measured seed list",
    },
)


# -------------------------------------------------------------------------------------------
# pure helpers
# -------------------------------------------------------------------------------------------
def now_z(when: Optional[dt.datetime] = None) -> str:
    d = when or dt.datetime.now(dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp(when: dt.datetime) -> str:
    return "{:%Y-%m-%dT%H%M}".format(when.astimezone(dt.timezone.utc))


def scrub(text: str, *, limit: int = 240) -> str:
    """Child stderr, made safe to write into an artifact.

    The wrapper resolves five credentials from Infisical. It has never been observed to echo one,
    but "has never been observed" is not a property — so any 40+ character token-shaped run is
    replaced by its LENGTH before the string can reach `sources[]`. Length is diagnostic (a
    116-char value is the bearer token, a 45-char one is the access secret) and is not the secret.
    """
    one_line = " ".join((text or "").split())
    safe = SECRET_RE.sub(lambda m: "<redacted:%d chars>" % len(m.group(0)), one_line)
    return safe[:limit]


def canon_url(raw: Optional[str]) -> str:
    """One url string per document, so four producers that saw it differently agree on the id.

    Drops the fragment and referral parameters (including x.com's own `?s=`/`?t=` share tokens),
    sorts what survives, normalises the trailing slash, lowercases scheme and host, folds
    `http` → `https`, and strips a leading `www.` — identical to `gb-feeds.py:243`.

    Then ONE extra fold this producer needs and the others do not: every x.com/twitter.com status
    permalink collapses to `https://x.com/i/status/<id>`. X hands out four spellings of the same
    post (`/i/status/N`, `/i/web/status/N`, `/<handle>/status/N`, and `twitter.com/...`), and a
    post quoted by two people arrives under two of them. Without the fold, the same tweet is
    three rows; with it, a cited status that we ALSO fetched merges into its own `x-post` row and
    the citation count lands on the post rather than on a shadow copy of it.

    Cost of the fold: the handle is discarded from the url. It is not lost — it is recorded in
    `signals.author`/`signals.cited_by_authors`, where it belongs, since a handle can be renamed
    while the numeric id cannot.
    """
    if not raw:
        return ""
    p = urllib.parse.urlsplit(raw.strip())
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host in X_HOSTS:
        m = X_STATUS_RE.match(p.path)
        if m and not X_MEDIA_RE.search(p.path):
            return "https://x.com/i/status/%s" % m.group(1)
        # Not a permalink (a profile, an Article, a broadcast) — still the same site under four
        # names, so the host folds and the path is left alone.
        host = "x.com"
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in DROP_PARAMS and not k.lower().startswith("utm_")
    ]
    return urllib.parse.urlunsplit(
        (
            "https" if p.scheme.lower() in ("", "http") else p.scheme.lower(),
            host,
            p.path.rstrip("/") or "/",
            urllib.parse.urlencode(sorted(kept)),
            "",
        )
    )


def content_id(url: str) -> str:
    """`blake2b(canonical_url.lower(), digest_size=16)` → 32 hex. The convention agreed with
    `gb-github`, `gb-feeds` and `gb-sources` on 2026-09-11, so that a url seen here and a url seen
    there are ONE row in the store. The `kind` is deliberately not in the hash: two producers
    seeing the same document must collide."""
    return hashlib.blake2b(
        canon_url(url).lower().encode("utf-8"), digest_size=16
    ).hexdigest()


def permalink(tweet_id: str) -> str:
    """The handle-independent spelling of a post url. Chosen over `x.com/<handle>/status/<id>`
    because a post found by a topical query has no handle attached (see docstring note (1) — even
    with `-v`, `includes.users` can be short) and the same post found by a `from:` query does.
    Two spellings would be two ids for one post."""
    return "https://x.com/i/status/%s" % str(tweet_id).strip()


def parse_created(value: Optional[str]) -> Optional[dt.datetime]:
    """X's `2026-09-11T17:13:09.000Z`. Anything unparseable answers None rather than raising — a
    malformed date on one post must not abort a run that has already spent quota."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def created_z(value: Optional[str]) -> Optional[str]:
    when = parse_created(value)
    return now_z(when) if when else None


def age_days(created: Optional[str], now: dt.datetime) -> Optional[int]:
    when = parse_created(created)
    if when is None:
        return None
    return max(0, (now.astimezone(dt.timezone.utc) - when).days)


def classify_link(url: str) -> "tuple[str, str]":
    """`(verdict, link_kind)`. `verdict` is `keep`, `drop-tco` or `drop-media`.

    Two things are dropped rather than emitted, because a row nobody can follow is worse than no
    row — it inflates the count and costs a reader a click to learn nothing:

      `drop-tco`   — a `t.co` shortener X did not expand. The target is unknowable from here, and
                     `https://t.co/ZIwbyTgUaT` as a "discovered url" is a lie about coverage.
      `drop-media` — `.../status/N/photo/1`. That is the post's own attached image, rendered as a
                     link by X's entity extractor. It is not something the author cited.

    Both are COUNTED (`counts.tco_dropped`, `counts.media_dropped`), so the drop is visible as a
    number rather than as a silently shorter list.
    """
    host = urllib.parse.urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host in TCO_HOSTS:
        return "drop-tco", "tco-unexpanded"
    if host in X_HOSTS:
        path = urllib.parse.urlsplit(url).path
        if X_MEDIA_RE.search(path):
            return "drop-media", "x-media"
        if X_STATUS_RE.match(path):
            return "keep", "x-status"
        return "keep", "x-other"
    return "keep", "web"


def strip_trailing(url: str) -> str:
    return url.rstrip(URL_TRAILING)


def url_entities(post: dict) -> list:
    """Every url entity X attached to the post, from BOTH places it puts them.

    `entities.urls` is the documented one. `note_tweet.entities.urls` is the one that matters for
    a long post: X truncates `text` at the classic limit and moves the full body — and its
    entities — under `note_tweet`. `x-cli` requests `note_tweet` (api.py:108), so a producer that
    read only `entities` would silently lose every link in the long posts, which are exactly the
    posts that carry the most.
    """
    out = []
    for container in (
        post.get("entities"),
        (post.get("note_tweet") or {}).get("entities"),
    ):
        if isinstance(container, dict):
            for item in container.get("urls") or []:
                if isinstance(item, dict):
                    out.append(item)
    return out


def harvest_urls(post: dict) -> "tuple[list, dict, str]":
    """`(kept, meta_by_canon, source)` — every outbound url in one post.

    `source` is `"entities"`, `"regex-fallback"` or `"none"` and is recorded on the row, because
    how a url was found bounds what may be claimed about it: an entity url comes with X's own
    expansion, unwound target, title, description and HTTP status, while a regex url is just
    characters that looked like a link in a sentence.

    The fallback fires ONLY when X attached no url entities at all. If entities are present they
    are authoritative and the text is not re-scanned — otherwise a post whose entities X had
    already expanded would also contribute its raw `t.co` text form, and `citations` would
    double-count a single citation.
    """
    entities = url_entities(post)
    kept: list = []
    meta: dict = {}
    drops = {"drop-tco": 0, "drop-media": 0}

    def consider(raw: str, info: dict) -> None:
        canon = canon_url(strip_trailing(raw or ""))
        if not canon:
            return
        verdict, link_kind = classify_link(canon)
        if verdict != "keep":
            drops[verdict] += 1
            return
        if canon not in meta:
            kept.append(canon)
            meta[canon] = {
                "link_kind": link_kind,
                "title": (info.get("title") or "").strip(),
                "description": (info.get("description") or "").strip(),
                "http_status": info.get("status")
                if isinstance(info.get("status"), int)
                else None,
            }

    if entities:
        for ent in entities:
            # `unwound_url` is X's resolved target and beats `expanded_url` when they differ
            # (a link-shim behind a shortener). `url` is the raw t.co and is the last resort —
            # it is kept only so `classify_link` can COUNT it as an unexpanded drop.
            raw = (
                ent.get("unwound_url")
                or ent.get("expanded_url")
                or ent.get("url")
                or ""
            )
            consider(str(raw), ent)
        return kept, meta, "entities"

    text = str(post.get("text") or "")
    note = (post.get("note_tweet") or {}).get("text")
    if isinstance(note, str):
        text = text + " " + note
    hits = URL_RE.findall(text)
    if not hits:
        return [], {}, "none"
    for raw in hits:
        consider(raw, {})
    return kept, meta, "regex-fallback"


def harvest_drops(post: dict) -> dict:
    """The drop tally for one post, recomputed the same way `harvest_urls` does it. Separate so
    the harvest stays a pure `(kept, meta, source)` triple that is trivial to assert on."""
    entities = url_entities(post)
    tally = {"tco": 0, "media": 0}
    raws: list = []
    if entities:
        for ent in entities:
            raws.append(
                str(
                    ent.get("unwound_url")
                    or ent.get("expanded_url")
                    or ent.get("url")
                    or ""
                )
            )
    else:
        text = str(post.get("text") or "")
        note = (post.get("note_tweet") or {}).get("text")
        if isinstance(note, str):
            text = text + " " + note
        raws = list(URL_RE.findall(text))
    seen = set()
    for raw in raws:
        canon = canon_url(strip_trailing(raw))
        if not canon or canon in seen:
            continue
        seen.add(canon)
        verdict, _ = classify_link(canon)
        if verdict == "drop-tco":
            tally["tco"] += 1
        elif verdict == "drop-media":
            tally["media"] += 1
    return tally


def handles(envelope: dict) -> dict:
    """`author_id` → handle, from `includes.users`. Empty when the CLI was run without `-v` or
    when X omitted the expansion; the row then carries `author: null`, which is an honest "not
    observed" rather than a guess."""
    users = ((envelope.get("includes") or {}).get("users")) or []
    return {
        str(u.get("id")): str(u.get("username"))
        for u in users
        if isinstance(u, dict) and u.get("id") and u.get("username")
    }


def post_row(post: dict, handle: Optional[str], now: dt.datetime) -> Optional[dict]:
    """One post, reduced to the shared producer contract. None without an `id` — a row with an
    unstable id is worse than no row: it reappears as "new" every single day."""
    tid = str(post.get("id") or "").strip()
    if not tid:
        return None
    url = permalink(tid)
    text = " ".join(str(post.get("text") or "").split())
    note = (post.get("note_tweet") or {}).get("text")
    if isinstance(note, str) and len(note) > len(text):
        text = " ".join(note.split())
    urls, meta, url_source = harvest_urls(post)
    metrics = post.get("public_metrics") or {}
    created = created_z(post.get("created_at"))
    return {
        "id": content_id(url),
        "source": SEARCH_ENDPOINT,
        "url": url,
        "title": (text[:110] + "…") if len(text) > 110 else (text or "(no text)"),
        "kind": "x-post",
        "published_at": created,
        "summary": text[:700],
        "signals": {
            "tweet_id": tid,
            "author_id": str(post.get("author_id") or "") or None,
            "author": handle,
            "conversation_id": str(post.get("conversation_id") or "") or None,
            "is_reply": bool(post.get("conversation_id"))
            and str(post.get("conversation_id")) != tid,
            "lang": post.get("lang"),
            "likes": int(metrics.get("like_count") or 0),
            "reposts": int(metrics.get("retweet_count") or 0),
            "replies": int(metrics.get("reply_count") or 0),
            "quotes": int(metrics.get("quote_count") or 0),
            "bookmarks": int(metrics.get("bookmark_count") or 0),
            "impressions": int(metrics.get("impression_count") or 0),
            "urls": urls,
            "url_count": len(urls),
            "url_source": url_source,
            "queries": [],
            "cited_by": [],
            "citations": 0,
            "age_days": age_days(post.get("created_at"), now),
        },
        "_link_meta": meta,
    }


def link_row(canon: str, meta: dict) -> dict:
    """One distinct outbound url. `citations`/`cited_by` are filled by `fold_citation` as posts
    arrive, so a url cited by nine posts is ONE row with `citations: 9` — which is the whole
    reason the link harvest is worth more than a flat list of every link in every post."""
    split = urllib.parse.urlsplit(canon)
    host = split.netloc.lower()
    title = meta.get("title") or ""
    if not title:
        tail = (split.path or "/").strip("/")
        title = "%s/%s" % (host, tail) if tail else host
    return {
        "id": content_id(canon),
        "source": "",
        "url": canon,
        "title": (title[:110] + "…") if len(title) > 110 else title,
        "kind": "x-link",
        "published_at": None,
        "summary": (meta.get("description") or "")[:400],
        "signals": {
            "host": host,
            "link_kind": meta.get("link_kind") or "web",
            "http_status": meta.get("http_status"),
            "citations": 0,
            "cited_by": [],
            "cited_by_authors": [],
            "queries": [],
        },
    }


def fold_citation(
    row: dict, tweet_id: str, author: Optional[str], created: Optional[str]
) -> None:
    """Record one post as a citer of this row, idempotently. Called for `x-link` rows AND for
    `x-post` rows, because a post quoted by another post is a citation of the post itself — the
    `canon_url` status fold makes those two things the same row on purpose."""
    sig = row["signals"]
    if tweet_id not in sig["cited_by"]:
        sig["cited_by"].append(tweet_id)
        sig["cited_by"].sort()
        sig["citations"] = len(sig["cited_by"])
    if "cited_by_authors" in sig and author and author not in sig["cited_by_authors"]:
        sig["cited_by_authors"].append(author)
        sig["cited_by_authors"].sort()
    if not row.get("source"):
        row["source"] = permalink(tweet_id)
    # Earliest citation is the best available date for a link: it is when this corpus first saw
    # it. A stable fact about the posts, never derived from the run clock.
    if created and (row.get("published_at") is None or created < row["published_at"]):
        if row["kind"] == "x-link":
            row["published_at"] = created


def note_query(row: dict, label: str) -> None:
    qs = row["signals"]["queries"]
    if label not in qs:
        qs.append(label)


# -------------------------------------------------------------------------------------------
# the writer detector — module scope so it can be fired on known-bad source
# -------------------------------------------------------------------------------------------
# `os.replace`/`str.replace` are deliberately NOT writers here: a rule that fires on correct code
# teaches people to add exemptions instead of keeping the property. Detector shape cross-checked
# with GitHubEcosystemMiner, FeedsAndPractitionerMiner and VendorDocsMiner, who run the same
# guard over their own producers.
WRITER_CALLS = {
    "atomic_write_json",
    "atomic_write_text",
    "atomic_write_bytes",
    "write_text",
    "write_bytes",
}


def writer_functions(tree: ast.Module) -> set:
    """Names of the module-level functions in `tree` that can touch the filesystem. Catches three
    shapes, because "one writer" is only true of the writers you thought of: the sanctioned atomic
    primitives, a raw `pathlib` write, and `open(..., 'w')` with or without a `json.dump`."""
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


def mutating_verbs(tree: ast.Module) -> set:
    """Every `FORBIDDEN_VERBS` member that `tree` puts in an X-CLI ARGV POSITION.

    The read-only guard. It is deliberately NOT a substring scan and NOT a bare "does the string
    'post' appear anywhere" walk. Both of those were tried and both are wrong in the same
    direction — they fire on prose:

      * a bare constant walk flags the word "post" in `post_row`, in a docstring, and (measured
        on the first run of this file) in `selftest`'s own expectation `== {"post"}`. A guard that
        fires on the test asserting the guard works is a guard people delete.

    So "argv position" is defined structurally: a list, tuple, or call-argument sequence that also
    contains an X-CLI MARKER — the constant `"tweet"`, any constant containing `x-cli`, or a
    reference to `X_CLI`/`ARGV_PREFIX`. That is exactly the shape `['x-cli', '-j', 'tweet', 'post',
    q]` has and exactly the shape `{"post"}` and `"the word post"` do not. Fired on this module's
    own AST every run, and on two known-bad snippets that really do build mutating argvs.
    """
    markers = {"X_CLI", "ARGV_PREFIX"}

    def is_marker(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value == "tweet" or "x-cli" in node.value.lower()
        if isinstance(node, ast.Name):
            return node.id in markers
        if isinstance(node, ast.Call):  # list(ARGV_PREFIX)
            return any(is_marker(a) for a in node.args)
        return False

    found = set()
    for sub in ast.walk(tree):
        if isinstance(sub, (ast.List, ast.Tuple)):
            elts = list(sub.elts)
        elif isinstance(sub, ast.Call):
            elts = list(sub.args)
        elif isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Add):
            elts = [sub.left, sub.right]
            for side in (sub.left, sub.right):
                if isinstance(side, (ast.List, ast.Tuple)):
                    elts.extend(side.elts)
        else:
            continue
        if not any(is_marker(e) for e in elts):
            continue
        for e in elts:
            if isinstance(e, ast.Constant) and e.value in FORBIDDEN_VERBS:
                found.add(e.value)
    return found


# -------------------------------------------------------------------------------------------
# the network edge — one function, so --selftest can replace exactly one thing
# -------------------------------------------------------------------------------------------
def x_search(
    query: str, max_results: int
) -> "tuple[Optional[dict], Optional[str], int]":
    """One `x-cli -j -v tweet search` call, as `(envelope, error, http_status)`.

    Never raises. A missing wrapper, an expired credential, a 429 and a malformed body are all
    ERRORS TO RECORD: a producer that dies on one of them writes no artifact and therefore reports
    nothing, which is the failure mode this repo's evidence discipline exists to prevent.

    `http_status` is reconstructed from the CLI's message, because the CLI does not surface the
    response: `api.py:46` turns 429 into `Rate limited. Resets at <epoch>.` and everything else
    into `API error (HTTP nnn)`. 0 means "never reached the network".
    """
    argv = list(ARGV_PREFIX) + [query, "--max", str(int(max_results))]
    try:
        proc = run(argv, timeout_s=X_TIMEOUT_S, max_output_bytes=X_MAX_BYTES)
    except FileNotFoundError:
        return None, "x-cli-absent: %s is not on PATH" % X_CLI, 0
    except OSError as exc:
        return None, "x-cli-unrunnable: %s: %s" % (type(exc).__name__, exc), 0
    if proc.timed_out:
        return None, "timeout after %ss" % X_TIMEOUT_S, 0
    if proc.truncated:
        return None, "output exceeded %d bytes" % X_MAX_BYTES, 0
    if proc.code != 0:
        err = scrub(proc.err or proc.out)
        if "Rate limited" in err:
            return None, "rate-limited: %s" % err, 429
        m = re.search(r"HTTP (\d{3})", err)
        return None, "x-cli-error: %s" % err, int(m.group(1)) if m else 0
    try:
        doc = json.loads(proc.out or "null")
    except json.JSONDecodeError as exc:
        return None, "unparseable-json: %s" % exc, 200
    if not isinstance(doc, dict):
        # The `-j` (non-verbose) shape is a bare list. If we ever see it, `-v` was dropped and
        # `includes`/`meta` are gone — that is a degraded instrument, and it is recorded as one.
        return None, "unexpected-shape: %s (was -v dropped?)" % type(doc).__name__, 200
    return doc, None, 200


Fetch = Callable[[str, int], "tuple[Optional[dict], Optional[str], int]"]


# -------------------------------------------------------------------------------------------
# collect — pure, takes no root, CANNOT write
# -------------------------------------------------------------------------------------------
def collect(
    *,
    fetch: Fetch = x_search,
    now: Optional[dt.datetime] = None,
    queries: tuple = QUERIES,
    max_calls: int = MAX_SEARCH_CALLS,
    sleep_s: float = MIN_SLEEP_S,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """The whole daily read. Always returns a document; never raises on a dead source.

    The call counter here is the ONLY rate guard, by necessity (docstring note (3)): there is no
    quota endpoint, and a vendor number we cannot read cannot be trusted anyway. `calls` is
    incremented BEFORE the request, so a fetch that hangs and is killed still consumed its slot.
    """
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    posts: dict = {}
    links: dict = {}
    sources: list = []
    calls = 0
    rate_limited = False
    reset_at: Optional[str] = None
    control_positive: Optional[int] = None
    control_links: Optional[int] = None
    control_links_total: Optional[int] = None
    tco_dropped = media_dropped = 0
    urls_seen = urls_outbound = 0

    for spec in queries:
        label, q = spec["label"], spec["q"]
        if rate_limited or calls >= max_calls:
            sources.append(
                {
                    "id": label,
                    "query": q,
                    "role": spec["role"],
                    "why": spec["why"],
                    "status": None,
                    "items": 0,
                    "rows": 0,
                    "note": "skipped — %s"
                    % (
                        "run stopped after rate limit"
                        if rate_limited
                        else "local call cap reached"
                    ),
                }
            )
            continue
        if calls and sleep_s:
            sleep(sleep_s)
        calls += 1
        envelope, error, status = fetch(q, int(spec["max"]))
        row = {
            "id": label,
            "query": q,
            "role": spec["role"],
            "why": spec["why"],
            "status": status,
            "items": 0,
            "rows": 0,
            "requested_max": int(spec["max"]),
            "urls_found": 0,
            "urls_outbound": 0,
            "tco_dropped": 0,
            "media_dropped": 0,
            "has_more": False,
            "note": "",
        }
        if error or envelope is None:
            row["note"] = error or "no envelope"
            if status == 429:
                rate_limited = True
                m = re.search(r"Resets at (\d+)", row["note"])
                if m:
                    reset_at = now_z(
                        dt.datetime.fromtimestamp(int(m.group(1)), dt.timezone.utc)
                    )
            sources.append(row)
            continue

        data = envelope.get("data") or []
        meta = envelope.get("meta") or {}
        by_id = handles(envelope)
        returned = meta.get("result_count")
        row["items"] = int(returned) if isinstance(returned, int) else len(data)
        row["has_more"] = bool(meta.get("next_token"))
        if not isinstance(data, list):
            row["note"] = "unexpected data shape: %s" % type(data).__name__
            sources.append(row)
            continue

        if row["items"] == 0 and "from:" in q:
            # A ZERO FROM A `from:` QUERY IS NOT AN ABSENT ACCOUNT, and the artifact says so
            # where the reader is rather than in a commit message. X's recent-search window is
            # ~7 days, so a real, correctly-spelled handle that simply did not post inside it
            # returns zero — indistinguishable, from this row alone, from a typo.
            #
            # MEASURED 2026-09-11: `from:SpaceXAI -is:retweet` returned 0 items while
            # `x-cli-infisical -j user get SpaceXAI` resolved the account (id
            # 1661523610111193088, 2.06M followers) — quiet, not misspelled. The note carries the
            # command that makes the distinction, so the next reader does not re-derive it.
            row["note"] = (
                "zero rows — NOT evidence the account is absent. X's recent-search window is "
                "~7 days. Disambiguate with a read: `x-cli-infisical -j user get <handle>`; "
                "a resolving handle means quiet, a 404 means the query is wrong."
            )

        new_rows = 0
        for post in data:
            if not isinstance(post, dict):
                continue
            candidate = post_row(post, by_id.get(str(post.get("author_id") or "")), now)
            if candidate is None:
                continue
            link_meta = candidate.pop("_link_meta")
            pid = candidate["id"]
            if pid in posts:
                existing = posts[pid]
                # The richer observation wins: a `from:` query carries the handle a topical query
                # lacks, and engagement counts only ever go up within a run.
                if candidate["signals"]["author"] and not existing["signals"]["author"]:
                    existing["signals"]["author"] = candidate["signals"]["author"]
            else:
                posts[pid] = candidate
                new_rows += 1
            note_query(posts[pid], label)

            drops = harvest_drops(post)
            tco_dropped += drops["tco"]
            media_dropped += drops["media"]
            row["tco_dropped"] += drops["tco"]
            row["media_dropped"] += drops["media"]

            tid = candidate["signals"]["tweet_id"]
            author = candidate["signals"]["author"]
            created = candidate["published_at"]
            for canon in candidate["signals"]["urls"]:
                kind = (link_meta.get(canon) or {}).get("link_kind") or "web"
                urls_seen += 1
                row["urls_found"] += 1
                if kind != "x-status":
                    urls_outbound += 1
                    row["urls_outbound"] += 1
                lid = content_id(canon)
                if lid == pid:
                    continue  # a post linking to itself; the fold already made them one row
                if lid not in links:
                    links[lid] = link_row(canon, link_meta.get(canon) or {})
                    new_rows += 1
                fold_citation(links[lid], tid, author, created)
                note_query(links[lid], label)

        row["rows"] = new_rows
        if spec["role"] == "control-positive":
            control_positive = row["items"]
        elif spec["role"] == "control-negative":
            # OUTBOUND, not total. MEASURED on the second live run of this file: three posts
            # returned by `-has:links` DID carry `entities.urls` — every one of them the
            # permalink of the post they were QUOTING. X's `has:links` operator evidently does
            # not count a quote-tweet's structural backlink as a link, and it is right not to:
            # nobody cited it, the client rendered it. Counting it here made a healthy run read
            # UNRELIABLE, which is the failure mode where a control gets deleted.
            #
            # So the predicate is aligned with the vendor semantics it is controlling AGAINST,
            # which is a correction rather than a weakening — and both numbers are recorded
            # (`negative_urls`, `negative_urls_total`) so the carve-out is visible instead of
            # silently absorbed. A genuine outbound link under `-has:links` still fires it.
            control_links = row["urls_outbound"]
            control_links_total = row["urls_found"]
        sources.append(row)

    # A cited x.com status that we ALSO fetched is one row, not two: fold the link's citations
    # onto the post (which has the text, the metrics and the author) and drop the shadow.
    folded = 0
    for lid in list(links):
        if lid in posts:
            src = links.pop(lid)
            dst = posts[lid]
            for tid in src["signals"]["cited_by"]:
                fold_citation(dst, tid, None, None)
            for label in src["signals"]["queries"]:
                note_query(dst, label)
            folded += 1

    rows = sorted(
        posts.values(), key=lambda r: (r["published_at"] or "", r["id"]), reverse=True
    )
    rows += sorted(
        links.values(),
        key=lambda r: (-r["signals"]["citations"], r["signals"]["host"], r["url"]),
    )

    # `control-positive` searches the bare term `grok`, which is off-topic by design — it has to
    # be, or it could not prove the search path independently of the thing being measured. Its
    # rows are KEPT (the brief is far and wide; a Grok Bot link can and does turn up under plain
    # `grok`) and labelled in `signals.queries`, and the spill is counted here so a reader can see
    # exactly how much of the artifact arrived via the control rather than via a tuned query.
    control_only = sum(
        1 for r in rows if r["signals"]["queries"] == ["control-positive"]
    )

    hosts = sorted({r["signals"]["host"] for r in links.values()})
    failed = [s for s in sources if s["status"] not in (200, None)]
    skipped = [s for s in sources if s["status"] is None]
    reliable = bool(control_positive) and control_links == 0 and not failed

    sources.insert(
        0,
        {
            "id": "quota",
            "role": "quota-observation",
            "status": 429 if rate_limited else 200,
            "items": calls,
            "rows": 0,
            "search_calls": calls,
            "local_cap": max_calls,
            "min_sleep_s": sleep_s,
            "rate_limited": rate_limited,
            "reset_at": reset_at,
            "note": (
                "X exposes no rate-limit endpoint and x-cli surfaces no headers (x_cli/api.py:46 "
                "turns 429 into a RuntimeError), so the quota is invisible until it is hit. This "
                "local counter is the binding guard — no vendor number is trusted. "
                "%d/%d call(s) spent%s."
                % (calls, max_calls, "; RATE LIMITED" if rate_limited else "")
            ),
        },
    )

    return {
        "schema": SCHEMA,
        "captured_at": now_z(now),
        "volatile_fields": list(VOLATILE_FIELDS),
        "reliable": reliable,
        "controls": {
            "positive_items": control_positive,
            "negative_urls": control_links,
            "negative_urls_total": control_links_total,
            "positive_passed": bool(control_positive),
            "negative_passed": control_links == 0,
        },
        "counts": {
            "rows": len(rows),
            "posts": len(posts),
            "links": len(links),
            "by_kind": {
                "x-post": len(posts),
                "x-link": len(links),
            },
            "distinct_hosts": len(hosts),
            "url_mentions": urls_seen,
            "url_mentions_outbound": urls_outbound,
            "tco_dropped": tco_dropped,
            "media_dropped": media_dropped,
            "links_folded_into_posts": folded,
            "rows_from_control_only": control_only,
            "search_calls": calls,
            "queries_failed": len(failed),
            "queries_skipped": len(skipped),
        },
        "hosts": hosts,
        "sources": sources,
        "rows": rows,
    }


# -------------------------------------------------------------------------------------------
# the single writer
# -------------------------------------------------------------------------------------------
def write_artifact(root: pathlib.Path, doc: dict) -> pathlib.Path:
    """The ONLY writer in this file. Named separately from `collect` so that reading X and
    recording it are two decisions a caller makes on purpose, one of which a library consumer
    never has to make."""
    when = parse_created(doc["captured_at"]) or dt.datetime.now(dt.timezone.utc)
    out = root / "x" / ("%s.json" % stamp(when))
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out, doc)
    return out


def report(doc: dict, artifact: Optional[pathlib.Path] = None) -> None:
    c = doc["counts"]
    art = artifact.name if artifact else "(not written)"
    flag = "" if doc["reliable"] else "  ** UNRELIABLE: see controls **"
    print(
        "x %s: %d row(s) (x-post=%d, x-link=%d), %d host(s), %d url mention(s) "
        "(%d outbound), "
        "%d t.co dropped, %d media dropped, %d call(s), %d failure(s), %d skipped%s"
        % (
            art,
            c["rows"],
            c["posts"],
            c["links"],
            c["distinct_hosts"],
            c["url_mentions"],
            c["url_mentions_outbound"],
            c["tco_dropped"],
            c["media_dropped"],
            c["search_calls"],
            c["queries_failed"],
            c["queries_skipped"],
            flag,
        )
    )
    for s in doc["sources"]:
        if s.get("role") == "quota-observation":
            print(
                "  quota calls=%d/%d rate_limited=%s reset_at=%s"
                % (
                    s["search_calls"],
                    s["local_cap"],
                    s["rate_limited"],
                    s.get("reset_at"),
                )
            )
            continue
        mark = "  " if s["status"] == 200 else ("--" if s["status"] is None else "!!")
        print(
            " %s %-22s status=%-4s items=%-4s new=%-4s urls=%-4s out=%-4s more=%s %s"
            % (
                mark,
                s["id"],
                s["status"],
                s["items"],
                s["rows"],
                s.get("urls_found", 0),
                s.get("urls_outbound", 0),
                s.get("has_more", False),
                (s.get("note") or "")[:60],
            )
        )
    top = [r for r in doc["rows"] if r["kind"] == "x-link"][:15]
    if top:
        print("  top-cited outbound urls:")
        for r in top:
            print(
                "    %3dx  %-12s %s"
                % (r["signals"]["citations"], r["signals"]["link_kind"], r["url"][:96])
            )
    if not doc["reliable"]:
        print(
            "  refusal recorded — do NOT read any zero in this artifact as absence "
            "(AGENTS.md positive-control rule)"
        )


# -------------------------------------------------------------------------------------------
# fixtures — real shapes, captured 2026-09-11 from `"grok bot" has:links -is:retweet`
# -------------------------------------------------------------------------------------------
FX_LINKED = {
    "author_id": "2011430731466387456",
    "conversation_id": "2098459028732911761",
    "created_at": "2026-09-11T17:09:24.000Z",
    "entities": {
        "urls": [
            {
                "description": "基础设施和api接入 by valstry",
                "display_url": "x.ai/bot/PqgO8EMZjN…",
                "end": 73,
                "expanded_url": "https://x.ai/bot/PqgO8EMZjN_SUdcvEmsRF",
                "start": 50,
                "status": 200,
                "title": "基础设施和api接入 by valstry",
                "unwound_url": "https://x.ai/bot/PqgO8EMZjN_SUdcvEmsRF",
                "url": "https://t.co/ZIwbyTgUaT",
            }
        ]
    },
    "id": "2098459028732911761",
    "lang": "zh",
    "public_metrics": {
        "bookmark_count": 1,
        "impression_count": 44,
        "like_count": 2,
        "quote_count": 0,
        "reply_count": 0,
        "retweet_count": 0,
    },
    "text": "分享一个我做的 grok bot 配置 https://t.co/ZIwbyTgUaT",
}

FX_MULTI = {
    "author_id": "2064651263317577728",
    "conversation_id": "2098459008030175290",
    "created_at": "2026-09-11T17:09:20.000Z",
    "entities": {
        "urls": [
            {
                "expanded_url": "https://alook.ai",
                "status": 200,
                "title": "Alook",
                "url": "https://t.co/aaaaaaaaaa",
            },
            {
                # X's own rendering of the post's attached image — not a cited link.
                "expanded_url": "https://x.com/GusYe1999/status/2098459008030175290/photo/1",
                "url": "https://t.co/bbbbbbbbbb",
            },
            {
                # A quoted post, arriving under the twitter.com spelling with a share token.
                "expanded_url": "https://twitter.com/benln/status/2098404942599905700?s=46&t=xyz",
                "url": "https://t.co/cccccccccc",
            },
        ]
    },
    "id": "2098459008030175290",
    "lang": "en",
    "public_metrics": {
        "bookmark_count": 0,
        "impression_count": 12,
        "like_count": 0,
        "quote_count": 0,
        "reply_count": 0,
        "retweet_count": 0,
    },
    "text": "grok bot + alook https://t.co/aaaaaaaaaa https://t.co/bbbbbbbbbb https://t.co/cccccccccc",
}

FX_NO_ENTITIES = {
    "author_id": "2375622560",
    "conversation_id": "2098459726048539014",
    "created_at": "2026-09-11T17:12:01.000Z",
    "id": "2098459726048539014",
    "lang": "en",
    "public_metrics": {
        "bookmark_count": 0,
        "impression_count": 3,
        "like_count": 0,
        "quote_count": 0,
        "reply_count": 0,
        "retweet_count": 0,
    },
    "text": "grok bot writeup at https://grokbot.money/ways/obsidian-memory-for-grok-bot, worth a read.",
}

FX_TCO_ONLY = {
    "author_id": "474585010",
    "conversation_id": "2098459845800468644",
    "created_at": "2026-09-11T17:13:09.000Z",
    "entities": {"urls": [{"url": "https://t.co/UnExPaNdEd", "expanded_url": ""}]},
    "id": "2098459845800468644",
    "lang": "en",
    "public_metrics": {
        "bookmark_count": 0,
        "impression_count": 2,
        "like_count": 0,
        "quote_count": 0,
        "reply_count": 0,
        "retweet_count": 0,
    },
    "text": "grok bot https://t.co/UnExPaNdEd",
}

FX_CITER_A = {
    "author_id": "111",
    "conversation_id": "900000000000000001",
    "created_at": "2026-09-11T10:00:00.000Z",
    "entities": {"urls": [{"expanded_url": "https://grokbotjobs.com", "status": 200}]},
    "id": "900000000000000001",
    "lang": "en",
    "public_metrics": {},
    "text": "grok bot jobs board",
}
FX_CITER_B = {
    "author_id": "222",
    "conversation_id": "900000000000000002",
    "created_at": "2026-09-11T11:00:00.000Z",
    "entities": {
        "urls": [{"expanded_url": "http://www.grokbotjobs.com/?utm_source=x#top"}]
    },
    "id": "900000000000000002",
    "lang": "en",
    "public_metrics": {},
    "text": "grok bot jobs board again",
}
FX_CITER_C = {
    "author_id": "333",
    "conversation_id": "900000000000000003",
    "created_at": "2026-09-11T12:00:00.000Z",
    "entities": {"urls": [{"expanded_url": "https://grokbotjobs.com/"}]},
    "id": "900000000000000003",
    "lang": "en",
    "public_metrics": {},
    "text": "third mention",
}
FX_QUOTED = {
    "author_id": "444",
    "conversation_id": "2098404942599905700",
    "created_at": "2026-09-11T09:00:00.000Z",
    "entities": {},
    "id": "2098404942599905700",
    "lang": "en",
    "public_metrics": {},
    "text": "the post everybody is quoting",
}

FX_USERS = [
    {"id": "2011430731466387456", "username": "Valstry"},
    {"id": "2064651263317577728", "username": "GusYe1999"},
    {"id": "111", "username": "alice"},
    {"id": "222", "username": "bob"},
    {"id": "444", "username": "benln"},
]


def envelope(
    posts: list, *, users: Optional[list] = None, next_token: Optional[str] = None
) -> dict:
    env: dict = {
        "data": list(posts),
        "includes": {"users": list(users if users is not None else FX_USERS)},
        "meta": {"result_count": len(posts)},
    }
    if next_token:
        env["meta"]["next_token"] = next_token
    if users is None and not FX_USERS:
        env.pop("includes")
    return env


def fake_x(
    *,
    by_label: Optional[dict] = None,
    default: Optional[dict] = None,
    fail_after: Optional[int] = None,
    fail_with: "tuple" = (
        None,
        "rate-limited: Rate limited. Resets at 1789000000.",
        429,
    ),
) -> "tuple":
    """An `x_search` stand-in plus the call log, so the call CAP and the STOP are provable without
    a network. `fail_after` refuses every call past the Nth, which is how the 429 leg is proven
    without waiting for a real quota to run out."""
    log: list = []

    def _fetch(query: str, max_results: int):
        log.append((query, max_results))
        if fail_after is not None and len(log) > fail_after:
            return fail_with
        for needle, env in (by_label or {}).items():
            if needle in query:
                return env, None, 200
        return (default if default is not None else envelope([])), None, 200

    return _fetch, log


# The two queries a selftest needs to satisfy the controls without a network. `grok -is:retweet`
# must return something; the `-has:links` query must return posts with no urls.
CTRL = {
    "grok -is:retweet": envelope([FX_QUOTED]),
    "-has:links": envelope([FX_QUOTED]),
}


def guards_broken_pipe(tree: ast.Module, fname: str) -> bool:
    """True when module-level function `fname` handles `BrokenPipeError` itself.

    Checked rather than trusted, because the bug this prevents is INVISIBLE to a reader: the
    failing exit code is produced by a `return f()` whose own literals are all 0, and the erasure
    happens in a shared spine two files away."""
    for fn in [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fname
    ]:
        for node in ast.walk(fn):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            names = (
                [node.type]
                if not isinstance(node.type, ast.Tuple)
                else list(node.type.elts)
            )
            if any(getattr(n, "id", "") == "BrokenPipeError" for n in names):
                return True
    return False


def unguarded_nonzero_returns(tree: ast.Module, fname: str) -> set:
    """Line numbers in module-level function `fname` that `return` a non-zero constant from
    OUTSIDE an `except BrokenPipeError` handler.

    The general form of the verdict-erasure class. A non-zero return is a VERDICT, and
    `gbtypes.main` (bin/gbtypes.py:637) erases any verdict when the process's output is a closed
    pipe — including output written on our behalf by argparse. So a non-zero return is legitimate
    here in exactly one place: inside the handler that IS the pipe guard. Anything else must
    either return 0 or delegate to a function that guards its own printing.
    """
    bad = set()
    for fn in [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fname
    ]:
        guarded: set = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            names = (
                [node.type]
                if not isinstance(node.type, ast.Tuple)
                else list(node.type.elts)
            )
            if not any(getattr(n, "id", "") == "BrokenPipeError" for n in names):
                continue
            for sub in ast.walk(node):
                guarded.add(id(sub))
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or id(node) in guarded:
                continue
            v = node.value
            if (
                isinstance(v, ast.Constant)
                and isinstance(v.value, int)
                and v.value != 0
            ):
                bad.add(node.lineno)
    return bad


def emit_verdict(checks: list, passed: int, total: int) -> None:
    """Print the selftest result. Separated from `selftest` so the verdict is computed before any
    byte is written and a closed pipe cannot reach the `return`.

    The `dup2` to `/dev/null` is not decoration: without it the interpreter re-tries the flush at
    shutdown, prints `BrokenPipeError ignored` to stderr and can exit 120 — which would replace
    one wrong exit code with another."""
    try:
        for name, ok, detail in checks:
            if not ok:
                print("FAIL %s %s" % (name, detail))
        print(
            "SELFTEST %s - %d/%d"
            % ("PASS" if passed == total else "FAIL", passed, total)
        )
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass


def selftest() -> int:
    checks: list = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    NOW = dt.datetime(2026, 9, 11, 18, 0, 0, tzinfo=dt.timezone.utc)

    # 1 — canonicalisation, shared with the other three producers.
    check(
        "canon-drops-utm-fragment-www-and-folds-http",
        canon_url("http://www.GrokBotJobs.com/Ways/?utm_source=x&ref=y#top")
        == "https://grokbotjobs.com/Ways",
        canon_url("http://www.GrokBotJobs.com/Ways/?utm_source=x&ref=y#top"),
    )
    # 2 — the x-status fold: FIVE spellings of one post become one url. The fifth is the one the
    #     first live run actually shipped wrong: X renders the permalink with the author's NUMERIC
    #     USER ID in the handle slot, and a handle-shaped `{1,15}` pattern skipped it, leaving
    #     three unfolded shadow rows in `x/2026-09-11T1727.json`.
    spellings = [
        "https://x.com/i/status/2098404942599905700",
        "https://x.com/i/web/status/2098404942599905700",
        "https://twitter.com/benln/status/2098404942599905700?s=46&t=xyz",
        "https://mobile.twitter.com/BenLn/statuses/2098404942599905700",
        "https://twitter.com/1483409796531625987/status/2098404942599905700",
    ]
    check(
        "canon-folds-every-x-status-spelling",
        len({canon_url(s) for s in spellings}) == 1
        and canon_url(spellings[2]) == "https://x.com/i/status/2098404942599905700",
        str({canon_url(s) for s in spellings}),
    )
    # 2b — a NON-permalink x host still folds (same site, four names) and keeps its path; a
    #      third-party mirror does NOT, because nitter.net is a different service.
    check(
        "x-host-aliases-fold-but-mirrors-do-not",
        canon_url("https://twitter.com/BotDirectoryAI")
        == "https://x.com/BotDirectoryAI"
        and canon_url("https://mobile.x.com/i/article/2097156023530434560")
        == "https://x.com/i/article/2097156023530434560"
        and canon_url("https://nitter.net/benln/status/2098404942599905700")
        == "https://nitter.net/benln/status/2098404942599905700",
        canon_url("https://twitter.com/BotDirectoryAI"),
    )
    # 3 — the id convention: 32 hex, blake2b-16 of the lowercased canonical url. The vector is
    #     `https://grokbotjobs.com/` WITH the trailing slash, because a bare host canonicalises to
    #     path `/` — the `p.path.rstrip("/") or "/"` rule shared verbatim with gb-feeds.py:275, so
    #     a bare-host link seen by either producer lands on the same row. Measured, not assumed:
    #     the first run of this leg asserted the no-slash form and went red.
    want = hashlib.blake2b(b"https://grokbotjobs.com/", digest_size=16).hexdigest()
    check(
        "id-is-blake2b16-of-canonical-url",
        content_id("http://www.grokbotjobs.com/?utm_source=x#f") == want
        and canon_url("http://www.grokbotjobs.com/?utm_source=x#f")
        == "https://grokbotjobs.com/"
        and len(want) == 32,
        "%s != %s" % (content_id("http://www.grokbotjobs.com/?utm_source=x#f"), want),
    )
    # 4 — extraction from entities, preferring the unwound target.
    kept, meta, src = harvest_urls(FX_LINKED)
    check(
        "extracts-expanded-url-from-entities",
        kept == ["https://x.ai/bot/PqgO8EMZjN_SUdcvEmsRF"] and src == "entities",
        "%s / %s" % (kept, src),
    )
    # 5 — entity metadata survives onto the link row.
    check(
        "entity-title-and-http-status-carried",
        meta[kept[0]]["http_status"] == 200
        and meta[kept[0]]["title"].endswith("valstry"),
        json.dumps(meta, ensure_ascii=False),
    )
    # 6 — unwound_url beats a differing expanded_url.
    shim = {
        "id": "1",
        "entities": {
            "urls": [
                {
                    "expanded_url": "https://lnk.to/shim",
                    "unwound_url": "https://real.example/page",
                }
            ]
        },
        "text": "x",
    }
    check(
        "unwound-url-beats-expanded-url",
        harvest_urls(shim)[0] == ["https://real.example/page"],
        str(harvest_urls(shim)[0]),
    )
    # 7 — note_tweet entities are read too: a long post's links are not silently lost.
    longpost = {
        "id": "2",
        "entities": {"urls": [{"expanded_url": "https://a.example"}]},
        "note_tweet": {
            "text": "the full body",
            "entities": {"urls": [{"expanded_url": "https://b.example"}]},
        },
        "text": "the truncated body",
    }
    check(
        "note-tweet-entities-are-harvested",
        set(harvest_urls(longpost)[0]) == {"https://a.example/", "https://b.example/"},
        str(harvest_urls(longpost)[0]),
    )
    # 8 — regex fallback, and it declares itself as such.
    kept3, _, src3 = harvest_urls(FX_NO_ENTITIES)
    check(
        "regex-fallback-when-entities-absent",
        kept3 == ["https://grokbot.money/ways/obsidian-memory-for-grok-bot"]
        and src3 == "regex-fallback",
        "%s / %s" % (kept3, src3),
    )
    # 9 — trailing prose punctuation is not part of the url.
    check(
        "regex-fallback-strips-trailing-comma",
        not kept3[0].endswith(","),
        kept3[0],
    )
    # 10 — the fallback does NOT double-count when entities exist (t.co text + expanded entity).
    check(
        "no-regex-fallback-when-entities-present",
        harvest_urls(FX_LINKED)[2] == "entities"
        and len(harvest_urls(FX_LINKED)[0]) == 1,
        str(harvest_urls(FX_LINKED)),
    )
    # 11 — an unexpanded t.co is dropped, not emitted, and it is counted.
    kept4, _, _ = harvest_urls(FX_TCO_ONLY)
    check(
        "unexpanded-tco-dropped-and-counted",
        kept4 == [] and harvest_drops(FX_TCO_ONLY)["tco"] == 1,
        "%s / %s" % (kept4, harvest_drops(FX_TCO_ONLY)),
    )
    # 12 — the post's own media rendering is dropped and counted; the quoted status survives.
    kept5, _, _ = harvest_urls(FX_MULTI)
    check(
        "media-sublink-dropped-quoted-status-kept",
        kept5 == ["https://alook.ai/", "https://x.com/i/status/2098404942599905700"]
        and harvest_drops(FX_MULTI)["media"] == 1,
        "%s / %s" % (kept5, harvest_drops(FX_MULTI)),
    )
    # 13 — classification.
    check(
        "classify-link-verdicts",
        classify_link("https://t.co/x") == ("drop-tco", "tco-unexpanded")
        and classify_link("https://x.com/a/status/1/photo/1")
        == ("drop-media", "x-media")
        and classify_link("https://x.com/i/status/1") == ("keep", "x-status")
        and classify_link("https://grokbotjobs.com") == ("keep", "web"),
        "",
    )

    # 14 — citation aggregation: one url cited by three posts is ONE row with citations=3.
    fetch, log = fake_x(
        by_label=dict(
            CTRL, **{"has:links": envelope([FX_CITER_A, FX_CITER_B, FX_CITER_C])}
        ),
    )
    doc = collect(fetch=fetch, now=NOW, sleep=lambda _s: None, sleep_s=0)
    jobs = [r for r in doc["rows"] if r["url"] == "https://grokbotjobs.com/"]
    check(
        "citations-aggregate-across-posts",
        len(jobs) == 1 and jobs[0]["signals"]["citations"] == 3,
        "%d row(s), citations=%s"
        % (len(jobs), jobs[0]["signals"]["citations"] if jobs else None),
    )
    # 15 — cited_by carries the post ids, sorted, and the authors that were resolvable.
    check(
        "cited-by-post-ids-and-authors",
        jobs
        and jobs[0]["signals"]["cited_by"]
        == ["900000000000000001", "900000000000000002", "900000000000000003"]
        and jobs[0]["signals"]["cited_by_authors"] == ["alice", "bob"],
        json.dumps(jobs[0]["signals"], ensure_ascii=False) if jobs else "no row",
    )
    # 16 — the link's date is the EARLIEST citation, a fact about posts, never about the clock.
    check(
        "link-published-at-is-earliest-citation",
        jobs and jobs[0]["published_at"] == "2026-09-11T10:00:00Z",
        jobs[0]["published_at"] if jobs else "",
    )
    # 17 — author handle resolved from includes.users.
    posts_rows = [r for r in doc["rows"] if r["kind"] == "x-post"]
    check(
        "author-handle-from-includes-users",
        any(r["signals"]["author"] == "alice" for r in posts_rows),
        str([r["signals"]["author"] for r in posts_rows]),
    )
    # 18 — no handle, no guess: author is null when the expansion is missing.
    f2, _ = fake_x(
        by_label=dict(CTRL, **{"has:links": envelope([FX_CITER_C], users=[])})
    )
    d2 = collect(fetch=f2, now=NOW, sleep=lambda _s: None, sleep_s=0)
    check(
        "author-null-when-includes-absent",
        all(
            r["signals"]["author"] is None
            for r in d2["rows"]
            if r["kind"] == "x-post"
            and r["signals"]["tweet_id"] == "900000000000000003"
        ),
        "",
    )
    # 19 — a cited status we ALSO fetched folds into its post row; no shadow x-link.
    f3, _ = fake_x(
        by_label=dict(
            CTRL,
            **{
                "has:links": envelope([FX_MULTI]),
                "from:LBallz77283": envelope([FX_QUOTED]),
            },
        )
    )
    d3 = collect(fetch=f3, now=NOW, sleep=lambda _s: None, sleep_s=0)
    quoted_url = "https://x.com/i/status/2098404942599905700"
    quoted = [r for r in d3["rows"] if r["url"] == quoted_url]
    check(
        "cited-status-folds-into-its-post-row",
        len(quoted) == 1
        and quoted[0]["kind"] == "x-post"
        and quoted[0]["signals"]["citations"] == 1
        and d3["counts"]["links_folded_into_posts"] == 1,
        "%d row(s) kind=%s citations=%s folded=%s"
        % (
            len(quoted),
            quoted[0]["kind"] if quoted else None,
            quoted[0]["signals"]["citations"] if quoted else None,
            d3["counts"]["links_folded_into_posts"],
        ),
    )
    # 20 — THE volatile-field property: two runs an hour apart differ ONLY in age_days.
    f4, _ = fake_x(
        by_label=dict(CTRL, **{"has:links": envelope([FX_LINKED, FX_MULTI])})
    )
    a = collect(fetch=f4, now=NOW, sleep=lambda _s: None, sleep_s=0)
    f5, _ = fake_x(
        by_label=dict(CTRL, **{"has:links": envelope([FX_LINKED, FX_MULTI])})
    )
    b = collect(
        fetch=f5, now=NOW + dt.timedelta(days=3), sleep=lambda _s: None, sleep_s=0
    )

    def strip_volatile(rows: list) -> str:
        out = []
        for r in rows:
            c = json.loads(json.dumps(r))
            c["signals"].pop("age_days", None)
            out.append(c)
        return json.dumps(out, sort_keys=True, ensure_ascii=False)

    check(
        "rows-are-clock-independent-once-age-days-stripped",
        strip_volatile(a["rows"]) == strip_volatile(b["rows"]),
        "rows moved between two clocks",
    )
    # 21 — and age_days really is the thing that moved, so leg 20 is not vacuous.
    check(
        "age-days-actually-moves-between-the-two-clocks",
        json.dumps(a["rows"], sort_keys=True) != json.dumps(b["rows"], sort_keys=True)
        and [r["signals"]["age_days"] for r in a["rows"] if r["kind"] == "x-post"]
        != [r["signals"]["age_days"] for r in b["rows"] if r["kind"] == "x-post"],
        "age_days did not move — leg 20 proves nothing",
    )
    # 22 — every volatile field named in the document actually exists on a row, and nothing else
    #      clock-shaped sneaked in.
    volatile_keys = {
        f.split(".", 1)[1] for f in a["volatile_fields"] if f.startswith("signals.")
    }
    check(
        "declared-volatile-fields-are-the-only-clock-fields",
        volatile_keys == {"age_days"}
        and all("age_days" in r["signals"] for r in a["rows"] if r["kind"] == "x-post")
        and not any(
            "age_days" in r["signals"] for r in a["rows"] if r["kind"] == "x-link"
        ),
        str(volatile_keys),
    )
    # 23 — ids are stable across runs (the whole point of a content address).
    check(
        "ids-stable-across-runs",
        [r["id"] for r in a["rows"]] == [r["id"] for r in b["rows"]],
        "",
    )

    # 24 — RATE LIMIT: the run stops, keeps what it had, and records the refusal + the reset.
    f6, log6 = fake_x(
        by_label=dict(CTRL, **{"has:links": envelope([FX_LINKED])}), fail_after=3
    )
    d6 = collect(fetch=f6, now=NOW, sleep=lambda _s: None, sleep_s=0)
    quota = d6["sources"][0]
    # The quota row also carries status 429 when the run was limited, so the QUERY that got the
    # refusal is isolated by role. (First run of this leg asserted `== 1` and measured 2 — the
    # quota row was the second. Kept as a named filter rather than a magic 2.)
    limited = [
        s for s in d6["sources"] if s.get("status") == 429 and s.get("id") != "quota"
    ]
    skipped = [s for s in d6["sources"] if s.get("status") is None]
    check(
        "rate-limit-stops-the-run-and-is-recorded",
        len(log6) == 4
        and quota["rate_limited"] is True
        and quota["reset_at"] == "2026-09-10T00:26:40Z"
        and len(limited) == 1
        and len(skipped) == len(QUERIES) - 4
        and d6["counts"]["rows"] > 0,
        "calls=%d limited=%d skipped=%d rows=%d reset=%s"
        % (
            len(log6),
            len(limited),
            len(skipped),
            d6["counts"]["rows"],
            quota.get("reset_at"),
        ),
    )
    # 25 — the LOCAL cap is the binding guard: it stops the run with no vendor signal at all.
    f7, log7 = fake_x(by_label=CTRL)
    d7 = collect(fetch=f7, now=NOW, sleep=lambda _s: None, sleep_s=0, max_calls=3)
    check(
        "local-call-cap-binds-without-any-vendor-number",
        len(log7) == 3
        and d7["counts"]["search_calls"] == 3
        and d7["counts"]["queries_skipped"] == len(QUERIES) - 3,
        "calls=%d skipped=%d" % (len(log7), d7["counts"]["queries_skipped"]),
    )
    # 26 — politeness: sleep happens between calls, never before the first.
    slept: list = []
    f8, log8 = fake_x(by_label=CTRL)
    collect(fetch=f8, now=NOW, sleep=slept.append, sleep_s=2.0, max_calls=4)
    check(
        "sleeps-between-calls-not-before-the-first",
        slept == [2.0, 2.0, 2.0] and len(log8) == 4,
        "slept=%s calls=%d" % (slept, len(log8)),
    )
    # 27 — a non-429 error is RECORDED, not raised, and the status is reconstructed.
    f9, _ = fake_x(
        by_label=CTRL,
        fail_after=0,
        fail_with=(None, "x-cli-error: API error (HTTP 401): bad", 401),
    )
    d9 = collect(fetch=f9, now=NOW, sleep=lambda _s: None, sleep_s=0)
    check(
        "non-429-error-recorded-not-raised",
        any(s.get("status") == 401 for s in d9["sources"]) and d9["reliable"] is False,
        "",
    )
    # 28 — the bare-list shape (i.e. `-v` dropped) is caught as a degraded instrument.
    f10, _ = fake_x(by_label=CTRL, default=None)

    def bare_list(query: str, mx: int):
        return None, "unexpected-shape: list (was -v dropped?)", 200

    d10 = collect(fetch=bare_list, now=NOW, sleep=lambda _s: None, sleep_s=0)
    check(
        "bare-list-shape-is-a-recorded-refusal",
        d10["reliable"] is False
        and all(
            "unexpected-shape" in (s.get("note") or "")
            for s in d10["sources"]
            if s.get("role") not in ("quota-observation",) and s.get("status") == 200
        ),
        "",
    )

    # 29 — POSITIVE CONTROL. Zero rows from `grok` means the search path is broken.
    f11, _ = fake_x(
        by_label={"has:links": envelope([FX_LINKED]), "-has:links": envelope([])}
    )
    d11 = collect(fetch=f11, now=NOW, sleep=lambda _s: None, sleep_s=0)
    check(
        "positive-control-failing-makes-the-run-unreliable",
        d11["controls"]["positive_passed"] is False and d11["reliable"] is False,
        json.dumps(d11["controls"]),
    )
    # 30 — NEGATIVE CONTROL, the fires-on-known-bad leg for the extractor itself: a `-has:links`
    #      query that somehow yields urls means the extractor is inventing them.
    f12, _ = fake_x(
        by_label={
            "grok -is:retweet": envelope([FX_QUOTED]),
            "-has:links": envelope(
                [FX_LINKED]
            ),  # deliberately wrong: links where none belong
        }
    )
    d12 = collect(fetch=f12, now=NOW, sleep=lambda _s: None, sleep_s=0)
    check(
        "negative-control-catches-an-extractor-that-invents-links",
        d12["controls"]["negative_passed"] is False and d12["reliable"] is False,
        json.dumps(d12["controls"]),
    )
    # 30b — THE MEASURED VENDOR SEMANTIC. A `-has:links` post that carries only the permalink of
    #       the post it is QUOTING must NOT fire the control: X does not count a quote-tweet's
    #       structural backlink as a link, and three such posts made the second live run read
    #       UNRELIABLE before the predicate was corrected to count OUTBOUND urls. The structural
    #       url is still harvested (it is how the discourse graph connects) and is still counted
    #       in `negative_urls_total`, so the carve-out is visible rather than absorbed.
    fx_quoter = {
        "id": "900000000000000011",
        "author_id": "111",
        "conversation_id": "900000000000000011",
        "created_at": "2026-09-11T13:00:00.000Z",
        "entities": {
            "urls": [
                {
                    "expanded_url": "https://x.com/benln/status/2097000000000000777",
                    "status": 200,
                }
            ]
        },
        "lang": "en",
        "public_metrics": {},
        "text": "quoting this",
    }
    f15, _ = fake_x(
        by_label={
            "grok -is:retweet": envelope([FX_QUOTED]),
            "-has:links": envelope([fx_quoter]),
            "has:links": envelope([FX_CITER_A]),
        }
    )
    d15 = collect(fetch=f15, now=NOW, sleep=lambda _s: None, sleep_s=0)
    check(
        "quote-permalink-does-not-fire-the-negative-control",
        d15["controls"]["negative_urls"] == 0
        and d15["controls"]["negative_urls_total"] == 1
        and d15["controls"]["negative_passed"] is True
        and d15["reliable"] is True
        and any(
            r["url"] == "https://x.com/i/status/2097000000000000777"
            and r["signals"].get("link_kind") == "x-status"
            for r in d15["rows"]
        ),
        json.dumps(d15["controls"]),
    )
    # 30c — and the carve-out is EXACTLY one link kind wide: a real outbound url under
    #       `-has:links` still fires the control. Without this leg, 30b would read as permission
    #       to stop counting anything inconvenient.
    fx_web_quoter = json.loads(json.dumps(fx_quoter))
    fx_web_quoter["entities"]["urls"][0]["expanded_url"] = "https://grokbotjobs.com"
    f16, _ = fake_x(
        by_label={
            "grok -is:retweet": envelope([FX_QUOTED]),
            "-has:links": envelope([fx_web_quoter]),
            "has:links": envelope([FX_CITER_A]),
        }
    )
    d16 = collect(fetch=f16, now=NOW, sleep=lambda _s: None, sleep_s=0)
    check(
        "an-outbound-url-under--has-links-still-fires-the-control",
        d16["controls"]["negative_urls"] == 1
        and d16["controls"]["negative_passed"] is False
        and d16["reliable"] is False,
        json.dumps(d16["controls"]),
    )
    # 31 — and the good case really does pass both controls, so 29/30 are not tautologies.
    check(
        "controls-pass-on-a-healthy-run",
        a["controls"]
        == {
            "positive_items": 1,
            "negative_urls": 0,
            "negative_urls_total": 0,
            "positive_passed": True,
            "negative_passed": True,
        }
        and a["reliable"] is True,
        json.dumps(a["controls"]),
    )

    # 32 — the query matrix is the budget, and every query is a read with retweets excluded.
    labels = [q["label"] for q in QUERIES]
    check(
        "query-matrix-is-well-formed-and-within-budget",
        len(labels) == len(set(labels))
        and len(QUERIES) <= MAX_SEARCH_CALLS
        and all("-is:retweet" in q["q"] for q in QUERIES)
        and all(10 <= int(q["max"]) <= MAX_RESULTS for q in QUERIES)
        and sum(1 for q in QUERIES if q["role"] == "control-positive") == 1
        and sum(1 for q in QUERIES if q["role"] == "control-negative") == 1,
        "%d queries" % len(QUERIES),
    )
    # 33 — the contract shape every consumer relies on.
    check(
        "document-matches-the-shared-producer-contract",
        a["schema"] == SCHEMA
        and a["captured_at"].endswith("Z")
        and isinstance(a["sources"], list)
        and a["volatile_fields"] == ["signals.age_days"]
        and all(
            set(
                (
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
            <= set(r)
            for r in a["rows"]
        )
        and all(len(r["id"]) == 32 for r in a["rows"])
        and all(r["kind"] in ("x-post", "x-link") for r in a["rows"])
        and not any("_link_meta" in r for r in a["rows"]),
        "",
    )
    # 34 — both row kinds are actually produced, which is the acceptance bar for this producer.
    check(
        "both-row-kinds-present",
        a["counts"]["posts"] > 0 and a["counts"]["links"] > 0,
        json.dumps(a["counts"]),
    )
    # 34b — the control spill is ACCOUNTED, not hidden. The positive control searches the bare
    #       term `grok`, so some of its rows are off-topic by construction; they stay in `rows`
    #       (dropping a real post to make the artifact look tidier is the dishonest half of that
    #       trade) and the spill is counted. Needs its own run: in the `a` fixture BOTH controls
    #       return FX_QUOTED, so nothing there is control-ONLY — which the first version of this
    #       leg asserted and measured 0/0.
    fx_quiet = {
        "id": "900000000000000009",
        "author_id": "999",
        "conversation_id": "900000000000000009",
        "created_at": "2026-09-11T08:00:00.000Z",
        "entities": {},
        "lang": "en",
        "public_metrics": {},
        "text": "no links here at all",
    }
    f14, _ = fake_x(
        by_label={
            "grok -is:retweet": envelope([FX_QUOTED]),
            "-has:links": envelope([fx_quiet]),
            "has:links": envelope([FX_CITER_A]),
        }
    )
    d14 = collect(fetch=f14, now=NOW, sleep=lambda _s: None, sleep_s=0)
    control_rows = [
        r for r in d14["rows"] if r["signals"]["queries"] == ["control-positive"]
    ]
    check(
        "control-only-rows-are-kept-and-counted",
        d14["counts"]["rows_from_control_only"] == len(control_rows) == 1
        and control_rows[0]["signals"]["tweet_id"] == FX_QUOTED["id"]
        and d14["reliable"] is True,
        "%d counted / %d present"
        % (d14["counts"]["rows_from_control_only"], len(control_rows)),
    )
    # 35 — `items` is RETURNED, not MATCHED, and an untaken next page is recorded as such.
    f13, _ = fake_x(
        by_label=dict(
            # The cursor is deliberately NOT realistic. A 16-char opaque value assigned to
            # a name containing "token" matches the exporter's credential_shape scanner,
            # which refused to publish this file at all (fail-closed, correctly). The fix
            # is synthetic test data, never a weaker scanner: the regex that caught this
            # is the one that catches a real gh*_/sk-/xox*- leak.
            CTRL,
            **{"has:links": envelope([FX_LINKED], next_token="FIXTURE-PAGE-2")},
        )
    )
    d13 = collect(fetch=f13, now=NOW, sleep=lambda _s: None, sleep_s=0)
    firehose = next(s for s in d13["sources"] if s["id"] == "grok-bot-links")
    check(
        "has-more-records-the-page-we-did-not-take",
        firehose["has_more"] is True and firehose["items"] == 1,
        json.dumps(firehose),
    )
    # 35b — a zero from a `from:` query carries its own disambiguation, and a zero from a topical
    #       query does not (there is no handle to check). MEASURED live: `from:SpaceXAI` returned
    #       0 items while `user get SpaceXAI` resolved a 2.06M-follower account.
    f17, _ = fake_x(by_label=CTRL)  # every non-control query returns an empty envelope
    d17 = collect(fetch=f17, now=NOW, sleep=lambda _s: None, sleep_s=0)
    vendor = next(s for s in d17["sources"] if s["id"] == "vendor-xai")
    topical = next(s for s in d17["sources"] if s["id"] == "grok-bot-mcp")
    check(
        "zero-from-a-from-query-carries-its-disambiguation",
        vendor["items"] == 0
        and "user get <handle>" in vendor["note"]
        and "NOT evidence" in vendor["note"]
        and topical["items"] == 0
        and topical["note"] == "",
        "vendor=%r topical=%r" % (vendor["note"][:40], topical["note"]),
    )

    # 35c — CANCELLATION RECOVERY, pinned on a CONSTRUCTED exception shape with no signal
    #       involved, so the behaviour is checked every run rather than only when someone
    #       remembers to press ctrl-C. Measured spine residual: closed-pipe SIGINT/SIGTERM exit 1
    #       instead of 130/143 (deterministic `os.pipe()` harness; zero artifacts either way).
    def with_context(inner: BaseException) -> BrokenPipeError:
        try:
            try:
                raise inner
            except type(inner):
                raise BrokenPipeError(32, "Broken pipe")
        except BrokenPipeError as e:
            return e

    check(
        "cancellation-exit-code-is-recovered-from-a-broken-pipe",
        recover_cancellation(with_context(Cancelled(2))) == 130
        and recover_cancellation(with_context(Cancelled(15))) == 143,
        "%s / %s"
        % (
            recover_cancellation(with_context(Cancelled(2))),
            recover_cancellation(with_context(Cancelled(15))),
        ),
    )
    # 35d — and the NEGATIVES, which are the whole point: a broken pipe that is NOT a cancelled
    #       run must keep propagating. A mutant that guessed (`getattr(ctx, "exit_code", 130)`)
    #       passes 35c and fails exactly here — it would manufacture a cancellation that never
    #       happened, which is worse than the rc 1 it replaced.
    check(
        "a-plain-broken-pipe-is-not-reported-as-a-cancellation",
        recover_cancellation(BrokenPipeError(32, "Broken pipe")) is None
        and recover_cancellation(with_context(ValueError("unrelated"))) is None
        and recover_cancellation(with_context(OSError(5, "EIO"))) is None,
        "%s / %s"
        % (
            recover_cancellation(BrokenPipeError(32, "Broken pipe")),
            recover_cancellation(with_context(ValueError("unrelated"))),
        ),
    )

    # 36 — READ-ONLY guard, on this file's own AST.
    own_src = pathlib.Path(__file__).read_text(encoding="utf-8")
    own_tree = ast.parse(own_src)
    check(
        "this-file-names-no-mutating-x-verb-in-any-argv",
        mutating_verbs(own_tree) == set() and ARGV_PREFIX[-2:] == ("tweet", "search"),
        str(sorted(mutating_verbs(own_tree))),
    )
    # 37 — fires-on-known-bad: source that DOES build a posting argv must be caught.
    bad_verb = (
        "def go(q):\n"
        "    argv = ['x-cli', '-j', 'tweet', 'post', q]\n"
        "    return run(argv)\n"
    )
    bad_like = "def go(i):\n    return run(['x-cli', 'like', i])\n"
    check(
        "read-only-guard-fires-on-a-posting-argv",
        mutating_verbs(ast.parse(bad_verb)) == {"post"}
        and mutating_verbs(ast.parse(bad_like)) == {"like"},
        "%s / %s"
        % (
            sorted(mutating_verbs(ast.parse(bad_verb))),
            sorted(mutating_verbs(ast.parse(bad_like))),
        ),
    )
    # 38 — one writer, and `collect` is not it.
    writers = writer_functions(own_tree)
    check(
        "write-artifact-is-the-only-writer",
        writers == {"write_artifact"},
        str(sorted(writers)),
    )
    # 39 — fires-on-known-bad: a `collect` that writes must be caught, in both shapes.
    bad_atomic = (
        "def collect(*, fetch, now=None):\n"
        "    atomic_write_json(pathlib.Path('x.json'), {})\n"
        "    return {}\n"
    )
    bad_raw = (
        "def collect(*, fetch):\n"
        "    with open('x.json', 'w') as fh:\n"
        "        json.dump({}, fh)\n"
        "    return {}\n"
    )
    check(
        "writer-detector-fires-on-a-collect-that-writes",
        writer_functions(ast.parse(bad_atomic)) == {"collect"}
        and writer_functions(ast.parse(bad_raw)) == {"collect"},
        "%s / %s"
        % (
            sorted(writer_functions(ast.parse(bad_atomic))),
            sorted(writer_functions(ast.parse(bad_raw))),
        ),
    )
    # 40 — secret discipline: a token-shaped value in child stderr cannot reach an artifact.
    token = "A" * 116
    scrubbed = scrub("x-cli: failed with bearer %s in header" % token)
    check(
        "stderr-scrubber-redacts-token-shaped-runs",
        token not in scrubbed and "<redacted:116 chars>" in scrubbed,
        scrubbed,
    )
    # 41 — and it does not redact ordinary diagnostics, or the scrubber is useless.
    check(
        "stderr-scrubber-keeps-short-diagnostics",
        scrub("API error (HTTP 401): Unauthorized")
        == "API error (HTTP 401): Unauthorized",
        scrub("API error (HTTP 401): Unauthorized"),
    )
    # 42 — stdlib only. The claim `packaging/pyproject.toml` makes, checked mechanically here.
    imported = set()
    for node in ast.walk(own_tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    allowed = {
        "__future__",
        "argparse",
        "ast",
        "datetime",
        "hashlib",
        "json",
        "os",
        "pathlib",
        "re",
        "sys",
        "time",
        "typing",
        "urllib",
        "gbtypes",
    }
    check(
        "imports-are-stdlib-plus-gbtypes-only",
        imported <= allowed,
        str(sorted(imported - allowed)),
    )

    # 43 — CANCELLATION MUST NOT BE SWALLOWED. `gbtypes.Cancelled` derives from `Exception`, and
    #      this producer's whole error posture is "record the refusal, keep going" — which is
    #      exactly the posture that turns a SIGTERM from the scheduler into a cheerful artifact
    #      full of `status: 0` rows and exit 0. `collect` has no `except` at all and `x_search`
    #      catches only `FileNotFoundError`/`OSError`, so cancellation propagates today. That is
    #      narrow-by-accident until something checks it. (Hazard flagged by
    #      GitHubEcosystemMiner, who measured the same shape in his own producer.)
    def cancelling_fetch(query: str, mx: int):
        raise Cancelled("cancelled by signal 15")

    try:
        collect(fetch=cancelling_fetch, now=NOW, sleep=lambda _s: None, sleep_s=0)
        propagated = False
    except Cancelled:
        propagated = True
    except Exception as exc:  # noqa: BLE001 - the whole point is to see what escaped
        propagated = "wrong exception: %s" % type(exc).__name__
    check("collect-propagates-cancellation", propagated is True, str(propagated))

    # 44 — and the network edge does too: a `Cancelled` out of `run` must not be laundered into
    #      `(None, "x-cli-unrunnable: ...", 0)`, which would read as a dead binary.
    import gbtypes as _gb  # the module is already imported; this is the handle to patch

    real_run = _gb.run
    globals_run = globals()["run"]

    def cancelling_run(argv, **kw):
        raise Cancelled("cancelled by signal 2")

    try:
        globals()["run"] = cancelling_run
        try:
            x_search("grok -is:retweet", 10)
            edge = False
        except Cancelled:
            edge = True
        except Exception as exc:  # noqa: BLE001
            edge = "wrong exception: %s" % type(exc).__name__
    finally:
        globals()["run"] = globals_run
        _gb.run = real_run
    check("x-search-propagates-cancellation", edge is True, str(edge))

    # 45 — and the property the re-raise must NOT break: an ordinary OSError is still a RECORDED
    #      refusal, not a crash. Without this leg, "propagate everything" would pass 43 and 44.
    def oserror_run(argv, **kw):
        raise OSError(24, "Too many open files")

    try:
        globals()["run"] = oserror_run
        env_, err_, status_ = x_search("grok -is:retweet", 10)
    finally:
        globals()["run"] = globals_run
    check(
        "ordinary-oserror-is-still-a-recorded-refusal",
        env_ is None and status_ == 0 and "x-cli-unrunnable" in (err_ or ""),
        str(err_),
    )

    # 46 — BOTH verdict-carrying exit paths guard their own printing: `emit_verdict` (the
    #      selftest summary) and `body` (where ARGPARSE prints usage on our behalf and exits 2).
    #      `report` is deliberately unguarded and that asymmetry is asserted, not accidental: the
    #      collect path's exit code carries no verdict, so the spine's EPIPE->0 arm is correct for
    #      it. MEASURED before the `body` guard existed: `--bogus 2>&1 | true` -> rc 0 against
    #      rc 2 unpiped.
    check(
        "verdict-carrying-paths-are-broken-pipe-guarded",
        guards_broken_pipe(own_tree, "emit_verdict")
        and guards_broken_pipe(own_tree, "body")
        and not guards_broken_pipe(own_tree, "report"),
        "emit_verdict=%s body=%s report=%s"
        % (
            guards_broken_pipe(own_tree, "emit_verdict"),
            guards_broken_pipe(own_tree, "body"),
            guards_broken_pipe(own_tree, "report"),
        ),
    )
    # 46b — the GENERAL rule, which is what actually stops the next instance of this class: a
    #       non-zero return is a verdict, and a verdict may only be returned from inside the pipe
    #       guard. Everything else returns 0 or delegates to a function that guards its own
    #       printing. (Generalisation from GitHubEcosystemMiner, after VendorDocsMiner found the
    #       same class behind `print_help(sys.stderr)`.)
    check(
        "no-unguarded-nonzero-return-in-any-entry-point",
        unguarded_nonzero_returns(own_tree, "body") == set()
        and unguarded_nonzero_returns(own_tree, "selftest") == set(),
        "body=%s selftest=%s"
        % (
            sorted(unguarded_nonzero_returns(own_tree, "body")),
            sorted(unguarded_nonzero_returns(own_tree, "selftest")),
        ),
    )
    # 47 — fires-on-known-bad, four shapes. The unguarded print (what this file shipped first);
    #      a `finally`-only version, which does NOT stop the exception; a bare non-zero return
    #      outside any handler; and — the one that matters — a non-zero return that IS inside the
    #      guard, which must be ACCEPTED or the rule degenerates into "never return non-zero".
    bad_unguarded = "def emit_verdict(c, p, t):\n    print('SELFTEST')\n"
    bad_finally = (
        "def emit_verdict(c, p, t):\n"
        "    try:\n"
        "        print('SELFTEST')\n"
        "    finally:\n"
        "        pass\n"
    )
    good = (
        "def emit_verdict(c, p, t):\n"
        "    try:\n"
        "        print('SELFTEST')\n"
        "    except BrokenPipeError:\n"
        "        pass\n"
    )
    bad_return = "def body():\n    if x:\n        return 3\n    return 0\n"
    good_return = (
        "def body():\n"
        "    try:\n"
        "        args = ap.parse_args()\n"
        "    except BrokenPipeError:\n"
        "        return 2\n"
        "    return 0\n"
    )
    check(
        "broken-pipe-guard-detectors-fire-on-all-four-known-bad-shapes",
        not guards_broken_pipe(ast.parse(bad_unguarded), "emit_verdict")
        and not guards_broken_pipe(ast.parse(bad_finally), "emit_verdict")
        and guards_broken_pipe(ast.parse(good), "emit_verdict")
        and unguarded_nonzero_returns(ast.parse(bad_return), "body") == {3}
        and unguarded_nonzero_returns(ast.parse(good_return), "body") == set(),
        "%s / %s"
        % (
            sorted(unguarded_nonzero_returns(ast.parse(bad_return), "body")),
            sorted(unguarded_nonzero_returns(ast.parse(good_return), "body")),
        ),
    )

    # The verdict is computed BEFORE anything is printed, and the printing is guarded. WHY: this
    # is the one exit path in this file whose code carries a VERDICT (`body` does
    # `return selftest()`), and `gbtypes.main` (bin/gbtypes.py:637) exits 0 unconditionally on
    # BrokenPipeError. That arm is right for `collect` — whose exit code carries no verdict,
    # because every refusal is recorded as data and the run always returns 0 — and wrong here:
    # `--selftest | head -0` on a FAILING run would exit 0, a broken producer reporting healthy
    # to any hook or CI step that pipes it. (Class found by GitHubEcosystemMiner in his own file.
    # Note what makes it invisible: `return some_function_that_prints()` is verdict-carrying even
    # though every integer literal in `body` is 0, so grepping for `return 1` does not find it.)
    # A closed pipe may lose the MESSAGE; it must never change the VERDICT.
    passed = sum(1 for _n, ok, _d in checks if ok)
    verdict = 0 if passed == len(checks) else 1
    emit_verdict(checks, passed, len(checks))
    return verdict


# -------------------------------------------------------------------------------------------
def body() -> int:
    ap = argparse.ArgumentParser(
        prog="gb-x.py",
        description="daily X read + outbound-link harvest for the Grok Bot deployment",
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
        "--dry-run", action="store_true", help="spend the quota, write no artifact"
    )
    ap.add_argument(
        "--max-calls",
        type=int,
        default=MAX_SEARCH_CALLS,
        help="lower the local request cap for a cheap probe run (never raises it)",
    )
    # ARGPARSE WRITES ON OUR BEHALF, and it carries a verdict. On a bad flag it calls
    # `self.exit(2, usage)`, which PRINTS FIRST and exits second — so when stderr is a closed
    # pipe the BrokenPipeError escapes `parse_args` BEFORE argparse's own `sys.exit(2)` runs, and
    # `gbtypes.main`'s EPIPE->0 arm converts a typo'd invocation into SUCCESS.
    #
    # MEASURED on this file before the guard: `gb-x.py --bogus 2>&1 | true` -> rc 0 while
    # unpiped -> rc 2; same for an unknown verb. (Class found by VendorDocsMiner via
    # `print_help(sys.stderr)` and generalised by GitHubEcosystemMiner. The audit error all
    # three of us made in different clothes: "I grepped for my own prints" is not an audit of
    # what the PROCESS writes — argparse, print_help and logging are writers on your behalf.)
    #
    # The verdict has to be recovered from argv, because the library never reaches the
    # `SystemExit` that would have carried it.
    try:
        args = ap.parse_args()
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stderr.fileno())
        except OSError:
            pass
        return 0 if {"-h", "--help"} & set(sys.argv[1:]) else 2

    if args.selftest or args.command == "selftest":
        return selftest()

    root = pathlib.Path(__file__).resolve().parents[1]
    # The flag can only tighten the budget. A cap that an argument could raise is not a cap.
    doc = collect(max_calls=max(1, min(args.max_calls, MAX_SEARCH_CALLS)))
    artifact = None if args.dry_run else write_artifact(root, doc)
    # `report`/`--json` are deliberately NOT guarded, and the asymmetry is the point: this path's
    # exit code carries NO verdict — every refusal is recorded as data in the artifact and the run
    # returns 0 whatever X did — so the spine's EPIPE->0 arm is exactly right for it. A guard here
    # would be ceremony; a guard on `parse_args` and on `emit_verdict` is load-bearing.
    if args.json:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
        return 0
    report(doc, artifact)
    return 0


def recover_cancellation(exc: BaseException) -> Optional[int]:
    """The exit code a `BrokenPipeError` was ABOUT to report on behalf of a cancellation, or None.

    `gbtypes.main` reports a cancelled run by PRINTING to stderr inside `except Cancelled`
    (bin/gbtypes.py:634-636) and only then exiting 130/143. Its `except BrokenPipeError` is a
    SIBLING arm, so when stderr is a closed pipe the print's EPIPE escapes the whole wrapper and
    the honest 130/143 is lost.

    MEASURED on this producer with a deterministic harness (`os.pipe()`, reader closed, exact
    pid, no `pkill` pattern): streams to /dev/null gives SIGINT 130 / SIGTERM 143, while streams
    on a closed pipe gives 1 for BOTH — a cancelled tick indistinguishable from a real failure.
    Zero artifacts in all four cases, so it fails in the safe direction; it is a
    reporting-fidelity defect, not a data-integrity one. The residual is in the shared spine and
    is not ours to edit, so it is recovered here.

    THE NEGATIVES CARRY THE WEIGHT. An EPIPE with no `Cancelled` behind it is NOT a cancellation
    and must keep propagating: a recovery that answered 130 for every broken pipe would
    MANUFACTURE a cancellation that never happened, which is exactly the kind of invented number
    this repo deletes. Hence `isinstance`, not `getattr(..., 130)`.
    """
    ctx = getattr(exc, "__context__", None)
    if not isinstance(ctx, Cancelled):
        return None
    code = getattr(ctx, "exit_code", None)
    return code if isinstance(code, int) else None


if __name__ == "__main__":
    try:
        main(body)
    except BrokenPipeError as _exc:
        _code = recover_cancellation(_exc)
        if _code is None:
            raise
        for _stream in (sys.stdout, sys.stderr):
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), _stream.fileno())
            except OSError:
                pass
        raise SystemExit(_code)
