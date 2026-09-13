#!/usr/bin/env python3
"""gb-bot-conformance — Bot turn-lifecycle stage-transition contract matrix (C4b).

Skill: testing-conformance-harnesses, Pattern 4 (SPEC-DERIVED TEST MATRIX).
Loop mapping: IDENTIFY (the send→dispatched→reply→match lifecycle in
bin/gb-bot.py, the only lifecycle spec that exists) -> EXTRACT (TRANSITIONS
table below: every stage transition that is MUST-observable) -> FIXTURE
(none frozen: the SUT is the code path itself) -> HARNESS (this file: source
predicates over gb-bot.py) -> COVER (one check per transition, tagged
MUST/SHOULD) -> DIVERGE (none: every deviation would be a FAIL) -> MATRIX
(stdout: transition x evidence matrix + coverage accounting + score).

Evidence source (stated plainly): CODE PATHS in bin/gb-bot.py. No transcript
evidence exists in this repo (deployment/*.json carry the roster, never
turns), and a live turn spends allowance and speaks as the user, so live
proof-calls are out of scope for a conformance harness. Every cell is
therefore marked code-proven vs live-proven: code-proven = the predicate
holds on the pinned source; live-proven = observed in a real turn (none —
0 live-proven cells, honestly reported, never hidden).

Lifecycle under contract (gb-bot.py):
  resolve → send → dispatched → poll → fresh → reply → match
  (auxiliary read path: log-decode, state-echo, resolve-cmd)

Usage: python3 bin/gb-bot-conformance.py [--md] [--json] [--selftest]
  default prints the text matrix; --md prints the markdown matrix artifact.
Exit: 0 = MUST score >= 0.95 (conformant) · 1 = honest-red · 2 = harness error.
Stdlib only. Read-only: imports nothing, sends nothing, only reads gb-bot.py.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOT_SRC = ROOT / "bin" / "gb-bot.py"

# (transition, level, source_anchor, description, [predicate regexps]).
# A transition is code-proven iff EVERY predicate matches gb-bot.py source.
TRANSITIONS = (
    (
        "L-resolve",
        "MUST",
        "resolve:78-97",
        "name→uuid via newest deployment roster; raw uuid passes through; unknown→None",
        [r"def resolve\(", r"newest.*roster|sorted\(.*deployment", r"return None"],
    ),
    (
        "L-send-shape",
        "MUST",
        "cmd_send:138-144",
        "SendGrokBotUserMessage takes {agent_id, message_id: uuid4, text}",
        [
            r"SendGrokBotUserMessage",
            r"uuid\.uuid4\(\)",
            r'"agent_id".*"message_id".*"text"|' r"'agent_id'.*'message_id'",
        ],
    ),
    (
        "L-send-receipt",
        "MUST",
        "cmd_send:145-156",
        "HTTP 200 echoes dispatched+workflowId; non-200 → UPSTREAM with capped body",
        [r"dispatched", r"workflowId", r"EXIT_UPSTREAM", r"\[:200\]"],
    ),
    (
        "L-send-guard",
        "MUST",
        "cmd_send:131-137",
        "unknown Bot → usage error naming the roster; no network leaves",
        [r"unknown Bot", r"not in newest deployment roster", r"EXIT_USAGE"],
    ),
    (
        "L-poll",
        "MUST",
        "cmd_ask:251-269",
        "ask snapshots pre-seqs, polls transcript(limit 15) every 20s to the deadline",
        [
            r"seen = \{e\.get\(\"seq\"\)",
            r"transcript\(mod, token, agent_id, 15\)",
            r"time\.sleep\(20\.0\)",
            r"deadline = time\.time\(\) \+ timeout_s",
        ],
    ),
    (
        "L-fresh",
        "MUST",
        "cmd_ask:272-276",
        "fresh = seq ∉ seen AND role == assistant",
        [r'not in seen and e\.get\("role"\) == "assistant"'],
    ),
    (
        "L-quiesce",
        "MUST",
        "cmd_ask:277-280",
        "match + 2 quiet rounds short-circuits the poll early",
        [r"quiet_rounds", r"matched and quiet_rounds >= 2"],
    ),
    (
        "L-match",
        "MUST",
        "cmd_ask:283-290",
        "--expect regex (re.S) over the fresh blob; matched wins, else last_fresh",
        [r"re\.search\(expect, blob, re\.S\)", r"matched or last_fresh"],
    ),
    (
        "L-no-reply",
        "MUST",
        "cmd_ask:291-293",
        "no assistant reply within the timeout → UPSTREAM naming the seconds",
        [r"no assistant reply within", r"EXIT_UPSTREAM"],
    ),
    (
        "L-expect-mismatch",
        "MUST",
        "cmd_ask:299-304",
        "unmatched --expect → exit 1 showing the last fresh message",
        [r"reply did not match", r"return 1"],
    ),
    (
        "L-log-decode",
        "MUST",
        "transcript:183-205",
        "bodies base64-decoded; non-dict → {content: body[:200]} fallback",
        [r"base64\.b64decode", r'"content": body\[:200\]'],
    ),
    (
        "L-log-empty",
        "MUST",
        "transcript:162-163",
        "empty transcript = no turns yet, never an error",
        [r"empty list = no turns yet, never an error"],
    ),
    (
        "L-read",
        "MUST",
        "cmd_read:308-340",
        "SetGrokBotAgentClientState bare echo; row fields unread/notif/preview",
        [r"SetGrokBotAgentClientState", r"lastMessagePreview", r"unreadCount"],
    ),
    (
        "L-resolve-cmd",
        "MUST",
        "cmd_resolve:343-356",
        "resolve verb: roster mapping with no session and no spend",
        [r"No session, no spend", r"def cmd_resolve"],
    ),
    (
        "L-auth",
        "MUST",
        "main:363-368",
        "missing client session → ENVIRONMENT before any network",
        [r"no client session", r"EXIT_ENVIRONMENT"],
    ),
    (
        "S-ask-timeout",
        "SHOULD",
        "harness-proposed",
        "ask default deadline is 300s (bounded wait, documented default)",
        [r"--timeout.*default=300\.0|default=300\.0"],
    ),
    (
        "S-body-cap",
        "SHOULD",
        "harness-proposed",
        "upstream error bodies are capped ([:200]) before printing — no secret spill",
        [r"json\.dumps\(resp\)\[:200\]"],
    ),
)


def evaluate(src_text: str):
    """Evaluate every transition. Returns (cells, notes, hits)."""
    cells, notes, hits = {}, {}, {}
    for tid, lvl, anchor, desc, preds in TRANSITIONS:
        missing = [p for p in preds if not re.search(p, src_text)]
        ok = not missing
        cells[tid] = "PASS" if ok else ("FAIL" if lvl == "MUST" else "GAP")
        hits[tid] = f"{len(preds) - len(missing)}/{len(preds)} predicates"
        if missing:
            notes[tid] = f"predicates absent from gb-bot.py: {missing[:2]} ({anchor})"
    return cells, notes, hits


def _level(tid):
    return next(l for t, l, _a, _d, _p in TRANSITIONS if t == tid)


def scores(cells):
    mp = mt = sp = st = 0
    for tid, lvl, _a, _d, _p in TRANSITIONS:
        if lvl == "MUST":
            mt += 1
            mp += cells.get(tid) == "PASS"
        else:
            st += 1
            sp += cells.get(tid) == "PASS"
    return {
        "must": mp / mt if mt else 1.0,
        "must_pass": mp,
        "must_total": mt,
        "should": sp / st if st else 1.0,
        "should_pass": sp,
        "should_total": st,
    }


def render_text(cells, sc, hits, live_proven):
    L = [
        "Bot turn-lifecycle conformance matrix (C4b)",
        "evidence: CODE PATHS in bin/gb-bot.py — every cell code-proven; "
        f"live-proven cells: {live_proven} (no transcript evidence in repo; live turns spend + speak)",
        "",
        f"{'transition':<18} {'level':<7} {'verdict':<7} {'code':<9} live    description",
    ]
    for tid, lvl, _a, desc, _p in TRANSITIONS:
        live = "live:0" if True else ""
        L.append(
            f"{tid:<18} {lvl:<7} {cells.get(tid, '?'):<7} {hits.get(tid, ''):<9} {live}    {desc}"
        )
    L.append("")
    L.append(
        f"MUST transitions: pass={sc['must_pass']}/{sc['must_total']} score={sc['must']:.3f} (code-proven)"
    )
    L.append(
        f"SHOULD transitions: pass={sc['should_pass']}/{sc['should_total']} score={sc['should']:.3f} (code-proven)"
    )
    L.append(
        f"live-proven coverage: {live_proven}/{len(TRANSITIONS)} — no transcript fixtures exist; live turns out of scope"
    )
    L.append(
        f"verdict: {'CONFORMANT (code-proven)' if sc['must'] >= 0.95 else 'NOT CONFORMANT'}"
    )
    return "\n".join(L) + "\n"


def render_md(cells, sc, hits, notes, live_proven):
    L = [
        "# Bot turn-lifecycle conformance matrix (C4b)",
        "",
        "Spec-derived (Pattern 4): the lifecycle spec is `bin/gb-bot.py` itself — the "
        "send→dispatched→reply→match stages and their exit codes. Each transition below "
        "names the source anchor it is proven against. Read-only: no turn was sent, no "
        "allowance spent, no transcript mutated for this matrix.",
        "",
        "Provenance: spec `bin/gb-bot.py` (pinned by the harness reading the live file); "
        "harness `bin/gb-bot-conformance.py`; command `python3 bin/gb-bot-conformance.py --md`.",
        "",
        "## Evidence source (stated, not buried)",
        "",
        "CODE PATHS. No transcript evidence exists in this repo (`deployment/*.json` "
        "carry the Bot roster, never turns), and a live `ask` spends allowance and speaks "
        "as the user — out of scope for a conformance harness by design. Every cell is "
        "therefore **code-proven** (predicate holds on the pinned source) and **not "
        f"live-proven**: live-proven coverage is **{live_proven}/{len(TRANSITIONS)}**. "
        "A turn that contradicts a code-proven cell would be a FAIL with a fix proposal, "
        "not a silent divergence.",
        "",
        "Cell vocabulary: MUST columns use PASS/FAIL; SHOULD columns use PASS/GAP; "
        "every cell carries code-predicate hits and a live-proven count (0).",
        "",
        "| transition | level | source anchor | verdict | code predicates | live-proven |",
        "|---|---|---|---|---|---|",
    ]
    for tid, lvl, anchor, desc, _p in TRANSITIONS:
        L.append(
            f"| {tid} | {lvl} | {anchor} | {cells.get(tid, '?')} | {hits.get(tid, '')} | 0 — {desc} |"
        )
    L.append("")
    L.append("## Coverage accounting")
    L.append("")
    L.append(
        "| section | transition | MUST | SHOULD | tested | passing | divergent | score |"
    )
    L.append("|---|---|---|---|---|---|---|---|")
    for tid, lvl, _a, _d, _p in TRANSITIONS:
        m, s = (1, 0) if lvl == "MUST" else (0, 1)
        p = 1 if cells.get(tid) == "PASS" else 0
        L.append(f"| lifecycle | {tid} | {m} | {s} | 1 | {p} | 0 | {p:.3f} |")
    L.append("")
    L.append(
        f"MUST score {sc['must']:.3f} ({sc['must_pass']}/{sc['must_total']}) code-proven; "
        f"SHOULD score {sc['should']:.3f} ({sc['should_pass']}/{sc['should_total']}) code-proven; "
        f"live-proven {live_proven}/{len(TRANSITIONS)}; "
        f"verdict **{'CONFORMANT (code-proven)' if sc['must'] >= 0.95 else 'NOT CONFORMANT'}**."
    )
    if notes:
        L.append("")
        L.append("## Failures with fix proposals")
        L.append("")
        for tid, note in notes.items():
            L.append(f"- `{tid}` ({note})")
    else:
        L.append("")
        L.append(
            "No failures: every lifecycle transition is observable on the pinned code "
            "path. C4b has no DISCREPANCIES entries."
        )
    return "\n".join(L) + "\n"


def render_json(cells, sc, hits, notes, live_proven):
    return (
        json.dumps(
            {
                "schema": "gb-bot-conformance/1",
                "evidence": "code-paths",
                "source": str(BOT_SRC.relative_to(ROOT)),
                "must_score": round(sc["must"], 4),
                "should_score": round(sc["should"], 4),
                "live_proven": live_proven,
                "live_total": len(TRANSITIONS),
                "verdict": "CONFORMANT" if sc["must"] >= 0.95 else "NOT CONFORMANT",
                "cells": cells,
                "predicates": hits,
                "notes": notes,
            },
            indent=1,
        )
        + "\n"
    )


# ---------------------------------------------------------------- selftest
def selftest() -> int:
    src = BOT_SRC.read_text(encoding="utf-8")
    # 1. Pinned source passes every clause (read-only sanity).
    cells, _notes, _hits = evaluate(src)
    bad = [
        t for t, l, _a, _d, _p in TRANSITIONS if l == "MUST" and cells.get(t) != "PASS"
    ]
    assert not bad, f"selftest: pinned source fails {bad}"
    # 2. Each clause fires on a planted source defect.
    # Tokens are (needle, n): unique path tokens remove once; guard strings
    # shared verbatim across send/log/ask/read remove everywhere (replace-all),
    # which still proves the predicate is not vacuous.
    tokens = {
        "L-resolve": ("def resolve(", 1),
        "L-send-shape": ("uuid.uuid4()", 1),
        "L-send-receipt": ("workflowId", 0),
        "L-send-guard": ("unknown Bot", 0),
        "L-poll": ("time.sleep(20.0)", 1),
        "L-fresh": ('== "assistant"', 1),
        "L-quiesce": ("quiet_rounds >= 2", 1),
        "L-match": ("matched or last_fresh", 1),
        "L-no-reply": ("no assistant reply within", 1),
        "L-expect-mismatch": ("reply did not match", 1),
        "L-log-decode": ("base64.b64decode", 1),
        "L-log-empty": ("never an error", 1),
        "L-read": ("lastMessagePreview", 1),
        "L-resolve-cmd": ("def cmd_resolve", 1),
        "L-auth": ("no client session", 1),
        "S-ask-timeout": ("default=300.0", 1),
        "S-body-cap": ("json.dumps(resp)[:200]", 0),
    }
    assert set(tokens) == {
        t for t, _l, _a, _d, _p in TRANSITIONS
    }, "selftest: token table drifted from TRANSITIONS"
    with tempfile.TemporaryDirectory() as td:
        for tid, (tok, n) in tokens.items():
            assert (
                tok in src
            ), f"selftest: token for {tid} not in pinned source ({tok!r})"
            mutated = (
                src.replace(tok, "REMOVED_BY_SELFTEST")
                if n == 0
                else src.replace(tok, "REMOVED_BY_SELFTEST", 1)
            )
            mcells, _mn, _mh = evaluate(mutated)
            lvl = _level(tid)
            want = "FAIL" if lvl == "MUST" else "GAP"
            assert (
                mcells[tid] == want
            ), f"selftest: {tid} did not fire on planted defect (got {mcells[tid]})"
            # No cascade: every OTHER clause still passes (token uniqueness).
            others = [
                t
                for t, l, _a, _d, _p in TRANSITIONS
                if t != tid and l == "MUST" and mcells.get(t) != "PASS"
            ]
            assert not others, f"selftest: {tid} token removal cascaded into {others}"
    print(
        f"SELFTEST PASS — {len(TRANSITIONS)} transitions fire on planted defects, pinned source clean"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--md", action="store_true")
    g.add_argument("--json", action="store_true")
    g.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        try:
            return selftest()
        except AssertionError as e:
            print(f"SELFTEST FAILED: {e}")
            return 1
    if not BOT_SRC.is_file():
        print("bin/gb-bot.py not found", file=sys.stderr)
        return 2
    src = BOT_SRC.read_text(encoding="utf-8")
    cells, notes, hits = evaluate(src)
    sc = scores(cells)
    live_proven = 0
    if args.md:
        sys.stdout.write(render_md(cells, sc, hits, notes, live_proven))
    elif args.json:
        sys.stdout.write(render_json(cells, sc, hits, notes, live_proven))
    else:
        sys.stdout.write(render_text(cells, sc, hits, live_proven))
    return 0 if sc["must"] >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main())
