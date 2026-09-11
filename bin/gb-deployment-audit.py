#!/usr/bin/env python3
"""gb-deployment-audit — measure the Grok Bot deployment actually installed on this machine.

Oracle sources 2 and 3 (see AGENTS.md §ORACLE):
  2. the client's own capability surface — app version + the Statsig feature
     gates xAI has evaluated FOR THIS ACCOUNT (`sand-statsig-bootstrap.json`).
     Gate names are djb2-hashed in the payload; we recover them by hashing the
     string literals mined out of `app.asar`. A gate we cannot name is reported
     as undecoded, never as absent.
  3. the live roster — every Bot, its standing description, its last activity.

Reads only. Never prints a secret value: `sand-secrets.json` contributes key
NAMES and byte lengths and nothing else.

Usage:
  gb-deployment-audit.py [--out FILE] [--app PATH] [--support DIR] [--no-decode] [--json]
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import plistlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import load, unb32  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402

DEFAULT_APP = "/Applications/Grok Bot.app"
DEFAULT_SUPPORT = os.path.expanduser("~/Library/Application Support/Grok Bot")
PERSIST = "sand-client-persistence"
ROSTER_SUFFIX = ".roster.last-roster"
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{4,70}")


def djb2(s: str) -> str:
    h = 0
    for ch in s:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    return str(h)


def b32key(name: str) -> str:
    return base64.b32encode(name.encode()).decode().rstrip("=").lower() + ".blob"


def app_facts(app: pathlib.Path) -> dict:
    out = {
        "path": str(app),
        "present": app.is_dir(),
        "version": None,
        "bundle_id": None,
        "signed_by": None,
    }
    plist = app / "Contents" / "Info.plist"
    if plist.is_file():
        try:
            d = plistlib.loads(plist.read_bytes())
            out["version"] = d.get("CFBundleShortVersionString")
            out["bundle_id"] = d.get("CFBundleIdentifier")
        except Exception as e:
            out["error"] = f"plist: {e}"
    if out["present"]:
        try:
            r = subprocess.run(
                ["codesign", "-dv", "--verbose=2", str(app)],
                capture_output=True,
                text=True,
                timeout=20,
            )
            m = re.search(r"^Authority=(.+)$", r.stderr, re.M)
            out["signed_by"] = m.group(1) if m else None
        except Exception:
            pass
    return out


# What the hash->name dictionary was mined against. Bump when the id set widens, so every cached
# client version is re-mined instead of being skipped as "already done".
SCOPE = "gates+configs+layers"


def asar_text_members(app: pathlib.Path):
    """Yield (path, text) for every text-ish member of the app's asar archive."""
    asar = app / "Contents" / "Resources" / "app.asar"
    if not asar.is_file():
        return
    import struct

    with asar.open("rb") as fh:
        (_, _, _, hlen) = struct.unpack("<IIII", fh.read(16))
        header = json.loads(fh.read(hlen).decode("utf-8", "replace"))
        base = 16 + hlen

        def walk(node, prefix=""):
            for nm, meta in (node.get("files") or {}).items():
                path = f"{prefix}/{nm}"
                if "files" in meta:
                    yield from walk(meta, path)
                elif "offset" in meta:
                    yield path, int(meta["offset"]), int(meta.get("size", 0))

        for path, off, size in walk(header):
            if not path.endswith(
                (".js", ".cjs", ".mjs", ".json", ".txt", ".map", ".html", ".css")
            ):
                continue
            if size > 64 * 1024 * 1024:
                continue
            fh.seek(base + off)
            yield path, fh.read(size).decode("utf-8", "replace")


def mine_bundle(
    app: pathlib.Path, ids: set[str], mapping: dict, ambiguous: set
) -> None:
    """Fold every literal in the client bundle that hashes to a wanted id into `mapping`.

    Walks the asar and regexes each text member rather than running `strings` over the archive:
    reading members is complete and faster, where `strings -n 6` depends on its own buffer
    boundaries. (Measured 2026-09-11 — coverage was identical at 692/2763 either way. The ceiling
    is what this client references, not how we read it; kept for the speed and for not resting on
    a heuristic.)

    Ambiguity refuses rather than guesses. djb2 is 32-bit, so two distinct literals CAN land on
    one id, and first-wins would silently attach a wrong name to a real capability — the NE-6
    failure mode (a confident wrong answer costs more than a blank). Sole candidate wins; several
    candidates name nothing. Measured 2026-09-11: 0 collisions across 3435 ids, so this costs
    nothing today and stays correct when it stops being true.
    """
    for _member, text in asar_text_members(app):
        for tok in set(TOKEN_RE.findall(text)):
            h = djb2(tok)
            if h not in ids:
                continue
            if h in mapping and mapping[h] != tok:
                ambiguous.add(h)
            mapping.setdefault(h, tok)


