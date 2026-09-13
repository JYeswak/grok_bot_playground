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

MAX_AGE_DAYS = 30
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


def recapture_argv(device: str) -> list[str]:
    return ["bin/gb", "inventory", "pull", "--device", device, "--apply", "--json"]


def _timestamp(value: object) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def validate(doc: dict, *, now: dt.datetime | None = None) -> list[str]:
    errs: list[str] = []
    if doc.get("schema") != "gb-inventory/1":
        errs.append("schema: must be 'gb-inventory/1'")
    for field in (
        "device",
        "hostname",
        "app_version",
        "source",
        "read_route",
        "account_fingerprint",
    ):
        if not doc.get(field):
            errs.append(f"{field}: required")
    recorded = _timestamp(doc.get("recorded_at"))
    if recorded is None:
        errs.append("recorded_at: required RFC3339 timestamp")
    elif now is not None:
        age = now.astimezone(dt.timezone.utc) - recorded
        if age > dt.timedelta(days=MAX_AGE_DAYS) or age < dt.timedelta(0):
            errs.append(f"recorded_at: stale or future ({age.days}d); recapture required")
    pol = doc.get("local_exec_policy")
    if pol not in LOCAL_EXEC_VALUES:
        errs.append(f"local_exec_policy: must be one of {LOCAL_EXEC_VALUES}, got {pol!r}")
    if not doc.get("local_exec_reason"):
        errs.append("local_exec_reason: required policy source")
    if _timestamp(doc.get("policy_recorded_at")) is None:
        errs.append("policy_recorded_at: required policy source date")
    for field in ("auto_review_rules", "plugins", "mcp_servers", "bots"):
        if doc.get(field) is None:
            errs.append(f"{field}: null means not checked; use [] for checked-none")
        elif not isinstance(doc[field], list):
            errs.append(f"{field}: must be a list")
    for index, plugin in enumerate(doc.get("plugins") or []):
        if not isinstance(plugin, dict):
            errs.append(f"plugins[{index}]: must be an object")
            continue
        if not plugin.get("name"):
            errs.append(f"plugins[{index}].name: required")
        if not isinstance(plugin.get("enabled"), bool):
            errs.append(f"plugins[{index}].enabled: required binding state")
        if not plugin.get("status"):
            errs.append(f"plugins[{index}].status: required binding state")
    for bot_index, bot in enumerate(doc.get("bots") or []):
        if not isinstance(bot, dict):
            errs.append(f"bots[{bot_index}]: must be an object")
            continue
        name = bot.get("name") or f"bots[{bot_index}]"
        if not bot.get("name"):
            errs.append(f"bots[{bot_index}].name: required")
        for field in ("routines", "skills"):
            if bot.get(field) is None:
                errs.append(f"bots[{bot_index}].{field}: null means not checked for {name}")
            elif not isinstance(bot[field], list):
                errs.append(f"bots[{bot_index}].{field}: must be a list")
        for routine_index, routine in enumerate(bot.get("routines") or []):
            path = f"bots[{bot_index}].routines[{routine_index}]"
            if not isinstance(routine, dict):
                errs.append(f"{path}: must be an object")
                continue
            if not routine.get("name"):
                errs.append(f"{path}.name: required")
            if routine.get("recent_runs") is None:
                errs.append(f"{path}.recent_runs: null means run history was not opened")
    return errs


def validate_set(
    documents: list[dict],
    expected: dict[str, str],
    *,
    now: dt.datetime,
) -> list[str]:
    errs: list[str] = []
    seen: set[str] = set()
    for index, doc in enumerate(documents):
        device = str(doc.get("device") or "")
        prefix = f"documents[{index}]"
        if device not in expected:
            errs.append(f"{prefix}.device: unknown device {device!r}")
        elif doc.get("hostname") != expected[device]:
            errs.append(
                f"{prefix}.hostname: {doc.get('hostname')!r} != registry {expected[device]!r}"
            )
        if device in seen:
            errs.append(f"{prefix}.device: duplicate device {device!r}")
        seen.add(device)
        errs.extend(f"{prefix}.{error}" for error in validate(doc, now=now))
    missing = [device for device in expected if device not in seen]
    errs.extend(f"documents: missing required device {device!r}" for device in missing)
    app_versions = {doc.get("app_version") for doc in documents if doc.get("app_version")}
    if len(app_versions) > 1:
        errs.append("documents.app_version: client mismatch across desktops")
    account_ids = {
        doc.get("account_fingerprint")
        for doc in documents
        if doc.get("account_fingerprint")
    }
    if len(account_ids) > 1:
        errs.append("documents.account_fingerprint: account mismatch across desktops")
    return errs

