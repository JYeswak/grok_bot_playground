#!/usr/bin/env python3
"""gb-monitor — the thresholds that turn a RED board into a thing somebody does.

The board already tells you WHAT is wrong. `gb doctor` prints 24 checks and three of them are
red today. What it does not tell you is whether any of that is worth interrupting a day for,
and a check that is red every morning for a week stops being read at all. This is the missing
half: a small set of NAMED thresholds, each with the number it watches, the limit it watches it
against, and the one action that clears it.

WHAT IT READS. Only artifacts this repo already wrote — the newest `inventory/`, `utilization/`
and `deployment/` records, plus `bin/gb-surface-gate.py --json` as a child. It never touches the
vendor. A monitor that re-fetches is a fetcher on a timer, and it turns a 200ms poll into a
network dependency the scheduler inherits.

THE LATTICE. Every threshold answers with one of three words, and the third one is the point:

  OK          measured, inside the limit
  BREACH      measured, outside the limit — the action field says what clears it
  UNMEASURED  the input does not exist, so there is no number

UNMEASURED is never rendered as 0 and never rolls up into OK. An account with no inventory has
not spent 0% of its allowance; it has an unknown spend position, which is a different fact and
sometimes a worse one. This is the same rule the coverage ledger enforces between EMPTY and
UNMEASURED, applied to thresholds, and `NEGATIVE_EVIDENCE.md` NE-14 is the live example of an
upstream tool collapsing the two and reporting a clean bill of health for a directory it could
not read.

THE SPEND LIMIT, AND WHY 2.7% DOES NOT BREACH IT. `g12` is red today at 2.7% of the included
allowance with on-demand spending ENABLED, and the obvious move is to pick a limit under 2.7 so
the monitor agrees with the board. That would be backwards, for a measurable reason.

  * 2.7% of the included allowance, measured 2026-09-11, is consumption of PREPAID capacity.
    The vendor bills real money at exactly one boundary: 100% of that allowance, after which
    `onDemandSettings.enabled` keeps the Bots working and starts charging. 100 is not a number
    this file invented; it is the vendor's own meter.
  * The allowance is a window, not a budget line: `period_start` 2026-09-05T18:22Z ->
    `next_reset` 2026-09-12T02:17Z, a 151.9h window that was 93.1% elapsed when this was
    written. 2.7% consumed at 93.1% elapsed PROJECTS to 2.9% at reset. Gating the raw percentage
    would page at the end of a week for a number that can no longer move, and stay silent on the
    Monday when 40% is consumed in the first 12 hours. So the gated number is the projection.
  * The default limit is 80% of the allowance, projected to reset. 80 buys the one thing a
    monitor exists for: lead time. At 80% projected there is still a fifth of a ~6.3-day window
    — about 30 hours at the observed pace — to turn on-demand off or decide to pay, BEFORE the
    meter starts. A limit of 100 fires only once the money is already committed, which is a
    receipt, not an alert.
  * So: 2.7% raw / 2.9% projected is OK, and it is OK because it is 28x below the alert point
    and 34x below the vendor's billing boundary — not because the limit was drawn under it. A
    limit low enough to catch 2.7% would be measuring UNDER-use of a paid plan. That is a real
    finding, it is `g19-fleet-earning-its-keep`'s finding, and routing it through a spend ceiling
    would mean the one threshold watching money pages about idleness.

  What IS wrong today is a separate, unbounded fact: on-demand is on and nobody has written down
  a ceiling. That is `spend-ceiling-recorded`, and it BREACHES at any percentage, including 0%,
  because the liability is the missing decision and not the consumption. Two thresholds, because
  there are two facts: one goes red only after money moves, the other goes red before.

THE OTHER LIMITS.

  artifact-staleness   8 days. Not a new number: `g3` already enforces an 8-day snapshot ceiling
                       against a weekly Monday 09:15 tick (`bin/gb-install-schedule.sh`), which
                       is 7 days of cadence plus one day of slack so a single late run is not an
                       incident. Re-deriving a second freshness number here would let the monitor
                       and the gate disagree about whether the loop is alive.
  board-verdict        GREEN. Any RED check breaches. An ERROR check with no REDs is UNMEASURED,
                       not OK — the gate rolls ERROR over RED in its own verdict, so this reads
                       the per-check counts instead of the rollup and cannot report a board with
                       three reds and one error as merely unmeasurable.

EXIT CODES, for the `if` in the scheduler:

  0  every threshold measured AND inside its limit
  1  at least one BREACH
  2  usage
  3  ENVIRONMENT: at least one threshold could not be measured
  130 cancelled

Exit 3 is deliberately wider than "nothing measurable": ANY unmeasured threshold takes it. A
scheduler branching on `$? -eq 0` is the consumer most likely to inherit an UNMEASURED read as a
pass, and this is the one place that collapse can be refused once for every caller.

WATCH. `--watch --interval N` loops. It writes an artifact on the first pass and then only when
the state of some threshold CHANGES, because a watcher at 2s that writes every pass is a disk
leak wearing a monitor's clothes. SIGINT exits 130 mid-loop with no partial artifact — the write
goes through `atomic_write_json`, so the temp file is unlinked and the real path is never
touched. `--watch --json` is refused: `--json` means stdout parses as ONE document, and a loop
cannot honour that. A long-running watcher's machine-readable surface is `monitor/`, which is
strictly better than a stream because it is dated and survives the terminal.

  gb-monitor.py                       # one pass, table, writes monitor/<stamp>.json
  gb-monitor.py --json                # one pass, the artifact on stdout
  gb-monitor.py --dry-run             # measure, write nothing
  gb-monitor.py --budget-pct 50       # tighter spend alert point
  gb-monitor.py --watch --interval 60 # loop until SIGINT
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import json
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import arg, parse  # noqa: E402
from gblib import dated_children  # noqa: E402
from gbtypes import Severity, atomic_write_json, main, read_json_capped, run  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE = pathlib.Path(__file__).resolve().parent / "gb-surface-gate.py"
MONITOR_DIR = "monitor"

# The vendor's own billing boundary: past 100% of the included allowance, `on_demand_enabled`
# turns continued work into charges. Every spend number here is a fraction of THIS.
ALLOWANCE_FULL_PCT = 100.0

# Alert point, projected to period end. See the module docstring for the derivation; the short
# version is that it is the last point with enough of the window left to act in.
DEFAULT_BUDGET_PCT = 80.0

# `g3`'s ceiling, reused rather than re-derived: weekly cadence + one day of slack.
DEFAULT_MAX_AGE_DAYS = 8

# `gb-surface-gate.py:80` MAX_INVENTORY_AGE_DAYS. A spend decision is a human decision that gets
# re-confirmed monthly, which is a different clock from the weekly artifact loop — but it is the
# gate's clock, and the monitor must not invent a second one.
DECISION_MAX_AGE_DAYS = 30

# Below this much of the billing window elapsed, dividing by the elapsed fraction amplifies a
# rounding artefact into a projection. Under it the raw percentage is gated instead, which
# under-alerts rather than inventing a number — and it cannot miss a real breach, because the raw
# figure only reaches the limit by actually being there.
MIN_ELAPSED_FRACTION = 0.10

DEFAULT_INTERVAL_S = 300

# Measured 2026-09-11 (`bench/`): the gate's median is 0.270s. 90s is ~330x that, which is the
# difference between "slow machine" and "wedged", and a wedged child must not wedge the monitor.
GATE_TIMEOUT_S = 90.0

# The roots this producer actually reads. A staleness threshold over roots nothing here consumes
# would be measuring somebody else's loop.
WATCHED_ROOTS: Tuple[str, ...] = ("deployment", "inventory", "utilization")


class State(str, enum.Enum):
    """A threshold's answer. `str` mixin so `json.dumps` emits the bare word."""

    OK = "OK"
    BREACH = "BREACH"
    UNMEASURED = "UNMEASURED"
    ADVISORY = "ADVISORY"
    # ADVISORY exists because one threshold here is unverifiable BY CONSTRUCTION, and a
    # threshold the tool cannot check must not fail a scheduler. Measured 2026-09-11: the
    # vendor's usage object publishes period_start, next_reset, usage_percent,
    # has_available_usage, plan_label, on_demand_enabled, on_demand_eligible and a dashboard
    # URL -- and NO spend cap and NO dollar figure. `usage_percent` is a fraction of the
    # included allowance, not money. So the operator's on-demand cap is knowable only to the
    # operator; this tool can record it and cannot confirm it.
    #
    # BREACHing on an operator's private configuration was the design error: a fresh installer
    # who has never heard of spend-decision.json would get a red board about a file, and a
    # scheduler branching on $? would fail nightly over somebody's billing preference.
    # ADVISORY reports the fact and contributes nothing to the exit code.


