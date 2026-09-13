#!/usr/bin/env python3
"""gb-feature-matrix — join the curated feature registry against this account's live entitlements.

`features.json` says what a Grok Bot capability IS and where it is switched on.
`deployment/<stamp>.json` says which feature gates xAI has enabled FOR THIS ACCOUNT.
This joins them, so "can we use X?" is computed from the shipped client every time
instead of remembered from a doc someone read in September.

Three states per feature, and the third one is the point:
  ON        every named gate for it is enabled (or it has no gate and is documented)
  OFF       at least one named gate is explicitly disabled for this account
  UNNAMED   the gate exists in the payload but its literal is not in our name
            dictionary — we cannot say. Absence of a name is not absence of a feature.

Usage:
  gb-feature-matrix.py                 # full matrix
  gb-feature-matrix.py --todo          # only what is ON and not yet verified adopted
  gb-feature-matrix.py --id custom-mcp # one feature, with all evidence
  gb-feature-matrix.py --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import load, newest_audit  # noqa: E402

ON, OFF, UNNAMED, NOGATE = "ON", "OFF", "UNNAMED", "n/a"


def classify(feature: dict, enabled: set[str], known: set[str]) -> tuple[str, list[str]]:
    gates = feature.get("gates") or []
    if not gates:
        return NOGATE, []
    detail = []
    state = ON
    for g in gates:
        if g in enabled:
            detail.append(f"{g}=on")
        elif g in known:
            detail.append(f"{g}=off")
            state = OFF if state != UNNAMED else state
        else:
            detail.append(f"{g}=?")
            if state == ON:
                state = UNNAMED
    return state, detail


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(root))
    ap.add_argument("--id", default=None, help="show one feature in full")
    ap.add_argument("--todo", action="store_true", help="only features that are ON and not measured-adopted")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.root)

    reg = load(root / "features.json")
    if not reg or not reg.get("features"):
        print("ERROR features.json missing or empty", file=sys.stderr)
        return 2
    apath, audit = newest_audit(root)
    if not audit:
        print("ERROR no deployment audit — run bin/gb-deployment-audit.py first", file=sys.stderr)
        return 2

    g = audit.get("gates") or {}
    enabled = set(g.get("enabled_names") or [])
    # Every name we can resolve at all: enabled names plus the cumulative dictionary.
    cache = load(root / "state" / "gate-name-map.json") or {}
    known = enabled | set((cache.get("map") or {}).values())
    if not enabled:
        print("ERROR audit has no named gates — decoding broke; absence is not proof", file=sys.stderr)
        return 2

    rows = []
    for f in sorted(reg["features"], key=lambda f: f.get("rank", 999)):
        state, detail = classify(f, enabled, known)
        rows.append({**f, "state": state, "gate_detail": detail})

    if args.id:
        hit = next((r for r in rows if r["id"] == args.id), None)
        if not hit:
            print(f"no feature with id {args.id!r}", file=sys.stderr)
            return 2
        print(json.dumps(hit, indent=1) if args.json else
              f"{hit['title']}  [{hit['state']}]\n  gates: {', '.join(hit['gate_detail']) or 'none'}\n"
              f"  where: {hit['where']}\n  adoption: {hit['adoption']} — {hit['check']}\n"
              + (f"  note: {hit['note']}\n" if hit.get("note") else "")
              + "  evidence:\n" + "".join(f"    - {e}\n" for e in hit["evidence"]))
        return 0

    shown = [r for r in rows if not args.todo or (r["state"] in (ON, NOGATE) and r["adoption"] != "measured")]
    if args.json:
        print(json.dumps({"audit": apath.name, "app": (audit.get("app") or {}).get("version"),
                          "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                          "features": shown}, indent=1))
        return 0

    counts = {s: sum(1 for r in rows if r["state"] == s) for s in (ON, OFF, UNNAMED, NOGATE)}
    print(f"feature matrix — audit {apath.name}, client {(audit.get('app') or {}).get('version')}, "
          f"{len(enabled)} named-enabled gates of {g.get('total')}")
    print(f"  {counts[ON]} ON · {counts[OFF]} OFF · {counts[UNNAMED]} UNNAMED · {counts[NOGATE]} no-gate\n")
    for r in shown:
        mark = {ON: "+", OFF: "-", UNNAMED: "?", NOGATE: "·"}[r["state"]]
        print(f"{mark} [{r['state']:<7}] {r['title']}")
        print(f"      where: {r['where']}")
        print(f"      verify ({r['adoption']}): {r['check']}")
        if r.get("note"):
            print(f"      note: {r['note']}")
        if r["gate_detail"]:
            print(f"      gates: {', '.join(r['gate_detail'])}")
        print()
    if args.todo:
        print(f"{len(shown)} feature(s) available and not yet verified as adopted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
