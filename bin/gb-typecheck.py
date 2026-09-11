#!/usr/bin/env python3
"""gb-typecheck — measure how much of this repo is strictly typed, and refuse to let it shrink.

Two independent checkers (mypy --strict, pyright) over a RATCHETED file list. The list is a
floor, not a config: a file enters it the day it first checks clean under BOTH, and nothing may
leave. A type annotation nobody verifies is a comment, and a comment cannot make the Rust port
mechanical — which is the entire reason these types exist.

Two checkers rather than one on purpose, and for the reason this repo trusts two of anything:
they disagree. `parse(int, ...)` was accepted by neither, but only mypy caught the `__exit__`
return type and only pyright phrased the dataclass bound in terms of the missing attribute. A
single checker is a single opinion about what "typed" means.

Writes `typecheck/<date>.json`. The gate (g23) reads that artifact — this script measures, the
gate judges, and neither does the other's job.

exit: 0 floor holds · 1 a floor file has errors or went missing · 2 a checker could not run
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import pathlib
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import arg, parse  # noqa: E402
from gbtypes import Proc, atomic_write_json, main, read_json_capped, run  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLOOR_PATH = ROOT / "types-floor.json"
CHECK_TIMEOUT_S = 180.0


@dataclasses.dataclass(frozen=True)
class Args:
    """Measure strict-typing coverage against the ratcheted floor."""

    add: Tuple[str, ...] = arg(help="file(s) to admit to the floor if they check clean")
    dry_run: bool = arg(help="print the verdict, write no artifact")
    json_out: bool = arg(help="print the artifact to stdout instead of a summary")


@dataclasses.dataclass(frozen=True)
class ToolRun:
    """One checker's verdict over the floor."""

    tool: str
    version: str
    errors: Tuple[str, ...]
    ran: bool
    detail: str

    def to_json(self) -> Dict[str, object]:
        return {
            "tool": self.tool,
            "version": self.version,
            "ran": self.ran,
            "error_count": len(self.errors),
            "errors": list(self.errors[:40]),
            "detail": self.detail,
        }


def _tool_version(binary: str) -> Optional[str]:
    proc = run([binary, "--version"], timeout_s=30)
    return proc.out.strip().splitlines()[0] if proc.ok and proc.out.strip() else None


def _parse_errors(proc: Proc, marker: str) -> Tuple[str, ...]:
    """Lines a human would call an error. Both tools put `error` in them; neither guarantees an
    exit code that distinguishes 'no errors' from 'could not run', which is why `ran` is tracked
    separately and an unparseable run is never counted as a clean one."""
    return tuple(ln.strip() for ln in proc.out.splitlines() if marker in ln)


def check_mypy(files: List[pathlib.Path]) -> ToolRun:
    version = _tool_version("mypy")
    if version is None:
        return ToolRun(
            "mypy",
            "",
            (),
            False,
            "mypy is not installed — coverage is UNMEASURED, not clean",
        )
    proc = run(
        ["mypy", "--strict", "--ignore-missing-imports", *[str(f) for f in files]],
        timeout_s=CHECK_TIMEOUT_S,
        cwd=ROOT,
    )
    if proc.timed_out:
        return ToolRun(
            "mypy", version, (), False, f"timed out after {CHECK_TIMEOUT_S}s"
        )
    return ToolRun(
        "mypy",
        version,
        _parse_errors(proc, ": error:"),
        True,
        f"{len(files)} file(s) under --strict",
    )


def check_pyright(files: List[pathlib.Path]) -> ToolRun:
    version = _tool_version("pyright")
    if version is None:
        return ToolRun(
            "pyright",
            "",
            (),
            False,
            "pyright is not installed — coverage is UNMEASURED, not clean",
        )
    proc = run(
        ["pyright", *[str(f) for f in files]], timeout_s=CHECK_TIMEOUT_S, cwd=ROOT
    )
    if proc.timed_out:
        return ToolRun(
            "pyright", version, (), False, f"timed out after {CHECK_TIMEOUT_S}s"
        )
    return ToolRun(
        "pyright",
        version,
        _parse_errors(proc, "- error:"),
        True,
        f"{len(files)} file(s)",
    )


