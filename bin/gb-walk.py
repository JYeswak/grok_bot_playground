#!/usr/bin/env python3
"""gb-walk — the guided tour. Two tracks: the TOOL, and the BOTS this repo proposes.

WHY THIS EXISTS. A stranger who installs `gb` has two separate questions and this repo used to
answer neither in order: "what are these nineteen verbs and which one do I type first", and "you
say you PROPOSE Bots — show me one and tell me where to paste it". Those are different tours, so
they are different tracks:

  gb-walk.py cli     walks the tool in the order a new operator needs it — setup, triage, doctor,
                     why, repair — and RUNS the read-only verbs live, showing their real output.
  gb-walk.py bots    walks templates/*.json: the job, the charter with its character count, the
                     routine, the approval boundary, and how to verify it actually works.
  gb-walk.py bots --paste <id>
                     prints ONLY the charter, clean, for pasting into the Grok Bot app.

THREE PROPERTIES THAT ARE NOT NEGOTIABLE HERE.

1. IT RUNS NOTHING THAT WRITES OR FETCHES. Every demo carries a mode: `run` or `would-run`. The
   classification is measured, not guessed — `gb doctor` with no `--scope` was measured on
   2026-09-11 probing MCP servers over the network and writing `mcp/2026-09-11T1931.json`, so it
   is `would-run` and the tour live-runs `gb doctor --scope plugin` instead. A tour that mutates
   the thing it is touring is not a tour.

2. IT NEVER HARDCODES THE VERB LIST. `gb capabilities --json` is the contract; this file carries
   annotations keyed by verb name and RECONCILES the two, reporting any verb it cannot explain
   and any annotation with no verb behind it. Measured 2026-09-11: the public mirror ships 18
   verbs and this checkout has 19 (`monitor` is newer than the export), so a hardcoded "19 verbs"
   would have been wrong for every pip user on the day it was written.

3. IT NEVER HARDCODES A TEMPLATE. The bots track reads `templates/*.json` off disk and validates
   the `gb-template/1` schema. An empty or absent `templates/` is reported as a FAILURE with the
   remediation, never rendered as a successful tour of nothing.

  gb-walk.py cli                     walk the tool
  gb-walk.py cli --step              one stop at a time
  gb-walk.py bots                    walk the proposed Bots
  gb-walk.py bots --paste inbox-triage    just the charter, for the clipboard
  gb-walk.py bots --json             machine-readable
  gb-walk.py --selftest              offline proofs
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import glob
import io
import json
import os
import pathlib
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import (  # noqa: E402
    atomic_write_json,
    atomic_write_text,
    main as gb_main,
    read_json_capped,
    run,
)

SCHEMA = "gb-walk/1"
TEMPLATE_SCHEMA = "gb-template/1"

# The 15 keys BotTemplateAuthor froze for gb-template/1 on 2026-09-11. Every template carries all
# of them; a template missing one is SKIPPED with the key named, because a walk screen that
# silently renders "job: None" teaches the reader that the schema is advisory.
TEMPLATE_KEYS: Tuple[str, ...] = (
    "schema",
    "id",
    "name",
    "tier",
    "category",
    "job",
    "charter",
    "charter_chars",
    "integrations",
    "routine",
    "approval_boundary",
    "memory",
    "verify",
    "why_this_shape",
    "replaces",
)

# What each tier MEANS, in the words of the measurement that produced it. Rendered verbatim so a
# reader learns why the walk is in this order rather than alphabetical.
TIER_LABEL: Dict[str, str] = {
    "A": "closes the measured deficit: 2 routines exist on this account and 0 have ever run",
    "B": "narrows a department Bot — `replaces` below names which one",
    "C": "corpus-proven shape, de-identified from the 645-Bot corpus",
}

# Per-run output cap. A tour that dumps 300 lines of `gb audit` is not a tour either; the honest
# tail line names how much was withheld and how to see it.
DEFAULT_MAX_LINES = 14
DEMO_TIMEOUT_S = 30.0


# ---------------------------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------------------------
def _colour_enabled(stream: Any) -> bool:
    """Colour only for a human at a terminal. NO_COLOR, CI and TERM=dumb each veto it.

    The veto list matches what this repo's own spine selftest asserts about `gb`, so piping the
    walk into a file or a peer agent yields bytes with no escapes in them.
    """
    if os.environ.get("NO_COLOR") or os.environ.get("CI"):
        return False
    if os.environ.get("TERM", "") in ("dumb", ""):
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


class Ink:
    """The two-escape palette. Anything richer is decoration nobody asked for."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def bold(self, s: str) -> str:
        return f"\033[1m{s}\033[0m" if self.enabled else s

    def dim(self, s: str) -> str:
        return f"\033[2m{s}\033[0m" if self.enabled else s


def emit(line: str = "") -> None:
    print(line)


def warn(line: str) -> None:
    print(line, file=sys.stderr)


def _indent(body: str, prefix: str = "    | ") -> List[str]:
    return [prefix + ln for ln in body.splitlines()]


def _clip(body: str, max_lines: int) -> Tuple[List[str], int]:
    """Return (kept lines, withheld count). Never silently truncates: the caller prints the count."""
    lines = body.splitlines()
    if max_lines <= 0 or len(lines) <= max_lines:
        return lines, 0
    return lines[:max_lines], len(lines) - max_lines


# ---------------------------------------------------------------------------------------------
# Track 1: the tool
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Demo:
    """One command the tour shows.

    `mode` is the whole point. `run` means measured pure-read and the tour executes it. `would-run`
    means it writes an artifact, hits the network, or mutates the account, and the tour prints the
    command and `why_not` instead of running it.
    """

    argv: Tuple[str, ...]
    mode: str  # "run" | "would-run"
    why_not: str = ""


@dataclasses.dataclass(frozen=True)
class Verb:
    """One `gb` verb, annotated: what it is for, and when a new operator reaches for it."""

    name: str
    purpose: str
    reach_for_it: str
    demos: Tuple[Demo, ...] = ()


@dataclasses.dataclass(frozen=True)
class Stop:
    """One stop on the tour: a question an operator actually has, and the verbs that answer it."""

    title: str
    question: str
    verbs: Tuple[Verb, ...]


def _d(*argv: str, mode: str = "run", why_not: str = "") -> Demo:
    return Demo(argv=tuple(argv), mode=mode, why_not=why_not)


