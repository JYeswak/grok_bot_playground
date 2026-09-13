#!/usr/bin/env python3
"""gb-advise — what to do next, ranked, with the receipt for every line.

This verb ADDS NO CORPUS. Every other producer here measures one thing and writes one artifact;
the operator is then left to hold nine of them in his head at once and decide what matters. That
join is the work, and it was the one thing nothing in this repo did. `gb-advise` does exactly
that join and nothing else: it reads what the other producers already wrote, ranks the actions
they imply, and refuses to say anything it cannot point at.

WHAT IT JOINS (nine sources; `--source` restricts the set)

  gate          `bin/gb-surface-gate.py --json`   24 checks — the board's own definition of done
  coverage      `bin/gb-coverage.py --json`       46 facets — what is measured, and what is not
  beads         `br list --status all --json`     the declared work ledger, with its priorities
  gaps          newest `gaps/*.json`              322 catalog plugins vs 645 built Bots
  usecases      newest `usecases/*.json`          645 attributed Bots, 294 distinct integrations
  market        newest `market/*.json`            the plugin catalog AND what is installed here
  discovery     newest `discovery/*.json`         new repos in the ecosystem, vs what was triaged
  mine          newest `mine/*.json`              franken-harvest + mirror, with file:line rows
  utilization   newest `utilization/*.json`       whether the fleet does anything, and what it cost

EVERY SOURCE GETS A TYPED STATE — OK, EMPTY, or UNREACHABLE. A missing artifact is UNREACHABLE
and the run says the view is PARTIAL; it never degrades into "nothing to advise". That is this
repo's founding rule and `NEGATIVE_EVIDENCE.md` NE-14 is a live example of an upstream tool
getting it wrong. Deselecting a source with `--source` is a third, different thing: the view is
RESTRICTED by request, which is not a defect and is labelled separately.

EVERY ACTION SHIPS ITS OWN DEREFERENCE COMMAND. An `evidence` row is a triple: the `ref` it came
from (artifact path + jq path, or a check id), the `says` value that was actually read there, and
a `deref` shell command that prints it again. A claim you cannot re-run is an assertion.

THE RANKING IS LEXICOGRAPHIC, NOT A SCORE. There is no measurement in this repo that would
justify weights, and a made-up 0.4/0.3/0.2 is a number an operator cannot argue with. `gb work`
set the precedent (ready-first, declared priority, dependents, age) and this follows it. Five
keys, one sentence each, every one a field some source already publishes:

  1. BLOCKING FIRST     — the repo's own gate doctrine calls two things stop conditions: a check
                          the gate scores RED/ERROR, and an oracle that could not be read at all
                          ("Missing oracle = FAIL, not skip", GATES.md §Fail-closed rules); those
                          sort above everything, because work chosen while the board is failing
                          is work chosen on an untrustworthy board.
  2. DECLARED PRIORITY  — `br`'s own 0..4 `priority`, taken at face value, because the author's
                          stated urgency is better evidence than anything this verb could
                          re-derive; a source that publishes no priority sits at NEUTRAL 2 BY
                          DECLARATION and the row carries `priority_declared: false` so it admits
                          it rather than passing the default off as a measurement.
  3. POPULATION SUPPORT — descending: how many of the 645 attributed Bots in the corpus already
                          do this thing, which is the only EXTERNAL evidence available here; rows
                          with no such count score 0, so this key can only separate rows that
                          already tie on 1 and 2 and can never lift an unsupported row over a
                          supported one.
  4. DEPENDENTS         — descending `dependent_count` from `br`: work that unblocks other work
                          beats leaf work (`gb work`'s third key, kept so the two verbs order the
                          same ledger the same way); 0 where the source has no such field.
  5. CLASS, THEN ID     — pure deterministic tiebreak, so two runs over unchanged inputs produce
                          a byte-identical ordering; a ranking that reshuffles cannot be diffed,
                          and this repo diffs everything.

WHAT IS DELIBERATELY NOT ADVISED. A coverage facet marked EMPTY is a MEASUREMENT whose answer is
zero — advising on it would be the exact EMPTY/UNREACHABLE collapse this file refuses elsewhere.
A facet marked BLOCKED has no read path at all, so "go measure it" is advice nobody can take.
Only UNMEASURED facets become actions.

DISCOVERY — the class the operator asked for by name. Three of the eight classes answer "tooling
you did not know was possible", and each is a join no single producer can make:

  install_plugin      an integration the POPULATION builds on, which the catalog already serves
                      with an approved plugin, and which this account has not installed. Needs
                      `usecases` (who builds what) AND `market` (what exists, what is installed).
  unserved_domain     a whole field with zero catalog plugins where the Dicklesworthstone mirror
                      already holds a working implementation, carried with file:line evidence.
  adoptable_technique the top-ranked row per query from the last `gb-mine` run — a specific
                      symbol at a specific line that could be read and imitated.

EVERY RED CHECK CARRIES WHAT THE CHECK IS FOR, OR SAYS IT CANNOT. A `red_gate` row quotes two
things: the board's `detail` (what is wrong with THIS deployment) and `GATES.md`'s "RED means"
column (what the check exists to catch). When the second is missing the row does NOT quietly
ship with one source — it carries a `MISSING` evidence entry naming the absent row, the action
text is marked `[UNSOURCED: …]`, and a caveat states the fraction. Measured 2026-09-11: the old
anchored parser recovered 18/25 against the pre-rewrite doc (g19…g25 had no row at all) and the
regex would additionally have dropped any indented row, any id past two digits, and truncated a
cell at an escaped pipe. `gates_evidence.recovery` in the envelope publishes the count, and the
`--selftest` leg `every-registered-check-has-a-documented-meaning` reads the REAL GATES.md
against the REAL roster in `bin/gb-surface-gate.py`, so a check landing without a row turns the
selftest red instead of thinning the advice.

EXIT CODES (this repo's dictionary; see `gb help exit-codes`):

    0   OK           every selected source answered and none of them implies an action
    1   FINDINGS     there is advice — or, under --selftest, a leg failed
    3   ENVIRONMENT  no selected source could be read — the machine cannot answer the question
    2   USAGE        bad invocation (--limit below 1, unknown --source)
    130 CANCELLED    SIGINT arrived; no partial artifact is left behind

  On the 0-vs-3 edge: when nothing is advisable AND a source was UNREACHABLE, the exit stays 0
  per this verb's contract, but `status` reads `PARTIAL_EMPTY` and never `OK`, and both stdout
  and stderr say the view was incomplete. The exit code is the coarse channel; the distinction
  survives in the one that can carry it.

  gb-advise.py                              # rank everything, write advise/<stamp>.json
  gb-advise.py --limit 20                   # more of the ranking
  gb-advise.py --json | jq .
  gb-advise.py --source market --source usecases    # only the plugin-discovery join
  gb-advise.py --dry-run                    # nothing is written
  gb-advise.py --selftest                   # inline fixtures + the evidence-coverage leg
  gb-advise.py --dry-run --json | jq .gates_evidence.recovery      # "25/25"
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import importlib.util
import json
import pathlib
import re
import shlex
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import arg, parse  # noqa: E402
from gbtypes import atomic_write_json, atomic_write_text, main  # noqa: E402
from gbtypes import read_json_capped  # noqa: E402
from gbtypes import run as run_child  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-advise/1"
SUMMARY = "rank what to do next by joining every producer this repo already runs"

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE, EXIT_ENVIRONMENT = 0, 1, 2, 3

# The same value `bin/gb` uses for an item whose source publishes no priority (bin/gb:137). Kept
# identical on purpose: two verbs that rank the same bead ledger must not disagree about what an
# undeclared priority means.
NEUTRAL_PRIORITY = 2

# `bin/gb-gaps.py` declares 3 as the point below which an integration is noise rather than a
# market (gb-gaps.py:55, `MIN_DEMAND`). Reused rather than re-chosen: two producers reading the
# same corpus must not hold two different opinions about what counts as demand.
MIN_POPULATION = 3

# Bounds. The gate is pure-stdlib and judges files already on disk (measured 0.270s as a `gb
# doctor` child); coverage 0.054s; `br list` ~0.06s. Each ceiling is ~100x its measured cost, so
# it only fires on something pathological — and when it does, the source is UNREACHABLE, because
# a probe we killed did not answer.
GATE_TIMEOUT_S = 120.0
COVERAGE_TIMEOUT_S = 60.0
BEADS_TIMEOUT_S = 60.0

BEADS_TOOL = "br"
BR_READONLY: Tuple[str, ...] = ("--no-auto-flush", "--no-auto-import")

# A hard ceiling on the artifact. 34 actions is the measured size today; 500 is ~15x that and
# exists so a corrupt corpus cannot produce a multi-megabyte advice file.
MAX_ACTIONS = 500

DETAIL_CHARS = 220


class State(str, enum.Enum):
    """What a source actually did. Three values, because there are three outcomes, and folding
    the last two into "nothing found" is the specific lie this repo was built to stop telling."""

    OK = "OK"
    EMPTY = "EMPTY"
    UNREACHABLE = "UNREACHABLE"


@dataclasses.dataclass(frozen=True)
class Evidence:
    """One dereferenceable claim.

    `ref` addresses it, `says` is the value that was read there, and `deref` is a shell command
    that prints it again. The third field is the point: an evidence pointer that does not ship
    its own re-run is a footnote, and a footnote is how a stale number survives a rewrite.
    """

    ref: str
    says: str
    deref: str

    def to_json(self) -> Dict[str, str]:
        return {"ref": self.ref, "says": self.says, "deref": self.deref}


@dataclasses.dataclass(frozen=True)
class Source:
    """One input's verdict. `remediation` is mandatory on UNREACHABLE and empty otherwise: an
    unreachable source that does not say how to reach it is a dead end, not a report."""

    name: str
    state: State
    detail: str
    origin: str
    remediation: str = ""
    notes: Tuple[str, ...] = ()

    @property
    def read(self) -> bool:
        """Did this source answer at all? EMPTY is an answer; UNREACHABLE is not."""
        return self.state is not State.UNREACHABLE

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "source": self.name,
            "status": self.state.value,
            "detail": self.detail,
            "origin": self.origin,
        }
        if self.remediation:
            out["remediation"] = self.remediation
        if self.notes:
            out["notes"] = list(self.notes)
        return out


@dataclasses.dataclass(frozen=True)
class Action:
    """One concrete next action, with everything needed to argue with it."""

    cls: str
    action: str
    command: str
    source: str
    evidence: Tuple[Evidence, ...]
    blocking: bool = False
    priority: int = NEUTRAL_PRIORITY
    priority_declared: bool = False
    priority_from: str = ""
    population: int = 0
    dependents: int = 0
    ident: str = ""
    rank: int = 0

    @property
    def sort_key(self) -> Tuple[int, int, int, int, str, str]:
        """The five ranking keys, in order. See this module's docstring for the one-sentence
        justification of each; the order here IS the ranking and there is no weighting step."""
        return (
            0 if self.blocking else 1,
            self.priority,
            -self.population,
            -self.dependents,
            self.cls,
            _natural(self.ident or self.action),
        )

    def why(self) -> str:
        """Why this row ranks where it does, key by key, in the ranking's own order."""
        bits = [
            (
                "blocking — the board calls this a stop condition"
                if self.blocking
                else "not blocking — no RED/ERROR check and no unreadable oracle"
            ),
            (
                f"p{self.priority} declared by {self.priority_from or self.source}"
                if self.priority_declared
                else f"p{self.priority} NEUTRAL by declaration "
                f"({self.source} publishes no priority)"
            ),
            (
                f"{self.population} of 645 corpus Bots already do this"
                if self.population
                else "no corpus count applies"
            ),
        ]
        if self.dependents:
            bits.append(f"{self.dependents} bead(s) depend on it")
        return " · ".join(bits)

    def to_json(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "class": self.cls,
            "action": self.action,
            "command": self.command,
            "source": self.source,
            "why_ranked": self.why(),
            "blocking": self.blocking,
            "priority": self.priority,
            "priority_declared": self.priority_declared,
            "priority_from": self.priority_from or self.source,
            "population_support": self.population,
            "dependents": self.dependents,
            "id": self.ident,
            "evidence": [e.to_json() for e in self.evidence],
        }


