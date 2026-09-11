#!/usr/bin/env python3
"""gb-bench — the measured latency baseline of the operator surface, so acceleration has a number.

WHY THIS EXISTS (measured 2026-09-11). `gb doctor` once took 183s and reported `spine DOWN`. The
cause was not a slow subsystem: the spine proofs drive this very CLI, so doctor fanned out into
itself until the deadlines fired (`bin/gb:386-396`, the `GB_IN_DOCTOR` re-entry guard). Nobody
measured it as 183s — it was noticed as "doctor feels broken". A surface with no baseline cannot
tell a regression from a mood, and it cannot tell a slow VERB from a slow PRODUCER either.

This producer measures, and only measures. It optimises nothing. It answers three questions:

  (1) what does each hot verb actually cost, over `--repeat` runs (min / median / max)?
  (2) when a verb is slow, is the cost in the PRODUCER it delegates to, or in the dispatcher?
      Every doctor subsystem and every `gb work` substrate is timed DIRECTLY, so the residual
      (verb median minus the sum of its children) is the dispatcher's own overhead, named.
  (3) what is a defensible BUDGET for each verb, and does today's median sit inside it?

Two facts this file is careful about, both of them this repo's founding rule:

  * a nonzero exit is NOT a bench failure. The board here is RED, so `gb triage`, `gb health`
    and `gb doctor` all exit 1 by design. The exit code is recorded as data; it never
    invalidates a timing.
  * a child that is absent is UNREACHABLE, not fast. `br` and `gh` may not be on PATH, and a
    missing producer cannot be measured. Those rows say so; they never render as 0.00s.

The `gb doctor` timeout is deliberately 300s rather than something snug: if the recursion class
ever comes back, the honest answer is "183s", and a 30s deadline would have reported it as
CANCELLED — which is the one thing the pre-fix run already lied about. A regression must be
measurable, not merely detectable.

The child environment has `GB_IN_DOCTOR` REMOVED before every run, because inherited it makes
`gb doctor` skip the spine subsystem and record a ~4s doctor as the baseline. The removal is
reported in the artifact.

WHAT THAT IMMEDIATELY FOUND (measured 2026-09-11, 3 runs each). The re-entry guard BOUNDS the
recursion at depth 1; it does not remove it. `gbtypes-selftest.py` costs 16.53s invoked plainly
and 8.35s with `GB_IN_DOCTOR` set, because its t8 leg drives `gb doctor`
(`bin/gbtypes-selftest.py:596`) and with the guard clear that nested doctor runs its OWN spine
subsystem — this same selftest, one level down. So the spine producer has TWO costs, 2x apart,
and only the guarded one may be subtracted from the doctor median. It is timed both ways here;
subtracting the wrong one made the dispatcher's residual -7.6s, which is how the difference was
noticed at all.

  gb-bench.py                          # 5 runs of every hot verb, write bench/<stamp>.json
  gb-bench.py --repeat 3 --json        # the artifact on stdout, nothing else
  gb-bench.py --dry-run                # measure, print, write nothing
  gb-bench.py --verb doctor            # just the two doctor rows
  gb-bench.py --verb producer          # just the attribution children
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
import platform
import shutil
import statistics
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import arg, parse  # noqa: E402
from gblib import dated_children  # noqa: E402
from gbtypes import Proc, Severity, atomic_write_json, main, run  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
GB = BIN / "gb"
BENCH_ROOT = ROOT / "bench"

# `bin/gb:392` skips the spine subsystem when this is set, to break doctor -> spine -> gb doctor.
# Inheriting it would silently cut ~8s off the doctor baseline. Cleared, never set.
REENTRY_ENV = "GB_IN_DOCTOR"

# The CONFIGURED rule, quoted from `bin/gb:441-449`: a working root is UNCONFIGURED exactly when
# there are zero dated artifacts across every producer root AND there is no device registry.
# Re-stated rather than imported because `bin/gb` is not an importable module name and importing
# the dispatcher to bench the dispatcher is a cycle nobody should have to reason about.
PRODUCER_ROOTS: Tuple[str, ...] = (
    "deployment",
    "inventory",
    "surface",
    "market",
    "usecases",
    "discovery",
    "mcp",
    "typecheck",
    "archive",
    "utilization",
    "gaps",
)
DEVICE_REGISTRY = "desktops.json"

# `gb work` reads two substrates; these are the exact argv from `bin/gb:732` and `bin/gb:799-804`.
BR_READONLY: Tuple[str, ...] = ("--no-auto-flush", "--no-auto-import")
ISSUES_REPO = "JYeswak/grok_bot_playground"
ISSUE_FIELDS = "number,title,state,labels,updatedAt"


@dataclasses.dataclass(frozen=True)
class Args:
    """Measure the latency of the hot operator surface. Measures only; changes nothing."""

    repeat: int = arg(default=5, help="runs per row; min/median/max are over these")
    verb: Tuple[str, ...] = arg(
        help="row to measure (repeatable). Matches a whole label or its leading words: "
        "`--verb triage` takes both triage rows, `--verb producer` takes every "
        "attribution child. Omit for all."
    )
    dry_run: bool = arg(help="measure and print, write no artifact")
    json: bool = arg(help="the artifact on stdout instead of a table")


@dataclasses.dataclass(frozen=True)
class Bench:
    """One thing to time, how to invoke it, and what it is allowed to cost.

    `budget_s` is a declared TARGET with a stated reason, not last week's measurement plus
    slack. A budget derived from the current number can only ever say "unchanged", which is the
    one thing a budget is not for.
    """

    label: str
    family: str  # "verb" | "producer"
    kind: str  # "gb" (through the dispatcher) | "bin" (a producer) | "which" (an external tool)
    argv: Tuple[str, ...]
    timeout_s: float
    budget_s: float
    why: str
    # Env laid over the cleared child environment. One row needs it: `gb doctor` invokes the
    # spine producer with the re-entry guard SET (`bin/gb:402`), and that halves what the spine
    # producer costs (16.4s -> 8.4s, measured below). Attribution has to invoke the child the
    # way the verb does, or the residual it computes is fiction.
    env_extra: Tuple[Tuple[str, str], ...] = ()

    def resolve(self) -> Tuple[Optional[Tuple[str, ...]], str]:
        """(argv, "") when this can be invoked here, or (None, reason) when it cannot.

        `gbtypes.run` does not catch `FileNotFoundError`, so an unresolvable target must be
        caught BEFORE the spawn — and "not installed" is a different answer from "instant".

        The third refusal is the recursion one, and it is here because this producer CLEARS
        `GB_IN_DOCTOR`. The guard in `bin/gb:392` is keyed on the subsystem NAME ("spine"), so if
        anyone ever adds a bench subsystem the chain doctor -> bench -> doctor -> bench has
        nothing to stop it, and clearing the env would defeat the one guard that exists. When the
        guard is inherited we are inside a doctor probe: the doctor rows are refused with a
        reason instead of measured, which surfaces as ENVIRONMENT rather than as a hang.
        """
        if self.kind == "gb":
            if not GB.is_file():
                return (
                    None,
                    f"{GB} is absent — there is no CLI on this machine to measure",
                )
            if self.argv and self.argv[0] == "doctor" and REENTRY_ENV in os.environ:
                return None, (
                    f"{REENTRY_ENV} is set, so this bench is running inside a doctor probe — "
                    f"timing `gb doctor` from here would recurse without a bound. Run "
                    f"gb-bench.py on its own"
                )
            return (sys.executable, str(GB), *self.argv), ""
        if self.kind == "bin":
            script = BIN / self.argv[0]
            if not script.is_file():
                return (
                    None,
                    f"bin/{self.argv[0]} is not in bin/ — nothing here can time it",
                )
            return (sys.executable, str(script), *self.argv[1:]), ""
        exe = shutil.which(self.argv[0])
        if exe is None:
            return None, f"`{self.argv[0]}` is not on PATH — unmeasured, not instant"
        return (exe, *self.argv[1:]), ""


# ---------------------------------------------------------------------------------------------
# The hot set. Every row is a verb an agent or a scheduler actually types; the budget column is
# the argument for what it should cost, in one line, from its ROLE rather than from its timing.
# ---------------------------------------------------------------------------------------------
VERBS: Tuple[Bench, ...] = (
    Bench(
        "capabilities",
        "verb",
        "gb",
        ("capabilities",),
        60.0,
        0.30,
        "the contract an agent reads before it does anything else; discovery must cost less "
        "than the work it is deciding between",
    ),
    Bench(
        "info",
        "verb",
        "gb",
        ("info",),
        60.0,
        0.30,
        "a pure print of static text — its only floor is interpreter start",
    ),
    Bench(
        "platform",
        "verb",
        "gb",
        ("platform",),
        60.0,
        0.30,
        "classifies the OS from env and paths; no child process, no network",
    ),
    Bench(
        "validate plugin",
        "verb",
        "gb",
        ("validate", "plugin"),
        60.0,
        0.50,
        "one manifest, static rules, one child — a pure read should not reach half a second",
    ),
    Bench(
        "triage",
        "verb",
        "gb",
        ("triage",),
        60.0,
        1.00,
        "the verb the quick-ref tells everyone to start with, and the one an agent calls in "
        "a loop; over 1s and the loop pays more for asking than for acting",
    ),
    Bench(
        "triage --json",
        "verb",
        "gb",
        ("triage", "--json"),
        60.0,
        1.00,
        "same read as `triage`; the envelope must not cost more than the human table",
    ),
    Bench(
        "health",
        "verb",
        "gb",
        ("health",),
        60.0,
        1.00,
        "documented as 'lighter than doctor, safe in a loop' — loop-safe means sub-second",
    ),
    Bench(
        "doctor --scope gate",
        "verb",
        "gb",
        ("doctor", "--scope", "gate"),
        120.0,
        1.00,
        "one subsystem, one child; a scoped probe is what an operator types while watching, "
        "so it must stay interactive",
    ),
    Bench(
        "work",
        "verb",
        "gb",
        ("work",),
        120.0,
        3.00,
        "joins two external substrates (4 `br` reads, 2 `gh` reads) and one of them is a "
        "network call; the budget is dominated by the network, not by this repo",
    ),
    Bench(
        "doctor",
        "verb",
        "gb",
        ("doctor",),
        300.0,
        20.00,
        "full fan-out over 6 subsystems, one of which is an 8s cancel-correctness proof that "
        "must really spawn and really signal; set just above the honest cost and far below "
        "the 183s recursion class it exists to catch",
    ),
)

# The children the two slowest verbs delegate to. Timed directly so a slow verb can be blamed on
# a producer instead of on the dispatcher. Subsystem argv is quoted from `bin/gb:90-97`.
PRODUCERS: Tuple[Bench, ...] = (
    Bench(
        "producer/gate",
        "producer",
        "bin",
        ("gb-surface-gate.py",),
        120.0,
        1.00,
        "24 static checks over artifacts already on disk; no network and no toolchain",
    ),
    Bench(
        "producer/coverage",
        "producer",
        "bin",
        ("gb-coverage.py",),
        120.0,
        0.50,
        "reads the artifact roots and renders a ledger — a directory walk",
    ),
    Bench(
        "producer/types",
        "producer",
        "bin",
        ("gb-typecheck.py", "--dry-run"),
        180.0,
        3.00,
        "spawns mypy and pyright; their own startup is the floor and it is not ours to move",
    ),
    Bench(
        "producer/mcp",
        "producer",
        "bin",
        ("gb-mcp-validate.py",),
        120.0,
        3.00,
        "probes every MCP server a Bot can reach — a network read, budgeted as one",
    ),
    Bench(
        "producer/plugin",
        "producer",
        "bin",
        ("gb-plugin-validate.py", "--path", "plugin"),
        120.0,
        0.50,
        "one manifest against static rules mined from the marketplace CONTRIBUTING",
    ),
    # TWO rows, because the spine producer has two costs and they differ by 2x. Measured
    # 2026-09-11: 16.53s with the guard cleared, 8.35s with it set. The selftest's t8 leg drives
    # `gb doctor` (bin/gbtypes-selftest.py:596); with the guard cleared that nested doctor runs
    # its OWN spine subsystem, which is this same selftest again — so the guard bounds the
    # recursion at depth 1 rather than removing it, and the standalone run pays for one extra
    # full doctor. `gb doctor` invokes it WITH the guard, so only the second row belongs in the
    # attribution of `gb doctor`; the first is what an operator pays for `gb doctor --scope spine`.
    Bench(
        "producer/spine",
        "producer",
        "bin",
        ("gbtypes-selftest.py",),
        180.0,
        10.00,
        "8 proofs that must really spawn, really signal and really wait out a deadline: those "
        "seconds ARE the proof. The measured floor with the re-entry guard set is 8.4s, so the "
        "budget is that plus headroom — anything above it is recursion depth, not proof, and a "
        "budget that accommodated the extra nested doctor would be blessing it",
    ),
    Bench(
        "producer/spine-in-doctor",
        "producer",
        "bin",
        ("gbtypes-selftest.py",),
        180.0,
        10.00,
        "the same producer invoked the way `gb doctor` invokes it (GB_IN_DOCTOR set), which is "
        "the only figure that can be subtracted from the doctor median honestly",
        env_extra=((REENTRY_ENV, "1"),),
    ),
    Bench(
        "producer/beads",
        "producer",
        "which",
        ("br", *BR_READONLY, "list", "--status", "all", "--json"),
        120.0,
        1.00,
        "a local ledger read; `gb work` makes four of these, so one must be cheap",
    ),
    Bench(
        "producer/gh-auth",
        "producer",
        "which",
        ("gh", "auth", "status"),
        60.0,
        1.50,
        "a token check against the network — the unavoidable toll on every `gb work`",
    ),
    Bench(
        "producer/gh-issues",
        "producer",
        "which",
        (
            "gh",
            "issue",
            "list",
            "--repo",
            ISSUES_REPO,
            "--json",
            ISSUE_FIELDS,
            "--limit",
            "50",
        ),
        120.0,
        2.00,
        "one paged API read; the second half of `gb work`'s network cost",
    ),
)

# verb label -> the children it fans out to. The residual (verb median - sum of child medians) is
# the dispatcher's own cost, which is the number an acceleration pass needs separated out.
ATTRIBUTION: Dict[str, Tuple[str, ...]] = {
    "doctor": (
        "producer/gate",
        "producer/coverage",
        "producer/types",
        "producer/mcp",
        "producer/plugin",
        "producer/spine-in-doctor",
    ),
    "doctor --scope gate": ("producer/gate",),
    "work": ("producer/beads", "producer/gh-auth", "producer/gh-issues"),
}

ALL: Tuple[Bench, ...] = VERBS + PRODUCERS


@dataclasses.dataclass(frozen=True)
class Sample:
    """One invocation. `code`, `timed_out` and `truncated` are carried because a timing without
    them is not a measurement of anything — a killed child is fast and wrong."""

    elapsed_s: float
    code: int
    severity: str
    timed_out: bool
    truncated: bool

    def to_json(self) -> Dict[str, Any]:
        return {
            "elapsed_s": self.elapsed_s,
            "exit": self.code,
            "severity": self.severity,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
        }


@dataclasses.dataclass(frozen=True)
class Row:
    """One bench's result: its samples, or the reason there are none."""

    bench: Bench
    argv: Tuple[str, ...]
    samples: Tuple[Sample, ...]
    unreachable: str

    @property
    def reachable(self) -> bool:
        return not self.unreachable and bool(self.samples)

    @property
    def times(self) -> List[float]:
        return [s.elapsed_s for s in self.samples]

    @property
    def median_s(self) -> Optional[float]:
        return round(statistics.median(self.times), 4) if self.samples else None

    @property
    def codes(self) -> List[int]:
        return sorted({s.code for s in self.samples})

    @property
    def verdict(self) -> str:
        """Against the declared budget. UNMEASURED is not a pass — it is the absence of one."""
        med = self.median_s
        if med is None:
            return "UNMEASURED"
        if any(s.timed_out or s.truncated for s in self.samples):
            return "UNMEASURED"
        return "WITHIN" if med <= self.bench.budget_s else "OVER"

    def to_json(self) -> Dict[str, Any]:
        times = self.times
        return {
            "label": self.bench.label,
            "family": self.bench.family,
            "argv": list(self.argv),
            "runs": len(self.samples),
            "min_s": round(min(times), 4) if times else None,
            "median_s": self.median_s,
            "max_s": round(max(times), 4) if times else None,
            "spread_s": round(max(times) - min(times), 4) if times else None,
            "stdev_s": round(statistics.stdev(times), 4) if len(times) > 1 else None,
            "exit_codes": self.codes,
            "timed_out": any(s.timed_out for s in self.samples),
            "truncated": any(s.truncated for s in self.samples),
            "unreachable": self.unreachable or None,
            "env_extra": {k: v for k, v in self.bench.env_extra} or None,
            "budget_s": self.bench.budget_s,
            "budget_why": self.bench.why,
            "verdict": self.verdict,
            "samples": [s.to_json() for s in self.samples],
        }