# THE TOUR. Ordered by the question a first-time operator has, not by the alphabet and not by
# argparse's declaration order. `setup -> triage -> doctor -> why -> repair` is the spine; the
# reference surfaces come last because nobody reads reference material before they have a problem.
STOPS: Tuple[Stop, ...] = (
    Stop(
        "Where am I, and is this machine even configured?",
        "You just installed `gb`. Before it can judge a deployment it has to have measured one.",
        (
            Verb(
                "platform",
                "Which OS this is, whether `gb` is verified here, and what works on it.",
                "First, because an answer you cannot trust on this OS is worse than no answer.",
                (_d("platform"),),
            ),
            Verb(
                "setup",
                "Takes a fresh clone to a measured instance. Dry-run unless `--apply`.",
                "Second. Read the plan, then run it with `--apply` when you believe it.",
                (
                    _d(
                        "setup",
                        mode="would-run",
                        why_not="it writes NOTHING — but MEASURED 2026-09-11 its `network` row "
                        "probes the internet (GET https://docs.x.ai/llms.txt -> 200), and this "
                        "tour does not reach the network on your behalf. Run it right now: the "
                        "plan it prints is the next thing you want to read.",
                    ),
                    _d(
                        "setup",
                        "--apply",
                        mode="would-run",
                        why_not="registers this desktop and takes the first measurements — it "
                        "writes. Run it yourself when you have read the plan above.",
                    ),
                ),
            ),
        ),
    ),
    Stop(
        "What is wrong right now?",
        "The one verb to type when you have no idea what is going on.",
        (
            Verb(
                "triage",
                "What is wrong right now, and the exact command that addresses it.",
                "Every session. If you learn one verb, learn this one.",
                (_d("triage"),),
            ),
            Verb(
                "health",
                "Single-shot state of the deployment — lighter than doctor, safe in a loop.",
                "From a scheduler, or any time you want one line instead of a board.",
                (_d("health"),),
            ),
        ),
    ),
    Stop(
        "What should I pick up next?",
        "Triage says what is broken. This says what is worth doing, and whether to trust the order.",
        (
            Verb(
                "work",
                "What to pick up next here, and whether that ranking can be trusted.",
                "When triage is clean but you still have an hour.",
                (_d("work"),),
            ),
        ),
    ),
    Stop(
        "Diagnose it properly, subsystem by subsystem",
        "Six subsystems, each PASS or FAIL on its own terms.",
        (
            Verb(
                "doctor",
                "Diagnose every subsystem (or one) and report PASS/FAIL per subsystem.",
                "When triage names a subsystem and you want that subsystem's own verdict.",
                (
                    _d("doctor", "--scope", "plugin"),
                    _d(
                        "doctor",
                        mode="would-run",
                        why_not="MEASURED 2026-09-11: with no --scope this probes MCP servers over "
                        "the network and WRITES mcp/<stamp>.json. Read-only it is not, so this "
                        "tour will not run it for you.",
                    ),
                ),
            ),
        ),
    ),
    Stop(
        "Why does it say that?",
        "The verb that makes the tool auditable instead of oracular.",
        (
            Verb(
                "why",
                "Provenance for one check or facet: what it reads, and what it last said.",
                "The moment a verdict surprises you. Never argue with a board you have not `why`-ed.",
                (_d("why", "g8-desktop-inventory"),),
            ),
        ),
    ),
    Stop(
        "Fix it",
        "Repair is idempotent and refuses to write without being told twice.",
        (
            Verb(
                "repair",
                "Idempotently rebuild a derived artifact. Dry-run unless `--apply` is given.",
                "After `why` tells you a derived artifact is stale rather than a real finding.",
                (
                    _d("repair", "--scope", "coverage"),
                    _d(
                        "repair",
                        "--scope",
                        "coverage",
                        "--apply",
                        mode="would-run",
                        why_not="this is the write. The dry-run above printed exactly what it "
                        "would do; `--apply` does it.",
                    ),
                ),
            ),
        ),
    ),
    Stop(
        "Verify something without changing it",
        "Pure reads with their own selftests behind them.",
        (
            Verb(
                "validate",
                "Verify a thing without changing it. Targets: plugin, mcp, types, platform, fixtures.",
                "Before you trust a subsystem, and in CI.",
                (_d("validate", "platform"),),
            ),
        ),
    ),
    Stop(
        "What changed here, and who changed it?",
        "Provenance for the repository itself, not just for one check.",
        (
            Verb(
                "audit",
                "Recent mutations to this repo's artifacts, with provenance.",
                "After a scheduled tick, or when an artifact is newer than you expected.",
                (_d("audit"),),
            ),
        ),
    ),
    Stop(
        "Give a scheduler something to branch on",
        "Named thresholds over artifacts already on disk — no fetching, so it is cheap in a loop.",
        (
            Verb(
                "monitor",
                "Named thresholds over the artifacts already on disk, for a scheduler to branch on.",
                "From cron. Exit code and named thresholds, not prose.",
                (
                    _d(
                        "monitor",
                        mode="would-run",
                        why_not="MEASURED 2026-09-11 by manifesting this repository before and "
                        "after a full tour: it WRITES monitor/<stamp>.json. Its own description "
                        "says 'over the artifacts already on disk', which reads as pure — it is "
                        "not, and this tour found that out by diffing the tree rather than by "
                        "believing the sentence.",
                    ),
                ),
            ),
        ),
    ),
    Stop(
        "Find capability this deployment is not using",
        "The only verb here whose job is to look outward.",
        (
            Verb(
                "mine",
                "Mine franken-harvest and the local mirror for capability this deployment could adopt.",
                "Weekly, not hourly.",
                (
                    _d(
                        "mine",
                        mode="would-run",
                        why_not="writes mine/<stamp>.json and reads corpora outside this "
                        "repository. Not a read, so not on the tour's dime.",
                    ),
                ),
            ),
        ),
    ),
    Stop(
        "The reference surfaces",
        "Read these when you have a problem, not before.",
        (
            Verb(
                "info",
                "What this tool is, in one screen.",
                "Once.",
                (_d("info"),),
            ),
            Verb(
                "quickstart",
                "The shortest path from install to a measured instance.",
                "Once, right after install.",
                (_d("quickstart"),),
            ),
            Verb(
                "examples",
                "Copy-pasteable invocations for the common jobs.",
                "When you know what you want and not how to spell it.",
                (_d("examples"),),
            ),
            Verb(
                "help",
                "Topic-based manual. `gb help` lists the topics.",
                "When a concept rather than a command is unclear.",
                (_d("help"),),
            ),
            Verb(
                "robot-docs",
                "The whole surface written for an agent rather than a human.",
                "If you are an agent, start here instead of this tour.",
                (
                    _d(
                        "robot-docs",
                        mode="would-run",
                        why_not="it is long by design — the point is that an agent reads it in one "
                        "shot. Run it yourself rather than have this tour quote a tenth of it.",
                    ),
                ),
            ),
            Verb(
                "capabilities",
                "The machine-readable contract: version, commands, exit codes, subsystems.",
                "From any program that drives `gb`. This tour reads it to build itself.",
                (_d("capabilities", "--json"),),
            ),
            Verb(
                "completion",
                "Print a shell completion script (bash | zsh).",
                "Once, into your shell rc.",
                (
                    _d(
                        "completion",
                        "zsh",
                        mode="would-run",
                        why_not="it emits a shell script; quoting it here teaches nothing. "
                        "`gb completion zsh >> ~/.zshrc` is the whole story.",
                    ),
                ),
            ),
        ),
    ),
)


def annotated_verbs(stops: Sequence[Stop] = STOPS) -> Dict[str, Verb]:
    return {v.name: v for s in stops for v in s.verbs}


