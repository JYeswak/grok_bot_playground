#!/usr/bin/env python3
"""gb-goldens — capture the CLI's observable behaviour as a hash, so a refactor can prove itself.

WHY THIS EXISTS. A simplification pass is only allowed to remove lines if behaviour is provably
identical afterwards. "The tests still pass" is not that proof: the gate suite checks the
FIXTURES, not the CLI's own surface, and nothing in this repo currently pins what
`gb <verb> --json` actually emits. Without a golden, a refactor that quietly drops a field,
reorders a list, or changes an exit code looks exactly like a clean one.

WHAT IS A GOLDEN HERE. For every read-only verb: its exit code, its stderr-emptiness, and a
sha256 over its stdout with the VOLATILE fields removed. Volatility is declared, not guessed:

  measured_at / recorded_at / captured_at / ts   wall-clock stamps, different every run
  elapsed_s / *_seconds                          timings, different every run
  runtime_sha                                    the commit, which a refactor changes by design

Everything else is contract. If a field is volatile and NOT in that list, this tool will show it
as a spurious diff — which is the correct failure: an undeclared source of nondeterminism is a
finding about the CLI, not noise to suppress.

WHAT IT DELIBERATELY DOES NOT CAPTURE. Mutating verbs (`repair --apply`, `setup --apply`) and
anything that reaches the vendor. A golden that performs writes or network I/O would make the
proof instrument itself a source of drift.

USAGE
    gb-goldens.py --capture          write the baseline (before the refactor)
    gb-goldens.py --verify           compare the current CLI against it (after each commit)
    gb-goldens.py --verify --json    the same, machine-readable

exit: 0 identical · 1 a golden differs · 2 usage · 3 no baseline to verify against
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import arg, parse  # noqa: E402
from gbtypes import atomic_write_json, main, read_json_capped, run  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "refactor" / "goldens.json"

# Read-only verbs only. Each entry is (label, argv-after-`gb`).
# NOT a subject: `audit`. Its entire job is to report the most recent commits, so its output
# changes every time this repo is committed to — including by the refactor being verified.
# Masking the shas would leave a golden that asserts almost nothing; keeping it would make the
# instrument cry drift at its own operator. A verb whose contract IS to change is not a golden.
SUBJECTS: Tuple[Tuple[str, List[str]], ...] = (
    ("capabilities", ["capabilities"]),
    ("info", ["info"]),
    ("platform", ["platform", "--json"]),
    ("triage", ["triage", "--json"]),
    ("health", ["health", "--json"]),
    ("work", ["work", "--json"]),
    ("doctor-gate", ["doctor", "--scope", "gate", "--json"]),
    ("validate-plugin", ["validate", "plugin", "--json"]),
    ("why-g8", ["why", "g8-desktop-inventory", "--json"]),
    ("repair-dry", ["repair", "--scope", "fixtures", "--json"]),
    ("quickstart", ["quickstart"]),
    ("examples", ["examples"]),
    ("robot-docs", ["robot-docs"]),
    ("help-exit-codes", ["help", "exit-codes"]),
    ("completion-bash", ["completion", "bash"]),
)

# Declared volatility. A key here is erased before hashing; anything else is contract.
VOLATILE_KEYS = frozenset(
    {
        "measured_at",
        "recorded_at",
        "captured_at",
        "reviewed_at",
        "ts",
        "timestamp",
        "elapsed_s",
        "runtime_sha",
        "wall_seconds",
    }
)
VOLATILE_TEXT = (
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"),
    re.compile(r"\b\d+\.\d{2,}s\b"),
)


@dataclasses.dataclass(frozen=True)
class Args:
    """Capture or verify the CLI's observable behaviour."""

    capture: bool = arg(help="write the baseline")
    verify: bool = arg(help="compare the current CLI against the baseline")
    json: bool = arg(help="machine-readable envelope on stdout")


def _strip(node: Any) -> Any:
    """Erase declared-volatile fields, recursively, so the hash is over contract only."""
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in sorted(node.items()) if k not in VOLATILE_KEYS}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def _canon(out: str) -> str:
    """Canonical form of one verb's stdout: parsed and volatility-stripped when it is JSON,
    volatility-masked text otherwise. JSON is re-serialised with sorted keys so a key-order
    change in the producer is not reported as a behaviour change — key order is not contract,
    key PRESENCE is."""
    try:
        return json.dumps(_strip(json.loads(out)), sort_keys=True, indent=None)
    except ValueError:
        masked = out
        for rx in VOLATILE_TEXT:
            masked = rx.sub("<volatile>", masked)
        return masked


def observe() -> Dict[str, Dict[str, Any]]:
    gb = ROOT / "bin" / "gb"
    seen: Dict[str, Dict[str, Any]] = {}
    for label, argv in SUBJECTS:
        proc = run([sys.executable, str(gb), *argv], timeout_s=600, cwd=ROOT)
        canon = _canon(proc.out)
        seen[label] = {
            "argv": argv,
            "exit": proc.code,
            "stderr_empty": not proc.err.strip(),
            "sha256": hashlib.sha256(canon.encode()).hexdigest(),
            "bytes": len(canon),
        }
    return seen


def body() -> int:
    a = parse(Args)
    if a.capture == a.verify:
        print(
            "gb-goldens: choose exactly one of --capture or --verify\n"
            "    try: gb-goldens.py --capture",
            file=sys.stderr,
        )
        return 2

    if a.capture:
        doc = {
            "schema": "gb-goldens/1",
            "captured_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "volatile_keys": sorted(VOLATILE_KEYS),
            "subjects": observe(),
        }
        atomic_write_json(GOLDEN, doc)
        print(
            f"captured {len(doc['subjects'])} golden(s) -> {GOLDEN.relative_to(ROOT)}"
        )
        return 0

    base = read_json_capped(GOLDEN)
    if not base:
        print(
            f"gb-goldens: no baseline at {GOLDEN.relative_to(ROOT)} — nothing to verify "
            f"against.\n    capture one first: python3 bin/gb-goldens.py --capture",
            file=sys.stderr,
        )
        return 3

    old, new = base.get("subjects") or {}, observe()
    drifted: List[Dict[str, Any]] = []
    for label in sorted(set(old) | set(new)):
        o, n = old.get(label), new.get(label)
        if o is None:
            drifted.append(
                {"subject": label, "how": "new subject, absent from the baseline"}
            )
        elif n is None:
            drifted.append(
                {"subject": label, "how": "subject disappeared from the CLI"}
            )
        elif o != n:
            how = [
                k for k in ("exit", "sha256", "stderr_empty") if o.get(k) != n.get(k)
            ]
            drifted.append(
                {
                    "subject": label,
                    "how": ", ".join(how),
                    "before": {k: o.get(k) for k in how},
                    "after": {k: n.get(k) for k in how},
                }
            )

    if a.json:
        print(
            json.dumps(
                {
                    "schema": "gb-goldens-verify/1",
                    "checked": len(new),
                    "drifted": drifted,
                    "identical": not drifted,
                },
                indent=1,
            )
        )
    else:
        for d in drifted:
            print(f"  DRIFT  {d['subject']:<18}{d['how']}")
        print(
            f"{len(new) - len(drifted)}/{len(new)} golden(s) identical"
            + (
                ""
                if not drifted
                else " — a refactor that changes these is not isomorphic"
            )
        )
    return 1 if drifted else 0


if __name__ == "__main__":
    main(body)
