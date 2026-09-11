#!/usr/bin/env python3
"""gb-record-inventory — turn the per-desktop hand audit into a machine-checkable artifact.

Routines, skills, plugins, MCP servers, Auto-review rules and the local-execution policy are
server-side or desktop-local: `gb-deployment-audit.py` cannot see any of them (AGENTS.md §ORACLE,
"known oracle gap"). Until an authenticated read exists, the honest substitute is a STRUCTURED
hand record — not prose in findings/ — so `g8-desktop-inventory` and `g9-routine-health` can grade
it and go stale on their own.

  gb-record-inventory.py --device studio --template   # write a skeleton to fill in
  gb-record-inventory.py --validate inventory/2026-09-11T0700.studio.json
  gb-record-inventory.py --list                       # what is recorded, and how old

The skeleton is deliberately full of nulls. A null is "not looked at"; it fails validation.
Writing `[]` means "looked, found none" — that is a measurement and it passes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_text  # noqa: E402

LOCAL_EXEC_VALUES = ["ask-every-time", "always-allow", "never"]


def roster_names(root: pathlib.Path) -> list[str]:
    """Bot names from the newest audit, so the recorder never asks a human to retype the roster."""
    d = root / "deployment"
    if not d.is_dir():
        return []
    rows = sorted(
        c for c in d.iterdir() if c.suffix == ".json" and c.name[:10].count("-") == 2
    )
    if not rows:
        return []
    try:
        audit = json.loads(rows[-1].read_text(errors="replace"))
    except Exception:
        return []
    return [b["name"] for b in (audit.get("bots") or []) if b.get("name")]


def template(device: str, bots: list[str] | None = None) -> dict:
    seeded = None
    if bots:
        # Pre-named, still unanswered: skills/routines stay null so "not checked" cannot be
        # mistaken for "none". The roster is the one thing we already know.
        seeded = [{"name": n, "skills": None, "routines": None} for n in bots]
    return {
        "_howto": "Replace every null. [] means 'checked, none exist'. null means 'not checked' and fails validation.",
        "schema": "gb-inventory/1",
        "device": device,
        "hostname": None,
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "app_version": None,
        "local_exec_policy": None,
        "local_exec_note": "one of: " + " | ".join(LOCAL_EXEC_VALUES),
        "auto_review_rules": None,
        "_auto_review_rules_shape": [
            {"kind": "require-approval|always-allow", "rule": "verbatim rule text"}
        ],
        "plugins": None,
        "_plugins_shape": [{"name": "", "account": "", "enabled_tools": None}],
        "mcp_servers": None,
        "_mcp_servers_shape": [
            {
                "name": "",
                "transport": "stdio|streamable-http",
                "tools_enabled": 0,
                "tools_total": 0,
            }
        ],
        "bots": seeded,
        "_bots_shape": [
            {
                "name": "",
                "skills": [],
                "routines": [
                    {
                        "name": "",
                        "schedule": "e.g. weekdays 08:00 America/Denver",
                        "enabled": True,
                        "recent_runs": ["ok", "ok", "failed"],
                    }
                ],
            }
        ],
    }


def validate(doc: dict) -> list[str]:
    errs = []
    if doc.get("schema") != "gb-inventory/1":
        errs.append("schema must be 'gb-inventory/1'")
    for field in ("device", "hostname", "app_version"):
        if not doc.get(field):
            errs.append(f"{field} is required")
    pol = doc.get("local_exec_policy")
    if pol not in LOCAL_EXEC_VALUES:
        errs.append(
            f"local_exec_policy must be one of {LOCAL_EXEC_VALUES}, got {pol!r}"
        )
    for field in ("auto_review_rules", "plugins", "mcp_servers", "bots"):
        if doc.get(field) is None:
            errs.append(
                f"{field} is null — that means 'not checked'. Use [] if you checked and found none."
            )
        elif not isinstance(doc[field], list):
            errs.append(f"{field} must be a list")
    for b in doc.get("bots") or []:
        if not b.get("name"):
            errs.append("a bot entry has no name")
        if b.get("routines") is None:
            errs.append(
                f"{b.get('name')}: routines is null — open Routines for this Bot. [] if it has none."
            )
        if b.get("skills") is None:
            errs.append(
                f"{b.get('name')}: skills is null — type / in its composer. [] if it has none."
            )
        for r in b.get("routines") or []:
            if not r.get("name"):
                errs.append(f"{b.get('name')}: a routine has no name")
            if r.get("recent_runs") is None:
                errs.append(
                    f"{b.get('name')}/{r.get('name')}: recent_runs is null — open the routine's run history"
                )
    return errs


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(root))
    ap.add_argument("--device", default=None, help="label from desktops.json")
    ap.add_argument("--template", action="store_true")
    ap.add_argument(
        "--no-roster",
        action="store_true",
        help="do not pre-name Bots from the newest audit",
    )
    ap.add_argument("--validate", default=None, metavar="FILE")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    inv = root / "inventory"

    if args.list:
        if not inv.is_dir() or not any(inv.glob("*.json")):
            print("no inventory recorded — run --template and fill it in")
            return 1
        today = dt.datetime.now(dt.timezone.utc).date()
        for f in sorted(inv.glob("*.json")):
            d = json.loads(f.read_text())
            age = (today - dt.date.fromisoformat(f.name[:10])).days
            nr = sum(len(b.get("routines") or []) for b in (d.get("bots") or []))
            print(
                f"{f.name:<40} {age:>3}d  device={d.get('device')} policy={d.get('local_exec_policy')} "
                f"rules={len(d.get('auto_review_rules') or [])} bots={len(d.get('bots') or [])} routines={nr}"
            )
        return 0

    if args.validate:
        f = pathlib.Path(args.validate)
        try:
            doc = json.loads(f.read_text())
        except Exception as e:
            print(f"INVALID {f}: unreadable ({e})", file=sys.stderr)
            return 2
        errs = validate(doc)
        if errs:
            print(f"INVALID {f}:", file=sys.stderr)
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print(f"VALID {f}")
        return 0

    if args.template:
        if not args.device:
            print(
                "--template needs --device <label from desktops.json>", file=sys.stderr
            )
            return 64
        reg = json.loads((root / "desktops.json").read_text())
        if args.device not in {d["label"] for d in reg["desktops"]}:
            print(f"device {args.device!r} is not in desktops.json", file=sys.stderr)
            return 64
        inv.mkdir(parents=True, exist_ok=True)
        out = (
            inv / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.{args.device}.json"
        )
        names = [] if args.no_roster else roster_names(root)
        atomic_write_text(
            out, json.dumps(template(args.device, names), indent=1) + "\n"
        )
        print(
            f"{out}  ({len(names)} Bot(s) pre-named from the newest audit)"
            if names
            else str(out)
        )
        return 0

    ap.print_help()
    return 64


if __name__ == "__main__":
    sys.exit(main())