def reconcile(
    annotated: Sequence[str], live: Optional[Sequence[str]]
) -> Tuple[List[str], List[str]]:
    """Compare the annotation table against `gb capabilities --json`.

    Returns (unannotated, absent): verbs this `gb` has that the tour cannot explain, and verbs the
    tour explains that this `gb` does not have. Both are NOTES rather than errors — the second one
    is the real, measured state of the public mirror, which shipped 18 verbs while this checkout
    had 19. `live is None` means capabilities could not be read, and yields two empty lists
    because inventing a diff against nothing is worse than declining to.
    """
    if live is None:
        return [], []
    a, l = set(annotated), set(live)
    return sorted(l - a), sorted(a - l)


def resolve_gb(explicit: Optional[str]) -> Optional[pathlib.Path]:
    """Find the `gb` this tour should demonstrate.

    Order: what you told us, the sibling of this file (a clone, or the installed package's own
    `bin/`), then PATH. Sibling before PATH so a contributor touring a checkout sees the checkout's
    `gb`, not whatever they pip-installed last month.
    """
    if explicit:
        p = pathlib.Path(explicit).expanduser()
        return p if p.exists() else None
    sibling = pathlib.Path(__file__).resolve().parent / "gb"
    if sibling.exists():
        return sibling
    import shutil

    found = shutil.which("gb")
    return pathlib.Path(found) if found else None


def gb_argv(gb: pathlib.Path, args: Sequence[str]) -> List[str]:
    """`bin/gb` is extensionless; run it under this interpreter rather than trusting its +x bit."""
    return [sys.executable, str(gb)] + list(args)


def live_capabilities(
    gb: Optional[pathlib.Path], cwd: pathlib.Path
) -> Optional[Dict[str, Any]]:
    if gb is None:
        return None
    proc = run(
        gb_argv(gb, ["capabilities", "--json"]), timeout_s=DEMO_TIMEOUT_S, cwd=cwd
    )
    if proc.code != 0:
        return None
    try:
        payload = json.loads(proc.out)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _child_env() -> Dict[str, str]:
    """Children inherit the environment plus NO_COLOR, so captured output has no escapes in it."""
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    return env


def execute(demo: Demo, gb: pathlib.Path, cwd: pathlib.Path) -> Dict[str, Any]:
    """Run one `run`-mode demo and record what actually happened.

    Exit 1 is FINDINGS in this tool's contract — "ran correctly, and the answer is bad". A tour
    that painted that red would be teaching the reader to misread every verb here, so the record
    keeps the code and the tour keeps going.
    """
    proc = run(
        gb_argv(gb, demo.argv), timeout_s=DEMO_TIMEOUT_S, cwd=cwd, env=_child_env()
    )
    body = proc.out if proc.out.strip() else proc.err
    return {
        "exit": proc.code,
        "timed_out": proc.timed_out,
        "truncated": proc.truncated,
        "elapsed_s": proc.elapsed_s,
        "output": body,
    }


def walk_cli(
    *,
    gb: Optional[pathlib.Path],
    cwd: pathlib.Path,
    caps: Optional[Dict[str, Any]],
    max_lines: int,
) -> Dict[str, Any]:
    """Build the whole CLI tour as data. Rendering is a separate, dumber function."""
    live = (
        sorted(caps["commands"])
        if isinstance(caps, dict) and isinstance(caps.get("commands"), dict)
        else None
    )
    annotated = annotated_verbs()
    unannotated, absent = reconcile(sorted(annotated), live)
    stops: List[Dict[str, Any]] = []
    for i, stop in enumerate(STOPS, 1):
        items: List[Dict[str, Any]] = []
        for verb in stop.verbs:
            here = live is None or verb.name in live
            for demo in verb.demos:
                rec: Dict[str, Any] = {
                    "verb": verb.name,
                    "purpose": verb.purpose,
                    "reach_for_it": verb.reach_for_it,
                    "command": "gb " + " ".join(demo.argv),
                    "mode": demo.mode,
                    "why_not": demo.why_not,
                    "available": here,
                }
                if demo.mode == "run" and gb is not None and here:
                    rec.update(execute(demo, gb, cwd))
                items.append(rec)
        stops.append(
            {
                "n": i,
                "title": stop.title,
                "question": stop.question,
                "items": items,
            }
        )
    return {
        "schema": SCHEMA,
        "track": "cli",
        "gb": str(gb) if gb else None,
        "gb_version": (caps or {}).get("version"),
        "cwd": str(cwd),
        "verbs_annotated": len(annotated),
        "verbs_live": len(live) if live is not None else None,
        "unannotated": unannotated,
        "absent_from_this_gb": absent,
        "max_lines": max_lines,
        "stops": stops,
    }


def render_cli(doc: Dict[str, Any], ink: Ink, *, step: bool, interactive: bool) -> int:
    """Print the tour. Returns the process exit code.

    3 (ENVIRONMENT, in this tool's own table) when there is no `gb` to demonstrate — the tour is
    still printed, because the annotations are the durable half, but a tour that demonstrated
    nothing must not claim success.
    """
    emit(ink.bold("gb — the tool, in the order you need it"))
    emit()
    if doc["gb"] is None:
        warn(
            "no `gb` found: not next to this file and not on PATH.\n"
            "  remediation: pip install git+https://github.com/JYeswak/grok_bot_playground\n"
            "  or:          bash install.sh\n"
            "  or:          gb-walk.py cli --gb /path/to/bin/gb\n"
            "the annotations below are still accurate; nothing was run."
        )
        emit()
    else:
        emit(f"  gb        {doc['gb']}")
        emit(f"  version   {doc['gb_version'] or 'unknown'}")
        emit(
            f"  cwd       {doc['cwd']}   (gb judges the artifacts in the directory you run it from)"
        )
        live = doc["verbs_live"]
        emit(
            f"  verbs     {doc['verbs_annotated']} explained here"
            + (f" / {live} this gb actually has" if live is not None else "")
        )
        for name in doc["absent_from_this_gb"]:
            emit(
                ink.dim(
                    f"  NOTE      `{name}` is explained below but this gb does not have it — your "
                    f"copy is older than this tour."
                )
            )
        for name in doc["unannotated"]:
            emit(
                ink.dim(
                    f"  NOTE      this gb has `{name}`, which this tour does not explain — the "
                    f"tour is older than your copy."
                )
            )
        emit()
        emit(
            ink.dim(
                "  run       executed live below — real output, and measured to touch neither"
            )
        )
        emit(ink.dim("            disk nor network"))
        emit(
            ink.dim(
                "  would run NOT executed: it writes an artifact, mutates the account, reaches"
            )
        )
        emit(
            ink.dim(
                "            the network, or is long by design. The command is yours to run."
            )
        )
    emit()

    total = len(doc["stops"])
    for stop in doc["stops"]:
        emit(ink.bold(f"[{stop['n']}/{total}] {stop['title']}"))
        emit(f"  {stop['question']}")
        emit()
        for item in stop["items"]:
            emit(f"  {ink.bold(item['verb'])} — {item['purpose']}")
            emit(f"    when: {item['reach_for_it']}")
            if not item["available"]:
                emit(f"    $ {item['command']}")
                emit("    [unavailable] this gb does not have that verb")
                emit()
                continue
            if item["mode"] == "would-run":
                emit(f"    $ {item['command']}")
                emit(f"    [would run] {item['why_not']}")
                emit()
                continue
            emit(f"    $ {item['command']}")
            if "output" not in item:
                emit("    [not run] no gb to run it with")
                emit()
                continue
            kept, withheld = _clip(item["output"], doc["max_lines"])
            for ln in _indent("\n".join(kept)):
                emit(ln)
            if withheld:
                emit(
                    ink.dim(
                        f"    | ... {withheld} more line(s) — run it yourself, or pass --full"
                    )
                )
            code = item["exit"]
            note = (
                " (FINDINGS — it ran correctly and the answer is bad; that is not a crash)"
                if code == 1
                else ""
            )
            emit(ink.dim(f"    exit {code}{note}  ·  {item['elapsed_s']}s"))
            emit()
        if step and not _pause(stop["n"], total, interactive):
            return 0
    emit(ink.bold("next"))
    emit("  gb-walk.py bots     the Bots this repo proposes, and where to paste them")
    emit("  gb triage           the verb you will actually type tomorrow")
    return 3 if doc["gb"] is None else 0


