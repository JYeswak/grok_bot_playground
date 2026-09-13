#!/usr/bin/env python3
"""gb-plugins — every installable plugin, this account's slice, a create-plugin brief.

The catalog already lives in `market/<stamp>.json` (`gb-market-snapshot.py`,
DashboardService/ListMarketplacePlugins). This file does not re-fetch it.
It makes the universe a verb an agent can ask without opening JSON.

  gb plugins universe          catalog totals + this account + first create
  gb plugins catalog           every row (name kind skills mcp git_ref)
  gb plugins installed         approved installs joined to catalog kind
  gb plugins new               names in newest snapshot not in the previous
  gb plugins show NAME         one catalog row + installed?
  gb plugins kinds             connector / skill-pack / empty counts
  gb plugins create-brief      paste-ready agent trigger (no vendor write)
  gb plugins refresh           run gb-market-snapshot.py (network)

There is no connector-install API (probed 2026-09-11). This command never
enables a plugin. HUMAN: Settings → Plugins. Create-plugin is a skill-pack
(0 MCP) already APPROVED on this account; attach it to the Bot that scaffolds.

stdlib only. Offline except `refresh`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children  # noqa: E402
from gbtypes import main as gbmain  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"

# First plugin to create is NOT a new empty scaffold. Catalog domain zeros
# (MARKET-GAPS.md): math/stats 0, OCR 0, retrieval 0. Those skills already
# sit in plugin/. Demand #1 YouTube has templates, no catalog plugin — do
# not invent a YouTube connector. LinkedIn send is refused on purpose.
FIRST_CREATE = {
    "name": "grok-bot-playground",
    "mode": "publish-existing",
    "path": "plugin/",
    "why": (
        "Catalog domain zeros (math/statistics, OCR/docs, search/retrieval) "
        "are already implemented as skills in plugin/. Validate and submit "
        "that tree; do not scaffold a duplicate."
    ),
    "next": "youtube-digest-ability",
    "next_why": (
        "Demand rank #1 is YouTube (13 builders, 0 catalog plugin). An "
        "ability pack on the cloud computer (transcript-mcp + existing "
        "youtube-brief/youtube-digest skills) is honest. A fake YouTube "
        "connector is not."
    ),
    "never": [
        "linkedin-send",
        "email-warmup",
        "localhost-mcp",
        "firecrawl-agpl-fork",
        "create-plugin-copy",
    ],
}


def newest_market(root: pathlib.Path) -> Tuple[Optional[pathlib.Path], Optional[dict]]:
    rows = dated_children(root / "market", ".json")
    if not rows:
        return None, None
    path = rows[-1]
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return path, None
    return path, doc


def previous_market(
    root: pathlib.Path,
) -> Tuple[Optional[pathlib.Path], Optional[dict]]:
    rows = dated_children(root / "market", ".json")
    if len(rows) < 2:
        return None, None
    path = rows[-2]
    try:
        return path, json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return path, None


def newest_inventory_plugins(root: pathlib.Path) -> List[str]:
    """Fresher than market.installed when a pull ran after the snapshot."""
    names: List[str] = []
    for path in reversed(dated_children(root / "inventory", ".json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        plugins = (
            (doc.get("data") or {}).get("plugins")
            if isinstance(doc.get("data"), dict)
            else doc.get("plugins")
        )
        if not plugins:
            continue
        for p in plugins:
            if isinstance(p, dict) and p.get("name"):
                names.append(str(p["name"]))
        if names:
            return names
    return []


def catalog_rows(doc: dict) -> List[dict]:
    return list(((doc.get("plugins") or {}).get("rows")) or [])


def by_name(rows: List[dict]) -> Dict[str, dict]:
    return {str(r.get("name")): r for r in rows if r.get("name")}


def catalog_coverage(rows: List[dict]) -> Dict[str, Any]:
    """Connector coverage, not listing size. Convention 29 (skill-pack != MCP):
    a catalog row only counts as a connector when it carries mcp_servers>0
    AND a verified publisher. Hollow rows (mcp==0) are skill-packs by design,
    never broken connectors."""
    hollow = [r for r in rows if not (r.get("mcp_servers") or 0)]
    hollow_by_kind: Dict[str, int] = {}
    for r in hollow:
        k = str(r.get("kind"))
        hollow_by_kind[k] = hollow_by_kind.get(k, 0) + 1
    unverified = [r for r in rows if r.get("verified_publisher") is not True]
    connectors = [
        r
        for r in rows
        if (r.get("mcp_servers") or 0) > 0 and r.get("verified_publisher") is True
    ]
    return {
        "total": len(rows),
        "hollow": len(hollow),
        "hollow_by_kind": hollow_by_kind,
        "unverified": len(unverified),
        "connectors_mcp_verified": len(connectors),
    }


def installed_names(doc: dict, root: pathlib.Path) -> List[str]:
    inv = newest_inventory_plugins(root)
    if inv:
        return inv
    return [str(x.get("name")) for x in (doc.get("installed") or []) if x.get("name")]


def emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def cmd_universe(path: pathlib.Path, doc: dict, root: pathlib.Path) -> int:
    pl = doc.get("plugins") or {}
    inst = installed_names(doc, root)
    index = by_name(catalog_rows(doc))
    kinds = pl.get("by_kind") or {}
    emit("PLUGIN UNIVERSE  {p}  n={n}".format(p=path.name, n=pl.get("total")))
    emit(
        "  kinds  "
        + ", ".join(
            "{v} {k}".format(v=kinds[k], k=k)
            for k in sorted(kinds, key=lambda x: -int(kinds[x] or 0))
        )
    )
    cov = catalog_coverage(catalog_rows(doc))
    emit(
        "  coverage  {c} connectors (mcp>0+verified) of {t}; "
        "{h} hollow ({hb}); {u} unverified — cite {c}, never {t}".format(
            c=cov["connectors_mcp_verified"],
            t=cov["total"],
            h=cov["hollow"],
            hb=", ".join(
                "{v} {k}".format(v=cov["hollow_by_kind"][k], k=k)
                for k in sorted(
                    cov["hollow_by_kind"], key=lambda x: -cov["hollow_by_kind"][x]
                )
            ),
            u=cov["unverified"],
        )
    )
    emit("  installed  {n}  {names}".format(n=len(inst), names=" ".join(inst)))
    missing = [n for n in inst if n not in index]
    if missing:
        emit("  installed-not-in-snapshot  " + " ".join(missing))
    prev_path, prev = previous_market(root)
    if prev:
        old = {r.get("name") for r in catalog_rows(prev)}
        new = sorted(n for n in index if n not in old)
        emit(
            "  new-since {p}  {n}  {names}".format(
                p=prev_path.name if prev_path else "?",
                n=len(new),
                names=" ".join(new[:20]) + (" …" if len(new) > 20 else ""),
            )
        )
    else:
        emit("  new-since  (need a second market/ snapshot)")
    emit(
        "  first-create  {n}  ({m})".format(
            n=FIRST_CREATE["name"], m=FIRST_CREATE["mode"]
        )
    )
    emit("  why  " + FIRST_CREATE["why"])
    emit("  next  {n} — {w}".format(n=FIRST_CREATE["next"], w=FIRST_CREATE["next_why"]))
    emit("  never  " + ", ".join(FIRST_CREATE["never"]))
    emit("  deploy  HUMAN Settings → Plugins. No install RPC.")
    emit(
        "  submit  gb-plugin-validate.py then gb-marketplace-dryrun.sh; Joshua opens the PR."
    )
    return 0


def cmd_catalog(doc: dict, kind: Optional[str], query: Optional[str]) -> int:
    rows = catalog_rows(doc)
    q = (query or "").lower()
    for r in rows:
        if kind and r.get("kind") != kind:
            continue
        blob = " ".join(
            str(r.get(k) or "") for k in ("name", "publisher", "kind", "git_ref")
        ).lower()
        if q and q not in blob:
            continue
        emit(
            "{name}\t{kind}\t{sk}\t{mcp}\t{pub}\t{ref}".format(
                name=r.get("name"),
                kind=r.get("kind"),
                sk=r.get("skills"),
                mcp=r.get("mcp_servers"),
                pub=r.get("publisher"),
                ref=(r.get("git_ref") or "")[:12],
            )
        )
    return 0


def cmd_installed(doc: dict, root: pathlib.Path) -> int:
    index = by_name(catalog_rows(doc))
    for name in installed_names(doc, root):
        r = index.get(name) or {}
        emit(
            "{name}\t{kind}\t{sk}\t{mcp}\t{status}".format(
                name=name,
                kind=r.get("kind") or "not-in-snapshot",
                sk=r.get("skills", "-"),
                mcp=r.get("mcp_servers", "-"),
                status=r.get("status") or "unknown",
            )
        )
    return 0


def cmd_new(root: pathlib.Path, doc: dict) -> int:
    prev_path, prev = previous_market(root)
    if not prev:
        emit("ERROR need two market/ snapshots to diff")
        return 2
    old = {r.get("name") for r in catalog_rows(prev)}
    new_rows = [r for r in catalog_rows(doc) if r.get("name") not in old]
    emit(
        "new since {p}: {n}".format(
            p=prev_path.name if prev_path else "?", n=len(new_rows)
        )
    )
    for r in new_rows:
        emit(
            "{name}\t{kind}\t{sk}\t{mcp}\t{pub}".format(
                name=r.get("name"),
                kind=r.get("kind"),
                sk=r.get("skills"),
                mcp=r.get("mcp_servers"),
                pub=r.get("publisher"),
            )
        )
    return 0


def cmd_show(doc: dict, root: pathlib.Path, name: str) -> int:
    r = by_name(catalog_rows(doc)).get(name)
    if not r:
        emit("ERROR {n} not in newest catalog".format(n=name))
        return 1
    inst = name in installed_names(doc, root)
    emit(json.dumps({**r, "installed_on_account": inst}, indent=2, sort_keys=True))
    return 0


def cmd_kinds(doc: dict) -> int:
    kinds = (doc.get("plugins") or {}).get("by_kind") or {}
    for k, v in sorted(kinds.items(), key=lambda kv: -int(kv[1] or 0)):
        emit("{k}\t{v}".format(k=k, v=v))
    return 0


def cmd_create_brief(name: Optional[str], job: Optional[str]) -> int:
    target = name or FIRST_CREATE["name"]
    job_line = job or FIRST_CREATE["why"]
    emit(
        "\n".join(
            [
                "# CREATE-PLUGIN BRIEF — paste to one agent. Do not send mail. Do not open a PR.",
                "",
                "Repo: {root}".format(root=ROOT),
                "Target plugin: {t}".format(t=target),
                "Mode: {m}".format(
                    m=FIRST_CREATE["mode"]
                    if target == FIRST_CREATE["name"]
                    else "scaffold-then-validate"
                ),
                "Job: {j}".format(j=job_line),
                "",
                "Universe (read, don't guess):",
                "  python3 bin/gb plugins universe",
                "  python3 bin/gb plugins show {t}".format(t=target),
                "",
                "create-plugin (Cursor, already APPROVED on this account, 0 MCP):",
                "  skills: create-plugin-scaffold, review-plugin-submission",
                "  command: /create-plugin",
                "  Attach that plugin to THIS Bot before scaffolding. Account-approved ≠ attached.",
                "  Cursor `rules/` do not run on Grok Bot (enum is COMMAND/MCP/SKILL/SUBAGENT).",
                "",
                "If target is grok-bot-playground: DO NOT scaffold. Work in plugin/.",
                "  python3 bin/gb-plugin-validate.py --path plugin",
                "  bash bin/gb-marketplace-dryrun.sh",
                "  Stop. Joshua opens the public repo / PR. This agent does not.",
                "",
                "If scaffolding something else:",
                "  1. Six-part SKILL.md, name==directory, when-clause, Boundaries/SEND-LOCK.",
                "  2. No secrets. variables names only as ${{VAR}}.",
                "  3. No localhost MCP. Cloud computer cannot reach Studio.",
                "  4. Prefer plugin if present, else computer; never invent a connector.",
                "  5. bash ~/.agents/skills/grokbot-skill-authoring/scripts/check-skill-shape.sh plugin/skills/<id>",
                "  6. python3 bin/gb-plugin-validate.py --path plugin",
                "",
                "Never: {n}".format(n=", ".join(FIRST_CREATE["never"])),
                "Never paste FIRECRAWL_API_KEY or any token.",
                "Firecrawl is a skill-pack; CLI on the cloud computer is the working path (NE-25).",
                "",
                "Done when: validator submittable, dry-run green if submitting, and a",
                "one-line receipt of what changed. No new markdown essay.",
            ]
        )
    )
    return 0


def cmd_refresh() -> int:
    snap = BIN / "gb-market-snapshot.py"
    if not snap.is_file():
        emit("ERROR missing bin/gb-market-snapshot.py")
        return 3
    return subprocess.call([sys.executable, str(snap)])


def _selftest_inline() -> int:
    # plugin_kind lives in gb-market-snapshot; copy the four-way table here so
    # this file does not import a producer. Same function, asserted.
    def kind(skills: int, mcp: int) -> str:
        if mcp and skills:
            return "connector+skills"
        if mcp:
            return "connector"
        if skills:
            return "skill-pack"
        return "empty"

    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, good: bool, detail: str = "") -> None:
        legs.append((name, good, detail))

    leg("kind-empty", kind(0, 0) == "empty")
    leg("kind-skill-pack", kind(2, 0) == "skill-pack")
    leg("kind-connector", kind(0, 1) == "connector")
    leg("kind-both", kind(1, 1) == "connector+skills")
    demo = [
        {
            "name": "a",
            "kind": "connector",
            "mcp_servers": 2,
            "verified_publisher": True,
        },
        {
            "name": "b",
            "kind": "connector",
            "mcp_servers": 1,
            "verified_publisher": None,
        },
        {
            "name": "c",
            "kind": "skill-pack",
            "mcp_servers": 0,
            "verified_publisher": True,
        },
        {"name": "d", "kind": "empty", "mcp_servers": 0, "verified_publisher": None},
    ]
    cov = catalog_coverage(demo)
    leg(
        "coverage-synthetic",
        cov
        == {
            "total": 4,
            "hollow": 2,
            "hollow_by_kind": {"skill-pack": 1, "empty": 1},
            "unverified": 2,
            "connectors_mcp_verified": 1,
        },
        str(cov),
    )
    path, doc = newest_market(ROOT)
    leg("market-present", path is not None and isinstance(doc, dict), str(path))
    if doc:
        rows = catalog_rows(doc)
        fc = by_name(rows).get("firecrawl") or {}
        cp = by_name(rows).get("create-plugin") or {}
        leg(
            "firecrawl-skill-pack-0mcp",
            fc.get("kind") == "skill-pack" and fc.get("mcp_servers") == 0,
            str(fc.get("kind")),
        )
        leg(
            "create-plugin-skill-pack-0mcp",
            cp.get("kind") == "skill-pack" and cp.get("mcp_servers") == 0,
            str(cp.get("kind")),
        )
        leg("first-create-is-existing-tree", FIRST_CREATE["mode"] == "publish-existing")
        brief_ok = (
            "linkedin-send" in FIRST_CREATE["never"]
            and "plugin/" in FIRST_CREATE["path"]
        )
        leg("never-linkedin-send", brief_ok)
        live = catalog_coverage(rows)
        wired = [r for r in rows if (r.get("mcp_servers") or 0) > 0]
        hollow_names = {r.get("name") for r in rows if not (r.get("mcp_servers") or 0)}
        leg(
            "coverage-sums-to-total",
            live["total"] == len(rows)
            and live["hollow"] + len(wired) == len(rows)
            and sum(live["hollow_by_kind"].values()) == live["hollow"]
            and live["connectors_mcp_verified"]
            == sum(1 for r in wired if r.get("verified_publisher") is True)
            and all(
                r.get("name") in hollow_names
                for r in rows
                if not (r.get("mcp_servers") or 0)
            ),
            str(
                {
                    k: live[k]
                    for k in (
                        "total",
                        "hollow",
                        "unverified",
                        "connectors_mcp_verified",
                    )
                }
            ),
        )
    ok = sum(1 for _, g, _ in legs if g)
    for name, good, detail in legs:
        emit(
            "  {m} {n}{d}".format(
                m="ok  " if good else "FAIL",
                n=name,
                d=(" — " + detail) if detail else "",
            )
        )
    emit(
        "SELFTEST {v} - {o}/{t}".format(
            v="PASS" if ok == len(legs) else "FAIL", o=ok, t=len(legs)
        )
    )
    return 0 if ok == len(legs) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gb-plugins.py",
        description="Every marketplace plugin, this account's installs, a create-plugin brief.",
    )
    ap.add_argument(
        "verb",
        nargs="?",
        choices=(
            "universe",
            "catalog",
            "installed",
            "new",
            "show",
            "kinds",
            "create-brief",
            "refresh",
        ),
        default="universe",
    )
    ap.add_argument("name", nargs="?", help="show NAME, or create-brief target")
    ap.add_argument("--kind", help="catalog: filter kind")
    ap.add_argument("--q", dest="query", help="catalog: substring")
    ap.add_argument("--job", help="create-brief: one-sentence job")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest_inline()
    if args.verb == "refresh":
        return cmd_refresh()
    if args.verb == "create-brief":
        return cmd_create_brief(args.name, args.job)

    path, doc = newest_market(ROOT)
    if path is None or doc is None:
        emit("ERROR no market/<stamp>.json — run: python3 bin/gb plugins refresh")
        return 2

    if args.json and args.verb == "universe":
        emit(
            json.dumps(
                {
                    "schema": "gb-plugins/1",
                    "snapshot": path.name,
                    "plugins": doc.get("plugins"),
                    "coverage": catalog_coverage(catalog_rows(doc)),
                    "installed": installed_names(doc, ROOT),
                    "first_create": FIRST_CREATE,
                },
                indent=1,
            )
        )
        return 0

    if args.verb == "universe":
        return cmd_universe(path, doc, ROOT)
    if args.verb == "catalog":
        return cmd_catalog(doc, args.kind, args.query)
    if args.verb == "installed":
        return cmd_installed(doc, ROOT)
    if args.verb == "new":
        return cmd_new(ROOT, doc)
    if args.verb == "show":
        if not args.name:
            emit("ERROR gb plugins show needs NAME")
            return 2
        return cmd_show(doc, ROOT, args.name)
    if args.verb == "kinds":
        return cmd_kinds(doc)
    emit("ERROR unknown verb")
    return 2


if __name__ == "__main__":
    gbmain(main)
