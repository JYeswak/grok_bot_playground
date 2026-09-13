#!/usr/bin/env python3
"""gb-mcp-validate — prove every MCP surface a Bot can reach still answers, and with what tools.

MCP is the one place where a capability can vanish without the vendor, the client, or any gate in
this repo noticing: a server moves, an auth token expires, a tool is renamed upstream, and the
Bot simply stops being able to do a thing it did last week. Nothing in the Grok Bot UI reports
that. This does.

Three classes of surface, and they are NOT equivalent:

  official  `https://docs.x.ai/api/mcp` — xAI's own docs MCP. Public, unauthenticated, and the
            one server we can validate end to end without touching a credential.
  local     servers declared in `~/.cursor/mcp.json`. HTTP ones are probed with a real MCP
            `initialize` + `tools/list`; stdio ones are RECORDED, NOT PROBED — launching a
            command to see if it answers is a side effect this tool refuses to have.
  account   servers attached to the Grok Bot account. `GetGrokBotUserMcpSettings` returns a
            `serverId` and custom instructions but NO URL, so the endpoint is unreachable from
            here by construction. Recorded as configured-but-unprobeable rather than skipped.

A server that fails is a finding. A server that answers with FEWER tools than last week is also a
finding, and the more dangerous one, because nothing else would ever surface it.

  gb-mcp-validate.py            # write mcp/<stamp>.json
  gb-mcp-validate.py --json
  gb-mcp-validate.py --selftest       # offline proofs, no network, no writes
  gb-mcp-validate.py --capabilities   # thresholds and inputs, machine-readable
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import load  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402

OFFICIAL = [("xai-docs", "https://docs.x.ai/api/mcp")]
PROTOCOL = "2025-06-18"
TIMEOUT = 25.0
UA_PROBE_TIMEOUT = 10.0
HTTP_CLOUDFLARE_BAN = 403
UA_BROWSER_VALUE = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
UA_PYTHON_VALUE = "Python-urllib/3 gb-mcp-validate/1 (+local weekly check)"
UA_KINDS = ("default", "python", "browser")
MISSING_CRED_CODE = -32001
MIN_ERROR_DETAIL_CHARS = 12
ERROR_SNIPPET_CHARS = 120
UNKNOWN_TOOL_PROBE = "gb_validate_probe_no_such_tool"


def infisical_secret(name: str) -> str | None:
    """Resolve one secret LIVE from Infisical. Never cached, never written anywhere.

    Calls the v3 REST API directly rather than `infisical secrets get`, because the installed CLI
    (0.43.111) calls /api/v4/secrets and this self-hosted instance answers 404 there while v3
    answers 401 — route present, auth required. That single fact breaks every `cf-secret`
    consumer on this machine; going around it here keeps THIS tool working and the finding is
    recorded rather than papered over.

    Returns None on any failure. Callers must treat that as "could not authenticate", never as
    "the server is fine".
    """
    env_file = pathlib.Path(os.path.expanduser("~/.config/infisical/zeststream.env"))
    if not env_file.is_file():
        return None
    ident = {}
    for line in env_file.read_text().splitlines():
        line = line.strip().removeprefix("export ").strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            ident[k.strip()] = v.strip().strip('"').strip("'")
    need = (
        "INFISICAL_CLIENT_ID",
        "INFISICAL_CLIENT_SECRET",
        "INFISICAL_API_URL",
        "INFISICAL_PROJECT_ID",
    )
    if not all(ident.get(k) for k in need):
        return None
    try:
        tok = subprocess.run(
            [
                "infisical",
                "login",
                "--method=universal-auth",
                f"--client-id={ident['INFISICAL_CLIENT_ID']}",
                f"--client-secret={ident['INFISICAL_CLIENT_SECRET']}",
                f"--domain={ident['INFISICAL_API_URL']}",
                "--silent",
                "--plain",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        if not tok:
            return None
        url = (
            f"{ident['INFISICAL_API_URL']}/api/v3/secrets/raw/{name}"
            f"?workspaceId={ident['INFISICAL_PROJECT_ID']}"
            f"&environment={ident.get('INFISICAL_ENV', 'prod')}"
        )
        # Cloudflare fronts this instance and answers Python-urllib's default signature with
        # 403 error code 1010 (browser-signature ban) — measured 2026-09-11, while the identical
        # curl call succeeded. A named User-Agent is the difference between "secret unresolvable"
        # and a working resolver, and the failure mode reads like a permissions problem.
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {tok}",
                "User-Agent": "gb-mcp-validate/1 (+local weekly MCP check)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return ((json.loads(r.read()) or {}).get("secret") or {}).get(
                "secretValue"
            ) or None
    except Exception:
        return None


def rpc(
    url: str,
    method: str,
    params: dict | None = None,
    session: str | None = None,
    auth: dict | None = None,
    user_agent: str | None = None,
    timeout: float = TIMEOUT,
):
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    ).encode()
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "user-agent": user_agent or "gb-mcp-validate/1 (+local weekly check)",
    }
    if session:
        headers["mcp-session-id"] = session
    if auth:
        headers.update(auth)
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            # Streamable HTTP may answer as SSE; take the first data: line.
            if raw.lstrip().startswith("event:") or "\ndata: " in raw:
                for line in raw.splitlines():
                    if line.startswith("data: "):
                        raw = line[6:]
                        break
            return r.status, json.loads(raw or "{}"), r.headers.get("mcp-session-id")
    except urllib.error.HTTPError as e:
        return e.code, {"_body": e.read()[:200].decode("utf-8", "replace")}, None
    except Exception as e:
        return 0, {"_error": f"{type(e).__name__}: {e}"}, None


def credential_for(name: str, creds: dict) -> tuple[dict | None, str]:
    """(header dict, state) for a server, resolved live. The VALUE never leaves this function's
    caller frame: only the state string is ever recorded."""
    spec = (creds.get("servers") or {}).get(name)
    if not spec:
        return None, "no-credential-declared"
    val = infisical_secret(spec["secret"])
    if not val:
        return None, "credential-unresolvable"
    fmt = spec.get("format", "{value}").replace("{value}", val)
    return {spec.get("header", "Authorization"): fmt}, "credential-resolved"


def ua_for(kind: str) -> str | None:
    """The User-Agent string for one UA-matrix leg. `default` is None on purpose: it
    exercises the interpreter's REAL default signature, which is what Cloudflare
    bans (1010) — sending the literal text "Python-urllib/x.y" would test a
    guess about that signature instead of the signature itself."""
    if kind == "browser":
        return UA_BROWSER_VALUE
    if kind == "python":
        return UA_PYTHON_VALUE
    return None


def classify_ua_matrix(statuses: dict) -> str:
    """Verdict for one server's per-UA status map. Pure: takes statuses, never the net.
    `ua-challenge-suspected` is the NE-10 shape — the default signature banned while
    an explicit UA answers. `ua-divergent` is any other split (record, do not grade).
    `ua-consistent-blocked` / `ua-consistent-answered` agree across all three legs."""
    vals = [statuses.get(k) for k in UA_KINDS]
    if any(v in (None, 0) for v in vals):
        return "ua-inconclusive"
    if len(set(vals)) == 1:
        if vals[0] == HTTP_CLOUDFLARE_BAN:
            return "ua-consistent-blocked"
        return "ua-consistent-answered"
    if statuses.get("default") == HTTP_CLOUDFLARE_BAN and any(
        statuses.get(k) not in (None, 0, HTTP_CLOUDFLARE_BAN)
        for k in ("python", "browser")
    ):
        return "ua-challenge-suspected"
    return "ua-divergent"


def check_error_shape(body: dict) -> tuple[bool, str]:
    """Missing-credential leg: the error MUST be a JSON-RPC object with an int code
    and a str message (gb-mcp-conformance err_shape_ok), and the credentialed
    servers' no-credential short-circuit MUST carry MISSING_CRED_CODE. Pure."""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False, "no error object"
    if not isinstance(err.get("code"), int) or not isinstance(err.get("message"), str):
        return False, "error without int code + str message"
    if err.get("code") != MISSING_CRED_CODE:
        return (
            False,
            f"code {err.get('code')} is not the missing-credential short-circuit",
        )
    return True, "missing-credential shape ok"