def _pause(n: int, total: int, interactive: bool) -> bool:
    """One step boundary. Returns False to stop the tour.

    Non-interactive (a pipe, a CI log, another agent) MUST NOT block: it prints the boundary and
    carries on. `--step` is a reading aid, not a reason for a script to hang forever.
    """
    if n >= total:
        return True
    if not interactive:
        emit(f"--- step {n}/{total} ---")
        return True
    try:
        answer = input(f"[{n}/{total}] enter to continue, q to quit: ").strip().lower()
    except EOFError:
        return True
    return answer not in ("q", "quit", "n", "no")


# ---------------------------------------------------------------------------------------------
# Track 2: the Bots
# ---------------------------------------------------------------------------------------------
def resolve_templates(
    explicit: Optional[str],
) -> Tuple[Optional[pathlib.Path], List[str]]:
    """Find `templates/`. Returns (dir or None, the places looked).

    AN EXPLICIT PATH IS AUTHORITATIVE AND DOES NOT FALL BACK. `--templates` and `$GB_TEMPLATES`
    are statements, not hints, so a path you named that does not exist is an error — not a licence
    to search elsewhere. MEASURED as a defect in the first cut of this function: pointing
    `--templates` at a directory that did not exist walked the repository's own `templates/`
    instead and exited 0, so a typo in a scheduler's flag would have silently toured the wrong
    corpus and reported success. `looked` carries exactly one entry in that case, which is what
    makes the error message unambiguous.

    Only the IMPLICIT search has candidates, and each one earns its place:
      <repo>/templates            a clone — the case that works today
      <installed pkg>/templates   a pip install, IF packaging ever ships them
      ./templates                 you are standing in a clone
    """
    for source, value in (
        ("--templates", explicit),
        ("$GB_TEMPLATES", os.environ.get("GB_TEMPLATES")),
    ):
        if value:
            named = pathlib.Path(value).expanduser()
            return (named if named.is_dir() else None), [f"{named} (from {source})"]
    here = pathlib.Path(__file__).resolve()
    candidates = (
        here.parents[1] / "templates",
        here.parents[1] / "grok_bot_ops" / "templates",
        pathlib.Path.cwd() / "templates",
    )
    looked: List[str] = []
    for c in candidates:
        looked.append(str(c))
        if c.is_dir():
            return c, looked
    return None, looked


