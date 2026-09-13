#!/usr/bin/env python3
"""gbhttp — the single typed HTTP effect every producer stands on.

Five urllib copies with diverging policy grew in bin/ (snapshot, feeds, bot, group,
handover, jobs); ETag/304 was absent everywhere, so the daily tick full-crawls while
calling itself incremental. This module is the one place that touches the network for
a GET: deadline + byte cap + checkpoint-between-reads hook + UA/Accept fields +
ETag/304 + robots policy + per-host budget, stdlib only.

Deliberately a LIBRARY, not a CLI: no argparse parser, so `gb-dogfood` reads it as a
library (the gblib precedent) rather than a verb missing from `bin/gb`. Direct
execution answers only `--selftest` (the executable proofs) and `--capabilities`
(the named thresholds), with `--help` staying silent on stdout for the same reason.
"""

from __future__ import annotations

import dataclasses
import http.server
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, ClassVar, Optional

# Named thresholds. Every number that governs a fetch lives here, and
# `--capabilities` reports them, so a policy change is a constant change.
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
DEFAULT_HOST_BUDGET_BYTES = 64 * 1024 * 1024
ROBOTS_MAX_BYTES = 256 * 1024
ROBOTS_TIMEOUT_S = 15.0
DEFAULT_UA = "grokbot-gbhttp/1 (+local refresh; contact: Joshua Nowak)"
DEFAULT_ACCEPT = "*/*"  # pinned: `Accept: text/markdown` 404s docs.x.ai (NE-1)

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2


@dataclasses.dataclass(frozen=True)
class FetchResult:
    """What one GET returned. A non-2xx status is a MEASUREMENT, not a crash —
    the snapshot that owns the fail-closed contract needs the status, the byte
    count, and the error string side by side, exactly as it recorded them before."""

    url: str
    status: int
    body: bytes
    error: str
    truncated: bool
    not_modified: bool
    etag: str


@dataclasses.dataclass(frozen=True)
class Policy:
    """The transport policy for one GET: every knob fetch() used to take loose.
    Frozen, so the module default below is safe to share; per-call tweaks go
    through dataclasses.replace, never mutation."""

    timeout_s: float = DEFAULT_TIMEOUT_S
    max_bytes: int = DEFAULT_MAX_BYTES
    accept: str = DEFAULT_ACCEPT
    user_agent: str = DEFAULT_UA
    etag: str = ""
    robots: Optional["RobotsPolicy"] = None
    on_chunk: Optional[Callable[[int], bool]] = None


_DEFAULT_POLICY = Policy()


def _read_capped(
    fp: Any, max_bytes: int, on_chunk: Optional[Callable[[int], bool]]
) -> tuple[bytes, int, bool, str]:
    """Read fp in chunks. Returns (body, total_seen, truncated, error). The byte
    cap fires first (deterministic); the checkpoint hook fires between reads so a
    caller can abort on a policy this module does not name (a host budget)."""
    parts: list[bytes] = []
    kept = 0
    total = 0
    while True:
        chunk = fp.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        if isinstance(chunk, str):  # pragma: no cover - defensive: HTTP is bytes
            chunk = chunk.encode("utf-8", "replace")
        total += len(chunk)
        if kept < max_bytes:
            parts.append(chunk[: max_bytes - kept])
            kept += len(parts[-1])
        if total > max_bytes:
            return (
                b"".join(parts),
                total,
                True,
                f"over-cap: {total}+ bytes > {max_bytes} cap",
            )
        if on_chunk is not None and not on_chunk(len(chunk)):
            return b"".join(parts), total, True, f"checkpoint-aborted@{total}"
    return b"".join(parts), total, False, ""


def fetch(url: str, policy: Optional[Policy] = None, **overrides: Any) -> FetchResult:
    """One GET with every policy attached. New code passes a Policy; existing
    callers (the snapshot producer) still pass policy fields as keywords, which
    land on a copy of the default via dataclasses.replace — same values, same
    bytes, no flag-day. An unknown keyword is a TypeError, as before."""
    eff = _DEFAULT_POLICY if policy is None else policy
    return _execute(url, dataclasses.replace(eff, **overrides))


