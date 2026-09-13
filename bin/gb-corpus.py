#!/usr/bin/env python3
"""gb-corpus — the 645-Bot corpus, measured once, with every denominator stated.

THE DEFECT THIS FILE CLOSES. Every figure anyone has quoted about the attributed corpus was
computed by a throwaway inline script, at least twice, by different agents, with slightly
different denominators — and then quoted as if there were only one number. There is not. The
same snapshot honestly supports:

  median charter  625 chars  over the 487 rows that HAVE captured text
  median charter  557 chars  over all 645 rows, counting the 158 uncaptured ones as zero
  approval share  31.9%      over all 645 rows      <- what `gb templates stats` prints
  approval share  42.3%      over the 487 captured  <- and NONE of the 158 uncaptured rows can
                                                       carry approval language, because no text
                                                       was captured to search
  ints per Bot    1.640      over all 645 rows
  ints per Bot    1.878      over the 450 rows naming something that is not Grok

Two agents "disagreed" about the median while both being right, and the builder-cohort safety
finding EXISTS on one denominator and VANISHES on the other. So every figure this verb prints
carries its own `n` and a plain-language `basis`, in text and in `--json`. A number without its
denominator is not a measurement here; it is a future argument.

  gb-corpus.py stats                # the canonical figures, each with its denominator
  gb-corpus.py builders             # contributor table + the prolific-cohort safety inversion
  gb-corpus.py categories           # category mix ranked by DISTINCT builders, not bot count
  gb-corpus.py freshness            # growth as a RATE, with the window named
  gb-corpus.py stats --json         # same numbers, machine-readable, basis on every figure
  gb-corpus.py stats --corpus P     # measure a different snapshot (so two can be compared)
  gb-corpus.py --selftest           # every rule above fires on a known-bad inline fixture

CROSS-CHECK. `stats` reproduces, field for field, the corpus block `gb-templates.py stats`
prints, including its `1.878 integrations among those with a real one` — which is a DIFFERENT
numerator (Grok excluded), not a disagreement. `templates_crosscheck` in the JSON is exactly
those fields, so a divergence between the two verbs is a diff and not a conversation.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import statistics
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import main as gbmain, read_json_capped  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The charter cap this repo's templates hold themselves to. Reported here only so the corpus's
# own distribution can be compared against it — `gb-templates.py` owns the rule.
CHARTER_CAP = 900
# Above three integrations a Bot is a platform, not a charter. Same provenance: templates owns it.
INTEGRATION_CAP = 3
# Names an account's own model surface rather than an outbound capability. Must match
# `READ_ONLY_OK` in gb-templates.py or the 1.878 cross-check silently stops meaning the same
# thing. One name today; kept as a set because the reason is a category, not a special case.
READ_ONLY_OK = {"Grok"}
# One account holding at least half of a bucket is speaking for it alone. Same bar as
# `gb-demand.py`'s DOMINATED_AT, and for the same reason: `cites/builders` density flagged Gmail
# at a 14% top-builder share and missed real single-account buckets. Share is the detector.
DOMINATED_AT = 0.50
# How many builders make up the "prolific cohort" in `builders`. Ten is where the measured
# inversion was first seen; `--top` moves it, and the verb prints a sensitivity row because the
# finding must not live or die on the boundary.
TOP_BUILDERS = 10
# Rate windows. Seven days because that is the unit anyone says out loud ("36 in the last week");
# three because the corpus's own busiest stretch is three days long.
TRAILING_DAYS = 7
PEAK_DAYS = 3


# ---------------------------------------------------------------------------------------------
# A figure is a value, the population it was measured over, and what that population IS.
# ---------------------------------------------------------------------------------------------
def fig(value: Any, n: int, basis: str) -> Dict[str, Any]:
    """Every number leaves this module inside one of these. That is the whole point of the file."""
    return {"value": value, "n": n, "basis": basis}


def figures_missing_basis(obj: Any, path: str = "$") -> List[str]:
    """Any figure-shaped dict that forgot its denominator. Walked by the selftest, so the
    contract is enforced mechanically rather than by remembering to write `basis` every time."""
    bad: List[str] = []
    if isinstance(obj, dict):
        if "value" in obj and ("n" in obj or "basis" in obj):
            if (
                not isinstance(obj.get("n"), int)
                or not str(obj.get("basis") or "").strip()
            ):
                bad.append(path)
        for k, v in obj.items():
            bad += figures_missing_basis(v, "{p}.{k}".format(p=path, k=k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += figures_missing_basis(v, "{p}[{i}]".format(p=path, i=i))
    return bad


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


def _med(xs: Sequence[float]) -> Optional[float]:
    return round(statistics.median(xs), 1) if xs else None


def _mean(xs: Sequence[float]) -> Optional[float]:
    return round(statistics.mean(xs), 3) if xs else None


def _share(hit: int, total: int) -> Optional[float]:
    return round(hit / total, 4) if total else None


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else "{:.1%}".format(x)


def _q(x: Optional[float], places: int) -> Optional[float]:
    """Re-round to another verb's precision. Only used by the cross-check block, where matching
    `gb-templates.py`'s own rounding is what makes an empty diff mean agreement."""
    return None if x is None else round(x, places)


# ---------------------------------------------------------------------------------------------
# Row accessors. The snapshot is read, never typed: a corrupt row must not take the verb down.
# ---------------------------------------------------------------------------------------------
def chars(row: Dict[str, Any]) -> int:
    v = row.get("prompt_chars")
    return v if isinstance(v, int) and v > 0 else 0


def captured(row: Dict[str, Any]) -> bool:
    """The row's charter TEXT was captured. `prompt_chars: 0` means attributed-but-unread, not
    a zero-character charter — a charter cannot be zero characters long."""
    return chars(row) > 0


def ints(row: Dict[str, Any]) -> List[str]:
    v = row.get("integrations")
    return [str(i) for i in v if str(i).strip()] if isinstance(v, list) else []


def real_ints(row: Dict[str, Any]) -> List[str]:
    return [i for i in ints(row) if i not in READ_ONLY_OK]


def builder(row: Dict[str, Any]) -> str:
    return str(row.get("contributor") or "").strip() or "(unattributed)"


def approving(row: Dict[str, Any]) -> bool:
    return bool(row.get("has_approval_language"))


