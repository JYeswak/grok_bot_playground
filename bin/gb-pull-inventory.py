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
import importlib.util
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import newest_audit as gblib_newest  # noqa: E402
from gblib import platform_refusal, platform_support  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402
# THE TRANSPORT MOVED TO `gbrpc.py` on 2026-09-12 and is RE-EXPORTED here, verbatim, because
# fifteen files name this producer by path to borrow it. Keeping the names bound here means none
# of them changed; new code should `from gbrpc import access_token, rpc` and skip the
# `spec_from_file_location` dance (and its python 3.9.6 `sys.modules` trap) entirely.
#
# Why it had to move: `gb dogfood audit` scored this file 172, the largest gap of 54, on
# `importers=9` — and the score could only RISE, because a library dependency living inside a
# PRODUCER is indistinguishable, to the detector, from nine files hand-rolling the same read.
# Four of them had in fact re-implemented `rpc`, and with it four copies of the read-only
# refusal that caught `gb fleet send` tonight.
from gbrpc import (  # noqa: E402
    HOST,
    KEYCHAIN,
    PLATFORM,
    READ_ONLY_PREFIXES,
    SERVICE,
    SUPPORT,
    access_token,
    decrypt,
    rpc,
    safestorage_key,
)

EXIT_ENVIRONMENT = 3