def _execute(url: str, policy: Policy) -> FetchResult:
    """The fetch body: never raises for an HTTP or network outcome (status 0 +
    error names it); only a caller cancel (KeyboardInterrupt and friends,
    BaseException) propagates — narrowing proved by the feeds producer's own
    legs, not re-proved here."""
    if policy.robots is not None and not policy.robots.allowed(url):
        path = urllib.parse.urlsplit(url).path or "/"
        return FetchResult(url, 0, b"", f"robots-denied: {path}", False, False, "")
    headers = {"User-Agent": policy.user_agent, "Accept": policy.accept}
    if policy.etag:
        headers["If-None-Match"] = policy.etag
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=policy.timeout_s) as resp:
            status = int(resp.status)
            seen_etag = str(resp.headers.get("ETag") or "")
            body, _, truncated, err = _read_capped(
                resp, policy.max_bytes, policy.on_chunk
            )
            return FetchResult(url, status, body, err, truncated, False, seen_etag)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return FetchResult(url, 304, b"", "", False, True, policy.etag)
        try:
            ebody, _, etrunc, _ = _read_capped(e, policy.max_bytes, None)
        except Exception:
            ebody, etrunc = b"", False
        # A 404 is a measurement, not a crash: the status travels, the error
        # string keeps the pre-migration `HTTPError <code>` shape.
        return FetchResult(
            url, int(e.code), ebody, f"HTTPError {e.code}", etrunc, False, ""
        )
    except TimeoutError:
        return FetchResult(
            url, 0, b"", f"timeout after {policy.timeout_s}s", False, False, ""
        )
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            return FetchResult(
                url, 0, b"", f"timeout after {policy.timeout_s}s", False, False, ""
            )
        return FetchResult(url, 0, b"", f"URLError: {e.reason}", False, False, "")
    except Exception as e:  # DNS, TLS — the "fetcher broke" family
        return FetchResult(url, 0, b"", f"{type(e).__name__}: {e}", False, False, "")


@dataclasses.dataclass(frozen=True)
class _Rule:
    """One Allow/Disallow line, as a path pattern with `*`/`$` per RFC 9309."""

    allow: bool
    pattern: str

    def specificity(self, path: str) -> int:
        """Length of the match, or -1. Longest match wins; ties go to Allow."""
        if _robots_match(self.pattern, path):
            return len(self.pattern.rstrip("$"))
        return -1


def _robots_match(pattern: str, path: str) -> bool:
    """RFC 9309 §2.2 matching: `*` spans any run, trailing `$` anchors the end,
    everything else is a literal prefix."""
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    parts = core.split("*")
    if len(parts) == 1:
        return path == core if anchored else path.startswith(core)
    regex = ".*?".join(re.escape(p) for p in parts)
    if anchored:
        return re.fullmatch(regex, path) is not None
    return re.match(regex, path) is not None