def load_floor() -> List[str]:
    doc = read_json_capped(FLOOR_PATH) or {}
    rows = doc.get("files") if isinstance(doc, dict) else None
    return sorted(str(r) for r in rows) if isinstance(rows, list) else []


def measure(
    files: List[pathlib.Path], floor: List[str], missing: List[str]
) -> Tuple[Dict[str, object], List[ToolRun]]:
    """Run both checkers and build the artifact. Pure with respect to the filesystem — nothing
    is written here, so a caller can measure without persisting (that is what `--dry-run` is)."""
    runs = [check_mypy(files), check_pyright(files)]
    doc: Dict[str, object] = {
        "schema": "gb-typecheck/1",
        "measured_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "floor_size": len(floor),
        "checked": [str(f.relative_to(ROOT)) for f in files],
        "missing_from_disk": missing,
        "runs": [r.to_json() for r in runs],
        "clean": all(r.ran and not r.errors for r in runs) and not missing,
    }
    return doc, runs


def persist(doc: Dict[str, object], floor: List[str], admitted: List[str]) -> None:
    """Write the artifact, and grow the floor if this run earned it. Both writes are atomic, so
    an interrupted run cannot leave a half-floor that silently lowers the bar."""
    atomic_write_json(ROOT / "typecheck" / f"{dt.date.today().isoformat()}.json", doc)
    if admitted:
        atomic_write_json(
            FLOOR_PATH,
            {
                "schema": "gb-types-floor/1",
                "why": "Files proven clean under BOTH mypy --strict and pyright. A ratchet: entries "
                "are added the day they first pass and are never removed — a file that "
                "regresses is a RED gate, not a smaller floor.",
                "files": sorted(set(floor) | set(admitted)),
            },
        )


def report(
    doc: Dict[str, object],
    runs: List[ToolRun],
    floor: List[str],
    admitted: List[str],
    missing: List[str],
    as_json: bool,
) -> None:
    """Print for a human, or the artifact for a machine. One or the other, never a mix."""
    if as_json:
        import json

        print(json.dumps(doc, indent=1))
        return
    for r in runs:
        state = (
            "clean"
            if (r.ran and not r.errors)
            else ("DID NOT RUN" if not r.ran else f"{len(r.errors)} error(s)")
        )
        print(f"  {r.tool:<8} {state:<14} {r.detail}")
    if missing:
        print(
            f"  MISSING   {len(missing)} floor file(s) no longer on disk: {', '.join(missing[:5])}"
        )
    if admitted:
        print(
            f"  admitted  {len(admitted)} new file(s) to the floor: {', '.join(admitted)}"
        )
    print(
        f"typecheck {'CLEAN' if doc['clean'] else 'DIRTY'} — floor {len(floor)} -> {len(set(floor) | set(admitted))}"
    )


def body() -> int:
    args = parse(Args)
    floor = load_floor()
    candidates = sorted(set(floor) | set(args.add))
    missing = [f for f in candidates if not (ROOT / f).is_file()]
    files = [ROOT / f for f in candidates if (ROOT / f).is_file()]
    if not files:
        print(
            "types-floor.json names no existing file — an empty floor is not a clean repo, "
            "it is an unmeasured one",
            file=sys.stderr,
        )
        return 1

    doc, runs = measure(files, floor, missing)
    unran = [r for r in runs if not r.ran]
    errors = {r.tool: r.errors for r in runs if r.errors}
    admitted = (
        [f for f in args.add if f not in floor and (ROOT / f).is_file()]
        if args.add and not errors and not unran
        else []
    )

    if not args.dry_run:
        persist(doc, floor, admitted)
    report(doc, runs, floor, admitted, missing, args.json_out)

    for tool, errs in errors.items():
        for e in list(errs)[:10]:
            print(f"  [{tool}] {e}", file=sys.stderr)
    if unran:
        return 2
    return 0 if doc["clean"] else 1


if __name__ == "__main__":
    main(body)
