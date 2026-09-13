#!/usr/bin/env python3
"""gbtypes-selftest — prove the typed spine's claims against a real OS, not against a mock.

Every claim in `gbtypes.py` is a claim about behaviour under adverse conditions (a deadline, a
runaway child, a signal landing mid-write). A claim like that is worth exactly as much as its
executable counter-example, so each test here has BOTH legs:

    known-bad : the untyped/naive construction, which MUST be caught failing
    known-good: the gbtypes construction, which MUST hold

A test whose known-bad leg cannot be made to fail is reported as INCONCLUSIVE, never as a pass.
That is the difference between a proof and a green tick.

The two expensive proofs (t5, t8) run on worker threads beside the cheap ones. Concurrency
changes no assertion here: the same legs fire against the same OS, and the transcript is
printed in canonical t1..t8 order however the threads interleave. What it does change is the
obligation to clean up — see `_spawn` below.

exit: 0 all proven · 1 a claim failed · 2 a known-bad leg never fired (test is vacuous)
      130 cancelled — no proof was reached, which is not the same as a failure
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeVar,
    cast,
)

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gbtypes as G  # noqa: E402

PASS, FAIL, VACUOUS = "PASS", "FAIL", "VACUOUS"

# One lock for the two pieces of state workers share: the result rows and the child registry.
# Both are touched for microseconds, neither is ever held across a wait, and nesting is
# impossible because nothing under this lock calls anything that takes it.
_LOCK = threading.Lock()
rows: List[Tuple[str, str, str]] = []


def record(name: str, verdict: str, detail: str) -> None:
    """Collect a verdict. Printing is deferred to `_report` so the transcript is in proof
    order rather than in whatever order the threads happened to finish."""
    with _LOCK:
        rows.append((name, verdict, detail))


# ---------------------------------------------------------------------------------------------
# Child processes, and the obligation that comes with running proofs concurrently
#
# Sequentially, a cancel unwinds through the one `subprocess` call in flight and that call's own
# cleanup runs. With workers in flight there is no such unwind: the main thread is where a
# signal is delivered, and the workers are somewhere else entirely. So every child this file
# starts is registered here, and the main thread reaps the registry before it exits.
#
# This is not theoretical. Measured on the sequential version, 2026-09-11: SIGINT delivered
# during t5 left an orphaned writer looping on 2 MB writes forever, because `_interrupt_writer`
# spawned a child and then slept with no `finally`. Adding threads without this registry would
# have widened that leak; adding the registry closes it.
# ---------------------------------------------------------------------------------------------
_CHILDREN: Set["subprocess.Popen[str]"] = set()
# Set once, by the main thread, the instant a cancel is observed. A one-shot reap is not enough
# on its own: measured 2026-09-11, SIGINT at 0.15s reaped the children that existed and then the
# still-running t5 worker spawned its NEXT writer into a process that was already exiting, which
# orphaned it. The flag closes that window — after a cancel, `_spawn` refuses.
_CANCELLED = threading.Event()


def _spawn(
    argv: Sequence[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    capture: bool = True,
) -> "subprocess.Popen[str]":
    """The only place this file starts a process. Refuses after a cancel; registered otherwise."""
    if _CANCELLED.is_set():
        raise G.Cancelled(signal.SIGINT)
    proc = subprocess.Popen(  # noqa: S603 - argv is a list, never a shell string
        [str(a) for a in argv],
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        text=True,
    )
    with _LOCK:
        _CHILDREN.add(proc)
    if (
        _CANCELLED.is_set()
    ):  # lost the race with the reap: undo it here rather than leak it
        proc.kill()
        with _LOCK:
            _CHILDREN.discard(proc)
        proc.wait(timeout=5)
        raise G.Cancelled(signal.SIGINT)
    return proc


def _settle(proc: "subprocess.Popen[str]", timeout_s: float) -> Tuple[int, str, str]:
    """Wait with a deadline, kill on overrun, deregister whatever happens."""
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    finally:
        with _LOCK:
            _CHILDREN.discard(proc)
    code = proc.returncode if proc.returncode is not None else -1
    return code, out or "", err or ""


def _call(
    argv: Sequence[str],
    *,
    timeout_s: float,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    capture: bool = True,
) -> Tuple[int, str, str]:
    """Spawn and settle in one step, for the calls that do not need the handle in between."""
    return _settle(_spawn(argv, cwd=cwd, env=env, capture=capture), timeout_s)


def _reap_all() -> int:
    """Stop new spawns, then kill every registered child. Returns how many there were.

    Order matters: the flag first, so a worker racing us cannot register a child after the
    registry has been drained; the kills second.
    """
    _CANCELLED.set()
    with _LOCK:
        victims = list(_CHILDREN)
        _CHILDREN.clear()
    for proc in victims:
        try:
            proc.kill()
        except OSError:
            pass  # already gone between the registry read and the kill
    for proc in victims:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return len(victims)


_T = TypeVar("_T")
_R = TypeVar("_R")


def _start(fns: Sequence[Callable[[], None]]) -> List[threading.Thread]:
    """Daemon threads on purpose: on cancel the main thread reaps the children and exits, and
    a worker blocked on a dead child must not be able to hold the interpreter open."""
    threads = [threading.Thread(target=fn, daemon=True) for fn in fns]
    for thread in threads:
        thread.start()
    return threads


def _join(threads: Sequence[threading.Thread]) -> None:
    """Join in slices. A signal is only ever delivered to the main thread, so the main thread
    must keep reaching a checkpoint where the interpreter can raise it."""
    for thread in threads:
        while thread.is_alive():
            thread.join(0.05)


def _map_parallel(
    fn: Callable[[_T], _R], items: Sequence[_T], *, width: int
) -> List[_R]:
    """`map` on daemon threads: input order preserved, first failure re-raised, never swallowed."""
    slots: List[Any] = [None] * len(items)
    errors: List[BaseException] = []
    cursor = iter(range(len(items)))

    def worker() -> None:
        while True:
            with _LOCK:
                index = next(cursor, -1)
            if index < 0:
                return
            try:
                slots[index] = fn(items[index])
            except BaseException as exc:  # a worker that died must never read as a pass
                with _LOCK:
                    errors.append(exc)
                return

    _join(_start([worker] * max(1, min(width, len(items)))))
    if errors:
        raise errors[0]
    return [cast(_R, slot) for slot in slots]


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

mode, target, ready = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
payload = {"rows": [{"i": i, "pad": "y" * 512} for i in range(4000)]}   # ~2 MB serialized
body = json.dumps(payload, indent=1) + "\n"

def once():
    if mode == "atomic":
        G.atomic_write_text(target, body)
    else:
        target.write_text(body)          # the known-bad: truncate-then-write, interruptible

def loop():
    once()
    ready.write_text("inside the write loop\n")   # the handshake; see `_interrupt_writer`
    while True:
        once()

if mode == "atomic":
    def run() -> int:
        loop()
        return 0
    G.main(run)
else:
    loop()
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
    _call(
        [sys.executable, str(child), mode, str(target)],
        timeout_s=60,
        capture=False,
    )
    raw = target.read_text(errors="replace")
    try:
        json.loads(raw)
    except ValueError:
        return False, f"unparseable, {len(raw)} bytes of a torn document"
    return True, (
        "the original, byte for byte" if raw == original else "a complete replacement"
    )


def _interrupt_writer(
    mode: str, workdir: pathlib.Path, attempt: int
) -> Tuple[bool, int, int]:
    """Run a writer in a loop, SIGINT it mid-flight, and report (artifact_intact, rc, tmp_left).

    The child says when it is inside the write loop; before this it was a blind
    `time.sleep(0.45)`. The sleep was a guess in BOTH directions. Too short on a loaded machine
    and the signal lands before the child has written anything — a leg that proves nothing
    while still reporting PASS, which is the exact failure mode this file exists to refuse. Too
    long on an idle one and every attempt pays ~0.4s waiting for something already done.
    Waiting for the child to SAY it completed a write is strictly stronger and strictly faster:
    the signal still arrives asynchronously at an arbitrary point of an infinite write loop, so
    what is being proven has not changed.
    """
    target = workdir / f"{mode}.json"
    original = {"original": True}
    target.write_text(json.dumps(original, indent=1) + "\n")
    child = workdir / f"child_{mode}.py"
    child.write_text(CHILD)
    ready = workdir / f"ready_{mode}_{attempt}"

    proc = _spawn(
        [sys.executable, str(child), mode, str(target), str(ready)], capture=False
    )
    deadline = time.monotonic() + 30.0
    while not ready.exists():
        if proc.poll() is not None or time.monotonic() > deadline:
            _rc, _out, err = _settle(proc, 5)
            raise RuntimeError(
                f"the {mode} writer never reported reaching its write loop "
                f"({err.strip()[:120] or 'no diagnostics'}) — the signal leg never ran, so "
                f"a PASS here would be vacuous"
            )
        time.sleep(0.002)
    proc.send_signal(signal.SIGINT)
    rc, _out, _err = _settle(proc, 15)

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
            intact, rc, tmp_left = _interrupt_writer("atomic", work, i)
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

    The invocations run concurrently and each distinct argv runs ONCE, its result reused by
    every property that asks about it. That is a change of schedule, not of evidence: the same
    argvs are executed against the same binary and every assertion below still runs, in order.
    It is sound because every verb here is a READ — the one `repair` is dry-run unless
    `--apply` is passed, and `gb`'s producers write through `atomic_write_*` — so no two of
    these calls can observe each other half-finished.
    """
    gb = HERE / "gb"
    if not gb.is_file():
        record(
            "t8-cli-contract",
            FAIL,
            "bin/gb is missing — the producer family has no entry point",
        )
        return
    # Clear the re-entry guard explicitly. `gb doctor` exports GB_IN_DOCTOR into every child,
    # and this proof IS a child when doctor runs its spine subsystem — but t8's whole job is to
    # drive the CLI on purpose, which is the one case the guard must not block. Opting out here
    # keeps the guard honest (it still stops ACCIDENTAL re-entry from any other producer) and
    # keeps this proof meaningful. The verbs t8 drives are all cheap and scoped, so clearing it
    # cannot resurrect the fan-out recursion the guard exists to prevent.
    env = {k: v for k, v in os.environ.items() if k != "GB_IN_DOCTOR"}
    env.update({"NO_COLOR": "1", "CI": "true", "TERM": "dumb"})

    def call(args: List[str]) -> Tuple[int, str, str]:
        return _call(
            [str(gb), *args],
            timeout_s=300,
            cwd=str(HERE.parent),
            env=env,
        )

    json_verbs = [
        ["capabilities"],
        ["info"],
        ["triage", "--json"],
        ["doctor", "--scope", "gate", "--json"],
        ["validate", "plugin", "--json"],
        ["repair", "--scope", "fixtures", "--json"],
    ]
    codes = [
        (["nosuchverb"], 2, "unknown verb"),
        (["doctor", "--scope", "nope"], 2, "unknown scope"),
        (["repair"], 2, "repair with no scope"),
        (["help", "nope"], 2, "unknown help topic"),
        (["capabilities"], 0, "a clean read"),
    ]
    # `doctor` is scoped here on purpose: the unscoped fan-out re-enters this very
    # selftest through its spine subsystem, which measured 8.3s of pure recursion
    # depth for a check that only looks for escape bytes in the output.
    human = [
        ["triage"],
        ["doctor", "--scope", "gate"],
        ["quickstart"],
        ["examples"],
        ["robot-docs"],
    ]
    typos = [
        ("dctor", "gb doctor"),
        ("healht", "gb health"),
        ("capabilites", "gb capabilities"),
        ("triage--json", "gb triage --json"),
    ]

    plan: List[List[str]] = []
    for argv in [
        *json_verbs,
        *[a for a, _c, _w in codes],
        *human,
        *[[t] for t, _c in typos],
    ]:
        if argv not in plan:
            plan.append(argv)
    # Width 8: enough that the two slowest calls (`doctor --scope gate`, ~0.25s) overlap
    # everything else, low enough that the machine is not measuring its own contention.
    answers = dict(zip((tuple(a) for a in plan), _map_parallel(call, plan, width=8)))

    for verb in json_verbs:
        out = answers[tuple(verb)][1]
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

    for args, want_code, why in codes:
        rc = answers[tuple(args)][0]
        if rc != want_code:
            record(
                "t8-cli-contract",
                FAIL,
                f"{why}: `gb {' '.join(args)}` exited {rc}, expected {want_code}",
            )
            return

    # Every invocation is checked for escapes, not just the five human verbs: the answers are
    # already in hand, so the wider claim costs nothing and a `--json` verb that coloured its
    # output would corrupt the data channel rather than merely look wrong.
    for argv in plan:
        _rc, out, err = answers[tuple(argv)]
        if "\x1b[" in out or "\x1b[" in err:
            record(
                "t8-cli-contract",
                FAIL,
                f"`gb {' '.join(argv)}` emitted ANSI escapes under NO_COLOR/CI/TERM=dumb",
            )
            return

    for typo, want_fix in typos:
        rc, _out, err = answers[(typo,)]
        if rc != 2 or want_fix not in err:
            record(
                "t8-cli-contract",
                FAIL,
                f"typo {typo!r} did not return the correction {want_fix!r} "
                f"(rc={rc}); an agent that mistypes learns nothing",
            )
            return

    record(
        "t8-cli-contract",
        PASS,
        f"{len(plan)} distinct invocations, concurrent: 6 --json verbs emit whole documents on "
        f"stdout; 5 exit codes correct; all {len(plan)} ANSI-free under NO_COLOR/CI; 4 typos "
        f"each answered with the corrected command",
    )