__all__ = [
    "HOST",
    "KEYCHAIN",
    "PLATFORM",
    "READ_ONLY_PREFIXES",
    "SERVICE",
    "SUPPORT",
    "access_token",
    "decrypt",
    "rpc",
    "safestorage_key",
]


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
    # NE-5 SUPERSEDED 2026-09-11T20:5xZ: the claim "CAPPED AT 10, newest first" is FALSIFIED.
    # A live call returned THIRTEEN agents, including the two Bots created minutes earlier
    # (Template 2465406, Routine Proof 2467454). Whatever stopped Claudey appearing after the
    # rebuild, it was not a hard cap of 10. The warning is kept but re-aimed: it now fires only
    # as a reminder that this response has no paging field, so a SHRINK is still suspicious.
    #
    # THE REAL BLIND SPOT, and it is ours: the `bots` list in the written artifact is sourced
    # from the LOCAL roster blob in sand-client-persistence, which is a per-machine CACHE. A Bot
    # created through the API is absent from it until that desktop syncs — measured: Routine
    # Proof was live in ListGrokBotAgents and in the app on another Mac while this machine's
    # artifact omitted it, so a reader concluded the Bot had been deleted. For a tool whose
    # product is DEPLOYING Bots, the roster must come from the API and the cache must only
    # enrich it. Until that is fixed, never conclude absence from the artifact alone.
    if len(got) < 10:
        print(
            f"NOTE ListGrokBotAgents returned {len(got)} — fewer than the 13 measured on "
            f"2026-09-11; this response has no paging field, so a shrink is unexplained",
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
                    # CORRECTED 2026-09-11T20:57Z by direct experiment. The previous comment
                    # claimed routines "can be created by the BOT ... and the record marks them
                    # provenance='untrusted'" — an INFERENCE from two routines on a freshly
                    # rebuilt Chief of Staff, written here as if measured. Both halves are now
                    # tested: a Bot WAS asked in chat to schedule itself, it DID create
                    # "Weekly receipt" (cron CRON_TZ=America/Denver 45 7 * * 1, isEnabled true),
                    # and that record carries provenance="user", NOT "untrusted". So chat IS a
                    # real create path for routines, and `untrusted` means something else we
                    # have not identified. g9 still treats enabled+untrusted as a finding.
                    "provenance": rec.get("provenance"),
                }
            )
        d = durable.get(b["id"])
        out.append(
            {
                "name": b["name"],
                "uuid": b["id"],
                # Which population this Bot came from: the server's durable index, the local
                # cache, or both. Carried through because a reader must be able to tell a
                # server-only Bot (new, or this desktop has not synced) from a cache-only one
                # (mid-delete, or the server list is short) instead of seeing a silent union.
                "roster_source": b.get("roster_source"),
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


def account_fingerprint(account: dict) -> str:
    stable = {
        "user_time_zone": account.get("user_time_zone"),
        "computer_ids": sorted(
            str(item.get("machine_id"))
            for item in (account.get("computers") or [])
            if item.get("machine_id")
        ),
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def inventory_validator():
    path = pathlib.Path(__file__).with_name("gb-record-inventory.py")
    spec = importlib.util.spec_from_file_location("gb_record_inventory", path)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load gb-record-inventory.py validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
        "read_route": "authenticated account RPC + policy/<device>.json",
        "account_fingerprint": account_fingerprint(account),
        "recapture_argv": [
            "bin/gb",
            "inventory",
            "pull",
            "--device",
            label,
            "--apply",
            "--json",
        ],
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


def receipt_for(
    root: pathlib.Path,
    doc: dict,
    validation_errors: list[str],
    payload: str,
    artifact: pathlib.Path | None,
) -> dict:
    bots = doc.get("bots") or []
    routines = [routine for bot in bots for routine in (bot.get("routines") or [])]
    skills = [skill for bot in bots for skill in (bot.get("skills") or [])]
    outcomes: dict[str, int] = {}
    for routine in routines:
        for outcome in routine.get("recent_runs") or []:
            key = str(outcome)
            outcomes[key] = outcomes.get(key, 0) + 1
    checked_empty = [
        field
        for field in ("auto_review_rules", "plugins", "mcp_servers", "bots")
        if doc.get(field) == []
    ]
    for index, bot in enumerate(bots):
        for field in ("skills", "routines"):
            if bot.get(field) == []:
                checked_empty.append(f"bots[{index}].{field}")
    policy = {
        "local_exec_policy": doc.get("local_exec_policy"),
        "local_exec_reason": doc.get("local_exec_reason"),
        "policy_recorded_at": doc.get("policy_recorded_at"),
        "auto_review_rules": doc.get("auto_review_rules"),
    }
    return {
        "device": doc.get("device"),
        "producer": "bin/gb-pull-inventory.py",
        "read_route": doc.get("read_route"),
        "captured_at": doc.get("recorded_at"),
        "freshness": "fresh" if not validation_errors else "invalid",
        "counts": {
            "bots": len(bots),
            "routines": len(routines),
            "skills": len(skills),
            "plugins": len(doc.get("plugins") or []),
            "mcp_servers": len(doc.get("mcp_servers") or []),
            "auto_review_rules": len(doc.get("auto_review_rules") or []),
        },
        "checked_empty": checked_empty,
        "null_required": [error for error in validation_errors if "null" in error],
        "routine_status": {
            "enabled": sum(routine.get("enabled") is True for routine in routines),
            "disabled": sum(routine.get("enabled") is False for routine in routines),
            "run_outcomes": outcomes,
        },
        "policy_digest": hashlib.sha256(
            json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "document_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "artifact": str(artifact.relative_to(root)) if artifact is not None else None,
        "artifact_sha256": hashlib.sha256(payload.encode()).hexdigest()
        if artifact is not None
        else None,
        "verdict": "GREEN" if not validation_errors else "ERROR",
        "validation_errors": validation_errors,
        "recapture_argv": doc.get("recapture_argv"),
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

def selftest() -> int:
    validator = inventory_validator()
    document = validator._fixture_document("brain", "Brain.local")
    payload = json.dumps(document, indent=1) + "\n"
    artifact = pathlib.Path("/repo/inventory/fixture.brain.json")
    good = receipt_for(pathlib.Path("/repo"), document, [], payload, artifact)
    bad = receipt_for(
        pathlib.Path("/repo"), document, ["documents[0].plugins: null"], payload, None
    )
    legs = [
        ("receipt-green", good["verdict"] == "GREEN" and good["freshness"] == "fresh"),
        ("receipt-counts", good["counts"] == {"bots": 1, "routines": 1, "skills": 0, "plugins": 1, "mcp_servers": 0, "auto_review_rules": 0}),
        ("receipt-artifact-hash", good["artifact"] == "inventory/fixture.brain.json" and good["artifact_sha256"] == good["document_sha256"]),
        ("receipt-policy-digest", len(good["policy_digest"]) == 64),
        ("receipt-error-no-artifact", bad["verdict"] == "ERROR" and bad["artifact"] is None and bad["null_required"]),
        ("receipt-recapture-argv", good["recapture_argv"] == validator.recapture_argv("brain")),
    ]
    for name, passed in legs:
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
    passed = sum(ok for _, ok in legs)
    print(f"SELFTEST {'PASS' if passed == len(legs) else 'FAIL'} - {passed}/{len(legs)}")
    return 0 if passed == len(legs) else 1



def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", action="append", help="label(s) from desktops.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.device:
        ap.error("--device is required")
    started = time.perf_counter()

    # THE PLATFORM GATE. The whole pull hangs off one macOS-only fact: the desktop client's
    # session is sealed with Electron safeStorage, and only macOS exposes it through a command
    # this tool can drive. On Windows it is DPAPI; on Linux it is the desktop secret service;
    # on a phone there is no desktop client at all. Attempting it anyway produced, before this
    # gate existed, a `security: command not found` traceback on Linux — a stack trace where
    # the honest answer is one sentence and a workaround. Exit 3 ENVIRONMENT, never 1: the
    # operator typed the right thing and this machine cannot do it.
    refusal = platform_refusal("gb-pull-inventory", PLATFORM, "credential_read")
    if refusal is not None or SUPPORT is None:
        print(
            refusal or platform_refusal("gb-pull-inventory", PLATFORM), file=sys.stderr
        )
        return EXIT_ENVIRONMENT

    reg = json.loads((root / "desktops.json").read_text())
    known = {d["label"]: d for d in reg["desktops"]}
    for label in args.device:
        if label not in known:
            raise SystemExit(f"device {label!r} is not in desktops.json")

    audit = newest_audit_or_die(root)
    token = access_token(SUPPORT)

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
    # THE ROSTER SPINE IS THE SERVER, corrected 2026-09-11. This line used to read
    # `audit.get("bots")` — the deployment audit's list, which is derived from the LOCAL
    # sand-client-persistence roster blob. That blob is a per-machine cache: measured tonight at
    # 13 Bots on the server against 10 in the artifact, and it reported a Bot that was live in
    # the API and visible on another Mac as though it had been deleted. Four separate wrong
    # conclusions in one session came from reading it, including "the deploy must have made a
    # phantom" about a Bot that was fine.
    #
    # So: the SERVER's durable index is the spine, and the cache only enriches. A Bot the server
    # knows is always present, carrying its routines. A Bot only the cache knows is still kept —
    # it is either mid-delete or the server list is short — but it is marked so a reader can
    # tell the two populations apart instead of silently unioning them.
    audit_bots = {b.get("id"): b for b in (audit.get("bots") or []) if b.get("id")}
    spine: list[dict] = []
    for uuid_key, rec in durable.items():
        row = dict(audit_bots.get(uuid_key) or {})
        row["id"] = uuid_key
        row["name"] = rec.get("name") or row.get("name")
        row["roster_source"] = "server" if uuid_key in audit_bots else "server-only"
        spine.append(row)
    for uuid_key, rec in audit_bots.items():
        if uuid_key not in durable:
            row = dict(rec)
            row["roster_source"] = "cache-only"
            spine.append(row)
    spine.sort(key=lambda b: str(b.get("name") or ""))
    bots = routine_rows(token, spine, durable)

    computers = {
        c["hello"]["label"]: c for c in (comps.get("computers") or []) if c.get("hello")
    }
    captured_now = dt.datetime.now(dt.timezone.utc)
    stamp = f"{captured_now:%Y-%m-%dT%H%M%S}"
    documents: list[dict] = []
    for label in args.device:
        documents.append(
            device_document(
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
        )

    validator = inventory_validator()
    expected = {label: known[label].get("hostname") for label in args.device}
    validation_errors = validator.validate_set(documents, expected, now=captured_now)
    payloads = [json.dumps(document, indent=1) + "\n" for document in documents]
    if validation_errors:
        receipts = [
            receipt_for(root, document, validation_errors, payload, None)
            for document, payload in zip(documents, payloads)
        ]
        error_envelope = {
            "schema": "gb-inventory-pull/2",
            "mode": "dry-run" if args.dry_run else "apply",
            "verdict": "ERROR",
            "producer": "bin/gb-pull-inventory.py",
            "expected_devices": args.device,
            "attempted": len(documents),
            "succeeded": 0,
            "failed": len(documents),
            "devices": documents,
            "artifacts": [],
            "receipts": receipts,
            "validation_errors": validation_errors,
            "timing_ms": {"total": round((time.perf_counter() - started) * 1000)},
        }
        if args.json:
            print(json.dumps(error_envelope, indent=1))
        else:
            for error in validation_errors:
                print(f"ERROR {error}", file=sys.stderr)
        return 1

    written: list[pathlib.Path] = []
    receipts = []
    for document, payload in zip(documents, payloads):
        label = document["device"]
        out = root / "inventory" / f"{stamp}.{label}.json"
        artifact = None
        if args.dry_run:
            if not args.json:
                print(payload[:2000])
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(out, payload)
            written.append(out)
            artifact = out
        receipts.append(receipt_for(root, document, [], payload, artifact))

    if args.json:
        print(
            json.dumps(
                {
                    "schema": "gb-inventory-pull/2",
                    "mode": "dry-run" if args.dry_run else "apply",
                    "verdict": "GREEN",
                    "producer": "bin/gb-pull-inventory.py",
                    "expected_devices": args.device,
                    "attempted": len(documents),
                    "succeeded": len(documents),
                    "failed": 0,
                    "devices": documents,
                    "artifacts": [str(path.relative_to(root)) for path in written],
                    "receipts": receipts,
                    "validation_errors": [],
                    "timing_ms": {
                        "total": round((time.perf_counter() - started) * 1000)
                    },
                },
                indent=1,
            )
        )
        return 0
    writable = sum(1 for b in bots if b["api_addressable"])
    total = sum(len(b["routines"]) for b in bots)
    dead = sum(1 for b in bots for r in b["routines"] if not r["enabled"])
    for o in written:
        print(o)
    summarize(bots, writable, total, dead, usage, account)
    return 0


if __name__ == "__main__":
    sys.exit(main())