# ---------------------------------------------------------------------------------------------
# Evidence construction — every `deref` here is a command that was run while writing this file.
# ---------------------------------------------------------------------------------------------
def _jq(path: str, program: str, *, key: Optional[str] = None) -> str:
    """A `jq` command that re-reads exactly one field of one artifact.

    Values that came from a corpus are passed with `--arg`, not interpolated: an integration
    literally named `Lowe's` would otherwise terminate the jq program's quoting and produce a
    command that looks right and does something else. `shlex.quote` is the only correct escape
    for the shell layer, so it is used rather than an f-string.
    """
    head = f"jq --arg k {shlex.quote(key)} " if key is not None else "jq "
    return f"{head}{shlex.quote(program)} {shlex.quote(path)}"


def _clip(text: str, limit: int = DETAIL_CHARS) -> str:
    """One line, bounded. Artifacts are read in a terminal and diffed in git; a 4 KB headline
    wrapped over 30 lines destroys both."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "\u2026"


def _shown_argv(argv: Sequence[str]) -> str:
    """The argv as an operator would retype it.

    This machine's interpreter path, this checkout's absolute prefix, and the `~/.local/bin`
    a tool happened to be resolved from are facts about the RUNNER, not about the command.
    Pasting them into a committed artifact makes it unreproducible anywhere else, and a reader
    on another machine cannot tell which parts he is supposed to substitute.
    """
    out: List[str] = []
    for token in argv:
        text = str(token)
        base = pathlib.PurePath(text).name
        if text == sys.executable:
            text = "python3"
        elif text.startswith(str(ROOT) + "/"):
            text = text[len(str(ROOT)) + 1 :]
        elif text.startswith("/") and shutil.which(base) == text:
            # Resolved off PATH, so the bare name reaches the same binary for the reader too.
            text = base
        out.append(text)
    return " ".join(out)


_DIGITS = re.compile(r"(\d+)")


def _natural(text: str) -> str:
    """A sort key where digit runs compare numerically: without it `g12` sorts before `g8`,
    which puts the ranking's final tiebreak in an order no reader would predict."""
    return _DIGITS.sub(lambda m: m.group(1).rjust(8, "0"), text)


# ---------------------------------------------------------------------------------------------
# Reading the sources
# ---------------------------------------------------------------------------------------------
def _dated(sub: str) -> List[pathlib.Path]:
    """Children of `<root>/<sub>` named `YYYY-MM-DD…json`, ascending. Same rule as
    `gblib.dated_children`: the date prefix is both the sort key and the filter, so an
    undated file in an artifact root is never mistaken for a tick's output."""
    directory = ROOT / sub
    if not directory.is_dir():
        return []
    rows = [
        c
        for c in directory.iterdir()
        if c.is_file() and c.name.endswith(".json") and c.name[:10].count("-") == 2
    ]
    return sorted(rows, key=lambda c: c.name)


def _read_artifact(name: str, sub: str) -> Tuple[Source, Optional[Dict[str, Any]]]:
    """The newest dated artifact under `<sub>/`, or UNREACHABLE with the reason."""
    rows = _dated(sub)
    if not rows:
        return (
            Source(
                name=name,
                state=State.UNREACHABLE,
                detail=(
                    f"no dated artifact under {sub}/ — the {name} view was never written on "
                    f"this machine, which is NOT the same fact as an empty one"
                ),
                origin=f"{sub}/",
                remediation=_PRODUCERS.get(
                    name, f"(no producer registered for {name})"
                ),
            ),
            None,
        )
    newest = rows[-1]
    rel = str(newest.relative_to(ROOT))
    try:
        doc = read_json_capped(newest)
    except ValueError as exc:  # larger than the cap: a short read is not a whole file
        return (
            Source(
                name=name,
                state=State.UNREACHABLE,
                detail=f"{rel} exceeds the read cap ({exc}) — reading a prefix would be a "
                f"partial artifact presented as the whole one",
                origin=rel,
                remediation=f"ls -lh {rel}",
            ),
            None,
        )
    if not isinstance(doc, dict):
        return (
            Source(
                name=name,
                state=State.UNREACHABLE,
                detail=f"{rel} did not parse as a JSON object — the artifact is unreadable, "
                f"not empty",
                origin=rel,
                remediation=f"jq . {rel}",
            ),
            None,
        )
    return (Source(name=name, state=State.OK, detail=f"read {rel}", origin=rel), doc)


def _read_child(
    name: str, argv: Sequence[str], remediation: str, timeout_s: float
) -> Tuple[Source, Optional[Dict[str, Any]]]:
    """Run a local producer and take its `--json` document.

    A NONZERO EXIT IS NOT UNREACHABLE. `gb-surface-gate.py` exits 1 when the board is RED and 2
    when it is ERROR — those are its ANSWERS, and treating them as a failed read would silently
    drop the three checks this verb most needs. Readability is therefore defined as "stdout
    parsed as an object", and the exit code is only used to explain a read that did not parse.

    A killed child, by contrast, IS unreachable: a probe stopped on its deadline or at the output
    cap did not answer, and `Severity.CANCELLED` is not `Severity.ERR`.
    """
    exe = argv[0]
    if not pathlib.Path(exe).exists() and shutil.which(exe) is None:
        return (
            Source(
                name=name,
                state=State.UNREACHABLE,
                detail=f"`{exe}` is not on this machine, so {name} could not be read — that is "
                f"not a report of zero findings",
                origin=_shown_argv(argv),
                remediation=remediation,
            ),
            None,
        )
    proc = run_child(list(argv), timeout_s=timeout_s, cwd=ROOT)
    origin = _shown_argv(proc.argv)
    if proc.timed_out or proc.truncated:
        stopped = (
            "its deadline"
            if proc.timed_out
            else f"the output cap after {len(proc.out)}B"
        )
        return (
            Source(
                name=name,
                state=State.UNREACHABLE,
                detail=f"`{origin}` was stopped on {stopped} — its answer is unknown, not empty",
                origin=origin,
                remediation=remediation,
            ),
            None,
        )
    note = ""
    doc: Optional[Any] = None
    try:
        doc = json.loads(proc.out) if proc.out.strip() else None
    except json.JSONDecodeError as exc:
        note = f"stdout did not parse ({exc.msg})"
    else:
        if not isinstance(doc, dict):
            note = f"stdout parsed as {type(doc).__name__}, not an object"
    if not isinstance(doc, dict):
        first = next(
            (ln.strip() for ln in (proc.err or proc.out).splitlines() if ln.strip()),
            "(no output)",
        )
        return (
            Source(
                name=name,
                state=State.UNREACHABLE,
                detail=f"`{origin}` exited {proc.code} and {note}: {_clip(first, 130)}",
                origin=origin,
                remediation=remediation,
            ),
            None,
        )
    return (
        Source(
            name=name,
            state=State.OK,
            detail=f"ran `{origin}` (exit {proc.code}, {proc.elapsed_s}s)",
            origin=origin,
        ),
        doc,
    )


# The command that regenerates each source. Doubles as the UNREACHABLE remediation, so a missing
# input always arrives with the one line that fixes it.
_PRODUCERS: Dict[str, str] = {
    "gate": "bin/gb-surface-gate.py --json",
    "coverage": "bin/gb-coverage.py --json",
    "beads": 'export PATH="$HOME/.local/bin:$PATH"   # br is installed there on this machine',
    "gaps": "bin/gb-gaps.py --markdown",
    "usecases": "bin/gb-usecases.py",
    "market": "bin/gb-market-snapshot.py",
    "discovery": "bin/gb-discover.py",
    "mine": "bin/gb-mine.py",
    "utilization": "bin/gb-utilization.py",
}

SOURCE_NAMES: Tuple[str, ...] = (
    "gate",
    "coverage",
    "beads",
    "gaps",
    "usecases",
    "market",
    "discovery",
    "mine",
    "utilization",
)