def gate_name_map(
    app: pathlib.Path, cache: pathlib.Path, ids: set[str], history: pathlib.Path
) -> dict:
    """Cumulative hash -> name dictionary, over EVERY hashed id the payload carries.

    djb2 is version-independent, so a name learned from an older `app.asar` stays valid forever.
    Measured 2026-09-11: the 0.24.0 -> 0.47.0 upgrade "lost" 38 named-enabled gates that were all
    still TRUE in the new payload — the literals had simply vanished from the new bundle. A cache
    that is REPLACED per version manufactures that phantom loss; a cache that is UNIONED does not.
    Names already recorded in past audits are folded back in for the same reason.

    `ids` must be gates AND dynamic configs AND layers. Measured the same day: mining against the
    gate ids alone reported dynamic configs as 0/656 nameable, which read as "the vendor hashes
    those differently". It does not — they hash identically; the miner was simply never told to
    look for them. Widening the target recovered 232 config names and 1 layer from the bundle
    already on disk.
    """
    cached = load(cache) or {}
    mapping: dict[str, str] = dict(cached.get("map") or {})
    ambiguous: set[str] = set(cached.get("ambiguous") or [])
    # The cache remembers WHICH ids it was mined against, not just which client version. Widening
    # the target from gates-only to every hashed id would otherwise be a silent no-op forever:
    # 0.47.0 was already in `versions_mined`, so the better question never got asked.
    mined = (
        list(cached.get("versions_mined") or []) if cached.get("scope") == SCOPE else []
    )
    for old in sorted(history.glob("**/*.json")) if history.is_dir() else []:
        for name in ((load(old) or {}).get("gates") or {}).get("enabled_names") or []:
            mapping.setdefault(djb2(name), name)
    version = app_facts(app)["version"]
    if version not in mined:
        mine_bundle(app, ids, mapping, ambiguous)
        mined.append(version)
    cache.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        cache,
        json.dumps(
            {
                "scope": SCOPE,
                "versions_mined": mined,
                "ambiguous": sorted(ambiguous),
                "map": mapping,
            },
            indent=0,
        ),
    )
    # Only names for ids this payload actually evaluates are meaningful to the caller, and an
    # ambiguous id is not named at all.
    return {h: n for h, n in mapping.items() if h in ids and h not in ambiguous}


def tunables(configs: dict, layers: dict, mapping: dict) -> dict:
    """The parameter surface: dynamic configs and layers, with a per-entry value digest.

    Gates answer "can this account do X". Configs answer "with what numbers" — model routing,
    sampling rates, cooldowns, the five-minute automation floor. Their NAMES are hashed like a
    gate's, but their VALUES are plaintext, so a config nobody can name still tells you exactly
    what knob moved. Measured 2026-09-11: 568 of 656 carry a non-empty value dict, 511 distinct
    parameter keys across them.

    Values are stored as a sha1 digest, not inline: the full set is ~400KB of vendor experiment
    payload per tick and would bury the artifact it rides in. The digest is enough to detect the
    change and name the entry; `gb-capabilities.py --tunables` reads the live payload for values.
    """

    def digest(v) -> str:
        return hashlib.sha1(json.dumps(v, sort_keys=True).encode()).hexdigest()[:12]

    rows, named = {}, {}
    for kind, src in (("config", configs), ("layer", layers)):
        for entry in src.values():
            h = entry.get("name")
            if not h:
                continue
            val = entry.get("value") if isinstance(entry.get("value"), dict) else {}
            rows[h] = digest(val)
            if h in mapping:
                named[h] = mapping[h]
            if kind == "layer":
                rows[h] = f"L:{rows[h]}"
    with_values = sum(
        1
        for e in list(configs.values()) + list(layers.values())
        if isinstance(e.get("value"), dict) and e["value"]
    )
    keys = {
        k
        for e in list(configs.values()) + list(layers.values())
        if isinstance(e.get("value"), dict)
        for k in e["value"]
    }
    return {
        "configs": len(configs),
        "layers": len(layers),
        "with_values": with_values,
        "distinct_param_keys": len(keys),
        "named": len(named),
        "names": dict(sorted(named.items(), key=lambda kv: kv[1])),
        "digests": dict(sorted(rows.items())),
    }