@dataclasses.dataclass(frozen=True)
class RobotsPolicy:
    """The crawl policy for one host. `allow_all()` is the fail-open answer:
    an unreadable, unparsable, or agent-less robots.txt constrains nothing."""

    allows: tuple[str, ...] = ()
    disallows: tuple[str, ...] = ()

    @classmethod
    def allow_all(cls) -> "RobotsPolicy":
        return cls((), ())

    @classmethod
    def parse(cls, text: str) -> "RobotsPolicy":
        """Only the `User-agent: *` group binds this client. A file with no
        such group (the status.x.ai HTML page, which is not a robots.txt at
        all) parses to allow-all rather than to a spurious deny."""
        allows: list[str] = []
        disallows: list[str] = []
        in_star = False
        seen_star = False
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                in_star = value == "*"
                seen_star = seen_star or in_star
            elif in_star and field == "allow" and value:
                allows.append(value)
            elif in_star and field == "disallow" and value:
                disallows.append(value)
        if not seen_star:
            return cls.allow_all()
        return cls(tuple(allows), tuple(disallows))

    def allowed(self, url: str) -> bool:
        path = urllib.parse.urlsplit(url).path or "/"
        best_allow = max(
            (_Rule(True, p).specificity(path) for p in self.allows), default=-1
        )
        best_deny = max(
            (_Rule(False, p).specificity(path) for p in self.disallows), default=-1
        )
        if best_allow < 0 and best_deny < 0:
            return True
        return best_allow >= best_deny

    @classmethod
    def fetch_for(
        cls,
        base_url: str,
        *,
        user_agent: str = DEFAULT_UA,
        timeout_s: float = ROBOTS_TIMEOUT_S,
    ) -> "RobotsPolicy":
        """Fetch `<base>/robots.txt`, fail-open. A policy fetch that could fail
        closed would turn a vendor outage into a self-inflicted blind spot."""
        target = base_url.rstrip("/") + "/robots.txt"
        res = fetch(
            target,
            timeout_s=timeout_s,
            max_bytes=ROBOTS_MAX_BYTES,
            user_agent=user_agent,
            robots=None,
        )
        if res.status != 200 or not res.body:
            return cls.allow_all()
        try:
            return cls.parse(res.body.decode("utf-8", "replace"))
        except Exception:
            return cls.allow_all()


class HostBudget:
    """Per-host byte budget for one run. Mutable by design: it accumulates
    across fetches, and the fetch loop charges it between reads through the
    checkpoint hook (`lambda n: budget.charge(host, n)`)."""

    def __init__(self, max_bytes_per_host: int = DEFAULT_HOST_BUDGET_BYTES) -> None:
        self.cap = max_bytes_per_host
        self.used: dict[str, int] = {}

    def charge(self, host: str, n: int) -> bool:
        """Add a chunk's bytes; False when the host is over budget (the fetch
        aborts at the next checkpoint)."""
        self.used[host] = self.used.get(host, 0) + n
        return self.used[host] <= self.cap

    def spent(self, host: str) -> int:
        return self.used.get(host, 0)


def capabilities() -> dict[str, Any]:
    return {
        "name": "gbhttp",
        "version": "1.0.0",
        "purpose": "the single typed HTTP effect: deadline, byte cap, "
        "checkpoint hook, ETag/304, robots policy, per-host budget",
        "stdlib_only": True,
        "thresholds": {
            "default_timeout_s": DEFAULT_TIMEOUT_S,
            "default_max_bytes": DEFAULT_MAX_BYTES,
            "read_chunk_bytes": READ_CHUNK_BYTES,
            "default_host_budget_bytes": DEFAULT_HOST_BUDGET_BYTES,
            "robots_max_bytes": ROBOTS_MAX_BYTES,
            "robots_timeout_s": ROBOTS_TIMEOUT_S,
        },
        "verdicts": {"SELFTEST PASS": EXIT_OK, "SELFTEST FAIL": EXIT_FINDINGS},
        "selftest": "bin/gbhttp.py --selftest",
    }


# --------------------------------------------------------------------------------------
# Selftest: every policy above has a known-bad leg that must fire, plus a positive
# control proving the leg did not simply break the fetcher. Local loopback only —
# no fixture is hand-edited, nothing is written, nothing leaves the machine.
# --------------------------------------------------------------------------------------

SELFTEST_HANG_S = 3.0
SELFTEST_TIMEOUT_S = 0.5
SELFTEST_BODY = b"hello-gbhttp"
SELFTEST_BIG_N = 200_000
SELFTEST_ETAG = '"v1"'


@dataclasses.dataclass
class _FixtureState:
    seen_ua: str = ""
    seen_accept: str = ""
    seen_none_match: str = ""
    secret_hits: int = 0