def check_unknown_tool(body: dict, tool: str) -> tuple[bool, str]:
    """Unknown-tool leg: the channel MUST stay diagnosable — a JSON-RPC error whose
    message names the tool (or method) instead of masking it. Pure."""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False, "no error object"
    if not isinstance(err.get("code"), int) or not isinstance(err.get("message"), str):
        return False, "error without int code + str message"
    if tool not in str(err.get("message")) and tool not in str(body.get("method", "")):
        return False, "error masks which tool was unknown"
    return True, "unknown tool diagnosable"


def check_upstream_detail(body: dict) -> tuple[bool, str]:
    """Upstream-detail leg: a propagated error MUST carry its detail — a message of
    at least MIN_ERROR_DETAIL_CHARS chars, never an empty string or a bare code.
    Pure: grades the body the server already sent, sends nothing new."""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False, "no error object"
    msg = err.get("message")
    if not isinstance(msg, str) or len(msg.strip()) < MIN_ERROR_DETAIL_CHARS:
        return False, "upstream detail missing or thinner than the floor"
    return True, "upstream detail propagated"


def probe_error_contract(
    url: str, session: str | None, auth: dict | None = None
) -> dict:
    """One error-channel probe, triple-graded. Calls a tool name no server implements:
    the credential gate precedes dispatch (source-verified in gb-mcp-conformance),
    so an unknown tool never touches upstream either way — the leg is side-effect
    free on gated AND keyless servers. Missing-cred shape is graded only when the
    server proves its gate with MISSING_CRED_CODE; anything else is recorded as
    not-applicable, never inferred. Read-only."""
    _, body, _ = rpc(
        url,
        "tools/call",
        {"name": UNKNOWN_TOOL_PROBE, "arguments": {}},
        session,
        auth=auth,
    )
    if not isinstance(body, dict) or "error" not in body:
        return {
            "missing_cred": {
                "verdict": "not-applicable",
                "detail": "no error to grade",
            },
            "unknown_tool": {
                "verdict": "not-applicable",
                "detail": "no error to grade",
            },
            "upstream_detail": {
                "verdict": "not-applicable",
                "detail": "no error to grade",
            },
        }
    code = (
        (body.get("error") or {}).get("code")
        if isinstance(body.get("error"), dict)
        else None
    )
    out = {}
    if code == MISSING_CRED_CODE:
        ok, detail = check_error_shape(body)
        out["missing_cred"] = {"verdict": "pass" if ok else "fail", "detail": detail}
        out["unknown_tool"] = {
            "verdict": "not-applicable",
            "detail": "credential gate precedes dispatch",
        }
    else:
        out["missing_cred"] = {
            "verdict": "not-applicable",
            "detail": "server answered without the credential gate",
        }
        ok, detail = check_unknown_tool(body, UNKNOWN_TOOL_PROBE)
        out["unknown_tool"] = {"verdict": "pass" if ok else "fail", "detail": detail}
    ok, detail = check_upstream_detail(body)
    out["upstream_detail"] = {"verdict": "pass" if ok else "fail", "detail": detail}
    return out


