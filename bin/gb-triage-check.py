#!/usr/bin/env python3
"""gb-triage-check — one gate check, untruncated: verdict, evidence, and the fix.

WHY THIS EXISTS. `gb triage` deliberately truncates: detail to 200 chars in JSON
and 80 on the human line, because it renders up to 27 rows. That is the right
shape for "what is wrong", and the wrong shape for "what do I do about this
one". This producer answers the second question for a single check, with three
things the board view cannot carry: the FULL detail text, the artifacts the
verdict was read from (resolved against the root, newest first, absences named),
and exactly one fix — a command to run, or a HUMAN line when the repair needs a
person (a client to update, a description to write, a decision to record).

THIN BY DESIGN. Nothing here reimplements the gate. The verdict and detail come
from `bin/gb-surface-gate.py --json`; this module selects one row, resolves the
evidence patterns that row's check function reads, and prints the fix its
verdict class calls for. Session/auth reuse is vacuous: the gate takes no
credentials, so neither does this.

EXIT CODES (gb's own dictionary, so the verb can relay them unchanged):
    0   OK           the check printed and is GREEN
    1   FINDINGS     the check printed and is RED or ERROR
    2   USAGE        unknown check id (stderr lists all 25)
    3   ENVIRONMENT  the gate produced no output, or it did not parse
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

PRODUCER = "bin/gb-surface-gate.py"
SCHEMA = "gb-triage-check/1"
GATE_TIMEOUT_S = 180

CHECK_ORDER: Tuple[str, ...] = (
    "g1-surface-fetch-integrity",
    "g2-surface-canary",
    "g3-snapshot-freshness",
    "g4-client-update-applied",
    "g5-bot-charter-present",
    "g6-capability-delta-reviewed",
    "g7-desktop-parity",
    "g8-desktop-inventory",
    "g9-routine-health",
    "g10-loop-scheduled",
    "g11-tunable-delta-reviewed",
    "g12-ondemand-spend-bounded",
    "g13-practitioner-index-reviewed",
    "g14-coverage-ratchet",
    "g15-market-delta-reviewed",
    "g16-doc-delta-reviewed",
    "g17-discovery-triaged",
    "g18-mcp-surface-healthy",
    "g19-fleet-earning-its-keep",
    "g20-context-archived",
    "g21-usecase-corpus-reviewed",
    "g22-durable-io",
    "g23-types-ratcheted",
    "g24-cli-contract",
    "g25-routine-liveness",
    "g26-jobs-proof-calls",
    "g27-surface-drift",
)

PULL = "python3 bin/gb-pull-inventory.py --device <label>"
AUDIT = "python3 bin/gb-deployment-audit.py"
SNAP = "python3 bin/gb-surface-snapshot.py"

# Per check: the artifact patterns its gate function reads (globbed against the
# root; see each gN_* function in gb-surface-gate.py), the re-measure command
# for an ERROR (unmeasured — the fix is to measure), and the repair for a RED
# (measured and bad — a command, or a HUMAN line when a person must act).
CHECKS: Dict[str, Dict[str, Any]] = {
    "g1-surface-fetch-integrity": {
        "reads": ["surface/*/manifest.json", "surface/*/pages"],
        "error_fix": SNAP,
        "red_fix": SNAP,
    },
    "g2-surface-canary": {
        "reads": ["surface/*/manifest.json"],
        "error_fix": SNAP,
        "red_fix": "HUMAN: the index collapsed — confirm by hand whether the fetcher or the doc site changed, then re-fetch with "
        + SNAP,
    },
    "g3-snapshot-freshness": {
        "reads": ["surface/*"],
        "error_fix": SNAP,
        "red_fix": "bin/gb-weekly.sh",
    },
    "g4-client-update-applied": {
        "reads": ["deployment/*.json"],
        "error_fix": AUDIT,
        "red_fix": "HUMAN: the running client is behind the staged update — install it in the Grok Bot app, then re-run "
        + AUDIT,
    },
    "g5-bot-charter-present": {
        "reads": ["deployment/*.json", "inventory/*.json"],
        "error_fix": AUDIT,
        "red_fix": "HUMAN: every Bot needs a standing description — add it in the app for the named Bot(s)",
    },
    "g6-capability-delta-reviewed": {
        "reads": ["deployment/*.json", "findings/*.md"],
        "error_fix": AUDIT,
        "red_fix": "HUMAN: the vendor moved capabilities — record the review in findings/<snapshot-date>.md with a `reviewed-audit: <audit>.json` line",
    },
    "g7-desktop-parity": {
        "reads": ["desktops.json", "deployment/*.json", "deployment/desktops/*.json"],
        "error_fix": AUDIT,
        "red_fix": "HUMAN: bring the named desktop to the host-of-record client version, or audit it with bin/gb-audit-remote.sh <label> if it was never measured",
    },
    "g8-desktop-inventory": {
        "reads": ["desktops.json", "inventory/*.json"],
        "error_fix": "python3 bin/gb-record-inventory.py --device <label> --template",
        "red_fix": "HUMAN: re-confirm local_exec_policy in Settings on the named desktop and record it with python3 bin/gb-record-inventory.py --device <label>",
    },
    "g9-routine-health": {
        "reads": ["inventory/*.json", "pauses-acknowledged.json"],
        "error_fix": PULL,
        "red_fix": "HUMAN: resume it, delete it, or acknowledge the pause with a review_by date in pauses-acknowledged.json",
    },
    "g10-loop-scheduled": {
        "reads": ["schedule/runs.jsonl", "schedule/agent.json"],
        "error_fix": "bin/gb-install-schedule.sh --install",
        "red_fix": "HUMAN: re-register with bin/gb-install-schedule.sh --install, or check launchd if the scheduler is registered but not firing",
    },
    "g11-tunable-delta-reviewed": {
        "reads": ["deployment/*.json", "findings/*.md"],
        "error_fix": AUDIT,
        "red_fix": "HUMAN: read the moved parameters with python3 bin/gb-capabilities.py --tunables and record the review in findings/<snapshot-date>.md with a `reviewed-audit: <audit>.json` line",
    },
    "g12-ondemand-spend-bounded": {
        "reads": ["inventory/*.json"],
        "error_fix": PULL,
        "red_fix": "HUMAN: the period is on pace to exceed the included allowance with on-demand ON — adjust the cap or turn on-demand off at the spending dashboard, then re-run "
        + PULL,
    },
    "g13-practitioner-index-reviewed": {
        "reads": ["surface/*/manifest.json", "practitioner-reviewed.json"],
        "error_fix": SNAP,
        "red_fix": "HUMAN: read the community-index diff and record the hash reviewed in practitioner-reviewed.json",
    },
    "g14-coverage-ratchet": {
        "reads": ["coverage-floor.json", "bin/gb-coverage.py"],
        "error_fix": "python3 bin/gb-coverage.py",
        "red_fix": "HUMAN: find what stopped being measured and restore it; raise the floor in coverage-floor.json only by an explicit commit that says why",
    },
    "g15-market-delta-reviewed": {
        "reads": ["market/*.json", "market-reviewed.json"],
        "error_fix": "python3 bin/gb-market-snapshot.py",
        "red_fix": "HUMAN: read python3 bin/gb-whatchanged.py and record the snapshot name in market-reviewed.json",
    },
    "g16-doc-delta-reviewed": {
        "reads": ["surface/*/manifest.json", "surface-reviewed.json"],
        "error_fix": SNAP,
        "red_fix": "HUMAN: read python3 bin/gb-whatchanged.py and record the snapshot name in surface-reviewed.json",
    },
    "g17-discovery-triaged": {
        "reads": ["discovery/*.json", "discovery-seen.json", "sources.json"],
        "error_fix": "python3 bin/gb-discover.py",
        "red_fix": "HUMAN: adopt each notable new repo into sources.json or dismiss it WITH A REASON in discovery-seen.json",
    },
    "g18-mcp-surface-healthy": {
        "reads": ["mcp/*.json"],
        "error_fix": "python3 bin/gb-mcp-validate.py",
        "red_fix": "HUMAN: a credential was rejected or a server is unreachable — rotate the stored secret or investigate the server, then re-run python3 bin/gb-mcp-validate.py",
    },
    "g19-fleet-earning-its-keep": {
        "reads": ["utilization/*.json"],
        "error_fix": "python3 bin/gb-utilization.py",
        "red_fix": "HUMAN: give each idle credentialed Bot a job or delete it",
    },
    "g20-context-archived": {
        "reads": ["archive/*.json"],
        "error_fix": "python3 bin/gb-context-archive.py",
        "red_fix": "python3 bin/gb-context-archive.py",
    },
    "g21-usecase-corpus-reviewed": {
        "reads": ["usecases/*.json", "usecases-reviewed.json"],
        "error_fix": "python3 bin/gb-usecases.py",
        "red_fix": "HUMAN: read USE-CASES.md and record the snapshot name in usecases-reviewed.json",
    },
    "g22-durable-io": {
        "reads": ["bin/*.py"],
        "error_fix": "HUMAN: a producer does not parse — fix the syntax error in the named file",
        "red_fix": "HUMAN: route the named write(s) through gbtypes.atomic_write_* and give every named child process a timeout=",
    },
    "g23-types-ratcheted": {
        "reads": ["typecheck/*.json"],
        "error_fix": "python3 bin/gb-typecheck.py",
        "red_fix": "HUMAN: fix the reported type error(s), or restore the deleted file(s) to the floor — the ratchet only turns one way",
    },
    "g24-cli-contract": {
        "reads": ["bin/gb"],
        "error_fix": "HUMAN: bin/gb is absent or unparseable — restore the entry point",
        "red_fix": "HUMAN: restore the missing verb(s) and wire every COMMANDS entry in HANDLERS — see `gb capabilities --json`",
    },
    "g25-routine-liveness": {
        "reads": ["inventory/*.json"],
        "error_fix": PULL,
        "red_fix": "HUMAN: the named routine is enabled, past due, and has never run — fix the scheduler or delete the routine",
    },
    "g26-jobs-proof-calls": {
        "reads": ["jobs/walks.jsonl"],
        "error_fix": "bin/gb jobs prove",
        "red_fix": "HUMAN: a done row lacks proof_ok — re-prove the job with a live Bot ask or revert the done claim",
    },
    "g27-surface-drift": {
        "reads": [
            "schema/*/manifest.json",
            "schema/registry.json",
            "schema/reviewed.json",
        ],
        "error_fix": "python3 bin/gb-schema.py",
        "red_fix": "HUMAN: the vendor moved the RPC surface — record the review in findings/ with the added/removed/changed lines, then update schema/reviewed.json to the reviewed version",
    },
}

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3


def resolve_evidence(
    root: pathlib.Path, patterns: List[str]
) -> Tuple[List[str], List[str]]:
    """Existing artifacts first (newest dated name first, capped), absences named.

    An evidence row that silently drops a missing artifact repeats the founding
    confusion (unmeasured reported as an absence). Both lists are load-bearing.
    """
    present: List[str] = []
    absent: List[str] = []
    for pat in patterns:
        hits = sorted(
            (
                str(p.relative_to(root))
                for p in root.glob(pat)
                if p.is_file() or p.is_dir()
            ),
            reverse=True,
        )
        if hits:
            present.extend(hits[:4])
            if len(hits) > 4:
                present.append(f"+{len(hits) - 4} more under {pat}")
        else:
            absent.append(pat)
    return present, absent


def fix_for(check: str, verdict: str) -> str:
    """One fix per verdict class: ERROR is unmeasured (re-measure), RED is bad (repair)."""
    if verdict == "GREEN":
        return "none - already green"
    if verdict == "ERROR":
        return str(CHECKS[check]["error_fix"])
    return str(CHECKS[check]["red_fix"])


def usage_error(bad: str) -> int:
    print(f"gb: no check named {bad!r}.", file=sys.stderr)
    print(f"    checks: {', '.join(CHECK_ORDER)}", file=sys.stderr)
    print("    try: gb triage g8-desktop-inventory", file=sys.stderr)
    return EXIT_USAGE


def run_gate(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    """The board, or None when the producer gave us nothing to select from."""
    gate = root / "bin" / "gb-surface-gate.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(gate), "--root", str(root), "--json"],
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT_S,
            cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        board = json.loads(proc.stdout)
    except ValueError:
        return None
    if not isinstance(board, dict) or not board.get("checks"):
        return None
    return board


def render(check: str, board: Dict[str, Any], root: pathlib.Path, as_json: bool) -> int:
    hit: Optional[Dict[str, Any]] = None
    for row in board["checks"]:
        if isinstance(row, dict) and row.get("check") == check:
            hit = row
            break
    if hit is None:  # the gate dropped a check — a broken read, not a pass
        print(
            f"gb: the gate returned no row for {check!r} — that is a broken read, not a green board.\n"
            "    run: python3 bin/gb-surface-gate.py --json | jq '.checks | length'",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT
    verdict = str(hit.get("verdict", "ERROR"))
    detail = str(hit.get("detail", ""))
    present, absent = resolve_evidence(root, list(CHECKS[check]["reads"]))
    fix = fix_for(check, verdict)
    payload = {
        "schema": SCHEMA,
        "verb": "triage",
        "check": check,
        "verdict": verdict,
        "detail": detail,
        "evidence": {"present": present, "absent": absent},
        "fix": fix,
        "produced_by": PRODUCER,
        "snapshot": board.get("snapshot"),
        "deployment": board.get("deployment"),
        "reviewed_baseline": board.get("reviewed_baseline"),
    }
    if as_json:
        print(json.dumps(payload, indent=1))
    else:
        lines = [
            f"  {check}",
            f"  verdict   {verdict}",
            f"  detail    {detail}",
            "  evidence  " + (present[0] if present else "(none on disk)"),
        ]
        lines += [f"            {p}" for p in present[1:]]
        lines += [f"  absent    {p}" for p in absent]
        lines += [f"  fix       {fix}", f"  producer  {PRODUCER}"]
        print("\n".join(lines))
    return EXIT_OK if verdict == "GREEN" else EXIT_FINDINGS


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-triage-check.py")
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("check", nargs="?", default=None)
    args = ap.parse_args(argv)

    root = pathlib.Path(str(args.root)).resolve()
    if args.selftest:
        return selftest(root)
    if not args.check:
        return usage_error(str(args.check))
    if args.check not in CHECKS:
        return usage_error(str(args.check))
    board = run_gate(root)
    if board is None:
        print(
            "gb: the gate produced no output — run `gb doctor --scope gate`",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT
    return render(str(args.check), board, root, bool(args.json))


def selftest(root: pathlib.Path) -> int:
    """Prove the table: 27 ids, parity with the gate's CHECKS, fixes present, usage path."""
    rows: List[Tuple[str, bool]] = []
    gate_path = root / "bin" / "gb-surface-gate.py"
    try:
        spec = importlib.util.spec_from_file_location("gbgate", gate_path)
        if spec is None or spec.loader is None:
            raise ImportError("no spec")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        gate_checks = tuple(getattr(mod, "CHECKS", ()))
    except Exception as exc:
        print(f"SELFTEST FAIL — cannot read gate CHECKS: {exc}", file=sys.stderr)
        return 1
    rows.append(("27 ids in CHECK_ORDER", len(CHECK_ORDER) == 27))
    rows.append(("table keys match CHECK_ORDER", set(CHECKS) == set(CHECK_ORDER)))
    rows.append(("table matches gate CHECKS", set(CHECKS) == set(gate_checks)))
    rows.append(
        (
            "every check has reads + both fixes",
            all(
                CHECKS[c].get("reads")
                and CHECKS[c].get("error_fix")
                and CHECKS[c].get("red_fix")
                for c in CHECK_ORDER
            ),
        ),
    )
    # The usage path must exit 2 without touching the gate: exercise main() directly.
    rows.append(("main(['__nope__']) exits 2", main(["__nope__"]) == EXIT_USAGE))
    rows.append(
        (
            "GREEN fix is a no-op line",
            fix_for("g1-surface-fetch-integrity", "GREEN") == "none - already green",
        )
    )
    rows.append(
        (
            "ERROR fix re-measures",
            fix_for("g12-ondemand-spend-bounded", "ERROR") == PULL,
        )
    )
    rows.append(
        ("RED fix repairs", "HUMAN" in fix_for("g12-ondemand-spend-bounded", "RED"))
    )
    present, absent = resolve_evidence(
        root, ["bin/gb-surface-gate.py", "no-such-dir/*.json"]
    )
    rows.append(
        (
            "evidence splits present/absent",
            present == ["bin/gb-surface-gate.py"] and absent == ["no-such-dir/*.json"],
        )
    )
    bad = [name for name, ok in rows if not ok]
    for name, ok in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    print(
        f"SELFTEST {'PASS' if not bad else 'FAIL'} — {sum(1 for _, ok in rows if ok)}/{len(rows)}"
    )
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