def _fixture_document(device: str, hostname: str) -> dict:
    return {
        "schema": "gb-inventory/1",
        "device": device,
        "hostname": hostname,
        "recorded_at": "2026-09-13T12:00:00+00:00",
        "app_version": "0.47.0",
        "source": "fixture authenticated read",
        "read_route": "fixture account RPC + policy",
        "account_fingerprint": "a" * 64,
        "local_exec_policy": "ask-every-time",
        "local_exec_reason": "fixture policy source",
        "policy_recorded_at": "2026-09-13",
        "auto_review_rules": [],
        "plugins": [{"name": "fixture", "enabled": True, "status": "APPROVED"}],
        "mcp_servers": [],
        "bots": [
            {
                "name": "Fixture",
                "skills": [],
                "routines": [{"name": "Check", "recent_runs": []}],
            }
        ],
    }


def selftest() -> int:
    now = dt.datetime(2026, 9, 13, 14, tzinfo=dt.timezone.utc)
    expected = {"studio": "Studio.local", "brain": "Brain.local"}
    good = [_fixture_document(device, host) for device, host in expected.items()]
    legs: list[tuple[str, bool]] = []

    def leg(name: str, passed: bool) -> None:
        legs.append((name, passed))

    def changed(index: int, path: str, value: object) -> list[dict]:
        documents = json.loads(json.dumps(good))
        target = documents[index]
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        if isinstance(target, list):
            target[int(parts[-1])] = value
        else:
            target[parts[-1]] = value
        return documents

    leg("good-two-device-set", validate_set(good, expected, now=now) == [])
    wrong = changed(0, "device", "other")
    wrong_errors = validate_set(wrong, expected, now=now)
    leg("wrong-device-path", any("documents[0].device: unknown" in e for e in wrong_errors))
    duplicate = changed(1, "device", "studio")
    duplicate_errors = validate_set(duplicate, expected, now=now)
    leg("duplicate-device-path", any("documents[1].device: duplicate" in e for e in duplicate_errors))
    stale = changed(0, "recorded_at", "2026-07-01T00:00:00+00:00")
    leg("stale-record-path", any("documents[0].recorded_at: stale" in e for e in validate_set(stale, expected, now=now)))
    client = changed(1, "app_version", "0.46.0")
    leg("client-mismatch-path", "documents.app_version: client mismatch across desktops" in validate_set(client, expected, now=now))
    account = changed(1, "account_fingerprint", "b" * 64)
    leg("account-mismatch-path", "documents.account_fingerprint: account mismatch across desktops" in validate_set(account, expected, now=now))
    null_facet = changed(0, "mcp_servers", None)
    leg("null-facet-path", any("documents[0].mcp_servers: null" in e for e in validate_set(null_facet, expected, now=now)))
    routine = changed(0, "bots.0.routines.0.recent_runs", None)
    leg("routine-history-path", any("documents[0].bots[0].routines[0].recent_runs" in e for e in validate_set(routine, expected, now=now)))
    plugin = changed(0, "plugins.0.enabled", None)
    leg("plugin-binding-path", any("documents[0].plugins[0].enabled" in e for e in validate_set(plugin, expected, now=now)))
    policy = changed(0, "local_exec_reason", None)
    leg("policy-source-path", any("documents[0].local_exec_reason" in e for e in validate_set(policy, expected, now=now)))
    leg("recapture-argv-exact", recapture_argv("brain") == ["bin/gb", "inventory", "pull", "--device", "brain", "--apply", "--json"])
    for name, passed in legs:
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
    passed = sum(ok for _, ok in legs)
    print(f"SELFTEST {'PASS' if passed == len(legs) else 'FAIL'} - {passed}/{len(legs)}")
    return 0 if passed == len(legs) else 1


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
    ap.add_argument("--validate-set", nargs="+", default=None, metavar="FILE")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    inv = root / "inventory"

    if args.selftest:
        return selftest()

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

    if args.validate or args.validate_set:
        files = [pathlib.Path(args.validate)] if args.validate else [pathlib.Path(p) for p in args.validate_set]
        documents: list[dict] = []
        for file in files:
            try:
                documents.append(json.loads(file.read_text()))
            except Exception as exc:
                print(f"INVALID {file}: unreadable ({exc})", file=sys.stderr)
                return 2
        now = dt.datetime.now(dt.timezone.utc)
        if args.validate_set:
            registry = json.loads((root / "desktops.json").read_text())
            expected = {
                item["label"]: item["hostname"] for item in registry.get("desktops") or []
            }
            errs = validate_set(documents, expected, now=now)
        else:
            errs = validate(documents[0], now=now)
        if errs:
            print("INVALID inventory:", file=sys.stderr)
            for error in errs:
                print(f"  - {error}", file=sys.stderr)
            for document in documents:
                if document.get("device"):
                    print(
                        "  recapture: " + json.dumps(recapture_argv(document["device"])),
                        file=sys.stderr,
                    )
            return 1
        print("VALID " + " ".join(str(file) for file in files))
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