def child_env() -> Dict[str, str]:
    """The environment every measured child gets: ours, minus the doctor re-entry guard."""
    env = dict(os.environ)
    env.pop(REENTRY_ENV, None)
    return env


def select(wanted: Sequence[str]) -> Tuple[List[Bench], List[str]]:
    """The rows `--verb` asks for, and the values that matched nothing.

    A label matches a request when it IS the request or when the request is its leading words
    (`triage` -> `triage --json`, `producer` -> `producer/spine`). One rule; an unmatched value
    is a usage error rather than a silently empty run, because an empty bench reads as a fast one.
    """
    if not wanted:
        return list(ALL), []
    chosen: List[Bench] = []
    unknown: List[str] = []
    for value in wanted:
        hits = [
            b
            for b in ALL
            if b.label == value
            or b.label.startswith(value + " ")
            or b.label.startswith(value + "/")
        ]
        if not hits:
            unknown.append(value)
        chosen += [h for h in hits if h not in chosen]
    return chosen, unknown


def measure(bench: Bench, repeat: int, env: Dict[str, str]) -> Row:
    """Time one bench `repeat` times. A nonzero exit is DATA and never stops the loop; an
    unresolvable target stops it before the first spawn, with the reason recorded."""
    argv, why = bench.resolve()
    if argv is None:
        return Row(bench=bench, argv=(), samples=(), unreachable=why)
    child = {**env, **dict(bench.env_extra)}
    samples: List[Sample] = []
    for _ in range(repeat):
        try:
            proc: Proc = run(argv, timeout_s=bench.timeout_s, cwd=ROOT, env=child)
        except OSError as exc:
            return Row(
                bench=bench,
                argv=argv,
                samples=tuple(samples),
                unreachable=f"could not be invoked: {exc}",
            )
        samples.append(
            Sample(
                elapsed_s=proc.elapsed_s,
                code=proc.code,
                severity=Severity(proc.severity).name,
                timed_out=proc.timed_out,
                truncated=proc.truncated,
            )
        )
    return Row(bench=bench, argv=argv, samples=tuple(samples), unreachable="")


