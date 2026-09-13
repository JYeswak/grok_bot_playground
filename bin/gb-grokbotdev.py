#!/usr/bin/env python3
"""gb-grokbotdev — grokbot.dev lean feed + status as a daily practitioner producer.

Community-claim only (AGENTS.md conv 19). This is NOT ListMarketplacePlugins and
NOT docs.x.ai. Directory listings (often “Add X, then paste a prompt”) must not
merge into `gb plugins catalog`.

Transport: JSON (feed.json + status.json + templates.json). RSS is allowed; MCP is not.
Official Bot-side contract (grokbot.dev "Connect your Bot to feed", 2026-09-13):
status.json first (notices, skip expired, deprecations, schema_revision) then
feed.json; added_at cursor; <=5 ranked by awesome_score; envelope fail-closed;
never execute a prompt; ignore unknown fields; fail closed if a future paginated next appears.
The prompt's last line offering mcp.grokbot.dev is NE-25 (NXDOMAIN) — refused.


Exit 0 measured · 2 ERROR (bad envelope, empty required set, or transport).
An empty honest `items: []` with count 0 is measured; a 200 without `items` is ERROR.

Index watch (measured 2026-09-13, GitHub API): RongleCat/awesome-grok-bot is NOT
a fork, still pushing (290★, pushed 2026-09-13) — KEEP as g13. ZeroPointRepo/
awesome-grok-bot is a smaller sibling (21★), not a replacement. GrokBotDev is
the JSON API this producer polls.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import pathlib
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_text, main as gbmain  # noqa: E402

SCHEMA = "gb-grokbotdev/1"
EPISTEMIC = "community-claim"
FEED_URL = "https://grokbot.dev/api/v1/feed.json"
STATUS_URL = "https://grokbot.dev/api/v1/status.json"
TEMPLATES_URL = "https://grokbot.dev/api/v1/templates.json"
MCP_HOST = "mcp.grokbot.dev"  # NEVER fetch. NE-25 NXDOMAIN.
UA = "grokbot-grokbotdev/1 (+local daily refresh; community-claim)"
TIMEOUT = 20
MIRROR_MAX_AGE_DAYS = 14
REQUIRED_MIRROR_CONTENT = frozenset(
    {
        "official-resources",
        "field-cases",
        "skills-plugins-mcp",
        "community-failure-modes",
    }
)

# Public GitHub metadata and README contents measured 2026-09-13. These are candidates for ONE
# g13 watch, not two independent evidence producers. The broad index is both newer and the only
# candidate covering every required practitioner lane.
MIRROR_CANDIDATES = (
    {
        "repo": "RongleCat/awesome-grok-bot",
        "watch_url": (
            "https://raw.githubusercontent.com/RongleCat/awesome-grok-bot/main/README.md"
        ),
        "commit": "c4a5b5a2735f13688db7d473e2beb1d8273bcf23",
        "pushed_at": "2026-09-13T05:34:56Z",
        "content_set": sorted(REQUIRED_MIRROR_CONTENT | {"tutorials-guides"}),
    },
    {
        "repo": "ZeroPointRepo/awesome-grok-bot",
        "watch_url": (
            "https://raw.githubusercontent.com/ZeroPointRepo/awesome-grok-bot/main/README.md"
        ),
        "commit": "ea22b0029f92370f4d473974ccc4d045d1715ff0",
        "pushed_at": "2026-09-12T09:00:24Z",
        "content_set": ["official-resources", "skills-plugins-mcp"],
    },
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
Fetcher = Callable[[str, str], "FetchResult"]
Writer = Callable[[pathlib.Path, str], None]


@dataclasses.dataclass(frozen=True)
class FetchResult:
    name: str
    url: str
    status: int
    document: Optional[dict]
    error: str
    fetched_at: str
    duration_ms: int
    content_sha256: Optional[str]


def row_id(kind: str, slug: str) -> str:
    return hashlib.blake2b(
        f"{kind}\n{slug}".encode("utf-8"), digest_size=16
    ).hexdigest()


def zulu(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cursor_z(when: dt.datetime) -> str:
    return (
        when.astimezone(dt.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def parse_timestamp(stamp: Any) -> dt.datetime:
    if not isinstance(stamp, str) or not stamp:
        raise ValueError("missing timestamp")
    try:
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp {stamp!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp has no timezone: {stamp!r}")
    return parsed.astimezone(dt.timezone.utc)


def digest_json(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def request_identity(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.hostname or '?'}{parsed.path or '/'}"


def fetch_json(name: str, url: str) -> FetchResult:
    started = time.monotonic()
    fetched_at = zulu(dt.datetime.now(dt.timezone.utc))
    if MCP_HOST in url:
        return FetchResult(name, url, 0, None, "refused-ne-25", fetched_at, 0, None)
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            status = int(resp.status)
    except urllib.error.HTTPError as exc:
        return FetchResult(
            name,
            url,
            int(exc.code),
            None,
            f"http-{exc.code}",
            fetched_at,
            round((time.monotonic() - started) * 1000),
            None,
        )
    except urllib.error.URLError as exc:
        return FetchResult(
            name,
            url,
            0,
            None,
            f"url:{type(exc.reason).__name__}",
            fetched_at,
            round((time.monotonic() - started) * 1000),
            None,
        )
    except TimeoutError:
        return FetchResult(
            name,
            url,
            0,
            None,
            "timeout",
            fetched_at,
            round((time.monotonic() - started) * 1000),
            None,
        )
    content_digest = hashlib.sha256(raw).hexdigest()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return FetchResult(
            name,
            url,
            status,
            None,
            "not-json",
            fetched_at,
            round((time.monotonic() - started) * 1000),
            content_digest,
        )
    if not isinstance(doc, dict):
        return FetchResult(
            name,
            url,
            status,
            None,
            "not-object",
            fetched_at,
            round((time.monotonic() - started) * 1000),
            content_digest,
        )
    return FetchResult(
        name,
        url,
        status,
        doc,
        "ok",
        fetched_at,
        round((time.monotonic() - started) * 1000),
        content_digest,
    )


def envelope_ok(doc: dict, need_items: bool) -> Optional[str]:
    if "generated_at" not in doc:
        return "missing generated_at"
    if need_items and "items" not in doc:
        return "missing items"
    if need_items and not isinstance(doc.get("items"), list):
        return "items not a list"
    return None


def lean_item(it: dict) -> dict:
    if not isinstance(it, dict):
        raise ValueError("item is not an object")
    kind = str(it.get("type") or "")
    slug = str(it.get("slug") or "")
    if not kind or not slug:
        raise ValueError("item missing type or slug")
    parse_timestamp(it.get("added_at"))
    row = {
        "id": row_id(kind, slug),
        "type": kind,
        "slug": slug,
        "url": it.get("url"),
        "headline": it.get("headline"),
        "added_at": it.get("added_at"),
        "awesome_score": it.get("awesome_score"),
    }
    if it.get("share_url"):
        row["share_url"] = it.get("share_url")
    return row


def notice_partition(status: dict, now: dt.datetime) -> Tuple[List[dict], List[str]]:
    active: List[dict] = []
    expired: List[str] = []
    for notice in status.get("notices") or []:
        notice_id = str(notice.get("id") or "")
        expiry = parse_timestamp(notice.get("expires_at"))
        if expiry <= now:
            expired.append(notice_id)
            continue
        active.append(
            {
                "id": notice_id,
                "level": notice.get("level"),
                "title": notice.get("title"),
                "expires_at": notice.get("expires_at"),
                "action_url": notice.get("action_url"),
            }
        )
    active.sort(key=lambda row: (parse_timestamp(row["expires_at"]), row["id"]))
    return active, expired


def active_notices(status: dict, now: dt.datetime) -> List[dict]:
    return notice_partition(status, now)[0]


def validate_feed(feed: Optional[dict]) -> Optional[str]:
    if not isinstance(feed, dict):
        return "feed is not an object"
    bad = envelope_ok(feed, True)
    if bad:
        return bad
    try:
        parse_timestamp(feed.get("generated_at"))
    except ValueError as exc:
        return str(exc)
    if feed.get("api_version") != "v1":
        return "feed api_version is not v1"
    if not isinstance(feed.get("schema_revision"), str) or not feed["schema_revision"]:
        return "feed missing schema_revision"
    items = feed.get("items")
    if not items:
        return "feed items empty"
    if not isinstance(feed.get("count"), int) or feed["count"] != len(items):
        return "feed count does not match items"
    if feed.get("status_url") not in (None, STATUS_URL):
        return "feed status_url does not match the v1 status producer"
    if feed.get("next"):
        return "feed pagination appeared; bounded next traversal is not implemented"
    try:
        for item in items:
            lean_item(item)
    except ValueError as exc:
        return str(exc)
    return None


def validate_status(status: Optional[dict]) -> Optional[str]:
    if not isinstance(status, dict):
        return "status is not an object"
    bad = envelope_ok(status, False)
    if bad:
        return bad
    try:
        parse_timestamp(status.get("generated_at"))
    except ValueError as exc:
        return str(exc)
    if status.get("api_version") != "v1":
        return "status api_version is not v1"
    if (
        not isinstance(status.get("schema_revision"), str)
        or not status["schema_revision"]
    ):
        return "status missing schema_revision"
    notices = status.get("notices")
    if not isinstance(notices, list):
        return "status notices is not a list"
    try:
        for notice in notices:
            if not isinstance(notice, dict) or not notice.get("id"):
                raise ValueError("notice missing id")
            parse_timestamp(notice.get("expires_at"))
    except ValueError as exc:
        return str(exc)
    return None


def select_authoritative_watch(
    candidates: Sequence[dict],
    *,
    required: frozenset[str],
    now: dt.datetime,
    max_age_days: int = MIRROR_MAX_AGE_DAYS,
) -> dict:
    if not candidates:
        raise ValueError("no mirror candidates")
    evaluated = []
    for candidate in candidates:
        repo = str(candidate.get("repo") or "")
        watch_url = str(candidate.get("watch_url") or "")
        content_set = frozenset(
            str(value) for value in candidate.get("content_set") or []
        )
        if not repo or not watch_url:
            raise ValueError("mirror candidate missing repo or watch_url")
        pushed = parse_timestamp(candidate.get("pushed_at"))
        evaluated.append(
            {
                "repo": repo,
                "watch_url": watch_url,
                "commit": str(candidate.get("commit") or ""),
                "pushed_at": zulu(pushed),
                "pushed": pushed,
                "content_set": sorted(content_set),
                "content_digest": digest_json(sorted(content_set)),
                "missing_required": sorted(required - content_set),
            }
        )
    eligible = [
        candidate for candidate in evaluated if not candidate["missing_required"]
    ]
    if not eligible:
        raise ValueError("no mirror covers the required content set")
    eligible.sort(
        key=lambda candidate: (-candidate["pushed"].timestamp(), candidate["repo"])
    )
    winner = eligible[0]
    age_days = (now.astimezone(dt.timezone.utc) - winner["pushed"]).days
    if age_days > max_age_days:
        raise ValueError(
            f"winning mirror {winner['repo']} is stale ({age_days}d > {max_age_days}d)"
        )
    losers = []
    for candidate in evaluated:
        if candidate["repo"] == winner["repo"]:
            continue
        if candidate["missing_required"]:
            reason = "missing required content: " + ",".join(
                candidate["missing_required"]
            )
        else:
            reason = f"older commit than {winner['repo']}"
        losers.append(
            {
                "repo": candidate["repo"],
                "watch_url": candidate["watch_url"],
                "pushed_at": candidate["pushed_at"],
                "content_digest": candidate["content_digest"],
                "reason": reason,
                "count_as_evidence": False,
            }
        )
    return {
        "selection_rule": (
            "cover required content set, then newest commit, then repo name; exactly one watch"
        ),
        "required_content": sorted(required),
        "authoritative": {
            key: winner[key]
            for key in (
                "repo",
                "watch_url",
                "commit",
                "pushed_at",
                "content_set",
                "content_digest",
            )
        },
        "losing_mirrors": sorted(losers, key=lambda candidate: candidate["repo"]),
        "evidence_watch_count": 1,
    }


INDEX_WATCH = select_authoritative_watch(
    MIRROR_CANDIDATES,
    required=REQUIRED_MIRROR_CONTENT,
    now=dt.datetime(2026, 9, 13, 6, tzinfo=dt.timezone.utc),
)


def mirror_watch_error(
    repos: Sequence[str], decision: dict = INDEX_WATCH
) -> Optional[str]:
    candidate_repos = {str(candidate["repo"]) for candidate in MIRROR_CANDIDATES}
    configured = [repo for repo in repos if repo in candidate_repos]
    winner = str((decision.get("authoritative") or {}).get("repo") or "")
    if configured == [winner]:
        return None
    if len(configured) > 1:
        return (
            "mirror duplication: only the authoritative g13 index may count as evidence"
        )
    if not configured:
        return f"authoritative g13 watch missing: {winner}"
    return f"wrong g13 mirror configured: {configured[0]} (expected {winner})"


def configured_repos(root: pathlib.Path) -> List[str]:
    path = root / "sources.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("sources.json unreadable") from exc
    sources = document.get("repos") if isinstance(document, dict) else None
    if not isinstance(sources, list):
        raise ValueError("sources.json missing repos list")
    return [
        str(source.get("repo"))
        for source in sources
        if isinstance(source, dict) and source.get("repo")
    ]


def load_history(root: pathlib.Path) -> Tuple[Optional[str], set[str], Optional[str]]:
    folder = root / "grokbotdev"
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    before: Optional[str] = None
    seen: set[str] = set()
    latest: Optional[str] = None
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"history unreadable: {path.name}") from exc
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            raise ValueError(f"history has wrong schema: {path.name}")
        rows = document.get("rows")
        if not isinstance(rows, list):
            raise ValueError(f"history missing rows: {path.name}")
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                raise ValueError(f"history row missing id: {path.name}")
            seen.add(str(row["id"]))
        cursor = (
            document.get("cursor") if isinstance(document.get("cursor"), dict) else {}
        )
        candidate = cursor.get("after") or document.get("newest_added_at")
        if candidate:
            parsed = parse_timestamp(candidate)
            if before is None or parsed > parse_timestamp(before):
                before = str(candidate)
        latest = str(path.relative_to(root))
    return before, seen, latest


def select_rows(
    items: Sequence[dict], cursor_before: Optional[str], seen_ids: set[str]
) -> Tuple[List[dict], dict]:
    unique: Dict[str, dict] = {}
    duplicates = 0
    for item in items:
        row = lean_item(item)
        current = unique.get(row["id"])
        if current is not None:
            duplicates += 1
            current_key = (parse_timestamp(current["added_at"]), digest_json(current))
            row_key = (parse_timestamp(row["added_at"]), digest_json(row))
            if row_key <= current_key:
                continue
        unique[row["id"]] = row

    before_dt = parse_timestamp(cursor_before) if cursor_before else None
    newest = before_dt
    replayed = 0
    late = 0
    selected = []
    for row in unique.values():
        added = parse_timestamp(row["added_at"])
        if newest is None or added > newest:
            newest = added
        if row["id"] in seen_ids:
            replayed += 1
            continue
        if before_dt is not None and added <= before_dt:
            late += 1
        selected.append(row)
    selected.sort(
        key=lambda row: (parse_timestamp(row["added_at"]), row["id"]), reverse=True
    )
    after = cursor_z(newest) if newest is not None else cursor_before
    if cursor_before is None:
        decision = "initial-import"
    elif newest is not None and before_dt is not None and newest > before_dt:
        decision = "advanced"
    elif late:
        decision = "late-additive"
    else:
        decision = "unchanged-replay"
    return selected, {
        "field": "added_at",
        "before": cursor_before,
        "after": after,
        "decision": decision,
        "selected": len(selected),
        "replayed": replayed,
        "late": late,
        "duplicate_ids": duplicates,
    }


def source_record(result: FetchResult, validation_error: Optional[str]) -> dict:
    succeeded = (
        result.status == 200
        and result.document is not None
        and result.error == "ok"
        and validation_error is None
    )
    if succeeded:
        error = None
    elif result.error != "ok":
        error = result.error
    else:
        error = validation_error
    return {
        "name": result.name,
        "url": result.url,
        "request_identity": request_identity(result.url),
        "http_status": result.status,
        "fetched_at": result.fetched_at,
        "duration_ms": result.duration_ms,
        "attempted": True,
        "succeeded": succeeded,
        "failed": not succeeded,
        "error": error,
        "content_sha256": result.content_sha256,
    }


def build_document(
    feed: dict,
    status: dict,
    now: dt.datetime,
    *,
    cursor_before: Optional[str] = None,
    seen_ids: Optional[set[str]] = None,
    previous_artifact: Optional[str] = None,
    sources: Optional[List[dict]] = None,
) -> dict:
    rows, cursor = select_rows(feed["items"], cursor_before, seen_ids or set())
    by_type: Dict[str, int] = {}
    for row in rows:
        by_type[row["type"]] = by_type.get(row["type"], 0) + 1
    notices, expired_notice_ids = notice_partition(status, now)
    source_rows = sources or []
    attempted = sum(int(row.get("attempted") is True) for row in source_rows)
    succeeded = sum(int(row.get("succeeded") is True) for row in source_rows)
    failed = sum(int(row.get("failed") is True) for row in source_rows)
    content_digest = digest_json(
        {
            "feed_api_version": feed.get("api_version"),
            "status_api_version": status.get("api_version"),
            "schema_revision": feed.get("schema_revision"),
            "rows": rows,
            "notices": notices,
            "deprecations": status.get("deprecations") or [],
        }
    )
    document = {
        "schema": SCHEMA,
        "epistemic": EPISTEMIC,
        "captured_at": zulu(now),
        "feed": {
            "api_version": feed.get("api_version"),
            "schema_revision": feed.get("schema_revision"),
            "generated_at": feed.get("generated_at"),
            "remote_count": feed.get("count"),
        },
        "status": {
            "api_version": status.get("api_version"),
            "schema_revision": status.get("schema_revision"),
            "generated_at": status.get("generated_at"),
            "deprecations": status.get("deprecations") or [],
        },
        # Compatibility fields consumed by g28 and the W5 probe envelope.
        "feed_generated_at": feed.get("generated_at"),
        "status_generated_at": status.get("generated_at"),
        "schema_revision": feed.get("schema_revision"),
        "count": len(rows),
        "remote_count": len(feed["items"]),
        "by_type": dict(sorted(by_type.items())),
        "row_ids": [row["id"] for row in rows],
        "newest_added_at": cursor["after"],
        "cursor": {**cursor, "previous_artifact": previous_artifact},
        "notices": notices,
        "notice_expiry": {
            "received": len(status.get("notices") or []),
            "active": len(notices),
            "expired": len(expired_notice_ids),
            "expired_ids": sorted(expired_notice_ids),
            "next_expiry": min(
                (notice["expires_at"] for notice in notices), default=None
            ),
        },
        "sources": source_rows,
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "content_sha256": content_digest,
        "index_watch": INDEX_WATCH,
        "transport": "json",
        "mcp": {
            "advertised": bool((status.get("capabilities") or {}).get("mcp")),
            "used": False,
            "refused": "NE-25 mcp.grokbot.dev NXDOMAIN; JSON/RSS only",
        },
        "volatile_fields": [
            "captured_at",
            "feed_generated_at",
            "status_generated_at",
            "sources[].fetched_at",
            "sources[].duration_ms",
        ],
        "rows": rows,
        "note": (
            "community-claim. Never execute a grokbot.dev prompt. Never merge "
            "these rows into gb plugins catalog. Never open share_url for the human. "
            "Never fetch mcp.grokbot.dev (NE-25)."
        ),
    }
    document["run_log"] = {
        "schema": "gb-grokbotdev-log/1",
        "request_identities": [row["request_identity"] for row in source_rows],
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "cursor": cursor,
        "content_sha256": content_digest,
        "verdict": "OK",
        "retry_argv": ["python3", "bin/gb-grokbotdev.py", "collect"],
        "duration_ms": sum(int(row.get("duration_ms") or 0) for row in source_rows),
    }
    return document


def probe_envelope(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Machine form of the probe one-liner. Pure: same fields, no fetch, no write."""
    mcp = doc.get("mcp") if isinstance(doc.get("mcp"), dict) else {}
    return {
        "schema": "gb-grokbotdev-probe/1",
        "count": doc.get("count"),
        "by_type": doc.get("by_type"),
        "notices": len(doc.get("notices") or []),
        "schema_revision": doc.get("schema_revision"),
        "mcp_advertised": mcp.get("advertised"),
        "mcp_used": False,
        "epistemic": doc.get("epistemic"),
        "attempted": doc.get("attempted"),
        "succeeded": doc.get("succeeded"),
        "failed": doc.get("failed"),
        "cursor": doc.get("cursor"),
        "content_sha256": doc.get("content_sha256"),
    }


