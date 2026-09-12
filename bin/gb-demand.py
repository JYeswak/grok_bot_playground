#!/usr/bin/env python3
"""gb-demand — which Bots SHOULD exist, ranked by demand nobody is serving.

Three populations already on disk, joined for the first time here:

  DEMAND    `usecases/` — 645 attributed Bots, 294 distinct integrations named across them
  SUPPLY    `market/`   — the 325-plugin connector catalog a Bot can actually install
  FRINGE    `github/`   — the 220 tracked `kind:mcp-server` repos (a server exists in the wild,
                          but nothing in the product installs it)

An integration named by many Bots and served by no connector is a gap. That much was already
claimed. Two corrections this file exists to make, both measured rather than argued:

1. THE POPULATION WAS WRONG. The prior figure — "249 of 294 unserved" — matched each integration
   name as a naive substring of `title + summary` across the 220 GitHub MCP repos. It reproduces
   here EXACTLY (`rank` prints it), and it is still the wrong number, because those 220 repos are
   not what serves a Grok Bot: the 325-plugin catalog is. Measured against the catalog the two
   methods land 8 apart and agree on only 210 of 294 names: 37 the GitHub-repo method called
   unserved have shipping connectors (`Granola`, `Gong`, `Airtable`, `Linear`, `Figma`, …), 2
   are surfaces the account already IS, and 31 it called SERVED are served by nothing at all
   (`Google Maps`, `Google Search Console`, `LinkedIn`, …) because prose mentioning a name is
   not a server serving it. Two errors pointing opposite ways is not accuracy, and `rank`
   prints the whole disagreement rather than the totals that hide it.

2. CITATIONS ARE NOT DEMAND. 179 of the unserved integrations are named by exactly one Bot, and
   ten of the loudest are named repeatedly by a SINGLE builder — one of them by the vendor's own
   account. Ranking by citation count therefore ranks self-promotion. This file ranks by DISTINCT
   BUILDERS who independently reached for the same missing thing, reports the citation count
   beside it, and flags any row whose citations outrun its builders.

  gb-demand.py rank                 # the ranked gap table, with the correction printed
  gb-demand.py rank --all           # every unserved integration, including the one-builder noise
  gb-demand.py gaps --json          # the same join, machine-readable
  gb-demand.py --selftest           # every rule above fires on a known-bad fixture
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import main as gbmain, read_json_capped  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Surfaces the account already IS. `Grok` is named by 213 of the 645 Bots and no connector will
# ever serve it, because it is the model, not a vendor. Counting it as a gap would put the
# largest number in the corpus at the top of a list of things to build.
NATIVE: Set[str] = {"grok", "grok bot", "grok bots", "grok imagine", "grokipedia"}

# Below this many DISTINCT builders an integration is one person's habit, not a market. Three is
# where the corpus itself breaks: 179 unserved names have one citation, 36 have two or more
# builders, 16 have three or more. The cliff is real and this is the far side of it.
MIN_BUILDERS = 3
# One account holding at least HALF an integration's citations is speaking for it alone.
# Measured: 26 of the 52 integrations with 3+ cites clear this bar. Replaces a cites/builders
# density of 2.0, which flagged Gmail (top builder 14%) and LinkedIn (30%) as self-cited.
DOMINATED_AT = 0.50
# Shorter than this, a name is a letter, and containment matching stops meaning anything: `X`
# would "match" exa, box, and every summary containing the word next. Short names are still
# ranked — they are only held to exact-equality matching.
MIN_CONTAINMENT_CHARS = 4


# ---------------------------------------------------------------------------------------------
# Name normalisation. One resource must not be two rows because one side wrote a hyphen.
# ---------------------------------------------------------------------------------------------
def slugify(name: str) -> str:
    """`Google Calendar` -> `google-calendar`. The catalog's own spelling."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def squash(name: str) -> str:
    """`Customer.io` -> `customerio`. Catches the separator disagreements slugify keeps."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


# ---------------------------------------------------------------------------------------------
# Supply: does anything actually serve this name?
# ---------------------------------------------------------------------------------------------
def catalog_hits(name: str, plugin_names: Sequence[str]) -> List[str]:
    """Catalog plugins that serve `name`.

    Equality on either spelling, or containment at a SLUG-TOKEN BOUNDARY — `notion` serves
    `notion-workspace`, and `x` serves nothing it merely appears inside. The boundary is the
    whole rule: substring matching is what let the prior pass score `X` as served.
    """
    s, c = slugify(name), squash(name)
    if not c:
        return []
    hits: List[str] = []
    for raw in plugin_names:
        ps, pc = slugify(raw), squash(raw)
        if ps == s or pc == c:
            hits.append(raw)
        elif len(c) >= MIN_CONTAINMENT_CHARS and (
            ps.startswith(s + "-") or ps.endswith("-" + s) or ("-" + s + "-") in ps
        ):
            hits.append(raw)
    return hits


def mcp_hits(name: str, mcp_rows: Sequence[Dict[str, Any]]) -> List[str]:
    """Tracked `kind:mcp-server` repos naming this integration in their SLUG or TOPICS.

    Deliberately not the summary. A repo whose prose mentions Slack is not a Slack server, and
    reading prose is exactly how the prior figure went wrong in both directions.
    """
    s, c = slugify(name), squash(name)
    if len(c) < MIN_CONTAINMENT_CHARS:
        return []
    hits: List[str] = []
    for row in mcp_rows:
        title = str(row.get("title") or "")
        path = slugify(title)
        toks: Set[str] = set(re.split(r"[-/ ]+", path))
        for topic in (row.get("signals") or {}).get("topics") or []:
            toks |= set(slugify(str(topic)).split("-"))
        if s in toks or c in {squash(t) for t in toks}:
            hits.append(title)
        elif ("-" + s + "-") in ("-" + path + "-"):
            hits.append(title)
    return hits


def naive_unserved(
    names: Sequence[str], mcp_rows: Sequence[Dict[str, Any]]
) -> List[str]:
    """THE PRIOR METHOD, kept executable so the correction is a diff and not an assertion.

    `integration name absent from title + summary of the 220 kind:mcp-server rows`, verbatim
    from `duel/WIZARD_IDEAS_DATA.md`. Reproducing a number you intend to replace is the only
    way to show you replaced the right one.
    """
    blob = [
        (str(r.get("title") or "") + " " + str(r.get("summary") or "")).lower()
        for r in mcp_rows
    ]
    return [n for n in names if not any(n.lower() in b for b in blob)]


# ---------------------------------------------------------------------------------------------
# Demand: who named it, and how many separate people were they?
# ---------------------------------------------------------------------------------------------
def tally(
    rows: Sequence[Dict[str, Any]],
) -> Tuple[
    "collections.Counter[str]",
    Dict[str, Set[str]],
    Dict[str, "collections.Counter[str]"],
    Dict[str, "collections.Counter[str]"],
]:
    """Citations, distinct builders, and category mix per integration.

    The two counts are kept apart on purpose. `Google Docs` is cited 20 times by 7 people and
    `Google Sheets` 9 times by 9; only the second number says nine people independently hit the
    same wall.
    """
    cites: "collections.Counter[str]" = collections.Counter()
    builders: Dict[str, Set[str]] = collections.defaultdict(set)
    # Per-builder counts, not just the distinct set: dominance needs to know whether ONE account
    # holds most of an integration's citations. A set can only answer "how many people", and
    # "how many people" is exactly the question that made Salesforce (4 builders) look like
    # broad demand when one of them holds 53% of it.
    by_builder: Dict[str, "collections.Counter[str]"] = collections.defaultdict(
        collections.Counter
    )
    cats: Dict[str, "collections.Counter[str]"] = collections.defaultdict(
        collections.Counter
    )
    for row in rows:
        who = str(row.get("contributor") or "").strip().lower()
        cat = str(row.get("category") or "?")
        seen: Set[str] = set()
        for integ in row.get("integrations") or []:
            if not isinstance(integ, str) or not integ.strip():
                continue
            integ = integ.strip()
            if integ in seen:  # one Bot naming a thing twice is still one Bot
                continue
            seen.add(integ)
            cites[integ] += 1
            if who:
                builders[integ].add(who)
                by_builder[integ][who] += 1
            cats[integ][cat] += 1
    return cites, builders, cats, by_builder


def classify(
    cites: "collections.Counter[str]",
    builders: Dict[str, Set[str]],
    cats: Dict[str, "collections.Counter[str]"],
    by_builder: Dict[str, "collections.Counter[str]"],
    plugin_names: Sequence[str],
    mcp_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """One row per named integration, with its serving status and the evidence for it."""
    out: List[Dict[str, Any]] = []
    for name in cites:
        nb = len(builders.get(name, ()))
        cat = cats.get(name) or collections.Counter()
        # CONCENTRATION, corrected 2026-09-11 after three independent derivations agreed on the
        # finding and then disagreed with this metric. `cites / builders` is a DENSITY, not a
        # dominance measure, and it misclassifies in both directions: Gmail scores 2.03 (69
        # cites / 34 builders) and LinkedIn exactly 2.00, so a >= 2.0 flag calls both
        # "self-cited" when their top builder holds only 14% and 30%. What actually matters is
        # whether ONE account is speaking for the integration, which is the top builder's SHARE.
        # Measured: of the 52 integrations with 3+ cites, 26 have a single builder holding >= 50%
        # — Salesforce 15/4 at 53%, Granola 10/4 at 70%, Gong 7/2 at 57%, Notion 30/11 at 50%.
        # So "15 Bots use Salesforce" is not fifteen people reaching for CRM tooling.
        counts = by_builder.get(name) or {}
        top_n = max(counts.values()) if counts else 0
        top_who = max(counts, key=lambda k: counts[k]) if counts else None
        share = (top_n / cites[name]) if cites[name] else 0.0
        row: Dict[str, Any] = {
            "integration": name,
            "cites": cites[name],
            "builders": nb,
            "concentration": round(cites[name] / nb, 2) if nb else None,
            "top_builder": top_who,
            "top_builder_share": round(share, 3),
            "top_category": (cat.most_common(1) or [("?", 0)])[0][0],
            "categories": dict(cat),
        }
        if name.lower() in NATIVE:
            row["status"], row["evidence"] = "native", "the account's own model surface"
        else:
            ch = catalog_hits(name, plugin_names)
            if ch:
                row["status"] = "catalog"
                row["evidence"] = "plugin " + ", ".join(sorted(ch)[:2])
            else:
                mh = mcp_hits(name, mcp_rows)
                if mh:
                    row["status"] = "mcp-only"
                    row["evidence"] = "repo " + sorted(mh)[0]
                else:
                    row["status"] = "unserved"
                    row["evidence"] = "nothing installable, no tracked repo"
        # A row is "one account speaking for the integration" when its top builder holds at
        # least half the citations — not when the density crosses an arbitrary 2.0.
        row["self_cited"] = bool(share >= DOMINATED_AT)
        out.append(row)
    return out


def gap_rank(rows: Sequence[Dict[str, Any]], min_builders: int) -> List[Dict[str, Any]]:
    """The gaps worth building for: nothing installable serves them, and enough separate people
    reached for them that the wall is not one person's.

    Sorted by BUILDERS first. Citations break ties, never lead — that ordering is the whole
    correction, and swapping the keys back puts `Google Docs` (20 cites, 7 people) above
    `Google Sheets` (9 cites, 9 people), which is the wrong instruction to give a builder.
    """
    gaps = [
        r
        for r in rows
        if r["status"] in ("unserved", "mcp-only") and r["builders"] >= min_builders
    ]
    gaps.sort(key=lambda r: (-r["builders"], -r["cites"], r["integration"]))
    return gaps


def summarise(rows: Sequence[Dict[str, Any]], prior: Sequence[str]) -> Dict[str, Any]:
    """The buckets, plus how far the prior method's MEMBERSHIP is from this one's.

    The headline counts land within a few of each other, which is the trap: they agree on 210
    names and disagree on 70, in both directions at once. Reporting only the totals would let
    two cancelling errors read as corroboration.
    """
    by = collections.Counter(r["status"] for r in rows)
    unserved = [r for r in rows if r["status"] in ("unserved", "mcp-only")]
    mine = {r["integration"] for r in unserved}
    catalog = {r["integration"] for r in rows if r["status"] == "catalog"}
    native = {r["integration"] for r in rows if r["status"] == "native"}
    old = set(prior)
    return {
        "distinct_integrations": len(rows),
        "citations": sum(r["cites"] for r in rows),
        "native_surfaces": by["native"],
        "served_by_catalog": by["catalog"],
        "mcp_repo_only": by["mcp-only"],
        "served_by_nothing": by["unserved"],
        "unserved_by_catalog": by["unserved"] + by["mcp-only"],
        "unserved_single_citation": sum(1 for r in unserved if r["cites"] == 1),
        "unserved_multi_builder": sum(1 for r in unserved if r["builders"] >= 2),
        "real_gaps": sum(1 for r in unserved if r["builders"] >= MIN_BUILDERS),
        "prior_method_unserved": len(old),
        "prior_agrees": len(old & mine),
        # a gap the prior method claimed, against a connector that actually ships
        "prior_false_gap": sorted(old & catalog),
        # ...or against a surface the account already IS, which no connector will ever serve
        "prior_false_gap_native": sorted(old & native),
        # a gap it missed, because prose containing the name is not a server serving it
        "prior_missed_gap": sorted(mine - old),
    }


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------
def emit(text: str) -> None:
    """`print`, except that a reader who walked away is not a failed measurement."""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass


def render_rank(
    gaps: Sequence[Dict[str, Any]], summary: Dict[str, Any], min_builders: int
) -> str:
    s = summary
    out = [
        "DEMAND NOBODY SERVES — {d} integrations named by {n} attributed Bots".format(
            d=s["distinct_integrations"], n=s["citations"]
        ),
        "",
        "  supply    {c:>4} served by a catalog plugin   {m:>4} only by a tracked MCP repo".format(
            c=s["served_by_catalog"], m=s["mcp_repo_only"]
        ),
        "            {u:>4} served by neither             {v:>4} native surfaces (not connectors)".format(
            u=s["served_by_nothing"], v=s["native_surfaces"]
        ),
        "  gap       {g:>4} with NO installable connector".format(
            g=s["unserved_by_catalog"]
        ),
        "  noise     {o:>4} of those are named by exactly one Bot".format(
            o=s["unserved_single_citation"]
        ),
        "  real      {r:>4} are named by {b}+ SEPARATE builders  <- the list below".format(
            r=s["real_gaps"], b=MIN_BUILDERS
        ),
        "",
        "  prior method reproduces EXACTLY at {p} (naive substring vs title+summary of the".format(
            p=s["prior_method_unserved"]
        ),
        "  tracked mcp-server repos) — and is still wrong: it agrees on only {a} names.".format(
            a=s["prior_agrees"]
        ),
        "    {f:>3} it called unserved have shipping connectors  ({fx}, …)".format(
            f=len(s["prior_false_gap"]),
            fx=", ".join(s["prior_false_gap"][:3]) or "none",
        ),
        "    {n:>3} it called unserved are the account's own surfaces ({nx})".format(
            n=len(s["prior_false_gap_native"]),
            nx=", ".join(s["prior_false_gap_native"][:3]) or "none",
        ),
        "    {m:>3} it called served are served by nothing       ({mx}, …)".format(
            m=len(s["prior_missed_gap"]),
            mx=", ".join(s["prior_missed_gap"][:3]) or "none",
        ),
        "  Two errors pointing opposite ways is not accuracy.",
        "",
        "  # integration              bldrs cites conc  category     served by",
        "  " + "-" * 84,
    ]
    if not gaps:
        out.append(
            "  (no gap clears the threshold — either supply caught up or the corpus did)"
        )
    for i, r in enumerate(gaps, 1):
        flag = " *" if r["self_cited"] else "  "
        out.append(
            "  {i:<2} {name:<24} {b:>5} {c:>5} {k:>5}{flag}{cat:<12} {ev}".format(
                i=i,
                name=r["integration"][:24],
                b=r["builders"],
                c=r["cites"],
                k="{:.2f}".format(r["concentration"] or 0.0),
                flag=flag,
                cat=r["top_category"][:12],
                ev=r["evidence"][:36],
            )
        )
    out += [
        "  " + "-" * 84,
        "  bldrs = distinct contributors who named it. conc = cites/builders, shown but NOT the",
        "  flag: it is a density and it called Gmail (top builder 14%) self-cited. `*` marks a",
        "  row whose TOP BUILDER holds >=50% of the citations — one account talking, not a",
        "  market. 26 of the 52 integrations with 3+ cites clear that bar.",
        "  threshold: builders >= {m}.  --all drops it.".format(m=min_builders),
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------
def _newest(d: pathlib.Path) -> Optional[pathlib.Path]:
    rows = sorted(p for p in d.glob("*.json") if p.name[:10].count("-") == 2)
    return rows[-1] if rows else None


def load_inputs(
    root: pathlib.Path,
) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
    """The newest snapshot of each of the three populations. Read, never typed."""
    uc_p, mk_p, gh_p = (_newest(root / s) for s in ("usecases", "market", "github"))
    uc = (read_json_capped(uc_p) if uc_p else None) or {}
    mk = (read_json_capped(mk_p) if mk_p else None) or {}
    gh = (read_json_capped(gh_p) if gh_p else None) or {}
    plugins = [
        str(r.get("name") or "")
        for r in ((mk.get("plugins") or {}).get("rows") or [])
        if str(r.get("name") or "").strip()
    ]
    mcp = [r for r in (gh.get("rows") or []) if r.get("kind") == "mcp-server"]
    return uc, plugins, mcp


def build(root: pathlib.Path, min_builders: int) -> Optional[Dict[str, Any]]:
    uc, plugins, mcp = load_inputs(root)
    rows = uc.get("rows") or []
    if not rows or not plugins:
        return None
    cites, builders, cats, by_builder = tally(rows)
    classified = classify(cites, builders, cats, by_builder, plugins, mcp)
    naive = naive_unserved(sorted(cites), mcp)
    return {
        "schema": "gb-demand/1",
        "inputs": {
            "bots": len(rows),
            "catalog_plugins": len(plugins),
            "tracked_mcp_repos": len(mcp),
        },
        "summary": summarise(classified, naive),
        "gaps": gap_rank(classified, min_builders),
        "all": sorted(
            classified, key=lambda r: (-r["builders"], -r["cites"], r["integration"])
        ),
    }


# ---------------------------------------------------------------------------------------------
# Selftest — inline fixtures only, and every leg plants exactly one defect.
# ---------------------------------------------------------------------------------------------
FIX_PLUGINS = [
    "gmail",
    "notion-workspace",
    "google-calendar",
    "calendly",
    "exa",
    "box",
    "customerio",
    "x",
]
FIX_MCP = [
    {
        "title": "stickerdaniel/linkedin-mcp-server",
        "summary": "A server for LinkedIn profiles.",
        "kind": "mcp-server",
        "signals": {"topics": ["linkedin", "mcp-server"]},
    },
    {
        "title": "acme/notes",
        "summary": "Notes about youtube and sheets and everything else under the sun.",
        "kind": "mcp-server",
        "signals": {"topics": ["notes"]},
    },
]
FIX_BOTS = [
    # Sheets: 3 citations, 3 separate builders. Genuine independent demand.
    {
        "contributor": "ann",
        "category": "Ops",
        "integrations": ["Google Sheets", "Gmail"],
    },
    {"contributor": "bo", "category": "Sales", "integrations": ["Google Sheets"]},
    {"contributor": "cy", "category": "Ops", "integrations": ["Google Sheets"]},
    # YouTube: 4 citations, 4 builders. Outranks Sheets on both.
    {"contributor": "ann", "category": "Marketing", "integrations": ["YouTube"]},
    {"contributor": "bo", "category": "Marketing", "integrations": ["YouTube"]},
    {"contributor": "cy", "category": "Marketing", "integrations": ["YouTube"]},
    {"contributor": "dee", "category": "Personal", "integrations": ["YouTube"]},
    # Loudmouth: 6 citations, ONE builder. Ranks first by cites, must not rank at all.
    {"contributor": "vendor", "category": "Ops", "integrations": ["Loudmouth"]},
    {"contributor": "vendor", "category": "Ops", "integrations": ["Loudmouth"]},
    {"contributor": "vendor", "category": "Ops", "integrations": ["Loudmouth"]},
    {"contributor": "vendor", "category": "Ops", "integrations": ["Loudmouth"]},
    {"contributor": "vendor", "category": "Ops", "integrations": ["Loudmouth"]},
    {"contributor": "vendor", "category": "Ops", "integrations": ["Loudmouth"]},
    # LinkedIn: 3 builders, no catalog plugin, but a real MCP repo exists.
    {"contributor": "ann", "category": "Sales", "integrations": ["LinkedIn"]},
    {"contributor": "bo", "category": "Sales", "integrations": ["LinkedIn"]},
    {"contributor": "cy", "category": "Sales", "integrations": ["LinkedIn"]},
    # Served, and native. Neither is a gap.
    {
        "contributor": "dee",
        "category": "Personal",
        "integrations": ["Calendly", "Grok"],
    },
    {"contributor": "ann", "category": "Personal", "integrations": ["Grok"]},
    # One Bot naming the same thing twice is still one citation.
    {"contributor": "eve", "category": "Ops", "integrations": ["Notion", "Notion"]},
]


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    # 1 — normalisation folds the separator disagreements, and only those.
    leg(
        "slugify/squash fold separators",
        slugify("Google Calendar") == "google-calendar"
        and squash("Customer.io") == "customerio"
        and slugify("Google Calendar") != slugify("Google Calendars"),
    )

    # 2 — exact match on either spelling.
    leg(
        "catalog exact match",
        catalog_hits("Gmail", FIX_PLUGINS) == ["gmail"]
        and catalog_hits("Customer.io", FIX_PLUGINS) == ["customerio"],
    )

    # 3 — token-boundary containment: `notion` does serve `notion-workspace`.
    leg(
        "catalog boundary match",
        catalog_hits("Notion", FIX_PLUGINS) == ["notion-workspace"],
    )

    # 4 — FIRES ON KNOWN BAD. The prior pass's substring matcher scores `X` as served off the
    # letter appearing inside exa and box. Ours must not, and the bad one must.
    naive_x = [p for p in FIX_PLUGINS if squash("X") in squash(p)]
    leg(
        "short name is not a substring (known-bad: naive containment)",
        catalog_hits("X", FIX_PLUGINS) == ["x"] and len(naive_x) > 1,
        "naive matcher claims {} plugins serve X".format(len(naive_x)),
    )

    # 5 — a name absent from the catalog entirely is absent.
    leg("unserved name finds no plugin", catalog_hits("Loudmouth", FIX_PLUGINS) == [])

    # 6 — MCP match reads slug and topics, never the summary prose.
    leg(
        "mcp match is slug+topics, not prose",
        mcp_hits("LinkedIn", FIX_MCP) == ["stickerdaniel/linkedin-mcp-server"]
        and mcp_hits("YouTube", FIX_MCP) == []
        and "youtube" in FIX_MCP[1]["summary"],
    )

    # 7 — the prior method, reproduced: prose matching calls YouTube served. It is the
    #     disagreement that documents the correction.
    naive = naive_unserved(["YouTube", "Loudmouth"], FIX_MCP)
    leg(
        "prior naive method reproduces its own disagreement",
        naive == ["Loudmouth"],
        "naive says YouTube is served by acme/notes prose",
    )

    # 8 — citations and builders are counted separately, and a Bot naming a thing twice is one.
    cites, builders, cats, by_builder = tally(FIX_BOTS)
    leg(
        "tally separates cites from builders",
        cites["Loudmouth"] == 6
        and len(builders["Loudmouth"]) == 1
        and cites["Notion"] == 1
        and cats["YouTube"]["Marketing"] == 3,
    )

    rows = classify(cites, builders, cats, by_builder, FIX_PLUGINS, FIX_MCP)
    by = {r["integration"]: r for r in rows}

    # 9 — four buckets, each reached by a different route.
    leg(
        "classify buckets native/catalog/mcp-only/unserved",
        by["Grok"]["status"] == "native"
        and by["Calendly"]["status"] == "catalog"
        and by["LinkedIn"]["status"] == "mcp-only"
        and by["Loudmouth"]["status"] == "unserved",
    )

    # 10 — FIRES ON KNOWN BAD. Ranking by citations puts the single-builder vendor first.
    gaps = gap_rank(rows, MIN_BUILDERS)
    bad = sorted(
        [r for r in rows if r["status"] in ("unserved", "mcp-only")],
        key=lambda r: -r["cites"],
    )
    leg(
        "ranked by builders, not citations (known-bad: cite-ranked)",
        [g["integration"] for g in gaps][:1] == ["YouTube"]
        and bad[0]["integration"] == "Loudmouth",
        "cite-ranking would lead with Loudmouth (6 cites, 1 builder)",
    )

    # 11 — the single-builder vendor is excluded entirely, and flagged when shown.
    leg(
        "single-builder noise excluded and flagged",
        "Loudmouth" not in [g["integration"] for g in gaps]
        and by["Loudmouth"]["self_cited"] is True
        and by["YouTube"]["self_cited"] is False,
    )

    # 12 — a served integration is never a gap however loud it is.
    leg(
        "served integrations never rank",
        all(g["integration"] not in ("Calendly", "Gmail", "Grok") for g in gaps),
    )

    # 13 — the summary's arithmetic closes: every row lands in exactly one bucket.
    s = summarise(rows, naive_unserved(sorted(cites), FIX_MCP))
    leg(
        "summary buckets partition the corpus",
        s["native_surfaces"]
        + s["served_by_catalog"]
        + s["mcp_repo_only"]
        + s["served_by_nothing"]
        == s["distinct_integrations"]
        and s["unserved_by_catalog"] == s["mcp_repo_only"] + s["served_by_nothing"],
    )

    # 14 — the table renders every ranked gap and invents none.
    text = render_rank(gaps, s, MIN_BUILDERS)
    leg(
        "render prints exactly the ranked rows",
        all(g["integration"] in text for g in gaps)
        and "Loudmouth" not in text
        and "prior method reproduces" in text,
    )

    # 16 — FIRES ON KNOWN BAD. The two methods' totals can agree while their MEMBERSHIP does
    #      not; the summary has to surface the disagreement, not just the count.
    leg(
        "membership disagreement reported, not just the total",
        "YouTube" in s["prior_missed_gap"]
        and "YouTube" not in s["prior_false_gap"]
        and s["prior_agrees"] < s["prior_method_unserved"],
        "prose matching scored YouTube served off acme/notes",
    )

    # 17 — no snapshots, no answer. An empty join must refuse, not print zeros.
    empty = build(pathlib.Path("/nonexistent-gb-demand-root"), MIN_BUILDERS)
    leg("missing snapshots refuse rather than print zeros", empty is None)

    ok = sum(1 for _, good, _ in legs if good)
    for name, good, detail in legs:
        mark = "ok  " if good else "FAIL"
        emit(
            "  {m} {n}{d}".format(m=mark, n=name, d=(" — " + detail) if detail else "")
        )
    emit(
        "SELFTEST {v} - {o}/{t}".format(
            v="PASS" if ok == len(legs) else "FAIL", o=ok, t=len(legs)
        )
    )
    return 0 if ok == len(legs) else 1


# ---------------------------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gb-demand.py",
        description="Integrations people build Bots around that nothing installable serves.",
    )
    ap.add_argument("verb", nargs="?", choices=("rank", "gaps"), default="rank")
    ap.add_argument("--json", action="store_true", help="machine-readable join")
    ap.add_argument("--all", action="store_true", help="drop the builder threshold")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    threshold = 1 if args.all else MIN_BUILDERS
    doc = build(ROOT, threshold)
    if doc is None:
        sys.stderr.write(
            "ERROR need a usecases/ and a market/ snapshot — run gb-usecases.py and "
            "gb-market-snapshot.py first\n"
        )
        return 2

    if args.json or args.verb == "gaps":
        if args.json:
            emit(json.dumps(doc, indent=1, sort_keys=False))
            return 0
        for g in doc["gaps"]:
            emit(
                "{n}\t{b}\t{c}\t{s}\t{e}".format(
                    n=g["integration"],
                    b=g["builders"],
                    c=g["cites"],
                    s=g["status"],
                    e=g["evidence"],
                )
            )
        return 0

    emit(render_rank(doc["gaps"], doc["summary"], threshold))
    return 0


if __name__ == "__main__":
    gbmain(main)