def configured() -> Tuple[bool, str]:
    """Whether this root has ever been measured, and the evidence. See the rule above."""
    measured = [n for n in PRODUCER_ROOTS if dated_children(ROOT / n)]
    total = sum(len(dated_children(ROOT / n)) for n in measured)
    registry = (ROOT / DEVICE_REGISTRY).is_file()
    if total or registry:
        return True, (
            f"{total} dated artifact(s) across {len(measured)} producer root(s); "
            f"{DEVICE_REGISTRY} {'present' if registry else 'absent'}"
        )
    return False, (
        f"no producer has ever written here (0 dated artifacts across "
        f"{len(PRODUCER_ROOTS)} roots) and there is no {DEVICE_REGISTRY}"
    )


def artifact_census() -> Dict[str, int]:
    """Dated artifacts per producer root, right now.

    Taken before and after the runs so the bench's own side effects are MEASURED rather than
    asserted. Several benched producers write on every invocation (`gb-mcp-validate.py` has no
    `--dry-run`), so a bench at `--repeat 5` leaves five real artifacts behind. That is a fact
    an operator reading `git status` afterwards deserves in the artifact, not a surprise.
    """
    return {n: len(dated_children(ROOT / n)) for n in PRODUCER_ROOTS}


def attribute(rows: Dict[str, Row]) -> List[Dict[str, Any]]:
    """For each fan-out verb measured alongside its children: where did the wall time go?

    `dispatcher_s` is the residual, and it is reported even when negative — a negative residual
    means the children cost MORE alone than inside the verb (a cache effect, or a subsystem the
    verb skipped), and hiding that would make the attribution look tighter than it is.
    """
    out: List[Dict[str, Any]] = []
    for verb, kids in ATTRIBUTION.items():
        row = rows.get(verb)
        if row is None or row.median_s is None:
            continue
        present = [
            (k, rows[k]) for k in kids if k in rows and rows[k].median_s is not None
        ]
        if not present:
            continue
        missing = [k for k in kids if k not in rows or rows[k].median_s is None]
        total = sum(r.median_s or 0.0 for _k, r in present)
        out.append(
            {
                "verb": verb,
                "verb_median_s": row.median_s,
                "children_measured_s": round(total, 4),
                "children_unmeasured": missing,
                "dispatcher_s": round((row.median_s or 0.0) - total, 4),
                "children": [
                    {
                        "label": k,
                        "median_s": r.median_s,
                        "share": (
                            round((r.median_s or 0.0) / row.median_s, 4)
                            if row.median_s
                            else None
                        ),
                    }
                    for k, r in sorted(present, key=lambda kv: -(kv[1].median_s or 0.0))
                ],
            }
        )
    return out


