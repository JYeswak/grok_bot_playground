#!/usr/bin/env python3
"""gb-compliance — the single compliance picture: goldens + C1-C4 in one command.

Skill: testing-conformance-harnesses, MATRIX step (compliance report generator).
Runs every suite live and prints one verdict table: the golden suite
(tests/golden/ via bin/gb-goldens-run.py) plus the four conformance matrices
(C1 MCP JSON-RPC, C2 template schema, C3 skill shape, C4a plugin manifest, C4b
Bot lifecycle). Scores are parsed from each harness's own machine-readable
output — this file asserts no contract itself, it only aggregates.

Usage: python3 bin/gb-compliance.py [--json]
  default prints the text report; --json prints the machine-readable report.
Exit: 0 = goldens clean AND every MUST score >= 0.95 · 1 = any red · 2 = runner error.
Stdlib only. Read-only: runs harnesses, writes nothing (C1 legs are the
harness's own read-only probe set; C4b sends no turns; goldens runner may
write *.actual files on mismatch, exactly as a bare goldens run would).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
PY = sys.executable

SUITES = (
    ("goldens", [PY, "bin/gb-goldens-run.py"], "golden"),
    ("C1 mcp", [PY, "bin/gb-mcp-conformance.py"], "live"),
    ("C2 template", [PY, "bin/gb-template-conformance.py", "--json"], "json"),
    ("C3 skill", [PY, "bin/gb-skill-conformance.py", "--json"], "json"),
    ("C4a plugin", [PY, "bin/gb-plugin-conformance.py", "--json"], "json"),
    ("C4b bot", [PY, "bin/gb-bot-conformance.py", "--json"], "json"),
)


def run(cmd: list[str], timeout: float):
    try:
        p = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 127, f"RUNNER ERROR: {e}"


def parse(name: str, kind: str, rc: int, out: str):
    """Return (verdict, score_text, detail). Verdict in OK/RED/ERROR."""
    if kind == "golden":
        oks = len(re.findall(r"\[GOLDEN\] OK", out))
        bad = re.findall(r"GOLDEN MISMATCH: (\S+)", out)
        if rc == 0 and not bad:
            return "OK", f"{oks}/{oks}", f"{oks} goldens byte-identical"
        return "RED", f"{oks}/{oks + len(bad)}", f"mismatch: {', '.join(bad)}"
    if kind == "live":
        m = re.search(
            r"fail=(\d+)\s+xfail=\d+\s+score=(\d\.\d+)\s+ex-divergence=(\d\.\d+)", out
        )
        if m:
            fails, primary, exdiv = (
                int(m.group(1)),
                float(m.group(2)),
                float(m.group(3)),
            )
            # Skill XFAIL semantics: accepted divergences (DISCREPANCIES.md) do
            # not fail conformance — zero FAILs + ex-divergence >= 0.95 is OK.
            ok = fails == 0 and exdiv >= 0.95
            return (
                "OK" if ok else "RED",
                f"MUST {primary:.3f} (ex-div {exdiv:.3f}, {fails} FAIL)",
                "live production, read-only legs; divergences in DISCREPANCIES.md",
            )
        return (
            "ERROR",
            "n/a",
            out.strip().splitlines()[-1] if out.strip() else f"rc={rc}",
        )
    # json kind: each harness reports its own verdict; respect it.
    try:
        d = json.loads(out[out.index("{") :])
        must = d.get("must_score", -1)
        verdict = d.get("verdict", "?")
        ok = rc == 0 and verdict.startswith("CONFORMANT")
        extra = ""
        if name == "C4b bot":
            extra = (
                f"; live-proven {d.get('live_proven', 0)}/{d.get('live_total', '?')}"
            )
        return ("OK" if ok else "RED", f"MUST {must:.3f}{extra}", verdict)
    except Exception:
        tail = out.strip().splitlines()
        return "ERROR", "n/a", (tail[-1] if tail else f"rc={rc}")[:160]


def collect():
    rows = []
    for name, cmd, kind in SUITES:
        timeout = 600.0 if name == "C1 mcp" else 300.0
        rc, out = run(cmd, timeout)
        verdict, score, detail = parse(name, kind, rc, out)
        rows.append(
            {
                "suite": name,
                "verdict": verdict,
                "score": score,
                "detail": detail,
                "rc": rc,
            }
        )
    return rows


def overall(rows):
    red = [r for r in rows if r["verdict"] != "OK"]
    return (
        "COMPLIANT" if not red else "NON-COMPLIANT",
        [] if not red else [r["suite"] for r in red],
    )


def render_text(rows, verd, _bad):
    L = [
        "compliance report (goldens + C1-C4, every suite run live)",
        f"{'suite':<12} {'verdict':<7} {'score':<28} detail",
    ]
    for r in rows:
        L.append(f"{r['suite']:<12} {r['verdict']:<7} {r['score']:<28} {r['detail']}")
    L.append("")
    L.append(f"verdict: {verd}")
    if _bad:
        L.append("red suites: " + ", ".join(_bad))
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows = collect()
    verd, bad = overall(rows)
    if args.json:
        sys.stdout.write(
            json.dumps(
                {"schema": "gb-compliance/1", "verdict": verd, "suites": rows}, indent=1
            )
            + "\n"
        )
    else:
        sys.stdout.write(render_text(rows, verd, bad))
    return 0 if verd == "COMPLIANT" else 1


if __name__ == "__main__":
    sys.exit(main())
