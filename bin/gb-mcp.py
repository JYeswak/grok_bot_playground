#!/usr/bin/env python3
"""gb-mcp — make the MCP in-app half programmatic: add servers, proof-call tools, cut allowlists.

Doctrine override (Joshua, explicit, 2026-09-11): the AGENTS.md 'humans change the app'
refusal is lifted FOR MCP wiring only — everything else stays human. Writes here touch
only the credentialed user's OWN MCP config (their servers, their tool allowlists).
Never Bots, routines, descriptions, templates, or any other user's state. Never
enable/publish/share anything.

Method map, mined from the shipped 0.47.0 bundle (client-binary-verified):
  DashboardService/ListSandMcpTools    {server_identifiers[]} — tools per server
  DashboardService/ExecuteSandMcpTool  {server_identifier, tool_name, args,
                                        tool_call_id, agent_id, turn_id}
  DashboardService/ProbeMcpUrl         {url, headers{}} -> {is_available}
  DashboardService/GetMcpConfig        {} -> {config_json, server_metadata_by_name}
  DashboardService/SetMcpConfig        {team_scope, team_id, config_json,
                                        server_ids_by_name{}, server_renames{}}
  GrokBotService/GetGrokBotUserMcpSettings  {} -> {settings}
  GrokBotService/SetGrokBotUserMcpSettings  {settings} — full round-trip; the
    servers[] entry {server_id, custom_instructions?, disabled_tools[]} is the
    allowlist. Empty disabled_tools normalises away server-side (kept = all).

NE-8 rules apply: a 400 is proof the method exists; classify resource-404 vs
route-404; the service prefix matters (List/Execute live on DashboardService,
not GrokBotService).

Auth reuses gb-pull-inventory's client-session reader (in-memory only). No
credential is ever written to disk, a log, or this repo.

Exit codes follow the gb dictionary: 0 OK · 2 USAGE · 3 ENVIRONMENT · 4 UPSTREAM.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = pathlib.Path(__file__).resolve().parent
EXIT_OK, EXIT_USAGE, EXIT_ENVIRONMENT, EXIT_UPSTREAM = 0, 2, 3, 4

DASHBOARD = "aiserver.v1.DashboardService"
GROKBOT = "aiserver.v1.GrokBotService"


def _load():
    spec = importlib.util.spec_from_file_location(
        "gb_pull_inventory", BIN / "gb-pull-inventory.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rpc(
    mod, token: str, service: str, method: str, body: dict, timeout: float = 60.0
) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        f"https://{mod.HOST}/{service}/{method}",
        data=json.dumps(body).encode(),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "connect-protocol-version": "1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode("utf-8", "replace") if e.fp else "")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=1))
    else:
        print(payload["human"].rstrip())


def fail(human: str, as_json: bool, code: int) -> int:
    """Report a refusal on stderr and hand back the exit code the caller chose.

    THIS FUNCTION DID NOT EXIST until 2026-09-12, and it is called from EIGHTEEN sites — every
    error path in this file. So every one of them raised `NameError: name 'fail' is not
    defined`: the handler that exists to report a failure was itself the failure, and
    `gb mcp list` crashed while trying to say it could not read a credential.

    Nothing caught it because nothing ever exercised an error path. The happy path never calls
    `fail`, the selftest never made an RPC refuse, and `--help` never reaches any of it. Dead
    error handling is the most reliably untested code in any tool: it only runs on the day you
    least want a second bug.

    Found by a real-invocation sweep of an installed build, on a machine without the optional
    `cryptography` extra — the one condition that forces the credential path to refuse.
    """
    payload = {"human": human, "error": human, "exit_code": code}
    if as_json:
        print(json.dumps(payload, indent=1))
    else:
        print(f"gb-mcp: {human}", file=sys.stderr)
    return code


def derive_name(url: str) -> str:
    host = urllib.request.urlparse(url).hostname or ""
    first = host.split(".")[0]
    if first.startswith("grokbot-"):
        first = first[len("grokbot-") :]
    if first:
        return first if first.endswith("-mcp") else first + "-mcp"
    return host.replace(".", "-") or "mcp-server"


def cmd_add(mod, token: str, a) -> int:
    name = a.name or derive_name(a.url)
    st, probe = rpc(mod, token, DASHBOARD, "ProbeMcpUrl", {"url": a.url})
    if st != 200 or not isinstance(probe, dict) or not probe.get("isAvailable"):
        return fail(
            f"ProbeMcpUrl -> {st} {str(probe)[:200]} — not adding",
            a.json,
            EXIT_UPSTREAM,
        )
    st, cur = rpc(mod, token, DASHBOARD, "GetMcpConfig", {})
    if st != 200 or not isinstance(cur, dict) or "configJson" not in cur:
        return fail(f"GetMcpConfig -> {st} {str(cur)[:200]}", a.json, EXIT_UPSTREAM)
    cfg = json.loads(cur["configJson"])
    servers = cfg.setdefault("mcpServers", {})
    if name in servers:
        return fail(
            f"{name!r} already in config — refusing to overwrite", a.json, EXIT_USAGE
        )
    servers[name] = {"url": a.url}
    ids = {
        k: int(v["serverId"])
        for k, v in (cur.get("serverMetadataByName") or {}).items()
        if v.get("serverId")
    }
    st, res = rpc(
        mod,
        token,
        DASHBOARD,
        "SetMcpConfig",
        {
            "teamScope": False,
            "teamId": 0,
            "configJson": json.dumps(cfg, indent=2),
            "serverIdsByName": ids,
            "serverRenames": {},
        },
    )
    if st != 200:
        return fail(f"SetMcpConfig -> {st} {str(res)[:300]}", a.json, EXIT_UPSTREAM)
    st, cur2 = rpc(mod, token, DASHBOARD, "GetMcpConfig", {})
    meta = (
        (cur2 if isinstance(cur2, dict) else {}).get("serverMetadataByName") or {}
    ).get(name, {})
    payload = {
        "status": "OK",
        "server": name,
        "url": a.url,
        "probe": {"isAvailable": True},
        "serverId": meta.get("serverId"),
        "servers_now": len(json.loads(cur2["configJson"])["mcpServers"]),
    }
    payload["human"] = (
        f"added {name} (serverId {meta.get('serverId')}) — "
        f"probe isAvailable=true, "
        f"{payload['servers_now']} servers in config"
    )
    emit(payload, a.json)
    return EXIT_OK


def cmd_list(mod, token: str, a) -> int:
    st, cur = rpc(mod, token, DASHBOARD, "GetMcpConfig", {})
    if st != 200 or not isinstance(cur, dict) or "configJson" not in cur:
        return fail(f"GetMcpConfig -> {st} {str(cur)[:200]}", a.json, EXIT_UPSTREAM)
    cfg = json.loads(cur["configJson"])
    meta = cur.get("serverMetadataByName") or {}
    rows = []
    for name in sorted((cfg.get("mcpServers") or {}).keys()):
        spec = cfg["mcpServers"][name] or {}
        rows.append(
            {
                "name": name,
                "serverId": (meta.get(name) or {}).get("serverId"),
                "url": spec.get("url")
                or (
                    "stdio:" + str(spec.get("command", ""))
                    if spec.get("command")
                    else None
                ),
            }
        )
    payload = {"status": "OK", "servers": rows, "count": len(rows)}
    payload["human"] = "\n".join(
        [f"{len(rows)} servers in account config:"]
        + [f"  {r['name']:<16}{str(r['serverId']):<10}{r['url']}" for r in rows]
    )
    emit(payload, a.json)
    return EXIT_OK


def cmd_tools(mod, token: str, a) -> int:
    st, r = rpc(
        mod, token, DASHBOARD, "ListSandMcpTools", {"serverIdentifiers": [a.server]}
    )
    if st != 200 or not isinstance(r, dict):
        return fail(f"ListSandMcpTools -> {st} {str(r)[:300]}", a.json, EXIT_UPSTREAM)
    rows = r.get("servers") or []
    if not rows:
        return fail(f"no server row for {a.server!r}", a.json, EXIT_UPSTREAM)
    sv = rows[0]
    names = [t.get("name") for t in sv.get("tools", [])]
    payload = {
        "status": "OK",
        "server": a.server,
        "identifier": sv.get("serverIdentifier"),
        "connection": sv.get("status"),
        "tools": names,
    }
    payload["human"] = (
        f"{sv.get('serverIdentifier')} "
        f"{sv.get('status')} — {len(names)} tools: " + ", ".join(names)
    )
    emit(payload, a.json)
    return EXIT_OK


def resolve_agent(mod, token: str, want: str | None) -> str | None:
    st, r = mod.rpc(token, "ListGrokBotAgents", {})
    if st != 200 or not isinstance(r, dict):
        return None
    agents = r.get("agents", [])
    if not agents:
        return None
    if want:
        for x in agents:
            if want in (str(x.get("id")), x.get("legacyAgentId"), x.get("name") or ""):
                return str(x.get("id"))
        return None
    return str(agents[0].get("id"))


def cmd_call(mod, token: str, a) -> int:
    try:
        args = json.loads(a.args_json)
    except json.JSONDecodeError as e:
        return fail(f"args are not JSON: {e}", a.json, EXIT_USAGE)
    tool = a.tool
    if "-" not in tool:
        st, r = rpc(
            mod, token, DASHBOARD, "ListSandMcpTools", {"serverIdentifiers": [a.server]}
        )
        names = (
            [
                t.get("name")
                for s in (r.get("servers") or [])
                for t in s.get("tools", [])
            ]
            if st == 200
            else []
        )
        prefixed = f"{a.server}-{tool}"
        if prefixed in names:
            tool = prefixed
    agent = resolve_agent(mod, token, a.agent)
    if agent is None:
        return fail("no durable agent to attribute the call to", a.json, EXIT_UPSTREAM)
    st, r = rpc(
        mod,
        token,
        DASHBOARD,
        "ExecuteSandMcpTool",
        {
            "serverIdentifier": a.server,
            "toolName": tool,
            "args": args,
            "toolCallId": str(uuid.uuid4()),
            "agentId": agent,
            "turnId": str(uuid.uuid4()),
        },
        timeout=90.0,
    )
    if st != 200 or not isinstance(r, dict):
        return fail(f"ExecuteSandMcpTool -> {st} {str(r)[:300]}", a.json, EXIT_UPSTREAM)
    try:
        content = r["result"]["success"]["content"]
        texts = [c.get("text", {}).get("text", "") for c in content]
    except (KeyError, TypeError):
        texts = [json.dumps(r)[:500]]
    payload = {
        "status": "OK",
        "server": a.server,
        "tool": tool,
        "agent_id": agent,
        "output_chars": sum(len(t) for t in texts),
        "output_head": (texts[0][:600] if texts else ""),
    }
    payload["human"] = (
        f"{a.server}/{tool} -> 200 via agent {agent} "
        f"({payload['output_chars']} chars)\n"
        + (texts[0][:1200] if texts else "(empty)")
    )
    emit(payload, a.json)
    return EXIT_OK


def cmd_allowlist(mod, token: str, a) -> int:
    keep = list(a.keep or []) + list(a.keep_flag or [])
    st, cur = rpc(mod, token, DASHBOARD, "GetMcpConfig", {})
    if st != 200 or not isinstance(cur, dict):
        return fail(f"GetMcpConfig -> {st} {str(cur)[:200]}", a.json, EXIT_UPSTREAM)
    meta = (cur.get("serverMetadataByName") or {}).get(a.server)
    if not meta or not meta.get("serverId"):
        return fail(f"{a.server!r} not in account config", a.json, EXIT_USAGE)
    server_id = meta["serverId"]
    st, r = rpc(
        mod, token, DASHBOARD, "ListSandMcpTools", {"serverIdentifiers": [a.server]}
    )
    if st != 200 or not isinstance(r, dict) or not r.get("servers"):
        return fail(f"ListSandMcpTools -> {st}", a.json, EXIT_UPSTREAM)
    full = [t.get("name") for t in (r["servers"][0].get("tools") or [])]
    prefix = a.server + "-"
    shorts = [n[len(prefix) :] if n.startswith(prefix) else n for n in full]
    kept_full = [f for f, s in zip(full, shorts) if f in keep or s in keep]
    missing = [k for k in keep if k not in full and k not in shorts]
    if missing:
        return fail(
            f"unknown tools for {a.server}: {missing} " f"(known: {shorts})",
            a.json,
            EXIT_USAGE,
        )
    disabled = [f for f in full if f not in kept_full]
    st, cur2 = mod.rpc(token, "GetGrokBotUserMcpSettings", {})
    if st != 200 or not isinstance(cur2, dict):
        return fail(f"GetGrokBotUserMcpSettings -> {st}", a.json, EXIT_UPSTREAM)
    settings = cur2["settings"]
    note = a.note or (
        f"Allowlist: keep {len(kept_full)}/{len(full)} "
        f"({', '.join(shorts) if not disabled else ', '.join(kept_full)})."
    )
    entry = {"serverId": server_id, "customInstructions": note}
    if disabled:
        entry["disabledTools"] = disabled
    settings["servers"] = [
        e for e in settings.get("servers", []) if e.get("serverId") != server_id
    ] + [entry]
    st, res = rpc(
        mod, token, GROKBOT, "SetGrokBotUserMcpSettings", {"settings": settings}
    )
    if st != 200:
        return fail(
            f"SetGrokBotUserMcpSettings -> {st} {str(res)[:300]}", a.json, EXIT_UPSTREAM
        )
    echo = [
        e
        for e in (res.get("settings") or {}).get("servers", [])
        if e.get("serverId") == server_id
    ]
    payload = {
        "status": "OK",
        "server": a.server,
        "serverId": server_id,
        "kept": kept_full,
        "disabled": disabled,
        "recorded": bool(echo),
    }
    payload["human"] = (
        f"{a.server} allowlist: keep {len(kept_full)}, "
        f"disable {len(disabled)}"
        + (f" ({', '.join(disabled)})" if disabled else " (already minimal)")
        + f" — recorded={bool(echo)}"
    )
    emit(payload, a.json)
    return EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser(prog="gb-mcp", description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="verb", metavar="<verb>")
    p = sub.add_parser("add", help="probe a URL and append it to the config")
    p.add_argument("url")
    p.add_argument("--name", default=None)
    p = sub.add_parser("list", help="dump account config servers (name, id, url)")
    p = sub.add_parser("tools", help="list a server's tools via the account")
    p.add_argument("server")
    p = sub.add_parser("call", help="proof-call one tool via the account")
    p.add_argument("server")
    p.add_argument("tool")
    p.add_argument("args_json")
    p.add_argument("--agent", default=None)
    p = sub.add_parser("allowlist", help="cut a server's tool allowlist")
    p.add_argument("server")
    p.add_argument(
        "keep", nargs="*", default=[], help="tools to keep (short or full names)"
    )
    p.add_argument("--keep-flag", dest="keep_flag", action="append", default=None)
    p.add_argument("--note", default=None)
    a = ap.parse_args()
    if not a.verb:
        ap.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        mod = _load()
    except Exception as e:
        return fail(f"cannot load session reader: {e}", a.json, EXIT_ENVIRONMENT)
    try:
        token = mod.access_token(mod.SUPPORT)
    except Exception as e:
        return fail(f"no client session: {e}", a.json, EXIT_ENVIRONMENT)
    if a.verb == "add":
        return cmd_add(mod, token, a)
    if a.verb == "tools":
        return cmd_tools(mod, token, a)
    if a.verb == "call":
        return cmd_call(mod, token, a)
    if a.verb == "list":
        return cmd_list(mod, token, a)
    return cmd_allowlist(mod, token, a)


if __name__ == "__main__":
    sys.exit(main())
