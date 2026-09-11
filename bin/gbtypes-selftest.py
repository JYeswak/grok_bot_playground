#!/usr/bin/env python3
"""gbtypes-selftest — prove the typed spine's claims against a real OS, not against a mock.

Every claim in `gbtypes.py` is a claim about behaviour under adverse conditions (a deadline, a
runaway child, a signal landing mid-write). A claim like that is worth exactly as much as its
executable counter-example, so each test here has BOTH legs:

    known-bad : the untyped/naive construction, which MUST be caught failing
    known-good: the gbtypes construction, which MUST hold

A test whose known-bad leg cannot be made to fail is reported as INCONCLUSIVE, never as a pass.
That is the difference between a proof and a green tick.

exit: 0 all proven · 1 a claim failed · 2 a known-bad leg never fired (test is vacuous)
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
from typing import List, Tuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gbtypes as G  # noqa: E402

PASS, FAIL, VACUOUS = "PASS", "FAIL", "VACUOUS"
rows: List[Tuple[str, str, str]] = []


def record(name: str, verdict: str, detail: str) -> None:
    rows.append((name, verdict, detail))
    print(f"[{verdict:<7}] {name}: {detail}")


# -- T1: a verdict cannot be a bare string ----------------------------------------------------
def t1_typed_verdict() -> None:
    try:
        G.CheckResult("g8-desktop-inventory", "GREEN", "looks fine")  # type: ignore[arg-type]
        record(
            "t1-typed-verdict",
            FAIL,
            "a bare string was accepted as a Verdict — transposition still possible",
        )
        return
    except TypeError:
        pass
    good = G.CheckResult("g8-desktop-inventory", G.Verdict.RED, "one field unread")
    if good.to_json() != {
        "check": "g8-desktop-inventory",
        "verdict": "RED",
        "detail": "one field unread",
    }:
        record("t1-typed-verdict", FAIL, f"wire shape changed: {good.to_json()}")
        return
    if good.ok:
        record("t1-typed-verdict", FAIL, "a RED reported ok")
        return
    record(
        "t1-typed-verdict",
        PASS,
        "bare string rejected; Verdict accepted; wire shape unchanged; RED is not ok",
    )


# -- T2: an oversize read refuses instead of short-reading -------------------------------------
def t2_bounded_read() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "big.json"
        p.write_text("x" * 5000)
        naive = len(p.read_text())  # the known-bad: reads whatever is there
        try:
            G.read_text_capped(p, max_bytes=1000)
            record("t2-bounded-read", FAIL, "over-cap file was read instead of refused")
            return
        except ValueError:
            pass
        got = G.read_text_capped(p, max_bytes=10_000)
        if got is None or len(got) != 5000:
            record(
                "t2-bounded-read", FAIL, "under-cap read did not return the whole file"
            )
            return
        if G.read_text_capped(pathlib.Path(d) / "absent.json") is not None:
            record("t2-bounded-read", FAIL, "absent file did not read as None")
            return
        record(
            "t2-bounded-read",
            PASS,
            f"naive read took {naive}B unbounded; capped read refused, then read 5000B whole",
        )


# -- T3: a deadline is enforced and reported ---------------------------------------------------
def t3_deadline() -> None:
    started = time.monotonic()
    proc = G.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.75)
    elapsed = time.monotonic() - started
    if not proc.timed_out:
        record(
            "t3-deadline",
            FAIL,
            f"a 30s child under a 0.75s deadline did not report timed_out (elapsed {elapsed:.1f}s)",
        )
        return
    if elapsed > 6:
        record(
            "t3-deadline", FAIL, f"deadline not enforced: returned after {elapsed:.1f}s"
        )
        return
    if proc.ok:
        record("t3-deadline", FAIL, "a timed-out run reported ok")
        return
    record(
        "t3-deadline",
        PASS,
        f"30s child killed at {elapsed:.2f}s, timed_out=True, ok=False",
    )


# -- T4: a runaway child is capped and the cap is reported -------------------------------------
def t4_bounded_capture() -> None:
    # 32 MB of output against a 256 KB cap.
    emitter = "import sys\nfor _ in range(512): sys.stdout.write('x'*65536)\n"
    proc = G.run(
        [sys.executable, "-c", emitter], timeout_s=30, max_output_bytes=256 * 1024
    )
    held = len(proc.out) + len(proc.err)
    if not proc.truncated:
        record(
            "t4-bounded-capture",
            FAIL,
            f"32MB of output under a 256KB cap did not report truncated (held {held}B)",
        )
        return
    if held > 512 * 1024:
        record("t4-bounded-capture", FAIL, f"cap not enforced: retained {held} bytes")
        return
    if proc.ok:
        record(
            "t4-bounded-capture",
            FAIL,
            "a truncated run reported ok — callers would trust a partial answer",
        )
        return
    record(
        "t4-bounded-capture",
        PASS,
        f"32MB emitter capped at {held}B, child killed, truncated=True, ok=False",
    )


# -- T5: the differential cancel-correctness proof ---------------------------------------------
CHILD = r"""
import json, os, pathlib, sys, time
sys.path.insert(0, %(here)r)
import gbtypes as G

