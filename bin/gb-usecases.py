#!/usr/bin/env python3
"""gb-usecases — what people actually build Grok Bots to DO, and which connectors they need.

Everything else in this repo measures Joshua's deployment. This measures the population: a
corpus of real, attributed Bot definitions, normalised into categories and — the part that
matters operationally — the INTEGRATIONS each one requires.

Sources, each with a different shape and a different bias:

  botdirectory   `elie222/botdirectory.ai` — 645 markdown records with YAML frontmatter
                 (name, category, integrations, contributor, source X post, full prompt). The
                 only source here that is structured rather than prose, so it carries the
                 integration counts.
  awesome-index  `RongleCat/awesome-grok-bot` — 799 curated links with one-line descriptions.
                 Breadth: guides, field cases, failure modes.
  shares-index   `kydlikebtc/awesome-grokbot` — 730 live `x.ai/bot` share links, status-checked.

The cross-reference is the product: the integrations the population builds around, joined
against the connectors THIS account has installed. That answers "what is everyone doing that we
cannot do yet" with counts instead of impressions.

Fetches each repo as a single tarball rather than walking the contents API — 645 files is 645
requests and a rate limit, and a partial corpus would quietly skew every count in here.

  gb-usecases.py              # refresh the corpus, write usecases/<stamp>.json
  gb-usecases.py --markdown   # also write USE-CASES.md
  gb-usecases.py --json
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import io
import json
import pathlib
import re
import sys
import tarfile
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children, load  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402

UA = "grokbot-usecase-index/1 (+local weekly refresh)"
CORPUS = ("elie222/botdirectory.ai", "main", "bots/")
LINK_RE = re.compile(r"^-\s*\[([^\]]+)\]\((https?://[^)]+)\)\s*-?\s*(.*)$")


def tarball(repo: str, ref: str) -> tarfile.TarFile | None:
    url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{ref}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            return tarfile.open(fileobj=io.BytesIO(r.read()), mode="r:gz")
    except Exception as e:
        print(
            f"corpus fetch failed for {repo}: {type(e).__name__}: {e}", file=sys.stderr
        )
        return None


def frontmatter(text: str) -> dict:
    """Parse the subset of YAML these records actually use: scalars, [a, b] lists, and { } maps.
    A real YAML parser is not a dependency this repo is taking for four field shapes."""
    if not text.startswith("---"):
        return {}
    block = text.split("---", 2)[1]
    out: dict = {}
    for line in block.splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip().strip('"')
        if v.startswith("[") and v.endswith("]"):
            out[k] = [x.strip().strip('"') for x in v[1:-1].split(",") if x.strip()]
        elif v.startswith("{"):
            continue  # url maps add nothing the integrations list does not already carry
        else:
            out[k] = v
    return out


def harvest_corpus() -> list[dict]:
    repo, ref, prefix = CORPUS
    tf = tarball(repo, ref)
    if tf is None:
        return []
    rows = []
    for m in tf.getmembers():
        parts = m.name.split("/", 1)
        if (
            len(parts) < 2
            or not parts[1].startswith(prefix)
            or not m.name.endswith(".md")
        ):
            continue
        f = tf.extractfile(m)
        if not f:
            continue
        text = f.read().decode("utf-8", "replace")
        fm = frontmatter(text)
        if not fm.get("name"):
            continue
        body = text.split("---", 2)[2].strip() if text.count("---") >= 2 else ""
        rows.append(
            {
                "name": fm.get("name"),
                "category": fm.get("category") or "Uncategorised",
                "integrations": fm.get("integrations") or [],
                "contributor": fm.get("contributor"),
                "source": fm.get("added_via"),
                "added_at": (fm.get("added_at") or "")[:10],
                "prompt_chars": len(body),
                # The operational tell: a Bot whose prompt names an approval step is one someone
                # trusted with a consequential action.
                "has_approval_language": bool(
                    re.search(r"\b(approv|confirm before|ask me before)", body, re.I)
                ),
            }
        )
    return rows


def harvest_links(path: pathlib.Path) -> list[dict]:
    """Curated link lists already fetched into the surface snapshot — parsed, not re-fetched."""
    if not path.is_file():
        return []
    out, section = [], ""
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        m = LINK_RE.match(line.strip())
        if m:
            out.append(
                {
                    "title": m.group(1),
                    "url": m.group(2),
                    "description": m.group(3)[:220],
                    "section": section,
                }
            )
    return out


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    bots = harvest_corpus()
    if not bots:
        print(
            "ERROR corpus empty — a failed fetch is not an empty ecosystem",
            file=sys.stderr,
        )
        return 2

    snaps = dated_children(root / "surface")
    idx = (
        (snaps[-1] / "extras" / "practitioner-index")
        if snaps
        else pathlib.Path("/nonexistent")
    )
    links = harvest_links(idx)

    cats = collections.Counter(b["category"] for b in bots)
    integ = collections.Counter(i for b in bots for i in b["integrations"])
    # What this account can actually reach today.
    invs = [
        f for f in dated_children(root / "inventory", ".json") if ".studio." in f.name
    ]
    installed = (
        {
            (p.get("name") or "").lower()
            for p in (
                ((load(invs[-1]) or {}).get("account_surface") or {}).get("plugins")
                or []
            )
        }
        if invs
        else set()
    )

    def have(name: str) -> bool:
        n = name.lower().replace(" ", "-")
        return any(n == p or n in p or p in n for p in installed)

    doc = {
        "schema": "gb-usecases/1",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "corpus_repo": CORPUS[0],
        "bots": len(bots),
        "curated_links": len(links),
        "categories": cats.most_common(),
        "integrations": integ.most_common(),
        "integration_gap": [
            {"integration": k, "bots": v, "installed": have(k)}
            for k, v in integ.most_common(25)
        ],
        "approval_share": round(
            sum(1 for b in bots if b["has_approval_language"]) / len(bots), 3
        ),
        "rows": sorted(bots, key=lambda b: b["name"] or ""),
        "links": links,
    }
    out = root / "usecases" / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(doc, indent=1) + "\n")

    if args.json:
        print(
            json.dumps(
                {k: v for k, v in doc.items() if k not in ("rows", "links")}, indent=1
            )
        )
    else:
        print(
            f"usecases {out.name}: {len(bots)} Bot definitions, {len(links)} curated links, "
            f"{len(integ)} distinct integrations"
        )
        print(f"  approval language in {doc['approval_share']:.0%} of prompts")
        print(
            "  top categories: " + ", ".join(f"{k} {v}" for k, v in cats.most_common(6))
        )
        print("  top integrations (✓ = installed here):")
        for g in doc["integration_gap"][:12]:
            print(
                f"    {'✓' if g['installed'] else ' '} {g['integration']:<22}{g['bots']:>4} Bots"
            )

    if args.markdown:
        atomic_write_text((root / "USE-CASES.md"), render(doc))
        print(root / "USE-CASES.md")
    return 0


def render(d: dict) -> str:
    L = [
        f"# What people build Grok Bots to do — {d['bots']} attributed Bot definitions\n",
        f"Derived by `bin/gb-usecases.py` from `{d['corpus_repo']}` ({d['bots']} records with",
        "structured frontmatter) plus the curated practitioner index already in the weekly",
        f"snapshot ({d['curated_links']} links). Counts, not impressions.\n",
        f"**Approval language appears in {d['approval_share']:.0%} of prompts** — the population",
        "writes the stop condition into the Bot, it is not a thing only this repo worries about.\n",
        "## Categories\n",
        "| category | Bots |",
        "|---|---:|",
    ]
    L += [f"| {k} | {v} |" for k, v in d["categories"]]
    L += [
        "",
        "## Integrations people build around",
        "",
        "`✓` = installed on this account today. The blanks are the gap between what the",
        "population automates and what this deployment can currently reach.\n",
        "| integration | Bots | installed here |",
        "|---|---:|---|",
    ]
    L += [
        f"| {g['integration']} | {g['bots']} | {'✓' if g['installed'] else '—'} |"
        for g in d["integration_gap"]
    ]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