def _rows_of(doc: Optional[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    """A list-of-objects field, defensively. An artifact whose shape moved yields nothing here
    rather than a traceback; the source's own state already says whether it was read."""
    if doc is None:
        return []
    value = doc.get(field)
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _pairs_of(doc: Optional[Dict[str, Any]], field: str) -> List[Tuple[str, int]]:
    """A `[[name, count], …]` field, as `gb-usecases` writes `integrations` and `categories`."""
    if doc is None:
        return []
    value = doc.get(field)
    if not isinstance(value, list):
        return []
    out: List[Tuple[str, int]] = []
    for row in value:
        if isinstance(row, list) and len(row) == 2 and isinstance(row[1], int):
            out.append((str(row[0]), row[1]))
    return out


def _refine(name: str, source: Source, doc: Optional[Dict[str, Any]]) -> Source:
    """Promote OK to EMPTY where the source answered and the answer is genuinely nothing.

    Spelled out per source rather than inferred, because "empty" means a different thing in each
    one and a generic `len(doc) == 0` would call a 30-field artifact non-empty forever.
    """
    if source.state is not State.OK or doc is None:
        return source

    def empty(detail: str) -> Source:
        return dataclasses.replace(source, state=State.EMPTY, detail=detail)

    if name == "gate":
        checks = _rows_of(doc, "checks")
        bad = [c for c in checks if c.get("verdict") in ("RED", "ERROR")]
        if not checks:
            return empty(
                f"{source.detail} — but it listed zero checks; an empty scan set is "
                f"never a pass"
            )
        return dataclasses.replace(
            source,
            detail=f"{source.detail}: {len(checks)} checks, {len(bad)} not GREEN",
        )
    if name == "coverage":
        rows = _rows_of(doc, "rows")
        unmeasured = [r for r in rows if r.get("verdict") == "UNMEASURED"]
        measured = [r for r in rows if r.get("verdict") == "MEASURED"]
        note = (
            f"{len(rows) - len(unmeasured) - len(measured)} facet(s) are EMPTY — measured, and "
            f"the answer is zero; deliberately NOT advised on"
        )
        return dataclasses.replace(
            source,
            state=State.OK if unmeasured else State.EMPTY,
            detail=f"{source.detail}: {len(rows)} facets, {len(unmeasured)} UNMEASURED",
            notes=source.notes + (note,),
        )
    if name == "beads":
        live = [i for i in _rows_of(doc, "issues") if i.get("status") == "open"]
        total = len(_rows_of(doc, "issues"))
        if not live:
            return empty(f"{source.detail}: {total} bead(s), none open")
        return dataclasses.replace(
            source, detail=f"{source.detail}: {len(live)} open of {total}"
        )
    if name == "gaps":
        domains = [r for r in _rows_of(doc, "domain_gap") if r.get("plugins") == 0]
        if not domains:
            return empty(f"{source.detail}: every domain has catalog coverage")
        return dataclasses.replace(
            source,
            detail=f"{source.detail}: {len(domains)} domain(s) with zero plugins",
        )
    if name == "usecases":
        integrations = _pairs_of(doc, "integrations")
        if not integrations:
            return empty(f"{source.detail}: the corpus named no integrations")
        return dataclasses.replace(
            source,
            detail=f"{source.detail}: {doc.get('bots')} Bots, {len(integrations)} integrations",
        )
    if name == "market":
        plugins = doc.get("plugins")
        catalog = plugins.get("rows") if isinstance(plugins, dict) else None
        installed = doc.get("installed")
        if not isinstance(catalog, list) or not catalog:
            return empty(f"{source.detail}: the catalog read came back with no rows")
        n_installed = len(installed) if isinstance(installed, list) else 0
        return dataclasses.replace(
            source,
            detail=f"{source.detail}: {len(catalog)} catalog plugins, "
            f"{n_installed} installed here",
        )
    if name == "discovery":
        notable = doc.get("notable")
        if not isinstance(notable, list) or not notable:
            return empty(f"{source.detail}: no notable repo in the lookback window")
        return dataclasses.replace(
            source, detail=f"{source.detail}: {len(notable)} notable repo(s)"
        )
    if name == "mine":
        cands = _rows_of(doc, "candidates")
        if not cands:
            return empty(f"{source.detail}: the last mine returned no candidate")
        return dataclasses.replace(
            source, detail=f"{source.detail}: {len(cands)} mined candidate(s)"
        )
    if name == "utilization":
        bots = _rows_of(doc, "bots")
        if not bots:
            return empty(f"{source.detail}: the roster had no Bot to measure")
        return dataclasses.replace(
            source,
            detail=f"{source.detail}: {len(bots)} Bot(s), "
            f"{doc.get('usage_percent')}% of the weekly allowance used",
        )
    return source


def read_sources(
    wanted: Sequence[str],
) -> Tuple[List[Source], Dict[str, Optional[Dict[str, Any]]]]:
    """Read exactly the selected sources, in the declared order. Nothing else is touched: a
    `--source market` run must not pay for a gate it was told to ignore."""
    sources: List[Source] = []
    docs: Dict[str, Optional[Dict[str, Any]]] = {}
    for name in SOURCE_NAMES:
        if name not in wanted:
            continue
        if name == "gate":
            src, doc = _read_child(
                name,
                [sys.executable, str(ROOT / "bin" / "gb-surface-gate.py"), "--json"],
                _PRODUCERS[name],
                GATE_TIMEOUT_S,
            )
        elif name == "coverage":
            src, doc = _read_child(
                name,
                [sys.executable, str(ROOT / "bin" / "gb-coverage.py"), "--json"],
                _PRODUCERS[name],
                COVERAGE_TIMEOUT_S,
            )
        elif name == "beads":
            exe = shutil.which(BEADS_TOOL)
            src, doc = _read_child(
                name,
                [exe or BEADS_TOOL, *BR_READONLY, "list", "--status", "all", "--json"],
                _PRODUCERS[name],
                BEADS_TIMEOUT_S,
            )
        else:
            src, doc = _read_artifact(name, name)
        src = _refine(name, src, doc)
        sources.append(src)
        docs[name] = doc
    return sources, docs


# ---------------------------------------------------------------------------------------------
# Advice classes. Each is a pure function of already-read documents.
# ---------------------------------------------------------------------------------------------
# A table row is split on UNESCAPED pipes, so a `RED means` cell containing a literal `\|` is
# carried whole instead of being truncated at the escape — a silently half-quoted meaning is
# worse than a missing one, because it still looks sourced.
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
# `\d+`, not `\d{1,2}`: the roster is 25 today and the id space is open. `bin/gb-gatesdoc.py:44`
# already reads the same column with `g\d+`, and two readers of one table must not disagree
# about which rows exist — at g100 the old bound here would have dropped a row gatesdoc accepts.
_ID_CELL = re.compile(r"^`(g\d+-[a-z0-9-]+)`$")


@dataclasses.dataclass(frozen=True)
class GatesDoc:
    """`GATES.md`'s "RED means" column, plus what happened while reading it.

    The state matters as much as the rows: zero meanings because the file is absent is a source
    that never answered, and zero meanings because the table moved is a source that answered
    nothing. Returning a bare dict collapsed those two, and a collapsed absence is how advice
    ends up unsourced without anyone noticing.
    """

    rows: Dict[str, str]
    state: State
    detail: str
    path: str = "GATES.md"


def _gates_md(path: Optional[pathlib.Path] = None) -> GatesDoc:
    """`GATES.md`'s "RED means" column, keyed by check id.

    The gate's own `detail` says WHAT is wrong with this deployment; this says what the check
    exists to catch. Both are quoted verbatim into the evidence rather than paraphrased, because
    a paraphrase of a gate's meaning is how a check quietly changes definition.

    Parsed row-wise rather than with one anchored regex. The anchored form (`^\\|\\s*`) dropped
    any indented row and any id past two digits, and truncated a cell at the first escaped pipe;
    each of those is a MISS that looks exactly like "this check has no documented meaning".
    """
    p = path if path is not None else (ROOT / "GATES.md")
    try:
        rel = str(p.relative_to(ROOT))
    except ValueError:
        rel = str(p)
    if not p.is_file():
        return GatesDoc({}, State.UNREACHABLE, f"{rel} is absent", rel)
    try:
        body = p.read_text(errors="replace")
    except OSError as exc:
        return GatesDoc({}, State.UNREACHABLE, f"{rel} unreadable: {exc}", rel)
    rows: Dict[str, str] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in _CELL_SPLIT.split(stripped)]
        if cells and not cells[0]:
            cells = cells[1:]
        if cells and not cells[-1]:
            cells = cells[:-1]
        if len(cells) < 2:
            continue
        found = _ID_CELL.match(cells[0])
        if found is None:
            continue
        # First row wins. Duplicate ids are a doc defect that `bin/gb-gatesdoc.py:54` already
        # fails on by name; re-reporting it here would be a second opinion about one fact.
        rows.setdefault(found.group(1), cells[1].replace("\\|", "|"))
    if not rows:
        return GatesDoc(
            {}, State.EMPTY, f"{rel} holds no `| `gN-…` | …` table row", rel
        )
    return GatesDoc(rows, State.OK, f"{len(rows)} documented check(s) in {rel}", rel)


def gates_coverage(
    gate: Optional[Dict[str, Any]], doc: GatesDoc
) -> Tuple[List[str], List[str]]:
    """`(documented, undocumented)` over every check the BOARD registers, in board order.

    The denominator is the gate's own roster, never the doc's row count: a table that documents
    18 checks out of 25 scores 18/25, and a table that documents 18 out of 18 scores 18/18. Only
    the first is a hole, and only comparing against the producer can tell them apart.
    """
    roster: List[str] = []
    for row in _rows_of(gate, "checks"):
        cid = str(row.get("check") or "")
        if cid and cid not in roster:
            roster.append(cid)
    return (
        [c for c in roster if c in doc.rows],
        [c for c in roster if c not in doc.rows],
    )


def _coverage_notes(
    gate: Optional[Dict[str, Any]], doc: GatesDoc
) -> Tuple[List[str], List[str]]:
    """`(undocumented_ids, notes)` — the sentence the run prints when evidence is missing.

    Emitted whether or not any of those checks is currently RED. A meaning that fell out of the
    table while the board was green is a hole that only shows up later, at the moment the check
    fires and the advice arrives with nothing behind it.
    """
    documented, undocumented = gates_coverage(gate, doc)
    total = len(documented) + len(undocumented)
    notes: List[str] = []
    if doc.state is State.UNREACHABLE or (doc.state is State.EMPTY and total):
        notes.append(
            f"no check meaning could be recovered: {doc.detail}. Every red_gate row below is "
            f"sourced by the board's own detail and by NOTHING about what the check exists to "
            f"catch (deref: python3 bin/gb-gatesdoc.py)"
        )
        return undocumented, notes
    if undocumented:
        shown = ", ".join(undocumented[:8]) + (
            f" +{len(undocumented) - 8} more" if len(undocumented) > 8 else ""
        )
        notes.append(
            f"{doc.path} documents {len(documented)}/{total} of the checks the board registers; "
            f"no `RED means` row for {shown} — advice about those checks is UNSOURCED on what "
            f"the check exists to catch (deref: python3 bin/gb-gatesdoc.py)"
        )
    return undocumented, notes


def gates_evidence_block(
    gate: Optional[Dict[str, Any]], doc: Optional[GatesDoc] = None
) -> Dict[str, Any]:
    """The evidence-recovery census, as a payload field: how many registered checks have a
    documented meaning, and the id of every one that does not.

    It ships on every run, green or red, so the recovery count is a number an operator can read
    off one command instead of a property someone has to go and measure:

        python3 bin/gb-advise.py --dry-run --json | jq .gates_evidence.recovery

    When the board itself could not be read there IS no denominator, and the counts are null
    rather than the doc's own row count dressed up as coverage.
    """
    doc = doc if doc is not None else _gates_md()
    documented, undocumented = gates_coverage(gate, doc)
    total = len(documented) + len(undocumented)
    known = gate is not None
    return {
        "doc": doc.path,
        "doc_state": doc.state.value,
        "doc_detail": doc.detail,
        "rows_in_doc": len(doc.rows),
        "checks_registered": total if known else None,
        "checks_documented": len(documented) if known else None,
        "recovery": f"{len(documented)}/{total}" if known else None,
        "undocumented": undocumented,
        "deref": "python3 bin/gb-gatesdoc.py",
        "note": (
            "every registered check has a `RED means` row"
            if known and not undocumented
            else (
                f"{len(undocumented)} registered check(s) have no `RED means` row; advice about "
                f"them carries a MISSING evidence row rather than an unsourced claim"
                if known
                else "the board could not be read, so evidence coverage has no denominator — "
                f"{len(doc.rows)} row(s) were parsed out of {doc.path} and nothing was "
                "compared against them"
            )
        ),
    }


def advise_gate(
    gate: Optional[Dict[str, Any]],
    beads: Optional[Dict[str, Any]],
    doc: Optional[GatesDoc] = None,
) -> Tuple[List[Action], List[str], List[str]]:
    """Checks the board scores RED or ERROR, joined to the bead that cites them.

    The join is by EXACT check id inside a bead's title or description — beads in this repo cite
    gates verbatim (`gb-charter-the-cfs-group-bot-7ks` names `g5-bot-charter-present`). When a
    bead is found, the action inherits that bead's declared priority and its `br show` command,
    and the bead is NOT emitted a second time as its own row. When none is found, the row says so
    and sits at NEUTRAL — an undeclared priority is never dressed up as a declared one.
    """
    if gate is None:
        return [], [], []
    # Injectable so the selftest can drive the real join against a doc it built, rather than
    # against whatever GATES.md happens to say on the machine running the test.
    doc = doc if doc is not None else _gates_md()
    meanings = doc.rows
    undocumented, notes = _coverage_notes(gate, doc)
    bead_rows = _rows_of(beads, "issues") if beads is not None else []
    claimed: List[str] = []
    actions: List[Action] = []
    for check in _rows_of(gate, "checks"):
        cid, verdict = str(check.get("check") or ""), str(check.get("verdict") or "")
        if not cid or verdict not in ("RED", "ERROR"):
            continue
        detail = _clip(check.get("detail") or "")
        bead = next(
            (
                b
                for b in bead_rows
                if b.get("status") == "open"
                and cid in f"{b.get('title') or ''} {b.get('description') or ''}"
            ),
            None,
        )
        evidence = [
            Evidence(
                ref=f"gate:{cid}",
                says=f"{verdict} — {detail}",
                deref="bin/gb-surface-gate.py --json | "
                + _jq("-", ".checks[]|select(.check==$k)", key=cid),
            )
        ]
        if cid in meanings:
            evidence.append(
                Evidence(
                    ref=f"{doc.path}#{cid}",
                    says=f"RED means: {_clip(meanings[cid])}",
                    deref=f"grep -n '`{cid}`' {doc.path}",
                )
            )
        else:
            # A NAMED absence. Emitting the action with only the board's detail behind it would
            # be advice whose "why" nobody can check, and silence is how that goes unnoticed —
            # the row now carries its own hole, and `grep` returns nothing, which is the proof.
            evidence.append(
                Evidence(
                    ref=f"{doc.path}#{cid}",
                    says=f"MISSING — {doc.path} carries no `RED means` row for {cid}"
                    + (f" ({doc.detail})" if doc.state is not State.OK else "")
                    + ", so what this check exists to catch is UNSOURCED here",
                    deref=f"grep -n '`{cid}`' {doc.path}",
                )
            )
        priority, declared, from_ref, command = (
            NEUTRAL_PRIORITY,
            False,
            "gate",
            (
                "bin/gb-surface-gate.py --json | "
                + _jq("-", ".checks[]|select(.check==$k)", key=cid)
            ),
        )
        if bead is not None:
            bid = str(bead.get("id") or "")
            claimed.append(bid)
            priority = int(bead.get("priority") or NEUTRAL_PRIORITY)
            declared, from_ref, command = (
                True,
                f"beads:{bid}",
                f"{BEADS_TOOL} show {bid}",
            )
            evidence.append(
                Evidence(
                    ref=f"beads:{bid}",
                    says=f"p{priority} — {_clip(bead.get('title') or '')}",
                    deref=f"{BEADS_TOOL} show {bid}",
                )
            )
        lead = "clear" if verdict == "RED" else "restore the measurement behind"
        actions.append(
            Action(
                cls="red_gate",
                action=f"{lead} {cid}: {detail}"
                + (
                    ""
                    if bead is not None
                    else (
                        " (no open bead cites this check)"
                        if beads is not None
                        else " (the bead ledger was unreadable, so no bead could be joined)"
                    )
                )
                + (
                    f" [UNSOURCED: no `RED means` row for {cid} in {doc.path}]"
                    if cid in undocumented
                    else ""
                ),
                command=command,
                source="gate",
                evidence=tuple(evidence),
                blocking=True,
                priority=priority,
                priority_declared=declared,
                priority_from=from_ref,
                ident=cid,
            )
        )
    # Only claimable when the ledger was actually read. Saying "no bead cites these" after
    # failing to open the ledger would be a measurement claim built on an unreachable source —
    # the exact substitution this file refuses everywhere else.
    if actions and not claimed and beads is not None:
        notes.append(
            f"{len(actions)} not-GREEN check(s) and no open bead cites any of them by id, so "
            f"each sits at NEUTRAL p{NEUTRAL_PRIORITY} — the board and the ledger are tracking "
            f"different work"
        )
    return actions, claimed, notes


def advise_beads(
    beads: Optional[Dict[str, Any]], claimed: Sequence[str]
) -> List[Action]:
    """Every open bead that was not already folded into a `red_gate` row.

    `br`'s `priority` is taken at face value and `dependent_count` becomes the fourth ranking
    key, so this verb and `gb work` order the same ledger the same way.
    """
    if beads is None:
        return []
    out: List[Action] = []
    for row in _rows_of(beads, "issues"):
        bid = str(row.get("id") or "")
        if not bid or row.get("status") != "open" or bid in claimed:
            continue
        priority = int(row.get("priority") or NEUTRAL_PRIORITY)
        title = _clip(row.get("title") or "")
        out.append(
            Action(
                cls="open_bead",
                action=title,
                command=f"{BEADS_TOOL} show {bid}",
                source="beads",
                evidence=(
                    Evidence(
                        ref=f"beads:{bid}",
                        says=f"p{priority} · {row.get('status')} · {title}",
                        deref=f"{BEADS_TOOL} show {bid}",
                    ),
                ),
                priority=priority,
                priority_declared=True,
                dependents=int(row.get("dependent_count") or 0),
                ident=bid,
            )
        )
    return out


def advise_coverage(coverage: Optional[Dict[str, Any]]) -> List[Action]:
    """Facets nobody read.

    UNMEASURED only. EMPTY is a measurement whose answer is zero and advising on it would be the
    exact collapse this file refuses everywhere else; BLOCKED has no read path, so "measure it"
    is advice that cannot be taken.
    """
    if coverage is None:
        return []
    out: List[Action] = []
    for row in _rows_of(coverage, "rows"):
        if row.get("verdict") != "UNMEASURED":
            continue
        facet = str(row.get("facet") or "")
        gate_ref = str(row.get("gate") or "-")
        out.append(
            Action(
                cls="unmeasured_facet",
                action=f"nobody reads `{facet}` — the ledger carries no measurement for it "
                f"(gate {gate_ref}, artifact {row.get('artifact')})",
                command="bin/gb-coverage.py --json | "
                + _jq("-", ".rows[]|select(.facet==$k)", key=facet),
                source="coverage",
                evidence=(
                    Evidence(
                        ref=f"coverage:{facet}",
                        says=f"UNMEASURED · gate {gate_ref} · artifact {row.get('artifact')}",
                        deref="bin/gb-coverage.py --json | "
                        + _jq("-", ".rows[]|select(.facet==$k)", key=facet),
                    ),
                ),
                ident=facet,
            )
        )
    return out


def _squash(name: str) -> str:
    """`Google Docs` -> `googledocs`. The same normalisation `gb-gaps.py:78` uses to decide
    whether a plugin serves an integration, so the two producers agree on what a match is."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def advise_plugins(
    usecases: Optional[Dict[str, Any]],
    market: Optional[Dict[str, Any]],
    uc_path: str,
    mk_path: str,
) -> Tuple[List[Action], List[str]]:
    """DISCOVERY — an approved catalog plugin for something the population already builds on,
    which this account has not installed.

    This is the inverse of `gb-gaps.py`'s DEMAND GAP. That one asks which integrations NO plugin
    serves; this asks which ones a plugin serves perfectly well while this deployment does
    without. Neither `usecases` nor `market` can answer it alone, which is why nothing did.

    The slug match is EXACT on the squashed name, deliberately stricter than `gb-gaps.py`'s
    substring rule. Measured while writing this: the loose rule matched `X` to `box`, `LinkedIn`
    to `link` and `Google Analytics` to `glean` — fine for a gap analysis, where a false positive
    only shrinks a claimed gap, and wrong here, where a false positive is advice to install the
    wrong software.
    """
    notes: List[str] = []
    if usecases is None or market is None:
        return [], notes
    plugins = market.get("plugins")
    rows = plugins.get("rows") if isinstance(plugins, dict) else None
    installed_raw = market.get("installed")
    if not isinstance(rows, list) or not isinstance(installed_raw, list):
        return [], ["market artifact carries no plugin catalog or no installed list"]
    catalog: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("name"):
            catalog.setdefault(_squash(str(row["name"])), row)
    installed = {
        str(p.get("name"))
        for p in installed_raw
        if isinstance(p, dict) and p.get("name")
    }
    where = _install_path()
    out: List[Action] = []
    for name, bots in _pairs_of(usecases, "integrations"):
        if bots < MIN_POPULATION:
            continue
        row = catalog.get(_squash(name))
        if row is None or str(row.get("name")) in installed:
            continue
        if str(row.get("status")) != "PLUGIN_STATUS_APPROVED":
            continue
        slug = str(row["name"])
        shape = (
            f"{row.get('kind')}, {row.get('skills')} skill(s), "
            f"{row.get('mcp_servers')} MCP server(s), publisher {row.get('publisher')}"
        )
        out.append(
            Action(
                cls="install_plugin",
                action=f"install the `{slug}` plugin — {bots} of "
                f"{usecases.get('bots')} attributed Bots build on {name}, the catalog ships an "
                f"approved connector ({shape}), and this account has not installed it"
                + (f". {where}" if where else ""),
                command=_jq(mk_path, ".plugins.rows[]|select(.name==$k)", key=slug),
                source="market",
                evidence=(
                    Evidence(
                        ref=f"{uc_path}#.integrations[{name}]",
                        says=f"{bots} Bot(s) in the corpus name {name}",
                        deref=_jq(
                            uc_path, ".integrations[]|select(.[0]==$k)", key=name
                        ),
                    ),
                    Evidence(
                        ref=f"{mk_path}#.plugins.rows[{slug}]",
                        says=shape,
                        deref=_jq(
                            mk_path, ".plugins.rows[]|select(.name==$k)", key=slug
                        ),
                    ),
                    Evidence(
                        ref=f"{mk_path}#.installed",
                        says=f"{len(installed)} installed: {', '.join(sorted(installed))} "
                        f"— `{slug}` is not among them",
                        deref=_jq(mk_path, "[.installed[].name]"),
                    ),
                ),
                population=bots,
                ident=slug,
            )
        )
    return out, notes


def _install_path() -> str:
    """The UI path for adding a plugin, quoted from `features.json` rather than remembered.

    Empty string when the file is unreadable: an unsourced UI path is exactly the kind of
    plausible-sounding instruction that is wrong after one release.
    """
    doc = read_json_capped(ROOT / "features.json")
    if not isinstance(doc, dict):
        return ""
    for feature in _rows_of(doc, "features"):
        if feature.get("id") == "plugins-connectors" and feature.get("where"):
            return f"Path (features.json#plugins-connectors): {feature['where']}"
    return ""


def advise_domains(gaps: Optional[Dict[str, Any]], gaps_path: str) -> List[Action]:
    """DISCOVERY — a whole field the catalog does not serve, where a working implementation
    already exists in the Dicklesworthstone mirror, carried with file:line evidence.

    `plugins: 0` is reported by `gb-gaps.py` as a FLOOR (its matching is deliberately broad), so
    the claim here is "at most this empty and never emptier", which is the honest direction for
    a sentence that says nobody has built something.
    """
    if gaps is None:
        return []
    by_repo = {
        str(c.get("repo")): c
        for c in _rows_of(gaps, "mirror_candidates")
        if c.get("repo")
    }
    out: List[Action] = []
    for row in _rows_of(gaps, "domain_gap"):
        if row.get("plugins") != 0:
            continue
        domain = str(row.get("domain") or "")
        repos = [
            str(r) for r in (row.get("mirror_candidates") or []) if isinstance(r, str)
        ]
        if not domain or not repos:
            continue
        evidence = [
            Evidence(
                ref=f"{gaps_path}#.domain_gap[{domain}]",
                says=f"0 of {gaps.get('catalog_plugins')} catalog plugins serve {domain} "
                f"(floor); mirror candidates: {', '.join(repos)}",
                deref=_jq(gaps_path, ".domain_gap[]|select(.domain==$k)", key=domain),
            )
        ]
        lead = by_repo.get(repos[0])
        pointer = ""
        if lead is not None:
            spots = [
                f"{e.get('path')}:{e.get('line')} ({e.get('symbol')})"
                for e in _rows_of(lead, "evidence")
            ][:2]
            if spots:
                pointer = f" — `{repos[0]}` already implements it at {', '.join(spots)}"
                evidence.append(
                    Evidence(
                        ref=f"{gaps_path}#.mirror_candidates[{repos[0]}]",
                        says=f"{lead.get('domain')} · {', '.join(spots)}",
                        deref=_jq(
                            gaps_path,
                            ".mirror_candidates[]|select(.repo==$k)",
                            key=repos[0],
                        ),
                    )
                )
        out.append(
            Action(
                cls="unserved_domain",
                action=f"no catalog plugin serves {domain} (floor: 0 of "
                f"{gaps.get('catalog_plugins')}){pointer}",
                command=f"bin/gb-mine.py --query {shlex.quote(domain)} --limit 5",
                source="gaps",
                evidence=tuple(evidence),
                ident=domain,
            )
        )
    return out


def advise_mine(mine: Optional[Dict[str, Any]], mine_path: str) -> List[Action]:
    """DISCOVERY — the best-ranked mined row per query: a named symbol at a named line.

    One per query, not all 24. `gb-mine` already round-robins its sources and states that scores
    from different corpora are not comparable, so taking the head of each query's list is the
    strongest true statement available here; taking the global top N would silently re-rank
    across corpora it told us not to compare.
    """
    if mine is None:
        return []
    best: Dict[str, Dict[str, Any]] = {}
    for cand in _rows_of(mine, "candidates"):
        query = str(cand.get("query") or "")
        rank = int(cand.get("rank") or 0)
        if query and (query not in best or rank < int(best[query].get("rank") or 0)):
            best[query] = cand
    out: List[Action] = []
    for query, cand in sorted(best.items()):
        repo, spot = str(cand.get("repo") or ""), str(cand.get("evidence") or "")
        out.append(
            Action(
                cls="adoptable_technique",
                action=f"read {spot} in `{repo}` — the top mined answer to "
                f"\u201c{_clip(query, 90)}\u201d: {_clip(cand.get('why') or '', 140)}",
                command=f"bin/gb-mine.py --query {shlex.quote(query)} --limit 5",
                source="mine",
                evidence=(
                    Evidence(
                        ref=f"{mine_path}#.candidates[rank={cand.get('rank')}]",
                        says=f"[{cand.get('source')}] {spot} — {_clip(cand.get('why') or '', 140)}",
                        deref=_jq(
                            mine_path,
                            ".candidates[]|select(.evidence==$k)",
                            key=spot,
                        ),
                    ),
                ),
                ident=spot or repo,
            )
        )
    return out


def advise_discovery(
    discovery: Optional[Dict[str, Any]], disc_path: str
) -> Tuple[List[Action], List[str]]:
    """Notable repos the ecosystem produced that this repo has neither adopted nor dismissed.

    The two triage ledgers are `sources.json` (watched) and `discovery-seen.json` (`dismissed`,
    with a written reason). When either is unreadable the class is SUPPRESSED rather than run on
    half a ledger — a repo that looks untriaged because the dismissal list could not be read is a
    false finding, and this is the same distinction as EMPTY vs UNREACHABLE one level down.
    """
    if discovery is None:
        return [], []
    notable = [str(r) for r in (discovery.get("notable") or []) if isinstance(r, str)]
    watched_doc = read_json_capped(ROOT / "sources.json")
    seen_doc = read_json_capped(ROOT / "discovery-seen.json")
    if not isinstance(watched_doc, dict) or not isinstance(seen_doc, dict):
        return [], [
            "discovery triage suppressed: sources.json or discovery-seen.json is unreadable, so "
            "'untriaged' could not be distinguished from 'dismissed with a reason'"
        ]
    watched = {
        str(r.get("repo"))
        for r in _rows_of(watched_doc, "repos")
        if isinstance(r, dict) and r.get("repo")
    }
    dismissed_raw = seen_doc.get("dismissed")
    dismissed = set(dismissed_raw) if isinstance(dismissed_raw, dict) else set()
    stars = {
        str(r.get("repo")): int(r.get("stars") or 0)
        for r in _rows_of(discovery, "new")
        if r.get("repo")
    }
    threshold = discovery.get("notable_stars")
    out: List[Action] = []
    for repo in notable:
        if repo in watched or repo in dismissed:
            continue
        out.append(
            Action(
                cls="triage_discovery",
                action=f"triage `{repo}` ({stars.get(repo, 0)}\u2605, at or above the "
                f"{threshold}\u2605 bar) into sources.json, or dismiss it with a written reason",
                command=_jq(disc_path, ".new[]|select(.repo==$k)", key=repo),
                source="discovery",
                evidence=(
                    Evidence(
                        ref=f"{disc_path}#.notable[{repo}]",
                        says=f"{stars.get(repo, 0)} stars, in neither sources.json nor "
                        f"discovery-seen.json#dismissed",
                        deref=_jq(disc_path, ".new[]|select(.repo==$k)", key=repo),
                    ),
                ),
                ident=repo,
            )
        )
    return out, []


def advise_utilization(util: Optional[Dict[str, Any]], util_path: str) -> List[Action]:
    """Whether the fleet earns its keep — one row per measured field, never one per Bot.

    Four separate numbers, four separate actions, because they have four separate fixes. A
    per-Bot fan-out would produce six identical rows saying the same thing and drown the ranking
    in one class.
    """
    if util is None:
        return []
    out: List[Action] = []

    def row(
        cls_id: str, action: str, command: str, ref: str, says: str, deref: str
    ) -> Action:
        return Action(
            cls="idle_asset",
            action=action,
            command=command,
            source="utilization",
            evidence=(Evidence(ref=f"{util_path}#{ref}", says=says, deref=deref),),
            ident=cls_id,
        )

    orphaned = util.get("orphaned_entries")
    live = util.get("live_entries")
    if isinstance(orphaned, int) and isinstance(live, int) and orphaned > live:
        out.append(
            row(
                "orphaned-history",
                f"{orphaned} orphaned transcript entries against {live} live ones across "
                f"{util.get('orphaned_replicas')} deleted Bot(s) — archive it before the "
                f"client's ~200-entry cap rotates it away",
                "bin/gb-context-archive.py",
                ".orphaned_entries",
                f"orphaned_entries={orphaned} vs live_entries={live}, "
                f"orphaned_replicas={util.get('orphaned_replicas')}",
                _jq(util_path, "{orphaned_entries,live_entries,orphaned_replicas}"),
            )
        )
    idle = [str(b) for b in (util.get("idle_credentialed") or []) if isinstance(b, str)]
    if idle:
        out.append(
            row(
                "idle-credentialed",
                f"{len(idle)} credentialed Bot(s) are idle ({', '.join(idle)}) — each holds "
                f"account access it is not using; give it work or take the credential back",
                "bin/gb-utilization.py",
                ".idle_credentialed",
                ", ".join(idle),
                _jq(util_path, ".idle_credentialed"),
            )
        )
    routines = util.get("routines_total")
    with_runs = sum(
        1 for b in _rows_of(util, "bots") if int(b.get("routines_with_runs") or 0) > 0
    )
    if isinstance(routines, int) and with_runs == 0:
        out.append(
            row(
                "routines-never-ran",
                f"{routines} routine(s) exist across {util.get('bot_count')} Bot(s) and not one "
                f"has a recorded run — scheduled work that never fires is a setting, not a fleet",
                "bin/gb-utilization.py",
                ".routines_total",
                f"routines_total={routines}, Bots with a recorded run={with_runs}",
                _jq(
                    util_path,
                    "{routines_total,bot_count,bots:[.bots[]|{name,routines,routines_with_runs}]}",
                ),
            )
        )
    shards = util.get("memory_shards_with_content")
    if shards == 0:
        out.append(
            row(
                "no-memory",
                f"no Bot carries any persistent memory (0 shards with content across "
                f"{util.get('bot_count')} Bots) — every run starts from nothing",
                "bin/gb-utilization.py",
                ".memory_shards_with_content",
                f"memory_shards_with_content=0 across bot_count={util.get('bot_count')}",
                _jq(util_path, "{memory_shards_with_content,bot_count}"),
            )
        )
    return out


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class AdviseArgs:
    """Rank what to do next by joining every producer this repo already runs."""

    limit: int = arg(default=5, metavar="N", help="ranked actions to show (default 5)")
    source: Optional[List[str]] = arg(
        metavar="STR",
        help="restrict the join to this input; repeatable. Known: "
        + ", ".join(SOURCE_NAMES),
    )
    dry_run: bool = arg(help="rank and report, but write no artifact")
    json: bool = arg(
        help="machine-readable envelope on stdout (top---limit actions unless --full)"
    )
    full: bool = arg(help="include every ranked action; counts always full")
    selftest: bool = arg(
        help="run the inline-fixture selftest (plus the GATES.md evidence-coverage leg) and exit"
    )


def build_actions(
    docs: Dict[str, Optional[Dict[str, Any]]], sources: Sequence[Source]
) -> Tuple[List[Action], List[str], List[str], List[str]]:
    """Run every class whose inputs were selected AND readable.

    Returns `(actions, suppressed, ran, caveats)`. `ran` matters as much as `actions`: a class
    that executed and found nothing is a MEASUREMENT whose answer is zero, and a class whose
    input was missing is not. Collapsing those two into "it is not in the list" is the same
    defect as collapsing EMPTY into UNREACHABLE one level up, so the census keeps them apart.
    """
    origin = {s.name: s.origin for s in sources}
    selected = {s.name for s in sources}
    got = {s.name for s in sources if s.read and docs.get(s.name) is not None}
    actions: List[Action] = []
    suppressed: List[str] = []
    ran: List[str] = []
    caveats: List[str] = []

    def need(cls: str, *names: str) -> bool:
        missing = [n for n in names if n not in got]
        if missing:
            reason = (
                "not selected"
                if any(n not in selected for n in missing)
                else "UNREACHABLE"
            )
            suppressed.append(
                f"{cls}: needs {', '.join(names)} — {', '.join(missing)} {reason}"
            )
            return False
        ran.append(cls)
        return True

    claimed: List[str] = []
    if need("red_gate", "gate"):
        gate_actions, claimed, gate_notes = advise_gate(
            docs.get("gate"), docs.get("beads")
        )
        actions += gate_actions
        caveats += gate_notes
        if "beads" not in got:
            caveats.append(
                "red_gate ran without the bead ledger, so no RED check could inherit a declared "
                "priority; every one of them sits at NEUTRAL because the join was unavailable, "
                "not because no bead exists"
            )
    if need("open_bead", "beads"):
        actions += advise_beads(docs.get("beads"), claimed)
    if need("unmeasured_facet", "coverage"):
        actions += advise_coverage(docs.get("coverage"))
    if need("install_plugin", "usecases", "market"):
        plugin_actions, notes = advise_plugins(
            docs.get("usecases"),
            docs.get("market"),
            origin.get("usecases", ""),
            origin.get("market", ""),
        )
        actions += plugin_actions
        suppressed += notes
    if need("unserved_domain", "gaps"):
        actions += advise_domains(docs.get("gaps"), origin.get("gaps", ""))
    if need("adoptable_technique", "mine"):
        actions += advise_mine(docs.get("mine"), origin.get("mine", ""))
    if need("triage_discovery", "discovery"):
        disc_actions, notes = advise_discovery(
            docs.get("discovery"), origin.get("discovery", "")
        )
        actions += disc_actions
        suppressed += notes
    if need("idle_asset", "utilization"):
        actions += advise_utilization(
            docs.get("utilization"), origin.get("utilization", "")
        )

    # An unreadable oracle is a stop condition in its own right (GATES.md §Fail-closed rules:
    # "Missing oracle = FAIL, not skip"), so it is ranked WITH the RED checks rather than below
    # them: advice computed on a view you cannot see all of is the thing to fix first.
    for src in sources:
        if src.read:
            continue
        if "restore_source" not in ran:
            ran.append("restore_source")
        actions.append(
            Action(
                cls="restore_source",
                action=f"the `{src.name}` input could not be read, so every class that needs it "
                f"was suppressed — {src.detail}",
                command=src.remediation or _PRODUCERS.get(src.name, ""),
                source=src.name,
                evidence=(
                    Evidence(
                        ref=f"source:{src.name}",
                        says=f"UNREACHABLE — {src.detail}",
                        deref=src.remediation or _PRODUCERS.get(src.name, ""),
                    ),
                ),
                blocking=True,
                ident=src.name,
            )
        )
    return actions, suppressed, ran, caveats


def render(
    payload: Dict[str, Any], shown: Sequence[Action], sources: Sequence[Source]
) -> None:
    """Human output. stdout is the data; diagnostics already went to stderr."""
    totals = payload["totals"]
    print(
        f"gb-advise \u2014 {payload['view']} \u00b7 {len(sources)} source(s) "
        f"({totals['sources_ok']} OK, {totals['sources_empty']} EMPTY, "
        f"{totals['sources_unreachable']} UNREACHABLE) \u00b7 {totals['actions']} action(s)"
    )
    print()
    print("sources:")
    for src in sources:
        print(f"  {src.state.value:<12} {src.name:<12} {_clip(src.detail, 110)}")
        for note in src.notes:
            print(f"               note: {_clip(note, 104)}")
        if src.remediation and not src.read:
            print(f"               fix: {src.remediation}")
    eve = payload.get("gates_evidence") or {}
    if eve:
        recovery = eve.get("recovery") or "no denominator (the board was unreadable)"
        print(
            f"  gate meanings: {recovery} registered check(s) documented in "
            f"{eve.get('doc')} [{eve.get('doc_state')}]"
        )
        if eve.get("undocumented"):
            print(f"               UNSOURCED: {', '.join(eve['undocumented'])}")
    print()
    print(
        f"ranked ({payload['ranking']['method']}; keys: {', '.join(payload['ranking']['keys'])}):"
    )
    for act in shown:
        print(f"  {act.rank:>2}. [{act.cls}] {_clip(act.action, 150)}")
        print(f"      why: {act.why()}")
        print(f"      run: {act.command}")
        for ev in act.evidence[:2]:
            print(f"      evidence: {ev.ref} \u2014 {_clip(ev.says, 110)}")
    if not shown:
        print("  (no action from the sources that could be read)")
    print()
    print(
        "by class (every class that RAN, so --limit hides no kind of advice, and a class "
        "that found nothing is visibly a zero rather than an absence):"
    )
    for row in payload["classes"]:
        best = (
            f"best rank {row['best_rank']:<4}{_clip(row['best_action'], 86)}"
            if row["count"]
            else "ran, and the measured answer is zero"
        )
        print(f"  {row['class']:<20}{row['count']:>3}  {best}")
    if payload["suppressed"]:
        print()
        print("suppressed classes (input missing — NOT a measured zero):")
        for line in payload["suppressed"]:
            print(f"  {_clip(line, 150)}")
    if payload["caveats"]:
        print()
        print("caveats — what this ranking does NOT establish:")
        for line in payload["caveats"]:
            print(f"  {_clip(line, 150)}")


# ---------------------------------------------------------------------------------------------
# Selftest — inline fixtures, plus one leg measured against the repo's own board
# ---------------------------------------------------------------------------------------------
_SELFTEST_DOC = """# Gates

Some prose that mentions `g9-routine-health` outside any table at all.

| Check | RED means | ERROR means |
| --- | --- | --- |
| `g1-surface-fetch-integrity` | a page came back short | the index did not return 200 |
  | `g9-routine-health` | an enabled routine whose every run failed | no inventory to read |
| `g100-future-check` | an id past two digits | nothing |
| `g12-ondemand-spend-bounded` | spend is on pace to exceed A \\| B of the allowance | no usage |
| not-a-check | ignore me | ignore me |
"""


def _registered_checks() -> Tuple[List[str], str]:
    """Every check id `bin/gb-surface-gate.py` registers, read from the producer itself.

    The same source `bin/gb-gatesdoc.py:45` compares the doc against, so the two oracles cannot
    disagree about what the roster is. Import, not `--json`: the roster is a literal in the
    module and running the whole gate to recover it would make this leg cost a board run.
    """
    gate_py = ROOT / "bin" / "gb-surface-gate.py"
    if not gate_py.is_file():
        return [], f"{gate_py} is absent"
    spec = importlib.util.spec_from_file_location("gb_surface_gate_roster", gate_py)
    if spec is None or spec.loader is None:
        return [], f"{gate_py} is not importable"
    mod = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: on python 3.9 a module that defines a dataclass resolves its own
    # annotations through `sys.modules[__name__]` and raises KeyError without this line.
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 — any import failure is the same verdict here
        return [], f"{gate_py} failed to import: {exc}"
    finally:
        sys.modules.pop(spec.name, None)
    ids = [str(c) for c in (getattr(mod, "CHECKS", None) or [])]
    return ids, "" if ids else f"{gate_py} registers no CHECKS"


def _board(*rows: Tuple[str, str]) -> Dict[str, Any]:
    """A gate document shaped like `bin/gb-surface-gate.py --json`, from `(id, verdict)` pairs."""
    return {
        "checks": [
            {"check": cid, "verdict": verdict, "detail": f"{cid} said so"}
            for cid, verdict in rows
        ]
    }


def selftest() -> int:
    """Offline proofs on INLINE fixtures, plus ONE leg measured against this repo's real board.

    Every positive leg has a known-bad sibling that must produce the opposite answer, because a
    coverage check that cannot fail is not a check. The leg that matters most is
    `every-registered-check-has-a-documented-meaning`: it reads the REAL `GATES.md` against the
    REAL roster in `bin/gb-surface-gate.py`, so a check landing tomorrow without a `RED means`
    row turns this selftest red instead of quietly thinning the advice.
    """
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    with tempfile.TemporaryDirectory(prefix="gb-advise-selftest-") as td:
        top = pathlib.Path(td)

        # ---- 1. the parser, on the shapes the anchored regex used to drop -------------------
        doc_path = top / "GATES.md"
        atomic_write_text(doc_path, _SELFTEST_DOC)
        doc = _gates_md(doc_path)
        leg(
            "parses-a-plain-table-row",
            doc.state is State.OK
            and doc.rows.get("g1-surface-fetch-integrity") == "a page came back short",
            f"got {doc.state} {doc.rows.get('g1-surface-fetch-integrity')!r}",
        )
        leg(
            "parses-an-indented-row",
            "g9-routine-health" in doc.rows,
            "an indented `| `gN` |` row is still a row; the old `^\\|` anchor dropped it",
        )
        leg(
            "parses-an-id-past-two-digits",
            "g100-future-check" in doc.rows,
            "the old `g\\d{1,2}` bound dropped g100 while gb-gatesdoc.py (`g\\d+`) kept it",
        )
        leg(
            "does-not-truncate-a-cell-at-an-escaped-pipe",
            doc.rows.get("g12-ondemand-spend-bounded", "").endswith(
                "A | B of the allowance"
            ),
            f"got {doc.rows.get('g12-ondemand-spend-bounded')!r} — a half-quoted meaning still "
            f"looks sourced, which is worse than a missing one",
        )
        leg(
            "KNOWN-BAD-prose-mention-is-not-a-documented-meaning",
            len(doc.rows) == 4 and "not-a-check" not in doc.rows,
            f"only table rows with a `\\`gN-…\\`` first cell count; got {sorted(doc.rows)}",
        )

        # ---- 2. absence is typed, never an empty dict ---------------------------------------
        gone = _gates_md(top / "nope.md")
        empty = top / "empty.md"
        atomic_write_text(empty, "# Gates\n\nno table here at all\n")
        blank = _gates_md(empty)
        leg(
            "KNOWN-BAD-absent-doc-is-UNREACHABLE-not-empty",
            gone.state is State.UNREACHABLE
            and not gone.rows
            and "absent" in gone.detail,
            f"got {gone.state} {gone.detail!r}",
        )
        leg(
            "KNOWN-BAD-tableless-doc-is-EMPTY-not-UNREACHABLE",
            blank.state is State.EMPTY and not blank.rows,
            f"a file that answered nothing is not a file that never answered; got {blank.state}",
        )

        # ---- 3. coverage is measured against the BOARD's roster ------------------------------
        full = _board(
            ("g1-surface-fetch-integrity", "GREEN"), ("g9-routine-health", "RED")
        )
        holed = _board(
            ("g1-surface-fetch-integrity", "GREEN"),
            ("g9-routine-health", "RED"),
            ("g77-undocumented", "RED"),
        )
        documented, undocumented = gates_coverage(full, doc)
        leg(
            "coverage-counts-every-registered-check-green-ones-included",
            len(documented) == 2 and not undocumented,
            f"got {documented} / {undocumented}",
        )
        _, holes = gates_coverage(holed, doc)
        leg(
            "KNOWN-BAD-coverage-names-the-check-with-no-row",
            holes == ["g77-undocumented"],
            f"got {holes}",
        )
        leg(
            "coverage-note-is-silent-when-every-check-is-documented",
            _coverage_notes(full, doc)[1] == [],
            f"got {_coverage_notes(full, doc)[1]}",
        )
        holed_notes = _coverage_notes(holed, doc)[1]
        leg(
            "KNOWN-BAD-coverage-note-fires-and-states-the-fraction",
            len(holed_notes) == 1
            and "2/3" in holed_notes[0]
            and "g77-undocumented" in holed_notes[0],
            f"got {holed_notes}",
        )
        gone_notes = _coverage_notes(holed, gone)[1]
        leg(
            "KNOWN-BAD-unreadable-doc-says-so-instead-of-listing-25-holes",
            len(gone_notes) == 1
            and "no check meaning could be recovered" in gone_notes[0],
            f"got {gone_notes}",
        )

        # ---- 4. the join: a documented check is sourced, an undocumented one SAYS it is not --
        actions, _claimed, notes = advise_gate(holed, {"issues": []}, doc)
        by_id = {a.ident: a for a in actions}
        sourced = by_id.get("g9-routine-health")
        unsourced = by_id.get("g77-undocumented")
        leg(
            "red-check-with-a-row-carries-its-RED-means-evidence",
            sourced is not None
            and any(
                e.says.startswith("RED means:") and "routine" in e.says
                for e in sourced.evidence
            ),
            f"got {[e.says for e in sourced.evidence] if sourced else None}",
        )
        leg(
            "KNOWN-BAD-red-check-with-no-row-emits-a-MISSING-evidence-row",
            unsourced is not None
            and any(
                e.says.startswith("MISSING") and "g77-undocumented" in e.ref
                for e in unsourced.evidence
            ),
            "an action whose meaning is unknown must SAY so; got "
            f"{[e.says for e in unsourced.evidence] if unsourced else None}",
        )
        leg(
            "KNOWN-BAD-unsourced-action-text-is-marked-in-the-headline",
            unsourced is not None
            and "[UNSOURCED:" in unsourced.action
            and sourced is not None
            and "[UNSOURCED:" not in sourced.action,
            "the marker must land on the undocumented row and ONLY on it",
        )
        leg(
            "the-join-still-carries-the-boards-own-detail-for-both",
            sourced is not None
            and unsourced is not None
            and all(
                any(e.ref.startswith("gate:") for e in a.evidence)
                for a in (sourced, unsourced)
            ),
            "a missing meaning must not cost the row its board evidence",
        )

        # ---- 5. the payload census ------------------------------------------------------------
        block = gates_evidence_block(holed, doc)
        leg(
            "payload-census-publishes-the-recovery-fraction",
            block["recovery"] == "2/3"
            and block["undocumented"] == ["g77-undocumented"],
            f"got {block['recovery']} {block['undocumented']}",
        )
        blind = gates_evidence_block(None, doc)
        leg(
            "KNOWN-BAD-census-has-no-denominator-when-the-board-is-unreadable",
            blind["recovery"] is None
            and blind["checks_registered"] is None
            and blind["rows_in_doc"] == 4,
            f"the doc's own row count is not coverage; got {blind}",
        )

        # ---- 6. THE REGRESSION LEG: the real board against the real GATES.md -----------------
        registered, why = _registered_checks()
        real = _gates_md()
        real_board = _board(*[(cid, "GREEN") for cid in registered])
        real_documented, real_holes = gates_coverage(real_board, real)
        leg(
            "the-roster-and-the-doc-are-both-readable",
            bool(registered) and real.state is State.OK,
            f"roster: {why or len(registered)}; doc: {real.detail}. A missing oracle is a FAIL, "
            f"not a skip (GATES.md §Fail-closed rules)",
        )
        leg(
            "every-registered-check-has-a-documented-meaning",
            bool(registered) and not real_holes,
            f"{len(real_documented)}/{len(registered)} recovered; NO `RED means` row in "
            f"{real.path} for {real_holes} — add the row, or `gb advise` will hand out actions "
            f"nothing sources",
        )
        # The fires-on-known-bad proof for the leg immediately above: the same measurement over
        # a copy of the SHIPPED doc with one row deleted must report exactly that id. Without
        # this, a coverage leg that silently measured nothing would pass forever.
        victim = registered[-1] if registered else ""
        doctored = top / "GATES.doctored.md"
        atomic_write_text(
            doctored,
            (
                "\n".join(
                    line
                    for line in (ROOT / "GATES.md")
                    .read_text(errors="replace")
                    .splitlines()
                    if not line.strip().startswith(f"| `{victim}`")
                )
                + "\n"
                if victim
                else ""
            ),
        )
        _, doctored_holes = gates_coverage(real_board, _gates_md(doctored))
        leg(
            "KNOWN-BAD-deleting-one-row-turns-the-coverage-leg-red",
            bool(victim) and victim in doctored_holes and victim not in real_holes,
            f"removed `| \\`{victim}\\`` from a copy of {real.path}; coverage reported "
            f"{doctored_holes} against a shipped-doc baseline of {real_holes}, so deleting a "
            f"row did NOT turn the leg above red",
        )

    passed = sum(1 for _, ok, _ in legs if ok)
    for name, ok, detail in legs:
        print(
            f"  {'ok  ' if ok else 'FAIL'} {name}"
            + (f"  \u2014 {detail}" if detail and not ok else ""),
            file=sys.stderr,
        )
    if passed != len(legs):
        print(f"SELFTEST FAIL - {passed}/{len(legs)}", file=sys.stderr)
        # 1 is this verb's "there is something wrong" code; the selftest is not a usage error
        # and not an environment refusal, so it shares that channel (gb-templates.py:27).
        return EXIT_FINDINGS
    print(f"SELFTEST PASS - {passed}/{len(legs)}")
    return EXIT_OK


def body() -> int:
    args = parse(AdviseArgs, description=SUMMARY)
    if args.selftest:
        # First, and before any other flag is judged: the selftest reads nothing outside the
        # checkout and writes nothing, so it must stay runnable on a machine where every source
        # is UNREACHABLE.
        return selftest()
    if args.limit < 1:
        print("gb-advise: --limit must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    requested = tuple(s for s in (args.source or []) if s.strip())
    unknown = [s for s in requested if s not in SOURCE_NAMES]
    if unknown:
        print(
            f"gb-advise: unknown --source {', '.join(repr(u) for u in unknown)}. "
            f"Known: {', '.join(SOURCE_NAMES)} (omit for all)\n"
            f"    try: gb-advise.py --source market --source usecases",
            file=sys.stderr,
        )
        return EXIT_USAGE
    wanted = requested or SOURCE_NAMES

    sources, docs = read_sources(wanted)
    unreachable = [s.name for s in sources if not s.read]
    if not any(s.read for s in sources):
        # Nothing could be read at all. This MUST NOT render as "nothing to advise": exit 3 says
        # the machine cannot answer the question, which is what happened.
        for src in sources:
            print(
                f"gb-advise: {src.name} UNREACHABLE \u2014 {src.detail}\n"
                f"           fix: {src.remediation}",
                file=sys.stderr,
            )
        stamp = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}"
        payload: Dict[str, Any] = {
            "schema": SCHEMA,
            "advised_at": stamp,
            "status": "ENVIRONMENT",
            "exit": EXIT_ENVIRONMENT,
            "view": "BLIND",
            "sources": [s.to_json() for s in sources],
            "totals": {
                "sources_ok": 0,
                "sources_empty": 0,
                "sources_unreachable": len(sources),
                "actions": 0,
                "shown": 0,
            },
            "actions": [],
            "classes": [],
            "suppressed": ["every class: no source could be read"],
            "caveats": [],
            "unreachable_sources": unreachable,
            # Present even here: a consumer that reads `gates_evidence` must never KeyError its
            # way into believing coverage is fine because the run went blind.
            "gates_evidence": gates_evidence_block(None),
            "artifact": None,
        }
        if args.json:
            print(json.dumps(payload, indent=1))
        else:
            print(
                "gb-advise \u2014 BLIND \u00b7 "
                f"{len(sources)} source(s), none readable \u2014 this is NOT 'nothing to do'"
            )
            for src in sources:
                print(f"  UNREACHABLE  {src.name:<12} {_clip(src.detail, 110)}")
            print("\nnext:")
            for src in sources:
                if src.remediation:
                    print(f"  {src.remediation}")
        return EXIT_ENVIRONMENT

    actions, suppressed, ran, caveats = build_actions(docs, sources)
    ordered = sorted(actions, key=lambda a: a.sort_key)
    ranked = [
        dataclasses.replace(a, rank=i + 1) for i, a in enumerate(ordered[:MAX_ACTIONS])
    ]
    if len(ordered) > MAX_ACTIONS:
        # Silently dropping the tail would make the class census under-report, and a census that
        # under-reports is exactly the instrument failure this verb is supposed to catch.
        caveats.append(
            f"{len(ordered)} actions were produced and the artifact caps at {MAX_ACTIONS}; the "
            f"{len(ordered) - MAX_ACTIONS} lowest-ranked were dropped, so every class count "
            f"below is a floor"
        )
    shown = ranked[: args.limit]

    # Every class that RAN gets a row, including the ones that found nothing. A class missing
    # from this census did not execute, and `suppressed` says why; a class present with count 0
    # executed and measured zero. Those are different facts and the census keeps them different.
    classes: List[Dict[str, Any]] = []
    for name in sorted(set(ran)):
        members = [a for a in ranked if a.cls == name]
        classes.append(
            {
                "class": name,
                "count": len(members),
                "best_rank": members[0].rank if members else None,
                "best_action": _clip(members[0].action, 160) if members else "",
                "state": "OK" if members else "EMPTY",
            }
        )

    caveats += [
        "population_support counts Bots in a third-party corpus (elie222/botdirectory.ai). It "
        "measures what other people build, never whether a thing fits THIS account.",
        "a domain gap's `plugins: 0` is a FLOOR — gb-gaps.py matches broadly on purpose, so the "
        "catalog is at most this empty and never emptier.",
        "the only bead<->measurement link this verb can prove is an exact check id quoted inside "
        "a bead. A bead describing the same work in other words is ranked as separate work.",
    ]

    restricted = bool(requested) and len(requested) < len(SOURCE_NAMES)
    if unreachable:
        view = "PARTIAL"
    elif restricted:
        view = "RESTRICTED"
    else:
        view = "COMPLETE"
    if ranked:
        status, code = "FINDINGS", EXIT_FINDINGS
    elif not ran:
        # Not one class could execute — usually a `--source` set that feeds no class on its own.
        # Zero advice here is not a measurement of anything, and calling it OK would be the
        # EMPTY/UNREACHABLE collapse in miniature.
        status, code = "NO_CLASS_RAN", EXIT_OK
    elif unreachable:
        # Nothing advisable AND a source never answered. The contract fixes the exit at 0, so the
        # distinction has to survive somewhere the exit code cannot carry it: the status word.
        status, code = "PARTIAL_EMPTY", EXIT_OK
    else:
        status, code = "OK", EXIT_OK

    stamp = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}"
    payload = {
        "schema": SCHEMA,
        "advised_at": stamp,
        "status": status,
        "exit": code,
        "view": view,
        "view_note": {
            "COMPLETE": "every known source was selected and every one answered",
            "PARTIAL": "one or more selected sources could NOT be read; the classes that need "
            "them were suppressed and this ranking is computed on less than the whole picture",
            "RESTRICTED": "the join was restricted by --source; the omitted sources were not "
            "read and say nothing about this ranking",
            "BLIND": "no source could be read",
        }[view],
        "sources_known": list(SOURCE_NAMES),
        "sources_selected": list(wanted),
        "sources": [s.to_json() for s in sources],
        "ranking": {
            "method": "lexicographic",
            "keys": [
                "blocking_first",
                "declared_priority_asc",
                "population_support_desc",
                "dependents_desc",
                "class_then_id_asc",
            ],
            "key_reasons": {
                "blocking_first": "the repo's own doctrine names two stop conditions — a check "
                "the gate scores RED/ERROR, and an oracle that could not be read at all "
                "(GATES.md: 'Missing oracle = FAIL, not skip') — and work chosen while the "
                "board is failing is work chosen on an untrustworthy board.",
                "declared_priority_asc": "br's own 0..4 priority, at face value, because the "
                "author's stated urgency beats anything this verb could re-derive.",
                "population_support_desc": "how many of the corpus's attributed Bots already do "
                "this — the only external evidence available, and 0 where no such count exists, "
                "so it can only separate rows that already tie above it.",
                "dependents_desc": "br's dependent_count: work that unblocks other work beats "
                "leaf work (the same key gb work uses, so both verbs order the ledger alike).",
                "class_then_id_asc": "a deterministic tiebreak with digit runs compared "
                "numerically, so two runs over unchanged inputs are byte-identical and g8 sorts "
                "before g12.",
            },
            "note": "no weighted score: every key is a field some source already publishes. "
            "A source with no priority sits at NEUTRAL "
            f"{NEUTRAL_PRIORITY} by declaration, and those rows carry priority_declared=false.",
            "neutral_priority": NEUTRAL_PRIORITY,
            "min_population": MIN_POPULATION,
        },
        "totals": {
            "sources_ok": sum(1 for s in sources if s.state is State.OK),
            "sources_empty": sum(1 for s in sources if s.state is State.EMPTY),
            "sources_unreachable": len(unreachable),
            "actions_produced": len(ordered),
            "actions": len(ranked),
            "shown": len(shown),
        },
        "classes": classes,
        "actions": [a.to_json() for a in ranked],
        "suppressed": suppressed,
        "caveats": caveats,
        "unreachable_sources": unreachable,
        "gates_evidence": gates_evidence_block(docs.get("gate")),
        "commands": [a.command for a in shown[:3]],
    }

    # The path goes into the payload BEFORE the write: an artifact that does not name itself
    # cannot be cited, and `"artifact": null` inside a file on disk is a self-evident lie.
    artifact: Optional[pathlib.Path] = (
        None if args.dry_run else ROOT / "advise" / f"{stamp}.json"
    )
    payload["artifact"] = str(artifact.relative_to(ROOT)) if artifact else None
    if artifact is not None:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(artifact, payload)

    if args.json:
        out = (
            dict(payload)
            if args.full
            else {
                **payload,
                "actions": [a.to_json() for a in shown],
                "rows_truncated": len(ranked) - len(shown),
            }
        )
        print(json.dumps(out, indent=1))
    else:
        render(payload, shown, sources)

    for src in sources:
        if not src.read:
            print(
                f"gb-advise: {src.name} UNREACHABLE \u2014 {src.detail}\n"
                f"           this view is PARTIAL; every class needing {src.name} was suppressed\n"
                f"           fix: {src.remediation}",
                file=sys.stderr,
            )
    if status == "NO_CLASS_RAN":
        print(
            f"gb-advise: no advice class could run under --source "
            f"{', '.join(requested)} \u2014 that is a restriction, NOT a measurement that there "
            f"is nothing to do.\n"
            f"           every class and its inputs are listed in `suppressed`; drop --source "
            f"to join all {len(SOURCE_NAMES)}.",
            file=sys.stderr,
        )
    if artifact is not None and not args.json:
        print(f"\nwrote {artifact.relative_to(ROOT)}", file=sys.stderr)
    return code


if __name__ == "__main__":
    main(body)
