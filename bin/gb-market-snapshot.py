#!/usr/bin/env python3
"""gb-market-snapshot — the weekly census of everything installable, and what MY installs are pinned to.

Three catalogs, all authenticated reads, all measured 2026-09-11:

  plugins    DashboardService/ListMarketplacePlugins  -> 322 entries with `updatedAt` and `gitRef`
  bot market GrokBotService/ListPublicGrokBotMarketplaceListings -> 71 listings, 10 categories
  my share   GrokBotService/ListGrokBotTemplates -> the templates this account publishes

Why this is its own producer and not part of the deployment audit: the audit is offline and
judges the machine in front of it. This is a network read of the vendor's catalog, and its value
is entirely in the DIFF — a plugin whose `gitRef` moved shipped new code into a Bot that already
has it enabled, and nothing else in this repo would notice.

  gb-market-snapshot.py            # write market/<stamp>.json
  gb-market-snapshot.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_text  # noqa: E402
from gblib import platform_support, unavailable  # noqa: E402

# TRANSPORT IS A LIBRARY (gbrpc.py) since 2026-09-12. This used to dynamically import
# bin/gb-pull-inventory.py by path to borrow SUPPORT/access_token/rpc — a library dependency
# wearing a producer's clothes, which made `gb dogfood audit` count this file as one more
# hand-roller of that producer's read and forced the python 3.9.6 `sys.modules` dance on
# every borrower. `rpc` takes `service=` so the DashboardService reads below still work.
import gbrpc as _pull  # noqa: E402

DASH = "aiserver.v1.DashboardService"


def plugin_kind(skills: int, mcp: int) -> str:
    """What a catalog entry IS, which the vendor never labels.

    There is no connector API — probed every declared service on 2026-09-11 and every
    `List*Connectors` shape 404s as a missing route. What the UI calls a "connector" is a catalog
    plugin that ships an MCP server to sign into a service; what it calls a skill pack is one
    that ships only instructions. The distinction matters operationally: a connector reaches a
    third-party account, a skill pack only changes how a Bot writes.
    """
    if mcp and skills:
        return "connector+skills"
    if mcp:
        return "connector"
    if skills:
        return "skill-pack"
    return "empty"


def plugin_row(p: dict) -> dict:
    """The fields that change when a plugin changes. `gitRef` is the one that matters: it is the
    commit a Bot actually runs, so a moved ref is new code inside an already-approved install."""
    pub = p.get("publisher") or {}
    skills, mcp = len(p.get("skills") or []), len(p.get("mcpServers") or [])
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "kind": plugin_kind(skills, mcp),
        "status": p.get("status"),
        "lifecycle": p.get("lifecycleState"),
        "git_ref": p.get("gitRef"),
        "updated_at": p.get("updatedAt"),
        "publisher": pub.get("name"),
        "verified_publisher": pub.get("isVerified"),
        "skills": skills,
        "mcp_servers": mcp,
        "variables": len(p.get("variables") or []),
    }


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    plat = platform_support()
    blocked = unavailable(plat, "credential_read")
    if blocked is not None or plat.support_dir is None:
        print(
            "gb-market-snapshot: official catalog needs a signed-in Grok Bot "
            "desktop on macOS. This machine cannot read that credential. "
            "Public corpus: gb market refresh --corpus",
            file=sys.stderr,
        )
        return 3
    token = _pull.access_token(plat.support_dir)
    st_p, plugins = _pull.rpc(token, "ListMarketplacePlugins", {}, service=DASH)
    st_m, market = _pull.rpc(token, "ListPublicGrokBotMarketplaceListings", {})
    st_t, mine = _pull.rpc(token, "ListGrokBotTemplates", {})
    st_i, installs = _pull.rpc(token, "ListUserPluginInstalls", {}, service=DASH)
    if st_p != 200 or st_m != 200:
        raise SystemExit(f"catalog read failed: plugins={st_p} market={st_m}")

    rows = [plugin_row(p) for p in (plugins.get("plugins") or [])]
    by_name = {r["name"]: r for r in rows}
    listings = (market.get("featuredListings") or []) + (market.get("listings") or [])

    installed = []
    for i in (installs.get("installs") or []) if st_i == 200 else []:
        pg = i.get("plugin") or {}
        cat = by_name.get(pg.get("name"))
        installed.append(
            {
                "name": pg.get("name"),
                "enabled": i.get("isEnabled"),
                # The pin question: what is the catalog serving right now for a plugin I already run?
                "catalog_git_ref": (cat or {}).get("git_ref"),
                "catalog_updated_at": (cat or {}).get("updated_at"),
                "catalog_status": (cat or {}).get("status"),
            }
        )

    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    doc = {
        "schema": "gb-market/1",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "plugins": {
            "total": len(rows),
            "by_kind": kinds,
            "rows": sorted(rows, key=lambda r: r["name"] or ""),
        },
        "bot_marketplace": {
            "total": len(listings),
            "categories": market.get("allCategoriesOrder") or [],
            "rows": sorted(
                (
                    {
                        "slug": x.get("slug"),
                        "name": x.get("name"),
                        "category": x.get("category"),
                        "creator": (x.get("creator") or {}).get("name"),
                        "updated_at_ms": x.get("updatedAtMs"),
                    }
                    for x in listings
                ),
                key=lambda r: r["slug"] or "",
            ),
        },
        "my_templates": sorted(
            (
                {"share_id": t.get("shareId"), "name": t.get("name")}
                for t in ((mine or {}).get("templates") or [])
            ),
            key=lambda r: r["name"] or "",
        )
        if st_t == 200
        else None,
        "installed": sorted(installed, key=lambda r: r["name"] or ""),
    }

    out = root / "market" / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.json"
    if args.dry_run:
        print(
            json.dumps({k: v for k, v in doc.items() if k != "plugins"}, indent=1)[
                :1500
            ]
        )
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(doc, indent=1) + "\n")
    print(
        f"market {out}: {doc['plugins']['total']} plugins "
        f"({', '.join(f'{v} {k}' for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))}), "
        f"{doc['bot_marketplace']['total']} Bot listings in "
        f"{len(doc['bot_marketplace']['categories'])} categories, "
        f"{len(doc['installed'])} installed, "
        f"{len(doc['my_templates'] or [])} template(s) published"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
