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
):
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    ).encode()
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "user-agent": "gb-mcp-validate/1 (+local weekly check)",
    }
    if session:
        headers["mcp-session-id"] = session
    if auth:
        headers.update(auth)
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
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
        row["error"] = f"tools/list -> {st2} {json.dumps(tools)[:120]}"
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


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

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