def probe_ua_matrix(url: str, auth: dict | None = None) -> dict:
    """One UA-matrix probe: `notifications/initialized` under each UA leg.
    Notifications are unanswered by spec, so the leg is side-effect free and the
    HTTP status alone carries the signal (1010 ban vs answered). Read-only."""
    init: dict = {}
    statuses = {}
    for kind in UA_KINDS:
        st, _, _ = rpc(
            url,
            "notifications/initialized",
            init,
            auth=auth,
            user_agent=ua_for(kind),
            timeout=UA_PROBE_TIMEOUT,
        )
        statuses[kind] = st
    return {"statuses": statuses, "verdict": classify_ua_matrix(statuses)}


def validate(name: str, url: str, creds: dict | None = None) -> dict:
    init = {
        "protocolVersion": PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "gb-mcp-validate", "version": "1"},
    }
    auth, cred_state = credential_for(name, creds or {})
    st, resp, session = rpc(url, "initialize", init, auth=auth)
    row = {
        "name": name,
        "url": url,
        "transport": "http",
        "init_status": st,
        "credential": cred_state,
    }
    if st in (401, 403) and auth:
        # We HELD a credential and the server rejected it. That is not "behind auth" — it is a
        # dead key, and it is the failure this whole mechanism exists to catch. Measured
        # 2026-09-11: Infisical's AGENTMAIL_HTTP_BEARER_TOKEN did not match the token the running
        # server accepts, so every consumer resolving it correctly got 401.
        row.update(
            {
                "ok": False,
                "state": "credential-rejected",
                "error": f"HTTP {st} WITH a resolved credential — the stored secret is stale "
                f"or the server was started with a different one",
            }
        )
        return row
    if st in (401, 403):
        # The server ANSWERED and demanded a credential. That is a live endpoint behind auth, not
        # a dead one — the same distinction as a 400 vs a route-404 on the RPC surface (NE-8).
        # Validating past this point would mean handling someone else's token, which this tool
        # will not do; it records reachability and stops.
        row.update(
            {
                "ok": None,
                "state": "auth-required",
                "note": "reachable, requires a credential this tool deliberately does not hold",
            }
        )
        return row
    if st != 200 or "result" not in resp:
        row["ok"] = False
        row["state"] = "unreachable"
        row["error"] = resp.get("_error") or resp.get("_body") or f"HTTP {st}"
        row["ua_matrix"] = probe_ua_matrix(url, auth)
        return row
    info = resp["result"].get("serverInfo") or {}
    row.update(
        {
            "server_name": info.get("name"),
            "server_version": info.get("version"),
            "protocol": resp["result"].get("protocolVersion"),
            "capabilities": sorted(resp["result"].get("capabilities") or {}),
        }
    )
    st2, tools, _ = rpc(url, "tools/list", {}, session, auth=auth)
    names = sorted(
        t.get("name") for t in ((tools.get("result") or {}).get("tools") or [])
    )
    row["tools"] = names
    row["tool_count"] = len(names)
    row["ok"] = st2 == 200 and bool(names)
    row["state"] = "healthy" if row["ok"] else "degraded"
    if not row["ok"]:
        row["error"] = f"tools/list -> {st2} {json.dumps(tools)[:ERROR_SNIPPET_CHARS]}"
    row["ua_matrix"] = probe_ua_matrix(url, auth)
    row["error_contract"] = probe_error_contract(url, session, auth)
    return row


