#!/usr/bin/env python3
"""gb-dogfood — is every capability in this repo reachable through `gb`, or only by hand?

WHY THIS EXISTS (measured 2026-09-11, and the measurement is an indictment).

The operator's standing instruction is: dogfood the CLI; if the CLI is missing a verb, ADD the
verb. The instruction exists because the opposite happened. In one evening the agent hand-rolled
the same three RPC calls SIX times as inline `importlib.spec_from_file_location` scripts — list
the Bots, read one Bot's routines, send one Bot a message — and each of those six copies got
something different wrong:

  1. `ListGrokBotAgentAutomations` was called with the record's NUMERIC `id` and answered
     `400 invalid_argument: Grok Bot agent id must be the agent's UUID`. The record carries BOTH
     ids and they are not interchangeable: writes take `id`, the automations read takes `agentId`.
  2. A roster read off the local `sand-client-persistence` blob reported a Bot as DELETED while
     it was live in the API and visible on another Mac. That blob is a per-machine CACHE; absence
     there is never evidence a Bot does not exist.
  3. `updatedAtMs` came back a STRING on one path and an int on another, so `int(x) / 1000` died
     with `TypeError: unsupported operand type(s) for /: 'str' and 'int'` mid-analysis.

Those three verbs are now `gb fleet roster|routines|send`, so those three bugs are retired. This
producer exists so the NEXT one is caught before it is hand-rolled a sixth time. It asks one
question mechanically, over the tree instead of over memory:

    what can this repo DO that `gb` cannot REACH?

HOW IT MEASURES, so no row here is an opinion.

  * Producer surface — for every `bin/gb-*.py`: parse its AST for the `choices=` on its own
    POSITIONAL argument (both idioms: raw `ap.add_argument("verb", choices=...)` and the derived
    `gbargs.arg(positional=True, choices=...)`), plus any `add_parser("x")` subcommands; then
    run `<producer> --help` with a deadline and read the `{a,b,c}` group out of argparse's own
    usage line. The union of the two is the surface, so a `choices=CHECKS` indirection that the
    AST cannot resolve is still measured, and a file that cannot even reach argparse is caught.
  * `gb` surface — parse `bin/gb`'s AST for its `COMMANDS` and `HANDLERS` dicts, the `choices=`
    on each command's Args dataclass, and every `"gb-*.py"` literal inside each `cmd_*` handler.
    That is the ONLY thing that makes a producer reachable by name, and it is read from source,
    not from `gb capabilities --json`, because capabilities lists VERBS and the question here is
    about the ACTIONS under them (`gb templates` is registered; `gb templates deploy` is not).
  * Demand — how many OTHER files in the tree have to reach a producer themselves because no
    verb reaches it for them: `importers` (a file that dynamically `importlib`s it, the exact
    shape of the six hand-rolled copies) and `callers` (a file that names its path at all, i.e.
    subprocesses it). Plus `docs` (root `*.md` telling a reader to run `bin/gb-x.py` directly —
    a document that has to route around the CLI is a gap with a witness), `tick` (a scheduled
    script runs it, so it is load-bearing and still unnameable) and `public` (it ships to
    strangers through `gb-export-public.py`).
  * Dispatch — the REVERSE walk, added 2026-09-12 because everything above starts from a FILE
    in `bin/` and is therefore structurally incapable of seeing a verb pointing at a file that
    is not there. For every verb in `COMMANDS` (the same table `gb capabilities --json` renders)
    take `HANDLERS`' handler, close over the module-level functions it calls and the
    module-level constants they reference, and collect every `gb-*.py` / `gb-*.sh` literal —
    attributing nothing unless something in that closure actually SPAWNS. Then ask two questions
    of each producer named: is the file on disk, and is it in `bin/gb-export-public.py`'s
    allowlist. See `verb_dispatch` for why the mapping is parsed rather than guessed —
    `gb spend` runs `gb-spend-ledger.py` and `gb daily` runs `gb-daily.sh`.

SEVEN GAP KINDS, in the order they matter:

    absent        a verb dispatches a producer that is NOT on disk. Ranked above `broken`
                  because it fails in CPython's argv handling — `can't open file` — before a
                  line of this project's code runs.
    broken        a producer that cannot run at all. Worse than unreachable: a scheduled tick or
                  a `gb` verb calls it and gets an ImportError. MEASURED HERE, not hypothetical.
    unshipped     a verb dispatches a producer that exists HERE and is not in the export
                  allowlist. It works for the author and hands a stranger `can't open file`.
    truncated     `gb` reaches the producer but exposes FEWER actions than it has. The worst
                  kind to miss, because the verb looks present and the CLI says `invalid choice`.
    unreachable   the producer has a CLI and nothing in `gb` names it.
    tick-only     only a scheduled shell script runs it; an operator cannot invoke it by name.
    internal-only `gb` runs it as a side effect of another verb (a doctor scope, the collection
                  fleet) but never by name.

A producer with no argument parser at all is a LIBRARY, not a gap, and is not reported.

WHY THE REVERSE DIRECTION EXISTS, since it was bought expensively. The public export was
validated by running `gb <verb> --help` for all 52 verbs the CLI carried that day, and reported
"0 dead verbs". That probe could not fail: `--help` is answered by `gb`'s own argparse and
NEVER spawns the producer.
Invoking the verbs for real exposed SEVEN — `bot`, `goldens`, `spend`, `feeds`, `github`,
`links`, `sources` — each emitting a raw `can't open file .../bin/gb-bot.py`. This audit had
reported the baseline clean throughout, because it only ever walked PRODUCER -> is there a verb.
The `shipping` field on the report is the other half of that lesson: when the allowlist cannot
be read, the count of unshipped verbs is reported as UNCHECKED, never as zero.

NOT A DUPLICATE OF g24. `g24-cli-contract` in `gb-surface-gate.py` checks `gb` against ITSELF —
every registered command has a handler, every canonical verb still exists. It cannot see a
producer, so it passes with a full board while `gb templates deploy` is unreachable and
`gb-context-archive.py` is dead. This checks `gb` against the PRODUCERS, in both directions.
Different axis — which is also why a verb whose handler cannot be resolved is REPORTED here
(`unresolved`) and not graded: that wiring is g24's to own, and two owners means neither does.

THE RATCHET. `audit` compares the measured gap set against `dogfood-baseline.json`. A gap that
DISAPPEARS shrinks the baseline automatically — closing a gap must never need a ceremony. A gap
that APPEARS is exit 1 and is NOT recorded; growing the baseline requires
`--accept <who> --why <reason>`, which is written into the artifact with a timestamp. So "we
added a capability and skipped the verb" cannot become the new normal silently.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import json
import pathlib
import re
import sys
import tempfile
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gbargs  # noqa: E402
import gbtypes  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE_NAME = "dogfood-baseline.json"

PRODUCER_RE = re.compile(r"^gb-[a-z0-9-]+\.py$")
PRODUCER_IN_TEXT = re.compile(r"gb-[a-z0-9-]+\.py")
USAGE_CHOICES_RE = re.compile(r"\{([a-z0-9_,-]+)\}")

# The REVERSE direction's alphabet. `PRODUCER_RE` is `.py`-only because the forward walk starts
# from `bin/gb-*.py`; the reverse walk starts from a VERB, and `gb daily` dispatches
# `gb-daily.sh`. A `.sh` producer left out of the export is the same defect as a `.py` one.
SCRIPT_RE = re.compile(r"^gb-[a-z0-9-]+\.(?:py|sh)$")

# What "this handler starts a process" looks like in `bin/gb`, by call NAME. Load-bearing, and
# measured: `cmd_capabilities` REFERENCES the `SUBSYSTEMS` table to print each scope's reason,
# so a closure that ignored spawning attributed EIGHT producers to `gb capabilities`, which
# runs none of them. Requiring a spawn somewhere in the closure drops exactly that one verb to
# zero and changes no other row — re-measured 2026-09-11 against all 54 verbs, after
# `skill-coverage` and `roadmap` landed: still exactly one row.
SPAWN_CALLS = frozenset(
    {
        "run",
        "run_child",
        "run_producer",
        "_passthrough",
        "passthrough",
        "Popen",
        "call",
        "check_call",
        "check_output",
    }
)

# `--selftest` is a property proof, not an operator action; a producer that has one is not
# "missing a verb" for it. `selftest` also appears as a literal choice in two producers
# (gb-github.py, gb-x.py), which is why it is filtered by NAME rather than by shape.
NON_ACTIONS = frozenset({"selftest"})

# What a gap costs, before demand. Ordered deliberately: a verb that cannot run outranks a
# capability nobody can name, and a half-exposed verb outranks a wholly absent one because the
# CLI actively lies about it.
#
# The two REVERSE kinds sit above their forward neighbours on purpose. `absent` outranks
# `broken` because a broken producer at least EXISTS — it fails inside Python with a real
# traceback — whereas a verb whose producer is not on disk fails in CPython's ARGV handling,
# before any of this tool's code runs, with `can't open file`. `unshipped` outranks `truncated`
# because a truncated verb refuses an action with argparse's own `invalid choice`, while an
# unshipped verb works perfectly for the author and hands a stranger that same raw
# `can't open file`. Measured 2026-09-12: SEVEN verbs shipped in exactly that state.
KIND_WEIGHT: Dict[str, int] = {
    "absent": 120,
    "broken": 100,
    "unshipped": 80,
    "truncated": 60,
    "unreachable": 40,
    "tick-only": 20,
    "internal-only": 10,
}

# Demand multipliers. `importers` is weighted hardest because it is literally the hand-rolled
# shape the operator objected to: a file that dynamically imports a producer is re-implementing
# a verb inline.
SIGNAL_WEIGHT: Dict[str, int] = {
    "importers": 12,
    "callers": 4,
    "docs": 2,
    "tick": 8,
    "public": 6,
}


# ---------------------------------------------------------------------------------------------
# Measured shapes
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Surface:
    """One producer's measured CLI surface. `runnable` is a fact about this machine, now."""

    name: str
    actions: Tuple[str, ...]
    has_cli: bool
    runnable: bool
    error: str

    def as_json(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "actions": list(self.actions),
            "has_cli": self.has_cli,
            "runnable": self.runnable,
            "error": self.error,
        }