def build(
    rows: List[Row], repeat: int, cleared: bool, wrote: Dict[str, int]
) -> Dict[str, Any]:
    """The artifact. Pure with respect to the filesystem, so `--dry-run` is the same measurement."""
    ok, why = configured()
    by_label = {r.bench.label: r for r in rows}
    ranked = [r for r in rows if r.bench.family == "verb" and r.median_s is not None]
    ranked.sort(key=lambda r: -(r.median_s or 0.0))
    unreachable = [r.bench.label for r in rows if not r.reachable]
    return {
        "schema": "gb-bench/1",
        "measured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "repeat": repeat,
        "host": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "machine": platform.machine(),
            "system": f"{platform.system()} {platform.release()}",
            "cpu_count": os.cpu_count(),
            "interpreter": sys.executable,
        },
        "repo": {"root": str(ROOT), "configured": ok, "why": why},
        "measurement_notes": [
            f"{REENTRY_ENV} was {'removed from' if cleared else 'absent from'} the inherited "
            f"environment before every run; inherited, it makes `gb doctor` skip the spine "
            f"subsystem entirely and understate the baseline by ~8s",
            "the re-entry guard (bin/gb:392) BOUNDS the doctor recursion at depth 1, it does "
            "not remove it: measured 2026-09-11, gbtypes-selftest.py costs 16.5s with the guard "
            "clear and 8.4s with it set, because its t8 leg drives a `gb doctor` that runs its "
            "own spine subsystem. That is why the spine producer is timed TWICE — only the "
            "in-doctor figure may be subtracted from the doctor median",
            "a nonzero exit is recorded, never treated as a bench failure: this board is RED, "
            "so triage/health/doctor exit 1 by design",
            "every verb is invoked as `<interpreter> bin/gb …`, not through a shim on PATH; "
            "there is no `gb` on PATH in this checkout to compare against",
            "samples are in run order, so a cold first run is visible rather than averaged away",
        ],
        "side_effects": {
            "artifacts_written_during_this_bench": wrote,
            "note": "measured by counting dated children before and after the runs, not "
            "assumed. Benched producers that write on every invocation (notably "
            "gb-mcp-validate.py, which has no --dry-run) leave one artifact per run, "
            "and `gb doctor` runs them too. --dry-run suppresses the BENCH artifact; "
            "it cannot suppress a child producer's own write",
        },
        "unreachable": unreachable,
        "verbs": [r.to_json() for r in rows if r.bench.family == "verb"],
        "producers": [r.to_json() for r in rows if r.bench.family == "producer"],
        "slowest_verbs": [
            {"label": r.bench.label, "median_s": r.median_s, "verdict": r.verdict}
            for r in ranked[:3]
        ],
        "attribution": attribute(by_label),
        "budget": {
            r.bench.label: {
                "target_s": r.bench.budget_s,
                "why": r.bench.why,
                "median_s": r.median_s,
                "verdict": r.verdict,
                "headroom_s": (
                    round(r.bench.budget_s - r.median_s, 4)
                    if r.median_s is not None
                    else None
                ),
            }
            for r in rows
        },
    }


