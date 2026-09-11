#!/usr/bin/env python3
"""gb-utilization — does the fleet actually do anything, and what did it cost.

Everything else in this repo measures the INSTRUMENT: what the vendor changed, what the account
is entitled to, what the parameters are set to. None of it answers the only question an owner
eventually asks — is this thing working for me?

Measured per Bot, from the desktop's own transcript replicas plus the recorded inventory:

  entries        conversation turns in that Bot's replica (send-message / message / event)
  last_active    newest timestamp in it
  routines       scheduled work it owns, and whether any has ever run
  skills         reusable instructions attached

And one account-level number that tends to be the real verdict: percent of the weekly allowance
consumed. A roster of ten specialists running at 2.7% is not a fleet, it is a standing bill.

ORPHANED HISTORY. A replica whose uuid is not on the current roster is conversation history for
a Bot that no longer exists. It is not garbage: it is the accumulated working context of a
deleted teammate, still on disk, and it is the honest measure of what a rebuild cost. Measured
2026-09-11 after the durable-identity rebuild: 1135 orphaned entries against 43 live ones.

  gb-utilization.py            # write utilization/<stamp>.json + print the table
  gb-utilization.py --json
"""

from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import (  # noqa: E402
    dated_children,
    load,
    platform_refusal,
    platform_support,
    unb32,
)
from gbtypes import atomic_write_text  # noqa: E402

PERSIST = "sand-client-persistence"
REPLICA = "transcript.replicas."


def replicas(support: pathlib.Path) -> dict[str, dict]:
    """uuid -> {entries, kinds, last_ms} from the desktop's own transcript store."""
    out: dict[str, dict] = {}
    pdir = support / PERSIST
    if not pdir.is_dir():
        return out
    for f in pdir.iterdir():
        key = unb32(f.name) if f.suffix == ".blob" else ""
        if REPLICA not in key:
            continue
        try:
            ents = ((load(f) or {}).get("value") or {}).get("entries") or []
        except Exception:
            continue
        out[key.split(REPLICA)[-1]] = {
            "entries": len(ents),
            "kinds": dict(collections.Counter(e.get("kind") for e in ents)),
            "last_ms": max((e.get("timestampMs") or 0) for e in ents) if ents else 0,
        }
    return out


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    plat = platform_support()
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--support", default=str(plat.support_dir) if plat.support_dir else None
    )
    args = ap.parse_args()
    # No desktop client on this platform means no transcript replicas to count. Reading the
    # macOS path anyway would report every Bot idle, which is a finding this tool invented.
    if args.support is None:
        print(platform_refusal("gb-utilization", plat), file=sys.stderr)
        return 3

    invs = [
        f for f in dated_children(root / "inventory", ".json") if ".studio." in f.name
    ]
    inv = load(invs[-1]) if invs else {}
    audits = dated_children(root / "deployment", ".json")
    audit = load(audits[-1]) if audits else {}
    reps = replicas(pathlib.Path(args.support))

    by_uuid = {b.get("uuid"): b for b in (inv.get("bots") or [])}
    now_ms = dt.datetime.now(dt.timezone.utc).timestamp() * 1000
    bots, live_ids = [], set(by_uuid)
    for b in inv.get("bots") or []:
        rep = reps.get(b.get("uuid")) or {}
        routines = b.get("routines") or []
        ran = sum(1 for r in routines if r.get("recent_runs"))
        last_ms = rep.get("last_ms") or 0
        bots.append(
            {
                "name": b.get("name"),
                "entries": rep.get("entries", 0),
                "last_active_days": round((now_ms - last_ms) / 86_400_000, 1)
                if last_ms
                else None,
                "routines": len(routines),
                "routines_with_runs": ran,
                "skills": len(b.get("skills") or []),
                # The shape this repo cares about: it can act on your behalf, and nothing asks it to.
                "idle_credentialed": len(routines) == 0 and rep.get("entries", 0) <= 2,
            }
        )
    orphaned = {k: v for k, v in reps.items() if k not in live_ids}

    usage = inv.get("usage") or {}
    doc = {
        "schema": "gb-utilization/1",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "bots": sorted(bots, key=lambda b: -b["entries"]),
        "live_entries": sum(b["entries"] for b in bots),
        "orphaned_entries": sum(v["entries"] for v in orphaned.values()),
        "orphaned_replicas": len(orphaned),
        "idle_credentialed": [b["name"] for b in bots if b["idle_credentialed"]],
        "routines_total": sum(b["routines"] for b in bots),
        "usage_percent": usage.get("usage_percent"),
        "memory_shards_with_content": sum(
            1
            for m in ((inv.get("account_surface") or {}).get("memory_shards") or [])
            if m.get("has_content")
        ),
        "bot_count": len(bots) or len(audit.get("bots") or []),
    }
    out = (
        root / "utilization" / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(doc, indent=1) + "\n")

    if args.json:
        print(json.dumps(doc, indent=1))
        return 0
    print(f"utilization {out.name}")
    print(f"{'Bot':<20}{'entries':>8}{'last_d':>8}{'routines':>10}{'ran':>5}")
    for b in doc["bots"]:
        print(
            f"{b['name']:<20}{b['entries']:>8}"
            f"{(b['last_active_days'] if b['last_active_days'] is not None else '-'):>8}"
            f"{b['routines']:>10}{b['routines_with_runs']:>5}"
        )
    print(
        f"\nlive entries {doc['live_entries']} · ORPHANED {doc['orphaned_entries']} across "
        f"{doc['orphaned_replicas']} replica(s) of deleted Bots"
    )
    print(
        f"{len(doc['idle_credentialed'])}/{doc['bot_count']} Bot(s) hold credentials with no "
        f"routine and no conversation: {', '.join(doc['idle_credentialed']) or '—'}"
    )
    print(
        f"weekly allowance used: {doc['usage_percent']}% · memory shards with content: "
        f"{doc['memory_shards_with_content']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