def local_servers(creds: dict) -> list[dict]:
    cfg = load(pathlib.Path(os.path.expanduser("~/.cursor/mcp.json"))) or {}
    out = []
    for name, spec in (cfg.get("mcpServers") or {}).items():
        url = spec.get("url")
        if url:
            out.append(validate(f"local:{name}", url, creds))
        else:
            # Declared as a command. Launching it to see whether it answers is a side effect this
            # tool will not have; recorded so the census is complete and honest.
            out.append(
                {
                    "name": f"local:{name}",
                    "transport": "stdio",
                    "ok": None,
                    "command": " ".join(
                        [spec.get("command", "")] + (spec.get("args") or [])
                    )[:120],
                    "note": "stdio server — declared, deliberately not launched",
                }
            )
    return out


def _selftest_contract(legs: list) -> int:
    """Error-channel contract legs, appended to the shared leg list. Inline bodies
    only — the shapes below are the gb-mcp-conformance error channel, frozen."""

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, ok, detail))

    good_cred = {
        "error": {"code": -32001, "message": "Missing credential: connect OAuth first"}
    }
    ok, _ = check_error_shape(good_cred)
    leg("missing-cred-shape-passes", ok)
    ok, _ = check_error_shape(
        {"error": {"code": -32601, "message": "Method not found"}}
    )
    leg(
        "missing-cred-shape-rejects-wrong-code",
        not ok,
        "known-bad: -32601 is unknown-method, not the credential gate",
    )
    ok, _ = check_error_shape({"error": {"code": -32001}})
    leg(
        "missing-cred-shape-rejects-message-less",
        not ok,
        "known-bad: a code with no message carries no diagnosis",
    )
    ok, _ = check_error_shape({"result": []})
    leg(
        "missing-cred-shape-rejects-non-error",
        not ok,
        "known-bad: a result body is not an error channel",
    )
    tool = UNKNOWN_TOOL_PROBE
    good_tool = {"error": {"code": -32601, "message": f"Unknown tool '{tool}'"}}
    ok, _ = check_unknown_tool(good_tool, tool)
    leg("unknown-tool-diagnosable-passes", ok)
    ok, _ = check_unknown_tool(
        {"error": {"code": -32601, "message": "Bad request"}}, tool
    )
    leg(
        "unknown-tool-masking-fails",
        not ok,
        "known-bad: 'Bad request' names neither the tool nor the method",
    )
    good_detail = {
        "error": {"code": -32000, "message": "Upstream 500: store quota exhausted"}
    }
    ok, _ = check_upstream_detail(good_detail)
    leg("upstream-detail-passes", ok)
    ok, _ = check_upstream_detail({"error": {"code": -32000, "message": "ERR"}})
    leg(
        "upstream-detail-thin-fails",
        not ok,
        "known-bad: a 3-char message propagates nothing",
    )
    ok, _ = check_upstream_detail({"error": {"code": -32000, "message": "   "}})
    leg(
        "upstream-detail-blank-fails",
        not ok,
        "known-bad: whitespace is not detail",
    )
    before = len(legs)
    body: dict = {}
    orig_rpc = rpc
    try:
        globals()["rpc"] = lambda *a, **k: (200, body, None)
        body = dict(good_tool)
        contract = probe_error_contract("https://fixture.local/mcp", None)
        leg(
            "contract-probe-grades-unknown-tool",
            contract["unknown_tool"]["verdict"] == "pass"
            and contract["missing_cred"]["verdict"] == "not-applicable",
        )
        body = dict(good_cred)
        contract = probe_error_contract("https://fixture.local/mcp", None)
        leg(
            "contract-probe-grades-credential-gate",
            contract["missing_cred"]["verdict"] == "pass"
            and contract["unknown_tool"]["verdict"] == "not-applicable",
        )
        body = {"result": []}
        contract = probe_error_contract("https://fixture.local/mcp", None)
        leg(
            "contract-probe-silent-on-non-error",
            all(v["verdict"] == "not-applicable" for v in contract.values()),
            "known-bad: a result body must grade nothing",
        )
    finally:
        globals()["rpc"] = orig_rpc
    leg(
        "contract-probe-ran-three-legs",
        len(legs) == before + 3,
        "the probe legs above did not all execute",
    )
    ok = sum(1 for _, passed, _ in legs if passed)
    for name, passed, detail in legs:
        print(
            f"  {'PASS' if passed else 'FAIL'} {name}"
            + (f" — {detail}" if detail and not passed else "")
        )
    print(f"SELFTEST {'PASS' if ok == len(legs) else 'FAIL'} - {ok}/{len(legs)}")
    return 0 if ok == len(legs) else 1