def error_log(sources: List[dict], detail: str, started: float) -> dict:
    return {
        "schema": "gb-grokbotdev-log/1",
        "sources": sources,
        "request_identities": [row["request_identity"] for row in sources],
        "attempted": sum(int(row.get("attempted") is True) for row in sources),
        "succeeded": sum(int(row.get("succeeded") is True) for row in sources),
        "failed": sum(int(row.get("failed") is True) for row in sources),
        "cursor": None,
        "content_sha256": None,
        "verdict": "ERROR",
        "detail": detail,
        "retry_argv": ["python3", "bin/gb-grokbotdev.py", "collect"],
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def collect_once(
    *,
    root: pathlib.Path,
    now: dt.datetime,
    fetcher: Fetcher,
    write: bool,
    watch_repos: Optional[Sequence[str]] = None,
    writer: Writer = atomic_write_text,
) -> Tuple[int, Optional[dict], Optional[pathlib.Path], dict]:
    started = time.monotonic()
    results = [fetcher("feed", FEED_URL), fetcher("status", STATUS_URL)]
    validations = [
        validate_feed(results[0].document),
        validate_status(results[1].document),
    ]
    feed_doc = results[0].document
    status_doc = results[1].document
    if (
        validations == [None, None]
        and feed_doc is not None
        and status_doc is not None
        and feed_doc.get("schema_revision") != status_doc.get("schema_revision")
    ):
        validations[1] = "status schema_revision differs from feed"
    source_rows = [
        source_record(result, validation)
        for result, validation in zip(results, validations)
    ]
    failed = [row for row in source_rows if row["failed"]]
    if failed:
        detail = "; ".join(
            f"{row['name']} http={row['http_status']} error={row['error']}"
            for row in failed
        )
        return 2, None, None, error_log(source_rows, detail, started)
    try:
        watch_error = (
            mirror_watch_error(watch_repos) if watch_repos is not None else None
        )
        if watch_error:
            raise ValueError(watch_error)
        cursor_before, seen_ids, previous = load_history(root)
        document = build_document(
            results[0].document or {},
            results[1].document or {},
            now,
            cursor_before=cursor_before,
            seen_ids=seen_ids,
            previous_artifact=previous,
            sources=source_rows,
        )
        document["run_log"]["duration_ms"] = round((time.monotonic() - started) * 1000)
    except ValueError as exc:
        return 2, None, None, error_log(source_rows, str(exc), started)
    out = None
    if write:
        out = root / "grokbotdev" / f"{now:%Y-%m-%dT%H%M%S}.json"
        if out.exists():
            return (
                2,
                None,
                None,
                error_log(source_rows, f"artifact already exists: {out.name}", started),
            )
        try:
            writer(out, json.dumps(document, indent=1) + "\n")
        except (OSError, InterruptedError) as exc:
            return (
                2,
                None,
                None,
                error_log(
                    source_rows, f"atomic commit failed: {type(exc).__name__}", started
                ),
            )
    return 0, document, out, document["run_log"]


def collect(write: bool, as_json: bool = False) -> int:
    try:
        repos = configured_repos(ROOT)
    except ValueError as exc:
        print(
            json.dumps(error_log([], str(exc), time.monotonic()), sort_keys=True),
            file=sys.stderr,
        )
        return 2
    rc, document, out, log = collect_once(
        root=ROOT,
        now=dt.datetime.now(dt.timezone.utc),
        fetcher=fetch_json,
        write=write,
        watch_repos=repos,
    )
    if rc != 0 or document is None:
        print(json.dumps(log, sort_keys=True), file=sys.stderr)
        return rc
    if as_json:
        summary = probe_envelope(document)
        summary["artifact"] = str(out.relative_to(ROOT)) if out else None
        print(json.dumps(summary, indent=1))
    elif write:
        print(
            f"grokbotdev {out.name if out else '-'}: {document['count']} new rows "
            f"cursor={document['cursor']['decision']} notices={len(document['notices'])} "
            f"sources={document['succeeded']}/{document['attempted']} mcp_used=false "
            f"epistemic={EPISTEMIC}"
        )
    else:
        print(
            f"probe ok count={document['count']} types={document['by_type']} "
            f"notices={len(document['notices'])} revision={document['schema_revision']} "
            f"cursor={document['cursor']['decision']} "
            f"mcp_advertised={document['mcp']['advertised']} mcp_used=false"
        )
    return 0


ROSTER_DOMAINS = {
    "chief-of-staff": ("meta-bot", "agent-team"),
    "crm": ("sales",),
    "front-desk": ("support", "email", "calendar"),
    "marketing": ("marketing", "content", "social-media", "marketer", "seo"),
    "ops": ("ops", "automation", "back-office", "monitoring"),
    "cfs": ("home", "family"),
}


def local_template_ids() -> set:
    ids: set = set()
    folder = ROOT / "templates"
    if not folder.is_dir():
        return ids
    for p in folder.glob("*.json"):
        ids.add(p.stem)
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict) and doc.get("id"):
            ids.add(str(doc["id"]))
    return ids