def roster(support: pathlib.Path) -> list[dict]:
    pdir = support / PERSIST
    if not pdir.is_dir():
        return []
    hits = [
        f
        for f in pdir.iterdir()
        if f.suffix == ".blob" and unb32(f.name).endswith(ROSTER_SUFFIX)
    ]
    if not hits:
        return []
    blob = load(max(hits, key=lambda f: f.stat().st_mtime)) or {}
    rows = ((blob.get("value") or {}).get("rows")) or []
    now = dt.datetime.now(dt.timezone.utc).timestamp() * 1000
    out = []
    for r in rows:
        last = r.get("lastActivityAt") or r.get("updatedAt") or 0
        out.append(
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "is_group": bool(r.get("isGroup")),
                "description_chars": len(r.get("description") or ""),
                "created_at": r.get("createdAt"),
                "last_activity_at": last,
                "idle_days": round((now - last) / 86_400_000, 1) if last else None,
                "notifications_enabled": r.get("notificationsEnabled"),
                "unread_count": r.get("unreadCount"),
                "hidden": bool(r.get("isHiddenFromSidebar")),
                "member_ids": r.get("memberIds") or [],
            }
        )
    return sorted(
        out, key=lambda b: b["idle_days"] if b["idle_days"] is not None else 1e9
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    root = pathlib.Path(__file__).resolve().parents[1]
    ap.add_argument("--app", default=DEFAULT_APP)
    ap.add_argument("--support", default=DEFAULT_SUPPORT)
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--label",
        default=None,
        help="desktop label; writes to deployment/desktops/<stamp>.<label>.json instead of the host-of-record path",
    )
    ap.add_argument(
        "--no-decode", action="store_true", help="skip djb2 gate-name recovery (fast)"
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    app = pathlib.Path(args.app)
    support = pathlib.Path(args.support)
    stamp = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}"
    if args.out:
        out_path = pathlib.Path(args.out)
    elif args.label:
        out_path = root / "deployment" / "desktops" / f"{stamp}.{args.label}.json"
    else:
        out_path = root / "deployment" / f"{stamp}.json"

    audit: dict = {
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "host": os.uname().nodename,
        "support_dir": str(support),
        "support_present": support.is_dir(),
        "app": app_facts(app),
        "update_marker": load(support / "sand-update-apply-marker.json"),
        "session_marker": load(support / "sand-session-marker.json"),
        "secret_keys": sorted((load(support / "sand-secrets.json") or {}).keys()),
        "bots": roster(support),
        "gates": {
            "total": 0,
            "enabled": 0,
            "decoded": 0,
            "enabled_names": [],
            "undecoded_enabled": 0,
        },
        "account": {},
    }

    boot = load(support / "sand-statsig-bootstrap.json")
    if boot and isinstance(boot.get("config"), str):
        try:
            cfg = json.loads(boot["config"])
        except Exception:
            cfg = {}
        gates = cfg.get("feature_gates") or {}
        configs = cfg.get("dynamic_configs") or {}
        layers = cfg.get("layer_configs") or {}
        # Every hashed id in the payload, not just the gates. Configs and layers hash the same way
        # and their names live in the same bundle; asking only about gates left 656 configs
        # reported as unnameable when 232 of them were sitting right there.
        ids = (
            set(gates)
            | {v.get("name") for v in configs.values()}
            | {v.get("name") for v in layers.values()}
        )
        ids.discard(None)
        enabled_ids = {k for k, v in gates.items() if v.get("value") is True}
        mapping = (
            {}
            if args.no_decode
            else gate_name_map(
                app, root / "state" / "gate-name-map.json", ids, root / "deployment"
            )
        )
        named = sorted(mapping[k] for k in enabled_ids if k in mapping)
        audit["gates"] = {
            "total": len(gates),
            "enabled": len(enabled_ids),
            "decoded": len({h for h in mapping if h in gates}),
            # The hashed ids are the exact entitlement set. Names come and go with how much of
            # app.asar we can mine, so a name-only diff conflates "xAI enabled something" with
            # "we got better at decoding". Store both; g6 prefers ids.
            "enabled_ids": sorted(enabled_ids),
            "enabled_names": named,
            "undecoded_enabled": len(enabled_ids) - len(named),
            "fetched_at_ms": boot.get("fetchedAtMs"),
            "hash_used": cfg.get("hash_used"),
        }
        audit["tunables"] = tunables(configs, layers, mapping)
        user = cfg.get("user") or {}
        custom = user.get("custom") or {}
        audit["account"] = {
            "app_version_seen_by_backend": user.get("appVersion"),
            "membership": custom.get("stripeMembershipStatus"),
            "is_enterprise_user": custom.get("isEnterpriseUser"),
            "is_enterprise_trial_user": custom.get("isEnterpriseTrialUser"),
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out_path, json.dumps(audit, indent=1) + "\n")
    if args.json:
        print(
            json.dumps(
                {
                    "audit": str(out_path),
                    "bots": len(audit["bots"]),
                    "gates_enabled": audit["gates"]["enabled"],
                }
            )
        )
    else:
        a = audit["app"]
        print(
            f"audit {out_path}: app {a['version']} ({a['bundle_id']}), {len(audit['bots'])} bots, "
            f"{audit['gates']['enabled']}/{audit['gates']['total']} gates enabled "
            f"({audit['gates']['decoded']} names recovered)"
        )
    return 0 if audit["support_present"] and audit["app"]["present"] else 2


if __name__ == "__main__":
    sys.exit(main())
