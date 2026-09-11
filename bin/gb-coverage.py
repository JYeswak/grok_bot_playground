#!/usr/bin/env python3
"""gb-coverage — what this repo actually measures about the deployment, derived from the artifacts.

Not a hand-written status page. Every row below names a real accessor into a real artifact on
disk, and the verdict is computed by reading it:

  MEASURED    a value is present
  EMPTY       measured, and the true answer is zero/none (distinct from unmeasured — the whole
              point of this repo's null discipline)
  UNMEASURED  null or absent: nobody has read it
  BLOCKED     no read path exists, with the reason recorded

The count of MEASURED+EMPTY facets is the number `g14` ratchets: coverage may go up and may not
silently go down. A facet that regresses to UNMEASURED is a reading failure someone has to see.

  gb-coverage.py              # table
  gb-coverage.py --markdown   # write COVERAGE.md
  gb-coverage.py --json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children, load, newest_audit  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402

MEASURED, EMPTY, UNMEASURED, BLOCKED = "MEASURED", "EMPTY", "UNMEASURED", "BLOCKED"

# (facet, artifact, accessor, gate, note). Accessor takes the loaded artifact and returns a value;
# None means unmeasured, an empty list/dict means EMPTY.
FACETS = [
    # --- vendor surface -------------------------------------------------------------------
    (
        "doc pages (docs.x.ai)",
        "surface",
        lambda s: s.get("pages"),
        "g1/g2",
        "18 grok-bot pages, hashed",
    ),
    (
        "doc index (llms.txt)",
        "surface",
        lambda s: s.get("llms_total_pages"),
        "g2",
        "168-page census",
    ),
    (
        "second doc tree (cursor.com)",
        "surface",
        lambda s: [
            e for e in (s.get("extras") or []) if "cursor.com" in (e.get("url") or "")
        ],
        "g1",
        "11 pages",
    ),
    (
        "release notes / status feed",
        "surface",
        lambda s: [
            e
            for e in (s.get("extras") or [])
            if e.get("label") in ("release-notes", "status-feed")
        ],
        "g1",
        "",
    ),
    (
        "practitioner index",
        "surface",
        lambda s: [
            e for e in (s.get("extras") or []) if e.get("label") == "practitioner-index"
        ],
        "g13",
        "awesome-grok-bot",
    ),
    # --- client + entitlements ------------------------------------------------------------
    (
        "client version / signature",
        "audit",
        lambda a: (a.get("app") or {}).get("version"),
        "g4/g7",
        "",
    ),
    # Key-present-but-null is the audit saying "I looked and there is no staged update" — that is
    # EMPTY, not UNMEASURED. Collapsing the two would let a reading failure hide behind a normal
    # state, which is the exact confusion the rest of this repo refuses to make.
    (
        "update marker (staged vs applied)",
        "audit",
        lambda a: a.get("update_marker")
        if a.get("update_marker") is not None
        else ({} if "update_marker" in a else None),
        "g4",
        "empty = nothing staged",
    ),
    (
        "session marker (client alive/crash)",
        "audit",
        lambda a: a.get("session_marker"),
        "g4",
        "",
    ),
    (
        "feature gates (enabled ids)",
        "audit",
        lambda a: (a.get("gates") or {}).get("enabled_ids"),
        "g6",
        "1212 exact",
    ),
    (
        "capability names",
        "audit",
        lambda a: (a.get("gates") or {}).get("enabled_names"),
        "g6",
        "250 of 1212 nameable",
    ),
    (
        "tunables (config/layer digests)",
        "audit",
        lambda a: (a.get("tunables") or {}).get("digests"),
        "g11",
        "672",
    ),
    (
        "tunable names",
        "audit",
        lambda a: (a.get("tunables") or {}).get("names"),
        "g11",
        "233",
    ),
    (
        "account plan / membership",
        "audit",
        lambda a: (a.get("account") or {}).get("membership"),
        "-",
        "",
    ),
    # --- the fleet --------------------------------------------------------------------------
    (
        "Bot roster + descriptions",
        "audit",
        lambda a: a.get("bots"),
        "g5",
        "server-authoritative",
    ),
    (
        "Bot durable identity / writability",
        "inventory",
        lambda i: [b for b in (i.get("bots") or []) if b.get("numeric_id")],
        "-",
        "",
    ),
    (
        "per-Bot skills",
        "inventory",
        lambda i: [
            b["skills"] for b in (i.get("bots") or []) if b.get("skills") is not None
        ],
        "g8",
        "NE-8",
    ),
    (
        "per-Bot routines + provenance",
        "inventory",
        lambda i: [b["routines"] for b in (i.get("bots") or [])],
        "g9",
        "",
    ),
    (
        "per-Bot memory shards",
        "inventory",
        lambda i: (i.get("account_surface") or {}).get("memory_shards"),
        "-",
        "content empty on all 10",
    ),
    (
        "Bot notification state",
        "audit",
        lambda a: [b for b in (a.get("bots") or []) if "notifications_enabled" in b],
        "-",
        "advisory only",
    ),
    # --- account controls --------------------------------------------------------------------
    (
        "Auto-review enabled",
        "inventory",
        lambda i: i.get("auto_review_enabled"),
        "g8",
        "",
    ),
    (
        "Auto-review RULES",
        "inventory",
        lambda i: i.get("auto_review_rules"),
        "g8",
        "account-level, NE-9",
    ),
    (
        "installed plugins",
        "inventory",
        lambda i: (i.get("account_surface") or {}).get("plugins"),
        "-",
        "user-level",
    ),
    ("MCP servers", "inventory", lambda i: i.get("mcp_servers"), "-", ""),
    (
        "registered computers",
        "inventory",
        lambda i: (i.get("account_surface") or {}).get("computers"),
        "g7",
        "",
    ),
    (
        "form-vault entries",
        "inventory",
        lambda i: (i.get("account_surface") or {}).get("form_vault_keys"),
        "-",
        "",
    ),
    (
        "template export policy",
        "inventory",
        lambda i: i.get("template_export_policy"),
        "-",
        "",
    ),
    ("usage + on-demand spend", "inventory", lambda i: i.get("usage"), "g12", ""),
    (
        "local secrets present",
        "audit",
        lambda a: a.get("secret_keys"),
        "-",
        "names only, never values",
    ),
    # --- what is installable, and what my installs are pinned to -----------------------------
    (
        "plugin catalog",
        "market",
        lambda m: (m.get("plugins") or {}).get("rows"),
        "g15",
        "322 entries",
    ),
    (
        "Bot marketplace listings",
        "market",
        lambda m: (m.get("bot_marketplace") or {}).get("rows"),
        "g15",
        "71",
    ),
    ("my published templates", "market", lambda m: m.get("my_templates"), "g15", ""),
    (
        "installed plugin pins (gitRef)",
        "market",
        lambda m: m.get("installed"),
        "g15",
        "new code in an approved Bot",
    ),
    (
        "watched repositories",
        "sources",
        lambda s: s.get("repos"),
        "g17",
        "declared, with why",
    ),
    (
        "discovery candidates",
        "discovery",
        lambda d: d.get("new"),
        "g17",
        "what no list holds",
    ),
    (
        "MCP surfaces probed",
        "mcp",
        lambda m: m.get("servers"),
        "g18",
        "tool sets, week over week",
    ),
    (
        "fleet work (transcripts/routines)",
        "utilization",
        lambda u: u.get("bots"),
        "g19",
        "does it DO anything",
    ),
    (
        "orphaned conversation history",
        "utilization",
        lambda u: {"n": u.get("orphaned_entries")}
        if u.get("orphaned_entries") is not None
        else None,
        "g19",
        "what the rebuild cost",
    ),
    (
        "archived conversation history",
        "archive",
        lambda a: a.get("rows"),
        "g20",
        "the only copy past the cap",
    ),
    (
        "population use-cases",
        "usecases",
        lambda u: u.get("rows"),
        "g21",
        "645 attributed Bots",
    ),
    (
        "integration demand vs installed",
        "usecases",
        lambda u: u.get("integration_gap"),
        "g21",
        "the adoption gap",
    ),
    (
        "strictly typed files",
        "typecheck",
        lambda t: t.get("checked"),
        "g23",
        "the type-floor ratchet",
    ),
    (
        "type checkers agreeing",
        "typecheck",
        lambda t: [r for r in (t.get("runs") or []) if r.get("ran")],
        "g23",
        "two independent lineages, not one opinion",
    ),
    (
        "market gap analysis",
        "gaps",
        lambda g: g.get("domain_gap"),
        "-",
        "recomputed per tick",
    ),
    (
        "connector classification",
        "market",
        lambda m: (m.get("plugins") or {}).get("by_kind"),
        "g15",
        "connector vs skill-pack",
    ),
    # --- the loop ------------------------------------------------------------------------------
    ("scheduler registration", "schedule", lambda s: s.get("installed"), "g10", ""),
    ("tick run history", "runs", lambda r: r, "g10", ""),
]

BLOCKED_FACETS = [
    (
        "X / Discord / WeChat / YouTube",
        "where a large share of practitioner signal actually lives. "
        "No API we can poll honestly; x.ai/news itself 403s. The human-curated practitioner indexes "
        "(g13) are the deliberate cover for this blind spot, which is why they are watched at all.",
    ),
    (
        "private and unnamed repos",
        "GitHub search finds what is public AND uses a term we search "
        "for. A tool published under a name containing none of them is invisible to g17 until a "
        "curated index picks it up.",
    ),
    (
        "Execution on Local Computer",
        "per-desktop setting; no server record on any of the six declared "
        "services. Hand record in policy/<label>.json is the only path.",
    ),
    (
        "per-Bot plugin attachment",
        "GetGrokBotAgentPlugins returns resource-404 for every Bot; only the "
        "user-level install list is answerable.",
    ),
    (
        "the 962 unnamed capabilities",
        "their literals are not in this client's bundle — they belong to "
        "Cursor's other surfaces (NE-7).",
    ),
    (
        "iOS / iPad / Android install",
        "no scriptable surface; the mobile client is not a registered "
        "computer and no RPC enumerates it.",
    ),
    (
        "Action Recording / audit logs",
        "Enterprise-only; this account is is_enterprise_user=false.",
    ),
    (
        "x.ai/news",
        "returns 403 to any plain client (NE-1 family); the status feed and release notes "
        "cover the same ground and do fetch.",
    ),
]


def verdict(v) -> str:
    if v is None:
        return UNMEASURED
    if isinstance(v, (list, dict, str)) and len(v) == 0:
        return EMPTY
    return MEASURED


def sources(root: pathlib.Path) -> dict:
    snaps = dated_children(root / "surface")
    _, audit = newest_audit(root)
    invs = dated_children(root / "inventory", ".json")
    runs = root / "schedule" / "runs.jsonl"
    return {
        "surface": load(snaps[-1] / "manifest.json") if snaps else None,
        "audit": audit,
        "inventory": load(invs[-1]) if invs else None,
        "gaps": load(dated_children(root / "gaps", ".json")[-1])
        if dated_children(root / "gaps", ".json")
        else None,
        "usecases": load(dated_children(root / "usecases", ".json")[-1])
        if dated_children(root / "usecases", ".json")
        else None,
        "typecheck": load(dated_children(root / "typecheck", ".json")[-1])
        if dated_children(root / "typecheck", ".json")
        else None,
        "archive": load(dated_children(root / "archive", ".json")[-1])
        if dated_children(root / "archive", ".json")
        else None,
        "utilization": load(dated_children(root / "utilization", ".json")[-1])
        if dated_children(root / "utilization", ".json")
        else None,
        "mcp": load(dated_children(root / "mcp", ".json")[-1])
        if dated_children(root / "mcp", ".json")
        else None,
        "sources": load(root / "sources.json"),
        "discovery": load(dated_children(root / "discovery", ".json")[-1])
        if dated_children(root / "discovery", ".json")
        else None,
        "market": load(dated_children(root / "market", ".json")[-1])
        if dated_children(root / "market", ".json")
        else None,
        "schedule": load(root / "schedule" / "agent.json"),
        "runs": [
            x
            for x in (runs.read_text().splitlines() if runs.is_file() else [])
            if x.strip()
        ],
    }


def rows(root: pathlib.Path) -> list[dict]:
    src = sources(root)
    out = []
    for name, key, fn, gate, note in FACETS:
        art = src.get(key)
        try:
            val = fn(art) if art is not None else None
        except Exception:
            val = None
        n = len(val) if isinstance(val, (list, dict)) else (1 if val is not None else 0)
        out.append(
            {
                "facet": name,
                "artifact": key,
                "gate": gate,
                "note": note,
                "verdict": verdict(val),
                "n": n,
            }
        )
    return out


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rs = rows(root)
    covered = sum(1 for r in rs if r["verdict"] in (MEASURED, EMPTY))
    summary = {
        "facets": len(rs),
        "covered": covered,
        "unmeasured": [r["facet"] for r in rs if r["verdict"] == UNMEASURED],
        "blocked": len(BLOCKED_FACETS),
    }

    if args.json:
        print(json.dumps({**summary, "rows": rs}, indent=1))
        return 0

    lines = [
        f"# Coverage — {covered}/{len(rs)} facets measured, {len(BLOCKED_FACETS)} blocked\n",
        "Derived by `bin/gb-coverage.py` from the artifacts on disk, not asserted. `EMPTY` means",
        "measured and genuinely zero; `UNMEASURED` means nobody read it. `g14` ratchets the",
        "covered count so it cannot silently fall.\n",
        "| facet | verdict | n | gate | artifact | note |",
        "|---|---|---:|---|---|---|",
    ]
    for r in rs:
        lines.append(
            f"| {r['facet']} | {r['verdict']} | {r['n']} | {r['gate']} | {r['artifact']} | {r['note']} |"
        )
    lines += ["", f"## Blocked — no read path ({len(BLOCKED_FACETS)})", ""]
    for name, why in BLOCKED_FACETS:
        lines.append(f"- **{name}** — {why}")
    body = "\n".join(lines) + "\n"

    if args.markdown:
        atomic_write_text((root / "COVERAGE.md"), body)
        print(root / "COVERAGE.md")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