@dataclasses.dataclass(frozen=True)
class Threshold:
    """One named limit, what it measures, what came back, and the single action that clears it.

    `action` is mandatory and is not decoration: a threshold whose breach has no named remedy is
    a dashboard tile, and this file exists because the board already had 24 of those.
    """

    name: str
    measures: str
    state: State
    observed: Any
    observed_text: str
    limit: Any
    limit_text: str
    action: str
    evidence: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "measures": self.measures,
            "state": self.state.value,
            "observed": self.observed,
            "observed_text": self.observed_text,
            "limit": self.limit,
            "limit_text": self.limit_text,
            "action": self.action,
            "evidence": self.evidence,
        }


@dataclasses.dataclass(frozen=True)
class Args:
    """Threshold the operator surface against limits a scheduler can branch on."""

    once: bool = arg(help="a single pass and exit; the default")
    watch: bool = arg(help="loop until SIGINT, re-measuring every --interval seconds")
    interval: int = arg(
        default=DEFAULT_INTERVAL_S, help="seconds between passes under --watch"
    )
    budget_pct: float = arg(
        default=DEFAULT_BUDGET_PCT,
        help="spend alert point: percent of the included allowance, projected to period end "
        f"(default {DEFAULT_BUDGET_PCT:g}; the vendor bills at {ALLOWANCE_FULL_PCT:g})",
    )
    max_age_days: int = arg(
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"artifact freshness ceiling in days (default {DEFAULT_MAX_AGE_DAYS}, matching g3)",
    )
    root: Optional[pathlib.Path] = arg(
        help="working root to read and write; defaults to the repository root"
    )
    dry_run: bool = arg(help="measure and report, write no artifact")
    json: bool = arg(help="the artifact on stdout instead of a table")