def report(doc: Dict[str, Any], rows: List[Row]) -> None:
    """The table a human reads. Times in seconds, medians ranked, budget verdict per row."""
    repo = doc["repo"]
    print(
        f"gb-bench — repeat {doc['repeat']} · {'CONFIGURED' if repo['configured'] else 'UNCONFIGURED'}"
        f" · {doc['host']['machine']} · python {doc['host']['python']}"
    )
    for family, title in (("verb", "verbs"), ("producer", "producers (attribution)")):
        group = [r for r in rows if r.bench.family == family]
        if not group:
            continue
        print(f"\n  {title}")
        print(
            f"    {'row':<26}{'min':>8}{'median':>9}{'max':>8}{'stdev':>8}"
            f"{'rc':>6}{'budget':>8}  verdict"
        )
        for r in sorted(group, key=lambda x: -(x.median_s or -1.0)):
            if not r.reachable:
                print(
                    f"    {r.bench.label:<26}{'UNREACHABLE':>39}  {r.unreachable[:60]}"
                )
                continue
            times = r.times
            stdev = statistics.stdev(times) if len(times) > 1 else 0.0
            codes = ",".join(str(c) for c in r.codes)
            print(
                f"    {r.bench.label:<26}{min(times):>8.3f}{r.median_s or 0.0:>9.3f}"
                f"{max(times):>8.3f}{stdev:>8.3f}{codes:>6}{r.bench.budget_s:>8.2f}  "
                f"{r.verdict}"
            )
    for att in doc["attribution"]:
        kids = " + ".join(
            f"{c['label'].split('/')[-1]} {c['median_s']:.2f}s"
            for c in att["children"][:3]
        )
        print(
            f"\n  {att['verb']} {att['verb_median_s']:.2f}s = children "
            f"{att['children_measured_s']:.2f}s + dispatcher {att['dispatcher_s']:.2f}s"
        )
        print(f"    top: {kids}")
        if att["children_unmeasured"]:
            print(
                f"    UNMEASURED children: {', '.join(att['children_unmeasured'])}"
                f" — the residual above absorbs them"
            )
    over = [lbl for lbl, b in doc["budget"].items() if b["verdict"] == "OVER"]
    unm = doc["unreachable"]
    print(f"\n  {len(over)} row(s) over budget{': ' + ', '.join(over) if over else ''}")
    if unm:
        print(
            f"  {len(unm)} row(s) UNREACHABLE — unmeasured, not fast: {', '.join(unm)}"
        )
    wrote = doc["side_effects"]["artifacts_written_during_this_bench"]
    if wrote:
        detail = ", ".join(f"{k}/ +{v}" for k, v in sorted(wrote.items()))
        print(
            f"  side effect: {detail} — benched producers that write have no --dry-run, "
            f"so measuring them leaves real artifacts"
        )


