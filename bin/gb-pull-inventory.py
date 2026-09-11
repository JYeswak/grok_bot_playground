#!/usr/bin/env python3
"""gb-pull-inventory — read the server-side Grok Bot surface with the desktop client's own session.

Authorised by Joshua 2026-09-11. This closes the read half of the §ORACLE gap: routines
(automations), user-level MCP settings, Auto Review enablement, registered local computers, and
the template export policy come back as data instead of a human squinting at the UI.

How the session is obtained, so nobody has to reverse it again:
  1. `security find-generic-password -w -s "Grok Bot Safe Storage" -a "Grok Bot Key"` returns the
     Electron safeStorage password. **macOS shows a keychain prompt the first time; a human must
     click Allow.** Nothing here can or should bypass that.
  2. key = PBKDF2-HMAC-SHA1(password, b"saltysalt", 1003, 16); the stored values are Chromium
     `v10` blobs: AES-128-CBC, IV = 16 spaces, PKCS7.
  3. `sand-secrets.json` -> `cursor-accounts` (plain JSON) -> the active account's
     `cursor-access-token` (encrypted) is the Bearer token.
  4. Wire: Connect-RPC JSON at `https://api2.cursor.sh/aiserver.v1.GrokBotService/<Method>`.
     Measured: `authorization` + `content-type` + `connect-protocol-version: 1` are sufficient;
     `x-cursor-checksum` is not required. `api.origin.cursor.com` returns 404 for this service.

Discipline this script holds to:
  - READ ONLY. It calls no Set*/Update*/Delete*/Create* method. Adding one is a deliberate,
    separately-reviewed change, not a drive-by.
  - The token never reaches disk, a log, or an artifact.
  - A field the API cannot answer stays `null` — "not checked" — so `g8-desktop-inventory` keeps
    failing until a human records it. Filling a null with a guess is the failure this repo exists
    to prevent.

Usage:
  gb-pull-inventory.py --device studio [--device brain] [--dry-run]
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import newest_audit as gblib_newest  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402
import urllib.error
import urllib.request

HOST = "api2.cursor.sh"
SERVICE = "aiserver.v1.GrokBotService"
SUPPORT = pathlib.Path("~/Library/Application Support/Grok Bot").expanduser()
KEYCHAIN = ("Grok Bot Safe Storage", "Grok Bot Key")
READ_ONLY_PREFIXES = ("List", "Get")


def safestorage_key() -> bytes:
    r = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-w",
            "-s",
            KEYCHAIN[0],
            "-a",
            KEYCHAIN[1],
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(
            "could not read the safeStorage password from the keychain — a human must "
            "click Allow on the macOS prompt, then re-run"
        )
    return hashlib.pbkdf2_hmac(
        "sha1", r.stdout.strip().encode(), b"saltysalt", 1003, 16
    )


def decrypt(value: str, key: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    raw = base64.b64decode(value)
    if raw[:3] != b"v10":
        raise ValueError("not a safeStorage v10 blob")
    d = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    out = d.update(raw[3:]) + d.finalize()
    return out[: -out[-1]].decode("utf-8", "replace")


def access_token() -> str:
    key = safestorage_key()
    secrets = json.loads((SUPPORT / "sand-secrets.json").read_text())
    accounts = json.loads(secrets["cursor-accounts"])
    return decrypt(accounts["accounts"][accounts["active"]]["cursor-access-token"], key)


def rpc(
    token: str,
    method: str,
    body: dict | None = None,
    timeout: float = 45.0,
    service: str = SERVICE,
) -> tuple[int, dict | str]:
    if not method.startswith(READ_ONLY_PREFIXES):
        raise SystemExit(
            f"refusing to call {method}: this tool is read-only by contract"
        )
    req = urllib.request.Request(
        f"https://{HOST}/{service}/{method}",
        data=json.dumps(body or {}).encode(),
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


def newest_audit_or_die(root: pathlib.Path) -> dict:
    _, audit = gblib_newest(root)
    if audit is None:
        raise SystemExit("no deployment audit — run bin/gb-deployment-audit.py first")
    return audit


def account_surface(token: str) -> dict:
    """Everything the server will answer about the ACCOUNT, as opposed to one desktop.

    Measured 2026-09-11, after NE-8 forced a probe of all six services instead of GrokBotService
    alone. Each of these had been recorded as "no read RPC exists" and pushed onto Joshua:

      auto_review_rules   GrokBotService/GetGrokBotUserMcpSettings -> settings.autoReviewInstructions
                          .blockInstructions. The five rules come back verbatim. They are
                          ACCOUNT-level: Joshua typed them on Brain only and the server serves them
                          for the account, so Studio inherits them (NE-9).
      plugins             DashboardService/ListUserPluginInstalls -> the installed plugin list with
                          publisher, status and enabled flag. Per-AGENT plugin reads still 404.
      computers           GrokBotService/ListGrokBotUserComputers -> every registered machine with
                          its label, localRoot and capability flags.
      memory              GrokBotService/ListGrokBotMemoryShards -> one shard per Bot. An empty
                          `folder` means the Bot has learned nothing durable yet.
      form vault          GrokBotService/ListGrokBotUserFormVaultKeys -> stored form/login entries.
    """
    st_s, settings = rpc(token, "GetGrokBotUserMcpSettings", {})
    s = (settings or {}).get("settings") or {} if st_s == 200 else {}
    ari = s.get("autoReviewInstructions") or {}
    st_p, plugins = rpc(
        token, "ListUserPluginInstalls", {}, service="aiserver.v1.DashboardService"
    )
    st_c, comps = rpc(token, "ListGrokBotUserComputers", {})
    st_m, mem = rpc(token, "ListGrokBotMemoryShards", {})
    st_v, vault = rpc(token, "ListGrokBotUserFormVaultKeys", {})
    shards = (mem or {}).get("shards") or [] if st_m == 200 else None
    return {
        "auto_review_enabled": ari.get("isEnabled") if st_s == 200 else None,
        # The rules themselves, not just the switch. null still means "not checked".
        "auto_review_rules": ari.get("blockInstructions") if st_s == 200 else None,
        "auto_review_scope": "account" if st_s == 200 else None,
        "user_time_zone": s.get("userTimeZone"),
        "mcp_servers": [
            {
                "server_id": x.get("serverId"),
                "has_custom_instructions": bool(x.get("customInstructions")),
            }
            for x in (s.get("servers") or [])
        ]
        if st_s == 200
        else None,
        "plugins": [
            {
                "name": (i.get("plugin") or {}).get("name"),
                "enabled": i.get("isEnabled"),
                "status": (i.get("plugin") or {}).get("status"),
                "publisher": ((i.get("plugin") or {}).get("publisher") or {}).get(
                    "name"
                ),
                "verified_publisher": (
                    (i.get("plugin") or {}).get("publisher") or {}
                ).get("isVerified"),
            }
            for i in ((plugins or {}).get("installs") or [])
        ]
        if st_p == 200
        else None,
        "computers": [
            {
                "label": (c.get("hello") or {}).get("label"),
                "machine_id": c.get("machineId"),
                "local_root": (c.get("hello") or {}).get("localRoot"),
                "supervised": (c.get("hello") or {}).get("supervised"),
                # A CAPABILITY flag: this client can hold standing grants. It is NOT the
                # Execution-on-Local-Computer setting, which has no server record.
                "local_standing_grants_capable": (
                    (c.get("hello") or {}).get("capabilities") or {}
                ).get("localStandingGrants"),
                "last_seen_at_ms": c.get("lastSeenAtMs"),
            }
            for c in ((comps or {}).get("computers") or [])
        ]
        if st_c == 200
        else None,
        "memory_shards": (
            None
            if shards is None
            else [
                {
                    "agent_id": x.get("agentId"),
                    "harness": x.get("harness"),
                    "has_content": bool(x.get("folder")),
                }
                for x in shards
            ]
        ),
        "form_vault_keys": ((vault or {}).get("keys") or []) if st_v == 200 else None,
    }


def usage_status(token: str) -> dict | None:
    """The account's spend position, from the dashboard service.

    Measured 2026-09-11: `GetSandUsageStatus` 404s on GrokBotService — which is how three ticks
    concluded "no read RPC" and filed the weekly-allowance item as a thing only Joshua could
    answer in Settings. It lives on `DashboardService` and returns 200: period bounds, percent
    consumed, and whether ON-DEMAND spending past the included allowance is enabled. The reading
    half was never a human task; only the ceiling is (that is a write, on a paid surface).
    """
    st, r = rpc(token, "GetSandUsageStatus", {}, service="aiserver.v1.DashboardService")
    if st != 200 or not isinstance(r, dict):
        return None
    od = r.get("onDemandSettings") or {}
    return {
        "period_start": r.get("currentPeriodStart"),
        "next_reset": r.get("nextResetTimestampUtc"),
        "usage_percent": r.get("usagePercent"),
        "has_available_usage": r.get("hasAvailableUsage"),
        "plan_label": r.get("grokPlanLabel"),
        "on_demand_enabled": od.get("enabled"),
        "on_demand_eligible": od.get("eligible"),
        "dashboard_url": od.get("dashboardUrl"),
    }


def durable_index(token: str) -> dict[str, dict]:
    """legacy uuid -> the server's durable agent record, for the Bots it will admit to knowing.

    Measured 2026-09-11: `ListGrokBotAgents` returned 1 of 15. The one it returned was created
    2026-08-30 and carries `harness: "box"` and a numeric `id`; the other 14 predate durable
    identity and every agent-record RPC 404s on their uuid. A Bot is programmatically writable
    exactly when it appears here — so the inventory records that per Bot, and the weekly tick
    tells us the moment one becomes controllable.
    """
    st, resp = rpc(token, "ListGrokBotAgents", {})
    if st != 200 or not isinstance(resp, dict):
        return {}
    got = resp.get("agents", [])
    # Measured 2026-09-11 (NE-5): this response is CAPPED AT 10, newest first, and the request has
    # no paging field. After the rebuild it stopped returning Claudey, which still exists
    # (ListGrokBotAgentSessions answers 200 for it). A full set therefore cannot be assumed —
    # api_addressable=False on a Bot beyond the cap means "not shown", not "not writable".
    if len(got) >= 10:
        print(
            f"WARNING ListGrokBotAgents returned {len(got)} — at or above the measured cap of 10; "
            f"addressability for Bots outside the newest 10 is UNKNOWN, not false",
            file=sys.stderr,
        )
    return {a["legacyAgentId"]: a for a in got if a.get("legacyAgentId")}


def agent_skills(token: str, agent_id: str) -> list[dict] | None:
    """The skills attached to one Bot, or None for "not checked".

    Measured 2026-09-11: `ListGrokBotAgentSkills {agent_id}` returns 200 with the full record
    (id, name, description, body) for every Bot — and has ZERO references in the shipped client,
    which is why three earlier ticks recorded "no read RPC exists" and asked Joshua to hand-audit
    (NE-8). Bodies are measured, not stored: a skill body is instructions the Bot follows, it can
    be long, and the inventory is a surface census rather than a content mirror.

    None is "not checked" and never "none" — the distinction g8 is built on.
    """
    st, resp = rpc(token, "ListGrokBotAgentSkills", {"agent_id": agent_id})
    if st != 200 or not isinstance(resp, dict):
        return None
    return [
        {
            "name": s.get("name"),
            "description_chars": len(s.get("description") or ""),
            "body_chars": len(s.get("body") or ""),
        }
        for s in (resp.get("skills") or [])
    ]


def routine_rows(token: str, bots: list[dict], durable: dict[str, dict]) -> list[dict]:
    out = []
    for b in bots:
        st, resp = rpc(token, "ListGrokBotAgentAutomations", {"agent_id": b["id"]})
        autos = (
            resp.get("automations") if st == 200 and isinstance(resp, dict) else None
        ) or []
        routines = []
        for a in autos:
            try:
                rec = json.loads(a["recordJson"])
            except Exception:
                continue
            runs = [str(r.get("status", "unknown")) for r in (rec.get("runs") or [])]
            trig = (rec.get("trigger") or {}).get("type")
            routines.append(
                {
                    "name": rec.get("name"),
                    "schedule": rec.get("schedule") or f"trigger:{trig}",
                    "trigger": trig,
                    "enabled": bool(rec.get("isEnabled")),
                    "last_run_at_ms": rec.get("lastRunAt"),
                    "next_run_at_ms": rec.get("nextRunAt"),
                    "recent_runs": runs,
                    # Measured 2026-09-11: routines can be created by the BOT, not the user, and the
                    # record marks them provenance="untrusted". Two appeared on a freshly built Chief
                    # of Staff with no run history and a next run already scheduled. g9 treats an
                    # enabled untrusted routine as a finding until a human has looked at it.
                    "provenance": rec.get("provenance"),
                }
            )
        d = durable.get(b["id"])
        out.append(
            {
                "name": b["name"],
                "uuid": b["id"],
                # The write contract: numeric id present => UpdateGrokBotAgent / SetGrokBotAgentPlugins
                # / SetGrokBotAgentVisibility / DeleteGrokBotAgent are callable for this Bot.
                "api_addressable": d is not None,
                "numeric_id": (d or {}).get("id"),
                "harness": (d or {}).get("harness"),
                "skills": agent_skills(token, b["id"]),
                "routines": routines,
            }
        )
    return out


def device_document(
    root: pathlib.Path,
    label: str,
    host: str | None,
    *,
    audit: dict,
    settings: dict,
    computers: dict,
    caps,
    tmpl,
    usage,
    server_bots: list,
    bots: list,
    account: dict,
) -> dict:
    """One desktop's inventory record: the authenticated reads, plus its own dated hand record."""
    comp = computers.get(host)
    pol = None
    pfile = root / "policy" / f"{label}.json"
    if pfile.is_file():
        try:
            pol = json.loads(pfile.read_text())
        except Exception as e:
            print(
                f"WARNING policy/{label}.json unreadable ({e}) — treating as not recorded",
                file=sys.stderr,
            )
    return {
        "schema": "gb-inventory/1",
        "device": label,
        "hostname": host,
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "app_version": (audit.get("app") or {}).get("version"),
        "source": "gb-pull-inventory (authenticated read) + human for the desktop-local fields",
        # Desktop-local: these two have no server record at all, so they come from the dated hand
        # record in policy/<label>.json. Absent file => null => g8 says "not checked". The record's
        # OWN date travels with it: a fresh pull must never make a stale policy look current.
        # The ONE field with no server record: Execution on Local Computer is set per desktop and
        # nothing serves it back (measured across all six services, 2026-09-11).
        "local_exec_policy": (pol.get("local_exec_policy") if pol else None),
        "local_exec_reason": (pol.get("local_exec_reason") if pol else None),
        "policy_recorded_at": (pol.get("recorded_at") if pol else None),
        # Auto-review rules come from the SERVER and are account-level (NE-9). The hand record is
        # kept only as a cross-check; the API is authoritative.
        "auto_review_rules": account.get("auto_review_rules"),
        "auto_review_rules_scope": account.get("auto_review_scope"),
        "auto_review_rules_hand_recorded": (
            pol.get("auto_review_rules") if pol else None
        ),
        "auto_review_enabled": account.get("auto_review_enabled"),
        "user_time_zone": account.get("user_time_zone"),
        "account_surface": account,
        "registered_computer": {
            "seen_by_server": comp is not None,
            "last_seen_at_ms": comp.get("lastSeenAtMs") if comp else None,
            "supervised": (comp or {}).get("hello", {}).get("supervised"),
            "capabilities": (comp or {}).get("hello", {}).get("capabilities"),
        },
        "runtime_capabilities": caps.get("capabilities")
        if isinstance(caps, dict)
        else None,
        "template_export_policy": tmpl.get("exportPolicy")
        if isinstance(tmpl, dict)
        else None,
        # Account-level, not device-level, but it rides here because this is the authenticated
        # reader and the inventory is what g8/g12 judge. `on_demand_enabled` is the one field that
        # can cost money without asking.
        "usage": usage,
        # Per-agent plugin reads 404 (resource, not route) for these Bots; only the user-level MCP
        # server list is answerable. Both recorded honestly.
        "plugins": account.get("plugins"),
        "mcp_servers": account.get("mcp_servers"),
        "server_bots": server_bots,
        "server_roster_capped": len(server_bots) >= 10,
        "bots": bots,
    }