# ---------------------------------------------------------------------------------------------
# Readers. Every one answers None rather than a default when the input is absent, so the caller
# is forced to decide between OK and UNMEASURED instead of inheriting a zero.
# ---------------------------------------------------------------------------------------------
def newest(root: pathlib.Path, name: str) -> Optional[pathlib.Path]:
    """The newest dated artifact under `<root>/<name>`, or None when there are none."""
    rows = dated_children(root / name, ".json")
    return rows[-1] if rows else None


def parse_iso(raw: Any) -> Optional[dt.datetime]:
    """An aware UTC datetime from a vendor timestamp, or None.

    The trailing `Z` is handled here because `fromisoformat` did not accept it until 3.11 and
    this repo runs on 3.9 — silently failing to parse `next_reset` would collapse the projection
    to the raw figure without saying so.
    """
    if not isinstance(raw, str) or not raw:
        return None
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def stamp_of(path: pathlib.Path) -> Optional[dt.datetime]:
    """The UTC instant encoded in a dated artifact's name.

    `YYYY-MM-DDTHHMM` when the minute is there (every producer here writes it) and midnight on
    `YYYY-MM-DD` when it is not. Device-suffixed names like `2026-09-11T0343.studio.json` carry
    the stamp in the same leading 15 characters.
    """
    head = path.name[:15]
    for fmt, width in (("%Y-%m-%dT%H%M", 15), ("%Y-%m-%d", 10)):
        try:
            return dt.datetime.strptime(head[:width], fmt).replace(
                tzinfo=dt.timezone.utc
            )
        except ValueError:
            continue
    return None


