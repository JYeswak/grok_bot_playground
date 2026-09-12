#!/usr/bin/env python3
"""gb-rebuild-fleet — create Bots from fleet-spec.json, reversibly, the way the product actually works.

THE MECHANISM, measured 2026-09-11 (do not "simplify" this back to one call):
  CreateGrokBotAgent alone registers a durable identity and NOTHING ELSE. The desktop never shows
  that Bot, on any machine, across restarts — no agent store is ever built for it. Ten Bots were
  created that way and had to be rolled back.
  CreateGrokBotAgentFromTemplate returns a blobGetUrl, the client materialises the agent store,
  and the Bot appears. It is the only creation path that produces a usable Bot.
So every Bot here is: instantiate from the seed template, then immediately UpdateGrokBotAgent with
the reviewed name / title / description / avatar. No seed text survives the second call.

The seed is declared in fleet-spec.json. It is currently a public marketplace template because the
API's own CreateGrokBotTemplate requires a hand-authored profile blob whose schema we have not
decoded ("template profile is not readable"). One UI action — Bot -> Share as template — makes the
client author a valid blob; swap that share_id into fleet-spec.json and the seed becomes ours.

What a rebuild carries and what it does NOT:
  carries      name, title, avatar, description
  does NOT     conversation history, learned memory, per-Bot skills, connector logins
  routines     NOT carried by the create, and NOT manual either: no create RPC exists, but a
               Bot asked in ONE chat turn schedules itself. `gb templates deploy <id> --apply`
               does both halves and CONFIRMS the routine by reading it back from the server.
               This line said "rebuild them in the UI" until 2026-09-12, and the same false
               sentence printed directly above a "ROUTINE CONFIRMED" line in one run's output
               — the tool contradicting itself on screen, seconds apart, in one terminal.
  DELETE       measured 2026-09-12: `DeleteGrokBotAgent` removes the Bot and NOT its routine
               record. After two rollbacks `ListGrokBotAgentAutomations` still returned 200
               with one automation per deleted uuid, while `ListGrokBotAgents` no longer
               carried the name. So "the manifest deletes exactly what that run created" is
               true of the BOT and false of the ROUTINE, and a routine read is never proof
               that its Bot exists. Whether an orphaned routine can still fire is UNKNOWN:
               nothing was observed either way and nothing is claimed either way.

  gb-rebuild-fleet.py --plan                 # what would be created
  gb-rebuild-fleet.py --apply [--only NAME]  # instantiate + shape; skips names already present
  gb-rebuild-fleet.py --rollback <manifest>  # delete exactly what that run created
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_text  # noqa: E402


# TRANSPORT IS A LIBRARY (gbrpc.py) since 2026-09-12. This used to dynamically import
# bin/gb-pull-inventory.py by path to borrow access_token/rpc — a library dependency
# wearing a producer's clothes, which made `gb dogfood audit` count this file as one
# more hand-roller and forced the python 3.9.6 sys.modules dance on every borrower.
def _load():
    """The transport, imported normally. Kept as a function so call sites are unchanged."""
    import gbrpc

    return gbrpc


def rpc_write(mod, token: str, method: str, body: dict):
    """The puller refuses non-read methods by contract. Writes go through here, where the
    authorisation is explicit and the method is named in the audit trail."""
    import urllib.error, urllib.request

    req = urllib.request.Request(
        f"https://{mod.HOST}/{mod.SERVICE}/{method}",
        data=json.dumps(body).encode(),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "connect-protocol-version": "1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode("utf-8", "replace") if e.fp else "")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default=None, help="rebuild just this source Bot name")
    ap.add_argument("--rollback", default=None, metavar="MANIFEST")
    ap.add_argument(
        "--spec",
        default=None,
        metavar="PATH",
        help=(
            "read the Bot list from PATH instead of fleet-spec.json. Added so "
            "`gb templates deploy` can instantiate ONE proposed template without editing the "
            "reviewed roster — the deploy mechanism stays this file, single-sourced, rather "
            "than being reimplemented against the RPCs a second time."
        ),
    )
    args = ap.parse_args()

    mod = _load()
    spec_path = pathlib.Path(args.spec) if args.spec else root / "fleet-spec.json"
    if not spec_path.is_file():
        raise SystemExit(
            f"{spec_path} is missing — the spec is reviewed text, not a derived file"
        )
    spec = json.loads(spec_path.read_text())

    if args.rollback:
        man = json.loads(pathlib.Path(args.rollback).read_text())
        token = mod.access_token(mod.SUPPORT)
        for row in man["created"]:
            st, r = rpc_write(
                mod, token, "DeleteGrokBotAgent", {"id": row["new_numeric_id"]}
            )
            print(
                f"delete {row['name']} ({row['new_numeric_id']}) -> {st} {str(r)[:80]}"
            )
        return 0

    rows = [b for b in spec["bots"] if not args.only or b["name"] == args.only]
    if not rows:
        raise SystemExit(f"no Bot named {args.only!r} in fleet-spec.json")

    if args.plan or not args.apply:
        print(
            f"{'source Bot':<22}{'title':<22}{'desc':>6}  first line of the new description"
        )
        for b in rows:
            first = b["description"].strip().splitlines()[0]
            print(
                f"{b['name'][:21]:<22}{str(b.get('title'))[:21]:<22}{len(b['description']):>6}  {first[:70]}"
            )
        print(
            f"\n{len(rows)} Bot(s) would be created on the box harness. Nothing is deleted or hidden by this tool."
        )
        return 0

    token = mod.access_token(mod.SUPPORT)
    seed = (spec.get("seed") or {}).get("share_id")
    if not seed:
        raise SystemExit(
            "fleet-spec.json has no seed.share_id — see this file's docstring for why one is required"
        )
    st, existing = mod.rpc(token, "ListGrokBotAgents", {})
    present = (
        {a["name"] for a in (existing.get("agents") or [])} if st == 200 else set()
    )
    if present:
        print(f"already present, skipping: {', '.join(sorted(present))}\n")

    stamp = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M%S}"
    manifest = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "spec_version": spec.get("version"),
        "seed_share_id": seed,
        "created": [],
    }
    for b in rows:
        if b["name"] in present:
            continue
        st, r = rpc_write(
            mod,
            token,
            "CreateGrokBotAgentFromTemplate",
            {"share_id": seed, "agent_id": str(uuid.uuid4())},
        )
        if st != 200:
            print(
                f"FAILED instantiate {b['name']}: {st} {str(r)[:180]}", file=sys.stderr
            )
            continue
        ag = r["agent"]
        st2, r2 = rpc_write(
            mod,
            token,
            "UpdateGrokBotAgent",
            {
                "id": ag["id"],
                "name": b["name"],
                "title": b.get("title", ""),
                "description": b["description"],
                "avatar_shape": b.get("avatar_shape", "hex"),
                "avatar_color": b.get("avatar_color", "gray"),
            },
        )
        if st2 != 200:
            # A materialised-but-unshaped Bot is worse than none: it carries the seed's identity.
            print(
                f"SHAPE FAILED {b['name']} (id {ag['id']}): {st2} {str(r2)[:160]} — rolling it back",
                file=sys.stderr,
            )
            rpc_write(mod, token, "DeleteGrokBotAgent", {"id": ag["id"]})
            continue
        manifest["created"].append(
            {
                "name": b["name"],
                "new_numeric_id": ag["id"],
                "new_uuid": ag["agentId"],
                "source_uuid": b.get("source_uuid"),
                "harness": ag["harness"],
            }
        )
        print(
            f"created {b['name']:<20} id={ag['id']:<9} desc={len(b['description'])} chars"
        )

    out = root / "rebuild" / f"{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(manifest, indent=1) + "\n")
    print(f"\nmanifest {out}")
    print(f"rollback with: bin/gb-rebuild-fleet.py --rollback {out}")
    print("NEXT: confirm the routine took — gb fleet routines --bot <name>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