def selftest() -> int:
    """Offline proofs over inline fixtures: every positive leg has a known-bad
    sibling that must produce the opposite answer. No network, no disk writes —
    the classifiers take statuses and bodies, never a socket."""
    legs: list = []

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, ok, detail))

    leg(
        "ua-for-default-is-the-real-signature",
        ua_for("default") is None,
        "a literal python UA string would test a guess, not the signature",
    )
    leg(
        "ua-for-names-all-three-legs",
        set(ua_for(k) is not None for k in ("python", "browser")) == {True}
        and ua_for("default") is None,
        "known-bad: a two-leg matrix cannot isolate the 1010 ban",
    )
    leg(
        "browser-ua-matches-conformance",
        # Conformance builds its UA by implicit concatenation, so the joined
        # string never appears literally there: match per segment instead.
        all(
            seg
            in pathlib.Path(__file__)
            .resolve()
            .parent.joinpath("gb-mcp-conformance.py")
            .read_text()
            for seg in (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ",
                "AppleWebKit/537.36 (KHTML, like Gecko) ",
                "Chrome/126.0 Safari/537.36",
            )
        ),
        "known-bad: two browser strings means the 1010 lesson drifts",
    )
    leg(
        "ua-matrix-agrees-on-answers",
        classify_ua_matrix({"default": 200, "python": 200, "browser": 200})
        == "ua-consistent-answered",
    )
    leg(
        "ua-matrix-agrees-on-blocks",
        classify_ua_matrix({"default": 403, "python": 403, "browser": 403})
        == "ua-consistent-blocked",
    )
    leg(
        "ua-matrix-names-the-1010-shape",
        classify_ua_matrix({"default": 403, "python": 403, "browser": 200})
        == "ua-challenge-suspected",
        "known-bad: default banned while browser answers is the NE-10 trap",
    )
    leg(
        "ua-matrix-other-splits-are-divergent",
        classify_ua_matrix({"default": 200, "python": 500, "browser": 200})
        == "ua-divergent",
        "known-bad: a 500 among 200s is not a UA challenge",
    )
    leg(
        "ua-matrix-transport-failure-is-inconclusive",
        classify_ua_matrix({"default": 0, "python": 200, "browser": 200})
        == "ua-inconclusive",
        "known-bad: a dead socket is not a challenge verdict",
    )

    return _selftest_contract(legs)