@dataclasses.dataclass(frozen=True)
class Verb:
    """One `gb` command as `bin/gb` declares it: the actions it accepts and what it runs."""

    name: str
    actions: Tuple[str, ...]
    producers: Tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Dispatch:
    """One verb's RESOLVED target: the handler `bin/gb` routes it to, and every producer script
    that handler can reach. The reverse of `Verb.producers`, which answers "who reaches this
    producer"; this answers "what does this verb need to exist"."""

    verb: str
    handler: str
    producers: Tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Signals:
    """Why a gap matters, in counts that came from the tree."""

    importers: int = 0
    callers: int = 0
    docs: int = 0
    tick: int = 0
    public: int = 0

    def score(self) -> int:
        return sum(SIGNAL_WEIGHT[k] * v for k, v in dataclasses.asdict(self).items())

    def as_json(self) -> Dict[str, int]:
        return dataclasses.asdict(self)

    def brief(self) -> str:
        parts = [f"{k}={v}" for k, v in dataclasses.asdict(self).items() if v]
        return " ".join(parts) if parts else "-"


@dataclasses.dataclass(frozen=True)
class Gap:
    """One capability that exists and is not reachable by name through `gb`."""

    kind: str
    target: str
    detail: str
    evidence: Tuple[str, ...]
    signals: Signals

    @property
    def key(self) -> str:
        """Stable identity across runs. The baseline is keyed on this and nothing else."""
        return f"{self.kind}:{self.target}"

    @property
    def score(self) -> int:
        return KIND_WEIGHT.get(self.kind, 0) + self.signals.score()

    def as_json(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "target": self.target,
            "detail": self.detail,
            "score": self.score,
            "signals": self.signals.as_json(),
            "evidence": list(self.evidence),
        }


@dataclasses.dataclass(frozen=True)
class Report:
    """The whole audit. `gaps` is sorted worst-first and that order is part of the contract.

    `dispatches`, `unresolved` and `shipping` carry the REVERSE walk. `shipping` is a REASON
    string, empty when the export allowlist was actually read: an unshipped count of zero has to
    be distinguishable from "nobody looked", which is the whole reason the seven dead verbs
    survived a clean-looking validation.
    """

    root: str
    producers: int
    libraries: int
    verbs: int
    gaps: Tuple[Gap, ...]
    dispatches: Tuple[Dispatch, ...] = ()
    unresolved: Tuple[str, ...] = ()
    shipping: str = ""

    @property
    def dispatch_edges(self) -> int:
        """verb -> producer pairs checked. The denominator for the reverse direction."""
        return sum(len(d.producers) for d in self.dispatches)

    def as_json(self) -> Dict[str, Any]:
        return {
            "schema": "gb-dogfood/1",
            "root": self.root,
            "producers": self.producers,
            "libraries": self.libraries,
            "verbs": self.verbs,
            "gap_count": len(self.gaps),
            "gaps": [g.as_json() for g in self.gaps],
            "dispatch": {d.verb: list(d.producers) for d in self.dispatches},
            "dispatch_edges": self.dispatch_edges,
            "dispatch_unresolved": list(self.unresolved),
            "shipping_checked": not self.shipping,
            "shipping_note": self.shipping,
        }


# ---------------------------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------------------------
def parse_file(path: pathlib.Path) -> Optional[ast.Module]:
    """Parse, or return None. An unparseable file is a measurement (`broken`), not a crash."""
    try:
        return ast.parse(gbtypes.read_text_capped(path) or "")
    except (OSError, SyntaxError, ValueError):
        return None


def _str_seq(node: ast.AST) -> Tuple[str, ...]:
    """A literal tuple/list/set of strings, or empty. `choices=CHECKS` is deliberately empty
    here — the `--help` probe is what resolves an indirection, not a guess about a Name."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return tuple(
            e.value
            for e in node.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        )
    return ()


def _kw(node: ast.Call, name: str) -> Optional[ast.AST]:
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_true(node: Optional[ast.AST]) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return str(getattr(func, "id", ""))


def positional_choices(tree: ast.AST) -> Tuple[str, ...]:
    """Every action a parser accepts as its leading POSITIONAL, from source.

    Three idioms are live in this tree and all three are read:
      * `ap.add_argument("verb", nargs="?", choices=("rank", "gaps"))`  — gb-demand.py:670
      * `gbargs.arg(positional=True, choices=("roster", ...))`          — gb-fleet.py:289
      * `sub.add_parser("collect", ...)`                               — gb-sources.py
    A `--flag` with choices is NOT an action and is excluded by checking the first argument is
    positional (gb-store.py's `--engine` has choices and must not be read as a verb).
    """
    found: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name == "add_parser":
            if node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str):
                    found.append(value)
            continue
        choices = _kw(node, "choices")
        if choices is None:
            continue
        if name == "add_argument":
            first = node.args[0] if node.args else None
            positional = (
                isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and not first.value.startswith("-")
            )
        elif name == "arg":
            positional = _is_true(_kw(node, "positional"))
        else:
            positional = False
        if positional:
            found.extend(_str_seq(choices))
    return tuple(dict.fromkeys(found))


def has_parser(tree: ast.AST) -> bool:
    """Does this file have its own CLI? A library (no parser) is not a missing verb."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in (
            "ArgumentParser",
            "build_parser",
            "parse_sub",
        ):
            return True
        if isinstance(node, ast.Call) and _call_name(node) == "parse":
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and getattr(func.value, "id", "") == "gbargs"
            ):
                return True
    return False


