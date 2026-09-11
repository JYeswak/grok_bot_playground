#!/usr/bin/env python3
"""gb-context-archive — keep the fleet's conversation history before the client throws it away.

Two measured ways this history disappears, one of them continuous:

  DELETION   a Bot is deleted (or rebuilt onto durable identity) and its replica is orphaned.
             Measured 2026-09-11: 1135 entries belonging to six deleted Bots.
  ROTATION   the desktop keeps at most ~200 entries per Bot — four replicas sat at EXACTLY 200,
             which is a cap, not a coincidence. Every turn past that silently drops the oldest.
             This one loses history on a healthy fleet, quietly, forever.

So the archive is not a backup of a backup: it is the only place older-than-200 turns will exist.

WHERE IT WRITES, AND WHY NOT INTO GIT. Transcripts contain client material — the CFS room alone
carries a locked rate sheet, a named client's terms, and inbox content. The bodies go to
`~/.local/state/grokbot-archive/` (outside the repo, gitignored by construction); the repo keeps
a MANIFEST with counts, hashes, names and lineage and no message text. That way the loop can
prove coverage without this repo becoming a copy of a customer's mailbox.

  gb-context-archive.py            # archive now, write the manifest
  gb-context-archive.py --json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import DEFAULT_SUPPORT, dated_children, load, unb32  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402

ARCHIVE_ROOT = pathlib.Path(os.path.expanduser("~/.local/state/grokbot-archive"))
REPLICA = "transcript.replicas."
CLIENT_CAP = (
    200  # measured: the desktop keeps this many entries per Bot, then drops the oldest
)


def lineage(root: pathlib.Path) -> dict[str, dict]:
    """source_uuid -> {name, new_uuid} from every rebuild manifest, so an orphaned replica can be
    named and pointed at its successor instead of being an anonymous blob."""
    out: dict[str, dict] = {}
    for m in dated_children(root / "rebuild", ".json"):
        for c in (load(m) or {}).get("created") or []:
            if c.get("source_uuid"):
                out[c["source_uuid"]] = {
                    "name": c.get("name"),
                    "successor": c.get("new_uuid"),
                }
    return out


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--support", default=DEFAULT_SUPPORT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pdir = pathlib.Path(args.support) / "sand-client-persistence"
    if not pdir.is_dir():
        print(f"no persistence dir at {pdir} — nothing to archive", file=sys.stderr)
        return 2

    stamp = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}"
    dest = ARCHIVE_ROOT / stamp
    dest.mkdir(parents=True, exist_ok=True)
    lin = lineage(root)
    inv = [
        f for f in dated_children(root / "inventory", ".json") if ".studio." in f.name
    ]
    live = (
        {
            b.get("uuid"): b.get("name")
            for b in ((load(inv[-1]) or {}).get("bots") or [])
        }
        if inv
        else {}
    )

    rows, total = [], 0
    for f in sorted(pdir.iterdir()):
        key = unb32(f.name) if f.suffix == ".blob" else ""
        if REPLICA not in key:
            continue
        uid = key.split(REPLICA)[-1]
        body = f.read_text(errors="replace")
        ents = (json.loads(body).get("value") or {}).get("entries") or []
        atomic_write_text((dest / f"{uid}.json"), body)
        total += len(ents)
        rows.append(
            {
                "uuid": uid,
                "entries": len(ents),
                # A replica AT the cap is the loud case: older turns are already gone from the client,
                # and every new turn drops one more. Only the archive keeps them after this.
                "at_client_cap": len(ents) >= CLIENT_CAP,
                "belonged_to": live.get(uid) or (lin.get(uid) or {}).get("name"),
                "status": "live"
                if uid in live
                else ("orphaned" if uid in lin else "unknown"),
                "successor_uuid": (lin.get(uid) or {}).get("successor"),
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
                "bytes": len(body.encode()),
            }
        )

    manifest = {
        "schema": "gb-context-archive/1",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        # The bodies live OUTSIDE the repo on purpose; only this manifest is committed.
        "archive_dir": str(dest),
        "client_cap": CLIENT_CAP,
        "replicas": len(rows),
        "entries_total": total,
        "at_cap": [r["uuid"] for r in rows if r["at_client_cap"]],
        "orphaned_entries": sum(
            r["entries"] for r in rows if r["status"] == "orphaned"
        ),
        "rows": sorted(rows, key=lambda r: -r["entries"]),
    }
    out = root / "archive" / f"{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(manifest, indent=1) + "\n")

    if args.json:
        print(json.dumps(manifest, indent=1))
        return 0
    print(f"archive {dest} · manifest {out.name}")
    print(
        f"  {len(rows)} replica(s), {total} entries, "
        f"{len(manifest['at_cap'])} at the {CLIENT_CAP}-entry client cap, "
        f"{manifest['orphaned_entries']} entries from deleted Bots"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