def body() -> int:
    args = parse(Args)
    repeat = 5 if args.repeat is None else args.repeat
    if repeat < 1:
        print(
            f"gb-bench: --repeat must be at least 1, got {repeat} — zero runs is not a "
            f"baseline\n    try: gb-bench.py --repeat 3",
            file=sys.stderr,
        )
        return 2

    chosen, unknown = select(args.verb or ())
    if unknown:
        print(
            f"gb-bench: unknown row(s) {', '.join(repr(u) for u in unknown)}. Known: "
            f"{', '.join(b.label for b in ALL)}\n"
            f"    try: gb-bench.py --verb doctor",
            file=sys.stderr,
        )
        return 2

    cleared = REENTRY_ENV in os.environ
    env = child_env()
    before = artifact_census()
    rows = [measure(b, repeat, env) for b in chosen]
    after = artifact_census()
    wrote = {k: after[k] - before[k] for k in after if after[k] > before[k]}
    doc = build(rows, repeat, cleared, wrote)

    if not args.dry_run:
        out = BENCH_ROOT / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.json"
        atomic_write_json(out, doc)
        doc["artifact"] = str(out.relative_to(ROOT))

    if args.json:
        print(json.dumps(doc, indent=1))
    else:
        report(doc, rows)
        if not args.dry_run:
            print(f"  wrote {doc['artifact']}")

    # Exit 0 is the normal answer even when the board is red and every verb exits 1 — this
    # producer reports timings, and a slow verb is a finding for the operator, not a failure of
    # the measurement. ENVIRONMENT is reserved for the one case where a VERB could not be
    # invoked at all, because then there is no baseline to report.
    dead = [r.bench.label for r in rows if r.bench.family == "verb" and not r.reachable]
    if dead:
        print(
            f"gb-bench: {len(dead)} verb(s) could not be invoked on this machine: "
            f"{', '.join(dead)}",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    main(body)