def usage_block(root: pathlib.Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """The newest recorded `usage` block and the inventory it came from.

    Newest-first across every device's inventory, because the usage block is an ACCOUNT fact that
    any desktop's pull records identically; taking the newest one means a stale second machine
    cannot drag the spend position backwards.
    """
    for path in reversed(dated_children(root / "inventory", ".json")):
        doc = read_json_capped(path)
        if isinstance(doc, dict) and isinstance(doc.get("usage"), dict):
            return doc["usage"], path.name
    return None, None


# ---------------------------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------------------------
def elapsed_fraction(usage: Dict[str, Any], now: dt.datetime) -> Optional[float]:
    """How far through the billing window we are, or None when the window is not recorded."""
    start = parse_iso(usage.get("period_start"))
    reset = parse_iso(usage.get("next_reset"))
    if start is None or reset is None or reset <= start:
        return None
    span = (reset - start).total_seconds()
    return max(0.0, min(1.0, (now - start).total_seconds() / span))


def spend_pace(
    usage: Optional[Dict[str, Any]],
    source: Optional[str],
    budget_pct: float,
    now: dt.datetime,
) -> Threshold:
    """Allowance consumption, projected to the end of the billing window, against the alert point."""
    name, measures = (
        "spend-pace",
        f"percent of the included allowance this period is on pace to consume; the vendor bills "
        f"at {ALLOWANCE_FULL_PCT:g}%",
    )
    limit_text = f"{budget_pct:g}% proj"
    if usage is None:
        return Threshold(
            name,
            measures,
            State.UNMEASURED,
            None,
            "no inventory records a usage block",
            budget_pct,
            limit_text,
            "run bin/gb-pull-inventory.py --device <label> — the spend position is unknown, "
            "which is not the same as zero",
        )
    raw = usage.get("usage_percent")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return Threshold(
            name,
            measures,
            State.UNMEASURED,
            None,
            f"{source} has a usage block with no numeric usage_percent",
            budget_pct,
            limit_text,
            "re-run bin/gb-pull-inventory.py — the recorded usage block is malformed",
            {"inventory": source, "usage_percent": raw},
        )
    fraction = elapsed_fraction(usage, now)
    projected = fraction is not None and fraction >= MIN_ELAPSED_FRACTION
    value = (
        round(float(raw) / fraction, 3)
        if projected and fraction
        else round(float(raw), 3)
    )
    basis = "projected" if projected else "raw"
    window = (
        "window not recorded"
        if fraction is None
        else f"{fraction * 100:.1f}% of the window elapsed"
    )
    detail = (
        f"raw {float(raw):.3f}%, {window}"
        if projected
        else f"{window} — too little to project from, gating the raw figure"
    )
    breached = value > budget_pct
    on_demand = usage.get("on_demand_enabled") is True
    return Threshold(
        name,
        measures,
        State.BREACH if breached else State.OK,
        value,
        f"{value:g}% {basis} ({detail})",
        budget_pct,
        limit_text,
        (
            f"on-demand spending is {'ENABLED' if on_demand else 'off'}; "
            f"open {usage.get('dashboard_url', 'the spending dashboard')} and either cap it or "
            f"accept the overage in spend-decision.json"
        )
        if breached
        else f"none — {value:g}% is inside the {budget_pct:g}% alert point",
        {
            "inventory": source,
            "usage_percent": raw,
            "basis": basis,
            "elapsed_fraction": None if fraction is None else round(fraction, 4),
            "period_start": usage.get("period_start"),
            "next_reset": usage.get("next_reset"),
            "on_demand_enabled": usage.get("on_demand_enabled"),
        },
    )


def _ceiling_ok(
    dec: Dict[str, Any], age: int, name: str, measures: str, ev: Dict[str, Any]
) -> Threshold:
    """The GREEN arm of `spend_ceiling`: on-demand is on and the decision is dated and current."""
    cap = dec.get("ceiling_usd")
    return Threshold(
        name,
        measures,
        State.OK,
        "decided",
        f"on-demand ENABLED, decision dated {str(dec['decided_at'])[:10]} ({age}d old), "
        f"ceiling {'$' + str(cap) if cap is not None else 'none set — deliberate'}",
        "dated",
        f"a decision no older than {DECISION_MAX_AGE_DAYS}d",
        f"none — re-confirm within {DECISION_MAX_AGE_DAYS - age}d",
        ev,
    )


def spend_ceiling(
    usage: Optional[Dict[str, Any]],
    source: Optional[str],
    root: pathlib.Path,
    now: dt.datetime,
) -> Threshold:
    """Whether unattended billing is bounded by a decision somebody dated.

    Independent of the percentage on purpose. `spend-pace` can only go red after money is
    committed; this one goes red while the account is still at 2.7%, which is the entire point of
    having it.
    """
    name = "spend-ceiling-recorded"
    measures = (
        f"whether on-demand billing is covered by a ceiling decision dated within "
        f"{DECISION_MAX_AGE_DAYS}d"
    )
    limit_text = f"dated <={DECISION_MAX_AGE_DAYS}d"
    ev: Dict[str, Any] = {"inventory": source, "decision_file": "spend-decision.json"}
    if usage is None:
        return Threshold(
            name,
            measures,
            State.UNMEASURED,
            None,
            "no inventory records a usage block",
            "dated",
            limit_text,
            "run bin/gb-pull-inventory.py --device <label> — whether on-demand is on is unknown",
            ev,
        )
    if usage.get("on_demand_enabled") is not True:
        return Threshold(
            name,
            measures,
            State.OK,
            "off",
            "on-demand spending is off; the account cannot bill past the included allowance",
            "dated",
            limit_text,
            "none",
            ev,
        )
    dec = read_json_capped(root / "spend-decision.json")
    if not isinstance(dec, dict) or not dec.get("decided_at"):
        return Threshold(
            name,
            measures,
            State.ADVISORY,
            "absent",
            "on-demand is ENABLED; the vendor publishes no cap and no dollar figure, so this "
            "tool cannot see yours. Record it if you want it shown here",
            "dated",
            limit_text,
            f"write {root / 'spend-decision.json'} with "
            f"{{on_demand, ceiling_usd, decided_at, why}} or turn on-demand off at "
            f"{usage.get('dashboard_url', 'the spending dashboard')}",
            ev,
        )
    try:
        decided = dt.date.fromisoformat(str(dec["decided_at"])[:10])
    except ValueError:
        return Threshold(
            name,
            measures,
            State.ADVISORY,
            "undated",
            f"spend-decision.json decided_at {dec['decided_at']!r} is not an ISO date, so the "
            f"decision is not dated",
            "dated",
            limit_text,
            "fix decided_at in spend-decision.json to YYYY-MM-DD",
            ev,
        )
    age = (now.date() - decided).days
    ev["decided_at"] = str(dec["decided_at"])[:10]
    ev["age_days"] = age
    ev["ceiling_usd"] = dec.get("ceiling_usd")
    if age > DECISION_MAX_AGE_DAYS:
        return Threshold(
            name,
            measures,
            State.ADVISORY,
            age,
            f"on-demand ENABLED and the recorded ceiling decision is {age}d old",
            DECISION_MAX_AGE_DAYS,
            limit_text,
            "re-confirm the ceiling and bump decided_at in spend-decision.json",
            ev,
        )
    return _ceiling_ok(dec, age, name, measures, ev)


def _root_age(root: pathlib.Path, name: str, now: dt.datetime) -> Dict[str, Any]:
    """One artifact root's freshness row. `age_days` is None when there is nothing to age."""
    path = newest(root, name)
    stamp = stamp_of(path) if path else None
    if path is None or stamp is None:
        return {
            "root": name,
            "newest": path.name if path else None,
            "age_days": None,
            "state": State.UNMEASURED.value,
        }
    return {
        "root": name,
        "newest": path.name,
        "age_days": round((now - stamp).total_seconds() / 86400.0, 2),
        "state": State.OK.value,
    }


def staleness(root: pathlib.Path, max_age_days: int, now: dt.datetime) -> Threshold:
    """Age of the newest artifact in every root this producer reads.

    A root with no artifacts contributes UNMEASURED, never an age of 0 — "the loop has never run
    here" and "the loop ran this minute" are the two states a zero would merge. The rollup is
    BREACH > UNMEASURED > OK, so this threshold cannot report OK while a root it is supposed to
    be watching has produced nothing: an absent root is the loudest possible staleness, and
    answering OK because the OTHER two roots are fresh is the same collapse in miniature.
    """
    rows = [_root_age(root, name, now) for name in WATCHED_ROOTS]
    for row in rows:
        age = row["age_days"]
        if isinstance(age, float) and age > max_age_days:
            row["state"] = State.BREACH.value
    stale = [r for r in rows if r["state"] == State.BREACH.value]
    absent = [str(r["root"]) for r in rows if r["state"] == State.UNMEASURED.value]
    measured = [r for r in rows if r["age_days"] is not None]
    oldest = max((float(r["age_days"]) for r in measured), default=None)
    if stale:
        state = State.BREACH
        text = "; ".join(f"{r['root']} {r['age_days']}d" for r in stale)
        action = "run bin/gb-weekly.sh — the loop has stopped feeding " + ", ".join(
            str(r["root"]) for r in stale
        )
    elif absent:
        state = State.UNMEASURED
        text = (
            f"no dated artifact in {', '.join(absent)}"
            if not measured
            else f"oldest of {len(measured)} measured root(s) is {oldest}d"
        )
        action = (
            f"run bin/gb-weekly.sh — {', '.join(absent)} has never produced an artifact"
        )
    else:
        state = State.OK
        text = f"oldest of {len(measured)} root(s) is {oldest}d"
        action = "none"
    return Threshold(
        "artifact-staleness",
        "age of the newest artifact in every root this monitor reads",
        state,
        oldest,
        text + (f" · unmeasured: {', '.join(absent)}" if absent else ""),
        max_age_days,
        f"{max_age_days}d",
        action,
        {"roots": rows},
    )


def gate_document(root: pathlib.Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """Run the gate and parse its JSON, or explain why there is no board to read.

    A nonzero exit is the gate's ANSWER (1 = findings, 2 = ERROR verdict), so the document is
    trusted whenever it parses. Only a child we stopped — deadline or output cap — or output that
    is not a board at all yields no document.
    """
    if not GATE.is_file():
        return None, f"{GATE.name} is not present next to this script"
    proc = run(
        [sys.executable, str(GATE), "--json", "--root", str(root)],
        timeout_s=GATE_TIMEOUT_S,
    )
    if proc.severity is Severity.CANCELLED:
        why = "exceeded its deadline" if proc.timed_out else "exceeded the output cap"
        return None, f"the gate {why} after {proc.elapsed_s}s"
    if proc.severity is Severity.PANICKED:
        return None, f"the gate was killed by signal {-proc.code}"
    try:
        doc = json.loads(proc.out)
    except ValueError:
        return None, f"the gate exited {proc.code} and its stdout is not JSON"
    if not isinstance(doc, dict) or "checks" not in doc:
        return None, f"the gate exited {proc.code} and its stdout is not a board"
    return doc, ""


def board(root: pathlib.Path) -> Threshold:
    """The board, read by per-check counts rather than by its rolled-up verdict.

    The gate lets ERROR dominate RED in its own rollup (`gb-surface-gate.py:1859`), so a board
    with three reds and one error reports ERROR. Reading the rollup here would file that as
    "could not measure" and drop three actionable findings on the floor.
    """
    name, measures = "board-verdict", "the surface gate's per-check verdicts"
    doc, why = gate_document(root)
    if doc is None:
        return Threshold(
            name,
            measures,
            State.UNMEASURED,
            None,
            why,
            "GREEN",
            "no RED check",
            f"run bin/gb-surface-gate.py --root {root} by hand — {why}",
        )
    checks = doc.get("checks") or []
    red = [str(c.get("check")) for c in checks if c.get("verdict") == "RED"]
    err = [str(c.get("check")) for c in checks if c.get("verdict") == "ERROR"]
    ev = {
        "rollup": doc.get("verdict"),
        "checks": len(checks),
        "red": red,
        "error": err,
        "gate_version": doc.get("version"),
    }
    if red:
        return Threshold(
            name,
            measures,
            State.BREACH,
            len(red),
            f"{len(red)} RED of {len(checks)} checks: {', '.join(red)}",
            0,
            "no RED check",
            f"clear them in order — `gb doctor` prints each one's remedy; first: {red[0]}",
            ev,
        )
    if err:
        return Threshold(
            name,
            measures,
            State.UNMEASURED,
            None,
            f"{len(err)} of {len(checks)} checks could not evaluate: {', '.join(err[:4])}"
            + (" …" if len(err) > 4 else ""),
            0,
            "no RED check",
            f"the board is not green, it is unreadable — run the producers behind {err[0]}",
            ev,
        )
    return Threshold(
        name,
        measures,
        State.OK,
        0,
        f"{len(checks)} checks, none red",
        0,
        "no RED check",
        "none",
        ev,
    )


# ---------------------------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------------------------
def evaluate(
    root: pathlib.Path, budget_pct: float, max_age_days: int, now: dt.datetime
) -> List[Threshold]:
    """Every threshold, in the order an operator should read them: money, then loop, then board."""
    usage, source = usage_block(root)
    return [
        spend_pace(usage, source, budget_pct, now),
        spend_ceiling(usage, source, root, now),
        staleness(root, max_age_days, now),
        board(root),
    ]


def roll_up(thresholds: Sequence[Threshold]) -> Tuple[str, int]:
    """(verdict, exit code) on the BREACH > UNMEASURED > OK lattice.

    Exit 0 means EVERY threshold was measured and inside its limit. The brief said "3 nothing
    measurable", and this is deliberately stronger: ANY unmeasured threshold exits 3. One
    measured-and-fine threshold alongside three that could not be read is not a pass, and a
    scheduler branching on `$? -eq 0` is exactly the consumer that would silently inherit the
    collapse this repo forbids everywhere else. BREACH still wins over UNMEASURED, because a
    known finding is more actionable than a missing input.
    """
    if any(t.state is State.BREACH for t in thresholds):
        return "BREACH", 1
    if any(t.state is State.UNMEASURED for t in thresholds):
        return "UNMEASURED", 3
    return "OK", 0


def build(
    root: pathlib.Path,
    thresholds: Sequence[Threshold],
    budget_pct: float,
    max_age_days: int,
    now: dt.datetime,
) -> Dict[str, Any]:
    """The artifact. Pure with respect to the filesystem, so `--dry-run` measures the same thing."""
    verdict, code = roll_up(thresholds)
    return {
        "schema": "gb-monitor/1",
        "captured_at": now.isoformat(timespec="seconds"),
        "root": str(root),
        "limits": {
            "budget_pct": budget_pct,
            "max_age_days": max_age_days,
            "decision_max_age_days": DECISION_MAX_AGE_DAYS,
            "allowance_full_pct": ALLOWANCE_FULL_PCT,
        },
        "verdict": verdict,
        "exit_code": code,
        "counts": {s.value: sum(1 for t in thresholds if t.state is s) for s in State},
        "breaches": [t.name for t in thresholds if t.state is State.BREACH],
        "unmeasured": [t.name for t in thresholds if t.state is State.UNMEASURED],
        "thresholds": [t.to_json() for t in thresholds],
    }


def report(doc: Dict[str, Any], thresholds: Sequence[Threshold]) -> None:
    """The table. One row per threshold, then the action for every row that is not OK."""
    print(f"monitor {doc['captured_at']} · {doc['root']}")
    print(f"{'THRESHOLD':<24}{'STATE':<12}{'LIMIT':<14}OBSERVED")
    for t in thresholds:
        print(f"{t.name:<24}{t.state.value:<12}{t.limit_text:<14}{t.observed_text}")
    todo = [t for t in thresholds if t.state is not State.OK]
    if todo:
        print()
        for t in todo:
            print(f"  {t.state.value} {t.name}: {t.action}")
    c = doc["counts"]
    print(
        f"\n{doc['verdict']} · {c['OK']} ok · {c['BREACH']} breach · "
        f"{c['UNMEASURED']} unmeasured · exit {doc['exit_code']}"
    )


def signature(thresholds: Sequence[Threshold]) -> Tuple[Tuple[str, str], ...]:
    """The state map a watch loop compares passes by. Values are deliberately excluded: a spend
    figure that moves 0.001% every pass is not a state change and must not trigger a write."""
    return tuple((t.name, t.state.value) for t in thresholds)


def one_pass(
    args: Args, root: pathlib.Path, previous: Optional[Tuple[Tuple[str, str], ...]]
) -> Tuple[int, Tuple[Tuple[str, str], ...]]:
    """Measure once, emit, and write if this is the first pass or the state map moved."""
    now = dt.datetime.now(dt.timezone.utc)
    budget = DEFAULT_BUDGET_PCT if args.budget_pct is None else args.budget_pct
    max_age = DEFAULT_MAX_AGE_DAYS if args.max_age_days is None else args.max_age_days
    thresholds = evaluate(root, budget, max_age, now)
    doc = build(root, thresholds, budget, max_age, now)
    sig = signature(thresholds)
    changed = previous is None or sig != previous

    if not args.dry_run and changed:
        out = root / MONITOR_DIR / f"{now:%Y-%m-%dT%H%M}.json"
        atomic_write_json(out, doc)
        doc["artifact"] = str(out.relative_to(root))

    if args.json:
        print(json.dumps(doc, indent=1))
    elif changed:
        report(doc, thresholds)
        if "artifact" in doc:
            print(f"  wrote {doc['artifact']}")
    else:
        c = doc["counts"]
        print(
            f"{now:%H:%M:%S}Z unchanged · {doc['verdict']} · {c['OK']} ok · "
            f"{c['BREACH']} breach · {c['UNMEASURED']} unmeasured",
            file=sys.stderr,
        )
    return int(doc["exit_code"]), sig


def usage_error(message: str, hint: str) -> int:
    print(f"gb-monitor: {message}\n    try: {hint}", file=sys.stderr)
    return 2


def validate(args: Args) -> Optional[int]:
    """Refuse the shapes that cannot mean anything, each with the command that does."""
    if args.watch and args.once:
        return usage_error(
            "--once and --watch ask for opposite things",
            "gb-monitor.py --watch --interval 60",
        )
    if args.watch and args.json:
        return usage_error(
            "--watch --json is refused: --json means stdout parses as ONE document and a loop "
            "emits many. A watcher's machine-readable surface is the dated artifact in monitor/",
            "gb-monitor.py --once --json   (or: gb-monitor.py --watch --interval 60)",
        )
    if args.interval is not None and args.interval < 1:
        return usage_error(
            f"--interval must be at least 1 second, got {args.interval}",
            "gb-monitor.py --watch --interval 60",
        )
    if args.budget_pct is not None and args.budget_pct <= 0:
        return usage_error(
            f"--budget-pct must be greater than 0, got {args.budget_pct:g} — a ceiling of zero "
            f"breaches on any consumption of a plan you already paid for",
            "gb-monitor.py --budget-pct 50",
        )
    if args.max_age_days is not None and args.max_age_days < 1:
        return usage_error(
            f"--max-age-days must be at least 1, got {args.max_age_days}",
            f"gb-monitor.py --max-age-days {DEFAULT_MAX_AGE_DAYS}",
        )
    return None


def body() -> int:
    args = parse(Args)
    bad = validate(args)
    if bad is not None:
        return bad

    root = (args.root or ROOT).resolve()
    if not root.is_dir():
        print(
            f"gb-monitor: --root {root} is not a directory — there is nothing to read",
            file=sys.stderr,
        )
        return 3

    if not args.watch:
        return one_pass(args, root, None)[0]

    interval = DEFAULT_INTERVAL_S if args.interval is None else args.interval
    print(
        f"gb-monitor: watching {root} every {interval}s — SIGINT to stop",
        file=sys.stderr,
    )
    previous: Optional[Tuple[Tuple[str, str], ...]] = None
    while True:
        _code, previous = one_pass(args, root, previous)
        # Cancellation lands here or inside the gate child; either way it unwinds through
        # `atomic_write_json`, which never leaves a partial file, and `main` maps it to 130.
        time.sleep(interval)


if __name__ == "__main__":
    main(body)