def load_templates(
    directory: pathlib.Path,
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """Read and validate every `templates/*.json`. Returns (good, skipped, warnings).

    Validation is deliberately asymmetric, because the two failure modes deserve different
    treatment. A STRUCTURAL problem — wrong schema, missing key, wrong type for `routine` — means
    the walk cannot render the screen, so the file is skipped and named. A CONSISTENCY problem —
    `charter_chars` disagreeing with `len(charter)`, an unexpected key — means the screen renders
    fine but somebody's generator is drifting, so it warns and renders anyway. Skipping on a
    character count would hide a Bot over a cosmetic mismatch; warning on a missing `charter`
    would render a Bot with nothing to paste.
    """
    good: List[Dict[str, Any]] = []
    skipped: List[str] = []
    warnings: List[str] = []
    for path_s in sorted(glob.glob(str(directory / "*.json"))):
        path = pathlib.Path(path_s)
        doc = read_json_capped(path)
        if not isinstance(doc, dict):
            skipped.append(f"{path.name}: not a JSON object")
            continue
        if doc.get("schema") != TEMPLATE_SCHEMA:
            skipped.append(
                f"{path.name}: schema is {doc.get('schema')!r}, not {TEMPLATE_SCHEMA!r}"
            )
            continue
        missing = [k for k in TEMPLATE_KEYS if k not in doc]
        if missing:
            skipped.append(f"{path.name}: missing required key(s) {', '.join(missing)}")
            continue
        if not isinstance(doc.get("routine"), dict):
            skipped.append(f"{path.name}: `routine` must be an object, never null")
            continue
        if doc.get("id") != path.stem:
            warnings.append(
                f"{path.name}: id {doc['id']!r} does not match the filename stem"
            )
        charter = doc.get("charter")
        if isinstance(charter, str) and doc.get("charter_chars") != len(charter):
            warnings.append(
                f"{path.name}: charter_chars says {doc.get('charter_chars')}, "
                f"len(charter) is {len(charter)}"
            )
        extra = sorted(set(doc) - set(TEMPLATE_KEYS))
        if extra:
            warnings.append(
                f"{path.name}: unrecognised key(s) {', '.join(extra)} — rendered anyway"
            )
        doc["_path"] = str(path)
        good.append(doc)
    good.sort(key=lambda d: (str(d.get("tier", "")), str(d.get("id", ""))))
    return good, skipped, warnings


def routine_line(routine: Dict[str, Any]) -> str:
    """One line for the routine. `cadence == "none"` is a real answer, not a missing one."""
    cadence = str(routine.get("cadence") or "")
    if cadence == "none":
        return "no routine (chat-invoked — see WHY THIS SHAPE)"
    when = str(routine.get("when") or "").strip()
    return f"{cadence}{' · ' + when if when else ''}"


def render_bot(doc: Dict[str, Any], ink: Ink, *, n: int, total: int) -> None:
    charter = str(doc.get("charter") or "")
    tier = str(doc.get("tier") or "?")
    emit(ink.bold(f"[{n}/{total}] {doc.get('name')}  ({doc.get('id')})"))
    emit(f"  tier {tier} — {TIER_LABEL.get(tier, 'unlabelled tier')}")
    emit(f"  category      {doc.get('category')}")
    emit(f"  the job       {doc.get('job')}")
    integrations = doc.get("integrations") or []
    emit(
        f"  integrations  {', '.join(str(i) for i in integrations) if integrations else 'none — chat only'}"
    )
    routine = doc.get("routine") or {}
    emit(f"  routine       {routine_line(routine)}")
    if str(routine.get("cadence")) != "none":
        if routine.get("prompt"):
            emit(f"    it says     {routine.get('prompt')}")
        if routine.get("writes"):
            emit(f"    it leaves   {routine.get('writes')}")
    boundary = str(doc.get("approval_boundary") or "")
    emit(
        "  approval      "
        + (boundary if boundary else "cannot act outside chat — nothing to gate")
    )
    emit(f"  memory        {doc.get('memory')}")
    emit(f"  verify        {doc.get('verify')}")
    replaces = doc.get("replaces")
    emit(f"  narrows       {replaces if replaces else 'nothing — this is a new job'}")
    emit()
    emit("  why this shape, and not a longer one:")
    emit(f"    {doc.get('why_this_shape')}")
    emit()
    emit(
        f"  CHARTER ({len(charter)} chars) — paste this into the Bot's description field:"
    )
    for ln in _indent(charter):
        emit(ln)
    emit()
    emit(ink.dim(f"  clipboard:  gb-walk.py bots --paste {doc.get('id')} | pbcopy"))
    emit()


def walk_bots(
    *, directory: Optional[pathlib.Path], looked: Sequence[str]
) -> Dict[str, Any]:
    if directory is None:
        return {
            "schema": SCHEMA,
            "track": "bots",
            "templates_dir": None,
            "looked_in": list(looked),
            "count": 0,
            "skipped": [],
            "warnings": [],
            "templates": [],
        }
    good, skipped, warnings = load_templates(directory)
    return {
        "schema": SCHEMA,
        "track": "bots",
        "templates_dir": str(directory),
        "looked_in": list(looked),
        "count": len(good),
        "skipped": skipped,
        "warnings": warnings,
        "templates": good,
    }


def render_bots(doc: Dict[str, Any], ink: Ink, *, step: bool, interactive: bool) -> int:
    """Print the Bot tour. Returns the exit code; an empty tour is never a success."""
    if doc["templates_dir"] is None:
        warn("no templates/ directory found. Looked in:")
        for p in doc["looked_in"]:
            warn(f"  {p}")
        warn(
            "\nremediation — this walk reads real template files, it does not carry copies:\n"
            "  git clone https://github.com/JYeswak/grok_bot_playground && cd grok_bot_playground\n"
            "  python3 bin/gb-walk.py bots\n"
            "or point it at one:\n"
            "  gb-walk.py bots --templates /path/to/templates\n"
            "  GB_TEMPLATES=/path/to/templates gb-walk.py bots"
        )
        return 3
    for w in doc["warnings"]:
        warn(f"WARN {w}")
    for s in doc["skipped"]:
        warn(f"SKIP {s}")
    if doc["count"] == 0:
        warn(
            f"\n{doc['templates_dir']} holds no valid gb-template/1 file"
            + (
                f" ({len(doc['skipped'])} skipped above)"
                if doc["skipped"]
                else " (it is empty)"
            )
            + ".\nremediation: `python3 bin/gb-templates.py list` to see what the template CLI "
            "thinks is there, or re-clone the repository. A walk of zero Bots is a failure, not a "
            "clean run."
        )
        return 3

    emit(ink.bold("The Bots this repo proposes"))
    emit()
    emit(
        "  This repository MEASURES a Grok Bot deployment and PROPOSES Bots for it. It cannot\n"
        "  create a Bot: there is no public write API and no verified import format. Every screen\n"
        "  below ends in a charter you paste, by hand, into the Bot's description field in the\n"
        "  Grok Bot app — and a `verify` line naming the number that should change afterwards."
    )
    emit()
    emit(f"  templates  {doc['templates_dir']}")
    emit(f"  proposed   {doc['count']} Bot(s), walked in tier order")
    emit()
    total = doc["count"]
    for i, t in enumerate(doc["templates"], 1):
        render_bot(t, ink, n=i, total=total)
        if step and not _pause(i, total, interactive):
            return 0
    emit(ink.bold("next"))
    emit(
        "  gb-walk.py bots --paste <id> | pbcopy     the charter, clean, for the clipboard"
    )
    emit(
        "  gb triage                                  then measure what the new Bot changed"
    )
    return 0


def paste(doc: Dict[str, Any], template_id: str) -> int:
    """Print ONLY the charter. No header, no banner, no trailing newline beyond the charter's own.

    This is piped into `pbcopy`. Anything else on stdout ends up in the Bot's description field.
    """
    for t in doc["templates"]:
        if t.get("id") == template_id:
            sys.stdout.write(str(t.get("charter") or ""))
            if not str(t.get("charter") or "").endswith("\n"):
                sys.stdout.write("\n")
            return 0
    known = ", ".join(str(t.get("id")) for t in doc["templates"]) or "(none)"
    warn(f"no template with id {template_id!r}. Known ids: {known}")
    return 2


# ---------------------------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------------------------
_FIXTURE_BASE: Dict[str, Any] = {
    "schema": TEMPLATE_SCHEMA,
    "id": "placeholder",
    "name": "Placeholder",
    "tier": "A",
    "category": "Productivity",
    "job": "Do one narrow job.",
    "charter": "You are a narrow job Bot.",
    "charter_chars": len("You are a narrow job Bot."),
    "integrations": ["Gmail"],
    "routine": {
        "cadence": "daily",
        "when": "weekdays 07:30 MDT",
        "prompt": "Run the job.",
        "writes": "a memory shard",
    },
    "approval_boundary": "Draft only. Only Joshua's explicit yes on that exact draft sends it.",
    "memory": "the last run's summary",
    "verify": "`routines_with_runs` goes 0 -> 1",
    "why_this_shape": "the corpus median charter is 625 chars",
    "replaces": "Chief of Staff",
}


def _fixture(**overrides: Any) -> Dict[str, Any]:
    doc = json.loads(json.dumps(_FIXTURE_BASE))
    doc.update(overrides)
    if "charter" in overrides and "charter_chars" not in overrides:
        doc["charter_chars"] = len(overrides["charter"])
    return doc


def _capture(fn: "Any") -> Tuple[int, str, str]:
    """Run a renderer with BOTH streams captured. Returns (code, stdout, stderr).

    The known-bad legs below drive the REAL renderer, which is the point — a leg that tested a
    reimplementation of the failure message would pass while the shipped message rotted. Capturing
    stderr as well means the selftest's own output stays clean AND the remediation text becomes
    something the legs can assert on rather than something a human has to notice scrolling past.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fn()
    return code, out.getvalue(), err.getvalue()


def selftest() -> int:
    """Offline proofs on INLINE fixtures. No network, no dependence on templates/ existing.

    Every leg names the failure it prevents. Legs marked KNOWN-BAD feed the checker something
    wrong and require it to fire; the leg immediately around them is the positive control proving
    the check did not simply break everything.
    """
    legs: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    ink_off = Ink(False)
    assert ink_off.bold("x") == "x"

    with tempfile.TemporaryDirectory(prefix="gb-walk-selftest-") as tmp:
        root = pathlib.Path(tmp)

        # --- discovery, ordering, and the happy path -----------------------------------------
        good_dir = root / "good"
        good_dir.mkdir()
        atomic_write_json(good_dir / "beta-job.json", _fixture(id="beta-job", tier="B"))
        atomic_write_json(
            good_dir / "alpha-job.json", _fixture(id="alpha-job", tier="A")
        )
        atomic_write_json(
            good_dir / "chat-only.json",
            _fixture(
                id="chat-only",
                tier="C",
                integrations=[],
                approval_boundary="",
                replaces=None,
                routine={"cadence": "none", "when": "", "prompt": "", "writes": ""},
            ),
        )
        found, skipped, warnings = load_templates(good_dir)
        check(
            "discovers-every-template-file",
            len(found) == 3 and not skipped,
            f"expected 3 good / 0 skipped, got {len(found)}/{len(skipped)}: {skipped}",
        )
        check(
            "sorts-by-tier-then-id",
            [t["id"] for t in found] == ["alpha-job", "beta-job", "chat-only"],
            f"tier order is the walk's teaching order; got {[t['id'] for t in found]}",
        )
        check(
            "clean-corpus-warns-about-nothing",
            not warnings,
            f"a valid corpus must be silent; got {warnings}",
        )

        # --- KNOWN-BAD: structural failures must SKIP, and name what is wrong -----------------
        bad_dir = root / "bad"
        bad_dir.mkdir()
        no_charter = _fixture(id="no-charter")
        del no_charter["charter"]
        atomic_write_json(bad_dir / "no-charter.json", no_charter)
        atomic_write_json(
            bad_dir / "wrong-schema.json",
            _fixture(id="wrong-schema", schema="gb-template/99"),
        )
        atomic_write_json(
            bad_dir / "null-routine.json", _fixture(id="null-routine", routine=None)
        )
        atomic_write_json(bad_dir / "ok.json", _fixture(id="ok"))
        atomic_write_text(bad_dir / "SCHEMA.md", "# not a template\n")
        bad_found, bad_skipped, _bw = load_templates(bad_dir)
        check(
            "known-bad-missing-key-is-skipped-and-named",
            any("no-charter.json" in s and "charter" in s for s in bad_skipped),
            f"a template with no charter has nothing to paste; skips were {bad_skipped}",
        )
        check(
            "known-bad-wrong-schema-is-skipped",
            any(
                "wrong-schema.json" in s and "gb-template/99" in s for s in bad_skipped
            ),
            f"an unknown schema version must not be rendered as if understood; got {bad_skipped}",
        )
        check(
            "known-bad-null-routine-is-skipped",
            any("null-routine.json" in s and "routine" in s for s in bad_skipped),
            f"routine is never null in gb-template/1; got {bad_skipped}",
        )
        check(
            "positive-control-one-good-file-survives-three-bad-ones",
            [t["id"] for t in bad_found] == ["ok"],
            f"skipping must be per-file, not fatal; got {[t['id'] for t in bad_found]}",
        )
        check(
            "non-json-siblings-are-not-templates",
            not any("SCHEMA" in s for s in bad_skipped),
            "SCHEMA.md must not be reported as a broken template",
        )

        # --- KNOWN-BAD: consistency failures must WARN and still render -----------------------
        drift_dir = root / "drift"
        drift_dir.mkdir()
        atomic_write_json(
            drift_dir / "miscounted.json", _fixture(id="miscounted", charter_chars=1)
        )
        atomic_write_json(drift_dir / "renamed.json", _fixture(id="not-renamed"))
        extra = _fixture(id="extra-key")
        extra["notes"] = "this key was dropped from the frozen schema"
        atomic_write_json(drift_dir / "extra-key.json", extra)
        drift_found, drift_skipped, drift_warn = load_templates(drift_dir)
        check(
            "known-bad-charter_chars-mismatch-warns",
            any("charter_chars" in w and "miscounted" in w for w in drift_warn),
            f"a drifting generator must be named; warnings were {drift_warn}",
        )
        check(
            "known-bad-id-filename-mismatch-warns",
            any("filename stem" in w for w in drift_warn),
            f"--paste takes an id, so id/filename drift breaks the clipboard path; got {drift_warn}",
        )
        check(
            "known-bad-unknown-key-warns-and-names-it",
            any("notes" in w for w in drift_warn),
            f"an unrecognised key means the schema moved; got {drift_warn}",
        )
        check(
            "positive-control-consistency-problems-still-render",
            len(drift_found) == 3 and not drift_skipped,
            f"a cosmetic mismatch must not hide a Bot; got {len(drift_found)} / {drift_skipped}",
        )

        # --- the empty and absent tours are FAILURES ------------------------------------------
        empty_dir = root / "empty"
        empty_dir.mkdir()
        empty_doc = walk_bots(directory=empty_dir, looked=[str(empty_dir)])
        empty_code, empty_out, empty_err = _capture(
            lambda: render_bots(empty_doc, ink_off, step=False, interactive=False)
        )
        check(
            "known-bad-empty-templates-dir-exits-nonzero",
            empty_code != 0,
            "an empty corpus rendered as a clean walk is the exact lie this leg exists to stop",
        )
        check(
            "known-bad-empty-tour-prints-no-bot-screens",
            "CHARTER" not in empty_out,
            f"a failed tour must not also emit a partial one; stdout was {empty_out!r}",
        )
        check(
            "empty-tour-says-a-walk-of-zero-is-a-failure",
            "failure, not a clean run" in empty_err and "gb-templates.py" in empty_err,
            f"the message has to say so in words, not just in the exit code; got {empty_err!r}",
        )
        absent_doc = walk_bots(directory=None, looked=[str(root / "nope")])
        absent_code, _absent_out, absent_err = _capture(
            lambda: render_bots(absent_doc, ink_off, step=False, interactive=False)
        )
        check(
            "known-bad-absent-templates-dir-exits-nonzero",
            absent_code == 3,
            f"a missing templates/ is an ENVIRONMENT failure (3); got {absent_code}",
        )
        check(
            "absent-tour-names-where-it-looked-and-how-to-fix-it",
            str(root / "nope") in absent_err
            and "--templates" in absent_err
            and "GB_TEMPLATES" in absent_err,
            f"'not found' has to say where it looked AND both overrides; got {absent_err!r}",
        )
        check(
            "absent-tour-records-the-search-path-in-the-envelope",
            absent_doc["looked_in"] == [str(root / "nope")],
            f"the JSON consumer needs it too; got {absent_doc['looked_in']}",
        )

        # --- an explicit templates path is authoritative and must NOT fall back --------------
        # This is the defect that motivated the legs: `--templates /does/not/exist` walked the
        # repository's own corpus and exited 0. A scheduler with a typo would have toured the
        # wrong Bots and reported success.
        saved_env = os.environ.pop("GB_TEMPLATES", None)
        try:
            bad_flag_dir, bad_flag_looked = resolve_templates(str(root / "nowhere"))
            check(
                "known-bad-explicit-templates-flag-does-not-fall-back",
                bad_flag_dir is None and len(bad_flag_looked) == 1,
                f"an explicit path is a statement, not a hint; got {bad_flag_dir} after looking "
                f"in {bad_flag_looked}",
            )
            check(
                "explicit-failure-names-which-input-said-so",
                "--templates" in bad_flag_looked[0],
                f"the reader must know whether the flag or the env var was wrong; got "
                f"{bad_flag_looked}",
            )
            good_flag_dir, _gl = resolve_templates(str(good_dir))
            check(
                "positive-control-an-explicit-path-that-exists-is-used",
                good_flag_dir == good_dir,
                f"authoritative must not mean broken; got {good_flag_dir}",
            )
            os.environ["GB_TEMPLATES"] = str(root / "also-nowhere")
            bad_env_dir, bad_env_looked = resolve_templates(None)
            check(
                "known-bad-GB_TEMPLATES-does-not-fall-back-either",
                bad_env_dir is None
                and len(bad_env_looked) == 1
                and "GB_TEMPLATES" in bad_env_looked[0],
                f"got {bad_env_dir} after looking in {bad_env_looked}",
            )
            os.environ["GB_TEMPLATES"] = str(good_dir)
            env_dir, _el = resolve_templates(None)
            check(
                "positive-control-GB_TEMPLATES-that-exists-is-used",
                env_dir == good_dir,
                f"got {env_dir}",
            )
            # env still points at good_dir here; the flag names the drift corpus and must win.
            check(
                "flag-beats-env-var",
                resolve_templates(str(drift_dir))[0] == drift_dir,
                f"--templates is more specific than the environment and must win; got "
                f"{resolve_templates(str(drift_dir))[0]}",
            )
        finally:
            if saved_env is None:
                os.environ.pop("GB_TEMPLATES", None)
            else:
                os.environ["GB_TEMPLATES"] = saved_env

        # --- --paste is byte-exact and nothing else -------------------------------------------
        paste_dir = root / "paste"
        paste_dir.mkdir()
        charter = "Line one.\nLine two, with  double  spaces and a trailing tab.\t"
        atomic_write_json(paste_dir / "p.json", _fixture(id="p", charter=charter))
        paste_doc = walk_bots(directory=paste_dir, looked=[str(paste_dir)])
        paste_code, paste_out, _pe = _capture(lambda: paste(paste_doc, "p"))
        check(
            "paste-emits-the-charter-verbatim-and-only-that",
            paste_code == 0 and paste_out == charter + "\n",
            f"this is piped into pbcopy; any banner lands in the Bot. got {paste_out!r}",
        )
        missing_code, missing_out, missing_err = _capture(
            lambda: paste(paste_doc, "no-such-id")
        )
        check(
            "known-bad-paste-unknown-id-exits-2-and-prints-nothing-to-stdout",
            missing_code == 2 and missing_out == "",
            f"a typo must not pipe an empty charter into the clipboard; got "
            f"{missing_code}/{missing_out!r}",
        )
        check(
            "known-bad-paste-unknown-id-lists-the-ids-it-does-know",
            "'no-such-id'" in missing_err and "p" in missing_err,
            f"a typo is recoverable only if the alternatives are named; got {missing_err!r}",
        )

        # --- routine rendering ----------------------------------------------------------------
        check(
            "cadence-none-renders-as-a-real-answer",
            routine_line({"cadence": "none", "when": "", "prompt": "", "writes": ""})
            == "no routine (chat-invoked — see WHY THIS SHAPE)",
            f"got {routine_line({'cadence': 'none'})!r}",
        )
        check(
            "cadence-daily-renders-its-when",
            routine_line({"cadence": "daily", "when": "weekdays 07:30 MDT"})
            == "daily · weekdays 07:30 MDT",
            f"got {routine_line({'cadence': 'daily', 'when': 'weekdays 07:30 MDT'})!r}",
        )

        # --- the CLI track's classification and reconciliation --------------------------------
        verbs = annotated_verbs()
        check(
            "every-annotated-verb-has-at-least-one-demo",
            all(v.demos for v in verbs.values()),
            f"a verb with no command shown teaches nothing: "
            f"{[n for n, v in verbs.items() if not v.demos]}",
        )
        modes = {
            (v.name, " ".join(d.argv)): d.mode for v in verbs.values() for d in v.demos
        }
        check(
            "bare-doctor-is-would-run",
            modes.get(("doctor", "doctor")) == "would-run",
            "MEASURED: bare `gb doctor` probes MCP over the network and writes mcp/<stamp>.json",
        )
        check(
            "bare-setup-is-would-run",
            modes.get(("setup", "setup")) == "would-run",
            "MEASURED: `gb setup` writes nothing but its `network` row GETs docs.x.ai. A tour "
            "that reaches the internet for you is not read-only, however harmless the request",
        )
        check(
            "bare-monitor-is-would-run",
            modes.get(("monitor", "monitor")) == "would-run",
            "MEASURED by manifesting the tree before and after a tour: `gb monitor` WRITES "
            "monitor/<stamp>.json, despite a description that reads as a pure read",
        )
        # THE INVARIANT, not four separate facts: every verb MEASURED to write an artifact,
        # reach the network, or run long is barred from `run` mode. New verbs get added to this
        # tuple when they are measured, and the positive control below keeps the tuple from
        # simply swallowing the whole tour.
        MUTATORS = ("setup", "mine", "monitor", "robot-docs", "completion")
        leaked = [k for k, m in modes.items() if m == "run" and k[0] in MUTATORS]
        check(
            "no-run-mode-demo-is-a-measured-mutator",
            not leaked,
            f"a tour that writes, fetches, or floods is not a tour; leaked {leaked}",
        )
        check(
            "positive-control-the-tour-still-runs-something",
            sum(1 for m in modes.values() if m == "run") >= 10,
            f"if the guard above barred everything the tour would be a list of commands; only "
            f"{sum(1 for m in modes.values() if m == 'run')} demos run live",
        )
        check(
            "scoped-doctor-is-run",
            modes.get(("doctor", "doctor --scope plugin")) == "run",
            "positive control: the scoped read must still be demonstrated live",
        )
        check(
            "every-apply-and-mine-is-would-run",
            all(
                mode == "would-run"
                for (verb, argv), mode in modes.items()
                if "--apply" in argv or verb == "mine"
            ),
            f"a tour that writes is not a tour: "
            f"{[k for k, m in modes.items() if ('--apply' in k[1] or k[0] == 'mine') and m != 'would-run']}",
        )
        check(
            "would-run-always-explains-itself",
            all(
                d.why_not.strip()
                for v in verbs.values()
                for d in v.demos
                if d.mode == "would-run"
            ),
            "'[would run]' with no reason is a refusal the reader cannot check",
        )
        check(
            "setup-comes-before-triage-before-doctor-before-why-before-repair",
            _stop_order(("setup", "triage", "doctor", "why", "repair")),
            "the spine order is the whole reason this is a walk and not `gb --help`",
        )
        # KNOWN-BAD reconciliation, using the REAL measured case: the public mirror had 18 verbs
        # while this checkout had 19.
        unannotated, absent = reconcile(sorted(verbs), sorted(set(verbs) - {"monitor"}))
        check(
            "known-bad-verb-missing-from-an-older-gb-is-noted",
            absent == ["monitor"] and unannotated == [],
            f"measured 2026-09-11: the mirror shipped 18 verbs, this checkout 19. got "
            f"{absent}/{unannotated}",
        )
        unannotated2, absent2 = reconcile(
            sorted(verbs), sorted(set(verbs) | {"brand-new"})
        )
        check(
            "known-bad-verb-this-tour-cannot-explain-is-noted",
            unannotated2 == ["brand-new"] and absent2 == [],
            f"a newer gb must not be silently under-toured; got {unannotated2}/{absent2}",
        )
        check(
            "no-gb-means-no-invented-diff",
            reconcile(sorted(verbs), None) == ([], []),
            "an unreadable capabilities file must not produce a 19-verb 'absent' list",
        )

        # --- rendering: no escapes when colour is off, no blocking when not a tty -------------
        cli_doc = walk_cli(gb=None, cwd=root, caps=None, max_lines=DEFAULT_MAX_LINES)
        cli_code, painted, cli_err = _capture(
            lambda: render_cli(cli_doc, ink_off, step=True, interactive=False)
        )
        check(
            "known-bad-no-gb-exits-3-but-still-prints-the-tour",
            cli_code == 3 and "triage" in painted and "would run" in painted,
            f"the annotations are durable; the demos are not. got exit {cli_code}, "
            f"{len(painted)} chars",
        )
        check(
            "known-bad-no-gb-names-all-three-ways-to-get-one",
            "pip install" in cli_err and "install.sh" in cli_err and "--gb" in cli_err,
            f"a tour with nothing to demonstrate must hand back every route out; got {cli_err!r}",
        )
        check(
            "step-does-not-block-when-stdin-is-not-a-tty",
            "--- step 1/" in painted,
            "a paging flag that hangs a pipe is a deadlock, not a reading aid",
        )
        check(
            "no-ansi-escapes-when-colour-is-off",
            "\033[" not in painted,
            "NO_COLOR, CI and TERM=dumb each veto colour; this output is piped by default",
        )
        check(
            "colour-vetoed-by-NO_COLOR-even-on-a-tty",
            not _colour_with_env({"NO_COLOR": "1", "TERM": "xterm"}, tty=True)
            and not _colour_with_env({"CI": "true", "TERM": "xterm"}, tty=True)
            and not _colour_with_env({"TERM": "dumb"}, tty=True),
            "each veto must hold independently",
        )
        check(
            "positive-control-colour-on-for-a-plain-tty",
            _colour_with_env({"TERM": "xterm-256color"}, tty=True),
            "if every environment vetoes colour the veto proves nothing",
        )

        # --- output clipping is honest --------------------------------------------------------
        kept, withheld = _clip("\n".join(str(i) for i in range(40)), 14)
        check(
            "clipping-reports-what-it-withheld",
            len(kept) == 14 and withheld == 26,
            f"got {len(kept)}/{withheld}",
        )
        kept2, withheld2 = _clip("short", 14)
        check(
            "positive-control-short-output-is-not-clipped",
            kept2 == ["short"] and withheld2 == 0,
            f"got {kept2}/{withheld2}",
        )

        # --- the bots JSON envelope -----------------------------------------------------------
        json_doc = walk_bots(directory=good_dir, looked=[str(good_dir)])
        check(
            "bots-json-envelope-is-complete-and-round-trips",
            json.loads(json.dumps(json_doc, default=str))["count"] == 3
            and json_doc["schema"] == SCHEMA
            and json_doc["track"] == "bots",
            f"got {sorted(json_doc)}",
        )
        check(
            "cli-json-envelope-declares-what-it-could-not-measure",
            cli_doc["gb"] is None
            and cli_doc["verbs_live"] is None
            and cli_doc["verbs_annotated"] == len(verbs),
            f"null is the honest answer for an unmeasured field; got {cli_doc['verbs_live']!r}",
        )

    passed = sum(1 for _, ok, _ in legs if ok)
    for name, ok, detail in legs:
        if not ok:
            warn(f"FAIL {name}: {detail}")
    if passed != len(legs):
        warn(f"SELFTEST FAIL - {passed}/{len(legs)}")
        return 1
    emit(f"SELFTEST PASS - {passed}/{len(legs)}")
    return 0


def _stop_order(spine: Sequence[str]) -> bool:
    """True when `spine` appears in the tour in exactly that relative order."""
    order = [v.name for s in STOPS for v in s.verbs]
    positions = [order.index(name) for name in spine if name in order]
    return len(positions) == len(spine) and positions == sorted(positions)


def _colour_with_env(env: Dict[str, str], *, tty: bool) -> bool:
    """`_colour_enabled` against a planted environment and a planted isatty."""

    class _Stream:
        def isatty(self) -> bool:
            return tty

    saved = {k: os.environ.get(k) for k in ("NO_COLOR", "CI", "TERM")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        os.environ.update(env)
        return _colour_enabled(_Stream())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="gb-walk.py",
        description="The guided tour: `cli` walks the tool, `bots` walks the Bots this repo proposes.",
        epilog="start with:  gb-walk.py cli        then:  gb-walk.py bots",
    )
    ap.add_argument(
        "track",
        nargs="?",
        choices=("cli", "bots"),
        help="cli: the tool, in the order you need it. bots: the proposed Bot templates.",
    )
    ap.add_argument(
        "--json", action="store_true", help="machine-readable envelope on stdout"
    )
    ap.add_argument(
        "--step",
        action="store_true",
        help="one item at a time. Prompts at a terminal; prints a boundary and continues in a pipe.",
    )
    ap.add_argument(
        "--paste",
        metavar="ID",
        help="bots track: print ONLY that template's charter, for `| pbcopy`",
    )
    ap.add_argument(
        "--templates", metavar="DIR", help="where the gb-template/1 files live"
    )
    ap.add_argument(
        "--gb",
        metavar="PATH",
        help="the `gb` to demonstrate (default: sibling, then PATH)",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="do not clip demo output (default: first %d lines)" % DEFAULT_MAX_LINES,
    )
    ap.add_argument(
        "--selftest", action="store_true", help="offline proofs; writes nothing"
    )
    return ap


def body(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return selftest()
    if args.track is None:
        ap.print_help()
        return 2

    ink = Ink(_colour_enabled(sys.stdout) and not args.json)
    interactive = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False

    if args.track == "bots":
        directory, looked = resolve_templates(args.templates)
        doc = walk_bots(directory=directory, looked=looked)
        if args.paste:
            if directory is None:
                warn("no templates/ directory found; nothing to paste. Looked in:")
                for p in looked:
                    warn(f"  {p}")
                return 3
            return paste(doc, args.paste)
        if args.json:
            print(json.dumps(doc, indent=1, default=str))
            return 0 if doc["count"] else 3
        return render_bots(doc, ink, step=args.step, interactive=interactive)

    cwd = pathlib.Path.cwd()
    gb = resolve_gb(args.gb)
    caps = live_capabilities(gb, cwd)
    doc = walk_cli(
        gb=gb,
        cwd=cwd,
        caps=caps,
        max_lines=0 if args.full else DEFAULT_MAX_LINES,
    )
    if args.json:
        print(json.dumps(doc, indent=1, default=str))
        return 0 if gb is not None else 3
    return render_cli(doc, ink, step=args.step, interactive=interactive)


if __name__ == "__main__":
    gb_main(body)