def summarize(
    bots: list, writable: int, total: int, dead: int, usage: dict | None, account: dict
) -> None:
    """What this pull learned, in three lines, so the tick is readable without opening a file."""
    skilled = sum(1 for b in bots if b["skills"] is not None)
    shards = account.get("memory_shards") or []
    print(
        f"{len(bots)} Bots ({writable} API-writable) · {total} routines · {dead} disabled · "
        f"{skilled}/{len(bots)} skill lists read · "
        f"{len(account.get('mcp_servers') or [])} MCP server(s) · "
        f"auto-review enabled={account.get('auto_review_enabled')}"
    )
    if usage:
        print(
            f"usage {usage['usage_percent']:.1f}% of the included allowance · "
            f"on-demand {'ENABLED' if usage['on_demand_enabled'] else 'off'} · "
            f"resets {str(usage['next_reset'])[:10]}"
        )
    print(
        f"account: {len(account.get('auto_review_rules') or [])} auto-review rule(s) "
        f"(scope={account.get('auto_review_scope')}) · {len(account.get('plugins') or [])} plugin(s) · "
        f"{len(account.get('computers') or [])} registered computer(s) · "
        f"{sum(1 for m in shards if m['has_content'])}/{len(shards)} memory shards with content · "
        f"{len(account.get('form_vault_keys') or [])} form-vault key(s)"
    )
    print(
        "the ONE field with no server record anywhere: local_exec_policy (Execution on Local "
        "Computer, per desktop). Per-AGENT plugin reads still 404; the user-level list above is "
        "the answerable one."
    )


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--device", action="append", required=True, help="label(s) from desktops.json"
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    reg = json.loads((root / "desktops.json").read_text())
    known = {d["label"]: d for d in reg["desktops"]}
    for label in args.device:
        if label not in known:
            raise SystemExit(f"device {label!r} is not in desktops.json")

    audit = newest_audit_or_die(root)
    token = access_token()

    st, mcp = rpc(token, "GetGrokBotUserMcpSettings")
    if st != 200:
        raise SystemExit(f"GetGrokBotUserMcpSettings failed: {st} {mcp}")
    settings = mcp.get("settings", {})
    _, comps = rpc(token, "ListGrokBotUserComputers")
    _, caps = rpc(token, "GetGrokBotRuntimeCapabilities")
    _, tmpl = rpc(token, "ListGrokBotTemplates")
    durable = durable_index(token)
    usage = usage_status(token)
    account = account_surface(token)
    # The server is authoritative for durable-identity Bots. Measured 2026-09-11: after deleting
    # the 14 pre-durable Bots the desktop's local roster blob showed a single stray "New Bot"
    # while the server held all 10 real ones. g5 grades THIS list when it is present.
    server_bots = [
        {
            "name": a.get("name"),
            "numeric_id": a.get("id"),
            "harness": a.get("harness"),
            "title": a.get("title"),
            "description_chars": len(a.get("description") or ""),
            "updated_at_ms": a.get("updatedAtMs"),
        }
        for a in sorted(durable.values(), key=lambda a: a.get("name") or "")
    ]
    bots = routine_rows(token, audit.get("bots") or [], durable)

    computers = {
        c["hello"]["label"]: c for c in (comps.get("computers") or []) if c.get("hello")
    }
    stamp = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}"
    written = []
    for label in args.device:
        doc = device_document(
            root,
            label,
            known[label].get("hostname"),
            audit=audit,
            settings=settings,
            computers=computers,
            caps=caps,
            tmpl=tmpl,
            usage=usage,
            server_bots=server_bots,
            bots=bots,
            account=account,
        )
        out = root / "inventory" / f"{stamp}.{label}.json"
        if args.dry_run:
            print(json.dumps(doc, indent=1)[:2000])
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(out, json.dumps(doc, indent=1) + "\n")
            written.append(out)

    writable = sum(1 for b in bots if b["api_addressable"])
    total = sum(len(b["routines"]) for b in bots)
    dead = sum(1 for b in bots for r in b["routines"] if not r["enabled"])
    for o in written:
        print(o)
    summarize(bots, writable, total, dead, usage, account)
    return 0


if __name__ == "__main__":
    sys.exit(main())
