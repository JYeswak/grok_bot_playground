#!/usr/bin/env python3
"""gb-inventory — the ONE read of the Grok Bot account surface, addressable by name.

WHY THIS FILE EXISTS, MEASURED RATHER THAN ASSERTED.

`bin/gb-pull-inventory.py` is this repo's authenticated reader. It is also the highest-scoring
row in `gb dogfood audit` (140 of 54 gaps) on one signal: `importers=7`. SEVEN files in `bin/`
dynamically `importlib` it and then hand-roll the read they wanted, because nothing in `gb`
could ask for that read by name. Measured 2026-09-11 over the AST (prose in docstrings
excluded, the same rule `gb-dogfood.py:_docstring_ids` uses):

    importer                  reaches for                                  RPCs it then calls
    gb-deployment-audit.py    access_token, rpc, durable_index             ListGrokBotAgentAutomations,
                                                                           ListGrokBotAgentSkills,
                                                                           GetManagedSkills,
                                                                           ListUserPluginInstalls,
                                                                           GetGrokBotUserMcpSettings,
                                                                           GetEffectiveMcpConfigForUser,
                                                                           ListSandMcpTools,
                                                                           GetSandUsageStatus
    gb-fleet.py               SUPPORT, access_token, rpc                   ListGrokBotAgents,
                                                                           ListGrokBotAgentAutomations
    gb-handover.py            HOST, SUPPORT, access_token, rpc             ListGrokBotAgents
    gb-market-snapshot.py     SUPPORT, access_token, rpc                   ListMarketplacePlugins,
                                                                           ListPublicGrokBotMarketplace
                                                                           Listings, ListGrokBotTemplates,
                                                                           ListUserPluginInstalls
    gb-mirror.py              access_token, account_surface, usage_status, (via those readers)
                              durable_index, routine_rows
    gb-rebuild-fleet.py       HOST, SERVICE, SUPPORT, access_token, rpc    ListGrokBotAgents
    gb-spend-ledger.py        access_token, rpc                            ListGrokBotAgentAutomations,
                                                                           GetSandUsageStatus,
                                                                           GetCurrentPeriodUsage,
                                                                           GetAggregatedUsageEvents,
                                                                           GetFilteredUsageEvents

Seven of seven reach for the TRANSPORT (`access_token` + `rpc`). That is a library dependency,
not a missing verb, and it stays. What is a missing verb is everything to the right of it: the
ACCOUNT reads, which four separate files project by hand and which nobody can ask for.

WHAT THIS FILE OWNS, and it is exactly the account surface:

    account     the whole account: auto-review rules, MCP servers, plugins, computers, memory
                shards, form-vault keys, timezone       (gb-mirror and gb-deployment-audit both
                                                         project this by hand today)
    usage       the spend position: GetSandUsageStatus + GetCurrentPeriodUsage
                                                        (gb-deployment-audit, gb-spend-ledger)
    computers   the registered local machines
    plugins     the user-level plugin installs          (gb-deployment-audit, gb-market-snapshot)
    mcp         account MCP settings + effective config + the tool census
                                                        (gb-deployment-audit)
    skills      the skills attached to each durable Bot + the managed catalog
                                                        (gb-deployment-audit)

WHAT THIS FILE DELIBERATELY DOES NOT OWN, because `gb fleet` already does:

    ListGrokBotAgents              -> `gb fleet roster`.  Three importers call it; all three want
                                      the roster, and `gb-fleet.py` is already the name for it.
                                      Shipping it here would be the same read under two verbs,
                                      which is the defect this file exists to remove.
    ListGrokBotAgentAutomations    -> `gb fleet routines`. Same argument, three importers.
    SendGrokBotUserMessage         -> `gb fleet send`. A WRITE; see the contract below.
    Marketplace/template catalogs  -> `gb-market-snapshot.py`. That is the vendor CATALOG, not
                                      this account's inventory.
    GetFilteredUsageEvents paging  -> `gb-spend-ledger.py`. Per-event attribution is ledger
                                      arithmetic over a paged feed, not an inventory read.

`skills` is the one borderline call, and it goes here rather than in fleet on purpose: the read
is `ListGrokBotAgentSkills`, but the QUESTION is "what is installed on this account", and the
catalog half (`GetManagedSkills`) is account-level with no Bot in it at all.

READ-ONLY BY CONSTRUCTION, INHERITED RATHER THAN RESTATED. Every call here goes through
`gb-pull-inventory.py`'s `rpc`, which raises on any method that is not a `List`/`Get`
(gb-pull-inventory.py:124). A selftest leg below hands that function a write method and requires
it to refuse, so the contract is proven inherited instead of claimed. Nothing here creates,
updates, deletes, or messages a Bot, and nothing here writes a file.

ONE DISTINCTION THIS FILE REFUSES TO LOSE. `None` is "not checked" and `[]` is "measured, and
the answer is zero". `gb-deployment-audit.py:462` collapses them (`... if st == 200 else None`
followed by `or []`), so a Bot whose skill read failed is indistinguishable from a Bot with no
skills. `count_or_unknown` is the single place that decision is made here, and two selftest
legs fire on a mutant that coerces.

FEWER PREREQUISITES THAN THE PULL IT REUSES. `gb-pull-inventory.py` needs a deployment audit
(`newest_audit_or_die`) because it writes a per-DEVICE document that joins the account read to
one desktop's record. These reads are account-level, so this file needs only the keychain and a
signed-in desktop — no audit, no artifact root, no prior tick.

Usage:
  gb-inventory.py account [--json]
  gb-inventory.py usage | computers | plugins | mcp | skills [--json]
  gb-inventory.py --selftest

EXIT CODES
  0   measured (including "measured, and the answer is zero" — that is an answer)
  1   the selftest failed, or a read this verb needs did not answer 200
  2   bad invocation
  3   this machine cannot answer: unsupported platform, or no readable desktop session
  130 cancelled (SIGINT); 143 (SIGTERM) — via `gbtypes.main`
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import arg, build_parser  # noqa: E402
from gblib import AVAILABLE, NOT_IMPLEMENTED, SUPPORTED, UNSUPPORTED  # noqa: E402
from gblib import Capability, Platform  # noqa: E402
from gblib import platform_refusal, platform_support  # noqa: E402
from gbtypes import main as gbmain  # noqa: E402

BIN = pathlib.Path(__file__).resolve().parent
SCHEMA = "gb-inventory/1"
TOOL = "gb-inventory"
SUMMARY = (
    "read the Grok Bot account surface: account, usage, computers, plugins, mcp, skills"
)

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_ENVIRONMENT = 0, 1, 2, 3

DASH = "aiserver.v1.DashboardService"
GBSVC = "aiserver.v1.GrokBotService"

# Every method this file can reach, with the service that answers it. Declared as data rather
# than scattered through the readers for one reason: a selftest leg asserts that all of them are
# `List`/`Get`, which turns the read-only contract into something checkable without a network.
METHODS: Tuple[Tuple[str, str], ...] = (
    ("GetGrokBotUserMcpSettings", GBSVC),
    ("ListUserPluginInstalls", DASH),
    ("ListGrokBotUserComputers", GBSVC),
    ("ListGrokBotMemoryShards", GBSVC),
    ("ListGrokBotUserFormVaultKeys", GBSVC),
    ("GetSandUsageStatus", DASH),
    ("GetCurrentPeriodUsage", DASH),
    ("GetEffectiveMcpConfigForUser", DASH),
    ("ListSandMcpTools", DASH),
    ("ListGrokBotAgents", GBSVC),
    ("ListGrokBotAgentSkills", GBSVC),
    ("GetManagedSkills", DASH),
)

# The keys `account_surface()` promises. Named here so a slice verb fails LOUDLY when that
# function's shape drifts, instead of answering null for a key that no longer exists.
ACCOUNT_KEYS: Tuple[str, ...] = (
    "auto_review_enabled",
    "auto_review_rules",
    "auto_review_scope",
    "user_time_zone",
    "mcp_servers",
    "plugins",
    "computers",
    "memory_shards",
    "form_vault_keys",
)

VERBS: Tuple[str, ...] = (
    "account",
    "usage",
    "computers",
    "plugins",
    "mcp",
    "skills",
)


class SchemaDrift(Exception):
    """`account_surface()` stopped answering a key a slice verb needs.

    An exception rather than a `.get()` default, because the default is the bug: a slice verb
    that answers `null` for a key the reader no longer returns reports "not checked" for a fact
    that is actually unreadable, and `null` is the one value this tool treats as load-bearing.
    """


# ---------------------------------------------------------------------------------------------
# The transport, borrowed — never copied
# ---------------------------------------------------------------------------------------------
def puller() -> Any:
    """`gb-pull-inventory.py` as a module: the measured reader for this account (NE-8/NE-9).

    `sys.modules[name] = mod` BEFORE `exec_module` because this interpreter is 3.9.6, where a
    `@dataclass` defined in a module that is not yet registered raises `AttributeError` on
    `cls.__module__`. That module declares none today (`gb-sources.py:72` measures it), so this
    is insurance rather than a fix — and insurance that costs one line, against a failure mode
    `gb-fleet.py:64` already documents having hit.
    """
    path = BIN / "gb-pull-inventory.py"
    spec = importlib.util.spec_from_file_location("gb_pull_inventory", path)
    if spec is None or spec.loader is None:
        raise SchemaDrift(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gb_pull_inventory"] = mod
    spec.loader.exec_module(mod)
    return mod


def session(plat: Platform) -> Tuple[Optional[Any], Optional[str], Optional[str]]:
    """(module, token, refusal). Exactly one of `token` and `refusal` is None.

    The platform gate runs BEFORE the keychain is touched, so an operator on Windows gets the
    sentence `gblib` wrote for that case rather than a traceback out of a macOS-only decrypt.
    """
    refusal = platform_refusal(TOOL, plat, "credential_read")
    if refusal is not None:
        return None, None, refusal
    if plat.support_dir is None:
        return None, None, platform_refusal(TOOL, plat)
    mod = puller()
    try:
        return mod, mod.access_token(plat.support_dir), None
    except Exception as exc:  # keychain denied, not signed in, no session file
        return (
            None,
            None,
            f"{TOOL}: the desktop session could not be read "
            f"({type(exc).__name__}: {exc}).\n"
            f"    That is 'this machine cannot see the account', which is NOT 'the account is "
            f"empty' — reporting zero here would be a fact this tool invented.\n"
            f"    fix: open the Grok Bot desktop app, sign in, and allow the keychain prompt",
        )


# ---------------------------------------------------------------------------------------------
# Pure views — every one of these is what the selftest actually exercises
# ---------------------------------------------------------------------------------------------
def count_or_unknown(value: Optional[Sequence[Any]]) -> Optional[int]:
    """`None` -> None ("not checked"); a sequence -> its length (0 is a measurement).

    The single place this tool decides that question. Inlining `len(x or [])` anywhere else is
    how `gb-deployment-audit.py:462` came to report a failed skill read as a Bot with no skills.
    """
    return None if value is None else len(value)


def project(account: Dict[str, Any], keys: Sequence[str]) -> Dict[str, Any]:
    """The named keys of an account document, or `SchemaDrift` naming the ones that vanished."""
    missing = [k for k in keys if k not in account]
    if missing:
        raise SchemaDrift(
            f"account_surface() no longer returns {', '.join(sorted(missing))} — "
            f"refusing to answer null for a key that does not exist"
        )
    return {k: account[k] for k in keys}


def account_view(account: Dict[str, Any]) -> Dict[str, Any]:
    """The whole account surface plus the counts, so the human render needs no arithmetic."""
    slice_ = project(account, ACCOUNT_KEYS)
    return {
        **slice_,
        "counts": {
            "auto_review_rules": count_or_unknown(slice_["auto_review_rules"]),
            "mcp_servers": count_or_unknown(slice_["mcp_servers"]),
            "plugins": count_or_unknown(slice_["plugins"]),
            "computers": count_or_unknown(slice_["computers"]),
            "memory_shards": count_or_unknown(slice_["memory_shards"]),
            "form_vault_keys": count_or_unknown(slice_["form_vault_keys"]),
        },
    }


def computers_view(account: Dict[str, Any]) -> Dict[str, Any]:
    rows = project(account, ("computers",))["computers"]
    return {"computers": rows, "count": count_or_unknown(rows)}


def plugins_view(account: Dict[str, Any]) -> Dict[str, Any]:
    """User-level installs, with the measured note that the per-Bot read does not exist."""
    rows = project(account, ("plugins",))["plugins"]
    enabled = None if rows is None else sum(1 for r in rows if r.get("enabled") is True)
    return {
        "plugins": rows,
        "count": count_or_unknown(rows),
        "enabled": enabled,
        "scope_note": "ListUserPluginInstalls is USER-level; the per-Agent plugin read 404s "
        "(measured 2026-09-11), so which Bot uses which plugin is not readable here",
    }


def usage_view(
    usage: Optional[Dict[str, Any]], cycle_status: int, cycle: Any
) -> Dict[str, Any]:
    """The spend position, or an honest "unknown" — never a zero this tool invented.

    `usage` is `gb-pull-inventory.usage_status()`'s return: a dict on 200, None otherwise. The
    None arm is the whole point: `gb-monitor.py:321` already has to say "the spend position is
    unknown, which is not the same as zero", and it says it because an earlier reader answered 0.
    """
    if usage is None:
        return {
            "measured": False,
            "why": "DashboardService/GetSandUsageStatus did not answer 200 — the spend "
            "position is UNKNOWN, which is not the same as zero",
            "usage_percent": None,
            "plan_label": None,
            "on_demand_enabled": None,
            "billing_cycle": None,
        }
    return {
        "measured": True,
        "usage_percent": usage.get("usage_percent"),
        "has_available_usage": usage.get("has_available_usage"),
        "plan_label": usage.get("plan_label"),
        "period_start": usage.get("period_start"),
        "next_reset": usage.get("next_reset"),
        "on_demand_enabled": usage.get("on_demand_enabled"),
        "on_demand_eligible": usage.get("on_demand_eligible"),
        "dashboard_url": usage.get("dashboard_url"),
        # The CEILING is a write on a paid surface and stays a human decision; only the reading
        # half was ever mechanical (gb-pull-inventory.py:246-254).
        "billing_cycle": (
            cycle.get("planUsage")
            if cycle_status == 200 and isinstance(cycle, dict)
            else None
        ),
    }


def effective_server_names(status: int, payload: Any) -> Optional[List[str]]:
    """The MCP servers the server says are in effect. None = not checked; [] = parsed to none.

    `configJson` is JSON inside JSON — the vendor's shape. An unparseable body yields `[]` and
    NOT None on purpose: the read succeeded, so "not checked" would be false; what is empty is
    our understanding of the body, and that is a different fact from a failed call.
    """
    if status != 200 or not isinstance(payload, dict):
        return None
    try:
        parsed = json.loads(payload.get("configJson") or "{}")
        return sorted(parsed.get("mcpServers") or {})
    except Exception:
        return []


def mcp_view(
    account: Dict[str, Any],
    eff_status: int,
    eff: Any,
    tools_status: int,
    tools: Any,
) -> Dict[str, Any]:
    """Account MCP settings, the effective config, and the tool census, in one answer."""
    slice_ = project(
        account, ("mcp_servers", "auto_review_enabled", "auto_review_rules")
    )
    servers = slice_["mcp_servers"]
    census = (
        [
            {
                "server_id": x.get("serverIdentifier"),
                "status": x.get("status"),
                "tool_count": len(x.get("tools") or []),
                "tool_names": sorted(
                    str(t.get("name")) for t in (x.get("tools") or []) if t.get("name")
                ),
            }
            for x in ((tools or {}).get("servers") or [])
        ]
        if tools_status == 200 and isinstance(tools, dict)
        else None
    )
    return {
        "account_servers": servers,
        "account_server_count": count_or_unknown(servers),
        "effective_server_names": effective_server_names(eff_status, eff),
        "tool_census": census,
        "tool_total": None if census is None else sum(c["tool_count"] for c in census),
        "auto_review_enabled": slice_["auto_review_enabled"],
        "auto_review_rule_count": count_or_unknown(slice_["auto_review_rules"]),
        "scope_note": "auto-review rules and the effective MCP config are ACCOUNT-level: typed "
        "on one desktop, served for the account (NE-9). There is no per-Bot scope in either.",
    }


def skills_view(
    rows: Sequence[Tuple[str, Optional[str], Optional[List[Dict[str, Any]]]]],
    managed_status: int,
    managed: Any,
) -> Dict[str, Any]:
    """Per-Bot skills plus the managed catalog, with not-checked kept apart from zero.

    `rows` is (uuid, name, agent_skills() result). That third element is None when the read did
    not answer 200 and `[]` when the Bot genuinely has no skills, and this function reports the
    two as different numbers rather than adding them together.
    """
    bots: List[Dict[str, Any]] = [
        {
            "uuid": uuid,
            "name": name,
            "skills": skills,
            "skill_count": count_or_unknown(skills),
        }
        for uuid, name, skills in rows
    ]
    # Counted off `rows` rather than off `bots`, so the arithmetic reads the TYPED tuple
    # instead of re-deriving it from an `Any`-valued dict it just built.
    counts = [count_or_unknown(skills) for _, _, skills in rows]
    not_checked = [uuid for (uuid, _, skills) in rows if skills is None]
    measured = [n for n in counts if n is not None]
    return {
        "bots": bots,
        "bot_count": len(bots),
        "not_checked": not_checked,
        "not_checked_count": len(not_checked),
        "with_skills": sum(1 for n in measured if n > 0),
        "without_skills": sum(1 for n in measured if n == 0),
        "skill_total": sum(measured),
        "managed_catalog_ids": (
            [s.get("id") for s in ((managed or {}).get("skills") or [])]
            if managed_status == 200 and isinstance(managed, dict)
            else None
        ),
        "scope_note": "a Bot is readable here only while ListGrokBotAgents admits to knowing "
        "it; absence from the durable index is 'not addressable', never 'deleted'",
    }


def envelope(
    read: str, source: str, data: Dict[str, Any], plat: Platform, now: dt.datetime
) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "read": read,
        "read_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Not decoration: the guarantee is inherited from gb-pull-inventory.rpc, and a consumer
        # that sees this key can branch on it without reading this file.
        "read_only": True,
        "source": source,
        "platform": plat.to_json(),
        "data": data,
    }


# ---------------------------------------------------------------------------------------------
# Human renders
# ---------------------------------------------------------------------------------------------
def num(value: Optional[int]) -> str:
    """A count, or the word this tool uses for "nobody read it"."""
    return "not checked" if value is None else str(value)


def flag(value: Optional[bool]) -> str:
    return "not checked" if value is None else ("on" if value else "off")


def render_account(d: Dict[str, Any]) -> str:
    c = d["counts"]
    lines = [
        f"account surface      auto-review {flag(d['auto_review_enabled'])}"
        f" ({num(c['auto_review_rules'])} rules, scope {d['auto_review_scope'] or 'unknown'})",
        f"  mcp servers        {num(c['mcp_servers'])}",
        f"  plugins            {num(c['plugins'])}",
        f"  computers          {num(c['computers'])}",
        f"  memory shards      {num(c['memory_shards'])}",
        f"  form vault keys    {num(c['form_vault_keys'])}",
        f"  time zone          {d['user_time_zone'] or 'not checked'}",
    ]
    for rule in d["auto_review_rules"] or []:
        lines.append(f"    rule  {str(rule)[:96]}")
    return "\n".join(lines)


def render_usage(d: Dict[str, Any]) -> str:
    if not d["measured"]:
        return f"usage               UNKNOWN\n  {d['why']}"
    pct = d["usage_percent"]
    return "\n".join(
        [
            f"usage               {'not checked' if pct is None else f'{pct}%'} of the "
            f"included allowance ({d['plan_label'] or 'plan unknown'})",
            f"  on-demand          {flag(d['on_demand_enabled'])}"
            f" (eligible {flag(d['on_demand_eligible'])})",
            f"  period start       {d['period_start'] or 'not checked'}",
            f"  next reset         {d['next_reset'] or 'not checked'}",
            f"  billing cycle      "
            f"{'not checked' if d['billing_cycle'] is None else json.dumps(d['billing_cycle'])}",
        ]
    )


def render_computers(d: Dict[str, Any]) -> str:
    lines = [f"computers           {num(d['count'])} registered"]
    for c in d["computers"] or []:
        lines.append(
            f"  {str(c.get('label') or '?'):<20} root={c.get('local_root') or '?'} "
            f"supervised={c.get('supervised')} "
            f"standing-grants={c.get('local_standing_grants_capable')}"
        )
    return "\n".join(lines)


def render_plugins(d: Dict[str, Any]) -> str:
    lines = [
        f"plugins             {num(d['count'])} installed, {num(d['enabled'])} enabled",
        f"  {d['scope_note']}",
    ]
    for p in d["plugins"] or []:
        lines.append(
            f"  {str(p.get('name') or '?'):<28} enabled={p.get('enabled')} "
            f"status={p.get('status')} publisher={p.get('publisher') or '?'}"
            f"{' (verified)' if p.get('verified_publisher') else ''}"
        )
    return "\n".join(lines)


def render_mcp(d: Dict[str, Any]) -> str:
    eff = d["effective_server_names"]
    lines = [
        f"mcp                 {num(d['account_server_count'])} account server(s), "
        f"{num(d['tool_total'])} tool(s) across the census",
        f"  effective config   {'not checked' if eff is None else (', '.join(eff) or 'none')}",
        f"  auto-review        {flag(d['auto_review_enabled'])}"
        f" ({num(d['auto_review_rule_count'])} rules)",
        f"  {d['scope_note']}",
    ]
    for s in d["tool_census"] or []:
        lines.append(
            f"  {str(s['server_id'] or '?'):<28} status={s['status']} "
            f"tools={s['tool_count']}"
        )
    return "\n".join(lines)


def render_skills(d: Dict[str, Any]) -> str:
    lines = [
        f"skills              {d['skill_total']} across {d['bot_count']} addressable Bot(s): "
        f"{d['with_skills']} with skills, {d['without_skills']} measured empty, "
        f"{d['not_checked_count']} not checked",
        f"  managed catalog    {num(count_or_unknown(d['managed_catalog_ids']))} entries",
        f"  {d['scope_note']}",
    ]
    for b in sorted(d["bots"], key=lambda r: str(r["name"] or "")):
        lines.append(
            f"  {str(b['name'] or b['uuid'])[:28]:<28} {num(b['skill_count'])} skill(s)"
        )
    return "\n".join(lines)


RENDER = {
    "account": render_account,
    "usage": render_usage,
    "computers": render_computers,
    "plugins": render_plugins,
    "mcp": render_mcp,
    "skills": render_skills,
}


# ---------------------------------------------------------------------------------------------
# Live reads
# ---------------------------------------------------------------------------------------------
def read_account(mod: Any, token: str) -> Tuple[Dict[str, Any], str]:
    """`account_surface()` verbatim. Four files project this payload by hand today.

    A slice verb (`computers`, `plugins`, `mcp`) pays for the whole five-RPC read rather than
    issuing its own one-shot call, and that is a decision rather than an oversight: the
    projection of each field lives in exactly one place (`gb-pull-inventory.account_surface`),
    and a second projection of the same payload is how two readers come to disagree about what
    `supervised` means. Four extra reads is cheaper than that.
    """
    return mod.account_surface(token), (
        "GrokBotService/GetGrokBotUserMcpSettings + DashboardService/ListUserPluginInstalls + "
        "GrokBotService/{ListGrokBotUserComputers,ListGrokBotMemoryShards,"
        "ListGrokBotUserFormVaultKeys} via gb-pull-inventory.account_surface"
    )


def read_usage(mod: Any, token: str) -> Tuple[Dict[str, Any], str]:
    usage = mod.usage_status(token)
    st, cycle = mod.rpc(token, "GetCurrentPeriodUsage", {}, service=DASH)
    return usage_view(usage, st, cycle), (
        "DashboardService/GetSandUsageStatus via gb-pull-inventory.usage_status + "
        "DashboardService/GetCurrentPeriodUsage"
    )


def read_mcp(mod: Any, token: str) -> Tuple[Dict[str, Any], str]:
    account = mod.account_surface(token)
    st_e, eff = mod.rpc(token, "GetEffectiveMcpConfigForUser", {}, service=DASH)
    st_t, tools = mod.rpc(token, "ListSandMcpTools", {}, service=DASH)
    return mcp_view(account, st_e, eff, st_t, tools), (
        "GrokBotService/GetGrokBotUserMcpSettings + "
        "DashboardService/GetEffectiveMcpConfigForUser + DashboardService/ListSandMcpTools"
    )


def read_skills(mod: Any, token: str) -> Tuple[Dict[str, Any], str]:
    """Per-Bot skills over the DURABLE index — the only Bots any agent RPC will answer for.

    `durable_index` is keyed by legacy uuid and that uuid is what `ListGrokBotAgentSkills` wants.
    (The sibling trap is measured and named: `ListGrokBotAgentAutomations` takes the uuid too,
    and passing the numeric `id` returns 400.)
    """
    durable = mod.durable_index(token)
    rows = [
        (uuid, (rec or {}).get("name"), mod.agent_skills(token, uuid))
        for uuid, rec in sorted(
            durable.items(), key=lambda kv: str((kv[1] or {}).get("name") or "")
        )
    ]
    st_m, managed = mod.rpc(token, "GetManagedSkills", {}, service=DASH)
    return skills_view(rows, st_m, managed), (
        "GrokBotService/ListGrokBotAgents + GrokBotService/ListGrokBotAgentSkills (per Bot) + "
        "DashboardService/GetManagedSkills"
    )


def read_account_slice(
    which: str,
) -> Any:
    """`computers`/`plugins`/`account` all derive from one account read; this picks the view."""
    view = {
        "account": account_view,
        "computers": computers_view,
        "plugins": plugins_view,
    }[which]

    def go(mod: Any, token: str) -> Tuple[Dict[str, Any], str]:
        account, source = read_account(mod, token)
        return view(account), source

    return go


READERS = {
    "account": read_account_slice("account"),
    "computers": read_account_slice("computers"),
    "plugins": read_account_slice("plugins"),
    "usage": read_usage,
    "mcp": read_mcp,
    "skills": read_skills,
}


# ---------------------------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------------------------
def _fixture_account() -> Dict[str, Any]:
    """One complete `account_surface()` document, shaped exactly like the measured one."""
    return {
        "auto_review_enabled": True,
        "auto_review_rules": ["never force-push", "ask before spending"],
        "auto_review_scope": "account",
        "user_time_zone": "America/Denver",
        "mcp_servers": [{"server_id": "linear", "has_custom_instructions": False}],
        "plugins": [
            {
                "name": "linear",
                "enabled": True,
                "status": "ACTIVE",
                "publisher": "acme",
                "verified_publisher": True,
            },
            {
                "name": "stale",
                "enabled": False,
                "status": "ARCHIVED",
                "publisher": "acme",
                "verified_publisher": False,
            },
        ],
        "computers": [
            {
                "label": "studio",
                "machine_id": "m1",
                "local_root": "/Users/j",
                "supervised": True,
                "local_standing_grants_capable": True,
                "last_seen_at_ms": 1,
            }
        ],
        "memory_shards": [{"agent_id": "u1", "harness": "box", "has_content": False}],
        "form_vault_keys": [],
    }


def _fixture_platform(*, readable: bool, with_support: bool = False) -> Platform:
    """A platform fixture. `with_support` is the case that matters and is easy to miss.

    `readable=False, with_support=False` is Linux as shipped: no support directory AND no
    credential read, so EITHER gate refuses it — which means it cannot prove the capability
    gate works. `readable=False, with_support=True` is Linux with `GB_SUPPORT_DIR` exported
    (a real, documented configuration): there IS a directory, so only the capability gate
    stands between this tool and a macOS-only `security` call. Measured against a mutant that
    deleted that gate: the with_support=False fixture passed anyway.
    """
    cap = Capability(
        "credential_read",
        AVAILABLE if readable else NOT_IMPLEMENTED,
        "read the desktop client's own session",
        None if readable else "run from a signed-in macOS desktop",
    )
    return Platform(
        os="macOS" if readable else "Linux",
        sys_platform="darwin" if readable else "linux",
        status=SUPPORTED if readable else UNSUPPORTED,
        support_dir=(
            pathlib.Path("/tmp/gb-inventory-fixture")
            if (readable or with_support)
            else None
        ),
        support_dir_exists=readable or with_support,
        path_source="fixture",
        forced=with_support,
        why="fixture platform",
        remediation=None if readable else "use a macOS desktop",
        capabilities=(cap,),
    )


def selftest() -> int:
    """Every view fires on an inline fixture, and every positive has a known-bad sibling.

    The legs that matter are the four that defend a DISTINCTION rather than a happy path:
    not-checked vs measured-zero (2, 6), unknown-spend vs zero-spend (3), a failed effective-MCP
    read vs an empty one (4, 5), and the inherited write refusal (7) — which is the only claim in
    this file's docstring that could otherwise be prose.
    """
    checks = 0
    fails: List[str] = []

    def check(ok: bool, why: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            fails.append(why)

    acct = _fixture_account()

    # 1. project() returns the named slice, and REFUSES a document that lost a key.
    check(
        project(acct, ("computers", "plugins")).keys() == {"computers", "plugins"},
        "project did not return exactly the requested keys",
    )
    try:
        broken = {k: v for k, v in acct.items() if k != "computers"}
        project(broken, ACCOUNT_KEYS)
        check(False, "KNOWN-BAD: a document missing `computers` was projected anyway")
    except SchemaDrift as exc:
        check("computers" in str(exc), "SchemaDrift did not name the missing key")
    except Exception as exc:
        # A bare `KeyError` out of `project` means the guard was removed and the dict access
        # became the detector. That is still a failure, but a selftest that TRACEBACKS reports
        # it worse than one that says FAIL — measured against exactly that mutant.
        check(
            False,
            f"KNOWN-BAD: drift raised {type(exc).__name__} instead of SchemaDrift — "
            f"the missing key is not named",
        )

    # 2. not-checked vs measured-zero, at the one place the decision is made.
    check(count_or_unknown([]) == 0, "an empty measured list was not reported as 0")
    check(
        count_or_unknown(None) is None, "KNOWN-BAD: not-checked was coerced to a number"
    )
    check(
        count_or_unknown(None) != 0 and count_or_unknown([]) is not None,
        "KNOWN-BAD: None and [] became the same answer",
    )

    # 3. the spend position: unknown is never zero.
    live = usage_view(
        {
            "usage_percent": 41,
            "plan_label": "Ultra",
            "on_demand_enabled": True,
            "on_demand_eligible": True,
            "period_start": "2026-09-01",
            "next_reset": "2026-10-01",
            "has_available_usage": True,
            "dashboard_url": "https://example",
        },
        200,
        {"planUsage": {"cents": 1234}},
    )
    check(
        live["measured"] and live["usage_percent"] == 41,
        "a 200 usage read was not reported as measured",
    )
    check(
        live["billing_cycle"] == {"cents": 1234},
        "GetCurrentPeriodUsage.planUsage was dropped",
    )
    check(
        usage_view(None, 0, "boom")["usage_percent"] is None
        and usage_view(None, 0, "boom")["measured"] is False,
        "KNOWN-BAD: an unreadable spend position was reported as a number",
    )
    check(
        usage_view(None, 200, {"planUsage": {}})["billing_cycle"] is None,
        "an unmeasured usage read still claimed a billing cycle",
    )
    check(
        usage_view({"usage_percent": 0, "plan_label": "Ultra"}, 500, "err")[
            "billing_cycle"
        ]
        is None,
        "KNOWN-BAD: a non-200 GetCurrentPeriodUsage was read as a cycle anyway",
    )

    # 4/5. the effective MCP config: three outcomes, three answers.
    check(
        effective_server_names(
            200, {"configJson": json.dumps({"mcpServers": {"b": 1, "a": 2}})}
        )
        == ["a", "b"],
        "the effective server names were not parsed out of configJson",
    )
    check(
        effective_server_names(404, {}) is None,
        "KNOWN-BAD: a failed effective-config read answered a list",
    )
    check(
        effective_server_names(200, {"configJson": "{not json"}) == [],
        "a parsed-but-unreadable config did not answer the empty measurement",
    )
    check(
        effective_server_names(404, {})
        != effective_server_names(200, {"configJson": "{"}),
        "KNOWN-BAD: 'not checked' and 'parsed to none' became the same answer",
    )

    mcp = mcp_view(
        acct,
        200,
        {"configJson": json.dumps({"mcpServers": {"linear": {}}})},
        200,
        {
            "servers": [
                {
                    "serverIdentifier": "linear",
                    "status": "READY",
                    "tools": [{"name": "b"}, {"name": "a"}, {}],
                }
            ]
        },
    )
    check(
        mcp["tool_total"] == 3 and mcp["tool_census"][0]["tool_names"] == ["a", "b"],
        f"the tool census miscounted: {mcp['tool_total']}",
    )
    check(
        mcp["auto_review_rule_count"] == 2,
        "the account auto-review rules were not counted into the mcp view",
    )
    check(
        mcp_view(acct, 0, "", 0, "")["tool_census"] is None,
        "KNOWN-BAD: an unread tool census answered an empty list",
    )

    # 6. skills: not-checked, measured-empty and populated are three separate numbers.
    sk = skills_view(
        [
            ("u1", "Alpha", [{"name": "s", "description_chars": 3, "body_chars": 9}]),
            ("u2", "Beta", []),
            ("u3", "Gamma", None),
        ],
        200,
        {"skills": [{"id": "k1"}, {"id": "k2"}]},
    )
    check(sk["with_skills"] == 1, f"populated Bots miscounted: {sk['with_skills']}")
    check(
        sk["without_skills"] == 1,
        f"KNOWN-BAD: a measured-empty Bot was not counted as empty: {sk['without_skills']}",
    )
    check(
        sk["not_checked_count"] == 1 and sk["not_checked"] == ["u3"],
        "KNOWN-BAD: an unread Bot was folded in with the empty ones "
        "(the gb-deployment-audit.py:462 collapse)",
    )
    check(sk["skill_total"] == 1, f"skill total wrong: {sk['skill_total']}")
    check(
        count_or_unknown(sk["managed_catalog_ids"]) == 2,
        "the managed catalog was not read",
    )
    check(
        skills_view([("u1", "A", None)], 500, "err")["managed_catalog_ids"] is None,
        "KNOWN-BAD: a failed catalog read answered a list",
    )

    # 7. the read-only contract, INHERITED and proven rather than asserted. No network: the
    #    guard in gb-pull-inventory.rpc runs before the request is built.
    try:
        mod = puller()
    except Exception as exc:
        mod = None
        check(False, f"gb-pull-inventory.py could not be imported: {exc}")
    if mod is not None:
        for method in (
            "CreateGrokBotAgent",
            "SendGrokBotUserMessage",
            "DeleteGrokBotAgent",
        ):
            try:
                mod.rpc("fixture-token", method, {})
                check(False, f"KNOWN-BAD: the write {method} was not refused")
            except SystemExit as exc:
                check(
                    "read-only by contract" in str(exc),
                    f"{method} was refused without naming the contract",
                )
            except Exception as exc:
                check(
                    False, f"{method} raised {type(exc).__name__} instead of refusing"
                )
        for name in (
            "access_token",
            "rpc",
            "account_surface",
            "usage_status",
            "durable_index",
            "agent_skills",
        ):
            check(
                callable(getattr(mod, name, None)),
                f"gb-pull-inventory.py no longer exports {name} — this file's reuse is broken",
            )

    # 8. every method this file can name is a read.
    bad = [m for m, _ in METHODS if not m.startswith(("List", "Get"))]
    check(not bad, f"KNOWN-BAD: non-read method(s) declared: {bad}")
    check(len(METHODS) == len({m for m, _ in METHODS}), "METHODS has a duplicate")

    # 9. the platform gate refuses instead of tracebacking, and only when it should.
    check(
        platform_refusal(TOOL, _fixture_platform(readable=False), "credential_read")
        is not None,
        "a platform that cannot read the session was not refused",
    )
    check(
        platform_refusal(TOOL, _fixture_platform(readable=True), "credential_read")
        is None,
        "KNOWN-BAD: a capable platform was refused",
    )
    mod_a, tok_a, refusal = session(_fixture_platform(readable=False))
    check(
        refusal is not None and TOOL in refusal,
        "session() did not name the tool in its refusal",
    )
    check(
        mod_a is None and tok_a is None,
        "session() returned a module or a token alongside a refusal",
    )
    # THE ISOLATING CASE: a directory exists, so only the CAPABILITY gate can refuse. Without
    # this leg, deleting that gate left the selftest green (measured), because the support-dir
    # gate caught the other fixture and hid the deletion.
    mod_b, tok_b, refusal_b = session(
        _fixture_platform(readable=False, with_support=True)
    )
    check(
        refusal_b is not None and "credential_read" in refusal_b,
        "KNOWN-BAD: a platform WITH a support directory but no credential read was not "
        "refused by the capability gate — the macOS-only keychain call was reachable",
    )
    check(
        mod_b is None and tok_b is None,
        "session() handed back a session for a platform that cannot read one",
    )

    # 10. the envelope says what it is, and every verb renders from its own view.
    env = envelope(
        "account",
        "fixture",
        account_view(acct),
        _fixture_platform(readable=True),
        dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc),
    )
    check(env["schema"] == SCHEMA and env["read_only"] is True, "envelope shape wrong")
    check(
        json.loads(json.dumps(env))["data"]["counts"]["plugins"] == 2,
        "the envelope did not survive a JSON round trip",
    )
    check(
        set(RENDER) == set(VERBS) and set(READERS) == set(VERBS),
        "a verb is declared without a reader or a render",
    )
    views = {
        "account": account_view(acct),
        "computers": computers_view(acct),
        "plugins": plugins_view(acct),
        "usage": live,
        "mcp": mcp,
        "skills": sk,
    }
    for verb, view in views.items():
        text = RENDER[verb](view)
        check(bool(text.strip()), f"{verb} rendered nothing")
        check("None" not in text, f"{verb} leaked a bare `None` into the human render")
    check(
        "not checked" in render_computers(computers_view({"computers": None})),
        "KNOWN-BAD: an unread computer list rendered as a number",
    )
    check(
        "UNKNOWN" in render_usage(usage_view(None, 0, "")),
        "KNOWN-BAD: an unknown spend position rendered as a measurement",
    )

    # 11. THE SURFACE THIS PRODUCER PRESENTS TO THE AUDIT, read the way the audit reads it.
    #     `gb-dogfood.py` resolves actions from a LITERAL choices sequence on the AST and from
    #     argparse's `{a,b}` group in `--help`. Both are fed deliberately (see `InventoryArgs`),
    #     and drift between either of them and `VERBS` is a silent loss of surface, so it is a
    #     failure here rather than a surprise in the next audit.
    import ast as _ast

    declared: Tuple[str, ...] = ()
    metavar = ""
    try:
        tree = _ast.parse(pathlib.Path(__file__).resolve().read_text())
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call) or getattr(node.func, "id", "") != "arg":
                continue
            kw = {k.arg: k.value for k in node.keywords}
            if not isinstance(kw.get("positional"), _ast.Constant):
                continue
            seq = kw.get("choices")
            if isinstance(seq, (_ast.Tuple, _ast.List)):
                declared = tuple(
                    e.value
                    for e in seq.elts
                    if isinstance(e, _ast.Constant) and isinstance(e.value, str)
                )
            mv = kw.get("metavar")
            if isinstance(mv, _ast.Constant):
                metavar = str(mv.value)
    except Exception as exc:
        check(False, f"could not read this file's own AST: {type(exc).__name__}: {exc}")
    check(
        declared == VERBS,
        f"KNOWN-BAD: the literal choices {declared} drifted from VERBS {VERBS} — "
        f"`gb dogfood audit` would report fewer actions than this producer accepts",
    )
    check(
        metavar == "{" + ",".join(VERBS) + "}",
        f"KNOWN-BAD: the metavar {metavar!r} no longer prints the choice group, so "
        f"`gb-dogfood.help_choices` reads zero actions out of --help",
    )

    for f in fails:
        print(f"FAIL: {f}")
    print(f"SELFTEST {'FAIL' if fails else 'PASS'} - {checks - len(fails)}/{checks}")
    return EXIT_FAIL if fails else EXIT_OK


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class InventoryArgs:
    """Read the Grok Bot account surface. Read-only, by inherited contract."""

    # THE CHOICES ARE SPELLED OUT, and that is not redundancy with `VERBS`.
    #
    # `gb dogfood.py:positional_choices` reads a producer's actions off the AST, and `_str_seq`
    # only resolves a LITERAL sequence — `choices=VERBS` is an `ast.Name` and reads as zero
    # actions. The other half of that detector (`help_choices`, which scrapes argparse's own
    # `{a,b}` group out of `--help`) cannot see it either, because `gbargs._kwargs_for:172`
    # sets `metavar` to the type name unless one is given, so argparse prints `[STR]` and never
    # the group. Measured: with `choices=VERBS` and no metavar, this producer presented SIX
    # verbs to an operator and NONE to the audit — which is the exact shape of the gap this
    # file was written to close, reappearing one layer up.
    #
    # So both routes are fed, and a selftest leg parses this file's own AST and fails if the
    # literal ever drifts from `VERBS`.
    read: str = arg(
        default="account",
        help=" | ".join(VERBS),
        choices=("account", "usage", "computers", "plugins", "mcp", "skills"),
        metavar="{account,usage,computers,plugins,mcp,skills}",
        positional=True,
    )
    json: bool = arg(help="machine-readable envelope on stdout")
    selftest: bool = arg(help="run the inline-fixture selftest and exit")


def body() -> int:
    # Answered before argparse, the same way `gb-dogfood.py` and `gb-fleet.py` do it: `read` is
    # positional with a default today, but a selftest that breaks the moment someone makes it
    # required is a selftest nobody runs.
    if "--selftest" in sys.argv[1:]:
        return selftest()

    ns = build_parser(InventoryArgs, prog=TOOL, description=SUMMARY).parse_args()
    read = str(ns.read)

    plat = platform_support()
    mod, token, refusal = session(plat)
    if refusal is not None or mod is None or token is None:
        print(refusal or platform_refusal(TOOL, plat), file=sys.stderr)
        return EXIT_ENVIRONMENT

    try:
        data, source = READERS[read](mod, token)
    except SchemaDrift as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return EXIT_FAIL

    doc = envelope(read, source, data, plat, dt.datetime.now(dt.timezone.utc))
    try:
        print(json.dumps(doc, indent=1) if ns.json else RENDER[read](data))
    except BrokenPipeError:  # a reader who walked away is not a failed measurement
        return EXIT_OK
    return EXIT_OK


if __name__ == "__main__":
    gbmain(body)