mode, target = sys.argv[1], pathlib.Path(sys.argv[2])
payload = {"rows": [{"i": i, "pad": "y" * 512} for i in range(4000)]}   # ~2 MB serialized
body = json.dumps(payload, indent=1) + "\n"

def once():
    if mode == "atomic":
        G.atomic_write_text(target, body)
    else:
        target.write_text(body)          # the known-bad: truncate-then-write, interruptible

if mode == "atomic":
    def run() -> int:
        while True:
            once()
        return 0
    G.main(run)
else:
    while True:
        once()
""" % {"here": str(HERE)}

# A child that dies at the WORST possible instant, deterministically. No race, no sleep, no luck:
#
#   naive  — opens the target, writes half the body, and calls os._exit mid-write. That is what
#            `write_text` looks like to a reader when the process dies inside it: the file has
#            been truncated and only partly rewritten.
#   atomic — dies in the equivalent window, which for the atomic path is AFTER the temp file is
#            written and fsynced but BEFORE the rename. `os.replace` is swapped for an immediate
#            `os._exit`, so the process dies at the one instant that could possibly tear the
#            artifact. The target must still hold its original bytes.
#
# Same payload, same interruption point in the causal sense, opposite outcomes. That comparison is
# the whole claim, and neither leg depends on timing.
CRASH_CHILD = r"""
import json, os, pathlib, sys
sys.path.insert(0, %(here)r)
import gbtypes as G

mode, target = sys.argv[1], pathlib.Path(sys.argv[2])
body = json.dumps({"rows": [{"i": i, "pad": "y" * 512} for i in range(4000)]}, indent=1) + "\n"

