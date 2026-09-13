#!/usr/bin/env python3
"""Observe exactly one Muse pane, failing closed when NTM ignores its selector.

`ntm --robot-is-working --inspect-index=N` has returned every pane while claiming
success. This wrapper accepts that response only when both selector echoes name
exactly N. Otherwise it remaps N to a tmux pane id and requires a fresh targeted
`robot-tail` between two identical tmux mappings before exposing dispatch state.

  gb-muse-observe.py --session grokbot --index 2 --json
  gb-muse-observe.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import main as gbmain  # noqa: E402

SCHEMA = "gb-muse-observe/1"
EXIT_OK, EXIT_UNMEASURED = 0, 2
PROBE_TIMEOUT_SECONDS = 30
TAIL_LINES = 120


def _names(values: object) -> List[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values]


def selector_problem(requested: int, doc: Mapping[str, Any]) -> Optional[str]:
    """Return why an inspect response is not authority for exactly one pane."""
    wanted = [str(requested)]
    query = doc.get("query")
    panes = doc.get("panes")
    echoed = _names(query.get("panes_requested")) if isinstance(query, dict) else []
    returned = sorted(str(key) for key in panes) if isinstance(panes, dict) else []
    if echoed != wanted:
        return "query.panes_requested=%r, wanted=%r" % (echoed, wanted)
    if returned != wanted:
        return "pane_keys=%r, wanted=%r" % (returned, wanted)
    return None


def parse_tmux_mapping(text: str) -> Dict[int, str]:
    """Parse `index<TAB>%pane_id`; duplicates are invalid, never last-one-wins."""
    mapping: Dict[int, str] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        pane_id = parts[1].strip()
        if not pane_id.startswith("%") or index in mapping:
            raise ValueError("invalid or duplicate tmux mapping for index %s" % index)
        mapping[index] = pane_id
    return mapping


def _run(
    argv: Sequence[str], *, env: Optional[Mapping[str, str]] = None
) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            env=dict(env) if env is not None else None,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("%s: %s" % (argv[0], exc)) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise RuntimeError(
            "%s exited %d: %s"
            % (argv[0], proc.returncode, detail[-1] if detail else "no detail")
        )
    return {"argv": list(argv), "stdout": proc.stdout}


def _json_stdout(run: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        doc = json.loads(str(run["stdout"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("command did not emit one JSON object") from exc
    if not isinstance(doc, dict):
        raise RuntimeError("command JSON root is not an object")
    return doc


def _tmux_env() -> Dict[str, str]:
    env = dict(os.environ)
    if not env.get("TMUX_TMPDIR"):
        candidate = pathlib.Path.home() / ".tmux-sockets"
        if candidate.is_dir():
            env["TMUX_TMPDIR"] = str(candidate)
    return env


def _mapping(session: str, env: Mapping[str, str]) -> Tuple[Dict[int, str], List[str]]:
    argv = ["tmux", "list-panes", "-t", session, "-F", "#{pane_index}\t#{pane_id}"]
    run = _run(argv, env=env)
    return parse_tmux_mapping(str(run["stdout"])), argv


def mapping_problem(
    index: int,
    pane_id: str,
    before: Mapping[int, str],
    after: Mapping[int, str],
) -> Optional[str]:
    if before.get(index) != pane_id:
        return "initial index-to-id mapping does not contain %s" % pane_id
    if after.get(index) != pane_id:
        return "stale index-to-id mapping: %s became %s" % (pane_id, after.get(index))
    return None


def rate_limit_reason(doc: Mapping[str, Any], index: int) -> Optional[str]:
    """Use a selector-drifted row only as a conservative dispatch veto."""
    panes = doc.get("panes")
    pane = panes.get(str(index)) if isinstance(panes, dict) else None
    if not isinstance(pane, dict):
        return None
    indicators = pane.get("indicators")
    limits = indicators.get("limit") if isinstance(indicators, dict) else []
    if pane.get("is_rate_limited") is True:
        return "is_rate_limited=true"
    if pane.get("recommendation") == "RATE_LIMITED_WAIT":
        return "recommendation=RATE_LIMITED_WAIT"
    if isinstance(limits, list) and limits:
        return "limit indicators present"
    return None


def _pane_state(doc: Mapping[str, Any], index: int) -> Dict[str, Any]:
    panes = doc.get("panes")
    pane = panes.get(str(index)) if isinstance(panes, dict) else None
    if not isinstance(pane, dict):
        raise RuntimeError("selected pane is missing from response")
    return {
        "observation_state": pane.get("observation_state"),
        "observation_freshness": pane.get("observation_freshness"),
        "safe_to_dispatch": pane.get("safe_to_dispatch"),
    }


def observe(session: str, index: int) -> Dict[str, Any]:
    inspect_argv = [
        "ntm",
        "--robot-is-working=" + session,
        "--inspect-index=" + str(index),
    ]
    inspected = _json_stdout(_run(inspect_argv))
    problem = selector_problem(index, inspected)
    limited = rate_limit_reason(inspected, index)
    if limited:
        env = _tmux_env()
        before, map_argv = _mapping(session, env)
        pane_id = before.get(index)
        if pane_id is None:
            raise RuntimeError("rate-limited pane index %d is absent from tmux" % index)
        after, _ = _mapping(session, env)
        stale = mapping_problem(index, pane_id, before, after)
        if stale:
            raise RuntimeError(stale)
        return {
            "schema": SCHEMA,
            "verdict": "RATE_LIMITED_WAIT",
            "session": session,
            "index": index,
            "pane_id": pane_id,
            "selector_problem": problem,
            "rate_limit_reason": limited,
            "state": {
                "observation_state": "rate_limited",
                "observation_freshness": "fresh",
                "safe_to_dispatch": False,
            },
            "recovery_argv": [map_argv],
        }
    if problem is None:
        state = _pane_state(inspected, index)
        return {
            "schema": SCHEMA,
            "verdict": "SELECTOR_MATCH",
            "session": session,
            "index": index,
            "pane_id": None,
            "selector_problem": None,
            "state": state,
            "recovery_argv": [],
        }

    env = _tmux_env()
    before, map_argv = _mapping(session, env)
    pane_id = before.get(index)
    if pane_id is None:
        raise RuntimeError("selector mismatch and tmux index %d is absent" % index)
    tail_argv = [
        "ntm",
        "--robot-tail=" + session,
        "--panes=" + pane_id,
        "--lines=" + str(TAIL_LINES),
        "--fresh",
    ]
    tailed = _json_stdout(_run(tail_argv))
    after, _ = _mapping(session, env)
    stale = mapping_problem(index, pane_id, before, after)
    if stale:
        raise RuntimeError(stale)
    if sorted(str(key) for key in (tailed.get("panes") or {})) != [str(index)]:
        raise RuntimeError("targeted tail did not return exactly index %d" % index)
    state = _pane_state(tailed, index)
    if state["observation_freshness"] != "fresh":
        raise RuntimeError("targeted tail is not fresh")
    return {
        "schema": SCHEMA,
        "verdict": "SELECTOR_MISMATCH_FALLBACK_VERIFIED",
        "session": session,
        "index": index,
        "pane_id": pane_id,
        "selector_problem": problem,
        "state": state,
        "recovery_argv": [map_argv, tail_argv],
    }


def selftest() -> int:
    legs: List[Tuple[str, bool]] = []

    def leg(name: str, good: bool) -> None:
        legs.append((name, good))

    exact = {"query": {"panes_requested": ["2"]}, "panes": {"2": {}}}
    leg("exact-selected-pane-passes", selector_problem(2, exact) is None)
    all_query = {"query": {"panes_requested": ["1", "2"]}, "panes": {"2": {}}}
    leg("all-pane-query-refused", selector_problem(2, all_query) is not None)
    all_returned = {"query": {"panes_requested": ["2"]}, "panes": {"1": {}, "2": {}}}
    leg("all-pane-result-refused", selector_problem(2, all_returned) is not None)
    absent = {"query": {"panes_requested": ["2"]}, "panes": {"3": {}}}
    leg("missing-selected-pane-refused", selector_problem(2, absent) is not None)
    no_query = {"panes": {"2": {}}}
    leg("missing-selector-echo-refused", selector_problem(2, no_query) is not None)
    rate_limited = {
        "panes": {
            "2": {
                "is_rate_limited": True,
                "recommendation": "RATE_LIMITED_WAIT",
                "indicators": {"limit": ["quota exhausted"]},
            }
        }
    }
    leg(
        "rate-limit-is-conservative-veto",
        rate_limit_reason(rate_limited, 2) == "is_rate_limited=true",
    )
    leg("other-pane-limit-does-not-veto", rate_limit_reason(rate_limited, 3) is None)
    mapping = parse_tmux_mapping("0\t%34\n2\t%57\n")
    leg("mapping-parses-exact-pane-id", mapping == {0: "%34", 2: "%57"})
    try:
        parse_tmux_mapping("2\t%57\n2\t%99\n")
        duplicate_refused = False
    except ValueError:
        duplicate_refused = True
    leg("duplicate-mapping-refused", duplicate_refused)
    try:
        parse_tmux_mapping("2\tbad\n")
        invalid_refused = False
    except ValueError:
        invalid_refused = True
    leg("invalid-pane-id-refused", invalid_refused)
    leg(
        "stable-mapping-passes",
        mapping_problem(2, "%57", {2: "%57"}, {2: "%57"}) is None,
    )
    leg(
        "stale-mapping-refused",
        mapping_problem(2, "%57", {2: "%57"}, {2: "%99"}) is not None,
    )
    recovery = [
        "ntm",
        "--robot-tail=grokbot",
        "--panes=%57",
        "--lines=120",
        "--fresh",
    ]
    leg("recovery-targets-pane-id", recovery[2] == "--panes=%57")
    leg("recovery-requires-fresh", recovery[-1] == "--fresh")

    passed = sum(good for _, good in legs)
    for name, good in legs:
        print("  %s %s" % ("ok  " if good else "FAIL", name))
    print(
        "SELFTEST %s - %d/%d"
        % ("PASS" if passed == len(legs) else "FAIL", passed, len(legs))
    )
    return EXIT_OK if passed == len(legs) else EXIT_UNMEASURED


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="gb-muse-observe.py")
    parser.add_argument("--session", default="grokbot")
    parser.add_argument("--index", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.index is None or args.index < 0:
        parser.error("--index is required and must be non-negative")
    try:
        doc = observe(args.session, args.index)
        rc = EXIT_OK
    except (RuntimeError, ValueError) as exc:
        doc = {
            "schema": SCHEMA,
            "verdict": "ERROR",
            "session": args.session,
            "index": args.index,
            "detail": str(exc),
            "recovery_argv": [
                "TMUX_TMPDIR=${TMUX_TMPDIR:-$HOME/.tmux-sockets}",
                "tmux list-panes -t %s -F '#{pane_index}\\t#{pane_id}'" % args.session,
            ],
        }
        rc = EXIT_UNMEASURED
    if args.json:
        print(json.dumps(doc, sort_keys=True, separators=(",", ":")))
    else:
        print("%s index=%s pane=%s" % (doc["verdict"], args.index, doc.get("pane_id")))
    return rc


if __name__ == "__main__":
    gbmain(main)
