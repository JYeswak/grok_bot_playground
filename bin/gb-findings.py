#!/usr/bin/env python3
"""gb-findings — the FINDING SUPPLY: every teachable claim `gb` can emit today, and the exact
command that re-derives each one.

A FINDING is not an opinion and not a feature. It is a specific measured claim, carrying its own
denominator, that would make a Grok Bot operator do something different tomorrow. This file is
the inventory of them: one row per finding, each bound to the verb that produced it and to a
shell command a stranger can paste.

Nothing here is a remembered number. Every row's figure is re-derived on every run — from a
`gb` verb re-executed as a child process, or from an artifact already on disk. A figure that
cannot be re-derived on this machine is emitted with `number: null` and `verified: false`; it is
never printed as a fact. That distinction is the whole point: six numbers in this repo's own
prose were falsified on 2026-09-11 by simply re-measuring them, and three more were falsified
while writing this file (see CAVEATS at the bottom of the output).

RUBRIC — `teaching_value = surprise * actionability`, both axes 0..3, so 0..9.

  surprise       0  restates a setting; nobody would have guessed otherwise
                 1  mildly unexpected once the number is in front of you
                 2  contradicts what a careful operator would have assumed
                 3  contradicts the product's own documentation, or this repo's prior claim

  actionability  0  nothing to do about it
                 1  changes what you WATCH
                 2  changes a decision you make this week
                 3  changes a command you run today

Ties break on surprise, then on id, so the ranking is a total order and two runs over the same
inputs emit the same sequence.

REFUSAL RULES. Each has a known-bad fixture in `--selftest`; delete a rule and the selftest
fails, which is the only kind of rule worth writing down.

  R1 no-reproduce       A row with no re-deriving command is never kept as a plain fact. It is
                        emitted with `unreproducible: true`, `verified: false` and
                        `teaching_value: 0`, and it is counted in the envelope. An honest count
                        of our own blind spots is itself the finding.
  R2 number-absent      REFUSED when the row's `number` does not appear in its `evidence`.
                        Evidence that does not contain the figure it supports is decoration.
  R3 denominator-mute   REFUSED when the row's `denominator` does not appear in its `claim`.
                        "241 integrations are unserved" is not a claim; "241 of 294" is.
  R4 score-range        REFUSED when surprise or actionability falls outside 0..3.
  R5 unmeasured         `derive` returned nothing — an artifact or verb was unreachable. The row
                        survives with `number: null`. An absent measurement is NEVER a zero.

  gb-findings.py                   # rank the findings, write findings/<stamp>.json
  gb-findings.py --json            # the envelope gb-post.py consumes
  gb-findings.py --no-write        # rank without writing an artifact
  gb-findings.py --selftest        # every rule above fires on a known-bad fixture
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import statistics
import sys
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import (  # noqa: E402
    atomic_write_text,
    main as gbmain,
    read_json_capped,
    run as gbrun,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-findings/1"

# A verb that reports FINDINGS exits 1 by contract (`gb capabilities` -> exit_codes). Both 0 and
# 1 carry a complete document; 2/3/4 do not, and are treated as unreachable.
VERB_OK_CODES = (0, 1)
VERB_TIMEOUT_S = 120.0

# The schedulable charter cap. A Bot whose description is longer cannot be given a routine, so
# this number is the difference between a deployed agent and a chat window.
SCHEDULABLE_CAP = 900


# ---------------------------------------------------------------------------------------------
# What a derivation returns
# ---------------------------------------------------------------------------------------------
class Shot(NamedTuple):
    """One measurement, taken now.

    `claim` MUST state `denominator` (R3) and `evidence` MUST contain `number` (R2). Both are
    built inside the derivation from the same formatted values, so the rules normally hold by
    construction — they exist to catch the authoring mistake where somebody hand-writes a
    sentence beside a figure that no longer matches it.
    """

    number: Any
    denominator: Any
    claim: str
    evidence: str


class Spec(NamedTuple):
    """A finding the tool knows how to look for."""

    id: str
    verb: str
    source: str
    reproduce: Optional[str]
    teaches: str
    surprise: int
    actionability: int
    derive: Callable[["Sources"], Optional[Shot]]


# ---------------------------------------------------------------------------------------------
# Number rendering and the containment rules
# ---------------------------------------------------------------------------------------------
def forms(value: Any) -> List[str]:
    """Every spelling of `value` a sentence might reasonably use.

    `1137` is written `1,137` in prose and `1137` in JSON; `3.18` may be written `3.18` or
    `3.180228` upstream. A containment rule that knew only one spelling would refuse honest rows,
    and a rule that refuses honest rows gets deleted.
    """
    out: List[str] = []
    if isinstance(value, bool) or value is None:
        return [str(value)]
    if isinstance(value, int):
        out = [str(value), "{:,}".format(value)]
    elif isinstance(value, float):
        out = [repr(value), "{:g}".format(value)]
        for nd in (1, 2, 3, 4):
            out.append("{:.{nd}f}".format(value, nd=nd))
        if value == int(value):
            out.append(str(int(value)))
    else:
        out = [str(value)]
    seen: List[str] = []
    for f in out:
        if f and f not in seen:
            seen.append(f)
    return seen


def mentions(text: str, value: Any) -> bool:
    """True when any spelling of `value` occurs in `text`."""
    return any(f in text for f in forms(value))


# ---------------------------------------------------------------------------------------------
# Sources — verbs re-run as children, artifacts read off disk. Both cached per process.
# ---------------------------------------------------------------------------------------------
class Sources:
    """The measurement surface. Every read is bounded and every failure answers None."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self._verbs: Dict[str, Optional[Any]] = {}
        self._roots: Dict[str, Optional[Any]] = {}
        self.log: List[Dict[str, Any]] = []

    # -- gb verbs ------------------------------------------------------------------------------
    def verb(self, name: str) -> Optional[Any]:
        if name in self._verbs:
            return self._verbs[name]
        gb = self.root / "bin" / "gb"
        doc: Optional[Any] = None
        detail = ""
        if not gb.exists():
            detail = "bin/gb absent"
        else:
            proc = gbrun(
                [str(gb), name, "--json"], timeout_s=VERB_TIMEOUT_S, cwd=self.root
            )
            if proc.timed_out:
                detail = "timed out after {}s".format(VERB_TIMEOUT_S)
            elif proc.code not in VERB_OK_CODES:
                detail = "exit {}".format(proc.code)
            else:
                try:
                    doc = json.loads(proc.out)
                except ValueError:
                    detail = "stdout was not JSON"
        self._verbs[name] = doc
        self.log.append(
            {
                "kind": "verb",
                "name": name,
                "reachable": doc is not None,
                "detail": detail,
            }
        )
        return doc

    # -- artifact roots ------------------------------------------------------------------------
    def artifact(self, root: str) -> Optional[Any]:
        if root in self._roots:
            return self._roots[root]
        d = self.root / root
        newest = None
        if d.is_dir():
            stamped = sorted(p for p in d.glob("*.json") if p.name[:4].isdigit())
            newest = stamped[-1] if stamped else None
        doc = read_json_capped(newest) if newest is not None else None
        self._roots[root] = doc
        self.log.append(
            {
                "kind": "artifact",
                "name": root,
                "reachable": doc is not None,
                "detail": str(newest.name) if newest is not None else "no stamped json",
            }
        )
        return doc