# Each proof, its canonical row name, and whether it is worth a thread. `slow` is measured, not
# guessed: t5 and t8 are ~95% of the suite's wall clock and spend nearly all of it waiting on
# child processes, while the other six together cost under a second. Running the two big ones
# beside the cheap ones bounds the suite at max(proof) instead of sum(proofs).
TESTS: Tuple[Tuple[str, Callable[[], None], bool], ...] = (
    ("t1-typed-verdict", t1_typed_verdict, False),
    ("t2-bounded-read", t2_bounded_read, False),
    ("t3-deadline", t3_deadline, False),
    ("t4-bounded-capture", t4_bounded_capture, False),
    ("t5-cancel-correctness", t5_cancel_correctness, True),
    ("t6-typed-argv", t6_typed_argv, False),
    ("t7-severity-lattice", t7_severity_lattice, False),
    ("t8-cli-contract", t8_cli_contract, True),
)


def _guarded(name: str, test: Callable[[], None]) -> None:
    try:
        test()
    except G.Cancelled:
        # A cancel is not a failed proof, it is the ABSENCE of a proof. Recording it as a FAIL
        # would be a lie in one direction and swallowing it would be a lie in the other: the
        # suite must stop and say 130. (The sequential version caught this with a bare
        # `except Exception`, and `Cancelled` is an Exception.)
        raise
    except Exception as exc:  # a crashing proof is a failed proof
        record(name, FAIL, f"raised {type(exc).__name__}: {exc}")