class _Handler(http.server.BaseHTTPRequestHandler):
    state: ClassVar[_FixtureState]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - must match BaseHTTPRequestHandler's parameter name
        pass

    def _send(
        self, code: int, body: bytes, headers: Optional[dict[str, str]] = None
    ) -> None:
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/ok":
            st = type(self).state
            st.seen_ua = str(self.headers.get("User-Agent") or "")
            st.seen_accept = str(self.headers.get("Accept") or "")
            st.seen_none_match = str(self.headers.get("If-None-Match") or "")
            self._send(200, SELFTEST_BODY)
        elif path == "/big":
            self._send(200, b"x" * SELFTEST_BIG_N)
        elif path == "/hang":
            time.sleep(SELFTEST_HANG_S)
            self._send(200, b"late")
        elif path == "/etag":
            if self.headers.get("If-None-Match") == SELFTEST_ETAG:
                self._send(304, b"")
            else:
                self._send(200, b"version-one", {"ETag": SELFTEST_ETAG})
        elif path == "/secret":
            type(self).state.secret_hits += 1
            self._send(200, b"should-not-fetch")
        else:
            self._send(404, b"no such fixture")


def _with_server() -> tuple[http.server.ThreadingHTTPServer, str, _FixtureState]:
    state = _FixtureState()
    _Handler.state = state
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}", state


@dataclasses.dataclass
class _Scope:
    """The selftest's shared bench: where the fixture server listens, what it
    saw, and the leg rows each area helper appends."""

    base: str
    state: _FixtureState
    legs: list[tuple[str, bool, str]]

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.legs.append((name, ok, detail))


def _t_headers(scope: _Scope) -> None:
    ua = "gbhttp-selftest/1"
    r = fetch(f"{scope.base}/ok", timeout_s=5.0, user_agent=ua, accept="*/*")
    scope.check(
        "ok-200-exact",
        r.status == 200
        and r.body == SELFTEST_BODY
        and r.error == ""
        and not r.truncated
        and not r.not_modified,
        f"got status={r.status} bytes={len(r.body)} err={r.error!r}",
    )
    scope.check(
        "headers-sent",
        scope.state.seen_ua == ua and scope.state.seen_accept == "*/*",
        f"server saw UA={scope.state.seen_ua!r} Accept={scope.state.seen_accept!r}",
    )


def _t_timeout(scope: _Scope) -> None:
    r = fetch(f"{scope.base}/hang", timeout_s=SELFTEST_TIMEOUT_S)
    scope.check(
        "timeout-fires",
        r.status == 0 and "timeout" in r.error,
        f"got status={r.status} err={r.error!r} — a hang must be a "
        f"measurement, never a wedged tick",
    )


def _t_cap(scope: _Scope) -> None:
    r = fetch(f"{scope.base}/big", timeout_s=5.0, max_bytes=1024)
    scope.check(
        "over-cap-truncates",
        r.status == 200 and len(r.body) == 1024 and r.truncated and "1024" in r.error,
        f"got bytes={len(r.body)} truncated={r.truncated} err={r.error!r}",
    )


def _t_checkpoint(scope: _Scope) -> None:
    r = fetch(f"{scope.base}/big", timeout_s=5.0, on_chunk=lambda n: False)
    scope.check(
        "checkpoint-aborts",
        r.truncated and r.error.startswith("checkpoint-aborted@"),
        f"got truncated={r.truncated} err={r.error!r}",
    )


def _t_etag(scope: _Scope) -> None:
    first = fetch(f"{scope.base}/etag", timeout_s=5.0)
    second = fetch(f"{scope.base}/etag", timeout_s=5.0, etag=first.etag)
    scope.check(
        "etag-recorded",
        first.etag == SELFTEST_ETAG and not first.not_modified,
        f"got etag={first.etag!r}",
    )
    scope.check(
        "304-hit",
        second.not_modified and second.status == 304 and second.body == b"",
        f"got status={second.status} not_modified={second.not_modified} "
        f"bytes={len(second.body)}",
    )


