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
import hashlib
import importlib.util
import json
import pathlib
import sys
import urllib.error
import urllib.parse
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
    host = urllib.parse.urlparse(url).hostname or ""
    first = host.split(".")[0]
    if first.startswith("grokbot-"):
        first = first[len("grokbot-") :]
    if first:
        return first if first.endswith("-mcp") else first + "-mcp"
    return host.replace(".", "-") or "mcp-server"


def digest(value) -> str:
    """Hash a canonical value so receipts prove equality without logging private data."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def proof_trace(
    operation: str, server: str, recovery_argv: list[str], token: str
) -> dict:
    """Build the redacted receipt shared by every MCP proof stage."""
    return {
        "schema": "gb-mcp-proof/1",
        "operation": operation,
        "server": server,
        "server_id": None,
        "config_presence": "UNKNOWN",
        "config_digest": None,
        "target_bot_id": None,
        "transport_state": "UNKNOWN",
        "transport_http_status": None,
        "auth_state": "session-present" if token else "session-missing",
        "expected_tools": [],
        "observed_tools": [],
        "advertised_tools": [],
        "inventory_digest": None,
        "approval_digest": None,
        "allowlist_diff": {},
        "scope_diff": {
            "required": [],
            "advertised": [],
            "enabled": [],
            "disabled": [],
            "unenforced": [],
        },
        "scope_enforcement": "NONE",
        "tool_exposure_state": "NOT_PROVEN",
        "execution_state": "NOT_RUN",
        "least_privilege_state": "NOT_PROVEN",
        "tool_protocol_status": "NOT_RUN",
        "semantic_observable_digest": None,
        "attribution": "NOT_RUN",
        "request_id": str(uuid.uuid4()),
        "readback_id": None,
        "side_effect_class": "none",
        "enforcement_authority": "signed-in-user account MCP settings",
        "claim_ceiling": "no capability proof",
        "verdict": "ERROR",
        "exit": EXIT_UPSTREAM,
        "recovery_argv": recovery_argv,
    }


def finish_trace(
    trace: dict, human: str, as_json: bool, code: int, verdict: str | None = None
) -> int:
    trace["verdict"] = verdict or ("PASS" if code == EXIT_OK else "FAIL")
    trace["exit"] = code
    trace["human"] = human
    if code != EXIT_OK:
        trace["error"] = human
    if as_json:
        print(json.dumps(trace, indent=1, sort_keys=True))
    elif code == EXIT_OK:
        print(human.rstrip())
    else:
        print(f"gb-mcp: {human}", file=sys.stderr)
    return code


def expected_tools(server: str, explicit: list[str] | None) -> list[str]:
    if explicit:
        return sorted(set(explicit))
    spec = importlib.util.spec_from_file_location(
        "gb_mcp_envelopes", BIN / "gb-mcp-envelopes.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for candidate in (server, server.removeprefix("user-")):
        if candidate in mod.TOOLS_LIST:
            return sorted(set(mod.TOOLS_LIST[candidate]))
    return []


def normalized_disabled(entry):
    if not isinstance(entry, dict):
        return None
    values = entry.get("disabledTools")
    if values is None:
        return []
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value for value in values
    ):
        return None
    return sorted(values)


def unenforced_scope_problem(a, trace: dict) -> str | None:
    scopes = list(getattr(a, "require_scope", None) or [])
    if not scopes:
        return None
    allowed = {"account", "resource", "host", "transitive", "bot"}
    kinds = []
    invalid = []
    for scope in scopes:
        kind = scope.split(":", 1)[0] if isinstance(scope, str) else "invalid"
        if kind not in allowed:
            invalid.append(kind)
        kinds.append(kind)
    kinds = sorted(set(kinds))
    trace["scope_diff"]["required"] = kinds
    trace["scope_diff"]["unenforced"] = kinds
    trace["scope_enforcement"] = "UNENFORCED"
    trace["least_privilege_state"] = "UNENFORCED"
    trace["claim_ceiling"] = (
        "platform exposes only an account-wide tool-name allowlist; requested scopes are unenforced"
    )
    if invalid:
        return f"unsupported scope kinds: {sorted(set(invalid))}"
    return f"requested scopes are not enforceable at the MCP account boundary: {kinds}"


def configured_server(mod, token: str, server: str, trace: dict):
    st, cur = rpc(mod, token, DASHBOARD, "GetMcpConfig", {})
    trace["transport_http_status"] = st
    if (
        st != 200
        or not isinstance(cur, dict)
        or not isinstance(cur.get("configJson"), str)
    ):
        trace["config_presence"] = "ERROR"
        return None, "GetMcpConfig did not return a usable configuration"
    try:
        cfg = json.loads(cur["configJson"])
    except (TypeError, json.JSONDecodeError):
        trace["config_presence"] = "ERROR"
        return None, "GetMcpConfig returned invalid configJson"
    servers = cfg.get("mcpServers") if isinstance(cfg, dict) else None
    metadata = cur.get("serverMetadataByName") or {}
    server_spec = servers.get(server) if isinstance(servers, dict) else None
    meta = metadata.get(server) if isinstance(metadata, dict) else None
    server_id = meta.get("serverId") if isinstance(meta, dict) else None
    if not isinstance(server_spec, dict) or server_id in (None, "", 0):
        trace["config_presence"] = "ABSENT"
        return None, f"{server!r} is absent from account config or has no server id"
    trace["config_presence"] = "PRESENT"
    trace["server_id"] = server_id
    trace["config_digest"] = digest(
        {"server": server, "server_id": server_id, "spec": server_spec}
    )
    return {"server_id": server_id, "spec": server_spec}, None


def inspect_inventory(mod, token: str, server: str, expected: list[str], trace: dict):
    trace["expected_tools"] = sorted(expected)
    if not expected:
        return None, "no expected manifest tools declared; use --expect-tool"
    config, problem = configured_server(mod, token, server, trace)
    if problem:
        return None, problem
    st, response = rpc(
        mod, token, DASHBOARD, "ListSandMcpTools", {"serverIdentifiers": [server]}
    )
    trace["transport_http_status"] = st
    trace["transport_state"] = "AVAILABLE" if st == 200 else "UNAVAILABLE"
    if st != 200 or not isinstance(response, dict):
        return None, "ListSandMcpTools transport did not return a JSON object"
    raw_rows = response.get("servers")
    if not isinstance(raw_rows, list):
        trace["tool_exposure_state"] = "MALFORMED"
        return None, "ListSandMcpTools returned no server list"
    rows = [row for row in raw_rows if isinstance(row, dict)]
    matching = [
        row
        for row in rows
        if server in (row.get("serverIdentifier"), row.get("rowServerIdentifier"))
    ]
    if len(matching) != 1:
        return None, f"ListSandMcpTools returned no unique row for {server!r}"
    row = matching[0]
    connection = str(row.get("status") or "").lower()
    trace["transport_state"] = connection.upper() or "UNKNOWN"
    if connection != "connected":
        trace["tool_exposure_state"] = "UNAVAILABLE"
        return None, f"{server!r} is not connected (state={connection or 'missing'})"
    tools = row.get("tools")
    if not isinstance(tools, list) or not tools:
        trace["tool_exposure_state"] = "EMPTY"
        return None, f"{server!r} exposes an empty tool inventory"
    actual_identifier = str(row.get("serverIdentifier") or server)
    full_names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
    if len(full_names) != len(tools) or any(
        not isinstance(name, str) or not name for name in full_names
    ):
        trace["tool_exposure_state"] = "MALFORMED"
        return None, f"{server!r} returned malformed tool definitions"
    if len(set(full_names)) != len(full_names):
        return None, f"{server!r} returned duplicate tool names"

    prefixes = (server + "-", actual_identifier + "-")
    short_names = []
    for name in full_names:
        short = name
        for prefix in prefixes:
            if short.startswith(prefix):
                short = short[len(prefix) :]
                break
        short_names.append(short)
    missing = sorted(set(expected) - set(short_names))
    unexpected = sorted(set(short_names) - set(expected))
    trace["observed_tools"] = sorted(short_names)
    trace["advertised_tools"] = sorted(full_names)
    trace["scope_diff"]["required"] = sorted(expected)
    trace["scope_diff"]["advertised"] = sorted(short_names)
    trace["inventory_digest"] = digest(sorted(short_names))
    trace["readback_id"] = digest(
        {"config": trace["config_digest"], "inventory": trace["inventory_digest"]}
    )
    trace["allowlist_diff"] = {"missing": missing, "unexpected": unexpected}
    if missing or unexpected:
        trace["tool_exposure_state"] = "DRIFT"
        return None, f"tool inventory drift: missing={missing}, unexpected={unexpected}"
    trace["tool_exposure_state"] = "EXACT"
    trace["approval_digest"] = digest(
        {
            "config": trace["config_digest"],
            "inventory": trace["inventory_digest"],
            "expected": sorted(expected),
        }
    )
    trace["claim_ceiling"] = "connected, exact manifest tool inventory"
    return {
        "config": config,
        "row": row,
        "tools": tools,
        "full_names": full_names,
        "short_names": short_names,
    }, None


def cmd_add(mod, token: str, a) -> int:
    name = a.name or derive_name(a.url)
    trace = proof_trace(
        "add", name, ["gb", "mcp", "add", "<redacted-url>", "--name", name], token
    )
    trace["side_effect_class"] = "account-config-write"
    st, probe = rpc(mod, token, DASHBOARD, "ProbeMcpUrl", {"url": a.url})
    trace["transport_http_status"] = st
    trace["transport_state"] = (
        "AVAILABLE"
        if st == 200 and isinstance(probe, dict) and probe.get("isAvailable") is True
        else "UNAVAILABLE"
    )
    if trace["transport_state"] != "AVAILABLE":
        return finish_trace(
            trace,
            "ProbeMcpUrl did not prove availability; not adding",
            a.json,
            EXIT_UPSTREAM,
        )

    st, current = rpc(mod, token, DASHBOARD, "GetMcpConfig", {})
    if (
        st != 200
        or not isinstance(current, dict)
        or not isinstance(current.get("configJson"), str)
    ):
        return finish_trace(
            trace,
            "GetMcpConfig did not return a usable configuration",
            a.json,
            EXIT_UPSTREAM,
        )
    try:
        cfg = json.loads(current["configJson"])
    except (TypeError, json.JSONDecodeError):
        return finish_trace(
            trace, "GetMcpConfig returned invalid configJson", a.json, EXIT_UPSTREAM
        )
    servers = cfg.setdefault("mcpServers", {}) if isinstance(cfg, dict) else None
    if not isinstance(servers, dict):
        return finish_trace(
            trace, "GetMcpConfig has no mcpServers object", a.json, EXIT_UPSTREAM
        )
    if name in servers:
        return finish_trace(
            trace, f"{name!r} already exists; refusing to overwrite", a.json, EXIT_USAGE
        )
    servers[name] = {"url": a.url}
    metadata = current.get("serverMetadataByName") or {}
    if not isinstance(metadata, dict):
        return finish_trace(
            trace,
            "GetMcpConfig returned malformed server metadata",
            a.json,
            EXIT_UPSTREAM,
        )
    try:
        ids = {
            key: int(value["serverId"])
            for key, value in metadata.items()
            if isinstance(value, dict) and value.get("serverId")
        }
    except (TypeError, ValueError):
        return finish_trace(
            trace, "GetMcpConfig returned a malformed server id", a.json, EXIT_UPSTREAM
        )
    st, _ = rpc(
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
        return finish_trace(
            trace,
            f"SetMcpConfig transport failed with HTTP {st}",
            a.json,
            EXIT_UPSTREAM,
        )

    st, readback = rpc(mod, token, DASHBOARD, "GetMcpConfig", {})
    if (
        st != 200
        or not isinstance(readback, dict)
        or not isinstance(readback.get("configJson"), str)
    ):
        return finish_trace(
            trace, "post-write GetMcpConfig readback failed", a.json, EXIT_UPSTREAM
        )
    try:
        readback_cfg = json.loads(readback["configJson"])
    except (TypeError, json.JSONDecodeError):
        return finish_trace(
            trace,
            "post-write GetMcpConfig returned invalid configJson",
            a.json,
            EXIT_UPSTREAM,
        )
    readback_servers = (
        readback_cfg.get("mcpServers") if isinstance(readback_cfg, dict) else None
    )
    readback_metadata = readback.get("serverMetadataByName") or {}
    if not isinstance(readback_servers, dict) or not isinstance(
        readback_metadata, dict
    ):
        return finish_trace(
            trace, "post-write config readback was malformed", a.json, EXIT_UPSTREAM
        )
    recorded = readback_servers.get(name)
    meta = readback_metadata.get(name) or {}
    server_id = meta.get("serverId") if isinstance(meta, dict) else None
    if recorded != {"url": a.url} or server_id in (None, "", 0):
        return finish_trace(
            trace,
            "post-write config readback did not exactly record the server",
            a.json,
            EXIT_UPSTREAM,
        )

    trace.update(
        {
            "status": "CONFIGURED",
            "server_id": server_id,
            "config_presence": "PRESENT",
            "config_digest": digest(
                {"server": name, "server_id": server_id, "spec": recorded}
            ),
            "readback_id": digest({"server_id": server_id, "spec": recorded}),
            "claim_ceiling": (
                "CONFIGURED only; tools, execution, and least-privilege readback are not proven"
            ),
        }
    )
    return finish_trace(
        trace,
        f"configured {name} (server id {server_id}); run tools, call, and allowlist proofs",
        a.json,
        EXIT_OK,
        "CONFIGURED",
    )


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
    expected = expected_tools(a.server, getattr(a, "expect_tool", None))
    recovery = ["gb", "mcp", "tools", a.server]
    for name in expected:
        recovery.extend(["--expect-tool", name])
    trace = proof_trace("tools", a.server, recovery, token)
    inventory, problem = inspect_inventory(mod, token, a.server, expected, trace)
    if problem:
        return finish_trace(trace, problem, a.json, EXIT_UPSTREAM)
    trace["status"] = "OK"
    return finish_trace(
        trace,
        f"{a.server} connected with exact {len(inventory['full_names'])}-tool manifest",
        a.json,
        EXIT_OK,
    )


def resolve_agent(mod, token: str, want: str | None) -> str | None:
    if not want:
        return None
    st, response = rpc(mod, token, GROKBOT, "ListGrokBotAgents", {})
    if st != 200 or not isinstance(response, dict):
        return None
    for agent in response.get("agents", []):
        if not isinstance(agent, dict) or agent.get("id") in (None, ""):
            continue
        identities = (
            str(agent.get("id")),
            str(agent.get("legacyAgentId") or ""),
            str(agent.get("name") or ""),
        )
        if want in identities:
            return str(agent["id"])
    return None


def cmd_call(mod, token: str, a) -> int:
    recovery = [
        "gb",
        "mcp",
        "call",
        a.server,
        a.tool,
        "<redacted-args-json>",
        "--agent",
        "<target-bot>",
        "--expect-text",
        "<redacted-observable>",
    ]
    trace = proof_trace("call", a.server, recovery, token)
    trace["side_effect_class"] = "unknown"
    scope_problem = unenforced_scope_problem(a, trace)
    if scope_problem:
        return finish_trace(trace, scope_problem, a.json, EXIT_USAGE)
    try:
        args = json.loads(a.args_json)
    except json.JSONDecodeError:
        return finish_trace(trace, "args are not valid JSON", a.json, EXIT_USAGE)
    if not isinstance(args, dict):
        return finish_trace(trace, "args JSON must be an object", a.json, EXIT_USAGE)
    observable = getattr(a, "expect_text", None)
    if not isinstance(observable, str) or not observable:
        return finish_trace(
            trace,
            "call requires a nonempty --expect-text semantic observable",
            a.json,
            EXIT_USAGE,
        )
    if not a.agent:
        return finish_trace(
            trace, "call requires --agent for explicit attribution", a.json, EXIT_USAGE
        )

    expected = expected_tools(a.server, getattr(a, "expect_tool", None))
    inventory, problem = inspect_inventory(mod, token, a.server, expected, trace)
    if problem:
        return finish_trace(trace, problem, a.json, EXIT_UPSTREAM)
    matches = [
        (full, short, definition)
        for full, short, definition in zip(
            inventory["full_names"], inventory["short_names"], inventory["tools"]
        )
        if a.tool in (full, short)
    ]
    if len(matches) != 1:
        return finish_trace(
            trace,
            f"tool {a.tool!r} is not uniquely exposed by {a.server}",
            a.json,
            EXIT_USAGE,
        )
    tool, _, definition = matches[0]
    annotations = (
        definition.get("annotations") if isinstance(definition, dict) else None
    )
    read_only = (
        isinstance(annotations, dict) and annotations.get("readOnlyHint") is True
    )
    destructive = (
        isinstance(annotations, dict) and annotations.get("destructiveHint") is True
    )
    if not read_only or destructive:
        trace["side_effect_class"] = "write-or-unknown"
        trace["claim_ceiling"] = (
            "REFUSED: tool annotations do not prove a non-destructive read"
        )
        return finish_trace(
            trace,
            "tool call refused: readOnlyHint=true and destructiveHint!=true are required",
            a.json,
            EXIT_USAGE,
        )
    trace["side_effect_class"] = "read"

    agent = resolve_agent(mod, token, a.agent)
    if agent is None:
        trace["attribution"] = "MISMATCH"
        return finish_trace(
            trace, "target Bot selector did not resolve exactly", a.json, EXIT_UPSTREAM
        )
    trace["target_bot_id"] = agent
    trace["attribution"] = "REQUEST_BOUND"
    tool_call_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    trace["request_id"] = tool_call_id
    st, response = rpc(
        mod,
        token,
        DASHBOARD,
        "ExecuteSandMcpTool",
        {
            "serverIdentifier": a.server,
            "toolName": tool,
            "args": args,
            "toolCallId": tool_call_id,
            "agentId": agent,
            "turnId": turn_id,
        },
        timeout=90.0,
    )
    trace["transport_http_status"] = st
    if st != 200 or not isinstance(response, dict):
        trace["transport_state"] = "UNAVAILABLE"
        trace["execution_state"] = "TRANSPORT_ERROR"
        return finish_trace(
            trace,
            f"ExecuteSandMcpTool transport failed with HTTP {st}",
            a.json,
            EXIT_UPSTREAM,
        )
    trace["transport_state"] = "CONNECTED"
    result = response.get("result")
    success = result.get("success") if isinstance(result, dict) else None
    if not isinstance(success, dict):
        trace["tool_protocol_status"] = (
            "TOOL_ERROR"
            if isinstance(result, dict)
            and any(key in result for key in ("error", "rejected", "permissionDenied"))
            else "MISSING_SUCCESS"
        )
        trace["execution_state"] = trace["tool_protocol_status"]
        return finish_trace(
            trace,
            "HTTP 200 response did not contain result.success",
            a.json,
            EXIT_UPSTREAM,
        )
    if success.get("isError") is True or success.get("is_error") is True:
        trace["tool_protocol_status"] = "TOOL_ERROR"
        trace["execution_state"] = "TOOL_ERROR"
        return finish_trace(
            trace, "tool reported isError at HTTP 200", a.json, EXIT_UPSTREAM
        )
    content = success.get("content")
    if not isinstance(content, list) or not content:
        trace["tool_protocol_status"] = "EMPTY_CONTENT"
        trace["execution_state"] = "EMPTY_CONTENT"
        return finish_trace(
            trace, "tool success content is missing or empty", a.json, EXIT_UPSTREAM
        )
    texts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        value = item.get("text")
        if isinstance(value, dict):
            value = value.get("text")
        if isinstance(value, str) and value.strip():
            texts.append(value)
    if not texts:
        trace["tool_protocol_status"] = "EMPTY_CONTENT"
        trace["execution_state"] = "EMPTY_CONTENT"
        return finish_trace(
            trace, "tool success contains no nonempty text", a.json, EXIT_UPSTREAM
        )
    joined = "\n".join(texts)
    if observable not in joined:
        trace["tool_protocol_status"] = "SEMANTIC_MISMATCH"
        trace["execution_state"] = "SEMANTIC_MISMATCH"
        return finish_trace(
            trace, "declared semantic observable was absent", a.json, EXIT_UPSTREAM
        )

    echoed_agent = response.get("agentId")
    echoed_call = response.get("toolCallId")
    if echoed_agent is not None and str(echoed_agent) != agent:
        trace["attribution"] = "MISMATCH"
        trace["execution_state"] = "ATTRIBUTION_MISMATCH"
        return finish_trace(
            trace,
            "response attribution did not match target Bot",
            a.json,
            EXIT_UPSTREAM,
        )
    if echoed_call is not None and str(echoed_call) != tool_call_id:
        trace["attribution"] = "MISMATCH"
        trace["execution_state"] = "ATTRIBUTION_MISMATCH"
        return finish_trace(
            trace, "response request id did not match tool call", a.json, EXIT_UPSTREAM
        )

    trace.update(
        {
            "status": "OK",
            "tool": tool,
            "tool_protocol_status": "SUCCESS",
            "execution_state": "SEMANTIC_PASS",
            "least_privilege_state": "PARTIAL",
            "scope_enforcement": "TOOL_READ_ONLY_ANNOTATION",
            "semantic_observable_digest": digest(
                {"observable": observable, "content": joined}
            ),
            "attribution": "MATCH" if echoed_agent is not None else "REQUEST_BOUND",
            "readback_id": digest(
                {"tool_call_id": tool_call_id, "protocol_status": "SUCCESS"}
            ),
            "claim_ceiling": (
                "read-only tool execution; account/resource/host/transitive scopes unverified"
            ),
        }
    )
    return finish_trace(
        trace,
        f"{a.server}/{tool} returned the declared observable via Bot {agent}",
        a.json,
        EXIT_OK,
    )


def cmd_allowlist(mod, token: str, a) -> int:
    keep = list(a.keep or []) + list(a.keep_flag or [])
    recovery = ["gb", "mcp", "allowlist", a.server]
    for name in keep:
        recovery.extend(["--keep-flag", name])
    trace = proof_trace("allowlist", a.server, recovery, token)
    trace["side_effect_class"] = "account-settings-write"
    scope_problem = unenforced_scope_problem(a, trace)
    if scope_problem:
        return finish_trace(trace, scope_problem, a.json, EXIT_USAGE)
    if not keep:
        return finish_trace(
            trace, "allowlist requires at least one kept tool", a.json, EXIT_USAGE
        )
    if any("*" in name for name in keep):
        return finish_trace(
            trace, "wildcards are forbidden in an MCP allowlist", a.json, EXIT_USAGE
        )
    if len(set(keep)) != len(keep):
        return finish_trace(
            trace, "duplicate kept tools are not an exact allowlist", a.json, EXIT_USAGE
        )

    expected = expected_tools(a.server, getattr(a, "expect_tool", None))
    inventory, problem = inspect_inventory(mod, token, a.server, expected, trace)
    if problem:
        return finish_trace(trace, problem, a.json, EXIT_UPSTREAM)
    resolved = [
        full
        for full, short in zip(inventory["full_names"], inventory["short_names"])
        if full in keep or short in keep
    ]
    missing = sorted(
        name
        for name in keep
        if name not in inventory["full_names"] and name not in inventory["short_names"]
    )
    if missing:
        return finish_trace(trace, f"unknown kept tools: {missing}", a.json, EXIT_USAGE)
    kept_full = sorted(resolved)
    disabled = sorted(set(inventory["full_names"]) - set(kept_full))
    trace["scope_diff"].update(
        {
            "required": kept_full,
            "advertised": sorted(inventory["full_names"]),
            "enabled": kept_full,
            "disabled": disabled,
        }
    )
    server_id = inventory["config"]["server_id"]

    st, current = rpc(mod, token, GROKBOT, "GetGrokBotUserMcpSettings", {})
    if (
        st != 200
        or not isinstance(current, dict)
        or not isinstance(current.get("settings"), dict)
    ):
        return finish_trace(
            trace,
            "GetGrokBotUserMcpSettings did not return settings",
            a.json,
            EXIT_UPSTREAM,
        )
    settings = current["settings"]
    current_servers = settings.get("servers") or []
    if not isinstance(current_servers, list) or any(
        not isinstance(entry, dict) for entry in current_servers
    ):
        return finish_trace(
            trace,
            "current MCP settings contain malformed server entries",
            a.json,
            EXIT_UPSTREAM,
        )
    note = (
        a.note
        or f"Allowlist: exact keep {len(kept_full)}/{len(inventory['full_names'])}."
    )
    entry = {"serverId": server_id, "customInstructions": note}
    if disabled:
        entry["disabledTools"] = disabled
    settings["servers"] = [
        item for item in current_servers if item.get("serverId") != server_id
    ] + [entry]

    approved_config = trace["config_digest"]
    approved_inventory = trace["inventory_digest"]
    fresh_trace = proof_trace("allowlist-revalidate", a.server, [], token)
    _, problem = inspect_inventory(mod, token, a.server, expected, fresh_trace)
    if (
        problem
        or fresh_trace["config_digest"] != approved_config
        or fresh_trace["inventory_digest"] != approved_inventory
    ):
        trace["tool_protocol_status"] = "STALE_APPROVAL"
        trace["least_privilege_state"] = "STALE_APPROVAL"
        return finish_trace(
            trace,
            "config or inventory drifted after approval; refusing write",
            a.json,
            EXIT_UPSTREAM,
        )
    trace["approval_digest"] = digest(
        {
            "config": approved_config,
            "inventory": approved_inventory,
            "kept": kept_full,
            "disabled": disabled,
        }
    )

    st, response = rpc(
        mod, token, GROKBOT, "SetGrokBotUserMcpSettings", {"settings": settings}
    )
    trace["transport_http_status"] = st
    set_rows = []
    if (
        st == 200
        and isinstance(response, dict)
        and isinstance(response.get("settings"), dict)
    ):
        candidate_rows = response["settings"].get("servers")
        if isinstance(candidate_rows, list):
            set_rows = candidate_rows
    echoes = [
        item
        for item in set_rows
        if isinstance(item, dict) and item.get("serverId") == server_id
    ]
    echo_disabled = normalized_disabled(echoes[0]) if len(echoes) == 1 else None
    echo_ok = len(echoes) == 1 and echo_disabled == disabled

    read_st, readback = rpc(mod, token, GROKBOT, "GetGrokBotUserMcpSettings", {})
    if (
        read_st != 200
        or not isinstance(readback, dict)
        or not isinstance(readback.get("settings"), dict)
    ):
        trace["allowlist_diff"] = {
            "set_echo_present": bool(echoes),
            "readback": "unavailable",
        }
        trace["least_privilege_state"] = "READBACK_UNAVAILABLE"
        return finish_trace(
            trace, "independent allowlist readback failed", a.json, EXIT_UPSTREAM
        )
    read_rows = readback["settings"].get("servers")
    readback_shape_valid = isinstance(read_rows, list)
    matches = [
        item
        for item in (read_rows if readback_shape_valid else [])
        if isinstance(item, dict) and item.get("serverId") == server_id
    ]
    actual_disabled_value = (
        normalized_disabled(matches[0]) if len(matches) == 1 else None
    )
    actual_shape_valid = actual_disabled_value is not None
    actual_disabled = actual_disabled_value or []
    actual_kept = (
        sorted(set(inventory["full_names"]) - set(actual_disabled))
        if len(matches) == 1 and actual_shape_valid
        else []
    )
    readback_shape_valid = readback_shape_valid and actual_shape_valid
    diff = {
        "set_echo_present": bool(echoes),
        "set_echo_exact": echo_ok,
        "server_id_matches": len(matches) == 1,
        "readback_shape_valid": readback_shape_valid,
        "missing_kept": sorted(set(kept_full) - set(actual_kept)),
        "unexpected_kept": sorted(set(actual_kept) - set(kept_full)),
        "missing_disabled": sorted(set(disabled) - set(actual_disabled)),
        "unexpected_disabled": sorted(set(actual_disabled) - set(disabled)),
    }
    trace["allowlist_diff"] = diff
    trace["readback_id"] = digest(matches[0]) if len(matches) == 1 else None
    exact = (
        echo_ok
        and readback_shape_valid
        and len(matches) == 1
        and not any(
            diff[key]
            for key in (
                "missing_kept",
                "unexpected_kept",
                "missing_disabled",
                "unexpected_disabled",
            )
        )
    )
    if st != 200:
        trace["least_privilege_state"] = "WRITE_FAILED"
        return finish_trace(
            trace,
            f"SetGrokBotUserMcpSettings transport failed with HTTP {st}",
            a.json,
            EXIT_UPSTREAM,
        )
    if not exact:
        trace["least_privilege_state"] = "DRIFT"
        return finish_trace(
            trace,
            "allowlist Set echo or independent readback drifted",
            a.json,
            EXIT_UPSTREAM,
        )

    trace.update(
        {
            "status": "OK",
            "kept": kept_full,
            "disabled": disabled,
            "recorded": True,
            "least_privilege_state": "EXACT_TOOL_SCOPE",
            "scope_enforcement": "ACCOUNT_WIDE_TOOL_NAME",
            "claim_ceiling": (
                "account-wide tool-name allowlist only; per-Bot/resource/account/host/transitive scopes UNENFORCED"
            ),
        }
    )
    return finish_trace(
        trace,
        f"{a.server} exact allowlist read back: keep {len(kept_full)}, disable {len(disabled)}",
        a.json,
        EXIT_OK,
    )


def main() -> int:
    ap = argparse.ArgumentParser(prog="gb-mcp", description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="verb", metavar="<verb>")
    p = sub.add_parser("add", help="probe a URL and append it to the config")
    p.add_argument("url")
    p.add_argument("--name", default=None)
    p = sub.add_parser("list", help="dump account config servers (name, id, url)")
    p = sub.add_parser("tools", help="prove a connected server's exact tool manifest")
    p.add_argument("server")
    p.add_argument(
        "--expect-tool",
        action="append",
        default=None,
        help="expected short tool name; repeat for servers absent from the frozen manifest",
    )
    p = sub.add_parser("call", help="proof-call one read-only tool via the account")
    p.add_argument("server")
    p.add_argument("tool")
    p.add_argument("args_json")
    p.add_argument("--agent", default=None, help="exact Bot id, legacy id, or name")
    p.add_argument("--expect-text", default=None, help="required semantic substring")
    p.add_argument("--expect-tool", action="append", default=None)
    p.add_argument(
        "--require-scope",
        action="append",
        default=None,
        metavar="KIND:VALUE",
        help="declare account/resource/host/transitive/Bot scope; unenforced scopes fail closed",
    )
    p = sub.add_parser(
        "allowlist", help="cut and independently read back a tool allowlist"
    )
    p.add_argument("server")
    p.add_argument(
        "keep", nargs="*", default=[], help="tools to keep (short or full names)"
    )
    p.add_argument("--keep-flag", dest="keep_flag", action="append", default=None)
    p.add_argument("--expect-tool", action="append", default=None)
    p.add_argument(
        "--require-scope",
        action="append",
        default=None,
        metavar="KIND:VALUE",
        help="declare a required non-tool scope; the account boundary cannot enforce it",
    )
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