def added(row: Dict[str, Any]) -> Optional[datetime.date]:
    raw = str(row.get("added_at") or "").strip()
    try:
        return datetime.date.fromisoformat(raw[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------
def _newest(d: pathlib.Path) -> Optional[pathlib.Path]:
    rows = sorted(p for p in d.glob("*.json") if p.name[:10].count("-") == 2)
    return rows[-1] if rows else None


def load_corpus(
    root: pathlib.Path, explicit: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """The newest `usecases/` snapshot, or the one named by `--corpus`. `None` when there is
    nothing to measure — an absent snapshot must refuse, never print zeros that read as facts."""
    path = (
        pathlib.Path(explicit).expanduser() if explicit else _newest(root / "usecases")
    )
    if path is None or not path.is_file():
        return None
    doc = read_json_capped(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return None
    rows = [r for r in doc["rows"] if isinstance(r, dict)]
    if not rows:
        return None
    try:
        label = str(path.resolve().relative_to(root))
    except ValueError:
        label = str(path)
    return {
        "source": label,
        "captured_at": doc.get("captured_at"),
        "doc": doc,
        "rows": rows,
    }


def provenance(snap: Dict[str, Any]) -> Dict[str, Any]:
    doc, rows = snap["doc"], snap["rows"]
    return {
        "source": snap["source"],
        "captured_at": snap.get("captured_at"),
        "corpus_repo": doc.get("corpus_repo"),
        "schema": doc.get("schema"),
        "rows_read": len(rows),
        "bots_claimed_by_snapshot": doc.get("bots"),
        "rows_match_claim": doc.get("bots") in (None, len(rows)),
    }


# ---------------------------------------------------------------------------------------------
# stats — the canonical figures. Every one of them states what it divided by.
# ---------------------------------------------------------------------------------------------
def charter_figures(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """TWO MEDIANS, BOTH TRUE. This is the exact split two agents disagreed across tonight."""
    allc = [chars(r) for r in rows]
    cap = [c for c in allc if c > 0]
    n_all, n_cap = len(allc), len(cap)
    return {
        "median_captured": fig(
            _med(cap), n_cap, "rows whose charter text was captured (prompt_chars > 0)"
        ),
        "median_all_rows": fig(
            _med(allc),
            n_all,
            "every attributed row, the {} uncaptured ones counted as 0 chars".format(
                n_all - n_cap
            ),
        ),
        "mean_captured": fig(_mean(cap), n_cap, "rows with captured charter text"),
        "range_captured": fig(
            [min(cap), max(cap)] if cap else None,
            n_cap,
            "rows with captured charter text",
        ),
        "uncaptured_rows": fig(
            n_all - n_cap,
            n_all,
            "attributed rows whose charter text was never captured",
        ),
        "share_within_cap": fig(
            _share(sum(1 for c in cap if c <= CHARTER_CAP), n_cap),
            n_cap,
            "captured charters at or under the {}-char template cap".format(
                CHARTER_CAP
            ),
        ),
        "denominator_gap_chars": fig(
            round((_med(cap) or 0) - (_med(allc) or 0), 1),
            n_all,
            "how far apart the two medians are — quote the wrong basis and you are off by this",
        ),
    }


def integration_figures(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Three means over three populations. `gb-templates.py` quotes the third one."""
    slots = [len(ints(r)) for r in rows]
    with_any = [n for n in slots if n > 0]
    real = [len(real_ints(r)) for r in rows]
    with_real = [n for n in real if n > 0]
    return {
        "total_named": fig(sum(slots), len(rows), "integration slots across all rows"),
        "mean_all_rows": fig(_mean(slots), len(slots), "every attributed row"),
        "mean_rows_with_any": fig(
            _mean(with_any), len(with_any), "rows naming at least one integration"
        ),
        "mean_rows_with_a_real_one": fig(
            _mean(with_real),
            len(with_real),
            "rows naming a non-{} integration — the figure gb-templates stats prints".format(
                "/".join(sorted(READ_ONLY_OK))
            ),
        ),
        "rows_naming_only_read_only": fig(
            len(real) - len(with_real),
            len(rows),
            "rows whose only integration is {}".format("/".join(sorted(READ_ONLY_OK))),
        ),
        "share_within_cap": fig(
            _share(sum(1 for n in slots if n <= INTEGRATION_CAP), len(slots)),
            len(slots),
            "rows carrying {} integrations or fewer".format(INTEGRATION_CAP),
        ),
    }


def approval_figures(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """THE WORST DENOMINATOR IN THE CORPUS. `has_approval_language` is derived from charter TEXT,
    so on an uncaptured row it is structurally false — no text was read to search. Dividing the
    hits by all 645 rows therefore reports a CAPTURE RATE dressed as a safety rate. Both figures
    are printed, and whether the structural claim holds is checked rather than assumed."""
    cap = [r for r in rows if captured(r)]
    unc = [r for r in rows if not captured(r)]
    unc_hits = sum(1 for r in unc if approving(r))
    return {
        "share_all_rows": fig(
            _share(sum(1 for r in rows if approving(r)), len(rows)),
            len(rows),
            "every attributed row, including rows with no text to search",
        ),
        "share_captured": fig(
            _share(sum(1 for r in cap if approving(r)), len(cap)),
            len(cap),
            "rows whose charter text was captured — the only rows where absence was measured",
        ),
        "uncaptured_carrying_approval": fig(
            unc_hits,
            len(unc),
            "uncaptured rows nonetheless flagged as carrying approval language",
        ),
        "uncaptured_are_structurally_false": unc_hits == 0 and len(unc) > 0,
        "why_it_matters": (
            "the all-rows share is {} and the captured share is {}; the gap is the capture rate, "
            "not a change in how people write charters".format(
                _pct(_share(sum(1 for r in rows if approving(r)), len(rows))),
                _pct(_share(sum(1 for r in cap if approving(r)), len(cap))),
            )
        ),
    }


def stats(snap: Dict[str, Any]) -> Dict[str, Any]:
    rows = snap["rows"]
    ch = charter_figures(rows)
    it = integration_figures(rows)
    ap = approval_figures(rows)
    return {
        "schema": "gb-corpus/1",
        "verb": "stats",
        "provenance": provenance(snap),
        "population": {
            "bots": fig(len(rows), len(rows), "attributed rows in the snapshot"),
            "distinct_builders": fig(
                len({builder(r) for r in rows}),
                len(rows),
                "distinct contributor handles",
            ),
            "distinct_categories": fig(
                len({str(r.get("category") or "?") for r in rows}),
                len(rows),
                "distinct category labels",
            ),
            "distinct_integrations": fig(
                len({i for r in rows for i in ints(r)}),
                len(rows),
                "distinct integration names across all rows",
            ),
        },
        "charter": ch,
        "integrations": it,
        "approval": ap,
        # Exactly the fields `gb-templates.py stats` prints in its CORPUS block, so the two
        # verbs can be diffed instead of argued about. QUANTISED TO ITS PRECISION ON PURPOSE:
        # templates rounds means to 1 decimal and shares to 3, this file keeps 3 and 4. Left
        # alone, every comparison showed four "differences" that were only rounding — and a
        # cross-check that always disagrees is one nobody reads. Rounded here, an empty diff
        # means agreement and any diff at all means a real divergence.
        "templates_crosscheck": {
            "bots": len(rows),
            "charters_captured": ch["median_captured"]["n"],
            "charters_uncaptured": ch["uncaptured_rows"]["value"],
            "median_chars_captured": ch["median_captured"]["value"],
            "median_chars_all_rows": ch["median_all_rows"]["value"],
            "mean_chars_captured": _q(ch["mean_captured"]["value"], 1),
            "share_captured_within_cap": _q(ch["share_within_cap"]["value"], 3),
            "integrations_mean_all_bots": it["mean_all_rows"]["value"],
            "integrations_mean_with_a_real_one": it["mean_rows_with_a_real_one"][
                "value"
            ],
            "share_within_integration_cap": _q(it["share_within_cap"]["value"], 3),
            "approval_language_share": _q(ap["share_all_rows"]["value"], 3),
            "note": (
                "values are rounded to gb-templates.py's own precision (means 1dp, shares 3dp) "
                "so a diff is a divergence and not a rounding artifact; gb-templates.py prints "
                "approval_language_share over all rows, while this verb also prints it over the "
                "captured rows — the only population where a missing boundary was observed"
            ),
        },
    }


# ---------------------------------------------------------------------------------------------
# builders — who shipped what, and the inversion that only exists on one denominator.
# ---------------------------------------------------------------------------------------------
def _cohort(rows: Sequence[Dict[str, Any]], label: str) -> Dict[str, Any]:
    cap = [r for r in rows if captured(r)]
    return {
        "cohort": label,
        "bots": len(rows),
        "captured_rows": len(cap),
        "capture_rate": _share(len(cap), len(rows)),
        "median_chars_captured": _med([chars(r) for r in cap]),
        "median_chars_all_rows": _med([chars(r) for r in rows]),
        "ints_per_bot_captured": _mean([len(ints(r)) for r in cap]),
        "ints_per_bot_all_rows": _mean([len(ints(r)) for r in rows]),
        "approval_share_captured": _share(
            sum(1 for r in cap if approving(r)), len(cap)
        ),
        "approval_share_all_rows": _share(
            sum(1 for r in rows if approving(r)), len(rows)
        ),
    }


def builder_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        by[builder(r)].append(r)
    out: List[Dict[str, Any]] = []
    for who, sub in by.items():
        cap = [r for r in sub if captured(r)]
        out.append(
            {
                "builder": who,
                "bots": len(sub),
                "captured_rows": len(cap),
                "median_chars_captured": _med([chars(r) for r in cap]),
                "ints_per_bot_captured": _mean([len(ints(r)) for r in cap]),
                "ints_per_bot_all_rows": _mean([len(ints(r)) for r in sub]),
                "approval_share_captured": _share(
                    sum(1 for r in cap if approving(r)), len(cap)
                ),
                "approval_share_all_rows": _share(
                    sum(1 for r in sub if approving(r)), len(sub)
                ),
                "categories": [
                    c
                    for c, _ in collections.Counter(
                        str(r.get("category") or "?") for r in sub
                    ).most_common(3)
                ],
            }
        )
    return sorted(out, key=lambda r: (-r["bots"], r["builder"]))


def builders(snap: Dict[str, Any], top_n: int = TOP_BUILDERS) -> Dict[str, Any]:
    rows = snap["rows"]
    table = builder_rows(rows)
    top_names = {r["builder"] for r in table[: max(1, top_n)]}
    top = _cohort([r for r in rows if builder(r) in top_names], "top-{}".format(top_n))
    rest = _cohort([r for r in rows if builder(r) not in top_names], "everyone else")

    # THE FINDING, AND THE REASON IT HAS TO BE STATED WITH ITS DENOMINATOR. On the captured rows
    # the prolific cohort is measurably LESS likely to write an approval boundary. Over all rows
    # the comparison flips, because the long tail's charters were captured far less often and an
    # uncaptured row can never be flagged as approving. Same data, opposite conclusion.
    cap_gap = None
    all_gap = None
    if (
        top["approval_share_captured"] is not None
        and rest["approval_share_captured"] is not None
    ):
        cap_gap = round(
            top["approval_share_captured"] - rest["approval_share_captured"], 4
        )
    if (
        top["approval_share_all_rows"] is not None
        and rest["approval_share_all_rows"] is not None
    ):
        all_gap = round(
            top["approval_share_all_rows"] - rest["approval_share_all_rows"], 4
        )
    inverted = (
        cap_gap is not None
        and all_gap is not None
        and cap_gap != 0
        and all_gap != 0
        and (cap_gap < 0) != (all_gap < 0)
    )

    findings: List[str] = []
    if cap_gap is not None and cap_gap < 0:
        findings.append(
            "SAFETY INVERSION: the {} most prolific builders carry approval language in {} of "
            "their captured charters against {} for everyone else — the cohort shipping the most "
            "Bots is the cohort gating them least ({} points).".format(
                top_n,
                _pct(top["approval_share_captured"]),
                _pct(rest["approval_share_captured"]),
                round(cap_gap * 100, 1),
            )
        )
    elif cap_gap is not None:
        findings.append(
            "No safety inversion on the captured denominator: prolific {} vs everyone else "
            "{}.".format(
                _pct(top["approval_share_captured"]),
                _pct(rest["approval_share_captured"]),
            )
        )
    if inverted:
        findings.append(
            "DENOMINATOR TRAP: over ALL rows the same comparison reads {} vs {} and reverses the "
            "conclusion, because the prolific cohort's charters were captured {} of the time "
            "against {} for the tail. Quoting the all-rows share hides the finding.".format(
                _pct(top["approval_share_all_rows"]),
                _pct(rest["approval_share_all_rows"]),
                _pct(top["capture_rate"]),
                _pct(rest["capture_rate"]),
            )
        )

    # The finding must not live on the boundary of an arbitrary top-N. Print the neighbours.
    sens = []
    for n in sorted({max(1, top_n // 2), top_n, top_n + 1, top_n + 2, top_n * 2}):
        if n > len(table):
            continue
        names = {r["builder"] for r in table[:n]}
        t = _cohort([r for r in rows if builder(r) in names], "top-{}".format(n))
        e = _cohort([r for r in rows if builder(r) not in names], "rest")
        sens.append(
            {
                "top_n": n,
                "bots_in_cohort": t["bots"],
                "approval_captured_top": t["approval_share_captured"],
                "approval_captured_rest": e["approval_share_captured"],
                "ints_captured_top": t["ints_per_bot_captured"],
                "ints_captured_rest": e["ints_per_bot_captured"],
            }
        )

    return {
        "schema": "gb-corpus/1",
        "verb": "builders",
        "provenance": provenance(snap),
        "population": {
            "bots": fig(len(rows), len(rows), "attributed rows"),
            "distinct_builders": fig(
                len(table), len(rows), "distinct contributor handles"
            ),
            "median_bots_per_builder": fig(
                _med([r["bots"] for r in table]), len(table), "distinct builders"
            ),
            "single_bot_builders": fig(
                sum(1 for r in table if r["bots"] == 1),
                len(table),
                "builders who shipped exactly one Bot",
            ),
            "top_cohort_share_of_corpus": fig(
                _share(top["bots"], len(rows)),
                len(rows),
                "share of all Bots shipped by the top-{} builders".format(top_n),
            ),
        },
        "cohorts": {"top": top, "rest": rest},
        "cohort_gaps": {
            "approval_captured": cap_gap,
            "approval_all_rows": all_gap,
            "denominator_inverts_the_conclusion": inverted,
        },
        "findings": findings,
        "sensitivity": sens,
        "builders": table,
    }


# ---------------------------------------------------------------------------------------------
# categories — ranked by DISTINCT builders, so one account cannot look like a trend.
# ---------------------------------------------------------------------------------------------
def categories(snap: Dict[str, Any]) -> Dict[str, Any]:
    rows = snap["rows"]
    by: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        by[str(r.get("category") or "?")].append(r)

    table: List[Dict[str, Any]] = []
    for cat, sub in by.items():
        heads = collections.Counter(builder(r) for r in sub)
        top_name, top_bots = heads.most_common(1)[0]
        cap = [r for r in sub if captured(r)]
        table.append(
            {
                "category": cat,
                "bots": len(sub),
                "builders": len(heads),
                "bots_per_builder": round(len(sub) / len(heads), 2),
                "top_builder": top_name,
                "top_builder_bots": top_bots,
                "top_builder_share": _share(top_bots, len(sub)),
                "dominated_by_one_account": (top_bots / len(sub)) >= DOMINATED_AT,
                "captured_rows": len(cap),
                "median_chars_captured": _med([chars(r) for r in cap]),
                "ints_per_bot_captured": _mean([len(ints(r)) for r in cap]),
                "approval_share_captured": _share(
                    sum(1 for r in cap if approving(r)), len(cap)
                ),
                "top_integrations": [
                    "{} ({})".format(n, c)
                    for n, c in collections.Counter(
                        i for r in sub for i in real_ints(r)
                    ).most_common(3)
                ],
            }
        )
    table.sort(key=lambda r: (-r["builders"], -r["bots"], r["category"]))

    dominated = [r for r in table if r["dominated_by_one_account"]]
    thin = [
        r for r in table if not r["dominated_by_one_account"] and r["builders"] < 20
    ]
    findings: List[str] = []
    if dominated:
        findings.append(
            "ONE-ACCOUNT CATEGORIES: {} — a single builder holds at least {} of the rows, so the "
            "bot count is that account's habit, not a market signal.".format(
                ", ".join(
                    "{} ({} of {} by {})".format(
                        r["category"],
                        _pct(r["top_builder_share"]),
                        r["bots"],
                        r["top_builder"],
                    )
                    for r in dominated
                ),
                _pct(DOMINATED_AT),
            )
        )
    else:
        findings.append(
            "No category clears the {} single-account bar; the largest concentration is {} at {} "
            "({}). Ranking by bot count alone would still have told you nothing about that.".format(
                _pct(DOMINATED_AT),
                max(table, key=lambda r: r["top_builder_share"] or 0)["category"],
                _pct(
                    max(table, key=lambda r: r["top_builder_share"] or 0)[
                        "top_builder_share"
                    ]
                ),
                max(table, key=lambda r: r["top_builder_share"] or 0)["top_builder"],
            )
        )
    if thin:
        findings.append(
            "THIN EVIDENCE: {} rest on fewer than 20 distinct builders each — read their medians "
            "as anecdote, not distribution.".format(
                ", ".join(
                    "{} ({} builders)".format(r["category"], r["builders"])
                    for r in thin
                )
            )
        )
    # Bot count and builder count do not order the same way; say so when they diverge, because
    # the whole reason this verb ranks by builders is that the two orders disagree.
    by_bots = [
        r["category"] for r in sorted(table, key=lambda r: (-r["bots"], r["category"]))
    ]
    by_builders = [r["category"] for r in table]
    if by_bots != by_builders:
        findings.append(
            "RANKING DISAGREES WITH ITSELF: by bots {} · by distinct builders {}. This verb "
            "ranks by builders.".format(" > ".join(by_bots), " > ".join(by_builders))
        )

    return {
        "schema": "gb-corpus/1",
        "verb": "categories",
        "provenance": provenance(snap),
        "population": {
            "bots": fig(len(rows), len(rows), "attributed rows"),
            "categories": fig(len(table), len(rows), "distinct category labels"),
            "dominated_categories": fig(
                len(dominated),
                len(table),
                "categories where one account holds >= {} of the rows".format(
                    _pct(DOMINATED_AT)
                ),
            ),
        },
        "findings": findings,
        "categories": table,
    }


# ---------------------------------------------------------------------------------------------
# freshness — growth as a RATE. A count without its window length is not a trend.
# ---------------------------------------------------------------------------------------------
def _window(
    dated: Sequence[Tuple[datetime.date, Dict[str, Any]]],
    start: datetime.date,
    end: datetime.date,
    label: str,
) -> Dict[str, Any]:
    days = (end - start).days + 1
    hit = [r for d, r in dated if start <= d <= end]
    return {
        "window": label,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": days,
        "bots": len(hit),
        "bots_per_day": round(len(hit) / days, 3) if days else None,
        "distinct_builders": len({builder(r) for r in hit}),
    }


def _peak(
    dated: Sequence[Tuple[datetime.date, Dict[str, Any]]],
    lo: datetime.date,
    hi: datetime.date,
    span_days: int,
) -> Dict[str, Any]:
    """The busiest contiguous `span_days` stretch. Searched rather than eyeballed, so the peak
    cannot quietly become whichever three days someone remembers."""
    best: Optional[Dict[str, Any]] = None
    total = (hi - lo).days + 1
    for off in range(max(1, total - span_days + 1)):
        s = lo + datetime.timedelta(days=off)
        e = s + datetime.timedelta(days=span_days - 1)
        if e > hi:
            break
        w = _window(dated, s, e, "peak {}-day".format(span_days))
        if best is None or w["bots"] > best["bots"]:
            best = w
    return best or _window(dated, lo, hi, "peak {}-day".format(span_days))


def freshness(
    snap: Dict[str, Any], trailing: int = TRAILING_DAYS, peak_days: int = PEAK_DAYS
) -> Dict[str, Any]:
    rows = snap["rows"]
    dated = [(d, r) for d, r in ((added(r), r) for r in rows) if d is not None]
    undated = len(rows) - len(dated)
    if not dated:
        return {
            "schema": "gb-corpus/1",
            "verb": "freshness",
            "provenance": provenance(snap),
            "available": False,
            "why": "no row carries a parseable added_at",
            "undated_rows": undated,
            "findings": [],
        }

    lo = min(d for d, _ in dated)
    hi = max(d for d, _ in dated)
    per_day = collections.Counter(d.isoformat() for d, _ in dated)

    all_w = _window(dated, lo, hi, "all history")
    trail = _window(
        dated,
        hi - datetime.timedelta(days=trailing - 1),
        hi,
        "trailing {}-day".format(trailing),
    )
    prior = _window(
        dated,
        hi - datetime.timedelta(days=2 * trailing - 1),
        hi - datetime.timedelta(days=trailing),
        "prior {}-day".format(trailing),
    )
    peak = _peak(dated, lo, hi, peak_days)

    def collapse(now: Dict[str, Any], then: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        a, b = now["bots_per_day"], then["bots_per_day"]
        if not a or not b:
            return None
        return {
            "against": then["window"],
            "window_days": [now["days"], then["days"]],
            "rate_now": a,
            "rate_then": b,
            "change_in_rate": round(a / b - 1.0, 4),
            # The mistake this key exists to prevent: comparing raw counts across windows of
            # different length. 36 vs 241 is a 6.7x drop in COUNT and a 15.6x drop in RATE.
            "count_ratio_if_you_forget_the_window": round(now["bots"] / then["bots"], 4)
            if then["bots"]
            else None,
        }

    comparisons = [
        c
        for c in (collapse(trail, peak), collapse(trail, prior), collapse(trail, all_w))
        if c
    ]

    findings: List[str] = []
    for c in comparisons:
        findings.append(
            "RATE: {} bots/day over the trailing {} days against {} bots/day over the {} window "
            "({} days) — {}{:.1%} in rate. The raw counts ({} vs {}) differ by {:.2f}x, which is "
            "NOT the same number, because the windows are different lengths.".format(
                trail["bots_per_day"],
                trail["days"],
                c["rate_then"],
                c["against"],
                c["window_days"][1],
                "+" if c["change_in_rate"] >= 0 else "",
                c["change_in_rate"],
                trail["bots"],
                round(c["rate_then"] * c["window_days"][1]),
                c["count_ratio_if_you_forget_the_window"] or 0.0,
            )
        )
    cap_day = str(snap.get("captured_at") or "")[:10]
    if cap_day and cap_day > hi.isoformat():
        findings.append(
            "RIGHT-CENSORED: the snapshot was captured {} but the newest added_at is {}, so the "
            "trailing window's last {} day(s) may simply not have been collected yet. The "
            "trailing rate is a floor, not a verdict.".format(
                cap_day,
                hi.isoformat(),
                (datetime.date.fromisoformat(cap_day) - hi).days,
            )
        )
    findings.append(
        "added_at is the date the Bot was ATTRIBUTED into the directory, not the date it was "
        "built; this measures collection, and a collection slowdown and a build slowdown are "
        "different claims."
    )
    if undated:
        findings.append(
            "{} row(s) carry no parseable added_at and are excluded from every rate above.".format(
                undated
            )
        )

    return {
        "schema": "gb-corpus/1",
        "verb": "freshness",
        "provenance": provenance(snap),
        "available": True,
        "span": {
            "first_added": lo.isoformat(),
            "last_added": hi.isoformat(),
            "calendar_days": all_w["days"],
            "active_days": len(per_day),
            "dated_rows": fig(len(dated), len(rows), "rows with a parseable added_at"),
            "undated_rows": fig(undated, len(rows), "rows excluded from every rate"),
        },
        "windows": {"all": all_w, "peak": peak, "prior": prior, "trailing": trail},
        "comparisons": comparisons,
        "findings": findings,
        "per_day": [{"date": d, "bots": per_day[d]} for d in sorted(per_day)],
    }


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------
def _f(figure: Dict[str, Any]) -> str:
    v = figure.get("value")
    return "—" if v is None else str(v)


def render_stats(doc: Dict[str, Any]) -> str:
    p, pop, ch, it, ap = (
        doc["provenance"],
        doc["population"],
        doc["charter"],
        doc["integrations"],
        doc["approval"],
    )
    o: List[str] = []
    o.append(
        "CORPUS       {b} attributed Bots — {s} captured {c}".format(
            b=_f(pop["bots"]), s=p["source"], c=p.get("captured_at") or "?"
        )
    )
    o.append(
        "             {r} · {bl} distinct builders · {ct} categories · {ig} distinct "
        "integrations".format(
            r=p.get("corpus_repo") or "?",
            bl=_f(pop["distinct_builders"]),
            ct=_f(pop["distinct_categories"]),
            ig=_f(pop["distinct_integrations"]),
        )
    )
    o.append("")
    o.append(
        "CHARTER      median {m} chars over the {n} rows WITH captured text".format(
            m=_f(ch["median_captured"]), n=ch["median_captured"]["n"]
        )
    )
    o.append(
        "             median {m} chars over all {n} rows, the {u} uncaptured counted as 0".format(
            m=_f(ch["median_all_rows"]),
            n=ch["median_all_rows"]["n"],
            u=_f(ch["uncaptured_rows"]),
        )
    )
    o.append(
        "             ^ same snapshot, both true, {d} chars apart. State the basis or expect "
        "an argument.".format(d=_f(ch["denominator_gap_chars"]))
    )
    o.append(
        "             mean {mn} · range {rg} · {sc} of captured charters within the {cap}-char "
        "cap".format(
            mn=_f(ch["mean_captured"]),
            rg="–".join(str(x) for x in (ch["range_captured"]["value"] or ["—"])),
            sc=_pct(ch["share_within_cap"]["value"]),
            cap=CHARTER_CAP,
        )
    )
    o.append("")
    o.append(
        "INTEGRATIONS {ma} per Bot over all {na} rows ({tot} named in total)".format(
            ma=_f(it["mean_all_rows"]),
            na=it["mean_all_rows"]["n"],
            tot=_f(it["total_named"]),
        )
    )
    o.append(
        "             {mr} per Bot over the {nr} rows naming a non-{ro} integration "
        "({only} name only {ro}) — the figure gb templates stats prints".format(
            mr=_f(it["mean_rows_with_a_real_one"]),
            nr=it["mean_rows_with_a_real_one"]["n"],
            ro="/".join(sorted(READ_ONLY_OK)),
            only=_f(it["rows_naming_only_read_only"]),
        )
    )
    o.append(
        "             {s} carry {cap} integrations or fewer".format(
            s=_pct(it["share_within_cap"]["value"]), cap=INTEGRATION_CAP
        )
    )
    o.append("")
    o.append(
        "APPROVAL     {a} over all {na} rows — the figure gb templates stats prints".format(
            a=_pct(ap["share_all_rows"]["value"]), na=ap["share_all_rows"]["n"]
        )
    )
    o.append(
        "             {c} over the {nc} rows with captured text — the only rows where a missing "
        "boundary was OBSERVED".format(
            c=_pct(ap["share_captured"]["value"]), nc=ap["share_captured"]["n"]
        )
    )
    if ap["uncaptured_are_structurally_false"]:
        o.append(
            "             0 of the {u} uncaptured rows carry approval language and none CAN — the "
            "flag is read off charter text. The all-rows figure is a capture rate in disguise.".format(
                u=ap["uncaptured_carrying_approval"]["n"]
            )
        )
    else:
        o.append(
            "             {h} of the {u} uncaptured rows are flagged as approving, so the "
            "structural claim does NOT hold on this snapshot — treat both shares as measured.".format(
                h=_f(ap["uncaptured_carrying_approval"]),
                u=ap["uncaptured_carrying_approval"]["n"],
            )
        )
    o.append("")
    o.append(
        "CROSS-CHECK  every field above is also emitted as `templates_crosscheck` in --json, "
        "field-for-field with the CORPUS block of gb templates stats."
    )
    return "\n".join(o)


def render_builders(doc: Dict[str, Any]) -> str:
    pop, top, rest = doc["population"], doc["cohorts"]["top"], doc["cohorts"]["rest"]
    o: List[str] = []
    o.append(
        "BUILDERS     {b} Bots from {n} distinct builders · median {m} Bots each · {s} shipped "
        "exactly one".format(
            b=_f(pop["bots"]),
            n=_f(pop["distinct_builders"]),
            m=_f(pop["median_bots_per_builder"]),
            s=_f(pop["single_bot_builders"]),
        )
    )
    o.append(
        "             the {c} cohort holds {sh} of the corpus ({nb} Bots)".format(
            c=top["cohort"],
            sh=_pct(pop["top_cohort_share_of_corpus"]["value"]),
            nb=top["bots"],
        )
    )
    o.append("")
    o.append(
        "COHORTS      basis                  bots  captured   median  ints/bot  approval"
    )
    for c in (top, rest):
        o.append(
            "  {lbl:<11} captured charters only {b:>5}  {cr:>8}  {md:>7}  {ip:>8}  {ap:>8}".format(
                lbl=c["cohort"][:11],
                b=c["bots"],
                cr=_pct(c["capture_rate"]),
                md=c["median_chars_captured"],
                ip=c["ints_per_bot_captured"],
                ap=_pct(c["approval_share_captured"]),
            )
        )
        o.append(
            "  {lbl:<11} all rows               {b:>5}  {cr:>8}  {md:>7}  {ip:>8}  {ap:>8}".format(
                lbl="",
                b=c["bots"],
                cr="—",
                md=c["median_chars_all_rows"],
                ip=c["ints_per_bot_all_rows"],
                ap=_pct(c["approval_share_all_rows"]),
            )
        )
    o.append("")
    for f in doc["findings"]:
        o.append("FINDING      " + f)
    o.append("")
    o.append(
        "SENSITIVITY  top_n  cohort bots  approval top/rest (captured)  ints top/rest"
    )
    for s in doc["sensitivity"]:
        o.append(
            "             {n:>5}  {b:>11}  {at:>12} / {ar:<12}  {it} / {ir}".format(
                n=s["top_n"],
                b=s["bots_in_cohort"],
                at=_pct(s["approval_captured_top"]),
                ar=_pct(s["approval_captured_rest"]),
                it=s["ints_captured_top"],
                ir=s["ints_captured_rest"],
            )
        )
    o.append("")
    o.append("TABLE        builder              bots  capt  median  ints/bot  approval")
    for r in doc["builders"][:20]:
        o.append(
            "             {w:<20} {b:>4}  {c:>4}  {m:>6}  {i:>8}  {a:>8}".format(
                w=r["builder"][:20],
                b=r["bots"],
                c=r["captured_rows"],
                m=r["median_chars_captured"]
                if r["median_chars_captured"] is not None
                else "—",
                i=r["ints_per_bot_captured"]
                if r["ints_per_bot_captured"] is not None
                else "—",
                a=_pct(r["approval_share_captured"]),
            )
        )
    if len(doc["builders"]) > 20:
        o.append(
            "             … {n} more builders — --json for all of them".format(
                n=len(doc["builders"]) - 20
            )
        )
    return "\n".join(o)


def render_categories(doc: Dict[str, Any]) -> str:
    o: List[str] = []
    pop = doc["population"]
    o.append(
        "CATEGORIES   {c} labels over {b} Bots · ranked by DISTINCT BUILDERS, not bot count · "
        "{d} dominated by one account".format(
            c=_f(pop["categories"]),
            b=_f(pop["bots"]),
            d=_f(pop["dominated_categories"]),
        )
    )
    o.append("")
    o.append(
        "             category       bots  builders  bots/bldr  top builder (share)        "
        "median  approval"
    )
    for r in doc["categories"]:
        o.append(
            "             {c:<13} {b:>5}  {u:>8}  {r:>9}  {t:<18} {s:>6}  {m:>6}  {a:>7}{flag}".format(
                c=r["category"][:13],
                b=r["bots"],
                u=r["builders"],
                r=r["bots_per_builder"],
                t=r["top_builder"][:18],
                s=_pct(r["top_builder_share"]),
                m=r["median_chars_captured"]
                if r["median_chars_captured"] is not None
                else "—",
                a=_pct(r["approval_share_captured"]),
                flag="  <- ONE ACCOUNT" if r["dominated_by_one_account"] else "",
            )
        )
    o.append("")
    for f in doc["findings"]:
        o.append("FINDING      " + f)
    return "\n".join(o)


def render_freshness(doc: Dict[str, Any]) -> str:
    if not doc.get("available"):
        return "FRESHNESS    unavailable — {}".format(doc.get("why"))
    sp, w = doc["span"], doc["windows"]
    o: List[str] = []
    o.append(
        "FRESHNESS    {f} → {t} · {d} calendar days, {a} of them active · {n} dated rows"
        "{u}".format(
            f=sp["first_added"],
            t=sp["last_added"],
            d=sp["calendar_days"],
            a=sp["active_days"],
            n=sp["dated_rows"]["value"],
            u=" ({} undated, excluded)".format(sp["undated_rows"]["value"])
            if sp["undated_rows"]["value"]
            else "",
        )
    )
    o.append("")
    o.append(
        "WINDOWS      window          from → to                 days   bots  bots/day"
    )
    for key in ("all", "peak", "prior", "trailing"):
        x = w[key]
        o.append(
            "             {n:<14}  {a} → {b}  {d:>5}  {c:>5}  {r:>8}".format(
                n=x["window"],
                a=x["from"],
                b=x["to"],
                d=x["days"],
                c=x["bots"],
                r=x["bots_per_day"],
            )
        )
    o.append("")
    for f in doc["findings"]:
        o.append("FINDING      " + f)
    return "\n".join(o)


# ---------------------------------------------------------------------------------------------
# Selftest — inline fixtures only. Every leg plants exactly one defect, and the denominator
# legs prove the split is LOAD-BEARING by showing the wrong basis change the answer.
# ---------------------------------------------------------------------------------------------
def _row(
    who: str,
    chars_: int,
    appr: bool,
    cat: str = "Ops",
    integrations: Optional[List[str]] = None,
    date: str = "2026-08-01",
) -> Dict[str, Any]:
    return {
        "name": "{}-{}".format(who, chars_),
        "category": cat,
        "integrations": ["Gmail", "Slack"] if integrations is None else integrations,
        "contributor": who,
        "added_at": date,
        "prompt_chars": chars_,
        "has_approval_language": appr,
    }


# `loud` shipped 4, all captured, half gated. The tail shipped 6, only 2 captured, and BOTH of
# those are gated. Captured basis: loud 50% vs tail 100% -> loud is less safe. All-rows basis:
# loud 50% vs tail 33% -> loud looks SAFER. One fixture, both conclusions.
FIX_ROWS: List[Dict[str, Any]] = [
    _row("loud", 400, False, "Sales"),
    _row("loud", 600, True, "Sales"),
    _row("loud", 800, False, "Sales"),
    _row("loud", 1000, True, "Ops"),
    _row("a1", 200, True, "Personal"),
    _row("a2", 300, True, "Personal"),
    _row("a3", 0, False, "Personal"),
    _row("a4", 0, False, "Ops", ["Grok"]),
    _row("a5", 0, False, "Ops"),
    _row("a6", 0, False, "Ops"),
]

# Bot count and builder count do NOT order categories the same way, and a verb that ranks by
# bots would put `Sales` — one account, five Bots — above `Ops`, which three separate people
# reached for. This fixture exists so the disagreement is exercised rather than asserted.
FIX_RANK: List[Dict[str, Any]] = [
    _row("loud", 500, True, "Sales"),
    _row("loud", 500, True, "Sales"),
    _row("loud", 500, True, "Sales"),
    _row("loud", 500, True, "Sales"),
    _row("loud", 500, True, "Sales"),
    _row("o1", 500, True, "Ops"),
    _row("o2", 500, True, "Ops"),
    _row("o3", 500, True, "Ops"),
]

# Counts alone say the trailing week is a 3x drop; the RATE says 7x. That is the whole reason
# `freshness` refuses to print a bare count.
FIX_DATES: Dict[str, int] = {
    "2026-08-01": 8,
    "2026-08-02": 7,
    "2026-08-03": 6,
    "2026-08-20": 2,
    "2026-08-25": 1,
    "2026-08-27": 2,
    "2026-08-29": 1,
    "2026-08-31": 3,
}


def _snap(
    rows: Sequence[Dict[str, Any]], captured_at: str = "2026-09-01T00:00:00+00:00"
) -> Dict[str, Any]:
    return {
        "source": "inline-fixture",
        "captured_at": captured_at,
        "doc": {
            "schema": "gb-usecases/1",
            "rows": list(rows),
            "bots": len(rows),
            "corpus_repo": "fixture/none",
            "captured_at": captured_at,
        },
        "rows": list(rows),
    }


def _date_rows() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d, n in FIX_DATES.items():
        for i in range(n):
            out.append(_row("b{}".format(i), 500, False, "Ops", None, d))
    out.append(_row("nodate", 500, False, "Ops", None, ""))
    return out


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    snap = _snap(FIX_ROWS)
    st = stats(snap)
    ch, ap, it = st["charter"], st["approval"], st["integrations"]

    # 1 — FIRES ON KNOWN BAD. The zero-char rows MUST move the median. A single-median
    #     implementation (the throwaway-script default) reports 250 where the captured basis
    #     reports 500; if these ever agree the split has stopped being load-bearing and this
    #     leg has stopped testing anything.
    leg(
        "the two charter medians disagree (known-bad: one median over all rows)",
        ch["median_captured"]["value"] == 500.0
        and ch["median_all_rows"]["value"] == 250.0
        and ch["median_captured"]["n"] == 6
        and ch["median_all_rows"]["n"] == 10,
        "captured 500 over 6 rows vs 250 over all 10 — a 250-char error from the wrong basis",
    )

    # 2 — the gap is reported as a number, not left for the reader to subtract.
    leg(
        "denominator gap is printed, not implied",
        ch["denominator_gap_chars"]["value"] == 250.0
        and ch["uncaptured_rows"]["value"] == 4,
    )

    # 3 — approval: two shares, and the artifact named.
    leg(
        "approval reported on both bases",
        ap["share_all_rows"]["value"] == 0.4
        and ap["share_captured"]["value"] == round(4 / 6, 4)
        and ap["share_all_rows"]["n"] == 10
        and ap["share_captured"]["n"] == 6,
        "40% over 10 rows vs 66.7% over the 6 with text",
    )

    # 4 — FIRES ON KNOWN BAD. The "uncaptured rows can never be flagged" claim is CHECKED, not
    #     assumed: plant an uncaptured row that claims approval and the claim must go false.
    liar = list(FIX_ROWS) + [_row("a7", 0, True, "Ops")]
    leg(
        "structural-false claim is verified, not assumed (known-bad: a lying uncaptured row)",
        ap["uncaptured_are_structurally_false"] is True
        and stats(_snap(liar))["approval"]["uncaptured_are_structurally_false"]
        is False,
        "one uncaptured row flagged as approving flips the claim to false",
    )

    # 5 — integration means over three populations, with the read-only exclusion reproduced.
    leg(
        "three integration means, three denominators",
        it["mean_all_rows"]["value"] == 1.9
        and it["mean_all_rows"]["n"] == 10
        and it["mean_rows_with_a_real_one"]["value"] == 2.0
        and it["mean_rows_with_a_real_one"]["n"] == 9
        and it["rows_naming_only_read_only"]["value"] == 1,
        "1.9 over all rows vs 2.0 over the 9 naming something other than Grok",
    )

    # 6 — the cross-check block carries exactly the fields gb-templates.py prints, AT ITS
    #     PRECISION. Measured against the real snapshot the two verbs agreed on every figure
    #     and still showed four diffs, all of them rounding (792.692 vs 792.7, 0.8542 vs
    #     0.854, 0.9457 vs 0.946, 0.3194 vs 0.319). Quantising here is what makes an empty
    #     diff mean agreement.
    need = {
        "bots",
        "charters_captured",
        "charters_uncaptured",
        "median_chars_captured",
        "median_chars_all_rows",
        "mean_chars_captured",
        "share_captured_within_cap",
        "integrations_mean_all_bots",
        "integrations_mean_with_a_real_one",
        "share_within_integration_cap",
        "approval_language_share",
    }
    cc = st["templates_crosscheck"]

    def _places(x: Any) -> int:
        s = repr(float(x))
        return len(s.split(".")[1].rstrip("0")) if "." in s else 0

    leg(
        "templates cross-check block is complete and consistent",
        need.issubset(set(cc))
        and cc["median_chars_captured"] == ch["median_captured"]["value"]
        and cc["approval_language_share"] == round(ap["share_all_rows"]["value"], 3),
    )

    # 6b — FIRES ON KNOWN BAD. The cross-check must be quantised to templates' precision; the
    #      unrounded figure this file publishes elsewhere is NOT, and if the two ever became
    #      identical the rounding step would have gone missing without a test noticing.
    wide = stats(_snap(FIX_ROWS + [_row("p1", 777, True, "Ops")]))
    leg(
        "cross-check is quantised to templates' precision (known-bad: raw precision leaks)",
        _places(wide["templates_crosscheck"]["mean_chars_captured"]) <= 1
        and _places(wide["templates_crosscheck"]["share_captured_within_cap"]) <= 3
        and _places(wide["charter"]["mean_captured"]["value"]) > 1,
        "crosscheck mean {} vs published mean {}".format(
            wide["templates_crosscheck"]["mean_chars_captured"],
            wide["charter"]["mean_captured"]["value"],
        ),
    )

    # 7 — every figure in every verb carries its denominator. Walked, not trusted.
    bl = builders(snap, top_n=1)
    ct = categories(snap)
    fr = freshness(_snap(_date_rows()))
    missing = (
        figures_missing_basis(st)
        + figures_missing_basis(bl)
        + figures_missing_basis(ct)
        + figures_missing_basis(fr)
    )
    leg(
        "no figure ships without its n and basis", missing == [], ", ".join(missing[:3])
    )

    # 8 — the cohorts reproduce the inversion, on the captured basis.
    top, rest = bl["cohorts"]["top"], bl["cohorts"]["rest"]
    leg(
        "prolific cohort is less gated on the captured basis",
        top["approval_share_captured"] == 0.5
        and rest["approval_share_captured"] == 1.0
        and bl["cohort_gaps"]["approval_captured"] < 0,
        "loud 50% vs tail 100% over rows with text",
    )

    # 9 — FIRES ON KNOWN BAD. Over ALL rows the same comparison reverses, and the verb has to
    #     SAY the denominator flips the conclusion rather than quietly picking one.
    leg(
        "denominator inversion detected and stated (known-bad: all-rows basis reverses it)",
        top["approval_share_all_rows"] == 0.5
        and rest["approval_share_all_rows"] == round(2 / 6, 4)
        and bl["cohort_gaps"]["approval_all_rows"] > 0
        and bl["cohort_gaps"]["denominator_inverts_the_conclusion"] is True
        and any("DENOMINATOR TRAP" in f for f in bl["findings"]),
        "all-rows says the prolific cohort is safer; captured says the opposite",
    )

    # 10 — the inversion is printed as a FINDING, not buried in a table.
    leg(
        "safety inversion surfaces as a finding",
        any("SAFETY INVERSION" in f for f in bl["findings"])
        and "SAFETY INVERSION" in render_builders(bl),
    )

    # 11 — FIRES ON KNOWN BAD. A corpus with no inversion must not claim one.
    flat = [
        _row("loud", 500, True, "Ops"),
        _row("loud", 500, True, "Ops"),
        _row("z1", 500, True, "Ops"),
    ]
    leg(
        "no inversion claimed when there is none (known-bad: false-positive finding)",
        builders(_snap(flat), top_n=1)["cohort_gaps"][
            "denominator_inverts_the_conclusion"
        ]
        is False
        and not any(
            "SAFETY INVERSION" in f for f in builders(_snap(flat), top_n=1)["findings"]
        ),
    )

    # 12 — FIRES ON KNOWN BAD. Two categories with the same bot count are NOT the same
    #      evidence: Sales is one account, Personal is three. Bot count alone cannot tell them
    #      apart, which is exactly why this verb ranks by distinct builders.
    cats = {c["category"]: c for c in ct["categories"]}
    leg(
        "same bot count, different builder counts (known-bad: ranking by bots alone)",
        cats["Sales"]["bots"] == cats["Personal"]["bots"] == 3
        and cats["Sales"]["builders"] == 1
        and cats["Personal"]["builders"] == 3
        and cats["Sales"]["dominated_by_one_account"] is True
        and cats["Personal"]["dominated_by_one_account"] is False,
        "bot count says tie; builder count says one of them is one person",
    )

    # 13 — FIRES ON KNOWN BAD. When the two orders disagree the verb must SAY SO, and when
    #      they agree it must stay quiet — a warning that always prints is not a signal. On
    #      FIX_RANK, bot count crowns `Sales` (one account, five Bots) while distinct builders
    #      crown `Ops` (three people); on FIX_ROWS both orders agree and nothing is claimed.
    rank = categories(_snap(FIX_RANK))
    by_bots_top = sorted(rank["categories"], key=lambda r: -r["bots"])[0]["category"]
    leg(
        "ranking disagreement stated when it exists, silent when it does not",
        ct["categories"][0]["category"] == "Ops"
        and any("ONE-ACCOUNT" in f for f in ct["findings"])
        and not any("RANKING DISAGREES" in f for f in ct["findings"])
        and by_bots_top == "Sales"
        and rank["categories"][0]["category"] == "Ops"
        and any("RANKING DISAGREES" in f for f in rank["findings"]),
        "by bots Sales(5) leads; by builders Ops(3) leads",
    )

    # 14 — freshness: rates, with windows, and the peak window SEARCHED rather than assumed.
    w = fr["windows"]
    leg(
        "windows carry their length and a per-day rate",
        w["peak"]["bots"] == 21
        and w["peak"]["days"] == 3
        and w["peak"]["bots_per_day"] == 7.0
        and w["peak"]["from"] == "2026-08-01"
        and w["trailing"]["bots"] == 7
        and w["trailing"]["days"] == 7
        and w["trailing"]["bots_per_day"] == 1.0,
        "peak 21/3d = 7.0/day, trailing 7/7d = 1.0/day",
    )

    # 15 — FIRES ON KNOWN BAD. The count ratio (3x) and the rate ratio (7x) are different
    #      numbers, and reporting the count as if it were the trend is the known bad.
    vs_peak = [c for c in fr["comparisons"] if c["against"].startswith("peak")][0]
    leg(
        "rate change != count ratio (known-bad: comparing counts across unequal windows)",
        round(vs_peak["change_in_rate"], 4) == round(1.0 / 7.0 - 1.0, 4)
        and round(vs_peak["count_ratio_if_you_forget_the_window"], 4)
        == round(7 / 21, 4)
        and abs(vs_peak["change_in_rate"])
        > abs(vs_peak["count_ratio_if_you_forget_the_window"]),
        "-85.7% in rate vs a 0.33 count ratio — the naive read understates the collapse",
    )

    # 16 — undated rows are excluded from rate math and COUNTED, never silently dropped.
    leg(
        "undated rows excluded from rates and reported",
        fr["span"]["undated_rows"]["value"] == 1
        and fr["span"]["dated_rows"]["value"] == 30
        and any("no parseable added_at" in f for f in fr["findings"]),
    )

    # 17 — right-censoring named when the snapshot outruns the newest row.
    leg(
        "right-censoring stated when capture date outruns the data",
        any("RIGHT-CENSORED" in f for f in fr["findings"])
        and not any(
            "RIGHT-CENSORED" in f
            for f in freshness(_snap(_date_rows(), "2026-08-31T00:00:00+00:00"))[
                "findings"
            ]
        ),
    )

    # 18 — never a bare verdict: the growth story is always a rate with a window attached.
    text = render_freshness(fr)
    leg(
        "growth is rendered as a rate with its window",
        "bots/day" in text and "trailing 7-day" in text and "dying" not in text.lower(),
    )

    # 19 — renderers survive every verb and print the basis words a reader needs.
    leg(
        "renderers state the basis in text, not only in --json",
        "WITH captured text" in render_stats(st)
        and "captured charters only" in render_builders(bl)
        and "DISTINCT BUILDERS" in render_categories(ct),
    )

    # 20 — --json is valid JSON for all four verbs.
    leg(
        "all four verbs serialise",
        all(isinstance(json.loads(json.dumps(d)), dict) for d in (st, bl, ct, fr)),
    )

    # 21 — an absent snapshot refuses instead of printing zeros that read as measurements.
    leg(
        "missing corpus refuses rather than printing zeros",
        load_corpus(pathlib.Path("/nonexistent-gb-corpus-root")) is None
        and load_corpus(ROOT, "/nonexistent-gb-corpus-file.json") is None,
    )

    # 22 — a dateless corpus answers `available: false` rather than inventing a window.
    nodates = freshness(_snap([_row("x", 500, True, "Ops", None, "")]))
    leg(
        "dateless corpus refuses to draw a curve",
        nodates["available"] is False and nodates["undated_rows"] == 1,
    )

    ok = sum(1 for _, good, _ in legs if good)
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


# ---------------------------------------------------------------------------------------------
VERBS = ("stats", "builders", "categories", "freshness")


def main(argv: Optional[List[str]] = None) -> int:
    # `--selftest` is answered BEFORE argparse so the selftest stays reachable even if the verb
    # is ever made positional-and-required.
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in raw:
        return selftest()

    ap = argparse.ArgumentParser(
        prog="gb-corpus.py",
        description="The attributed Bot corpus, measured once, with every denominator stated.",
    )
    ap.add_argument("verb", nargs="?", choices=VERBS, default="stats")
    ap.add_argument(
        "--json", action="store_true", help="machine-readable, basis on every figure"
    )
    ap.add_argument(
        "--corpus", default=None, help="measure this snapshot instead of the newest"
    )
    ap.add_argument(
        "--top",
        type=int,
        default=TOP_BUILDERS,
        help="builders: size of the prolific cohort",
    )
    ap.add_argument(
        "--window",
        type=int,
        default=TRAILING_DAYS,
        help="freshness: trailing window in days",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(raw)

    snap = load_corpus(ROOT, args.corpus)
    if snap is None:
        sys.stderr.write(
            "ERROR no corpus snapshot to measure — run gb-usecases.py, or pass "
            "--corpus PATH to a usecases/*.json snapshot\n"
        )
        return 2

    if args.verb == "builders":
        doc, text = builders(snap, max(1, args.top)), None
        text = render_builders(doc)
    elif args.verb == "categories":
        doc = categories(snap)
        text = render_categories(doc)
    elif args.verb == "freshness":
        doc = freshness(snap, max(1, args.window))
        text = render_freshness(doc)
    else:
        doc = stats(snap)
        text = render_stats(doc)

    emit(json.dumps(doc, indent=1, sort_keys=False) if args.json else text)
    return 0


if __name__ == "__main__":
    gbmain(main)
