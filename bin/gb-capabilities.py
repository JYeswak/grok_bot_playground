#!/usr/bin/env python3
"""gb-capabilities — the full derived capability list for this account, grouped and counted.

Every entitlement xAI evaluates for this account arrives as a djb2-hashed Statsig gate. This joins
the newest audit's `enabled_ids` against the cumulative hash→name dictionary and prints what we can
name, by family, with the unnameable remainder stated rather than hidden.

The ceiling is real and worth understanding before reading the numbers: names are recovered from
the string literals inside the Grok Bot client's own bundle. Measured 2026-09-11 — 692 of 2763
evaluated gates (25%) appear there. The other ~2000 are evaluated by the same Cursor backend for
its other surfaces (the IDE, the web app, Bugbot) and are simply not this client's business. So
"unnamed" overwhelmingly means "not a Grok Bot capability", NOT "a capability we failed to see".
The one thing it does mean: a gate we cannot name is also a gate we cannot act on.

  gb-capabilities.py                 # summary + families
  gb-capabilities.py --family sand   # one family in full
  gb-capabilities.py --markdown      # write capabilities/<date>.md
  gb-capabilities.py --json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import load, newest_audit, statsig_config  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402

# Ordered: first match wins, so the specific families come before the generic ones.
FAMILIES = [
    ("grok_bot", "Grok Bot server surface", ("grok_bot_",)),
    ("sand", "Grok Bot desktop client", ("sand_", "sandbox_")),
    (
        "mcp",
        "MCP / tool servers",
        (
            "mcp_",
            "unified_mcp",
            "meta_mcp",
            "playwright_mcp",
            "dedupe_mcp",
            "proper_well_known_for_mcp",
        ),
    ),
    (
        "plugin",
        "Plugins, skills, marketplace",
        (
            "plugin",
            "marketplace",
            "skill",
            "publish_user",
            "team_marketplace",
            "customize_plugin",
            "onboard_skill",
            "private_skills",
            "managed_review",
        ),
    ),
    ("automation", "Routines / automations", ("automation", "routine")),
    (
        "cloud_agent",
        "Cloud Agents",
        ("cloud_agent", "cloud_env", "cloud_multitask", "cloud_canvas"),
    ),
    (
        "browser",
        "Browser / computer use",
        ("browser", "internal_browser", "show_browser"),
    ),
    (
        "model",
        "Model routing and pickers",
        (
            "model_",
            "use_model",
            "route_ent",
            "migrate_default_model",
            "opt_devs_into",
            "slow_model",
            "web_cloud_agent_new_model",
            "website_use_new_model",
            "disable_smart_auto",
            "explicit_subagent_models",
            "cli_model_picker",
        ),
    ),
    (
        "agent",
        "Agent runtime / subagents",
        (
            "agent_",
            "agentkit",
            "agentdata",
            "nal_agent",
            "named_agent",
            "subagent",
            "parallel_agent",
            "glass_subagent",
            "push_local_agent",
            "defer_cursor_agent",
            "cursor_agent",
            "enable_cursor_agent",
            "proxy_agent",
            "local_agent",
            "focus_gate_local_agent",
            "fix_claude_subagent",
            "fix_grok_subagent",
            "hide_async_subagent",
        ),
    ),
    (
        "team",
        "Team / enterprise controls",
        ("team_", "admin_", "org_", "dashboard_", "enable_origin", "origin_"),
    ),
    ("glass", "Desktop shell (glass)", ("glass",)),
]


def family_of(name: str) -> str:
    for key, _label, prefixes in FAMILIES:
        if any(name.startswith(p) or p in name for p in prefixes):
            return key
    return "other"


def render_tunables(root: pathlib.Path, mapping: dict) -> str:
    """The parameter surface, read live: what the knobs are currently SET TO.

    A config's name is hashed like a gate's, but its value is plaintext — so even a config we
    cannot name is readable, and the unnamed section below is evidence, not a hole. Values are
    truncated at 160 chars; the audit keeps a digest of the full value so a change is detectable
    even where it is not printable.
    """
    cfg = statsig_config()
    configs = cfg.get("dynamic_configs") or {}
    layers = cfg.get("layer_configs") or {}
    named, unnamed = [], []
    for kind, src in (("config", configs), ("layer", layers)):
        for entry in src.values():
            h = entry.get("name")
            val = entry.get("value") if isinstance(entry.get("value"), dict) else {}
            if not val:
                continue
            (named if h in mapping else unnamed).append(
                (mapping.get(h, h), kind, val, entry)
            )

    out = [
        f"# Tunables — live payload, {len(named)} named / {len(unnamed)} unnamed\n",
        "A gate says whether a capability is on. A tunable says with what numbers. Names are",
        "hashed the same way gates are; values are not, so an unnamed row is still readable.\n",
    ]
    for title, rows in (
        ("Named", sorted(named)),
        ("Unnamed (id only — values are still plaintext)", sorted(unnamed)),
    ):
        out.append(f"\n## {title} — {len(rows)}\n")
        for name, kind, val, entry in rows:
            group = entry.get("group_name")
            head = (
                f"### `{name}`"
                + (f" · {kind}" if kind != "config" else "")
                + (f" · group `{group}`" if group else "")
            )
            out.append(head + "\n")
            for k, v in sorted(val.items()):
                s = json.dumps(v, separators=(",", ":"))
                out.append(f"- `{k}` = `{s[:160]}{'…' if len(s) > 160 else ''}`")
            out.append("")
    return "\n".join(out) + "\n"


def render_family(groups, labels, key) -> int:
    v = groups.get(key)
    if not v:
        print(f"no family {key!r}; have: {', '.join(sorted(groups))}", file=sys.stderr)
        return 2
    print(f"## {labels.get(key, key)} — {len(v['on'])} on / {len(v['off'])} off\n")
    for n in v["on"]:
        print(f"  + {n}")
    for n in v["off"]:
        print(f"  - {n}")
    return 0


def render_report(summary: dict, groups: dict, labels: dict) -> str:
    pct = 100 * (summary["nameable"] or 0) // (summary["evaluated"] or 1)
    out = [
        f"# Derived capability list — {summary['audit']}, client {summary['app_version']}\n",
        f"- **{summary['enabled']} of {summary['evaluated']}** gates enabled for this account.",
        f"- **{summary['named_enabled']}** of those can be named from the client bundle; "
        f"**{summary['unnamed_enabled']}** cannot.",
        f"- Nameable at all: **{summary['nameable']}** of {summary['evaluated']} ({pct}%). The remainder are "
        f"evaluated by the same backend for Cursor's other surfaces and are not this client's.\n",
        "| family | on | off |",
        "|---|---:|---:|",
    ]
    order = sorted(groups.items(), key=lambda kv: -len(kv[1]["on"]))
    for k, v in order:
        out.append(f"| {labels.get(k, k)} (`{k}`) | {len(v['on'])} | {len(v['off'])} |")
    for k, v in order:
        out.append(
            f"\n## {labels.get(k, k)} — {len(v['on'])} on / {len(v['off'])} off\n"
        )
        out += [f"- `+ {n}`" for n in v["on"]] + [f"- `- {n}`" for n in v["off"]]
    return "\n".join(out) + "\n"


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default=None)
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--tunables",
        action="store_true",
        help="write capabilities/tunables-<date>.md from the LIVE payload",
    )
    args = ap.parse_args()

    apath, audit = newest_audit(root)
    if audit is None:
        raise SystemExit("no deployment audit — run bin/gb-deployment-audit.py")
    g = audit.get("gates") or {}
    names = set(g.get("enabled_names") or [])
    if not names:
        print("ERROR audit carries no gate names — decoding broke", file=sys.stderr)
        return 2
    cache = (load(root / "state" / "gate-name-map.json") or {}).get("map") or {}

    groups: dict[str, dict[str, list[str]]] = {}
    for n in sorted(names):
        groups.setdefault(family_of(n), {"on": [], "off": []})["on"].append(n)
    for n in sorted(set(cache.values()) - names):
        groups.setdefault(family_of(n), {"on": [], "off": []})["off"].append(n)

    labels = {k: lbl for k, lbl, _ in FAMILIES} | {"other": "Other Cursor surfaces"}
    summary = {
        "audit": apath.name,
        "app_version": (audit.get("app") or {}).get("version"),
        "evaluated": g.get("total"),
        "enabled": g.get("enabled"),
        "nameable": g.get("decoded"),
        "named_enabled": len(names),
        "unnamed_enabled": g.get("undecoded_enabled"),
        "families": {
            k: {"label": labels.get(k, k), "on": len(v["on"]), "off": len(v["off"])}
            for k, v in sorted(groups.items())
        },
    }

    if args.tunables:
        out = root / "capabilities" / f"tunables-{apath.name[:10]}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out, render_tunables(root, {h: n for h, n in cache.items()}))
        print(out)
        return 0
    if args.json:
        print(json.dumps({**summary, "groups": groups}, indent=1))
        return 0
    if args.family:
        return render_family(groups, labels, args.family)

    body = render_report(summary, groups, labels)
    if args.markdown:
        out = root / "capabilities" / f"{apath.name[:10]}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out, body)
        print(out)
        return 0
    print("\n".join(body.splitlines()[:20]))
    print(
        f"\n… {summary['named_enabled']} named-enabled entries; "
        f"--family <key> for one, --markdown to write capabilities/<date>.md"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