if mode == "naive":
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(body[: len(body) // 2])
        fh.flush()
        os._exit(1)                      # died inside write_text's window
else:
    G.os.replace = lambda *a, **k: os._exit(1)   # die after fsync, before the rename
    G.atomic_write_text(target, body)
""" % {"here": str(HERE)}


def _crash_writer(mode: str, workdir: pathlib.Path) -> Tuple[bool, str]:
    """Kill a writer at the tearing instant; report (artifact_intact, what_the_reader_sees)."""
    target = workdir / f"crash_{mode}.json"
    original = json.dumps({"original": True}, indent=1) + "\n"
    target.write_text(original)
    child = workdir / f"crashchild_{mode}.py"
    child.write_text(CRASH_CHILD)
    subprocess.run(
        [sys.executable, str(child), mode, str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )
    raw = target.read_text(errors="replace")
    try:
        json.loads(raw)
    except ValueError:
        return False, f"unparseable, {len(raw)} bytes of a torn document"
    return True, (
        "the original, byte for byte" if raw == original else "a complete replacement"
    )


def _interrupt_writer(mode: str, workdir: pathlib.Path) -> Tuple[bool, int, int]:
    """Run a writer in a loop, SIGINT it mid-flight, and report (artifact_intact, rc, tmp_left)."""
    target = workdir / f"{mode}.json"
    original = {"original": True}
    target.write_text(json.dumps(original, indent=1) + "\n")
    child = workdir / f"child_{mode}.py"
    child.write_text(CHILD)

    proc = subprocess.Popen(
        [sys.executable, str(child), mode, str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.45)  # let it get well inside the write loop
    proc.send_signal(signal.SIGINT)
    try:
        _, _err = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1

    raw = target.read_bytes()
    try:
        json.loads(raw)
        intact = True  # complete document: either the original or a full payload
    except ValueError:
        intact = False  # a reader would see this as "unreadable" == "not measured"
    tmp_left = len(
        [
            p
            for p in workdir.iterdir()
            if p.name.startswith(f".{target.name}.") and p.suffix == ".tmp"
        ]
    )
    return intact, rc, tmp_left


def t5_cancel_correctness() -> None:
    """Two deterministic legs prove the invariant; a signal leg proves the cancel SEMANTICS.

    The deterministic legs answer "is the invariant true", and they cannot be flaky because the
    process is killed at a fixed point rather than at whatever instant a signal happens to land.
    The signal leg answers the separate question "does a cancelled run exit 130 and clean up",
    which only has an answer when a real signal is delivered — so it is run repeatedly and any
    single violation fails.
    """
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d)

        # Leg 1 (deterministic, known-bad): the hazard must be real.
        naive_intact, naive_saw = _crash_writer("naive", work)
        if naive_intact:
            record(
                "t5-cancel-correctness",
                VACUOUS,
                f"the naive writer survived a kill mid-write ({naive_saw}) — the hazard this "
                f"claims to fix was not demonstrated, so the atomic leg proves nothing",
            )
            return

        # Leg 2 (deterministic, known-good): the same kill, at the atomic path's worst instant.
        atomic_intact, atomic_saw = _crash_writer("atomic", work)
        if not atomic_intact:
            record(
                "t5-cancel-correctness",
                FAIL,
                f"atomic writer killed before its rename left a torn artifact ({atomic_saw}) — "
                f"the invariant is false",
            )
            return
        if "original" not in atomic_saw:
            record(
                "t5-cancel-correctness",
                FAIL,
                f"atomic writer killed before its rename changed the artifact ({atomic_saw}) — "
                f"a write that never completed must not be visible",
            )
            return

        # Leg 3 (signals): cancel semantics — exit 130, artifact whole, no temp residue.
        attempts = 4
        for i in range(attempts):
            intact, rc, tmp_left = _interrupt_writer("atomic", work)
            if not intact:
                record(
                    "t5-cancel-correctness",
                    FAIL,
                    f"SIGINT attempt {i + 1} left a torn artifact — the invariant is false",
                )
                return
            if tmp_left:
                record(
                    "t5-cancel-correctness",
                    FAIL,
                    f"{tmp_left} temp file(s) left behind on attempt {i + 1}",
                )
                return
            if rc != 130:
                record(
                    "t5-cancel-correctness",
                    FAIL,
                    f"cancelled run exited {rc}, not 130 — the scheduler cannot tell cancel from failure",
                )
                return

    record(
        "t5-cancel-correctness",
        PASS,
        f"killed mid-write: naive left {naive_saw}, atomic left {atomic_saw}; "
        f"{attempts} SIGINTs: artifact whole, zero temp residue, exit 130 every time",
    )


# -- T6: the derived parser is the type, and it refuses what the type forbids ------------------
def t6_typed_argv() -> None:
    import dataclasses as dc

    import gbargs

    @dc.dataclass(frozen=True)
    class Probe:
        """probe"""

        device: Tuple[str, ...] = gbargs.arg(help="device label", required=True)
        dry_run: bool = gbargs.arg(help="print only")
        limit: int = gbargs.arg(default=20, help="rows")
        verdict: G.Verdict = gbargs.arg(default=G.Verdict.GREEN, help="verdict")

    got = gbargs.parse(
        Probe,
        ["--device", "studio", "--device", "brain", "--dry-run", "--verdict", "RED"],
    )
    if got.device != ("studio", "brain"):
        record(
            "t6-typed-argv",
            FAIL,
            f"repeatable flag did not become a tuple: {got.device!r}",
        )
        return
    if got.verdict is not G.Verdict.RED:
        record("t6-typed-argv", FAIL, f"enum flag stayed a string: {got.verdict!r}")
        return
    if got.limit != 20 or got.dry_run is not True:
        record(
            "t6-typed-argv",
            FAIL,
            f"defaults/flags wrong: limit={got.limit} dry_run={got.dry_run}",
        )
        return
    try:
        got.limit = 5  # type: ignore[misc]
        record(
            "t6-typed-argv",
            FAIL,
            "parsed arguments are mutable — a later function can rewrite the inputs",
        )
        return
    except dc.FrozenInstanceError:
        pass

    # Rejection legs: each MUST exit 2 (argparse's usage error), not fall through with a bad value.
    for bad_argv, why in (
        ([], "missing a required flag"),
        (
            ["--device", "studio", "--verdict", "MAUVE"],
            "an enum value outside the type",
        ),
        (["--device", "studio", "--limit", "twenty"], "a non-integer for an int field"),
    ):
        try:
            gbargs.parse(Probe, bad_argv)
        except SystemExit as exc:
            if exc.code == 2:
                continue
            record("t6-typed-argv", FAIL, f"{why}: exited {exc.code}, expected 2")
            return
        record(
            "t6-typed-argv",
            FAIL,
            f"{why} was ACCEPTED — the parser does not enforce its own type",
        )
        return
    record(
        "t6-typed-argv",
        PASS,
        "derived parser: tuple/enum/bool/int typed and frozen; 3 known-bad argvs each rejected with exit 2",
    )


# -- T7: the severity lattice classifies, and worse dominates ----------------------------------
def t7_severity_lattice() -> None:
    """The four-valued lattice, checked against Asupersync's stated ordering.

    Transliterated from `src/types/outcome.rs:213-227` in the pinned mirror (`fa3c01ae`, v0.4.9):
    `Ok < Err < Cancelled < Panicked`, worse dominates. Two things must hold: the ORDER, and the
    classification of a real `Proc` — in particular that a run we stopped is CANCELLED, not ERR,
    because the board's old boolean could not tell those apart.
    """
    S = G.Severity
    if not (S.OK < S.ERR < S.CANCELLED < S.PANICKED):
        record(
            "t7-severity-lattice",
            FAIL,
            "the lattice is not ordered Ok < Err < Cancelled < Panicked",
        )
        return
    if S.worst([]) is not S.OK:
        record(
            "t7-severity-lattice", FAIL, "an empty set of outcomes must dominate to OK"
        )
        return
    if S.worst([S.OK, S.CANCELLED, S.ERR]) is not S.CANCELLED:
        record("t7-severity-lattice", FAIL, "worse did not dominate")
        return

    def proc(code: int, timed_out: bool = False, truncated: bool = False) -> G.Proc:
        return G.Proc(
            argv=("x",),
            code=code,
            out="",
            err="",
            timed_out=timed_out,
            truncated=truncated,
            elapsed_s=0.0,
        )

    cases = [
        (proc(0), S.OK, "GREEN", "clean run"),
        (proc(1), S.ERR, "RED", "the child answered no"),
        (
            proc(0, timed_out=True),
            S.CANCELLED,
            "ERROR",
            "we stopped it on the deadline",
        ),
        (
            proc(0, truncated=True),
            S.CANCELLED,
            "ERROR",
            "we stopped reading at the cap",
        ),
        (proc(-9), S.PANICKED, "ERROR", "killed by a signal"),
    ]
    for pr, want_sev, want_verdict, why in cases:
        if pr.severity is not want_sev:
            record(
                "t7-severity-lattice",
                FAIL,
                f"{why}: classified {pr.severity.name}, expected {want_sev.name}",
            )
            return
        if pr.severity.verdict.value != want_verdict:
            record(
                "t7-severity-lattice",
                FAIL,
                f"{why}: {pr.severity.name} reads as {pr.severity.verdict.value}, expected {want_verdict}",
            )
            return
        if pr.ok is not (want_sev is S.OK):
            record(
                "t7-severity-lattice", FAIL, f"{why}: `ok` disagrees with `severity`"
            )
            return

    # The distinction the old boolean threw away: both are not-ok, and they are NOT the same.
    stopped, failed = proc(0, timed_out=True), proc(1)
    if stopped.severity is failed.severity:
        record(
            "t7-severity-lattice",
            FAIL,
            "a run we cancelled and a run that failed still classify identically",
        )
        return
    if stopped.ok or failed.ok:
        record("t7-severity-lattice", FAIL, "a cancelled or failed run reported ok")
        return
    record(
        "t7-severity-lattice",
        PASS,
        f"lattice ordered and dominating; 5 Proc shapes classify correctly; "
        f"cancelled ({stopped.severity.name}) and failed ({failed.severity.name}) no longer collapse",
    )


# -- T8: the CLI's runtime contract holds -------------------------------------------------------
def t8_cli_contract() -> None:
    """`bin/gb` obeys the contract it publishes — proven by invoking it, not by reading it.

    g24 checks the SHAPE statically (verbs registered, handlers wired, codes documented). Shape
    is not behaviour: a verb can be wired and still print a log line onto stdout, or exit 0 on a
    usage error. This leg runs the binary.

    Four properties, each with a known-bad that must be impossible to pass by accident:
      stdout purity   every `--json` verb's stdout parses WHOLE as one document
      exit codes      a usage error is 2, a red board is 1 — not both 1, not both 0
      no ANSI         nothing emits escapes under NO_COLOR/CI/TERM=dumb
      intent          a one-edit typo returns the corrected command, not a list dump
    """
    gb = HERE / "gb"
    if not gb.is_file():
        record(
            "t8-cli-contract",
            FAIL,
            "bin/gb is missing — the producer family has no entry point",
        )
        return
    env = {**os.environ, "NO_COLOR": "1", "CI": "true", "TERM": "dumb"}

    def call(args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(gb), *args],
            cwd=str(HERE.parent),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    for verb in (
        ["capabilities"],
        ["info"],
        ["triage", "--json"],
        ["doctor", "--scope", "gate", "--json"],
        ["validate", "plugin", "--json"],
        ["repair", "--scope", "fixtures", "--json"],
    ):
        out = call(verb).stdout
        try:
            json.loads(out)
        except ValueError as exc:
            record(
                "t8-cli-contract",
                FAIL,
                f"`gb {' '.join(verb)}` stdout is not one whole JSON document ({exc}) — "
                f"something is printing diagnostics onto the data channel",
            )
            return

    for args, want, why in (
        (["nosuchverb"], 2, "unknown verb"),
        (["doctor", "--scope", "nope"], 2, "unknown scope"),
        (["repair"], 2, "repair with no scope"),
        (["help", "nope"], 2, "unknown help topic"),
        (["capabilities"], 0, "a clean read"),
    ):
        rc = call(args).returncode
        if rc != want:
            record(
                "t8-cli-contract",
                FAIL,
                f"{why}: `gb {' '.join(args)}` exited {rc}, expected {want}",
            )
            return

    for verb in (["triage"], ["doctor"], ["quickstart"], ["examples"], ["robot-docs"]):
        r = call(verb)
        if "\x1b[" in r.stdout or "\x1b[" in r.stderr:
            record(
                "t8-cli-contract",
                FAIL,
                f"`gb {verb[0]}` emitted ANSI escapes under NO_COLOR/CI/TERM=dumb",
            )
            return

    for typo, want in (
        ("dctor", "gb doctor"),
        ("healht", "gb health"),
        ("capabilites", "gb capabilities"),
        ("triage--json", "gb triage --json"),
    ):
        r = call([typo])
        if r.returncode != 2 or want not in r.stderr:
            record(
                "t8-cli-contract",
                FAIL,
                f"typo {typo!r} did not return the correction {want!r} "
                f"(rc={r.returncode}); an agent that mistypes learns nothing",
            )
            return

    record(
        "t8-cli-contract",
        PASS,
        "6 --json verbs emit whole documents on stdout; 5 exit codes correct; 5 human verbs "
        "ANSI-free under NO_COLOR/CI; 4 typos each answered with the corrected command",
    )


def main() -> int:
    for test in (
        t1_typed_verdict,
        t2_bounded_read,
        t3_deadline,
        t4_bounded_capture,
        t5_cancel_correctness,
        t6_typed_argv,
        t7_severity_lattice,
        t8_cli_contract,
    ):
        try:
            test()
        except Exception as exc:  # a crashing proof is a failed proof
            record(test.__name__, FAIL, f"raised {type(exc).__name__}: {exc}")
    bad = [r for r in rows if r[1] == FAIL]
    vac = [r for r in rows if r[1] == VACUOUS]
    print(
        f"\nSELFTEST {'FAIL' if bad else ('VACUOUS' if vac else 'PASS')} — "
        f"{len(rows) - len(bad) - len(vac)}/{len(rows)} proven"
        + (f", {len(vac)} inconclusive" if vac else "")
        + (f", {len(bad)} failed" if bad else "")
    )
    return 1 if bad else (2 if vac else 0)


if __name__ == "__main__":
    sys.exit(main())