def domain_hits(tags: List[str]) -> List[str]:
    tset = {str(t).lower() for t in tags}
    return [name for name, keys in ROSTER_DOMAINS.items() if tset & set(keys)]


def mine() -> int:
    """Join grokbot.dev templates.json against our templates/. Never stores body/prompt."""
    now = dt.datetime.now(dt.timezone.utc)
    result = fetch_json("templates", TEMPLATES_URL)
    doc = result.document
    if result.status != 200 or doc is None:
        print(f"ERROR templates {result.status} {result.error}", file=sys.stderr)
        return 2
    bad = envelope_ok(doc, True)
    if bad:
        print(f"ERROR envelope: {bad}", file=sys.stderr)
        return 2
    ours = local_template_ids()
    rows = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        slug = str(it.get("slug") or "")
        if not slug:
            continue
        includes = [str(x) for x in (it.get("includes") or [])]
        tags = [str(x) for x in (it.get("tags") or [])]
        domains = domain_hits(tags)
        scheduled = "schedule" in includes
        overlap = slug in ours
        score = (10 if scheduled else 0) + 3 * len(domains) + (0 if overlap else 2)
        rows.append(
            {
                "id": row_id("template", slug),
                "slug": slug,
                "name": it.get("name"),
                "tagline": it.get("tagline"),
                "url": it.get("url"),
                "share_url": it.get("share_url"),
                "source_url": (it.get("source") or {}).get("url"),
                "includes": includes,
                "scheduled": scheduled,
                "domains": domains,
                "overlap_ours": overlap,
                "score": score,
            }
        )
    rows.sort(key=lambda r: (-r["score"], r["slug"]))
    scheduled_n = sum(1 for r in rows if r["scheduled"])
    domain_n = sum(1 for r in rows if r["domains"])
    out_doc = {
        "schema": "gb-grokbotdev-mine/1",
        "epistemic": EPISTEMIC,
        "captured_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": TEMPLATES_URL,
        "count": len(rows),
        "ours": len(ours),
        "scheduled": scheduled_n,
        "domain_overlap": domain_n,
        "overlap_ours": sum(1 for r in rows if r["overlap_ours"]),
        "note": (
            "community-claim. Never execute share_url or a grokbot.dev prompt. "
            "MCP refused (NE-25). No body/prompt stored."
        ),
        "rows": rows,
    }
    path = ROOT / "grokbotdev-mine" / f"{now:%Y-%m-%dT%H%M}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(out_doc, indent=1) + "\n")
    top = [
        r for r in rows if r["scheduled"] and r["domains"] and not r["overlap_ours"]
    ][:5]
    print(
        f"grokbotdev mine {path.name}: {len(rows)} templates "
        f"scheduled={scheduled_n} domain={domain_n} ours_overlap="
        f"{out_doc['overlap_ours']} epistemic={EPISTEMIC}"
    )
    for r in top:
        print(
            f"  {r['score']:02d} {r['slug']} domains={','.join(r['domains'])} "
            f"{r['url']}"
        )
    return 0


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, good: bool, detail: str = "") -> None:
        legs.append((name, good, detail))

    def item(slug: str, added_at: str, kind: str = "template") -> dict:
        return {
            "type": kind,
            "slug": slug,
            "headline": slug.title(),
            "added_at": added_at,
            "url": f"https://grokbot.dev/{slug}",
            "awesome_score": None,
            "prompt": "MUST NOT LAND",
        }

    def feed(items: Sequence[dict]) -> dict:
        return {
            "generated_at": "2026-09-13T00:00:00.000Z",
            "api_version": "v1",
            "schema_revision": "2026-08-28",
            "count": len(items),
            "items": list(items),
        }

    def status(expiry: str = "2026-09-13T12:00:00.000Z") -> dict:
        return {
            "generated_at": "2026-09-13T00:00:00.000Z",
            "api_version": "v1",
            "schema_revision": "2026-08-28",
            "capabilities": {"mcp": True, "rss": True},
            "deprecations": [],
            "notices": [
                {
                    "id": "notice-1",
                    "level": "info",
                    "title": "Notice",
                    "expires_at": expiry,
                    "action_url": "https://grokbot.dev/news/",
                }
            ],
        }

    def fetcher_for(feed_doc: dict, status_doc: dict) -> Fetcher:
        documents = {"feed": feed_doc, "status": status_doc}

        def fake(name: str, url: str) -> FetchResult:
            document = documents[name]
            return FetchResult(
                name,
                url,
                200,
                document,
                "ok",
                "2026-09-13T00:00:01Z",
                7,
                digest_json(document),
            )

        return fake

    a = item("a", "2026-09-11T00:00:00.000Z")
    b = item("b", "2026-09-12T00:00:00.000Z")
    c = item("c", "2026-09-13T01:00:00.000Z")
    initial, initial_cursor = select_rows([b, a], None, set())
    leg(
        "initial-import",
        [row["slug"] for row in initial] == ["b", "a"]
        and initial_cursor["decision"] == "initial-import"
        and initial_cursor["after"] == b["added_at"],
    )
    additive, additive_cursor = select_rows(
        [a, c, b], b["added_at"], {row_id("template", "a"), row_id("template", "b")}
    )
    leg(
        "additive-page",
        [row["slug"] for row in additive] == ["c"]
        and additive_cursor["decision"] == "advanced",
    )
    replay, replay_cursor = select_rows(
        [a, b, c], c["added_at"], {row_id("template", slug) for slug in ("a", "b", "c")}
    )
    leg(
        "unchanged-replay",
        replay == [] and replay_cursor["decision"] == "unchanged-replay",
    )
    ordered, ordered_cursor = select_rows(
        [c, a, b], a["added_at"], {row_id("template", "a")}
    )
    leg(
        "out-of-order-added-at",
        [row["slug"] for row in ordered] == ["c", "b"]
        and ordered_cursor["after"] == c["added_at"],
    )
    late, late_cursor = select_rows(
        [item("late", "2026-09-10T00:00:00.000Z")], c["added_at"], set()
    )
    leg(
        "late-additive-row",
        [row["slug"] for row in late] == ["late"]
        and late_cursor["decision"] == "late-additive"
        and late_cursor["after"] == c["added_at"],
    )
    duplicate, duplicate_cursor = select_rows(
        [item("dup", a["added_at"]), item("dup", b["added_at"])], None, set()
    )
    leg(
        "duplicate-id",
        len(duplicate) == 1
        and duplicate[0]["added_at"] == b["added_at"]
        and duplicate_cursor["duplicate_ids"] == 1,
    )

    now_before = dt.datetime(2026, 9, 13, 1, tzinfo=dt.timezone.utc)
    now_after = dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)
    leg(
        "unexpired-notice",
        [row["id"] for row in active_notices(status(), now_before)] == ["notice-1"],
    )
    active, expired = notice_partition(status(), now_after)
    leg("expired-notice", active == [] and expired == ["notice-1"])
    malformed = feed([a])
    malformed.pop("schema_revision")
    leg("malformed-schema", validate_feed(malformed) == "feed missing schema_revision")
    leg("empty-source", validate_feed(feed([])) == "feed items empty")

    good_watch = ["RongleCat/awesome-grok-bot", "ZeroPointRepo/GrokBotDev"]
    with tempfile.TemporaryDirectory(prefix="gb-grokbotdev-selftest-") as tmp:
        root = pathlib.Path(tmp)
        first_rc, first_doc, first_path, _ = collect_once(
            root=root,
            now=now_before,
            fetcher=fetcher_for(feed([b, a]), status()),
            write=True,
            watch_repos=good_watch,
        )
        second_rc, second_doc, second_path, _ = collect_once(
            root=root,
            now=now_after,
            fetcher=fetcher_for(feed([a, c, b]), status()),
            write=True,
            watch_repos=good_watch,
        )
        leg(
            "controlled-two-run",
            first_rc == second_rc == 0
            and first_doc is not None
            and second_doc is not None
            and first_path is not None
            and second_path is not None
            and [row["slug"] for row in first_doc["rows"]] == ["b", "a"]
            and [row["slug"] for row in second_doc["rows"]] == ["c"]
            and second_doc["notices"] == []
            and second_doc["notice_expiry"]["expired_ids"] == ["notice-1"]
            and second_doc["cursor"]["previous_artifact"]
            == str(first_path.relative_to(root)),
        )
        third_rc, third_doc, _, _ = collect_once(
            root=root,
            now=now_after + dt.timedelta(seconds=1),
            fetcher=fetcher_for(feed([a, b, c]), status()),
            write=False,
            watch_repos=good_watch,
        )
        leg(
            "two-run-replay-stays-empty",
            third_rc == 0
            and third_doc is not None
            and third_doc["rows"] == []
            and third_doc["cursor"]["decision"] == "unchanged-replay",
        )
        leg(
            "honest-source-counts",
            second_doc is not None
            and second_doc["attempted"] == 2
            and second_doc["succeeded"] == 2
            and second_doc["failed"] == 0
            and all(source["http_status"] == 200 for source in second_doc["sources"]),
        )

    def http_failure(name: str, url: str) -> FetchResult:
        if name == "feed":
            return FetchResult(
                name, url, 503, None, "http-503", zulu(now_before), 4, None
            )
        document = status()
        return FetchResult(
            name, url, 200, document, "ok", zulu(now_before), 3, digest_json(document)
        )

    with tempfile.TemporaryDirectory(prefix="gb-grokbotdev-http-") as tmp:
        root = pathlib.Path(tmp)
        rc, document, _, run_log = collect_once(
            root=root,
            now=now_before,
            fetcher=http_failure,
            write=True,
            watch_repos=good_watch,
        )
        leg(
            "http-failure",
            rc == 2
            and document is None
            and run_log["attempted"] == 2
            and run_log["succeeded"] == 1
            and run_log["failed"] == 1
            and not (root / "grokbotdev").exists(),
        )

    def interrupted(_: pathlib.Path, __: str) -> None:
        raise InterruptedError("fixture interruption before rename")

    with tempfile.TemporaryDirectory(prefix="gb-grokbotdev-atomic-") as tmp:
        root = pathlib.Path(tmp)
        rc, document, _, run_log = collect_once(
            root=root,
            now=now_before,
            fetcher=fetcher_for(feed([a]), status()),
            write=True,
            watch_repos=good_watch,
            writer=interrupted,
        )
        leg(
            "interruption-before-atomic-commit",
            rc == 2
            and document is None
            and run_log["verdict"] == "ERROR"
            and list(root.rglob("*.json")) == [],
        )

    mirror_fixture = (
        {
            "repo": "example/older",
            "watch_url": "https://example.test/older",
            "commit": "aaa",
            "pushed_at": "2026-09-10T00:00:00Z",
            "content_set": ["required"],
        },
        {
            "repo": "example/newer",
            "watch_url": "https://example.test/newer",
            "commit": "bbb",
            "pushed_at": "2026-09-12T00:00:00Z",
            "content_set": ["required", "extra"],
        },
    )
    mirror = select_authoritative_watch(
        mirror_fixture,
        required=frozenset({"required"}),
        now=now_before,
    )
    leg(
        "mirror-selection",
        mirror["authoritative"]["repo"] == "example/newer"
        and mirror["evidence_watch_count"] == 1
        and mirror["losing_mirrors"][0]["count_as_evidence"] is False
        and mirror["authoritative"]["content_digest"]
        != mirror["losing_mirrors"][0]["content_digest"],
    )
    stale_failed = False
    try:
        select_authoritative_watch(
            mirror_fixture[:1],
            required=frozenset({"required"}),
            now=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
            max_age_days=7,
        )
    except ValueError as exc:
        stale_failed = "stale" in str(exc)
    leg("stale-winning-mirror-fails", stale_failed)
    leg(
        "mirror-duplication-rejected",
        mirror_watch_error(
            ["RongleCat/awesome-grok-bot", "ZeroPointRepo/awesome-grok-bot"]
        )
        is not None,
    )

    sample_sources = [
        source_record(fetcher_for(feed([a]), status())("feed", FEED_URL), None),
        source_record(fetcher_for(feed([a]), status())("status", STATUS_URL), None),
    ]
    sample = build_document(feed([a]), status(), now_before, sources=sample_sources)
    envelope = probe_envelope(sample)
    leg(
        "probe-envelope-json-shape",
        envelope.get("schema") == "gb-grokbotdev-probe/1"
        and "rows" not in envelope
        and "ids" not in envelope
        and envelope.get("mcp_used") is False
        and envelope.get("attempted") == 2
        and isinstance(envelope.get("cursor"), dict),
    )
    dead = fetch_json("mcp", "https://mcp.grokbot.dev/mcp")
    leg(
        "mcp-refused-before-network",
        dead.error == "refused-ne-25" and dead.document is None and dead.status == 0,
    )
    leg(
        "artifact-content-digest",
        len(sample["content_sha256"]) == 64
        and sample["run_log"]["content_sha256"] == sample["content_sha256"],
    )
    leg("prompt-never-stored", "prompt" not in sample["rows"][0])
    leg(
        "authoritative-watch-is-g13",
        INDEX_WATCH["authoritative"]["repo"] == "RongleCat/awesome-grok-bot"
        and INDEX_WATCH["evidence_watch_count"] == 1,
    )

    passed = sum(1 for _, good, _ in legs if good)
    for name, good, detail in legs:
        print(
            "  {mark} {name}{detail}".format(
                mark="ok  " if good else "FAIL",
                name=name,
                detail=(" — " + detail) if detail else "",
            )
        )
    print(
        "SELFTEST {verdict} - {passed}/{total}".format(
            verdict="PASS" if passed == len(legs) else "FAIL",
            passed=passed,
            total=len(legs),
        )
    )
    return 0 if passed == len(legs) else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-grokbotdev.py")
    ap.add_argument(
        "verb",
        nargs="?",
        choices=("collect", "probe", "mine"),
        default="probe",
    )
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument(
        "--json", action="store_true", help="probe prints the machine envelope"
    )
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.verb == "mine":
        return mine()
    return collect(write=args.verb == "collect", as_json=args.json)


if __name__ == "__main__":
    gbmain(main)
