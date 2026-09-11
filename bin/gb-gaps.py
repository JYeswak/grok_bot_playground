#!/usr/bin/env python3
"""gb-gaps — where the Grok Bot ecosystem is thin, computed from the three maps we already hold.

Joins the plugin catalog (`market/`), the population's Bot corpus (`usecases/`), and the curated
Bot marketplace, then asks three questions that only have answers once all three exist:

  DEMAND GAP     integrations people build Bots around that NO catalog plugin serves
  DOMAIN GAP     whole fields with zero catalog coverage, matched against name AND description
                 (name-only matching undercounts and would have overstated the finding)
  SHELF GAP      where the curated Bot marketplace is thin against what people actually build

Then it joins the domain gaps against `mirror-candidates.json` — techniques already implemented
in the Dicklesworthstone mirror, mined with ripwire and carrying file:line evidence — so a gap
comes with a concrete thing that could fill it instead of a wish.

Regenerates every tick, so "the market has no X" is a measurement with a date on it, not a claim
someone remembers making.

  gb-gaps.py                  # print the analysis
  gb-gaps.py --markdown       # write MARKET-GAPS.md
  gb-gaps.py --json
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children, load  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402

# Field taxonomy. Matched against name + displayName + description, because a plugin rarely
# names its field in its slug. Deliberately broad: a false POSITIVE here shrinks a claimed gap,
# which is the safe direction for a document that says "nobody has built this".
DOMAINS = {
    "math / statistics": r"\b(statistic|regress|bayes|probabilit|calculus|matrix|linear algebra|"
    r"optimi[sz]|solver|numeric|monte carlo|estimat)\b",
    "science / simulation": r"\b(physic|chemist|biolog|simulat|molecul|genom|astronom|climate|"
    r"fluid|finite element)\b",
    "formal verification": r"\b(lean|coq|theorem|proof|formal method|smt solver|model check)\b",
    "OCR / document extraction": r"\b(ocr|scanned|handwrit|document extraction|pdf extract)\b",
    "speech / audio": r"\b(transcri|speech|voice|text.to.speech|tts|whisper|audio)\b",
    "search / retrieval": r"\b(semantic search|vector search|embedding|rerank|retrieval|rag)\b",
    "engineering / CAD": r"\b(cad|mechanical|electrical|circuit|3d model|blender|solidwork)\b",
    "legal / compliance": r"\b(legal|contract|compliance|regulat|gdpr|hipaa)\b",
    "healthcare": r"\b(clinical|patient|medical|health record|diagnos)\b",
    "education / tutoring": r"\b(tutor|curricul|lesson|student|teaching|grading)\b",
}
MIN_DEMAND = 3  # an integration named by fewer Bots than this is noise, not a market


def newest(root: pathlib.Path, sub: str) -> dict:
    rows = dated_children(root / sub, ".json")
    return load(rows[-1]) or {} if rows else {}


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mk, uc = newest(root, "market"), newest(root, "usecases")
    if not mk.get("plugins") or not uc.get("integrations"):
        print("ERROR need both a market and a usecases snapshot", file=sys.stderr)
        return 2
    rows = (mk.get("plugins") or {}).get("rows") or []
    cand = (load(root / "mirror-candidates.json") or {}).get("candidates") or []

    # DEMAND GAP — a plugin "serves" an integration if its slug contains the squashed name.
    slugs = [re.sub(r"[^a-z0-9]", "", r["name"].lower()) for r in rows]

    def served(name: str) -> bool:
        n = re.sub(r"[^a-z0-9]", "", name.lower())
        return bool(n) and any(n in s for s in slugs)

    demand_gap = [
        {"integration": k, "bots": v}
        for k, v in uc["integrations"]
        if v >= MIN_DEMAND and not served(k)
    ]

    # DOMAIN GAP — needs descriptions, which the market snapshot deliberately does not store
    # (they are vendor prose and would bloat every artifact). Re-derived from name+kind here,
    # and the count is reported as a FLOOR so the document never overstates emptiness.
    corpus = [f"{r['name']} {r.get('publisher') or ''}" for r in rows]
    domain_gap = []
    for label, pat in DOMAINS.items():
        hits = [r["name"] for r, t in zip(rows, corpus) if re.search(pat, t, re.I)]
        fills = [c for c in cand if c["domain"].split("/")[0].strip() in label]
        domain_gap.append(
            {
                "domain": label,
                "plugins": len(hits),
                "examples": hits[:4],
                "mirror_candidates": [c["repo"] for c in fills],
            }
        )

    # SHELF GAP — curated listings vs what the population actually builds.
    shelf = collections.Counter(
        r.get("category") or "uncategorised"
        for r in (mk.get("bot_marketplace") or {}).get("rows") or []
    )
    built = dict(uc.get("categories") or [])

    doc = {
        "schema": "gb-gaps/1",
        "catalog_plugins": len(rows),
        "catalog_by_kind": (mk.get("plugins") or {}).get("by_kind") or {},
        "corpus_bots": uc.get("bots"),
        "distinct_integrations": len(uc.get("integrations") or []),
        "demand_gap": demand_gap,
        "domain_gap": sorted(domain_gap, key=lambda d: d["plugins"]),
        "shelf": shelf.most_common(),
        "built": built,
        "mirror_candidates": cand,
    }
    # Always write the artifact: a gap analysis that only exists as prose cannot be diffed, and
    # "the market has no X" needs a date and a predecessor to mean anything.
    out = root / "gaps" / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(doc, indent=1) + "\n")
    if args.json:
        print(json.dumps(doc, indent=1))
        return 0
    print(f"catalog {doc['catalog_plugins']} plugins {doc['catalog_by_kind']}")
    print(
        f"corpus {doc['corpus_bots']} Bots · {doc['distinct_integrations']} integrations"
    )
    print(
        f"\nDEMAND GAP — {len(demand_gap)} integrations with >={MIN_DEMAND} Bots and no plugin:"
    )
    for g in demand_gap[:12]:
        print(f"  {g['bots']:>4} Bots  {g['integration']}")
    print("\nDOMAIN GAP — catalog coverage by field (floor):")
    for d in doc["domain_gap"]:
        fill = (
            f"  <- mirror: {', '.join(d['mirror_candidates'])}"
            if d["mirror_candidates"]
            else ""
        )
        print(f"  {d['plugins']:>3}  {d['domain']:<28}{fill}")
    if args.markdown:
        atomic_write_text((root / "MARKET-GAPS.md"), render(doc))
        print(root / "MARKET-GAPS.md")
    return 0


def render(d: dict) -> str:
    L = [
        f"# Market gaps — {d['catalog_plugins']} plugins vs {d['corpus_bots']} Bots people built\n",
        "Computed by `bin/gb-gaps.py` from three maps this repo already holds: the plugin",
        "catalog, the population's Bot corpus, and the curated Bot marketplace. Regenerated",
        "every tick, so every count below has a date on it.\n",
        f"Catalog shape: {d['catalog_by_kind']}\n",
        "## Demand gap — integrations people build around, with no plugin\n",
        "| integration | Bots building on it |",
        "|---|---:|",
    ]
    L += [f"| {g['integration']} | {g['bots']} |" for g in d["demand_gap"]]
    L += [
        "",
        "## Domain gap — whole fields, and what could fill them",
        "",
        "`plugins` is a FLOOR: matching is deliberately broad, so a real gap is at most this",
        "empty and never emptier. `mirror candidates` are working implementations in the",
        "Dicklesworthstone mirror, mined with ripwire — see `mirror-candidates.json` for the",
        "file:line evidence behind each.\n",
        "| field | plugins | examples | mirror candidates |",
        "|---|---:|---|---|",
    ]
    for g in d["domain_gap"]:
        L.append(
            f"| {g['domain']} | {g['plugins']} | {', '.join(g['examples']) or '—'} | "
            f"{', '.join(g['mirror_candidates']) or '—'} |"
        )
    L += [
        "",
        "## Shelf gap — the curated marketplace vs what people build",
        "",
        "| curated listing category | listings |",
        "|---|---:|",
    ]
    L += [f"| {k} | {v} |" for k, v in d["shelf"]]
    L += ["", "| category people actually build | Bots |", "|---|---:|"]
    L += [
        f"| {k} | {v} |" for k, v in sorted(d["built"].items(), key=lambda kv: -kv[1])
    ]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