def capabilities() -> dict:
    return {
        "producer": "gb-mcp-validate.py",
        "inputs": [
            "mcp-credentials.json (secret NAMES only)",
            "~/.cursor/mcp.json",
            "inventory/*.json",
        ],
        "thresholds": {
            "protocol": PROTOCOL,
            "timeout_s": TIMEOUT,
            "ua_probe_timeout_s": UA_PROBE_TIMEOUT,
            "cloudflare_ban_http": HTTP_CLOUDFLARE_BAN,
            "ua_kinds": list(UA_KINDS),
            "missing_cred_code": MISSING_CRED_CODE,
            "min_error_detail_chars": MIN_ERROR_DETAIL_CHARS,
            "error_snippet_chars": ERROR_SNIPPET_CHARS,
        },
        "selftest": "gb-mcp-validate.py --selftest  (offline inline fixtures, no network)",
    }


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--capabilities", action="store_true")
    args = ap.parse_args()
    if args.capabilities:
        print(json.dumps(capabilities(), indent=1))
        return 0
    if args.selftest:
        return selftest()

    creds = load(root / "mcp-credentials.json") or {}
    rows = [validate(n, u, creds) for n, u in OFFICIAL] + local_servers(creds)

    account = []
    inv = (
        sorted((root / "inventory").glob("*.json"))
        if (root / "inventory").is_dir()
        else []
    )
    for s in ((load(inv[-1]) or {}).get("mcp_servers") or []) if inv else []:
        account.append(
            {
                "name": f"account:{s.get('server_id')}",
                "transport": "unknown",
                "ok": None,
                "note": "attached to the account; GetGrokBotUserMcpSettings exposes no URL, "
                "so the endpoint cannot be reached from here",
            }
        )

    doc = {
        "schema": "gb-mcp/1",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "protocol": PROTOCOL,
        "servers": rows + account,
        "probed": sum(1 for r in rows if r.get("ok") is not None),
        "ok": sum(1 for r in rows if r.get("ok")),
        "failed": [r["name"] for r in rows if r.get("ok") is False],
    }
    out = root / "mcp" / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(doc, indent=1) + "\n")
    if args.json:
        print(json.dumps(doc, indent=1))
        return 0
    print(
        f"mcp {out.name}: {doc['ok']}/{doc['probed']} probed server(s) healthy"
        + (f", FAILED: {', '.join(doc['failed'])}" if doc["failed"] else "")
        + f" · {len(account)} account server(s) unprobeable by construction"
    )
    for r in doc["servers"]:
        mark = {True: "ok  ", False: "FAIL", None: "--  "}[r.get("ok")]
        print(
            f"  {mark} {r['name']:<26} {r.get('server_name') or r.get('transport'):<16} "
            f"{r.get('tool_count', '')} tools {('· ' + r['error'][:60]) if r.get('error') else ''}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