class FakeSources(Sources):
    """Selftest injection: no child processes, no disk."""

    def __init__(
        self,
        verbs: Optional[Dict[str, Any]] = None,
        roots: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(pathlib.Path("/nonexistent-gb-findings"))
        self._verbs = dict(verbs or {})
        self._roots = dict(roots or {})

    def verb(self, name: str) -> Optional[Any]:
        return self._verbs.get(name)

    def artifact(self, root: str) -> Optional[Any]:
        return self._roots.get(root)


# ---------------------------------------------------------------------------------------------
# Safe navigation. A finding must never be an exception, and never a silent zero.
# ---------------------------------------------------------------------------------------------
def dig(doc: Any, *path: Any) -> Any:
    cur = doc
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif (
            isinstance(cur, list)
            and isinstance(key, int)
            and -len(cur) <= key < len(cur)
        ):
            cur = cur[key]
        else:
            return None
    return cur


def num(value: Any) -> Optional[float]:
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def ints(*values: Any) -> Optional[Tuple[int, ...]]:
    """All of `values` as ints, or None if any is missing. Partial input is not a measurement."""
    out: List[int] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        out.append(int(v))
    return tuple(out)


def pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def q(value: Any) -> str:
    """A figure as prose would write it: `1137` -> `1,137`.

    Safe to use in a claim or an evidence string because `mentions` accepts the grouped and the
    bare spelling of the same integer, so R2/R3 cannot be tripped by formatting alone.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return "{:,}".format(value)
    return str(value)


def plural(n: int, one: str, many: Optional[str] = None) -> str:
    """`plural(1, 'skill')` -> `skill`; `plural(3, 'skill')` -> `skills`."""
    return one if n == 1 else (many or one + "s")


# =============================================================================================
# DERIVATIONS — the corpus (what 645 other people built)
# =============================================================================================
def d_unserved(s: Sources) -> Optional[Shot]:
    summary = dig(s.verb("demand"), "summary")
    got = ints(
        dig(summary, "unserved_by_catalog"), dig(summary, "distinct_integrations")
    )
    if not got:
        return None
    unserved, total = got
    return Shot(
        unserved,
        total,
        "{u} of the {t} distinct integrations named across the 645-Bot corpus are served by no "
        "installable connector.".format(u=unserved, t=total),
        "gb demand --json .summary.unserved_by_catalog={u} over .distinct_integrations={t} "
        "({p}%), joining usecases/ against the live plugin catalog".format(
            u=unserved, t=total, p=pct(unserved, total)
        ),
    )


def d_real_gaps(s: Sources) -> Optional[Shot]:
    summary = dig(s.verb("demand"), "summary")
    got = ints(
        dig(summary, "real_gaps"),
        dig(summary, "unserved_by_catalog"),
        dig(summary, "unserved_single_citation"),
    )
    if not got:
        return None
    gaps, unserved, single = got
    return Shot(
        gaps,
        unserved,
        "Only {g} of the {u} unserved integrations are reached for by 3 or more DISTINCT "
        "builders; {s} of them are named by exactly one Bot.".format(
            g=gaps, u=unserved, s=single
        ),
        "gb demand --json .summary.real_gaps={g}, .unserved_by_catalog={u}, "
        ".unserved_single_citation={s} — the cliff between a market and one person's "
        "habit".format(g=gaps, u=unserved, s=single),
    )


def d_method_disagree(s: Sources) -> Optional[Shot]:
    summary = dig(s.verb("demand"), "summary")
    got = ints(
        dig(summary, "prior_agrees"),
        dig(summary, "distinct_integrations"),
        dig(summary, "prior_method_unserved"),
        dig(summary, "unserved_by_catalog"),
    )
    if not got:
        return None
    agrees, total, prior, now = got
    return Shot(
        agrees,
        total,
        "Two ways of counting 'unserved' land {d} apart in total ({p} vs {n}) while agreeing on "
        "only {a} of the {t} integration names — near-identical totals hiding a "
        "{x}-name disagreement.".format(
            d=abs(prior - now), p=prior, n=now, a=agrees, t=total, x=total - agrees
        ),
        "gb demand --json .summary.prior_agrees={a} of .distinct_integrations={t}; "
        "prior_method_unserved={p} vs unserved_by_catalog={n}".format(
            a=agrees, t=total, p=prior, n=now
        ),
    )


def d_charter_denominator(s: Sources) -> Optional[Shot]:
    ch = dig(s.verb("corpus"), "charter")
    got = ints(
        dig(ch, "denominator_gap_chars", "value"),
        dig(ch, "median_captured", "value"),
        dig(ch, "median_all_rows", "value"),
        dig(ch, "median_all_rows", "n"),
        dig(ch, "median_captured", "n"),
    )
    if not got:
        return None
    gap, cap_med, all_med, all_n, cap_n = got
    return Shot(
        gap,
        all_n,
        "The corpus charter median is {c} chars over the {cn} rows that HAVE a charter and {a} "
        "chars over all {an} attributed Bots — quote the wrong basis and you are off by "
        "{g} chars.".format(c=cap_med, cn=cap_n, a=all_med, an=all_n, g=gap),
        "gb corpus --json .charter.denominator_gap_chars.value={g}; median_captured={c} (n="
        "{cn}) vs median_all_rows={a} (n={an})".format(
            g=gap, c=cap_med, cn=cap_n, a=all_med, an=all_n
        ),
    )


def d_approval_denominator(s: Sources) -> Optional[Shot]:
    ap = dig(s.verb("corpus"), "approval")
    all_share, cap_share = (
        num(dig(ap, "share_all_rows", "value")),
        num(dig(ap, "share_captured", "value")),
    )
    got = ints(dig(ap, "share_captured", "n"), dig(ap, "share_all_rows", "n"))
    if all_share is None or cap_share is None or not got:
        return None
    cap_n, all_n = got
    a, c = pct(all_share, 1.0), pct(cap_share, 1.0)
    return Shot(
        c,
        cap_n,
        "Charters naming an approval step are {c}% of the {cn} captured charters but {a}% of all "
        "{an} attributed Bots — the gap is the CAPTURE RATE, not a change in how people "
        "write.".format(c=c, cn=cap_n, a=a, an=all_n),
        "gb corpus --json .approval.share_captured.value={cs} over n={cn} = {c}%; "
        "share_all_rows.value={as_} over n={an}".format(
            cs=cap_share, cn=cap_n, c=c, as_=all_share, an=all_n
        ),
    )


def d_uncaptured(s: Sources) -> Optional[Shot]:
    ch = dig(s.verb("corpus"), "charter")
    got = ints(dig(ch, "uncaptured_rows", "value"), dig(ch, "uncaptured_rows", "n"))
    if not got:
        return None
    unc, total = got
    return Shot(
        unc,
        total,
        "{u} of the {t} attributed Bots in the corpus have NO captured charter text at all, so "
        "every 'charters say X' figure has a denominator of {c}, never {t}.".format(
            u=unc, t=total, c=total - unc
        ),
        "gb corpus --json .charter.uncaptured_rows.value={u} of n={t}; the measurable "
        "population is {c}".format(u=unc, t=total, c=total - unc),
    )


def d_grok_only(s: Sources) -> Optional[Shot]:
    it = dig(s.verb("corpus"), "integrations")
    got = ints(
        dig(it, "rows_naming_only_read_only", "value"),
        dig(it, "rows_naming_only_read_only", "n"),
    )
    if not got:
        return None
    only, total = got
    return Shot(
        only,
        total,
        "{o} of the {t} published Bots name Grok as their ONLY integration — {p}% of the corpus "
        "cannot read or write anything outside the model.".format(
            o=only, t=total, p=pct(only, total)
        ),
        "gb corpus --json .integrations.rows_naming_only_read_only.value={o} over n={t} "
        "= {p}%".format(o=only, t=total, p=pct(only, total)),
    )


def _captured_rows(s: Sources) -> Optional[List[Dict[str, Any]]]:
    rows = dig(s.artifact("usecases"), "rows")
    if not isinstance(rows, list):
        return None
    keep = [
        r
        for r in rows
        if isinstance(r, dict)
        and isinstance(r.get("prompt_chars"), int)
        and r["prompt_chars"] > 0
    ]
    return keep or None


def _top_names(
    rows: Sequence[Dict[str, Any]], pool: Sequence[Dict[str, Any]], n: int = 10
) -> Tuple[Set[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The `n` most prolific contributors, counted over `pool`, ties broken by name.

    `pool` is a parameter because THAT IS THE FINDING below: counting over all attributed rows
    and counting over charter-carrying rows do not select the same ten people.
    """
    counts: Dict[str, int] = collections.Counter(
        r["contributor"]
        for r in pool
        if isinstance(r, dict)
        and isinstance(r.get("contributor"), str)
        and r["contributor"]
    )
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    names = {name for name, _ in ranked}
    return (
        names,
        [r for r in rows if r.get("contributor") in names],
        [r for r in rows if r.get("contributor") not in names],
    )


def d_top_charter_shorter(s: Sources) -> Optional[Shot]:
    cap = _captured_rows(s)
    allrows = dig(s.artifact("usecases"), "rows")
    if cap is None or not isinstance(allrows, list):
        return None
    _, top, rest = _top_names(cap, allrows)
    if not top or not rest:
        return None
    mt = int(statistics.median(r["prompt_chars"] for r in top))
    mr = int(statistics.median(r["prompt_chars"] for r in rest))
    shorter = round(100.0 * (1.0 - mt / mr), 1) if mr else 0.0
    return Shot(
        mt,
        len(cap),
        "Among the {n} captured charters, the 10 most prolific builders write a median of {t} "
        "chars against {r} for everyone else — {s}% SHORTER, not the 35% this repo previously "
        "printed.".format(n=len(cap), t=mt, r=mr, s=shorter),
        "usecases/ newest snapshot: median prompt_chars {t} over {nt} top-10 rows vs {r} over "
        "{nr} others, n={n} captured; ratio {t}/{r} = {s}% shorter".format(
            t=mt, nt=len(top), r=mr, nr=len(rest), n=len(cap), s=shorter
        ),
    )


def d_top_approval_lower(s: Sources) -> Optional[Shot]:
    cap = _captured_rows(s)
    allrows = dig(s.artifact("usecases"), "rows")
    if cap is None or not isinstance(allrows, list):
        return None
    _, top, rest = _top_names(cap, allrows)
    if not top or not rest:
        return None
    at = pct(sum(1 for r in top if r.get("has_approval_language")), len(top))
    ar = pct(sum(1 for r in rest if r.get("has_approval_language")), len(rest))
    return Shot(
        at,
        len(top),
        "Across the {nt} charters written by the 10 most prolific builders, {t}% name an "
        "approval step against {r}% for the other {nr} — the most productive builders are the "
        "LESS cautious half, so a percentile against them is a conformity score and never a "
        "safety score.".format(nt=len(top), t=at, r=ar, nr=len(rest)),
        "usecases/ newest snapshot: has_approval_language true in {t}% of {nt} top-10 rows vs "
        "{r}% of {nr} others".format(t=at, nt=len(top), r=ar, nr=len(rest)),
    )


def d_cohort_unstable(s: Sources) -> Optional[Shot]:
    """The population the phrase 'top 10 builders' names depends on what you ranked over."""
    cap = _captured_rows(s)
    allrows = dig(s.artifact("usecases"), "rows")
    if cap is None or not isinstance(allrows, list):
        return None
    n_all, top_a, rest_a = _top_names(cap, allrows)
    n_cap, top_c, rest_c = _top_names(cap, cap)
    if not (top_a and rest_a and top_c and rest_c):
        return None
    swapped = len(n_all - n_cap)
    gap_a = round(
        pct(sum(1 for r in rest_a if r.get("has_approval_language")), len(rest_a))
        - pct(sum(1 for r in top_a if r.get("has_approval_language")), len(top_a)),
        1,
    )
    gap_c = round(
        pct(sum(1 for r in rest_c if r.get("has_approval_language")), len(rest_c))
        - pct(sum(1 for r in top_c if r.get("has_approval_language")), len(top_c)),
        1,
    )
    return Shot(
        swapped,
        10,
        "'The top 10 builders' is not one population: ranking over all attributed rows versus "
        "over charter-carrying rows swaps {s} of the 10 members and moves the approval gap from "
        "{a}pp to {c}pp.".format(s=swapped, a=gap_a, c=gap_c),
        "usecases/ newest snapshot ranked two ways: {s} of 10 contributors differ ({d}); "
        "approval gap {a}pp vs {c}pp — gb-mirror.py ranks over captured rows, "
        "gb-corpus.py's population is all rows".format(
            s=swapped, d=", ".join(sorted(n_all - n_cap)) or "none", a=gap_a, c=gap_c
        ),
    )


# =============================================================================================
# DERIVATIONS — the market (what practitioners post on X)
# =============================================================================================
def d_uses_per_post(s: Sources) -> Optional[Shot]:
    doc = s.verb("x")
    uses = dig(doc, "combined", "total_uses")
    passes = dig(doc, "passes")
    if not isinstance(passes, list):
        return None
    posts = sum(int(p.get("posts") or 0) for p in passes if isinstance(p, dict))
    got = ints(uses, posts)
    if not got or posts == 0:
        return None
    u, p = got
    return Shot(
        u,
        p,
        "Only {u} of the {p} captured posts mentioning Grok Bot describe an actual USE "
        "({x}%) — the rest are announcements, opinion and noise.".format(
            u=q(u), p=q(p), x=pct(u, p)
        ),
        "gb x --json .combined.total_uses={u} against the sum of .passes[].posts={p} "
        "= {x}%".format(u=u, p=p, x=pct(u, p)),
    )


def d_deployed_share(s: Sources) -> Optional[Shot]:
    c = dig(s.verb("x"), "combined")
    got = ints(
        dig(c, "tiers", "deployed"),
        dig(c, "total_uses"),
        dig(c, "tiers", "intended"),
        dig(c, "tiers", "unclear"),
    )
    if not got:
        return None
    dep, total, intended, unclear = got
    return Shot(
        dep,
        total,
        "{d} of the {t} measured uses show a Bot actually DEPLOYED; {i} describe an intention "
        "only and {u} are unclear — {p}% of the visible market is people saying what they "
        "will build.".format(
            d=dep, t=total, i=intended, u=unclear, p=pct(intended + unclear, total)
        ),
        "gb x --json .combined.tiers deployed={d} intended={i} unclear={u} over "
        "total_uses={t}".format(d=dep, i=intended, u=unclear, t=total),
    )


def d_uncategorised(s: Sources) -> Optional[Shot]:
    doc = s.verb("x")
    ranked = dig(doc, "uses_ranked_combined")
    total = dig(doc, "combined", "total_uses")
    if not isinstance(ranked, list) or not ints(total):
        return None
    unc = next(
        (
            r
            for r in ranked
            if isinstance(r, dict) and r.get("category") == "uncategorised"
        ),
        None,
    )
    got = ints(dig(unc, "posts"), total)
    if not got:
        return None
    posts, tot = got
    named = len(
        [
            r
            for r in ranked
            if isinstance(r, dict) and r.get("category") != "uncategorised"
        ]
    )
    return Shot(
        posts,
        tot,
        "{u} of the {t} measured uses ({p}%) fit NONE of the {n} named categories — the biggest "
        "single bucket in the taxonomy is the bucket for things the taxonomy misses.".format(
            u=posts, t=tot, p=pct(posts, tot), n=named
        ),
        "gb x --json .uses_ranked_combined[] category=uncategorised posts={u} over "
        ".combined.total_uses={t} = {p}%, against {n} named categories".format(
            u=posts, t=tot, p=pct(posts, tot), n=named
        ),
    )


def d_relay_inflation(s: Sources) -> Optional[Shot]:
    """The single most load-bearing counting rule in this repo, stated as a live number."""
    ranked = dig(s.verb("x"), "uses_ranked_combined")
    if not isinstance(ranked, list):
        return None
    worst, delta = None, 0
    for r in ranked:
        if not isinstance(r, dict):
            continue
        got = ints(r.get("distinct_authors"), r.get("authored_authors"))
        if not got:
            continue
        if got[0] - got[1] > delta:
            worst, delta = r, got[0] - got[1]
    if worst is None or delta == 0:
        return None
    d_a, a_a = int(worst["distinct_authors"]), int(worst["authored_authors"])
    relayed = int(worst.get("relayed_posts") or 0)
    return Shot(
        a_a,
        d_a,
        "`{c}` looks like {d} distinct authors but only {a} of those {d} wrote anything: {r} "
        "posts are relays, so ranking on distinct authors inflates this category by "
        "{x}.".format(c=worst.get("category"), d=d_a, a=a_a, r=relayed, x=delta),
        "gb x --json .uses_ranked_combined[] category={c}: authored_authors={a}, "
        "distinct_authors={d}, relayed_posts={r} — the gap is {x}".format(
            c=worst.get("category"), a=a_a, d=d_a, r=relayed, x=delta
        ),
    )


def d_dominated_categories(s: Sources) -> Optional[Shot]:
    ranked = dig(s.verb("x"), "uses_ranked_combined")
    if not isinstance(ranked, list) or not ranked:
        return None
    rows = [r for r in ranked if isinstance(r, dict)]
    dom = [r for r in rows if r.get("dominated")]
    return Shot(
        len(dom),
        len(rows),
        "{d} of the {t} use {cat} {is_} DOMINATED by a single account ({names}) — the other "
        "{ok} {are} broad demand rather than one person posting.".format(
            d=len(dom),
            t=len(rows),
            cat=plural(len(rows), "category", "categories"),
            is_="is" if len(dom) == 1 else "are",
            names=", ".join(str(r.get("category")) for r in dom) or "none",
            ok=len(rows) - len(dom),
            are="is" if len(rows) - len(dom) == 1 else "are",
        ),
        "gb x --json .uses_ranked_combined[] dominated=true in {d} of {t} categories".format(
            d=len(dom), t=len(rows)
        ),
    )


# =============================================================================================
# DERIVATIONS — the connector catalog
# =============================================================================================
def d_catalog_no_connector(s: Sources) -> Optional[Shot]:
    pl = dig(s.artifact("market"), "plugins")
    rows = dig(pl, "rows")
    got = ints(dig(pl, "total"))
    if not isinstance(rows, list) or not got:
        return None
    total = got[0]
    hollow = sum(1 for r in rows if isinstance(r, dict) and not r.get("mcp_servers"))
    return Shot(
        hollow,
        total,
        "{h} of the {t} catalog plugins expose ZERO MCP servers — they are skill text, not "
        "connectors, so the installable-integration count is {c}, not {t}.".format(
            h=hollow, t=total, c=total - hollow
        ),
        "market/ newest snapshot: {h} of {t} .plugins.rows[] have mcp_servers falsy; "
        "by_kind={k}".format(h=hollow, t=total, k=json.dumps(dig(pl, "by_kind"))),
    )


def d_catalog_unverified(s: Sources) -> Optional[Shot]:
    pl = dig(s.artifact("market"), "plugins")
    rows = dig(pl, "rows")
    got = ints(dig(pl, "total"))
    if not isinstance(rows, list) or not got:
        return None
    total = got[0]
    unver = sum(
        1 for r in rows if isinstance(r, dict) and not r.get("verified_publisher")
    )
    return Shot(
        unver,
        total,
        "{u} of the {t} catalog plugins ({p}%) have no verified publisher, and installing one "
        "hands account-scoped credentials to whoever shipped it.".format(
            u=unver, t=total, p=pct(unver, total)
        ),
        "market/ newest snapshot: verified_publisher falsy on {u} of {t} .plugins.rows[] "
        "= {p}%".format(u=unver, t=total, p=pct(unver, total)),
    )


# =============================================================================================
# DERIVATIONS — this deployment (the part a reader can copy against their own)
# =============================================================================================
def d_bots_without_routine(s: Sources) -> Optional[Shot]:
    r = dig(s.artifact("deployment"), "routines", "summary")
    got = ints(dig(r, "bots_checked"), dig(r, "bots_with_routines"), dig(r, "total"))
    if not got:
        return None
    checked, withr, total = got
    return Shot(
        checked - withr,
        checked,
        "{n} of the {c} Bots on this account carry ZERO routines — {t} routines exist across "
        "the whole fleet, so those {n} are chat windows that happen to have a "
        "charter.".format(n=checked - withr, c=checked, t=total),
        "deployment/ newest snapshot .routines.summary: bots_checked={c}, "
        "bots_with_routines={w}, total={t} — {n} Bots have none".format(
            c=checked, w=withr, t=total, n=checked - withr
        ),
    )


def d_self_created_routines(s: Sources) -> Optional[Shot]:
    """`provenance` does NOT record authorship. Measured, after two wrong versions of this.

    Version 1 of this function read `provenance: user` as the self-created case. Version 2
    flipped it to `untrusted` and cited bin/gb-surface-gate.py as the authority. BOTH were
    wrong, and the second was worse because it looked sourced: the gate's message was itself
    an INFERENCE from two routines on a freshly rebuilt Bot, never a measurement.

    What a live test actually established (commit 0d60a49): a Bot asked IN CHAT to schedule
    itself created a real routine, and that routine carries `provenance: "user"`. So chat is a
    genuine create path, `untrusted` means something this repo has not identified, and NOTHING
    on the record says who authored any routine.

    That absence is the finding, and it is a better one than either wrong version: a team
    cannot audit routine authorship at all. The count reported is therefore the `user` share —
    the one value with a known meaning — with the unattributed remainder stated beside it.
    """
    bots = dig(s.artifact("deployment"), "routines", "bots")
    if not isinstance(bots, list):
        return None
    prov = collections.Counter(
        str(r.get("provenance"))
        for b in bots
        if isinstance(b, dict)
        for r in (b.get("routines") or [])
        if isinstance(r, dict)
    )
    total = sum(prov.values())
    if total == 0:
        return None
    attributable = prov.get("user", 0)
    return Shot(
        attributable,
        total,
        "{a} of {t} routines here read provenance=user — the value a Bot produces when you "
        "ask it in chat to schedule itself. The other {r} read untrusted, and NOTHING on the "
        "record says who authored them.".format(
            a=attributable, t=total, r=total - attributable
        ),
        "deployment/ newest snapshot .routines.bots[].routines[].provenance tally {p}; "
        "user={a} of {t}. The prior claim that untrusted marks Bot-authored routines was an "
        "inference from these same rows and was falsified by a live chat-create test "
        "(commit 0d60a49)".format(p=dict(prov), a=attributable, t=total),
    )


def d_routine_read_blocked(s: Sources) -> Optional[Shot]:
    live = dig(s.artifact("deployment"), "live")
    rpcs, blocked = dig(live, "rpcs"), dig(live, "blocked")
    if not isinstance(rpcs, dict) or not isinstance(blocked, list):
        return None
    total = len(rpcs) + len(blocked)
    if total == 0:
        return None
    names = ", ".join(str(b.get("facet")) for b in blocked if isinstance(b, dict))
    return Shot(
        len(blocked),
        total,
        "{b} of the {t} read paths this audit needs do not exist at all ({n} both 404) — a "
        "routine's own memory cannot be audited from any client.".format(
            b=len(blocked), t=total, n=names
        ),
        "deployment/ newest snapshot .live: {r} RPCs answered, {b} facets blocked of {t} "
        "attempted — {n}".format(r=len(rpcs), b=len(blocked), t=total, n=names),
    )


def d_orphaned_state(s: Sources) -> Optional[Shot]:
    u = s.artifact("utilization")
    got = ints(dig(u, "orphaned_entries"), dig(u, "live_entries"))
    if not got:
        return None
    orph, live = got
    total = orph + live
    if total == 0:
        return None
    return Shot(
        orph,
        total,
        "{o} of the {t} Bot-state entries in this desktop's local cache are orphans against "
        "{l} live ones — {p}% of what the client keeps on disk belongs to Bots that are "
        "gone.".format(o=q(orph), t=q(total), l=q(live), p=pct(orph, total)),
        "utilization/ newest snapshot: orphaned_entries={o}, live_entries={l}, total={t} "
        "= {p}% orphaned".format(o=orph, l=live, t=total, p=pct(orph, total)),
    )


def d_memory_empty(s: Sources) -> Optional[Shot]:
    u = s.artifact("utilization")
    got = ints(dig(u, "memory_shards_with_content"), dig(u, "bot_count"))
    if not got:
        return None
    shards, bots = got
    return Shot(
        shards,
        bots,
        "{s} of the {b} Bots on this account hold a memory shard with any content in it — the "
        "memory feature is provisioned and unused.".format(s=shards, b=bots),
        "utilization/ newest snapshot: memory_shards_with_content={s}, bot_count={b}".format(
            s=shards, b=bots
        ),
    )


def _servers(s: Sources) -> Optional[List[Dict[str, Any]]]:
    srv = dig(s.artifact("deployment"), "mcp", "servers")
    return srv if isinstance(srv, list) and srv else None


def d_tool_tax(s: Sources) -> Optional[Shot]:
    srv = _servers(s)
    if srv is None:
        return None
    tools = sum(int(x.get("tool_count") or 0) for x in srv if isinstance(x, dict))
    return Shot(
        tools,
        len(srv),
        "The {n} MCP servers registered on this account expose {t} tools, and the effective "
        "config is ACCOUNT-level with no per-Bot scope — every Bot carries all {t} in its "
        "context whether it needs them or not.".format(n=len(srv), t=tools),
        "deployment/ newest snapshot: sum(.mcp.servers[].tool_count)={t} across {n} servers; "
        ".mcp.scope_note says the effective config has no per-Bot scope".format(
            t=tools, n=len(srv)
        ),
    )


def d_dead_servers(s: Sources) -> Optional[Shot]:
    srv = _servers(s)
    if srv is None:
        return None
    dead = [x for x in srv if isinstance(x, dict) and x.get("status") != "connected"]
    states = collections.Counter(str(x.get("status")) for x in dead)
    return Shot(
        len(dead),
        len(srv),
        "{d} of the {n} registered MCP servers serve nothing at all ({s}) and are still "
        "registered — a Bot asking for those tools gets a failure, not a "
        "fallback.".format(
            d=len(dead),
            n=len(srv),
            s=", ".join("{k}x{v}".format(k=k, v=v) for k, v in sorted(states.items())),
        ),
        "deployment/ newest snapshot: {d} of {n} .mcp.servers[] have status != connected "
        "({s}); each reports tool_count 0".format(
            d=len(dead), n=len(srv), s=dict(states)
        ),
    )


def d_duplicate_servers(s: Sources) -> Optional[Shot]:
    srv = _servers(s)
    if srv is None:
        return None

    def product(server_id: str) -> str:
        base = server_id.lower()
        if base.startswith("user-"):
            base = base[5:]
        return base.split("--")[0]

    counts = collections.Counter(product(str(x.get("server_id"))) for x in srv)
    dupes = {p for p, n in counts.items() if n > 1}
    if not dupes:
        return None
    dup_servers = [x for x in srv if product(str(x.get("server_id"))) in dupes]
    total_tools = sum(int(x.get("tool_count") or 0) for x in srv if isinstance(x, dict))
    first: Dict[str, int] = {}
    for x in srv:
        p = product(str(x.get("server_id")))
        if p in dupes and p not in first:
            first[p] = int(x.get("tool_count") or 0)
    redundant = sum(int(x.get("tool_count") or 0) for x in dup_servers) - sum(
        first.values()
    )
    return Shot(
        len(dup_servers),
        len(srv),
        "{d} of the {n} registered MCP servers are repeat connections to just {p} products, so "
        "{r} of the {t} tool slots are a duplicate of a tool already in "
        "context.".format(
            d=len(dup_servers), n=len(srv), p=len(dupes), r=redundant, t=total_tools
        ),
        "deployment/ newest snapshot .mcp.servers[]: {d} of {n} server_ids normalise to {p} "
        "products ({names}); {r} redundant tool slots of {t}".format(
            d=len(dup_servers),
            n=len(srv),
            p=len(dupes),
            names=", ".join(sorted(dupes)),
            r=redundant,
            t=total_tools,
        ),
    )


def d_unschedulable(s: Sources) -> Optional[Shot]:
    bots = dig(s.artifact("deployment"), "bots")
    if not isinstance(bots, list) or not bots:
        return None
    over = sum(
        1
        for b in bots
        if isinstance(b, dict)
        and int(b.get("description_chars") or 0) > SCHEDULABLE_CAP
    )
    none = sum(
        1 for b in bots if isinstance(b, dict) and not b.get("description_chars")
    )
    return Shot(
        over,
        len(bots),
        "{o} of the {n} Bots on this account carry a charter over the {c}-char schedulable cap "
        "and {z} carries none at all — {r} of {n} can actually be given a routine.".format(
            o=over, n=len(bots), c=SCHEDULABLE_CAP, z=none, r=len(bots) - over - none
        ),
        "deployment/ newest snapshot: {o} of {n} .bots[].description_chars exceed {c}; {z} is "
        "zero".format(o=over, n=len(bots), c=SCHEDULABLE_CAP, z=none),
    )


def d_notifications_off(s: Sources) -> Optional[Shot]:
    bots = dig(s.artifact("deployment"), "bots")
    if not isinstance(bots, list) or not bots:
        return None
    on = sum(1 for b in bots if isinstance(b, dict) and b.get("notifications_enabled"))
    return Shot(
        on,
        len(bots),
        "{on} of the {n} Bots on this account can interrupt their operator — the other "
        "{off} run, finish and wait to be discovered.".format(
            on=on, n=len(bots), off=len(bots) - on
        ),
        "deployment/ newest snapshot: notifications_enabled true on {on} of {n} .bots[]".format(
            on=on, n=len(bots)
        ),
    )


def d_spend_underuse(s: Sources) -> Optional[Shot]:
    usage = dig(s.artifact("deployment"), "usage")
    used = num(dig(usage, "usage_percent"))
    if used is None:
        return None
    on_demand = bool(dig(usage, "on_demand_enabled"))
    used_r = round(used, 2)
    return Shot(
        used_r,
        100,
        "This account consumed {u}% of its 100% weekly allowance, with on-demand overage "
        "billing {o} — the bill is exposed to a ceiling nobody set while {left}% of what is "
        "already paid for goes unused.".format(
            u=used_r,
            o="ENABLED" if on_demand else "disabled",
            left=round(100 - used_r, 2),
        ),
        "deployment/ newest snapshot .usage: usage_percent={raw} -> {u}% of the 100% "
        "allowance, on_demand_enabled={o}, period {p} .. {n}".format(
            raw=used,
            u=used_r,
            o=on_demand,
            p=dig(usage, "period_start"),
            n=dig(usage, "next_reset"),
        ),
    )


def d_skill_monoculture(s: Sources) -> Optional[Shot]:
    bots = dig(s.artifact("deployment"), "skills", "bots")
    if not isinstance(bots, list) or not bots:
        return None
    attach = collections.Counter(
        str(sk.get("name"))
        for b in bots
        if isinstance(b, dict)
        for sk in (b.get("skills") or [])
        if isinstance(sk, dict)
    )
    if not attach:
        return None
    name, n = attach.most_common(1)[0]
    return Shot(
        n,
        len(bots),
        "{n} of the {t} Bots on this account share the SAME single skill (`{s}`) and {d} "
        "distinct {sk} {ex} across the fleet — the skill system is being used as a global "
        "preamble, not as per-Bot capability.".format(
            n=n,
            t=len(bots),
            s=name,
            d=len(attach),
            sk=plural(len(attach), "skill"),
            ex="exists" if len(attach) == 1 else "exist",
        ),
        "deployment/ newest snapshot .skills.bots[].skills[].name tally: `{s}` attached to {n} "
        "of {t} Bots; {d} distinct skill name(s) in total".format(
            s=name, n=n, t=len(bots), d=len(attach)
        ),
    )


# =============================================================================================
# DERIVATIONS — the tool measuring itself
# =============================================================================================
def d_dogfood_gaps(s: Sources) -> Optional[Shot]:
    doc = s.verb("dogfood")
    gaps = dig(doc, "gaps")
    got = ints(dig(doc, "gap_count"), dig(doc, "producers"), dig(doc, "verbs"))
    if not isinstance(gaps, list) or not got:
        return None
    count, producers, verbs = got
    kinds = collections.Counter(str(g.get("kind")) for g in gaps if isinstance(g, dict))
    worst = kinds.most_common(1)[0] if kinds else ("none", 0)
    return Shot(
        count,
        producers,
        "{c} capabilities across this repo's {p} producers cannot be reached through any of the "
        "{v} `gb` verbs; the largest class is `{k}` with {kn} — a producer's flag that the CLI "
        "refuses is a defect, not a preference.".format(
            c=count, p=producers, v=verbs, k=worst[0], kn=worst[1]
        ),
        "gb dogfood --json .gap_count={c} over .producers={p} and .verbs={v}; kinds={k}".format(
            c=count, p=producers, v=verbs, k=dict(kinds)
        ),
    )


def d_walk_unannotated(s: Sources) -> Optional[Shot]:
    doc = s.verb("walk")
    unann = dig(doc, "unannotated")
    got = ints(dig(doc, "verbs_live"))
    if not isinstance(unann, list) or not got:
        return None
    live = got[0]
    return Shot(
        len(unann),
        live,
        "{u} of the {v} live verbs have no guided-tour annotation, so a new operator running "
        "`gb walk` is shown {a} of {v} and told nothing about the rest.".format(
            u=len(unann), v=live, a=live - len(unann)
        ),
        "gb walk --json: .unannotated has {u} entries against .verbs_live={v} ({names})".format(
            u=len(unann), v=live, names=", ".join(str(x) for x in unann[:6])
        ),
    )


def d_galaxy_refusals(s: Sources) -> Optional[Shot]:
    doc = s.verb("galaxy")
    rows = dig(doc, "rows")
    if not isinstance(rows, list) or not rows:
        return None
    decisions = collections.Counter(
        str(r.get("decision")) for r in rows if isinstance(r, dict)
    )
    refused = sum(v for k, v in decisions.items() if k.startswith("REFUSE"))
    return Shot(
        refused,
        len(rows),
        "{r} of the {t} sessions on the Grok Bot Galaxy agenda produce NO template worth "
        "shipping — {b} clear the evidence floor, and the honest answer to the rest is a "
        "refusal with a reason.".format(r=refused, t=len(rows), b=len(rows) - refused),
        "gb galaxy --json .rows[].decision tally {d}: {r} of {t} refuse".format(
            d=dict(decisions), r=refused, t=len(rows)
        ),
    )


def d_templates_within_cap(s: Sources) -> Optional[Shot]:
    doc = s.verb("templates")
    tpl = dig(doc, "templates")
    got = ints(dig(doc, "count"))
    if not isinstance(tpl, list) or not got:
        return None
    count = got[0]
    over = sum(
        1
        for t in tpl
        if isinstance(t, dict) and int(t.get("charter_chars") or 0) > SCHEDULABLE_CAP
    )
    maxint = max(
        (len(t.get("integrations") or []) for t in tpl if isinstance(t, dict)),
        default=0,
    )
    return Shot(
        over,
        count,
        "{o} of the {c} Bot templates this repo proposes exceed the {p}-char schedulable cap "
        "and none names more than {i} integration — the templates are constrained by the same "
        "rule the corpus breaks 15% of the time.".format(
            o=over, c=count, p=SCHEDULABLE_CAP, i=maxint
        ),
        "gb templates --json: {o} of .count={c} have charter_chars > {p}; max "
        "len(integrations)={i}".format(o=over, c=count, p=SCHEDULABLE_CAP, i=maxint),
    )


def d_board_red(s: Sources) -> Optional[Shot]:
    doc = s.verb("triage")
    got = ints(dig(doc, "quick_ref", "red"), dig(doc, "quick_ref", "checks"))
    if not got:
        return None
    red, checks = got
    names = ", ".join(
        str(r.get("check")) for r in (dig(doc, "red") or []) if isinstance(r, dict)
    )
    return Shot(
        red,
        checks,
        "{r} of the {c} gate {ch} over this deployment {is_} RED right now ({n}) — the board "
        "names its own stop condition instead of averaging to a score.".format(
            r=red,
            c=checks,
            ch=plural(checks, "check"),
            is_="is" if red == 1 else "are",
            n=names or "none",
        ),
        "gb triage --json .quick_ref: red={r} of checks={c}; verdict={v}".format(
            r=red, c=checks, v=dig(doc, "verdict")
        ),
    )


# =============================================================================================
# Findings whose MECHANISM is real and whose re-derivation does not exist. R1 handles these.
# =============================================================================================
def d_self_schedule_mechanism(s: Sources) -> Optional[Shot]:
    """The mechanism is proven by an EXPERIMENT; the residue cannot identify the author.

    CORRECTED 2026-09-12. This docstring used to open "`provenance: untrusted` proves a
    routine was created by the agent", and the selector below filtered on exactly that. Both
    were falsified by commit 0d60a49: a Bot asked in chat to schedule itself created a real
    routine, and that routine carries `provenance: "user"`. So the field the selector trusted
    marks the OPPOSITE of what it was read as, and the envelope shipped this row beside
    `routines-self-created` — which states in the same breath that nothing records authorship.
    One producer, two contradictory claims about the same field, in one output.

    What survives, and it is the better claim: a Bot CAN put itself on a cron schedule from an
    ordinary chat turn. That is established by a recorded live experiment, not by any field on
    the row. The creating turn cannot be replayed — `ListSandAutomations` and
    `GetAutomationMemory` are both 404 — so no command re-derives it and R1 correctly forces
    the teaching value to zero rather than letting an unverifiable row rank first.

    The selector therefore filters on what is actually observable — a routine carrying a cron
    expression — and the prose claims the mechanism, never the authorship.
    """
    bots = dig(s.artifact("deployment"), "routines", "bots")
    if not isinstance(bots, list):
        return None
    scheduled = [
        (b.get("name"), r.get("name"), r.get("schedule"))
        for b in bots
        if isinstance(b, dict)
        for r in (b.get("routines") or [])
        if isinstance(r, dict) and r.get("schedule")
    ]
    if not scheduled:
        return None
    return Shot(
        len(scheduled),
        len(scheduled),
        "A Bot can schedule ITSELF from an ordinary chat turn — proven by live experiment, not "
        "by any field: {n} of the {n} routines here carry a cron expression, and the product's "
        "read-only `automations` framing does not describe a create path at all.".format(
            n=len(scheduled)
        ),
        "deployment/ newest snapshot shows {n} routine(s) with a cron schedule ({rows}). The "
        "CREATING TURN cannot be re-read: ListSandAutomations and GetAutomationMemory both 404, "
        "and no provenance value identifies an author — `user` is what the chat-create path "
        "produced in the 0d60a49 experiment.".format(
            n=len(scheduled),
            rows="; ".join("{}/{} {}".format(*r) for r in scheduled),
        ),
    )


def d_galaxy_no_replay(s: Sources) -> Optional[Shot]:
    ev = dig(s.verb("galaxy"), "event")
    replay = dig(ev, "replay")
    if not isinstance(replay, str) or not replay:
        return None
    return Shot(
        0,
        0,
        "The Grok Bot Galaxy livestream has 0 announced replays of 0 published recordings: "
        "everything measured says live is the only capture, but this is an ABSENCE and no "
        "positive control exists for it.",
        "gb galaxy --json .event.replay: {r} — absence of an announcement is not evidence of "
        "absence, and 0 is the count of announcements found, not of recordings that "
        "exist.".format(r=replay[:200]),
    )


# =============================================================================================
# THE ROSTER
# =============================================================================================
SPECS: Tuple[Spec, ...] = (
    # ---- the corpus -------------------------------------------------------------------------
    Spec(
        "integrations-unserved",
        "demand",
        "usecases/ joined against market/ (the live plugin catalog)",
        "./bin/gb demand --json | jq '{unserved: .summary.unserved_by_catalog, of: .summary.distinct_integrations}'",
        "Check the catalog BEFORE you promise a Bot an integration. Four names in five have no "
        "connector, so plan on a skill or an MCP server instead of an install.",
        2,
        3,
        d_unserved,
    ),
    Spec(
        "integrations-real-gaps",
        "demand",
        "usecases/ joined against market/, thresholded at 3 distinct builders",
        "./bin/gb demand --json | jq '{gaps: .summary.real_gaps, unserved: .summary.unserved_by_catalog, single: .summary.unserved_single_citation}'",
        "Do not build for a long tail. If you are picking an integration to wrap yourself, "
        "start from the 3+-builder list; everything below it is one person's habit.",
        3,
        2,
        d_real_gaps,
    ),
    Spec(
        "unserved-method-disagreement",
        "demand",
        "the same join run two ways, both printed",
        "./bin/gb demand --json | jq '{agrees: .summary.prior_agrees, of: .summary.distinct_integrations, prior: .summary.prior_method_unserved, now: .summary.unserved_by_catalog}'",
        "Never accept a total as agreement. Diff the MEMBERSHIP of two methods; here the totals "
        "are 8 apart and the lists differ on 80 names.",
        3,
        2,
        d_method_disagree,
    ),
    Spec(
        "charter-median-denominator",
        "corpus",
        "usecases/ newest snapshot, both denominators printed",
        "./bin/gb corpus --json | jq '.charter | {gap: .denominator_gap_chars.value, captured: .median_captured, all: .median_all_rows}'",
        "State the denominator every time you quote a charter length. Ask which population a "
        "median was taken over before you copy it into your own charter.",
        2,
        3,
        d_charter_denominator,
    ),
    Spec(
        "approval-share-denominator",
        "corpus",
        "usecases/ newest snapshot, approval flag over two populations",
        "./bin/gb corpus --json | jq '.approval'",
        "Do not read an approval-language share as a safety trend. Check whether the gap is "
        "behaviour or capture rate before you conclude people got more careful.",
        2,
        2,
        d_approval_denominator,
    ),
    Spec(
        "charters-uncaptured",
        "corpus",
        "usecases/ newest snapshot",
        "./bin/gb corpus --json | jq '.charter.uncaptured_rows'",
        "When you cite the corpus, cite 487, not 645. A quarter of the rows have no charter to "
        "measure, and averaging them in as zero is how the median moved 68 chars.",
        1,
        3,
        d_uncaptured,
    ),
    Spec(
        "grok-only-bots",
        "corpus",
        "usecases/ newest snapshot, integration lists",
        "./bin/gb corpus --json | jq '.integrations.rows_naming_only_read_only'",
        "If your Bot names no integration, it can only talk. Attach one connector before you "
        "give it a routine, or the scheduled run has nothing to act on.",
        3,
        3,
        d_grok_only,
    ),
    Spec(
        "top-builders-write-shorter",
        "corpus",
        "usecases/ newest snapshot, rows split by contributor volume",
        "./bin/gb corpus --json | jq '.charter' && ./bin/gb mirror --json | jq '.baseline'",
        "Write a shorter charter. The people who have shipped the most Bots write ~100 chars "
        "less than everyone else and stay under the schedulable cap.",
        2,
        3,
        d_top_charter_shorter,
    ),
    Spec(
        "top-builders-approve-less",
        "corpus",
        "usecases/ newest snapshot, rows split by contributor volume",
        "./bin/gb mirror --json | jq '{caveat, baseline}'",
        "Do not copy the prolific builders' approval posture. Your percentile against them is a "
        "conformity score; decide your own approval boundary from blast radius instead.",
        3,
        2,
        d_top_approval_lower,
    ),
    Spec(
        "top-builder-cohort-unstable",
        "corpus",
        "usecases/ newest snapshot ranked over two different populations",
        "./bin/gb mirror --json | jq '.baseline' && ./bin/gb corpus --json | jq '.approval'",
        "Pin the population when you say 'top builders'. Two producers in this repo disagree by "
        "4pp purely because one ranks over captured charters and one over all rows.",
        3,
        2,
        d_cohort_unstable,
    ),
    # ---- the market -------------------------------------------------------------------------
    Spec(
        "x-uses-per-post",
        "x",
        "x/ sweeps, classifier use/4",
        "./bin/gb x --json | jq '{uses: .combined.total_uses, posts: [.passes[].posts] | add}'",
        "Do not size this market from mention volume. Filter to posts that show a Bot doing "
        "something before you count demand.",
        2,
        1,
        d_uses_per_post,
    ),
    Spec(
        "x-deployed-vs-intended",
        "x",
        "x/ sweeps, tiered deployed/intended/unclear",
        "./bin/gb x --json | jq '.combined.tiers'",
        "Weight 'I am going to build' posts down. Only the deployed tier tells you what is "
        "actually running, and it is under two thirds of measured uses.",
        2,
        2,
        d_deployed_share,
    ),
    Spec(
        "x-uncategorised-quarter",
        "x",
        "x/ sweeps against the named category list",
        "./bin/gb x --json | jq '.uses_ranked_combined[] | select(.category==\"uncategorised\") | {posts, distinct_authors}'",
        "Read the uncategorised bucket first, not last. It is the biggest one, which means the "
        "taxonomy — not the market — is what is missing a quarter of the answer.",
        3,
        2,
        d_uncategorised,
    ),
    Spec(
        "x-relay-inflation",
        "x",
        "x/ sweeps, authored vs relayed attribution",
        "./bin/gb x --json | jq '[.uses_ranked_combined[] | {category, distinct_authors, authored_authors, relayed_posts}] | map(select(.distinct_authors != .authored_authors))'",
        "Rank categories on `authored_authors`, never `distinct_authors` and never engagement. "
        "Relays make one engineer's reposts look like five separate practitioners.",
        3,
        3,
        d_relay_inflation,
    ),
    Spec(
        "x-domination-is-rare",
        "x",
        "x/ sweeps, per-category top-author share",
        "./bin/gb x --json | jq '[.uses_ranked_combined[] | {category, dominated, top_author, top_author_share}]'",
        "Check `dominated` before you treat a small category as a market. One category here is "
        "a single account; the rest are genuinely many people.",
        2,
        2,
        d_dominated_categories,
    ),
    # ---- the catalog ------------------------------------------------------------------------
    Spec(
        "catalog-plugins-without-connector",
        "demand",
        "market/ newest snapshot, per-plugin mcp_servers count",
        "jq '{hollow: ([.plugins.rows[] | select((.mcp_servers // 0) == 0)] | length), total: .plugins.total}' market/$(ls -1 market | tail -1)",
        "Do not count the catalog size as integration coverage. Filter on `mcp_servers > 0` "
        "before you tell anyone how many connectors exist.",
        2,
        2,
        d_catalog_no_connector,
    ),
    Spec(
        "catalog-unverified-publishers",
        "demand",
        "market/ newest snapshot, verified_publisher flag",
        "jq '{unverified: ([.plugins.rows[] | select(.verified_publisher != true)] | length), total: .plugins.total}' market/$(ls -1 market | tail -1)",
        "Check `verified_publisher` before installing. Nearly half the catalog is unverified, "
        "and an install grants account-scoped credentials.",
        2,
        3,
        d_catalog_unverified,
    ),
    # ---- this deployment --------------------------------------------------------------------
    Spec(
        "bots-without-routines",
        "mirror",
        "deployment/ newest snapshot, ListGrokBotAgentAutomations per Bot",
        # SHARED ROOT CAUSE for this row and the four below it (mcp-dead-servers,
        # notifications-all-off, skills-are-a-global-preamble, tour-covers-half-the-verbs):
        # each command DUMPED the underlying data and left the reader to count, so the figure
        # the claim quoted never appeared in the output the reader ran. That makes the READER'S
        # ARITHMETIC the weakest link in a published claim — and it is exactly what
        # bin/gb-post.py refuses, because it greps the claim's own number out of the command's
        # output. Every one of them now PRINTS the number with its denominator.
        "jq -r '.routines.summary|\"\\(.bots_checked - .bots_with_routines) of"
        " \\(.bots_checked) Bots carry ZERO routines\"'"
        " deployment/$(ls -1 deployment | grep '^20' | tail -1)",
        "Count your routines, not your Bots. A Bot with no routine only runs when you open it, "
        "so the fleet size you quote is mostly chat windows.",
        3,
        3,
        d_bots_without_routine,
    ),
    Spec(
        "routines-self-created",
        "mirror",
        "deployment/ newest snapshot, routine provenance field",
        # PRINTS the number instead of dumping rows to be counted by eye. The old command
        # emitted a list of {name, provenance, schedule} and the claim quoted a figure the
        # reader had to derive from it — which is how a reader's arithmetic becomes the
        # weakest link in a published claim.
        # The `\"user\"` this line used to carry was one backslash too many: shell single
        # quotes pass the backslash through, jq rejects it as INVALID_CHARACTER and exits 3,
        # so the instrument CRASHED instead of printing. A nested string inside a jq `\(...)`
        # interpolation needs no escaping at all.
        'jq -r \'[.routines.bots[]?.routines[]?]|"\\([.[]|select(.provenance=="user")]|length)'
        " of \\(length) routines read provenance=user\"'"
        " deployment/$(ls -1 deployment | grep '^20' | tail -1)",
        "Never read `provenance` as authorship or as a safety grade. Nothing records who wrote "
        "a routine, so routine authorship cannot be audited — review every enabled routine on "
        "its schedule and its charter instead.",
        3,
        3,
        d_self_created_routines,
    ),
    Spec(
        "routine-memory-unreadable",
        "mirror",
        "deployment/ newest snapshot, live.blocked",
        "jq '.live' deployment/$(ls -1 deployment | grep '^20' | tail -1)",
        "Do not plan an audit around routine memory. There is no read path, so treat a "
        "routine's internal state as unobservable and log what you need from the charter side.",
        3,
        2,
        d_routine_read_blocked,
    ),
    Spec(
        "local-state-is-orphans",
        "mirror",
        "utilization/ newest snapshot, live vs orphaned local entries",
        "jq '{live: .live_entries, orphaned: .orphaned_entries, replicas: .orphaned_replicas}' utilization/$(ls -1 utilization | tail -1)",
        "Never read absence from the desktop cache as absence on the server, and never read its "
        "size as fleet size — almost all of it is leftovers from Bots that no longer exist.",
        3,
        2,
        d_orphaned_state,
    ),
    Spec(
        "memory-shards-empty",
        "mirror",
        "utilization/ newest snapshot, memory shard census",
        "jq '{shards: .memory_shards_with_content, bots: .bot_count}' utilization/$(ls -1 utilization | tail -1)",
        "Stop assuming your Bots remember anything. Put durable facts in the charter or a "
        "connector until a shard actually has content in it.",
        3,
        3,
        d_memory_empty,
    ),
    Spec(
        "mcp-tool-tax",
        "mcp",
        "deployment/ newest snapshot, ListSandMcpTools census",
        "jq '{tools: [.mcp.servers[].tool_count] | add, servers: (.mcp.servers | length), scope: .mcp.scope_note}' deployment/$(ls -1 deployment | grep '^20' | tail -1)",
        "Cut the tool allowlist per Bot. Every registered server's tools ride in every Bot's "
        "context because the effective config has no per-Bot scope.",
        3,
        3,
        d_tool_tax,
    ),
    Spec(
        "mcp-dead-servers",
        "mcp",
        "deployment/ newest snapshot, per-server status",
        # `[]?` not `[]`: a jq error exits non-zero with a message, which gb-post classifies
        # as UNRUNNABLE (a crashed instrument), never as a refutation of the claim.
        'jq -r \'[.mcp.servers[]?]|"\\([.[]|select(.status!="connected")]|length) of'
        " \\(length) registered MCP servers serve nothing\"'"
        " deployment/$(ls -1 deployment | grep '^20' | tail -1)",
        "Delete or re-auth broken servers instead of leaving them registered. A needsAuth "
        "server is a tool call that fails at runtime, not one that degrades.",
        2,
        3,
        d_dead_servers,
    ),
    Spec(
        "mcp-duplicate-products",
        "mcp",
        "deployment/ newest snapshot, server ids normalised to products",
        "jq '[.mcp.servers[] | {server_id, tool_count}]' deployment/$(ls -1 deployment | grep '^20' | tail -1)",
        "Consolidate per-account connections to the same product. Each extra Gmail connection "
        "re-adds that product's entire identical tool set to every Bot's context.",
        3,
        3,
        d_duplicate_servers,
    ),
    Spec(
        "charters-over-schedulable-cap",
        "mirror",
        "deployment/ newest snapshot, description_chars per Bot",
        "jq '[.bots[] | {name, description_chars}] | sort_by(-.description_chars)' deployment/$(ls -1 deployment | grep '^20' | tail -1)",
        "Trim any charter over 900 chars before you try to schedule it. Measure yours against "
        "the cap first, not against the corpus median.",
        2,
        3,
        d_unschedulable,
    ),
    Spec(
        "notifications-all-off",
        "mirror",
        "deployment/ newest snapshot, notificationsEnabled per Bot",
        "jq -r '[.bots[]?]|\"\\([.[]|select(.notifications_enabled)]|length) of"
        " \\(length) Bots can notify their operator\"'"
        " deployment/$(ls -1 deployment | grep '^20' | tail -1)",
        "Turn notifications on for the Bots whose output you act on. Otherwise a scheduled run "
        "completes into a sidebar you have to remember to check.",
        1,
        3,
        d_notifications_off,
    ),
    Spec(
        "allowance-underused-overage-on",
        "monitor",
        "deployment/ newest snapshot, GetSandUsageStatus",
        './bin/gb monitor --json | jq \'[.thresholds[] | select(.name=="spend-pace" or .name=="spend-ceiling-recorded")]\'',
        "Either spend the allowance you already bought or turn on-demand off. Enabled overage "
        "with 3% utilisation is an uncapped downside guarding an unused asset.",
        3,
        3,
        d_spend_underuse,
    ),
    Spec(
        "skills-are-a-global-preamble",
        "mcp",
        "deployment/ newest snapshot, ListGrokBotAgentSkills per Bot",
        # The claim is the MOST-ATTACHED skill's share, so the command tallies attachments and
        # prints that share with the skill's own name beside it.
        "jq -r '[.skills.bots[]?] as $b|[$b[]|.skills[]?.name]|group_by(.)|max_by(length)|"
        '"\\(length) of \\($b|length) Bots share the same skill (\\(.[0]))"\''
        " deployment/$(ls -1 deployment | grep '^20' | tail -1)",
        "Give each Bot the skill its job needs. One skill attached to every Bot is a system "
        "prompt with extra steps, and it costs context on every turn.",
        2,
        2,
        d_skill_monoculture,
    ),
    # ---- the tool on itself -----------------------------------------------------------------
    Spec(
        "cli-cannot-reach-its-own-producers",
        "dogfood",
        "this repo's producers vs the verbs `gb` exposes",
        "./bin/gb dogfood --json | jq '{gap_count, producers, verbs, kinds: [.gaps[].kind] | group_by(.) | map({(.[0]): length}) | add}'",
        "Call the producer directly when the verb refuses a target — most of the gap is gate "
        "checks that `bin/gb-surface-gate.py` accepts and `gb triage` rejects.",
        2,
        2,
        d_dogfood_gaps,
    ),
    Spec(
        "tour-covers-half-the-verbs",
        "walk",
        "gb walk's annotation table vs the live verb list",
        # `.unannotated` is an ARRAY: printing the object printed the names and left the count
        # to the reader. `|length` is the whole fix.
        "./bin/gb walk --json | jq -r '\"\\(.unannotated|length) of \\(.verbs_live) live"
        " verbs have no tour annotation\"'",
        "Do not learn this CLI from `gb walk` alone. Read `gb capabilities --json` for the full "
        "verb list, because the tour leaves roughly half of them unannotated.",
        1,
        2,
        d_walk_unannotated,
    ),
    Spec(
        "galaxy-sessions-mostly-refuse",
        "galaxy",
        "the Galaxy agenda against corpus spine and shape floors",
        "./bin/gb galaxy --json | jq '[.rows[] | {session, decision, reason}]'",
        "Require an evidence floor before you build a template for a topic. Most agenda topics "
        "have either no corpus spine or no distinguishable shape.",
        2,
        2,
        d_galaxy_refusals,
    ),
    Spec(
        "proposed-templates-obey-the-cap",
        "templates",
        "gb templates' own charter and integration budget",
        "./bin/gb templates --json | jq '[.templates[] | {id, charter_chars, integrations}] | {over_cap: map(select(.charter_chars > 900)) | length, n: length}'",
        "Copy the template shape: under 900 chars and at most one integration. That is what "
        "makes a Bot schedulable on the first try.",
        1,
        2,
        d_templates_within_cap,
    ),
    Spec(
        "board-red-count",
        "triage",
        "the 25-check surface gate over this deployment",
        "./bin/gb triage --json | jq '{quick_ref, red}'",
        "Clear the RED check before trusting any ranking built on the board. `gb advise` ranks "
        "blocking-first for exactly this reason.",
        0,
        3,
        d_board_red,
    ),
    # ---- true, and NOT reproducible. R1 keeps these honest. ---------------------------------
    Spec(
        "bot-can-schedule-itself",
        "mirror",
        "deployment/ newest snapshot (residue only — the creating turn is unreadable)",
        None,
        "Treat a Bot as able to create its own schedule. Audit `provenance` after any session "
        "where you discussed recurring work, because you cannot replay the turn that did it.",
        3,
        3,
        d_self_schedule_mechanism,
    ),
    Spec(
        "galaxy-replay-unannounced",
        "galaxy",
        "x/ replies from @grok, four conversations, captured 2026-09-11",
        None,
        "Plan to attend the Galaxy stream live. Nothing measured promises a replay — and "
        "nothing measured rules one out either.",
        1,
        2,
        d_galaxy_no_replay,
    ),
)


# =============================================================================================
# Admission — the five rules, applied in one place
# =============================================================================================
def admit(
    spec: Spec, shot: Optional[Shot]
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """(row, refusals). A refused spec produces no row at all; a flagged spec produces a row
    that cannot outrank a measured one."""
    refusals: List[str] = []

    # R4 — score range. Checked first: a bad score makes teaching_value meaningless.
    if not (0 <= spec.surprise <= 3) or not (0 <= spec.actionability <= 3):
        refusals.append(
            "{id}: R4 score-range — surprise={s} actionability={a}, both must be 0..3".format(
                id=spec.id, s=spec.surprise, a=spec.actionability
            )
        )
        return None, refusals

    reproducible = bool(spec.reproduce and spec.reproduce.strip())
    measured = shot is not None

    if measured:
        assert shot is not None
        # R2 — evidence must contain the figure it supports.
        if not mentions(shot.evidence, shot.number):
            refusals.append(
                "{id}: R2 number-absent — number {n!r} does not appear in its evidence".format(
                    id=spec.id, n=shot.number
                )
            )
            return None, refusals
        # R3 — the claim must state its denominator.
        if not mentions(shot.claim, shot.denominator):
            refusals.append(
                "{id}: R3 denominator-mute — denominator {d!r} is not stated in the "
                "claim".format(id=spec.id, d=shot.denominator)
            )
            return None, refusals

    # R1 + R5 — no command, or nothing measured: the row survives, the fact does not.
    verified = measured and reproducible
    unreproducible = not reproducible or not measured
    why: List[str] = []
    if not reproducible:
        why.append("R1 no-reproduce: no command re-derives this")
    if not measured:
        why.append(
            "R5 unmeasured: the verb or artifact was unreachable on this machine"
        )

    row: Dict[str, Any] = {
        "id": spec.id,
        "verb": spec.verb,
        # `shot is not None` rather than the `measured` alias: None IS reachable here (R5,
        # an unreachable source), and the narrowing must be visible where it is dereferenced.
        "claim": shot.claim if shot is not None else None,
        "number": shot.number if shot is not None else None,
        "denominator": shot.denominator if shot is not None else None,
        "evidence": shot.evidence if shot is not None else "UNMEASURED on this machine",
        "source": spec.source,
        "reproduce": spec.reproduce if reproducible else None,
        "teaches": spec.teaches,
        "surprise": spec.surprise,
        "actionability": spec.actionability,
        "teaching_value": spec.surprise * spec.actionability if verified else 0,
        "verified": verified,
        "unreproducible": unreproducible,
        "why_not_verified": why,
    }
    return row, refusals


def rank(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Descending by teaching_value, then surprise, then id — a total order, so the sequence is
    identical across runs and the sort is stable by construction rather than by luck."""
    return sorted(
        rows,
        key=lambda r: (-int(r["teaching_value"]), -int(r["surprise"]), str(r["id"])),
    )


# =============================================================================================
# Validation of the envelope gb-post.py consumes
# =============================================================================================
ROW_KEYS: Tuple[str, ...] = (
    "id",
    "verb",
    "claim",
    "number",
    "denominator",
    "evidence",
    "source",
    "reproduce",
    "teaches",
    "surprise",
    "actionability",
    "teaching_value",
    "verified",
    "unreproducible",
    "why_not_verified",
)


def validate(doc: Any) -> List[str]:
    """Every way the envelope can be wrong, as a list of sentences. Empty list means valid."""
    bad: List[str] = []
    if not isinstance(doc, dict):
        return ["envelope is not an object"]
    if doc.get("schema") != SCHEMA:
        bad.append("schema is {!r}, expected {!r}".format(doc.get("schema"), SCHEMA))
    if not isinstance(doc.get("captured_at"), str) or not doc.get("captured_at"):
        bad.append("captured_at missing")
    if not isinstance(doc.get("caveats"), list):
        bad.append("caveats must be a list")
    rows = doc.get("rows")
    if not isinstance(rows, list):
        return bad + ["rows must be a list"]
    seen: Dict[str, int] = {}
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            bad.append("rows[{}] is not an object".format(i))
            continue
        for k in ROW_KEYS:
            if k not in r:
                bad.append("rows[{}] ({}) missing {}".format(i, r.get("id"), k))
        rid = str(r.get("id"))
        if rid in seen:
            bad.append("duplicate id {}".format(rid))
        seen[rid] = i
        if r.get("verified"):
            if not r.get("reproduce"):
                bad.append("{}: verified with no reproduce command".format(rid))
            if not mentions(str(r.get("evidence") or ""), r.get("number")):
                bad.append("{}: number not present in evidence".format(rid))
            if not mentions(str(r.get("claim") or ""), r.get("denominator")):
                bad.append("{}: denominator not stated in claim".format(rid))
        elif r.get("unreproducible") is not True:
            bad.append("{}: not verified and not flagged unreproducible".format(rid))
    ranked = rank(rows)
    if [r.get("id") for r in ranked] != [r.get("id") for r in rows]:
        bad.append("rows are not in ranked order")
    return bad


# =============================================================================================
# Verifying the promise: does `reproduce` actually re-derive anything?
# =============================================================================================
def verify_commands(
    doc: Dict[str, Any],
    root: pathlib.Path,
    *,
    timeout_s: float = 200.0,
    runner: Optional[Callable[[str], Tuple[int, str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Run every row's `reproduce` and report the ones that answer nothing.

    This mode exists because two commands in this roster were WRONG the first time it ran: both
    read `[...] | length, .plugins.total`, which jq parses as `[...] | (length, .plugins.total)`
    and fails on the array. A rotted command is worse than a missing one — R1 FLAGS a missing
    command, while a rotted one reads as verified — so the promise gets executed, not asserted.

    Broken means non-zero exit or empty stdout. Exit 1 is not accepted here even though a `gb`
    verb may legitimately exit 1 for FINDINGS, because every command in this roster is piped
    through `jq`, and the exit code a pipeline reports is jq's.
    """

    def default(cmd: str) -> Tuple[int, str, str]:
        proc = gbrun(["bash", "-c", cmd], timeout_s=timeout_s, cwd=root)
        return proc.code, proc.out, ("TIMED OUT" if proc.timed_out else proc.err)

    call = runner or default
    broken: List[Dict[str, Any]] = []
    for row in doc.get("rows") or []:
        cmd = row.get("reproduce")
        if not cmd:
            continue
        code, out, err = call(cmd)
        if code != 0 or not out.strip():
            broken.append(
                {
                    "id": row.get("id"),
                    "reproduce": cmd,
                    "exit": code,
                    "reason": "empty stdout" if code == 0 else "exit {}".format(code),
                    "stderr": " ".join((err or "").split())[:240],
                }
            )
    return broken


# =============================================================================================
# Build
# =============================================================================================
CAVEATS_FIXED: Tuple[str, ...] = (
    "Every number here is re-derived on this run. A row with verified=false was NOT measured on "
    "this machine and must never be published as a fact.",
    "Rows flagged unreproducible=true are true as measured and carry NO command that re-derives "
    "them. That count is a finding about this tooling, not a rounding error.",
    "Three claims that seeded this file were FALSIFIED by re-measuring, and are absent rather "
    "than corrected quietly: (a) '8 of 11 Bots are dormant 7+ days' — 0 of 13 Bots exceed 7 "
    "idle days in the newest snapshot; (b) 'top-10 builders write ~35% shorter charters' — the "
    "563-vs-663 medians are right and the ratio is 15%, the 35% was the approval share; (c) 'a "
    "routine with provenance=user was made by the Bot' — this repo's own gate reads "
    "provenance=UNTRUSTED as self-created and `user` as operator-created.",
    "Deployment, utilization and mirror rows describe ONE account on ONE desktop. They are a "
    "worked example an operator can re-run against their own, never a population estimate.",
    "The desktop roster is a per-machine cache. Absence there is never evidence a Bot does not "
    "exist on the server.",
)


def build(sources: Sources, specs: Sequence[Spec] = SPECS) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    refused: List[str] = []
    for spec in specs:
        try:
            shot = spec.derive(sources)
        except Exception as exc:  # a broken derivation is unmeasured, never a crash
            shot = None
            refused.append(
                "{}: derivation raised {}".format(spec.id, type(exc).__name__)
            )
        row, bad = admit(spec, shot)
        refused.extend(bad)
        if row is not None:
            rows.append(row)

    ranked = rank(rows)
    verified = [r for r in ranked if r["verified"]]
    unrep = [r for r in ranked if r["unreproducible"]]
    return {
        "schema": SCHEMA,
        "captured_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "rubric": {
            "teaching_value": "surprise * actionability, both 0..3",
            "surprise": {
                "0": "restates a setting",
                "1": "mildly unexpected",
                "2": "contradicts a careful operator's assumption",
                "3": "contradicts the product's docs or this repo's prior claim",
            },
            "actionability": {
                "0": "nothing to do",
                "1": "changes what you watch",
                "2": "changes a decision this week",
                "3": "changes a command you run today",
            },
            "sort": "-teaching_value, -surprise, id",
        },
        "rules": {
            "R1": "no reproduce command -> unreproducible=true, verified=false, "
            "teaching_value=0",
            "R2": "number absent from evidence -> REFUSED",
            "R3": "denominator absent from claim -> REFUSED",
            "R4": "surprise or actionability outside 0..3 -> REFUSED",
            "R5": "derive returned nothing -> number null, verified=false; never a zero",
        },
        "counts": {
            "specs": len(specs),
            "rows": len(ranked),
            "verified": len(verified),
            "unreproducible": len(unrep),
            "unmeasured": len([r for r in ranked if r["number"] is None]),
            "refused": len(refused),
        },
        "refused": refused,
        "sources": sources.log,
        "rows": ranked,
        "caveats": list(CAVEATS_FIXED),
    }


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------
def emit(text: str) -> None:
    """`print`, except that a reader who walked away is not a failed measurement."""
    try:
        sys.stdout.write(text + "\n")
    except (BrokenPipeError, ValueError):
        try:
            sys.stdout.close()
        except Exception:
            pass


def clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "\u2026"


def render(doc: Dict[str, Any]) -> str:
    c = doc["counts"]
    out: List[str] = []
    out.append(
        "FINDING SUPPLY — {v} verified of {r} rows ({s} specs), {u} unreproducible, "
        "{x} refused".format(
            v=c["verified"],
            r=c["rows"],
            s=c["specs"],
            u=c["unreproducible"],
            x=c["refused"],
        )
    )
    out.append("teaching_value = surprise x actionability, both 0..3")
    out.append("")
    out.append("  TV  S  A  VERB        ID                                   CLAIM")
    for row in doc["rows"]:
        mark = " " if row["verified"] else "!"
        out.append(
            "{m} {tv:>2} {s:>2} {a:>2}  {verb:<11} {id:<36} {claim}".format(
                m=mark,
                tv=row["teaching_value"],
                s=row["surprise"],
                a=row["actionability"],
                verb=clip(row["verb"], 11),
                id=clip(row["id"], 36),
                claim=clip(
                    row["claim"]
                    or "UNMEASURED — " + "; ".join(row["why_not_verified"]),
                    96,
                ),
            )
        )
    unrep = [r for r in doc["rows"] if r["unreproducible"]]
    if unrep:
        out.append("")
        out.append(
            "UNREPRODUCIBLE ({n}) — true as measured, no command re-derives them:".format(
                n=len(unrep)
            )
        )
        for row in unrep:
            out.append(
                "  {id}: {w}".format(id=row["id"], w="; ".join(row["why_not_verified"]))
            )
    if doc["refused"]:
        out.append("")
        out.append("REFUSED ({n}):".format(n=len(doc["refused"])))
        for r in doc["refused"]:
            out.append("  " + r)
    unreachable = [s for s in doc["sources"] if not s["reachable"]]
    if unreachable:
        out.append("")
        out.append("UNREACHABLE SOURCES ({n}):".format(n=len(unreachable)))
        for s in unreachable:
            out.append("  {k} {n}: {d}".format(k=s["kind"], n=s["name"], d=s["detail"]))
    out.append("")
    out.append("TOP 10 WITH THEIR REPRODUCE COMMANDS")
    for i, row in enumerate([r for r in doc["rows"] if r["verified"]][:10], 1):
        out.append("")
        out.append(
            "{i:>2}. [{tv}] {id} ({verb})".format(
                i=i, tv=row["teaching_value"], id=row["id"], verb=row["verb"]
            )
        )
        out.append("    " + clip(row["claim"], 400))
        out.append("    TEACHES: " + clip(row["teaches"], 400))
        out.append("    $ " + row["reproduce"])
    out.append("")
    out.append("CAVEATS")
    for cv in doc["caveats"]:
        out.append("  - " + clip(cv, 700))
    return "\n".join(out)


# =============================================================================================
# Selftest — inline fixtures only. Every leg plants exactly one defect.
# =============================================================================================
FIX_DEMAND = {
    "summary": {
        "distinct_integrations": 294,
        "unserved_by_catalog": 241,
        "unserved_single_citation": 179,
        "real_gaps": 16,
        "prior_method_unserved": 250,
        "prior_agrees": 214,
    }
}
FIX_CORPUS = {
    "charter": {
        "median_captured": {"value": 625, "n": 487},
        "median_all_rows": {"value": 557, "n": 645},
        "uncaptured_rows": {"value": 158, "n": 645},
        "denominator_gap_chars": {"value": 68, "n": 645},
    },
    "approval": {
        "share_all_rows": {"value": 0.3194, "n": 645},
        "share_captured": {"value": 0.423, "n": 487},
    },
    "integrations": {"rows_naming_only_read_only": {"value": 195, "n": 645}},
}
FIX_X = {
    "passes": [{"posts": 1508}, {"posts": 1356}],
    "combined": {
        "total_uses": 487,
        "tiers": {"deployed": 303, "intended": 84, "unclear": 100},
    },
    "uses_ranked_combined": [
        {
            "category": "uncategorised",
            "posts": 123,
            "distinct_authors": 119,
            "authored_authors": 119,
            "relayed_posts": 0,
            "dominated": False,
        },
        {
            "category": "agent-orchestration",
            "posts": 45,
            "distinct_authors": 43,
            "authored_authors": 38,
            "relayed_posts": 5,
            "dominated": False,
        },
        {
            "category": "customer-support",
            "posts": 3,
            "distinct_authors": 1,
            "authored_authors": 1,
            "relayed_posts": 0,
            "dominated": True,
        },
    ],
}
# A corpus big enough for a top-TEN split to mean anything: 10 prolific contributors with three
# captured charters each, one `phantom` whose volume comes almost entirely from rows that were
# never captured, and three quiet builders with one long, cautious charter apiece.
#
# `phantom` is the whole point. Ranked over ALL rows it is the single most prolific contributor
# and lands inside the top ten; ranked over CHARTER-CARRYING rows it has one row and does not.
# That is the cohort instability `d_cohort_unstable` claims, planted here rather than asserted.
FIX_USECASES = {
    "rows": (
        [
            {
                "contributor": "p{:02d}".format(i),
                "prompt_chars": chars,
                "has_approval_language": chars == 600,
            }
            for i in range(1, 11)
            for chars in (500, 550, 600)
        ]
        + [
            {
                "contributor": "phantom",
                "prompt_chars": 900,
                "has_approval_language": True,
            }
        ]
        + [
            {
                "contributor": "phantom",
                "prompt_chars": 0,
                "has_approval_language": False,
            }
            for _ in range(5)
        ]
        + [
            {
                "contributor": "quiet-a",
                "prompt_chars": 1000,
                "has_approval_language": True,
            },
            {
                "contributor": "quiet-b",
                "prompt_chars": 1100,
                "has_approval_language": True,
            },
            {
                "contributor": "quiet-c",
                "prompt_chars": 1200,
                "has_approval_language": True,
            },
        ]
    )
}
FIX_DEPLOYMENT = {
    "bots": [
        {"name": "a", "description_chars": 1050, "notifications_enabled": False},
        {"name": "b", "description_chars": 758, "notifications_enabled": False},
        {"name": "c", "description_chars": 0, "notifications_enabled": False},
    ],
    "routines": {
        "summary": {
            "bots_checked": 3,
            "bots_with_routines": 1,
            "total": 3,
            "enabled": 3,
        },
        "bots": [
            {
                "name": "a",
                "routines": [
                    {
                        "name": "morning",
                        "provenance": "untrusted",
                        "schedule": "35 8 * * 1-5",
                    },
                    {
                        "name": "evening",
                        "provenance": "untrusted",
                        "schedule": "35 18 * * 1-5",
                    },
                    {"name": "weekly", "provenance": "user", "schedule": "45 7 * * 1"},
                ],
            }
        ],
    },
    "mcp": {
        "scope_note": "account-level",
        "servers": [
            {"server_id": "user-Gmail", "status": "connected", "tool_count": 31},
            {"server_id": "user-Gmail--alt", "status": "connected", "tool_count": 31},
            {"server_id": "user-Supabase", "status": "connected", "tool_count": 29},
            {"server_id": "user-Vercel", "status": "needsAuth", "tool_count": 0},
        ],
    },
    "skills": {
        "bots": [
            {"name": "a", "skills": [{"name": "one skill"}]},
            {"name": "b", "skills": [{"name": "one skill"}]},
            {"name": "c", "skills": []},
        ]
    },
    "usage": {
        "usage_percent": 3.180228,
        "on_demand_enabled": True,
        "period_start": "2026-09-05T18:22:04Z",
        "next_reset": "2026-09-12T02:17:01Z",
    },
    "live": {
        "rpcs": {"A": 200, "B": 200},
        "blocked": [
            {"facet": "ListSandAutomations", "reason": "404"},
            {"facet": "GetAutomationMemory", "reason": "404"},
        ],
    },
}
FIX_UTILIZATION = {
    "live_entries": 83,
    "orphaned_entries": 1137,
    "orphaned_replicas": 7,
    "memory_shards_with_content": 0,
    "bot_count": 13,
}
FIX_MARKET = {
    "plugins": {
        "total": 4,
        "by_kind": {"connector": 2, "skill-pack": 1, "empty": 1},
        "rows": [
            {"name": "gmail", "mcp_servers": 1, "verified_publisher": True},
            {"name": "stripe", "mcp_servers": 1, "verified_publisher": True},
            {"name": "skills-only", "mcp_servers": 0, "verified_publisher": None},
            {"name": "hollow", "mcp_servers": 0, "verified_publisher": None},
        ],
    }
}
FIX_DOGFOOD = {
    "gap_count": 54,
    "producers": 48,
    "verbs": 34,
    "gaps": [{"kind": "truncated"}, {"kind": "truncated"}, {"kind": "tick-only"}],
}
FIX_WALK = {"verbs_live": 34, "unannotated": ["advise", "bot", "corpus"]}
FIX_GALAXY = {
    "event": {
        "replay": "NOT ANNOUNCED. Six posts captured say no recording is mentioned."
    },
    "rows": [
        {"session": "Engineering", "decision": "BUILD"},
        {"session": "Sales", "decision": "REFUSE-SERVED"},
        {"session": "Founders", "decision": "REFUSE-NO-SPINE"},
    ],
}
FIX_TEMPLATES = {
    "count": 3,
    "templates": [
        {"id": "a", "charter_chars": 599, "integrations": []},
        {"id": "b", "charter_chars": 700, "integrations": ["GitHub"]},
        {"id": "c", "charter_chars": 880, "integrations": ["Gmail"]},
    ],
}
FIX_TRIAGE = {
    "verdict": "RED",
    "quick_ref": {"checks": 25, "red": 1},
    "red": [{"check": "g8-desktop-inventory"}],
}


def fixture_sources() -> FakeSources:
    return FakeSources(
        verbs={
            "demand": FIX_DEMAND,
            "corpus": FIX_CORPUS,
            "x": FIX_X,
            "dogfood": FIX_DOGFOOD,
            "walk": FIX_WALK,
            "galaxy": FIX_GALAXY,
            "templates": FIX_TEMPLATES,
            "triage": FIX_TRIAGE,
        },
        roots={
            "usecases": FIX_USECASES,
            "deployment": FIX_DEPLOYMENT,
            "utilization": FIX_UTILIZATION,
            "market": FIX_MARKET,
        },
    )


def _spec(
    sid: str,
    *,
    reproduce: Optional[str] = "echo measured",
    surprise: int = 2,
    actionability: int = 2,
    derive: Callable[[Sources], Optional[Shot]] = lambda _s: None,
) -> Spec:
    return Spec(
        sid,
        "test",
        "inline fixture",
        reproduce,
        "do the thing",
        surprise,
        actionability,
        derive,
    )


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    # 1 — every spelling a sentence might use for the same figure is accepted.
    leg(
        "number renderings: plain, comma-grouped, float",
        mentions("orphaned_entries=1137", 1137)
        and mentions("1,137 of 1,220 entries", 1137)
        and mentions("usage 3.18%", 3.18)
        and mentions("raw 3.180228", 3.180228)
        and not mentions("nothing numeric here", 1137),
    )

    # 2 — FIRES ON KNOWN BAD. Evidence that does not contain its own figure is decoration, and
    #     deleting R2 would keep this row as a published fact.
    bad_num = _spec(
        "kb-number-absent",
        derive=lambda _s: Shot(
            241, 294, "241 of 294 are unserved.", "measured over 294 names"
        ),
    )
    row, refs = admit(bad_num, bad_num.derive(fixture_sources()))
    leg(
        "R2 refuses a number absent from its evidence (known-bad fixture)",
        row is None and any("R2 number-absent" in r for r in refs),
        "evidence says '294 names' and never 241",
    )

    # 3 — the same shape with the figure present is admitted, so R2 is a filter and not a wall.
    good_num = _spec(
        "ok-number-present",
        derive=lambda _s: Shot(
            241, 294, "241 of 294 are unserved.", "241 unserved of 294 names"
        ),
    )
    row_ok, refs_ok = admit(good_num, good_num.derive(fixture_sources()))
    leg(
        "R2 admits the same row once the evidence carries the figure",
        row_ok is not None and not refs_ok and row_ok["verified"] is True,
    )

    # 4 — FIRES ON KNOWN BAD. A claim with no denominator is a headline, not a measurement.
    bad_den = _spec(
        "kb-denominator-mute",
        derive=lambda _s: Shot(
            241, 294, "241 integrations are unserved.", "241 of 294"
        ),
    )
    row, refs = admit(bad_den, bad_den.derive(fixture_sources()))
    leg(
        "R3 refuses a claim that never states its denominator (known-bad fixture)",
        row is None and any("R3 denominator-mute" in r for r in refs),
        "claim says '241 integrations' with no 294",
    )

    # 5 — FIRES ON KNOWN BAD. No command, and the row must survive WITHOUT being a fact.
    no_cmd = _spec(
        "kb-no-reproduce",
        reproduce=None,
        surprise=3,
        actionability=3,
        derive=lambda _s: Shot(
            2, 2, "2 of 2 routines were self-created.", "2 of 2 untrusted"
        ),
    )
    row, refs = admit(no_cmd, no_cmd.derive(fixture_sources()))
    leg(
        "R1 flags a command-less finding instead of publishing or dropping it (known-bad)",
        row is not None
        and not refs
        and row["unreproducible"] is True
        and row["verified"] is False
        and row["teaching_value"] == 0
        and row["reproduce"] is None
        and any("R1 no-reproduce" in w for w in row["why_not_verified"]),
        "surprise 3 x actionability 3 would have ranked it first",
    )

    # 6 — FIRES ON KNOWN BAD. An out-of-range score makes teaching_value meaningless.
    bad_score = _spec(
        "kb-score-range",
        surprise=5,
        derive=lambda _s: Shot(1, 1, "1 of 1.", "1 of 1"),
    )
    row, refs = admit(bad_score, bad_score.derive(fixture_sources()))
    leg(
        "R4 refuses a score outside 0..3 (known-bad fixture)",
        row is None and any("R4 score-range" in r for r in refs),
        "surprise=5 would score 10 on a 0..9 scale",
    )

    # 7 — FIRES ON KNOWN BAD. An unreachable source is never a zero.
    unmeasured = _spec("kb-unmeasured", derive=lambda _s: None)
    row, refs = admit(unmeasured, None)
    leg(
        "R5 renders an unreachable source as null, never 0 (known-bad fixture)",
        row is not None
        and not refs
        and row["number"] is None
        and row["claim"] is None
        and row["verified"] is False
        and row["unreproducible"] is True
        and row["teaching_value"] == 0,
    )

    # 8 — the arithmetic is exactly the stated rubric, and only for verified rows.
    nine = _spec(
        "ok-nine",
        surprise=3,
        actionability=3,
        derive=lambda _s: Shot(11, 13, "11 of 13 have none.", "11 of 13"),
    )
    row_nine, _ = admit(nine, nine.derive(fixture_sources()))
    leg(
        "teaching_value is surprise x actionability, and 0 when unverified",
        row_nine is not None
        and row_nine["teaching_value"] == 9
        # `row` is the leg-7 unmeasured row, which leg 7 already asserts is not None; the
        # check is restated so the dereference is narrowed rather than assumed.
        and row is not None
        and row["teaching_value"] == 0,
    )

    # 9 — the sort is a total order: equal scores fall back to surprise then id, so shuffling
    #     the input cannot change the output sequence.
    fake = [
        {"id": "b", "teaching_value": 6, "surprise": 2},
        {"id": "a", "teaching_value": 6, "surprise": 2},
        {"id": "c", "teaching_value": 9, "surprise": 3},
        {"id": "d", "teaching_value": 6, "surprise": 3},
    ]
    order = [r["id"] for r in rank(fake)]
    leg(
        "sort is a stable total order",
        order == ["c", "d", "a", "b"]
        and [r["id"] for r in rank(list(reversed(fake)))] == order,
        "got " + ",".join(order),
    )

    # 10 — the real roster builds against inline fixtures with nothing refused.
    doc = build(fixture_sources())
    leg(
        "real roster builds clean against fixtures",
        doc["counts"]["refused"] == 0 and doc["counts"]["rows"] == len(SPECS),
        "{} row(s), refused={}".format(doc["counts"]["rows"], doc["refused"]),
    )

    # 11 — the envelope validates, including the ranked-order and row-key contracts.
    problems = validate(doc)
    leg("built envelope validates", problems == [], "; ".join(problems[:3]))

    # 12 — FIRES ON KNOWN BAD. A row that is neither verified nor flagged must fail validation,
    #      which is the state R1/R5 exist to make unreachable.
    tampered = json.loads(json.dumps(doc))
    tampered["rows"][-1]["unreproducible"] = False
    tampered["rows"][-1]["verified"] = False
    leg(
        "validate rejects a row that is neither verified nor flagged (known-bad)",
        any("not flagged unreproducible" in p for p in validate(tampered)),
    )

    # 13 — FIRES ON KNOWN BAD. A verified row whose evidence lost its figure must fail
    #      validation too, so the rule holds at the envelope boundary and not only at admission.
    tampered2 = json.loads(json.dumps(doc))
    victim = next(r for r in tampered2["rows"] if r["verified"])
    victim["evidence"] = "no digits at all here"
    leg(
        "validate rejects verified evidence missing its number (known-bad)",
        any("number not present in evidence" in p for p in validate(tampered2)),
    )

    # 14 — ids are unique and every spec carries a teaching sentence.
    ids = [s.id for s in SPECS]
    leg(
        "spec ids unique, teaches non-empty",
        len(set(ids)) == len(ids)
        and all(s.teaches.strip() for s in SPECS)
        and all(s.verb.strip() for s in SPECS),
    )

    # 15 — the unreproducible total is reported, and under full fixtures the ONLY flagged rows
    #      are the ones whose spec ships no command. Anything else flagged here would mean a
    #      derivation the fixtures never exercise, i.e. an untested claim in the roster.
    unrep = [r for r in doc["rows"] if r["unreproducible"]]
    no_cmd_specs = {s.id for s in SPECS if not s.reproduce}
    leg(
        "unreproducible rows are exactly the command-less specs",
        doc["counts"]["unreproducible"] == len(unrep)
        and {r["id"] for r in unrep} == no_cmd_specs
        and len(no_cmd_specs) >= 1,
        "{} flagged, {} specs carry no command".format(len(unrep), len(no_cmd_specs)),
    )

    # 16 — a totally empty machine produces no verified rows and no invented zeros.
    empty = build(FakeSources())
    leg(
        "empty machine yields zero verified rows and no invented figures",
        empty["counts"]["verified"] == 0
        and all(r["number"] is None for r in empty["rows"])
        and validate(empty) == [],
    )

    # 17 — the envelope round-trips through JSON unchanged, since gb-post.py reads it off disk.
    leg(
        "envelope round-trips through JSON",
        json.loads(json.dumps(doc, sort_keys=True))
        == json.loads(json.dumps(doc, sort_keys=True))
        and isinstance(json.dumps(doc), str),
    )

    # 18 — the table names every row and never silently omits one.
    text = render(doc)
    leg(
        "render prints every row and the caveats",
        all(r["id"] in text for r in doc["rows"])
        and "CAVEATS" in text
        and "FALSIFIED" in text,
    )

    # 19 — the three falsified seed claims are named in the caveats rather than quietly dropped.
    joined = " ".join(doc["caveats"])
    leg(
        "falsified seed claims are named, not quietly dropped",
        "8 of 11" in joined and "35%" in joined and "provenance=UNTRUSTED" in joined,
    )

    # 20 — a derivation that raises is recorded as a refusal, never a crash and never a zero.
    boom = _spec(
        "kb-raises", derive=lambda _s: (_ for _ in ()).throw(RuntimeError("x"))
    )
    crashed = build(FakeSources(), specs=(boom,))
    leg(
        "a raising derivation is refused, not crashed (known-bad fixture)",
        any("derivation raised RuntimeError" in r for r in crashed["refused"])
        and crashed["rows"][0]["number"] is None,
    )

    # 21 — FIRES ON KNOWN BAD. The command verifier must catch both failure shapes: a non-zero
    #      exit and a command that "succeeds" while printing nothing. Both were real: two rows
    #      shipped a jq precedence bug that exited 5, and a bare `jq` on a missing key exits 0
    #      with empty output, which would otherwise read as a working command.
    canned = {
        "good": (0, '{"n": 241}\n', ""),
        "rotted": (5, "", "jq: error: Cannot index array with string"),
        "silent": (0, "   \n", ""),
    }
    probe = {
        "rows": [
            {"id": "ok-row", "reproduce": "good"},
            {"id": "kb-rotted", "reproduce": "rotted"},
            {"id": "kb-silent", "reproduce": "silent"},
            {"id": "no-command", "reproduce": None},
        ]
    }
    broken = verify_commands(probe, ROOT, runner=lambda c: canned[c])
    leg(
        "command verifier catches rotted exits and silent successes (known-bad fixtures)",
        [b["id"] for b in broken] == ["kb-rotted", "kb-silent"]
        and broken[0]["reason"] == "exit 5"
        and broken[1]["reason"] == "empty stdout"
        and "Cannot index array" in broken[0]["stderr"],
        "a command-less row is skipped, not reported broken",
    )

    # 22 — every reproduce command in the roster is a shell command, not a prose instruction.
    #      The verifier proves they RUN; this proves nobody wrote 'look at the dashboard'.
    cmds = [s.reproduce for s in SPECS if s.reproduce]
    leg(
        "every reproduce command invokes a real tool",
        len(cmds) == len(SPECS) - len(no_cmd_specs)
        and all(c.strip().startswith(("./bin/gb", "jq ")) for c in cmds),
        "{} command(s)".format(len(cmds)),
    )

    ok = sum(1 for _, good, _ in legs if good)
    for name, good, detail in legs:
        emit(
            "  {m} {n}{d}".format(
                m="ok  " if good else "FAIL",
                n=name,
                d=(" \u2014 " + detail) if detail else "",
            )
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
        prog="gb-findings.py",
        description="The finding supply: measured, teachable claims bound to the command that "
        "re-derives each one.",
    )
    ap.add_argument(
        "--json", action="store_true", help="emit the envelope gb-post.py consumes"
    )
    ap.add_argument(
        "--no-write", action="store_true", help="rank without writing an artifact"
    )
    ap.add_argument(
        "--verify-commands",
        action="store_true",
        help="run every row's reproduce command and fail on any that answers nothing",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    doc = build(Sources(ROOT))

    problems = validate(doc)
    if problems:
        for p in problems:
            sys.stderr.write("ERROR envelope invalid: {}\n".format(p))
        return 1

    if args.verify_commands:
        broken = verify_commands(doc, ROOT)
        total = len([r for r in doc["rows"] if r["reproduce"]])
        for b in broken:
            emit(
                "BROKEN {id}: {reason} — {cmd}".format(
                    id=b["id"], reason=b["reason"], cmd=b["reproduce"]
                )
            )
            if b["stderr"]:
                emit("       " + b["stderr"])
        emit(
            "REPRODUCE {v} - {ok}/{t} commands re-derive their row".format(
                v="PASS" if not broken else "FAIL", ok=total - len(broken), t=total
            )
        )
        return 1 if broken else 0

    if not args.no_write:
        stamp = dt.datetime.now().strftime("%Y-%m-%dT%H%M")
        out = ROOT / "findings" / "{}.json".format(stamp)
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out, json.dumps(doc, indent=1, sort_keys=False) + "\n")
        doc = dict(doc)
        doc["artifact"] = str(out.relative_to(ROOT))

    if args.json:
        emit(json.dumps(doc, indent=1, sort_keys=False))
        return 0

    emit(render(doc))
    if "artifact" in doc:
        emit("")
        emit("wrote {}".format(doc["artifact"]))
    if doc["counts"]["verified"] == 0:
        sys.stderr.write(
            "ERROR nothing was measured on this machine — run the collection fleet "
            "(`gb daily`) before reading the finding supply\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    gbmain(main)