def _worker(name: str, test: Callable[[], None]) -> Callable[[], None]:
    """Wrap a proof for a thread. A cancel reaches a worker only as a refused spawn, and the
    main thread already owns the exit code — so the worker just stops, without a thread
    traceback that would bury the one line the operator needs to read."""

    def go() -> None:
        try:
            _guarded(name, test)
        except G.Cancelled:
            pass

    return go


def _report(elapsed_s: float) -> int:
    order = {name: i for i, (name, _fn, _slow) in enumerate(TESTS)}
    for name, verdict, detail in sorted(
        rows, key=lambda r: order.get(r[0], len(order))
    ):
        print(f"[{verdict:<7}] {name}: {detail}")
    bad = [r for r in rows if r[1] == FAIL]
    vac = [r for r in rows if r[1] == VACUOUS]
    print(
        f"\nSELFTEST {'FAIL' if bad else ('VACUOUS' if vac else 'PASS')} — "
        f"{len(rows) - len(bad) - len(vac)}/{len(rows)} proven"
        + (f", {len(vac)} inconclusive" if vac else "")
        + (f", {len(bad)} failed" if bad else "")
        + f" in {elapsed_s:.2f}s"
    )
    return 1 if bad else (2 if vac else 0)


def _body() -> int:
    started = time.monotonic()
    workers = _start([_worker(n, fn) for n, fn, slow in TESTS if slow])
    try:
        for name, test, slow in TESTS:
            if not slow:
                _guarded(name, test)
        _join(workers)
    except BaseException:
        # The main thread is the only one a signal reaches, so it is the only one that can
        # honour it. Kill the registry before unwinding: the daemon workers are about to be
        # torn down mid-wait and their children would otherwise outlive this process.
        killed = _reap_all()
        print(
            f"reaped {killed} child process(es); {len(rows)}/{len(TESTS)} proofs had answered",
            file=sys.stderr,
        )
        raise
    return _report(time.monotonic() - started)


if __name__ == "__main__":
    # `G.main` is the same entry point every other producer here uses: a cancel exits 130 with
    # a stated reason instead of a traceback, which is what `gb doctor` and `gb-weekly.sh` read.
    G.main(_body)