def _t_robots(scope: _Scope) -> None:
    policy = RobotsPolicy.parse(
        "User-agent: *\nDisallow: /secret\nDisallow: /tmp\nAllow: /tmp/public\n"
    )
    scope.check(
        "robots-parse",
        not policy.allowed(f"{scope.base}/secret/x")
        and policy.allowed(f"{scope.base}/ok")
        and policy.allowed(f"{scope.base}/tmp/public/y"),
        "longest match must win: /tmp/public is allowed under a /tmp deny",
    )
    deny = RobotsPolicy.parse("User-agent: *\nDisallow: /secret\n")
    before = scope.state.secret_hits
    r = fetch(f"{scope.base}/secret", timeout_s=5.0, robots=deny)
    scope.check(
        "robots-deny-no-request",
        r.status == 0
        and r.error.startswith("robots-denied")
        and scope.state.secret_hits == before,
        f"got err={r.error!r} hits={scope.state.secret_hits - before} — a denial "
        f"that still sends the request is not a policy",
    )
    r = fetch(f"{scope.base}/ok", timeout_s=5.0, robots=deny)
    scope.check(
        "robots-allow-passes",
        r.status == 200 and r.body == SELFTEST_BODY,
        f"got status={r.status} err={r.error!r}",
    )


def _t_budget(scope: _Scope) -> None:
    budget = HostBudget(max_bytes_per_host=100)
    scope.check(
        "budget-unit",
        budget.charge("h", 60)
        and not budget.charge("h", 50)
        and budget.spent("h") == 110,
        f"spent={budget.spent('h')}",
    )
    small = HostBudget(max_bytes_per_host=100)
    host = urllib.parse.urlsplit(scope.base).hostname or "h"
    r = fetch(
        f"{scope.base}/big",
        timeout_s=5.0,
        on_chunk=lambda n: small.charge(host, n),
    )
    scope.check(
        "budget-abort-integration",
        r.truncated and r.error.startswith("checkpoint-aborted@"),
        f"got truncated={r.truncated} err={r.error!r}",
    )


def _t_misc(scope: _Scope) -> None:
    r = fetch(f"{scope.base}/missing", timeout_s=5.0)
    scope.check(
        "404-measured",
        r.status == 404 and r.error == "HTTPError 404",
        f"got status={r.status} err={r.error!r} — positive control: error "
        f"pages are recorded, never raised",
    )
    dead = RobotsPolicy.fetch_for("http://127.0.0.1:1/")
    scope.check(
        "robots-fail-open",
        dead.allowed("http://127.0.0.1:1/anything"),
        "an unreadable robots.txt constrains nothing, or a vendor outage "
        "becomes a self-inflicted blind spot",
    )
    caps = capabilities()
    scope.check(
        "capabilities-shape",
        caps["thresholds"]["default_max_bytes"] == DEFAULT_MAX_BYTES
        and caps["thresholds"]["read_chunk_bytes"] == READ_CHUNK_BYTES,
        "the reported thresholds must BE the constants, not a copy",
    )


def selftest() -> int:
    server, base, state = _with_server()
    scope = _Scope(base=base, state=state, legs=[])
    try:
        _t_headers(scope)
        _t_timeout(scope)
        _t_cap(scope)
        _t_checkpoint(scope)
        _t_etag(scope)
        _t_robots(scope)
        _t_budget(scope)
        _t_misc(scope)
    finally:
        server.shutdown()
        server.server_close()

    failed = [name for name, ok, _ in scope.legs if not ok]
    for name, ok, detail in scope.legs:
        print(
            f"  [{'PASS' if ok else 'FAIL'}] {name}"
            + (f" — {detail}" if not ok else "")
        )
    print(
        f"SELFTEST {'PASS' if not failed else 'FAIL'} — "
        f"{len(scope.legs) - len(failed)}/{len(scope.legs)} legs"
    )
    return EXIT_OK if not failed else EXIT_FINDINGS


def main(argv: list[str]) -> int:
    if argv == ["--selftest"]:
        return selftest()
    if argv == ["--capabilities"]:
        print(json.dumps(capabilities(), indent=1))
        return EXIT_OK
    if argv in (["--help"], ["-h"], []):
        print(
            "usage: gbhttp.py [--selftest | --capabilities]",
            file=sys.stderr,
        )
        return EXIT_OK
    print(
        f"usage: gbhttp.py [--selftest | --capabilities]: unknown {argv!r}",
        file=sys.stderr,
    )
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
