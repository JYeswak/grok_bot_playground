#!/usr/bin/env python3
"""gb-whatchanged — exactly what moved since the last tick, across every axis this repo measures.

One command, six axes, each diffed between the two newest artifacts of its kind:

  client       version, staged update
  capabilities feature gates, on the exact hashed-id basis (never names — NE-3)
  tunables     dynamic-config and layer VALUE digests
  docs         per-page sha256 across both official trees, plus the practitioner index
  market       the plugin catalog, the Bot marketplace, and MY installs' pinned git refs
  fleet        Bots, their skills, and their routines

The gates say whether a change was REVIEWED. This says what the change WAS. They are different
jobs and this one has no verdict: it prints, it never blocks.

  gb-whatchanged.py            # since the previous artifact of each kind
  gb-whatchanged.py --json
  gb-whatchanged.py --markdown # write findings-ready markdown to stdout
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children, load  # noqa: E402


def pair(rows: list[pathlib.Path]) -> tuple[dict | None, dict | None, str, str]:
    """The two newest artifacts of a kind, as (previous, current, prev_name, cur_name)."""
    if not rows:
        return None, None, "", ""
    cur = load(rows[-1])
    prev = load(rows[-2]) if len(rows) > 1 else None
    return prev, cur, (rows[-2].name if len(rows) > 1 else ""), rows[-1].name


def delta(prev: dict, cur: dict, key) -> tuple[list, list, list]:
    """(added, removed, changed) over a {id: fingerprint} view of two artifacts."""
    a, b = key(prev), key(cur)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    return added, removed, changed


def section(title: str, lines: list[str], *, comparable: bool = True) -> list[str]:
    """`comparable=False` means there is no previous artifact of this kind. That is NOT "no
    change" — it is "nothing to compare", and printing the first as the second is how a loop
    starts reporting a clean week it never measured."""
    if not comparable:
        body = ["- _first artifact of this kind — nothing to compare against yet_"]
    else:
        body = lines or ["- no change"]
    return [f"## {title}", ""] + body + [""]


def build(root: pathlib.Path) -> dict:
    out: dict = {}

    # --- client + capabilities + tunables: all three live in the deployment audit --------------
    prev_a, cur_a, pn, cn = pair(dated_children(root / "deployment", ".json"))
    out["audit"] = {"previous": pn, "current": cn}
    if cur_a:
        out["client"] = {
            "version": (cur_a.get("app") or {}).get("version"),
            "changed_from": ((prev_a or {}).get("app") or {}).get("version") if prev_a else None,
            "staged_update": (cur_a.get("update_marker") or {}).get("targetVersion"),
        }
        if prev_a:
            names = {**((prev_a.get("tunables") or {}).get("names") or {}),
                     **((cur_a.get("tunables") or {}).get("names") or {})}
            ca = set((cur_a.get("gates") or {}).get("enabled_ids") or [])
            pa = set((prev_a.get("gates") or {}).get("enabled_ids") or [])
            cur_names = {n: 1 for n in (cur_a.get("gates") or {}).get("enabled_names") or []}
            out["capabilities"] = {
                "gained": sorted(names.get(i, f"<unnamed {i}>") for i in ca - pa),
                "lost": sorted(names.get(i, f"<unnamed {i}>") for i in pa - ca),
                "enabled_total": len(ca), "named": len(cur_names),
            }
            add, rem, chg = delta(prev_a, cur_a, lambda d: (d.get("tunables") or {}).get("digests") or {})
            out["tunables"] = {"added": [names.get(k, k) for k in add],
                               "removed": [names.get(k, k) for k in rem],
                               "retuned": [names.get(k, k) for k in chg]}

    # --- docs: per-page hashes across both official trees ---------------------------------------
    snaps = dated_children(root / "surface")
    if snaps:
        cur_s = load(snaps[-1] / "manifest.json")
        prev_s = load(snaps[-2] / "manifest.json") if len(snaps) > 1 else None
        out["docs"] = {"snapshot": snaps[-1].name,
                       "previous": snaps[-2].name if len(snaps) > 1 else None}
        if cur_s and prev_s:
            def view(m):
                return ({p["slug"]: p.get("sha256") for p in (m.get("pages") or [])}
                        | {e["label"]: e.get("sha256") for e in (m.get("extras") or [])})
            add, rem, chg = delta(prev_s, cur_s, view)
            out["docs"].update({"added": add, "removed": rem, "changed": chg})

    # --- market: catalog + my pinned installs -----------------------------------------------------
    prev_m, cur_m, mp, mc = pair(dated_children(root / "market", ".json"))
    if cur_m:
        out["market"] = {"previous": mp, "current": mc,
                         "plugins_total": (cur_m.get("plugins") or {}).get("total"),
                         "listings_total": (cur_m.get("bot_marketplace") or {}).get("total")}
        if prev_m:
            def pv(d):
                return {r["name"]: (r.get("git_ref"), r.get("status"), r.get("updated_at"))
                        for r in ((d.get("plugins") or {}).get("rows") or [])}

            def lv(d):
                return {r["slug"]: (r.get("name"), r.get("updated_at_ms"))
                        for r in ((d.get("bot_marketplace") or {}).get("rows") or [])}

            def iv(d):
                return {r["name"]: r.get("catalog_git_ref") for r in (d.get("installed") or [])}

            pa, pr, pc = delta(prev_m, cur_m, pv)
            la, lr, lc = delta(prev_m, cur_m, lv)
            _ia, _ir, ic = delta(prev_m, cur_m, iv)
            out["market"].update({
                "plugins_added": pa, "plugins_removed": pr, "plugins_updated": pc,
                "listings_added": la, "listings_removed": lr, "listings_updated": lc,
                # The one that can change what a Bot actually runs without anyone touching it.
                "installed_plugins_updated": ic,
            })

    # --- fleet: Bots, their skills, their routines -------------------------------------------------
    prev_i, cur_i, ip, ic_ = pair([f for f in dated_children(root / "inventory", ".json")
                                   if ".studio." in f.name])
    if cur_i:
        out["fleet"] = {"previous": ip, "current": ic_}
        if prev_i:
            def bots(d):
                return {b["name"]: (len(b.get("skills") or []), len(b.get("routines") or []))
                        for b in (d.get("bots") or [])}
            a, r, c = delta(prev_i, cur_i, bots)
            out["fleet"].update({"bots_added": a, "bots_removed": r, "bots_changed": c})
    return out


def render(d: dict) -> str:
    L = [f"# What changed — audit {d.get('audit', {}).get('current', '?')}", ""]
    c = d.get("client") or {}
    L += section("Client", [f"- version **{c.get('version')}**"
                            + (f" (was {c['changed_from']})" if c.get("changed_from") and c["changed_from"] != c.get("version") else "")
                            + (f" · staged: {c['staged_update']}" if c.get("staged_update") else "")])
    cap = d.get("capabilities") or {}
    L += section("Capabilities (exact, ids basis)",
                 [f"- **gained {len(cap.get('gained', []))}**: {', '.join(cap.get('gained') or []) or '—'}",
                  f"- **lost {len(cap.get('lost', []))}**: {', '.join(cap.get('lost') or []) or '—'}"],
                 comparable=bool(cap))
    tun = d.get("tunables") or {}
    L += section("Tunables",
                 [f"- retuned {len(tun.get('retuned', []))}: {', '.join(tun.get('retuned') or [])[:400] or '—'}",
                  f"- added {len(tun.get('added', []))} · removed {len(tun.get('removed', []))}"],
                 comparable=bool(tun))
    doc = d.get("docs") or {}
    L += section("Docs (both official trees + practitioner index)",
                 [f"- changed: {', '.join(doc.get('changed') or []) or '—'}",
                  f"- added: {', '.join(doc.get('added') or []) or '—'}",
                  f"- removed: {', '.join(doc.get('removed') or []) or '—'}"],
                 comparable="changed" in doc)
    mk = d.get("market") or {}
    L += section("Market",
                 [f"- plugin catalog **{mk.get('plugins_total')}** · Bot listings **{mk.get('listings_total')}**",
                  f"- plugins added {len(mk.get('plugins_added', []))}: {', '.join((mk.get('plugins_added') or [])[:12]) or '—'}",
                  f"- plugins updated {len(mk.get('plugins_updated', []))}: {', '.join((mk.get('plugins_updated') or [])[:12]) or '—'}",
                  f"- listings added {len(mk.get('listings_added', []))}: {', '.join((mk.get('listings_added') or [])[:12]) or '—'}",
                  f"- **MY installed plugins updated {len(mk.get('installed_plugins_updated', []))}**: "
                  f"{', '.join(mk.get('installed_plugins_updated') or []) or '—'}"]
                 if "plugins_added" in mk else
                 [f"- plugin catalog **{mk.get('plugins_total')}** · Bot listings **{mk.get('listings_total')}** (first snapshot — nothing to diff yet)"])
    fl = d.get("fleet") or {}
    L += section("Fleet",
                 [f"- Bots added {len(fl.get('bots_added', []))}: {', '.join(fl.get('bots_added') or []) or '—'}",
                  f"- Bots changed (skills/routines) {len(fl.get('bots_changed', []))}: {', '.join(fl.get('bots_changed') or []) or '—'}",
                  f"- Bots removed {len(fl.get('bots_removed', []))}: {', '.join(fl.get('bots_removed') or []) or '—'}"],
                 comparable="bots_added" in fl)
    return "\n".join(L)


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()
    d = build(root)
    print(json.dumps(d, indent=1) if args.json else render(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
