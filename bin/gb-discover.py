#!/usr/bin/env python3
"""gb-discover — find the Grok Bot repos nobody told us about yet.

`sources.json` watches what we already know. This answers the other half: what appeared this week
that is not in any list we hold. It searches GitHub directly — keyword, topic, and code — and
diffs the result against everything already seen, so the output is only what is NEW.

Measured 2026-09-11 on the first run: 557 repos match `grok-bot` pushed in the last 14 days, and
the top of that list included a 2497-star open-source alternative, a 2240-star one, and a SECOND
730-entry community index — none of which appeared in the curated list this repo was watching.
A fixed watch list cannot find those. That is the whole reason this file exists.

What it structurally CANNOT see, and no amount of searching will fix: X/Twitter threads, Discord
and Slack rooms, WeChat articles, YouTube, private repos, and anything published under a name
that does not contain a term we search for. Those are recorded as BLOCKED in COVERAGE.md rather
than pretended away — the practitioner index (g13) is the human-curated cover for exactly that
blind spot, which is why both exist.

  gb-discover.py              # write discovery/<stamp>.json, print what is new
  gb-discover.py --json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children, load  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402

# Each query states what it is FOR. A query nobody can justify is noise that will be ignored,
# and an ignored row trains everyone to ignore the whole report.
QUERIES = [
    (
        "recent-keyword",
        "repositories",
        "grok-bot pushed:>={since}",
        "stars",
        "anything active that names Grok Bot",
    ),
    (
        "recent-grokbot",
        "repositories",
        "grokbot pushed:>={since}",
        "stars",
        "the unhyphenated spelling, which a surprising number of authors use",
    ),
    (
        "topic",
        "repositories",
        "topic:grok-bot",
        "updated",
        "authors who deliberately tagged their work for this ecosystem",
    ),
    ("topic-alt", "repositories", "topic:grokbot", "updated", "same, unhyphenated"),
    (
        "plugins",
        "repositories",
        "grok-plugin pushed:>={since}",
        "stars",
        "installable plugins, which can end up inside a Bot",
    ),
    (
        "skills",
        "repositories",
        '"SKILL.md" grok bot pushed:>={since}',
        "stars",
        "skill packs, the other thing a Bot can be handed",
    ),
]
NOTABLE_STARS = (
    50  # a NEW repo at or above this must be triaged before the board goes green
)
LOOKBACK_DAYS = 21


def gh_search(kind: str, q: str, sort: str, per_page: int = 25) -> list[dict]:
    cmd = [
        "gh",
        "api",
        "-X",
        "GET",
        f"search/{kind}",
        "-f",
        f"q={q}",
        "-f",
        f"sort={sort}",
        "-f",
        f"per_page={per_page}",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return [{"_error": f"{type(e).__name__}: {e}"}]
    if p.returncode != 0:
        return [{"_error": (p.stderr or "").strip()[:200]}]
    try:
        return (json.loads(p.stdout) or {}).get("items") or []
    except Exception as e:
        return [{"_error": f"unparseable: {e}"}]


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    since = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    known = {r["repo"] for r in (load(root / "sources.json") or {}).get("repos", [])}
    seen_doc = load(root / "discovery-seen.json") or {}
    seen = set(seen_doc.get("repos") or []) | known

    found: dict[str, dict] = {}
    errors = []
    for label, kind, tmpl, sort, why in QUERIES:
        items = gh_search(kind, tmpl.format(since=since), sort)
        if items and "_error" in items[0]:
            errors.append({"query": label, "error": items[0]["_error"]})
            continue
        for it in items:
            full = it.get("full_name")
            if not full:
                continue
            row = found.setdefault(
                full,
                {
                    "repo": full,
                    "stars": it.get("stargazers_count", 0),
                    "pushed_at": it.get("pushed_at"),
                    "created_at": it.get("created_at"),
                    "description": (it.get("description") or "")[:160],
                    "queries": [],
                },
            )
            row["queries"].append(label)

    new = sorted(
        (r for k, r in found.items() if k not in seen), key=lambda r: -r["stars"]
    )
    notable = [r for r in new if r["stars"] >= NOTABLE_STARS]

    doc = {
        "schema": "gb-discovery/1",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "lookback_days": LOOKBACK_DAYS,
        "notable_stars": NOTABLE_STARS,
        "queries": [
            {"label": q[0], "query": q[2].format(since=since), "why": q[4]}
            for q in QUERIES
        ],
        # A query that failed is NOT an empty result. Recorded so g17 can refuse to call a broken
        # search "nothing new" — the positive-control rule from AGENTS.md, applied to discovery.
        "errors": errors,
        "candidates_total": len(found),
        "new": new,
        "notable": [r["repo"] for r in notable],
    }
    out = root / "discovery" / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(doc, indent=1) + "\n")

    if args.json:
        print(json.dumps(doc, indent=1))
        return 0
    print(
        f"discovery {out.name}: {len(found)} candidate(s) across {len(QUERIES)} queries, "
        f"{len(new)} new, {len(notable)} notable (>={NOTABLE_STARS}★)"
        + (f", {len(errors)} QUERY ERROR(S)" if errors else "")
    )
    for r in new[:20]:
        mark = "**" if r["stars"] >= NOTABLE_STARS else "  "
        print(f" {mark} {r['stars']:>6}★ {r['repo']:<44} {r['description'][:70]}")
    if len(new) > 20:
        print(f"    … {len(new) - 20} more in {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