def help_choices(text: str) -> Tuple[str, ...]:
    """Read argparse's own `{a,b,c}` group out of a `--help` rendering.

    This is the half of the surface the AST cannot see: `choices=CHECKS` (gb-surface-gate.py) is
    a Name at parse time and a concrete list at runtime.
    """
    found: List[str] = []
    for group in USAGE_CHOICES_RE.findall(text):
        found.extend(p for p in group.split(",") if p)
    return tuple(dict.fromkeys(found))


# ---------------------------------------------------------------------------------------------
# Producer + gb surfaces
# ---------------------------------------------------------------------------------------------
def producer_surface(
    path: pathlib.Path, *, probe: bool, timeout_s: float = 25.0
) -> Surface:
    """The union of what the source declares and what `--help` actually prints."""
    tree = parse_file(path)
    if tree is None:
        return Surface(path.name, (), True, False, "does not parse")

    actions = list(positional_choices(tree))
    cli = has_parser(tree)
    runnable, error = True, ""
    if probe:
        proc = gbtypes.run(
            [sys.executable, str(path), "--help"],
            timeout_s=timeout_s,
            cwd=path.parents[1],
        )
        if proc.code != 0 or proc.timed_out:
            runnable = False
            tail = [ln for ln in (proc.err or proc.out).splitlines() if ln.strip()]
            error = (
                "--help timed out"
                if proc.timed_out
                else (tail[-1][:200] if tail else f"--help exited {proc.code}")
            )
        else:
            for c in help_choices(proc.out):
                if c not in actions:
                    actions.append(c)
            cli = cli or bool(proc.out.strip())
    return Surface(
        path.name,
        tuple(a for a in actions if a not in NON_ACTIONS),
        cli,
        runnable,
        error,
    )


def _docstring_ids(tree: ast.AST) -> FrozenSet[int]:
    """The `id()` of every docstring Constant in a tree, so prose can be excluded from code.

    Load-bearing, not tidiness. `bin/gb`'s own module docstring names `gb-pull-inventory.py` and
    `gb-surface-gate.py` while explaining why the CLI exists, and `gb-fleet.py`'s docstring names
    `gb-pull-inventory.py` twice. Counting prose as an invocation inflates every demand signal
    and, worse, regrades a wholly `unreachable` producer as `internal-only` because `bin/gb`
    merely MENTIONS it. A reachability claim must rest on code.
    """
    out = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            out.add(id(first.value))
    return frozenset(out)


def named_producers(
    node: ast.AST, skip: FrozenSet[int] = frozenset()
) -> Tuple[str, ...]:
    """Every `gb-*.py` named as a CODE string in this subtree, in source order, deduped."""
    return tuple(
        dict.fromkeys(
            c.value
            for c in ast.walk(node)
            if isinstance(c, ast.Constant)
            and isinstance(c.value, str)
            and PRODUCER_RE.match(c.value)
            and id(c) not in skip
        )
    )


