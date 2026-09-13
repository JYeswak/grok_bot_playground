#!/usr/bin/env python3
"""gb-galaxy — measure what the Grok Bot Galaxy livestream changes, instead of remembering it.

THE EVENT, as captured rather than as recalled: Grok Bot Galaxy runs 2026-09-15 .. 2026-09-17,
roughly 08:30-18:00 PT each day, at The Howard in San Francisco plus a free livestream; three
SpaceXAI builders (mattyp, poteto, roshan_s) start from a blank slate and ship a company live,
with role-specific department sessions alongside. Provenance on disk: `x/2026-09-11T1753.json`
carries 19 posts naming the event, `feeds/2026-09-11T1753.json` carries the Hacker News item for
`https://x.ai/galaxy`.

AND THE PART THAT MAKES THIS FILE NECESSARY: six of those posts — every one of them a reply from
`@grok` itself, the vendor's own account, across four conversations — say the official pages
mention no recording, no replay and no on-demand availability. **If nothing is captured live,
nothing is captured.** A week later, "what the stream changed" is an impression; a dated artifact
is worth exactly what it measures. So:

    gb-galaxy.py baseline            # freeze the ecosystem NOW -> galaxy/<stamp>.json
    gb-galaxy.py baseline --dry-run  # print the fingerprint, write nothing
    gb-galaxy.py diff                # newest baseline against what is on disk today
    gb-galaxy.py diff --from A --to B
    gb-galaxy.py sessions            # which announced department our pack can actually serve
    gb-galaxy.py --selftest

It re-fetches NOTHING. Every number comes from an artifact another producer already wrote
(`usecases/`, `sources/`, `github/`, `feeds/`, `x/`, `market/`, `mcp/`, `utilization/`), so a
baseline costs no network, no API allowance and no account mutation.

FOUR VERDICTS, and the two that matter are the unusual ones:

  CHANGED       a facet moved between the two sides.
  UNCHANGED     it was re-measured and did not move.
  INSUFFICIENT  it was NOT re-measured — the same artifact sits on both sides, or it is absent
                from one. This is the answer for a lone baseline, and it is the honest one: a
                diff against yourself is not evidence of stability.
  INCONSISTENT  same artifact stamp on both sides, different numbers — an artifact was rewritten
                in place, so every delta derived from it is untrustworthy. Exits 1.

Two deliberate refusals in the measurement itself:

  * A TRUNCATED set is never diffed. Sets are capped (SET_CAP) so the artifact stays small; when
    a cap bites, the added/removed lists would be an artifact of the cap, so that set reports
    INSUFFICIENT instead of a confident lie.
  * The account facet carries AGGREGATES ONLY — Bot count, routines, runs, entries. Bot names on
    this account are client-identifying and this artifact is meant to be publishable.

`sessions` is the other half. The vendor announced eleven departments; this repo has evidence for
some of them and not others, so the decision is COMPUTED from the corpus every time it runs — a
department needs a spine (a category or connector cohort of at least SPINE_MIN Bots) and a shape
(at least SHAPE_MIN Bots whose names say they do that job), and a department an existing template
already serves is refused rather than duplicated. Nothing in this file asserts a department is
unserveable; it reports the two cohort sizes and applies one rule to them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children, load  # noqa: E402
from gbtypes import atomic_write_json, main as gbmain  # noqa: E402

SCHEMA = "gb-galaxy/1"
# A set of names is stored so a diff can say WHICH repo appeared, not just that one did. The cap
# is set ABOVE the largest real set measured here (the 645-Bot corpus roster; next is 452 GitHub
# repos, then 325 plugins), which keeps a baseline near 100 KB and keeps every set diffable
# today. Past the cap, honesty beats completeness: a truncated set refuses rather than reporting
# additions that are really artefacts of the cap.
SET_CAP = 1000
SHOW = 12  # names printed per delta; the counts are always complete

# A department needs BOTH: something the population already builds on (spine) and evidence of
# this particular job (shape). Both thresholds are stated here so a reader can move them and
# re-run rather than argue with a paragraph.
SPINE_MIN = 20  # cohort size — the whole Success category is exactly 20, the corpus floor we accept
SHAPE_MIN = 10  # Bots whose NAME says they do this job

EVENT: Dict[str, Any] = {
    "name": "Grok Bot Galaxy",
    "dates": "2026-09-15 .. 2026-09-17",
    "hours": "about 08:30-18:00 PT daily",
    "where": "The Howard, San Francisco, plus a free livestream",
    "register": ["https://x.ai/galaxy", "https://luma.com/3ifrgttw"],
    "builders": ["mattyp", "poteto", "roshan_s"],
    "premise": "three builders take a company from a blank slate to shipped, live, in three days",
    "replay": (
        "NOT ANNOUNCED. Six posts captured 2026-09-11 in x/, all of them replies from @grok "
        "itself across four conversations, say the official pages mention no recording, replay "
        "or on-demand availability. Treat live as the only capture."
    ),
    "session_times": (
        "UNMEASURED. The department list is published; a per-session timetable is not present in "
        "anything this repo has captured. Do not invent one."
    ),
    "departments": (
        "Grok Bot 101",
        "Engineering",
        "Product Managers",
        "Founders",
        "Sales Engineering",
        "Sales",
        "SDRs",
        "Customer Support",
        "Marketing Ops",
        "Post-Sales",
        "Marketing",
    ),
}

# The announced departments, each with the two cohorts it is judged on. `spine` probes are the
# department's OWN population: a category it lives in, or a connector it runs on. `shape` is a
# name query. `served_by` names templates that already do this job — a pack that duplicates its
# own coverage is worse than a pack with gaps.
DEPARTMENTS: Tuple[Dict[str, Any], ...] = (
    {
        "session": "Grok Bot 101",
        "spine": (("corpus", ""),),
        "shape": None,
        "served_by": ("routine-proof",),
        "template": None,
        "note": "the 101 job is 'prove one routine fires'; routine-proof is exactly that Bot",
    },
    {
        "session": "Engineering",
        "spine": (("integration", "GitHub"),),
        "shape": r"\b(dev|devops|engineer|code|coding|commit|pr|pull request|repo|bug|ci|"
        r"release|deploy|lint|test)\b|github",
        "served_by": (),
        "template": "galaxy-engineering",
        "note": "GitHub is the department's connector spine",
    },
    {
        "session": "Product Managers",
        "spine": (("integration", "Linear"), ("integration", "Jira")),
        "shape": r"\b(product|roadmap|backlog|spec|feature|prd|ticket|sprint|jira|linear)\b",
        "served_by": (),
        "template": None,
        "note": "probed against the trackers a PM department runs on; a document store is not a "
        "tracker and is deliberately not counted as a PM spine",
    },
    {
        "session": "Founders",
        "spine": (),
        "shape": r"\b(founder|investor|fundrais|vc|pitch|cap table|board|startup)\b",
        "served_by": (),
        "template": None,
        "note": "no corpus category and no founder-specific connector to probe",
    },
    {
        "session": "Sales Engineering",
        "spine": (("category", "Sales"),),
        "shape": r"\b(demo|poc|pov|rfp|security questionnaire|sales engineer|technical eval)\b",
        "served_by": (),
        "template": None,
        "note": "sits inside the Sales category, but the job barely appears in it",
    },
    {
        "session": "Sales",
        "spine": (("category", "Sales"),),
        "shape": r"\b(crm|deal|pipeline|quote|forecast|account|opportunit|lead)\b",
        "served_by": ("stale-deal-sweep",),
        "template": None,
        "note": "stale-deal-sweep already carves one schedulable job out of a CRM department Bot",
    },
    {
        "session": "SDRs",
        "spine": (("category", "Sales"), ("integration", "LinkedIn")),
        "shape": r"\b(sdr|outbound|prospect|lead|cold|sequence|icp|enrich)\b",
        "served_by": (),
        "template": "galaxy-sdr-desk",
        "note": "the department where copying the most prolific builders is measurably unsafe",
    },
    {
        "session": "Customer Support",
        "spine": (("category", "Success"),),
        "shape": r"\b(support|ticket|helpdesk|triage|escalat|customer service|csat|bug report)\b",
        "served_by": ("first-reply-desk",),
        "template": None,
        "note": "the shape cohort is dominated by reply drafting, which first-reply-desk owns",
    },
    {
        "session": "Marketing Ops",
        "spine": (("category", "Marketing"),),
        "shape": r"\b(analytics|attribution|utm|campaign|ads?|seo|funnel|conversion|dashboard|"
        r"report)\b",
        "served_by": (),
        "template": "galaxy-marketing-ops",
        "note": "the longest-charter cohort measured here, which is what makes it worth narrowing",
    },
    {
        "session": "Post-Sales",
        "spine": (("category", "Success"),),
        "shape": r"\b(renewal|churn|qbr|onboard|account health|upsell|retention)\b",
        "served_by": (),
        "template": None,
        "note": "a real category, but the job itself is three Bots",
    },
    {
        "session": "Marketing",
        "spine": (("category", "Marketing"),),
        "shape": r"\b(post|content|newsletter|blog|social|thread|video|youtube|repurpos)\b",
        "served_by": ("one-post-a-week", "repurpose-desk"),
        "template": None,
        "note": "content marketing is the pack's densest existing coverage",
    },
)

# The vocabulary whose FREQUENCY is the interesting measurement. If the stream teaches the
# population to schedule, "routine" moves; if it launches a connector story, "connector" moves.
# Counted over title+summary of every row in the text corpora, so the number is comparable
# across artifacts even when the row sets differ.
TOPICS: Dict[str, str] = {
    "routine": r"\broutines?\b",
    "schedule": r"\bschedul",
    "connector": r"\bconnectors?\b",
    "mcp": r"\bmcp\b",
    "skill": r"\bskills?\b",
    "template": r"\btemplates?\b",
    "memory": r"\bmemor(y|ies)\b",
    "approval": r"\bapprov",
    "galaxy": r"\bgalaxy\b",
}
_TOPIC_RX = {k: re.compile(v, re.I) for k, v in TOPICS.items()}
_REPLAY_RX = re.compile(r"\b(replays?|recordings?|on.demand)\b", re.I)
_GALAXY_RX = re.compile(r"\bgalaxy\b", re.I)

# Which announced departments a Galaxy post names. Informational: corroboration says the vendor
# talked about a department, never that we have evidence of its Bot shape.
CORROBORATE: Dict[str, str] = {
    "Grok Bot 101": r"\b101\b",
    "Engineering": r"\bengineer",
    "Product Managers": r"\bproduct\b",
    "Founders": r"\bfounder",
    "Sales Engineering": r"sales engineer",
    "Sales": r"\bsales\b",
    "SDRs": r"\bsdrs?\b",
    "Customer Support": r"\bsupport\b",
    "Marketing Ops": r"marketing ops",
    "Post-Sales": r"post.sales",
    "Marketing": r"\bmarketing\b",
}
_CORROBORATE_RX = {k: re.compile(v, re.I) for k, v in CORROBORATE.items()}

CHANGED, UNCHANGED, INSUFFICIENT, INCONSISTENT = (
    "CHANGED",
    "UNCHANGED",
    "INSUFFICIENT",
    "INCONSISTENT",
)


# ---------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------
def emit(text: str) -> None:
    """`print`, except that a reader who walked away is not a failed measurement."""
    try:
        print(text)
    except BrokenPipeError:
        try:
            import os

            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass


def warn(text: str) -> None:
    try:
        print(text, file=sys.stderr)
    except BrokenPipeError:
        pass


# ---------------------------------------------------------------------------------------------
# Reading what other producers already wrote
# ---------------------------------------------------------------------------------------------
def newest(
    root: pathlib.Path, sub: str
) -> Tuple[Optional[pathlib.Path], Optional[Any]]:
    rows = dated_children(root / sub, ".json")
    if not rows:
        return None, None
    return rows[-1], load(rows[-1])


def rel(root: pathlib.Path, path: Optional[pathlib.Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def row_text(row: Dict[str, Any]) -> str:
    return "{} {}".format(row.get("title") or "", row.get("summary") or "")


def topic_counts(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    out = dict.fromkeys(TOPICS, 0)
    for row in rows:
        text = row_text(row)
        for label, rx in _TOPIC_RX.items():
            if rx.search(text):
                out[label] += 1
    return out


def setfacet(values: Sequence[str], cap: int = SET_CAP) -> Dict[str, Any]:
    """A comparable set: its true size, a capped sample, and whether the cap bit.

    `truncated` is load-bearing — a diff of two truncated samples would report additions and
    removals that are properties of the cap, so the diff refuses those sets instead.
    """
    uniq = sorted({v for v in values if isinstance(v, str) and v})
    return {"n": len(uniq), "names": uniq[:cap], "truncated": len(uniq) > cap}


def facet(
    root: pathlib.Path,
    source: Optional[pathlib.Path],
    doc: Optional[Dict[str, Any]],
    counts: Dict[str, Any],
    sets: Optional[Dict[str, Any]] = None,
    maps: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "source": rel(root, source),
        "captured_at": (doc or {}).get("captured_at"),
        "counts": counts,
        "sets": sets or {},
        "maps": maps or {},
    }


def rows_of(doc: Optional[Any]) -> List[Dict[str, Any]]:
    if not isinstance(doc, dict):
        return []
    return [r for r in (doc.get("rows") or []) if isinstance(r, dict)]


def facet_corpus(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    """The attributed Bot corpus: how many, how long, how many connectors, how gated.

    Returns None on an empty corpus rather than a zero median. "No rows" and "a median of 0" are
    different claims and only one of them is true.
    """
    path, doc = newest(root, "usecases")
    rows = rows_of(doc)
    if not rows:
        return None
    chars = sorted(
        int(r.get("prompt_chars") or 0)
        for r in rows
        if int(r.get("prompt_chars") or 0) > 0
    )
    ints = [len(r.get("integrations") or []) for r in rows]
    counts: Dict[str, Any] = {
        "bots": len(rows),
        "with_text": len(chars),
        "median_chars": chars[len(chars) // 2] if chars else None,
        "integrations_per_bot": round(sum(ints) / len(rows), 3),
        "approval_share": round(
            sum(1 for r in rows if r.get("has_approval_language")) / len(rows), 3
        ),
        "distinct_integrations": len(
            {i for r in rows for i in (r.get("integrations") or [])}
        ),
        "newest_added_at": max(
            (str(r.get("added_at") or "") for r in rows), default=""
        ),
    }
    cats: Dict[str, int] = {}
    for row in rows:
        key = str(row.get("category") or "uncategorised")
        cats[key] = cats.get(key, 0) + 1
    igs: Dict[str, int] = {}
    for row in rows:
        for name in row.get("integrations") or []:
            igs[str(name)] = igs.get(str(name), 0) + 1
    return facet(
        root,
        path,
        doc if isinstance(doc, dict) else None,
        counts,
        sets={
            "bot_names": setfacet([str(r.get("name") or "") for r in rows]),
            "contributors": setfacet([str(r.get("contributor") or "") for r in rows]),
        },
        maps={"categories": cats, "integrations": igs},
    )


def facet_vendor_docs(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    path, doc = newest(root, "sources")
    rows = rows_of(doc)
    if not isinstance(doc, dict) or not rows:
        return None
    vel = doc.get("velocity") or {}
    counts = {
        "rows": len(rows),
        "by_kind": (doc.get("counts") or {}).get("by_kind") or {},
        "pages_total": vel.get("pages_total"),
        "changed_1d": vel.get("changed_1d"),
        "changed_7d": vel.get("changed_7d"),
        "churn_7d_pct": vel.get("churn_7d_pct"),
    }
    return facet(
        root,
        path,
        doc,
        counts,
        sets={"pages": setfacet([str(r.get("url") or "") for r in rows])},
        maps={"topics": topic_counts(rows)},
    )


def facet_github(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    path, doc = newest(root, "github")
    rows = rows_of(doc)
    if not isinstance(doc, dict) or not rows:
        return None
    counts = dict(doc.get("counts") or {})
    counts.pop("queries_ok", None)
    counts.pop("queries_failed", None)
    return facet(
        root,
        path,
        doc,
        counts,
        sets={"repos": setfacet([str(r.get("title") or "") for r in rows])},
        maps={"topics": topic_counts(rows)},
    )


def facet_feeds(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    path, doc = newest(root, "feeds")
    rows = rows_of(doc)
    if not isinstance(doc, dict) or not rows:
        return None
    return facet(
        root,
        path,
        doc,
        dict(doc.get("counts") or {}),
        sets={
            "sources": setfacet(
                [
                    str(s.get("url") or "")
                    for s in (doc.get("sources") or [])
                    if isinstance(s, dict)
                ]
            )
        },
        maps={"topics": topic_counts(rows)},
    )


def facet_x(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    """The X corpus, plus the Galaxy-specific block: who is talking about the event, and whether
    anyone has yet said a replay exists."""
    path, doc = newest(root, "x")
    rows = rows_of(doc)
    if not isinstance(doc, dict) or not rows:
        return None
    gal = [r for r in rows if _GALAXY_RX.search(row_text(r))]
    counts = {
        k: v
        for k, v in (doc.get("counts") or {}).items()
        if not k.startswith("queries")
    }
    counts["galaxy_posts"] = len(gal)
    counts["galaxy_replay_mentions"] = sum(
        1 for r in gal if _REPLAY_RX.search(row_text(r))
    )
    hosts = doc.get("hosts") or {}
    host_names = (
        list(hosts.keys()) if isinstance(hosts, dict) else [str(h) for h in hosts]
    )
    return facet(
        root,
        path,
        doc,
        counts,
        sets={
            "hosts": setfacet(host_names),
            "galaxy_authors": setfacet(
                [str((r.get("signals") or {}).get("author") or "") for r in gal]
            ),
        },
        maps={"topics": topic_counts(rows), "galaxy_departments": corroboration(gal)},
    )


def facet_market(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    """The vendor's own shelves. `git_refs` is the one that pays for itself: a plugin whose ref
    moved shipped new code into every Bot that already had it enabled."""
    path, doc = newest(root, "market")
    if not isinstance(doc, dict):
        return None
    plugins = doc.get("plugins") or {}
    shelf = doc.get("bot_marketplace") or {}
    prows = [r for r in (plugins.get("rows") or []) if isinstance(r, dict)]
    lrows = [r for r in (shelf.get("rows") or []) if isinstance(r, dict)]
    if not prows and not lrows:
        return None
    counts = {
        "plugins": plugins.get("total"),
        "by_kind": plugins.get("by_kind") or {},
        "listings": shelf.get("total"),
    }
    refs = {
        str(r.get("name") or ""): str(r.get("git_ref") or r.get("gitRef") or "")
        for r in prows
        if r.get("name")
    }
    cats = shelf.get("categories")
    return facet(
        root,
        path,
        doc,
        counts,
        sets={
            "plugin_names": setfacet([str(r.get("name") or "") for r in prows]),
            "listing_names": setfacet([str(r.get("name") or "") for r in lrows]),
        },
        maps={
            "git_refs": refs,
            "listing_categories": cats if isinstance(cats, dict) else {},
        },
    )


def facet_mcp(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    path, doc = newest(root, "mcp")
    if not isinstance(doc, dict):
        return None
    servers = [s for s in (doc.get("servers") or []) if isinstance(s, dict)]
    if not servers:
        return None
    return facet(
        root,
        path,
        doc,
        {
            "servers": len(servers),
            "probed": doc.get("probed"),
            "ok": doc.get("ok"),
            "tools": sum(int(s.get("tool_count") or 0) for s in servers),
        },
        sets={"names": setfacet([str(s.get("name") or "") for s in servers])},
    )


def facet_account(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    """This account, in AGGREGATE ONLY. Bot names here are client-identifying; the four numbers
    that matter for a before/after are not."""
    path, doc = newest(root, "utilization")
    if not isinstance(doc, dict):
        return None
    bots = [b for b in (doc.get("bots") or []) if isinstance(b, dict)]
    if not bots:
        return None
    return facet(
        root,
        path,
        doc,
        {
            "bots": len(bots),
            "routines_total": sum(int(b.get("routines") or 0) for b in bots),
            "routines_with_runs": sum(
                int(b.get("routines_with_runs") or 0) for b in bots
            ),
            "entries_total": sum(int(b.get("entries") or 0) for b in bots),
            "idle_credentialed": sum(1 for b in bots if b.get("idle_credentialed")),
        },
    )


FACETS: Tuple[Tuple[str, Any], ...] = (
    ("corpus", facet_corpus),
    ("vendor_docs", facet_vendor_docs),
    ("github", facet_github),
    ("feeds", facet_feeds),
    ("x", facet_x),
    ("market", facet_market),
    ("mcp", facet_mcp),
    ("account", facet_account),
)


def fingerprint(root: pathlib.Path) -> Dict[str, Any]:
    """Every facet, or an explicit null for the ones this machine cannot measure. A missing key
    and a measured zero are never the same answer."""
    return {name: build(root) for name, build in FACETS}


def corroboration(galaxy_rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    out = dict.fromkeys(CORROBORATE, 0)
    for row in galaxy_rows:
        text = row_text(row)
        for label, rx in _CORROBORATE_RX.items():
            if rx.search(text):
                out[label] += 1
    return out


# ---------------------------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------------------------
def flat(counts: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """One level of nesting flattened to dotted keys; non-numbers dropped. `by_kind` is a count
    map in every producer here, and its members move independently of the total."""
    out: Dict[str, float] = {}
    for key, val in (counts or {}).items():
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            out[str(key)] = val
        elif isinstance(val, dict):
            for sub, subval in val.items():
                if isinstance(subval, (int, float)) and not isinstance(subval, bool):
                    out["{}.{}".format(key, sub)] = subval
    return out


def diff_counts(a: Dict[str, Any], b: Dict[str, Any]) -> List[Dict[str, Any]]:
    fa, fb = flat(a), flat(b)
    moved: List[Dict[str, Any]] = []
    for key in sorted(set(fa) | set(fb)):
        before, after = fa.get(key), fb.get(key)
        if before != after:
            moved.append(
                {
                    "key": key,
                    "from": before,
                    "to": after,
                    "delta": (
                        round(after - before, 4)
                        if isinstance(before, (int, float))
                        and isinstance(after, (int, float))
                        else None
                    ),
                }
            )
    return moved


def diff_set(
    a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return {"verdict": INSUFFICIENT, "why": "the set is absent on one side"}
    if a.get("truncated") or b.get("truncated"):
        return {
            "verdict": INSUFFICIENT,
            "why": "one side is truncated at the {}-name cap — any added/removed list would be "
            "a property of the cap".format(SET_CAP),
            "n_from": a.get("n"),
            "n_to": b.get("n"),
        }
    before = set(a.get("names") or [])
    after = set(b.get("names") or [])
    added, removed = sorted(after - before), sorted(before - after)
    return {
        "verdict": CHANGED if (added or removed) else UNCHANGED,
        "n_from": a.get("n"),
        "n_to": b.get("n"),
        "added": added[:SHOW],
        "added_n": len(added),
        "removed": removed[:SHOW],
        "removed_n": len(removed),
    }


def diff_map(
    a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return {"verdict": INSUFFICIENT, "why": "the map is absent on one side"}
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = [
        {"key": k, "from": a[k], "to": b[k]}
        for k in sorted(set(a) & set(b))
        if a[k] != b[k]
    ]
    return {
        "verdict": CHANGED if (added or removed or changed) else UNCHANGED,
        "added": added[:SHOW],
        "added_n": len(added),
        "removed": removed[:SHOW],
        "removed_n": len(removed),
        "changed": changed[:SHOW],
        "changed_n": len(changed),
    }


def diff_facet(
    name: str, a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """One facet, both sides. The same-artifact case is the one worth reading twice: identical
    stamps mean nothing was re-measured, and identical stamps with different numbers mean an
    artifact was rewritten under its own name."""
    if not isinstance(a, dict) and not isinstance(b, dict):
        return {
            "facet": name,
            "verdict": INSUFFICIENT,
            "why": "not measured on either side",
        }
    if not isinstance(a, dict):
        return {
            "facet": name,
            "verdict": INSUFFICIENT,
            "why": "absent from the baseline — a facet that appeared is not a facet that grew",
            "to": (b or {}).get("source"),
        }
    if not isinstance(b, dict):
        return {
            "facet": name,
            "verdict": INSUFFICIENT,
            "why": "absent from the later side — an unread artifact is not a deletion",
            "from": a.get("source"),
        }

    moved = diff_counts(a.get("counts") or {}, b.get("counts") or {})
    head: Dict[str, Any] = {
        "facet": name,
        "from": a.get("source"),
        "to": b.get("source"),
        "from_captured_at": a.get("captured_at"),
        "to_captured_at": b.get("captured_at"),
    }
    if a.get("source") == b.get("source"):
        if moved:
            head.update(
                {
                    "verdict": INCONSISTENT,
                    "why": "the same artifact stamp holds different numbers on the two sides — it "
                    "was rewritten in place, so no delta from it can be trusted",
                    "counts": moved,
                }
            )
        else:
            head.update(
                {
                    "verdict": INSUFFICIENT,
                    "why": "the same artifact on both sides — this facet has not been "
                    "re-measured since the baseline; run its producer first",
                }
            )
        return head

    sets = {
        key: diff_set((a.get("sets") or {}).get(key), (b.get("sets") or {}).get(key))
        for key in sorted(set(a.get("sets") or {}) | set(b.get("sets") or {}))
    }
    maps = {
        key: diff_map((a.get("maps") or {}).get(key), (b.get("maps") or {}).get(key))
        for key in sorted(set(a.get("maps") or {}) | set(b.get("maps") or {}))
    }
    changed = bool(moved) or any(
        d.get("verdict") == CHANGED for d in list(sets.values()) + list(maps.values())
    )
    head.update(
        {
            "verdict": CHANGED if changed else UNCHANGED,
            "counts": moved,
            "sets": sets,
            "maps": maps,
        }
    )
    return head


def diff_all(
    before: Dict[str, Any],
    after: Dict[str, Any],
    labels: Tuple[str, str] = ("baseline", "now"),
) -> Dict[str, Any]:
    facets = [
        diff_facet(name, before.get(name), after.get(name))
        for name, _ in FACETS
        if name in before or name in after
    ]
    verdicts = [f["verdict"] for f in facets]
    if INCONSISTENT in verdicts:
        overall = INCONSISTENT
    elif CHANGED in verdicts:
        overall = CHANGED
    elif verdicts and all(v == INSUFFICIENT for v in verdicts):
        overall = INSUFFICIENT
    elif not verdicts:
        overall = INSUFFICIENT
    else:
        overall = UNCHANGED
    return {
        "schema": SCHEMA,
        "kind": "diff",
        "from": labels[0],
        "to": labels[1],
        "verdict": overall,
        "measured": sum(1 for v in verdicts if v in (CHANGED, UNCHANGED)),
        "insufficient": [f["facet"] for f in facets if f["verdict"] == INSUFFICIENT],
        "facets": facets,
    }


# ---------------------------------------------------------------------------------------------
# Sessions — which department this pack can honestly serve
# ---------------------------------------------------------------------------------------------
def probe_n(rows: Sequence[Dict[str, Any]], kind: str, key: str) -> int:
    if kind == "corpus":
        return len(rows)
    if kind == "category":
        return sum(1 for r in rows if str(r.get("category") or "") == key)
    if kind == "integration":
        return sum(1 for r in rows if key in (r.get("integrations") or []))
    return 0


def decide(
    dept: Dict[str, Any],
    rows: Sequence[Dict[str, Any]],
    template_ids: Sequence[str],
    corroborated: int = 0,
) -> Dict[str, Any]:
    """One department, decided from two cohort sizes and one rule.

    Order matters: an already-served department is refused before its cohorts are weighed, because
    the reason not to build it is coverage, not evidence.
    """
    spine_n, spine_via = 0, "nothing to probe"
    for kind, key in dept.get("spine") or ():
        n = probe_n(rows, kind, key)
        if n > spine_n:
            spine_n, spine_via = n, "{} {}".format(kind, key).strip()
    pattern = dept.get("shape")
    shape_n = (
        sum(1 for r in rows if re.search(pattern, str(r.get("name") or ""), re.I))
        if pattern
        else 0
    )

    have = set(template_ids)
    served = tuple(dept.get("served_by") or ())
    problems: List[str] = []
    missing_served = [t for t in served if t not in have]

    if served and not missing_served:
        decision, reason = (
            "REFUSE-SERVED",
            "already served by {}; a pack that duplicates its own coverage is worse than one "
            "with gaps".format(", ".join(served)),
        )
    elif missing_served:
        decision, reason = (
            "REFUSE-SERVED",
            "claimed served by {} — and {} is not on disk".format(
                ", ".join(served), ", ".join(missing_served)
            ),
        )
        problems.append(
            "{}: claims coverage by absent template(s) {}".format(
                dept["session"], ", ".join(missing_served)
            )
        )
    elif spine_n < SPINE_MIN:
        decision, reason = (
            "REFUSE-NO-SPINE",
            "spine cohort is {} Bots ({}), under the {} floor — nothing to derive a shape from".format(
                spine_n, spine_via, SPINE_MIN
            ),
        )
    elif shape_n < SHAPE_MIN:
        decision, reason = (
            "REFUSE-NO-SHAPE",
            "spine is {} Bots but only {} name this job, under the {} floor — a template here "
            "would be invented, not measured".format(spine_n, shape_n, SHAPE_MIN),
        )
    else:
        decision, reason = (
            "BUILD",
            "spine {} Bots ({}), shape {} Bots — both floors cleared".format(
                spine_n, spine_via, shape_n
            ),
        )

    template = dept.get("template")
    if decision == "BUILD" and not template:
        problems.append(
            "{}: cleared both floors and names no template — the pack has an unfilled slot".format(
                dept["session"]
            )
        )
    if decision == "BUILD" and template and template not in have:
        problems.append(
            "{}: decided BUILD and template {!r} is not on disk".format(
                dept["session"], template
            )
        )
    if decision != "BUILD" and template:
        problems.append(
            "{}: refused ({}) yet ships template {!r}".format(
                dept["session"], decision, template
            )
        )
    return {
        "session": dept["session"],
        "spine": {"n": spine_n, "via": spine_via},
        "shape": {"n": shape_n, "pattern": pattern},
        "corroborated_posts": corroborated,
        "decision": decision,
        "reason": reason,
        "template": template,
        "served_by": list(served),
        "note": dept.get("note", ""),
        "problems": problems,
    }


def template_ids(root: pathlib.Path) -> List[str]:
    box = root / "templates"
    if not box.is_dir():
        return []
    return sorted(p.stem for p in box.glob("*.json"))


def sessions(root: pathlib.Path) -> Dict[str, Any]:
    _, corpus_doc = newest(root, "usecases")
    rows = rows_of(corpus_doc)
    _, x_doc = newest(root, "x")
    gal = [r for r in rows_of(x_doc) if _GALAXY_RX.search(row_text(r))]
    corr = corroboration(gal)
    have = template_ids(root)
    out = [decide(d, rows, have, corr.get(d["session"], 0)) for d in DEPARTMENTS]
    return {
        "schema": SCHEMA,
        "kind": "sessions",
        "event": EVENT,
        "corpus_bots": len(rows),
        "corpus_measured": rel(root, newest(root, "usecases")[0]),
        "galaxy_posts": len(gal),
        "floors": {"spine_min": SPINE_MIN, "shape_min": SHAPE_MIN},
        "built": [r["session"] for r in out if r["decision"] == "BUILD"],
        "refused": [r["session"] for r in out if r["decision"] != "BUILD"],
        "problems": [p for r in out for p in r["problems"]],
        "rows": out,
    }


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------
def render_sessions(doc: Dict[str, Any]) -> str:
    out = [
        "GALAXY {} · {} · {}".format(EVENT["dates"], EVENT["hours"], EVENT["where"]),
        "REPLAY  {}".format(EVENT["replay"]),
        "",
        "{} announced department(s) judged against {} corpus Bots ({}); floors spine>={} shape>={}".format(
            len(doc["rows"]),
            doc["corpus_bots"],
            doc["corpus_measured"],
            SPINE_MIN,
            SHAPE_MIN,
        ),
        "",
    ]
    for row in doc["rows"]:
        out.append(
            "{:<17} {:<15} spine {:>4} ({})  shape {:>3}  galaxy-posts {}".format(
                row["session"],
                row["decision"],
                row["spine"]["n"],
                row["spine"]["via"],
                row["shape"]["n"],
                row["corroborated_posts"],
            )
        )
        out.append("                  {}".format(row["reason"]))
        if row["template"]:
            out.append(
                "                  template: templates/{}.json".format(row["template"])
            )
    out.append("")
    out.append(
        "BUILT    {}".format(", ".join(doc["built"]) if doc["built"] else "— none")
    )
    out.append(
        "REFUSED  {}".format(", ".join(doc["refused"]) if doc["refused"] else "— none")
    )
    if doc["problems"]:
        out.append("")
        out.append("PROBLEMS")
        for p in doc["problems"]:
            out.append("  ! {}".format(p))
    return "\n".join(out)


def render_baseline(doc: Dict[str, Any], where: Optional[str]) -> str:
    out = ["BASELINE {}".format(doc["captured_at"])]
    if where:
        out.append("wrote {}".format(where))
    out.append("")
    for name, _ in FACETS:
        f = doc["facets"].get(name)
        if not isinstance(f, dict):
            out.append("{:<13} UNMEASURED — no artifact on disk".format(name))
            continue
        counts = flat(f.get("counts") or {})
        head = ", ".join("{}={}".format(k, counts[k]) for k in list(sorted(counts))[:6])
        out.append("{:<13} {}".format(name, f.get("source")))
        out.append("              {}".format(head or "no numeric counts"))
    built = doc.get("sessions", {}).get("built") or []
    out.append("")
    out.append(
        "SESSIONS      built {} · refused {}".format(
            len(built), len(doc.get("sessions", {}).get("refused") or [])
        )
    )
    return "\n".join(out)


def render_diff(doc: Dict[str, Any]) -> str:
    out = [
        "DIFF {} -> {}".format(doc["from"], doc["to"]),
        "VERDICT {} · {} facet(s) re-measured · {} insufficient".format(
            doc["verdict"], doc["measured"], len(doc["insufficient"])
        ),
        "",
    ]
    for f in doc["facets"]:
        out.append("{:<13} {}".format(f["facet"], f["verdict"]))
        if f.get("why"):
            out.append("              {}".format(f["why"]))
        for moved in f.get("counts") or []:
            out.append(
                "              {} {} -> {}{}".format(
                    moved["key"],
                    moved["from"],
                    moved["to"],
                    (
                        "  ({:+})".format(moved["delta"])
                        if isinstance(moved["delta"], (int, float))
                        else ""
                    ),
                )
            )
        for key, d in (f.get("sets") or {}).items():
            if d.get("verdict") == CHANGED:
                out.append(
                    "              set {} {} -> {}  +{} -{}".format(
                        key,
                        d.get("n_from"),
                        d.get("n_to"),
                        d["added_n"],
                        d["removed_n"],
                    )
                )
                if d["added"]:
                    out.append("                + {}".format(", ".join(d["added"])))
                if d["removed"]:
                    out.append("                - {}".format(", ".join(d["removed"])))
            elif d.get("verdict") == INSUFFICIENT:
                out.append(
                    "              set {} INSUFFICIENT — {}".format(key, d.get("why"))
                )
        for key, d in (f.get("maps") or {}).items():
            if d.get("verdict") == CHANGED:
                out.append(
                    "              map {} +{} -{} ~{}".format(
                        key, d["added_n"], d["removed_n"], d["changed_n"]
                    )
                )
                for ch in d["changed"]:
                    out.append(
                        "                ~ {}: {} -> {}".format(
                            ch["key"], ch["from"], ch["to"]
                        )
                    )
                if d["added"]:
                    out.append(
                        "                + {}".format(", ".join(map(str, d["added"])))
                    )
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------
# Selftest — inline fixtures, and a known-bad leg for every rule that matters
# ---------------------------------------------------------------------------------------------
def _fx(
    source: str,
    counts: Optional[Dict[str, Any]] = None,
    sets: Optional[Dict[str, Any]] = None,
    maps: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "source": source,
        "captured_at": source,
        "counts": counts or {"rows": 10},
        "sets": sets or {},
        "maps": maps or {},
    }


def _corpus_rows(
    n_sales: int, n_named: int, prefix: str = "Outbound"
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for i in range(n_sales):
        rows.append(
            {
                "name": "{} {}".format(prefix if i < n_named else "Quiet", i),
                "category": "Sales",
                "integrations": ["LinkedIn"] if i < 25 else [],
                "prompt_chars": 600 + i,
                "has_approval_language": i % 2 == 0,
            }
        )
    return rows


def selftest() -> int:
    """Each leg plants ONE defect or asserts ONE property, so a pass names a rule rather than a
    mood. The known-bad legs are the point: a diff tool that cannot tell "did not move" from "was
    never re-measured" is worse than no diff tool, because it produces a confident wrong answer
    on the exact day the answer matters.
    """
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    # 1 — a real move is reported, with the names.
    a = _fx("github/A.json", {"rows": 452}, {"repos": setfacet(["a/one", "b/two"])})
    b = _fx("github/B.json", {"rows": 461}, {"repos": setfacet(["b/two", "c/three"])})
    d = diff_facet("github", a, b)
    leg(
        "count-delta-reported",
        d["verdict"] == CHANGED
        and any(m["key"] == "rows" and m["delta"] == 9 for m in d["counts"]),
        str(d.get("counts")),
    )
    leg(
        "set-membership-reported",
        d["sets"]["repos"]["added"] == ["c/three"]
        and d["sets"]["repos"]["removed"] == ["a/one"],
        str(d["sets"]["repos"]),
    )

    # 2 — KNOWN BAD: the same artifact on both sides is NOT evidence of stability.
    same = diff_facet("github", a, _fx("github/A.json", {"rows": 452}, a["sets"]))
    leg(
        "same-artifact-is-insufficient",
        same["verdict"] == INSUFFICIENT and "re-measured" in same["why"],
        str(same),
    )

    # 3 — KNOWN BAD: same stamp, different numbers = an artifact rewritten in place.
    rewritten = diff_facet("github", a, _fx("github/A.json", {"rows": 999}))
    leg(
        "rewritten-artifact-is-inconsistent",
        rewritten["verdict"] == INCONSISTENT,
        str(rewritten.get("verdict")),
    )

    # 4 — KNOWN BAD: a facet absent on one side must not read as a wholesale deletion.
    one_sided = diff_facet("market", a, None)
    leg(
        "absent-facet-is-not-a-deletion",
        one_sided["verdict"] == INSUFFICIENT and "sets" not in one_sided,
        str(one_sided),
    )

    # 5 — KNOWN BAD: a truncated set must refuse rather than invent additions.
    big_a = setfacet(["r{:04d}".format(i) for i in range(SET_CAP + 30)])
    big_b = setfacet(["r{:04d}".format(i) for i in range(20, SET_CAP + 90)])
    trunc = diff_set(big_a, big_b)
    leg(
        "truncated-set-refuses",
        trunc["verdict"] == INSUFFICIENT
        and big_a["truncated"]
        and "added" not in trunc,
        str(trunc),
    )
    leg(
        "uncapped-set-still-diffs",
        diff_set(setfacet(["x"]), setfacet(["x", "y"]))["added"] == ["y"],
        "a cap that disabled every set diff would pass the leg above",
    )

    # 6 — map changes (a plugin's git ref moving is the whole reason maps exist).
    m = diff_map({"p": "sha1", "q": "sha9"}, {"p": "sha2", "r": "sha3"})
    leg(
        "map-change-reported",
        m["verdict"] == CHANGED
        and m["changed"] == [{"key": "p", "from": "sha1", "to": "sha2"}]
        and m["added"] == ["r"]
        and m["removed"] == ["q"],
        str(m),
    )
    leg(
        "identical-map-unchanged",
        diff_map({"p": "sha1"}, {"p": "sha1"})["verdict"] == UNCHANGED,
        "a map diff that always fires would pass the leg above",
    )

    # 7 — the lone-baseline case: everything insufficient, overall INSUFFICIENT.
    lone = diff_all({"github": a}, {"github": a})
    leg(
        "lone-baseline-overall-insufficient",
        lone["verdict"] == INSUFFICIENT and lone["insufficient"] == ["github"],
        str(lone["verdict"]),
    )
    leg(
        "real-move-overall-changed",
        diff_all({"github": a}, {"github": b})["verdict"] == CHANGED,
        "an overall verdict stuck on INSUFFICIENT would pass the leg above",
    )
    leg(
        "inconsistent-dominates",
        diff_all(
            {"github": a, "mcp": _fx("mcp/A.json")},
            {"github": _fx("github/A.json", {"rows": 1}), "mcp": _fx("mcp/B.json")},
        )["verdict"]
        == INCONSISTENT,
        "a rewritten artifact anywhere must not be masked by a clean facet",
    )

    # 8 — KNOWN BAD: an empty corpus must not answer with a median.
    leg(
        "empty-corpus-is-unmeasured",
        facet_corpus(pathlib.Path("/nonexistent-galaxy-root")) is None,
        "an absent corpus must be None, never a zero median",
    )

    # 9 — sessions: the floors do the deciding.
    rows = _corpus_rows(59, 14)
    built = decide(
        {
            "session": "SDRs",
            "spine": (("category", "Sales"),),
            "shape": r"outbound",
            "served_by": (),
            "template": "galaxy-sdr-desk",
        },
        rows,
        ["galaxy-sdr-desk"],
    )
    leg("spine-and-shape-clear-builds", built["decision"] == "BUILD", str(built))
    thin_shape = decide(
        {
            "session": "Post-Sales",
            "spine": (("category", "Sales"),),
            "shape": r"renewal",
            "served_by": (),
            "template": None,
        },
        rows,
        [],
    )
    leg(
        "no-shape-refuses",
        thin_shape["decision"] == "REFUSE-NO-SHAPE" and not thin_shape["problems"],
        str(thin_shape),
    )
    no_spine = decide(
        {
            "session": "Founders",
            "spine": (),
            "shape": r"outbound",
            "served_by": (),
            "template": None,
        },
        rows,
        [],
    )
    leg("no-spine-refuses", no_spine["decision"] == "REFUSE-NO-SPINE", str(no_spine))
    served = decide(
        {
            "session": "Sales",
            "spine": (("category", "Sales"),),
            "shape": r"outbound",
            "served_by": ("stale-deal-sweep",),
            "template": None,
        },
        rows,
        ["stale-deal-sweep"],
    )
    leg(
        "served-refuses-before-cohorts",
        served["decision"] == "REFUSE-SERVED" and not served["problems"],
        str(served),
    )

    # 10 — KNOWN BAD: a BUILD whose template is not on disk is a broken pack, not a pass.
    orphan = decide(
        {
            "session": "SDRs",
            "spine": (("category", "Sales"),),
            "shape": r"outbound",
            "served_by": (),
            "template": "galaxy-missing",
        },
        rows,
        [],
    )
    leg(
        "build-without-template-flags",
        orphan["decision"] == "BUILD"
        and any("not on disk" in p for p in orphan["problems"]),
        str(orphan["problems"]),
    )
    phantom = decide(
        {
            "session": "Marketing",
            "spine": (("category", "Sales"),),
            "shape": r"outbound",
            "served_by": ("no-such-template",),
            "template": None,
        },
        rows,
        [],
    )
    leg(
        "served-by-absent-template-flags",
        phantom["problems"] and "absent template" in phantom["problems"][0],
        str(phantom["problems"]),
    )

    # 11 — corroboration counts a department only inside a Galaxy post.
    posts = [
        {"title": "Grok Bot Galaxy covers engineering and sales", "summary": ""},
        {"title": "unrelated engineering thread", "summary": "no event here"},
    ]
    gal = [p for p in posts if _GALAXY_RX.search(row_text(p))]
    corr = corroboration(gal)
    leg(
        "corroboration-scoped-to-galaxy",
        corr["Engineering"] == 1 and corr["Sales"] == 1 and corr["Founders"] == 0,
        str(corr),
    )

    # 12 — topic counting is per-row presence, not per-occurrence.
    topics = topic_counts(
        [
            {"title": "routine routine routine", "summary": ""},
            {"title": "nothing", "summary": "approval gates"},
        ]
    )
    leg(
        "topics-count-rows-not-hits",
        topics["routine"] == 1 and topics["approval"] == 1 and topics["mcp"] == 0,
        str(topics),
    )

    # 13 — positive control on the real tree: the shipped pack decides cleanly.
    root = pathlib.Path(__file__).resolve().parents[1]
    live = sessions(root)
    leg(
        "repo-sessions-has-no-problems",
        not live["problems"],
        "; ".join(live["problems"]) or "clean",
    )
    leg(
        "repo-sessions-builds-something",
        len(live["built"]) >= 1 and len(live["refused"]) >= 1,
        "built={} refused={}".format(live["built"], live["refused"]),
    )

    passed = sum(1 for _, ok, _ in legs if ok)
    for name, ok, detail in legs:
        if not ok:
            emit("  FAIL {} — {}".format(name, detail))
        else:
            emit("  ok   {}".format(name))
    if passed != len(legs):
        emit("SELFTEST FAIL - {}/{}".format(passed, len(legs)))
        return 1
    emit("SELFTEST PASS - {}/{}".format(passed, len(legs)))
    return 0


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
VERBS = ("baseline", "diff", "sessions")


def read_baseline(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    doc = load(path)
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None
    facets = doc.get("facets")
    return doc if isinstance(facets, dict) else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(
        prog="gb-galaxy.py", description=(__doc__ or "").split("\n\n")[0]
    )
    ap.add_argument("verb", nargs="?", choices=VERBS, help="baseline | diff | sessions")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--dry-run", action="store_true", help="baseline: write nothing")
    ap.add_argument(
        "--from", dest="from_", help="diff: baseline artifact (default: newest)"
    )
    ap.add_argument(
        "--to",
        help="diff: the later side, another baseline artifact (default: live artifacts)",
    )
    ap.add_argument("--root", default=str(root), help="artifact root")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return selftest()
    root = pathlib.Path(args.root).resolve()
    if not args.verb:
        ap.print_help()
        return 2

    if args.verb == "sessions":
        doc = sessions(root)
        emit(json.dumps(doc, indent=1) if args.json else render_sessions(doc))
        return 1 if doc["problems"] else 0

    if args.verb == "baseline":
        doc = {
            "schema": SCHEMA,
            "kind": "baseline",
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "generator": "bin/gb-galaxy.py",
            "event": EVENT,
            "sessions": sessions(root),
            "facets": fingerprint(root),
        }
        measured = [k for k, v in doc["facets"].items() if isinstance(v, dict)]
        if not measured:
            warn(
                "ERROR no artifacts under {} — run the producers first; an empty baseline is "
                "worse than none".format(root)
            )
            return 2
        where = None
        if not args.dry_run:
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M")
            out = root / "galaxy" / "{}.json".format(stamp)
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(out, doc)
            where = rel(root, out)
        emit(json.dumps(doc, indent=1) if args.json else render_baseline(doc, where))
        return 0

    # diff
    if args.from_:
        base_path = pathlib.Path(args.from_)
    else:
        rows = dated_children(root / "galaxy", ".json")
        if not rows:
            warn(
                "ERROR no baseline under {} — run `gb-galaxy.py baseline` before the stream, "
                "then diff after it".format(root / "galaxy")
            )
            return 2
        base_path = rows[-1]
    base = read_baseline(base_path)
    if base is None:
        warn("ERROR {} is not a {} baseline artifact".format(base_path, SCHEMA))
        return 2

    if args.to:
        later = read_baseline(pathlib.Path(args.to))
        if later is None:
            warn("ERROR {} is not a {} baseline artifact".format(args.to, SCHEMA))
            return 2
        after, label = later["facets"], rel(root, pathlib.Path(args.to)) or str(args.to)
    else:
        after, label = fingerprint(root), "live artifacts"

    doc = diff_all(
        base["facets"], after, (rel(root, base_path) or str(base_path), label)
    )
    emit(json.dumps(doc, indent=1) if args.json else render_diff(doc))
    return 1 if doc["verdict"] == INCONSISTENT else 0


if __name__ == "__main__":
    gbmain(main)