def dict_tables(tree: ast.AST, *wanted: str) -> Dict[str, Dict[str, str]]:
    """The named module-level `{"key": Identifier}` tables, as `{table: {key: identifier}}`.

    BOTH assignment forms, because the real file uses the annotated one: `bin/gb` declares
    `COMMANDS: Dict[str, type] = {...}`, which is an `ast.AnnAssign` and NOT an `ast.Assign`.
    Measured 2026-09-11: reading only `Assign` found 0 of 27 verbs and silently regraded every
    passthrough producer as internal-only. The fixture below carries both spellings so that
    cannot regress.

    Shared by both directions deliberately. The forward walk and the reverse walk MUST agree on
    what the verb set is; two copies of this loop would let them drift apart silently.
    """
    out: Dict[str, Dict[str, str]] = {name: {} for name in wanted}
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            names = {node.target.id} if isinstance(node.target, ast.Name) else set()
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        else:
            continue
        hit = names & set(wanted)
        if not hit or not isinstance(node.value, ast.Dict):
            continue
        into = out[sorted(hit)[0]]
        for k, v in zip(node.value.keys, node.value.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                ident = getattr(v, "id", "") or getattr(v, "attr", "")
                if ident:
                    into[k.value] = str(ident)
    return out


def gb_surface(gb_path: pathlib.Path) -> Tuple[Dict[str, Verb], Tuple[str, ...]]:
    """`bin/gb`'s own surface, from its AST: the verbs, and every producer it names anywhere.

    Two returns because reachability has two grades. A producer named inside a `cmd_*` handler is
    reachable BY NAME. A producer named anywhere else in `bin/gb` — a `SUBSYSTEMS` entry, the
    collection fleet inside the daily tick — runs, but only as a side effect of a different verb.
    """
    tree = parse_file(gb_path)
    if tree is None:
        return {}, ()

    docs = _docstring_ids(tree)
    tables = dict_tables(tree, "COMMANDS", "HANDLERS")
    commands, handlers = tables["COMMANDS"], tables["HANDLERS"]

    class_actions: Dict[str, Tuple[str, ...]] = {}
    func_producers: Dict[str, Tuple[str, ...]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_actions[node.name] = positional_choices(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_producers[node.name] = named_producers(node, docs)

    verbs: Dict[str, Verb] = {}
    for verb, cls in commands.items():
        verbs[verb] = Verb(
            verb,
            tuple(a for a in class_actions.get(cls, ()) if a not in NON_ACTIONS),
            func_producers.get(handlers.get(verb, ""), ()),
        )

    return verbs, named_producers(tree, docs)


# ---------------------------------------------------------------------------------------------
# The reverse direction: VERB -> producer -> does it exist, and does it SHIP
# ---------------------------------------------------------------------------------------------
def _scripts_in(node: ast.AST, skip: FrozenSet[int]) -> Tuple[str, ...]:
    """Every `gb-*.py` or `gb-*.sh` named as a CODE string in this subtree, source order."""
    return tuple(
        dict.fromkeys(
            c.value
            for c in ast.walk(node)
            if isinstance(c, ast.Constant)
            and isinstance(c.value, str)
            and SCRIPT_RE.match(c.value)
            and id(c) not in skip
        )
    )


def verb_dispatch(
    gb_path: pathlib.Path,
) -> Tuple[Tuple[Dispatch, ...], Tuple[str, ...]]:
    """(what each verb dispatches, the verbs whose handler could not be resolved at all).

    THE METHOD, because `gb-<verb>.py` is a guess and the guess is wrong for 15 of this tree's
    54 verbs — measured 2026-09-11 as "the resolved set contains no file named `gb-<verb>.py`".
    Six are plain renames (`gb spend` -> `gb-spend-ledger.py`, `gb deployment` ->
    `gb-deployment-audit.py`, `gb gates` -> `gb-capabilities.py`, `gb matrix` ->
    `gb-feature-matrix.py`, `gb record` -> `gb-record-inventory.py`, `gb roadmap` ->
    `gb-gaps.py`). Three run a producer named after something else entirely (`gb health` and
    `gb why` both run `gb-surface-gate.py`; `gb export` runs `gb-export-public.py`). One is not
    even Python (`gb daily` -> `gb-daily.sh`). Five fan out to several producers at once
    (`gb validate` alone reaches thirteen). 6 + 3 + 1 + 5 = 15:

      1. The verb set is `COMMANDS`, read from `bin/gb`'s AST — the same table
         `gb capabilities --json` builds its `commands` map from, so the two cannot disagree.
      2. `HANDLERS` maps each verb to a handler IDENTIFIER.
      3. From that handler, take the TRANSITIVE CLOSURE over module-level functions it calls,
         accumulating (a) every script literal in each function's own body and (b) every script
         literal in each module-level constant those functions reference by name — that second
         arm is what resolves `gb setup` through `SETUP_PRODUCERS` and `gb doctor` through the
         `SUBSYSTEMS` table, neither of which names a script inside the handler.
      4. Attribute nothing unless some function in that closure SPAWNS (see `SPAWN_CALLS`). A
         verb that starts no process cannot die on a missing file, and without this arm
         `gb capabilities` — which reads `SUBSYSTEMS` only to print each scope's reason —
         falsely claims eight producers.

    KNOWN LIMIT, stated rather than hidden: step 3 is name-based, so a spawning handler that
    merely MENTIONS a script it does not run would over-attribute. That direction is safe — it
    can only demand that a named producer exist and ship — but it is not free of false rows, and
    the `unresolved` return is the honest other half: a verb in `COMMANDS` with no `HANDLERS`
    entry, or one pointing at a function this module cannot find, is reported, never guessed at.
    """
    tree = parse_file(gb_path)
    if tree is None:
        return (), ()

    docs = _docstring_ids(tree)
    tables = dict_tables(tree, "COMMANDS", "HANDLERS")
    commands, handlers = tables["COMMANDS"], tables["HANDLERS"]

    # Module level only. A nested helper is reached through its enclosing function's body, which
    # `ast.walk` already covers; hoisting it here would let an unrelated verb borrow its scripts.
    funcs: Dict[str, ast.AST] = {}
    consts: Dict[str, Tuple[str, ...]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            consts[node.target.id] = (
                _scripts_in(node.value, docs) if node.value is not None else ()
            )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = _scripts_in(node.value, docs)

    scripts: Dict[str, Tuple[str, ...]] = {}
    calls: Dict[str, Tuple[str, ...]] = {}
    refs: Dict[str, Tuple[str, ...]] = {}
    spawns: Dict[str, bool] = {}
    for name, fn in funcs.items():
        called: List[str] = []
        named: List[str] = []
        spawn = False
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call):
                ident = getattr(sub.func, "id", "") or getattr(sub.func, "attr", "")
                spawn = spawn or ident in SPAWN_CALLS
                called.append(str(ident))
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                named.append(sub.id)
        scripts[name] = _scripts_in(fn, docs)
        calls[name] = tuple(dict.fromkeys(c for c in called if c in funcs))
        refs[name] = tuple(dict.fromkeys(n for n in named if n in consts))
        spawns[name] = spawn

    def closure(entry: str) -> Tuple[str, ...]:
        seen: Set[str] = set()
        found: Dict[str, None] = {}
        spawn = False
        queue = [entry]
        while queue:
            cur = queue.pop(0)
            if cur in seen or cur not in funcs:
                continue
            seen.add(cur)
            spawn = spawn or spawns[cur]
            for script in scripts[cur]:
                found[script] = None
            for const in refs[cur]:
                for script in consts[const]:
                    found[script] = None
            queue.extend(calls[cur])
        return tuple(found) if spawn else ()

    dispatches: List[Dispatch] = []
    unresolved: List[str] = []
    for verb in sorted(commands):
        handler = handlers.get(verb, "")
        if not handler:
            unresolved.append(f"{verb} (no HANDLERS entry)")
            continue
        if handler not in funcs:
            unresolved.append(f"{verb} -> {handler} (not a module-level function)")
            continue
        dispatches.append(Dispatch(verb, handler, closure(handler)))

    return tuple(dispatches), tuple(unresolved)


def shipped_scripts(export_path: pathlib.Path) -> Tuple[FrozenSet[str], str]:
    """(every `bin/` entry the exporter will publish, why the allowlist could not be read).

    Read STRUCTURALLY — the `_BIN` tuple's first elements plus each explicit `Member(src, ...)` —
    and NOT by scanning the file for producer-shaped strings. The difference is load-bearing:
    `gb-export-public.py` also carries an `EXCLUSIONS` table naming the entries it deliberately
    does NOT publish, so a text scan would report the excluded ones as shipped. It is also why
    the existing `public` demand signal cannot stand in for this check.

    The reason string is the whole point of the second return. A clone has no exporter, and
    "0 unshipped" from a tree that was never examined is exactly the shape of the falsified
    validation this direction exists to prevent.
    """
    if not export_path.is_file():
        return frozenset(), f"{export_path.name} is absent — the allowlist was not read"
    tree = parse_file(export_path)
    if tree is None:
        return (
            frozenset(),
            f"{export_path.name} does not parse — the allowlist was not read",
        )

    names: Set[str] = set()
    saw_bin = saw_allowlist = False
    for node in tree.body:
        targets: List[str] = []
        value: Optional[ast.expr] = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets, value = [node.target.id], node.value
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        if not targets or value is None:
            continue
        if "_BIN" in targets and isinstance(value, (ast.Tuple, ast.List)):
            saw_bin = True
            for elt in value.elts:
                if isinstance(elt, (ast.Tuple, ast.List)) and elt.elts:
                    first = elt.elts[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        names.add(first.value)
        if "ALLOWLIST" in targets:
            saw_allowlist = True
            for sub in ast.walk(value):
                if (
                    isinstance(sub, ast.Call)
                    and getattr(sub.func, "id", "") == "Member"
                    and sub.args
                    and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)
                ):
                    names.add(sub.args[0].value.rsplit("/", 1)[-1])
    if not saw_bin or not saw_allowlist:
        missing = " and ".join(
            n for n, ok in (("_BIN", saw_bin), ("ALLOWLIST", saw_allowlist)) if not ok
        )
        return (
            frozenset(),
            f"{export_path.name} has no {missing} — the allowlist was not read",
        )
    return frozenset(names), ""


# ---------------------------------------------------------------------------------------------
# Demand
# ---------------------------------------------------------------------------------------------
def measure_signals(root: pathlib.Path, names: Sequence[str]) -> Dict[str, Signals]:
    """Count, per producer, everything in the tree that has to reach it without a verb.

    `importers` is the load-bearing one: a `.py` file that holds `importlib` AND names the
    producer as a CODE string is re-implementing a verb inline — the exact shape of the six
    hand-rolled copies this producer exists to prevent.

    Two deliberate exclusions, both to stop a signal double-counting itself:
      * Python files are read through the AST, so a producer named only in a DOCSTRING is not
        counted as a caller. Measured: a regex over raw text scored `gb-pull-inventory.py` at
        8 importers / 11 callers; the AST says 5 / 6, and the difference was entirely prose
        explaining the defect.
      * `gb-export-public.py` is skipped for callers/importers. It names every producer because
        it IS the publish manifest, not because it invokes them — that fact is already carried
        by the separate `public` signal.
    """
    bin_dir = root / "bin"
    counts: Dict[str, Dict[str, int]] = {
        n: {"importers": 0, "callers": 0, "docs": 0, "tick": 0, "public": 0}
        for n in names
    }
    wanted = set(names)
    MANIFEST = "gb-export-public.py"

    for path in sorted(bin_dir.glob("*")):
        if not path.is_file() or path.name == "gb":
            continue
        body = gbtypes.read_text_capped(path)
        if not body:
            continue
        tick = path.suffix == ".sh" and path.stem in ("gb-daily", "gb-weekly")
        if path.suffix == ".py":
            tree = parse_file(path)
            hits = named_producers(tree, _docstring_ids(tree)) if tree else ()
        else:
            hits = tuple(dict.fromkeys(PRODUCER_IN_TEXT.findall(body)))
        for hit in hits:
            if hit not in wanted or hit == path.name:
                continue
            row = counts[hit]
            if path.name == MANIFEST:
                row["public"] = 1
                continue
            row["callers"] += 1
            if "importlib" in body:
                row["importers"] += 1
            if tick:
                row["tick"] = 1

    for path in sorted(root.glob("*.md")):
        body = gbtypes.read_text_capped(path)
        for hit in PRODUCER_IN_TEXT.findall(body or ""):
            if hit in wanted:
                counts[hit]["docs"] += 1

    return {n: Signals(**counts[n]) for n in names}


# ---------------------------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------------------------
def audit(root: pathlib.Path, *, probe: bool = True) -> Report:
    """Diff what the producers can do against what `bin/gb` can reach, and rank the difference."""
    bin_dir = root / "bin"
    paths = sorted(p for p in bin_dir.glob("gb-*.py") if p.is_file())
    surfaces = {p.name: producer_surface(p, probe=probe) for p in paths}
    verbs, named_anywhere = gb_surface(bin_dir / "gb")

    by_verb: Dict[str, List[str]] = {}
    for verb in verbs.values():
        for prod in verb.producers:
            by_verb.setdefault(prod, []).append(verb.name)

    signals = measure_signals(root, list(surfaces))
    gaps: List[Gap] = []
    libraries = 0

    for name, surface in sorted(surfaces.items()):
        sig = signals[name]
        reached_by = by_verb.get(name, [])
        internal = name in named_anywhere and not reached_by

        if not surface.has_cli:
            libraries += 1
            continue

        if not surface.runnable:
            where = (
                f"`gb {reached_by[0]}` runs it"
                if reached_by
                else (
                    "a scheduled tick runs it"
                    if sig.tick
                    else "nothing in gb reaches it"
                )
            )
            gaps.append(
                Gap(
                    "broken",
                    name,
                    f"cannot run: {surface.error}",
                    (f"bin/{name} --help -> nonzero; {where}",),
                    sig,
                )
            )
            continue

        if reached_by:
            verb = verbs[reached_by[0]]
            missing = [a for a in surface.actions if a not in verb.actions]
            for action in missing:
                gaps.append(
                    Gap(
                        "truncated",
                        f"{verb.name}:{action}",
                        f"`gb {verb.name} {action}` is refused; `bin/{name} {action}` works",
                        (
                            f"bin/{name} offers {list(surface.actions)}",
                            f"bin/gb {verb.name} accepts {list(verb.actions)}",
                        ),
                        sig,
                    )
                )
            continue

        if internal:
            gaps.append(
                Gap(
                    "internal-only",
                    name,
                    "gb runs it as a side effect of another verb, never by name",
                    (f"bin/gb names {name} outside every cmd_* handler",),
                    sig,
                )
            )
        elif sig.tick:
            gaps.append(
                Gap(
                    "tick-only",
                    name,
                    "only a scheduled shell script runs it; no verb invokes it",
                    (
                        f"bin/gb-daily.sh or bin/gb-weekly.sh runs {name}; bin/gb does not",
                    ),
                    sig,
                )
            )
        else:
            actions = (
                f" (actions: {', '.join(surface.actions)})" if surface.actions else ""
            )
            gaps.append(
                Gap(
                    "unreachable",
                    name,
                    f"has a CLI and nothing in gb names it{actions}",
                    (f"no `gb-*.py` literal for {name} anywhere in bin/gb",),
                    sig,
                )
            )

    # --- the REVERSE direction. Everything above walks PRODUCER -> is there a verb? That walk
    # is structurally blind to a verb pointing at a producer which is not there, or which is
    # there and does not travel: it only ever looks at files that exist in `bin/`. Measured
    # 2026-09-12, and the blindness shipped — SEVEN verbs (`bot`, `goldens`, `spend`, `feeds`,
    # `github`, `links`, `sources`) were advertised by `gb capabilities --json` with no producer
    # in the public export, each answering a stranger with a raw CPython `can't open file`,
    # while this audit reported the baseline clean.
    dispatches, unresolved = verb_dispatch(bin_dir / "gb")
    shipped, shipping = shipped_scripts(bin_dir / "gb-export-public.py")
    for dispatch in dispatches:
        for prod in dispatch.producers:
            sig = signals.get(prod, Signals())
            if not (bin_dir / prod).is_file():
                gaps.append(
                    Gap(
                        "absent",
                        f"{dispatch.verb}:{prod}",
                        f"`gb {dispatch.verb}` dispatches bin/{prod} and no such file exists",
                        (
                            f"bin/gb {dispatch.handler}() names {prod}",
                            f"{bin_dir / prod} is not on disk",
                        ),
                        sig,
                    )
                )
            elif not shipping and prod not in shipped:
                gaps.append(
                    Gap(
                        "unshipped",
                        f"{dispatch.verb}:{prod}",
                        f"`gb {dispatch.verb}` works here and dies in a clone: bin/{prod} is not exported",
                        (
                            f"bin/gb {dispatch.handler}() names {prod}",
                            "bin/gb-export-public.py's allowlist does not carry it",
                            f"a clone answers: can't open file '.../bin/{prod}'",
                        ),
                        sig,
                    )
                )

    gaps.sort(key=lambda g: (-g.score, g.kind, g.target))
    return Report(
        str(root),
        len(surfaces) - libraries,
        libraries,
        len(verbs),
        tuple(gaps),
        dispatches,
        unresolved,
        shipping,
    )


# ---------------------------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------------------------
def load_baseline(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    data = gbtypes.read_json_capped(path)
    return data if isinstance(data, dict) else None


def baseline_payload(
    report: Report, *, accepted_by: str, why: str, previous: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """The artifact. Acceptances ACCUMULATE: who let a gap in, and why, is never overwritten."""
    history: List[Dict[str, Any]] = []
    if previous and isinstance(previous.get("acceptances"), list):
        history = [h for h in previous["acceptances"] if isinstance(h, dict)]
    if accepted_by:
        history.append(
            {
                "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "by": accepted_by,
                "why": why,
                "keys": sorted(g.key for g in report.gaps),
            }
        )
    return {
        "schema": "gb-dogfood-baseline/1",
        "recorded_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gap_count": len(report.gaps),
        "gaps": {g.key: {"score": g.score, "detail": g.detail} for g in report.gaps},
        "acceptances": history,
    }


@dataclasses.dataclass(frozen=True)
class Ratchet:
    """The verdict of comparing a report against a baseline.

    `new` and `accepted` are separate on purpose. A new gap is a FINDING (exit 1) only while it
    is unaccepted; once `--accept <who> --why <reason>` has written it into the record, the same
    gap set is the new floor and the exit code is 0. Collapsing the two would make `--accept`
    unusable in a pre-commit hook: it would record the decision and still fail the run.
    """

    new: Tuple[str, ...]
    closed: Tuple[str, ...]
    first_run: bool
    wrote: bool
    accepted: bool = False

    @property
    def code(self) -> int:
        return 1 if (self.new and not self.accepted) else 0


def apply_ratchet(
    report: Report,
    baseline_path: pathlib.Path,
    *,
    accepted_by: str = "",
    why: str = "",
    write: bool = True,
) -> Ratchet:
    """Compare, then write ONLY when the baseline may legally change.

    Shrinking is automatic: closing a gap must not require a ceremony. Growing is refused unless
    `--accept` names a person and `--why` gives a reason, both recorded in the artifact.
    """
    previous = load_baseline(baseline_path)
    current = {g.key for g in report.gaps}
    if previous is None:
        wrote = False
        if write:
            gbtypes.atomic_write_json(
                baseline_path,
                baseline_payload(
                    report, accepted_by=accepted_by, why=why, previous=None
                ),
            )
            wrote = True
        return Ratchet((), (), True, wrote)

    recorded = set(previous.get("gaps") or {})
    new = tuple(sorted(current - recorded))
    closed = tuple(sorted(recorded - current))

    if new and not accepted_by:
        return Ratchet(new, closed, False, False)

    wrote = False
    if write and (new or closed):
        gbtypes.atomic_write_json(
            baseline_path,
            baseline_payload(
                report, accepted_by=accepted_by, why=why, previous=previous
            ),
        )
        wrote = True
    return Ratchet(new, closed, False, wrote, accepted=bool(accepted_by))


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------
def render(report: Report, ratchet: Ratchet, baseline_path: pathlib.Path) -> None:
    print(
        f"  {report.producers} producers with a CLI, {report.libraries} libraries, "
        f"{report.verbs} gb verbs -> {len(report.gaps)} gaps"
    )
    # The reverse direction's denominator, printed whether or not it found anything. A count of
    # zero is only meaningful next to the number of edges that were actually walked.
    if report.shipping:
        print(
            f"  reverse: {report.dispatch_edges} verb->producer edges checked for EXISTENCE "
            f"only — SHIPPING NOT CHECKED ({report.shipping})"
        )
    else:
        print(
            f"  reverse: {report.dispatch_edges} verb->producer edges, each checked against "
            f"bin/gb-export-public.py's allowlist"
        )
    if report.unresolved:
        print(
            f"  UNRESOLVED {len(report.unresolved)} verb(s) — dispatch target unknown, so "
            f"NOT checked: {', '.join(report.unresolved[:4])}"
        )
    if not report.gaps:
        print("  no gaps: every producer capability is reachable by name through gb")
    else:
        print()
        print(f"  {'SCORE':>5}  {'KIND':<14}{'TARGET':<28}{'DEMAND':<34}WHY")
        for g in report.gaps:
            print(
                f"  {g.score:>5}  {g.kind:<14}{g.target[:27]:<28}"
                f"{g.signals.brief()[:33]:<34}{g.detail[:78]}"
            )
    print()
    if ratchet.first_run:
        print(f"  baseline recorded at {baseline_path.name} — this run is the floor")
    else:
        if ratchet.closed:
            print(f"  CLOSED {len(ratchet.closed)}: {', '.join(ratchet.closed[:6])}")
        if ratchet.new and ratchet.accepted:
            print(f"  ACCEPTED {len(ratchet.new)} new gap(s) into the baseline:")
            for key in ratchet.new:
                print(f"    + {key}")
        elif ratchet.new:
            print(
                f"  NEW {len(ratchet.new)} gap(s) since the baseline — this is a FINDING:"
            )
            for key in ratchet.new:
                print(f"    + {key}")
            # The forward fix and the reverse fix are different edits in different files, and
            # "add the verb" is actively wrong advice for a verb that already exists.
            kinds = {k.split(":", 1)[0] for k in ratchet.new}
            if kinds & {"unshipped", "absent"}:
                print(
                    "  fix (unshipped): add bin/<producer> to `_BIN` in bin/gb-export-public.py\n"
                    "  fix (absent):    the verb names a producer that is not there — restore it\n"
                    "                   or unregister the verb in bin/gb"
                )
            if kinds - {"unshipped", "absent"}:
                print("  fix: add the verb.")
            print(
                "  If the gap is deliberate, record it:\n"
                "       bin/gb-dogfood.py audit --accept <who> --why '<reason>'"
            )
        if ratchet.wrote:
            print(f"  baseline rewritten: {baseline_path.name}")
        if not ratchet.new and not ratchet.closed:
            print("  baseline holds: no new gaps")
    print()
    print(
        "  THE RULE: a task that needs a capability gb cannot reach STOPS and adds the verb.\n"
        "  It does not hand-roll an inline script. See DOGFOOD.md."
    )


def render_baseline(baseline_path: pathlib.Path, as_json: bool) -> int:
    data = load_baseline(baseline_path)
    if data is None:
        print(
            f"gb-dogfood: no baseline at {baseline_path} — run `audit` first",
            file=sys.stderr,
        )
        return 3
    if as_json:
        print(json.dumps(data, indent=1))
        return 0
    print(f"  recorded_at {data.get('recorded_at')}  gaps {data.get('gap_count')}")
    for key, row in sorted((data.get("gaps") or {}).items()):
        print(f"    {key}  score={row.get('score')}  {str(row.get('detail'))[:70]}")
    for acc in data.get("acceptances") or []:
        print(f"  accepted {acc.get('at')} by {acc.get('by')}: {acc.get('why')}")
    return 0


# ---------------------------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------------------------
FIXTURE_GB = """\
import dataclasses
from typing import Optional
COMMANDS = {"alpha": AlphaArgs, "broken": BrokenArgs}
HANDLERS = {"alpha": cmd_alpha, "broken": cmd_broken}

@dataclasses.dataclass(frozen=True)
class AlphaArgs:
    action: str = arg(default="list", choices=("list", "show"), positional=True)

@dataclasses.dataclass(frozen=True)
class BrokenArgs:
    pass

SUBSYSTEMS = {"eps": (["gb-epsilon.py"], "reachable only as a doctor scope")}

def cmd_alpha(a):
    return run([str(BIN / "gb-alpha.py"), a.action])

def cmd_broken(a):
    return run([str(BIN / "gb-broken.py")])

def body():
    parse_sub(COMMANDS)
"""

FIXTURE_PRODUCER = """\
import argparse
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", choices=({choices}), default="{first}")
    ap.parse_args()
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
"""

FIXTURE_LIB = '''\
"""A library: no parser, so not a missing verb."""
def helper():
    return 1
'''

FIXTURE_BROKEN = """\
import argparse
from gblib import THIS_DOES_NOT_EXIST
def main():
    argparse.ArgumentParser().parse_args()
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
"""

FIXTURE_DAILY = """\
#!/usr/bin/env bash
"$PY" "$BIN/gb-gamma.py" collect
"""

# The REVERSE fixture: a miniature `bin/gb` built to exercise every arm of `verb_dispatch` at
# once, and kept SEPARATE from FIXTURE_GB on purpose — an `absent` gap outscores every forward
# kind, so folding these verbs into the shared fixture would silently rewrite the ranking the
# forward legs assert on.
#
#   alpha  literal in the handler, file present, allowlisted  -> no gap
#   omega  literal in the handler, file present, NOT allowlisted -> unshipped
#   zeta   literal in the handler, file ABSENT                -> absent
#   inert  names a producer through a table and NEVER spawns  -> attributes nothing
#   chain  handler spawns nothing itself; the helper it calls does -> transitive closure
#   table  names its producer only through a module-level constant -> the constant arm
#   orphan declared in COMMANDS with no HANDLERS entry        -> unresolved, never guessed
FIXTURE_DISPATCH_GB = """\
COMMANDS = {"alpha": A, "omega": O, "zeta": Z, "inert": I, "chain": C, "table": T,
            "orphan": R}
HANDLERS = {"alpha": cmd_alpha, "omega": cmd_omega, "zeta": cmd_zeta, "inert": cmd_inert,
            "chain": cmd_chain, "table": cmd_table}

SETUP_PRODUCERS = ("gb-table.py",)
SUBSYSTEMS = {"eps": (["gb-inert.py"], "printed, never run")}

def cmd_alpha(a):
    return run([str(BIN / "gb-alpha.py")])

def cmd_omega(a):
    return run([str(BIN / "gb-omega.py")])

def cmd_zeta(a):
    return run([str(BIN / "gb-zeta.py")])

def cmd_inert(a):
    return emit({n: why for n, (_a, why) in SUBSYSTEMS.items()})

def cmd_chain(a):
    return _chain_target(a)

def _chain_target(a):
    return run([str(BIN / "gb-chain.py")])

def cmd_table(a):
    for name in SETUP_PRODUCERS:
        run([str(BIN / name)])
    return 0
"""

# The exporter fixture. Both shapes the real one uses — a `_BIN` tuple of `(name, why)` pairs
# and explicit `Member(src, dest, why)` rows — plus an `EXCLUSIONS` table naming a producer it
# does NOT publish, which is the trap a text scan falls into. `{extra}` is the hole the positive
# control fills: the SAME fixture with `gb-omega.py` allowlisted must report no unshipped gap.
FIXTURE_EXPORT = '''\
"""An export manifest. Its docstring names gb-omega.py, which must not count as shipped."""
_BIN = (
    ("gb", "the CLI"),
    ("gb-alpha.py", "allowlisted"),
    ("gb-chain.py", "allowlisted"),
    ("gb-table.py", "allowlisted"),
    ("gb-inert.py", "allowlisted"),{extra}
)
ALLOWLIST = (
    Member("LICENSE", "LICENSE", "the licence"),
) + tuple(Member(f"bin/{{name}}", f"bin/{{name}}", why) for name, why in _BIN)
EXCLUSIONS = (
    ("gb-omega.py", "deliberately not published"),
)
'''


def _write_fixture(root: pathlib.Path, *, extra: Sequence[str] = ()) -> None:
    """Materialise a whole miniature repo. Real files, because the probe runs real processes —
    a fixture that only exercises the AST half would not prove the `broken` detector at all."""
    bin_dir = root / "bin"
    gbtypes.atomic_write_text(bin_dir / "gb", FIXTURE_GB)
    gbtypes.atomic_write_text(
        bin_dir / "gb-alpha.py",
        FIXTURE_PRODUCER.format(choices='"list", "show", "deploy"', first="list"),
    )
    gbtypes.atomic_write_text(
        bin_dir / "gb-beta.py",
        FIXTURE_PRODUCER.format(choices='"rank", "gaps"', first="rank"),
    )
    gbtypes.atomic_write_text(
        bin_dir / "gb-gamma.py",
        FIXTURE_PRODUCER.format(choices='"collect",', first="collect"),
    )
    gbtypes.atomic_write_text(
        bin_dir / "gb-epsilon.py",
        FIXTURE_PRODUCER.format(choices='"scan",', first="scan"),
    )
    gbtypes.atomic_write_text(bin_dir / "gb-broken.py", FIXTURE_BROKEN)
    gbtypes.atomic_write_text(bin_dir / "gb-lib.py", FIXTURE_LIB)
    gbtypes.atomic_write_text(bin_dir / "gb-daily.sh", FIXTURE_DAILY)
    gbtypes.atomic_write_text(
        root / "README.md", "run bin/gb-beta.py rank for the ranking\n"
    )
    for name in extra:
        gbtypes.atomic_write_text(
            bin_dir / name, FIXTURE_PRODUCER.format(choices='"go",', first="go")
        )


def _write_dispatch_fixture(
    root: pathlib.Path, *, ship_omega: bool = False, manifest: bool = True
) -> None:
    """The reverse-direction repo. `ship_omega` is the POSITIVE CONTROL and `manifest` is the
    "nobody looked" control: a detector that cannot be turned off proves as little as one that
    cannot fire."""
    bin_dir = root / "bin"
    gbtypes.atomic_write_text(bin_dir / "gb", FIXTURE_DISPATCH_GB)
    # gb-zeta.py is deliberately NOT written: that absence IS the `absent` leg.
    for name in (
        "gb-alpha.py",
        "gb-omega.py",
        "gb-chain.py",
        "gb-table.py",
        "gb-inert.py",
    ):
        gbtypes.atomic_write_text(
            bin_dir / name, FIXTURE_PRODUCER.format(choices='"go",', first="go")
        )
    if manifest:
        gbtypes.atomic_write_text(
            bin_dir / "gb-export-public.py",
            FIXTURE_EXPORT.format(
                extra='\n    ("gb-omega.py", "allowlisted"),' if ship_omega else ""
            ),
        )


def selftest() -> int:
    fails: List[str] = []
    checks = 0

    def check(ok: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            fails.append(label)

    # --- unit: the two surface extractors, on the two idioms that exist in this tree
    raw = ast.parse(
        'ap.add_argument("verb", nargs="?", choices=("rank", "gaps"))\n'
        'ap.add_argument("--engine", choices=("auto", "sqlite3"))\n'
    )
    check(
        positional_choices(raw) == ("rank", "gaps"),
        "raw add_argument positional not read",
    )
    check(
        "auto" not in positional_choices(raw),
        "a --flag with choices was read as an action (gb-store.py --engine would become a verb)",
    )
    derived = ast.parse(
        'action: str = gbargs.arg(default="roster", choices=("roster", "send"), positional=True)\n'
        'engine: str = gbargs.arg(default="auto", choices=("auto", "fsqlite"))\n'
    )
    check(
        positional_choices(derived) == ("roster", "send"),
        "gbargs positional choices not read",
    )
    check(
        "fsqlite" not in positional_choices(derived),
        "a non-positional gbargs field with choices was read as an action",
    )
    check(
        positional_choices(
            ast.parse('sub.add_parser("collect")\nsub.add_parser("velocity")')
        )
        == ("collect", "velocity"),
        "add_parser subcommands not read (gb-sources.py shape)",
    )
    check(
        positional_choices(ast.parse("ap.add_argument('c', choices=CHECKS)")) == (),
        "a non-literal choices= was guessed at instead of left to the --help probe",
    )
    check(
        help_choices("usage: x [-h] [{list,show,deploy}] [id]")
        == ("list", "show", "deploy"),
        "argparse usage {a,b,c} group not parsed",
    )
    check(
        has_parser(ast.parse("import argparse\nargparse.ArgumentParser()")),
        "parser not seen",
    )
    check(
        not has_parser(ast.parse("def f():\n    return 1")),
        "a library was read as a CLI",
    )

    # --- unit: scoring order is the contract, not an accident
    zero = Signals()
    check(
        Gap("broken", "a", "", (), zero).score
        > Gap("truncated", "b", "", (), zero).score
        > Gap("unreachable", "c", "", (), zero).score
        > Gap("tick-only", "d", "", (), zero).score
        > Gap("internal-only", "e", "", (), zero).score,
        "gap kinds do not rank broken > truncated > unreachable > tick-only > internal-only",
    )
    check(
        Gap("absent", "a", "", (), zero).score > Gap("broken", "b", "", (), zero).score,
        "a verb pointing at a file that is not there did not outrank a producer that is",
    )
    check(
        Gap("unshipped", "a", "", (), zero).score
        > Gap("truncated", "b", "", (), zero).score,
        "a verb that dies in a stranger's clone did not outrank a half-exposed verb",
    )
    check(
        Signals(importers=2).score() > Signals(callers=2).score(),
        "a dynamic importer does not outweigh a subprocess caller",
    )

    # --- end-to-end, against a real miniature repo with real processes
    with tempfile.TemporaryDirectory(prefix="gb-dogfood-selftest.") as tmp:
        root = pathlib.Path(tmp)
        _write_fixture(root)
        rep = audit(root, probe=True)
        keys = {g.key for g in rep.gaps}

        check(
            "truncated:alpha:deploy" in keys,
            "the truncated gap was missed (this is the gb-templates.py deploy defect)",
        )
        check(
            "broken:gb-broken.py" in keys, "a producer that cannot run was not reported"
        )
        check(
            "unreachable:gb-beta.py" in keys,
            "an unreferenced producer was not reported",
        )
        check("tick-only:gb-gamma.py" in keys, "a tick-only producer was not reported")
        check(
            "internal-only:gb-epsilon.py" in keys,
            "a producer named outside every cmd_* handler was not graded internal-only",
        )
        check(
            not any(k.endswith("gb-lib.py") for k in keys),
            "a library with no parser was reported as a gap",
        )
        check(rep.libraries == 1, f"library count wrong: {rep.libraries}")
        check(
            rep.gaps[0].key == "broken:gb-broken.py",
            f"ranking did not put the broken producer first: {rep.gaps[0].key}",
        )
        beta = next(g for g in rep.gaps if g.target == "gb-beta.py")
        check(
            beta.signals.docs == 1,
            "a README naming a producer path was not counted as demand",
        )
        check(
            next(g for g in rep.gaps if g.target == "gb-gamma.py").signals.tick == 1,
            "a producer run by gb-daily.sh was not marked tick",
        )

        # --- the ratchet: record, hold, fire on a NEW gap, shrink on a closed one
        base = root / BASELINE_NAME
        first = apply_ratchet(rep, base)
        check(
            first.first_run and first.code == 0 and base.exists(),
            "first run did not record",
        )

        hold = apply_ratchet(audit(root, probe=True), base)
        check(
            hold.code == 0 and not hold.new and not hold.wrote,
            "an unchanged repo did not hold the baseline",
        )

        # fires-on-known-bad: a brand new unreachable producer MUST be exit 1 and MUST NOT record
        _write_fixture(root, extra=("gb-delta.py",))
        grew = apply_ratchet(audit(root, probe=True), base)
        check(grew.code == 1, "KNOWN-BAD: an injected new gap did not exit 1")
        check(
            grew.new == ("unreachable:gb-delta.py",),
            f"KNOWN-BAD: the new gap was not named: {grew.new}",
        )
        check(
            (load_baseline(base) or {}).get("gap_count") == len(rep.gaps),
            "KNOWN-BAD: a refused growth was written into the baseline anyway",
        )

        accepted = apply_ratchet(
            audit(root, probe=True),
            base,
            accepted_by="selftest",
            why="proving the gate",
        )
        check(
            accepted.code == 0 and accepted.wrote, "--accept did not record the growth"
        )
        recorded = load_baseline(base) or {}
        check(
            "unreachable:gb-delta.py" in (recorded.get("gaps") or {}),
            "--accept did not persist the accepted gap",
        )
        check(
            (recorded.get("acceptances") or [{}])[-1].get("by") == "selftest",
            "--accept did not record WHO accepted it",
        )

        # ratchet down: delete the producers, baseline must shrink without any ceremony
        (root / "bin" / "gb-delta.py").unlink()
        (root / "bin" / "gb-beta.py").unlink()
        shrank = apply_ratchet(audit(root, probe=True), base)
        check(shrank.code == 0, "closing gaps was treated as a finding")
        check(
            set(shrank.closed) == {"unreachable:gb-delta.py", "unreachable:gb-beta.py"},
            f"closed gaps not detected: {shrank.closed}",
        )
        check(
            "unreachable:gb-beta.py"
            not in ((load_baseline(base) or {}).get("gaps") or {}),
            "the baseline did not shrink after a gap was closed",
        )

    # --- the REVERSE direction, end to end, in its own repo. Every leg here is paired: the
    # detector must FIRE on the known-bad shape and must FALL SILENT on the control, because a
    # probe that cannot fail and a probe that cannot pass are the same worthless probe.
    with tempfile.TemporaryDirectory(prefix="gb-dogfood-dispatch.") as tmp:
        root = pathlib.Path(tmp)
        _write_dispatch_fixture(root)
        rep = audit(root, probe=False)
        keys = {g.key for g in rep.gaps}
        routed = {d.verb: d.producers for d in rep.dispatches}

        check(
            routed.get("alpha") == ("gb-alpha.py",),
            f"a literal in the handler did not resolve: {routed.get('alpha')}",
        )
        check(
            routed.get("chain") == ("gb-chain.py",),
            f"a producer named only in a called helper was not reached: {routed.get('chain')}",
        )
        check(
            routed.get("table") == ("gb-table.py",),
            f"a producer named only in a module constant was not reached: {routed.get('table')}",
        )
        check(
            routed.get("inert") == (),
            f"a verb that NAMES a producer but never spawns claimed it: {routed.get('inert')}",
        )
        check(
            any(u.startswith("orphan") for u in rep.unresolved),
            f"a verb with no HANDLERS entry was not reported unresolved: {rep.unresolved}",
        )
        check(
            "absent:zeta:gb-zeta.py" in keys,
            f"KNOWN-BAD: a verb dispatching a file that is not there was not caught: {keys}",
        )
        check(
            "unshipped:omega:gb-omega.py" in keys,
            f"KNOWN-BAD: a verb whose producer is not in the export allowlist was not caught:"
            f" {keys}",
        )
        check(
            not any(k.startswith("unshipped:alpha") for k in keys),
            "an allowlisted producer was reported unshipped (false positive)",
        )
        check(
            rep.shipping == "" and rep.dispatch_edges == 5,
            f"the reverse denominator is wrong: edges={rep.dispatch_edges} "
            f"shipping={rep.shipping!r}",
        )

        # POSITIVE CONTROL: the identical repo with gb-omega.py allowlisted must go quiet, and
        # ONLY that row may move. Without this, "0 unshipped" could mean the check never ran.
        _write_dispatch_fixture(root, ship_omega=True)
        fixed = audit(root, probe=False)
        fixed_keys = {g.key for g in fixed.gaps}
        check(
            "unshipped:omega:gb-omega.py" not in fixed_keys,
            "allowlisting the producer did not clear the unshipped gap — the check is stuck on",
        )
        check(
            keys - fixed_keys == {"unshipped:omega:gb-omega.py"},
            f"allowlisting one producer changed more than one row: {keys - fixed_keys}",
        )
        check(
            "absent:zeta:gb-zeta.py" in fixed_keys,
            "the absent gap vanished when the allowlist changed — the two legs are entangled",
        )

        # NOBODY-LOOKED CONTROL: with no exporter in the tree, shipping MUST report itself
        # unchecked rather than silently returning zero unshipped gaps.
        (root / "bin" / "gb-export-public.py").unlink()
        blind = audit(root, probe=False)
        check(
            bool(blind.shipping) and not blind.as_json()["shipping_checked"],
            "a tree with no export manifest claimed the shipping check had run",
        )
        check(
            not any(g.kind == "unshipped" for g in blind.gaps)
            and "absent:zeta:gb-zeta.py" in {g.key for g in blind.gaps},
            "an unreadable allowlist did not suppress unshipped while keeping absent",
        )

    for f in fails:
        print(f"FAIL: {f}")
    print(
        f"SELFTEST {'FAIL' if fails else 'PASS'} - {checks - len(fails)}/{checks} properties"
    )
    return 1 if fails else 0


# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class DogfoodArgs:
    """Is every capability in this repo reachable through `gb`, or only by hand?"""

    action: str = gbargs.arg(
        default="audit",
        help="audit | baseline",
        choices=("audit", "baseline"),
        positional=True,
    )
    root: Optional[str] = gbargs.arg(
        default=None, help="repo root (default: this checkout)"
    )
    accept: Optional[str] = gbargs.arg(
        default=None, help="record a NEW gap as accepted; names who accepted it"
    )
    why: Optional[str] = gbargs.arg(
        default=None, help="required with --accept: the reason"
    )
    no_probe: bool = gbargs.arg(
        default=False,
        help="skip the --help probe (AST only; cannot detect a broken producer)",
    )
    dry_run: bool = gbargs.arg(
        default=False, help="measure and report, but write no baseline"
    )
    json_out: bool = gbargs.arg(
        default=False, help="machine-readable envelope on stdout"
    )
    selftest: bool = gbargs.arg(default=False, help="prove this module's properties")


def body() -> int:
    # `--selftest` is answered before argparse for the same reason gb-fleet.py does it: a
    # selftest that cannot be invoked without also naming an action is a selftest nobody runs.
    if "--selftest" in sys.argv[1:]:
        return selftest()

    parser = gbargs.build_parser(
        DogfoodArgs, prog="gb-dogfood", description=(__doc__ or "").splitlines()[0]
    )
    ns = parser.parse_args()

    root = pathlib.Path(str(ns.root)).resolve() if ns.root else ROOT
    if not (root / "bin" / "gb").exists():
        print(
            f"gb-dogfood: {root} has no bin/gb — not a grokbot checkout",
            file=sys.stderr,
        )
        return 3

    baseline_path = root / BASELINE_NAME
    as_json = bool(ns.json_out)

    if str(ns.action) == "baseline":
        return render_baseline(baseline_path, as_json)

    accept = str(ns.accept) if ns.accept else ""
    why = str(ns.why) if ns.why else ""
    if accept and not why:
        print(
            "gb-dogfood: --accept requires --why (a reason is part of the record)",
            file=sys.stderr,
        )
        return 2
    if why and not accept:
        print("gb-dogfood: --why without --accept records nothing", file=sys.stderr)
        return 2

    report = audit(root, probe=not ns.no_probe)
    ratchet = apply_ratchet(
        report,
        baseline_path,
        accepted_by=accept,
        why=why,
        write=not ns.dry_run,
    )

    if as_json:
        payload = report.as_json()
        payload["ratchet"] = {
            "new": list(ratchet.new),
            "closed": list(ratchet.closed),
            "first_run": ratchet.first_run,
            "baseline_written": ratchet.wrote,
            "baseline": str(baseline_path),
        }
        payload["verdict"] = "FINDINGS" if ratchet.new else "OK"
        print(json.dumps(payload, indent=1))
    else:
        render(report, ratchet, baseline_path)
    return ratchet.code


if __name__ == "__main__":
    gbtypes.main(body)
