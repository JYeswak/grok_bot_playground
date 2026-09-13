"""gbtypes — the typed spine every producer and gate in this repo stands on.

WHY THIS EXISTS (measured 2026-09-11). Three defects were structural, not incidental:

  1. `result(check: str, verdict: str, detail: str)` — three strings in a row. Nothing stops
     `result(GREEN, "g8", ...)`. A transposition is a silently wrong verdict, and a verdict is
     the only thing this repo produces.
  2. 43 artifact writes, 0 atomic. A tick killed mid-write (Ctrl-C, laptop sleep, SIGTERM from the
     scheduler) leaves a truncated JSON. The next tick reads it through `load()`, which maps
     unreadable -> None -> "not measured". A cancelled run therefore erases a measurement and
     reports it as an absence. That is the exact failure this repo's doctrine calls unforgivable.
  3. `subprocess.run(...)` with no timeout and unbounded capture. A hung fetch hangs the tick
     forever; a runaway child fills memory with captured output.

Each fix is stated as a TYPE or an INVARIANT that a checker can enforce, not as a convention.

RUST TARGET: ASUPERSYNC, NOT TOKIO. This repo's Rust stack is Asupersync — a cancel-correct,
capability-secure runtime, not a Tokio wrapper — so naming Tokio APIs here would have aimed the
port at the wrong house. Every mapping below was read out of the pinned source, not remembered:
`/Volumes/ZestData/dicklesworthstone-mirror/asupersync` @ `fa3c01ae` (v0.4.9), the exact rev
`uds-lmju-base` pins. v0.4.11 is published beyond it; re-read before citing.

  Severity / Outcome        -> `types::outcome::{Severity, Outcome}` — FOUR-valued, a severity
                               lattice `Ok < Err < Cancelled < Panicked` where worse dominates
                               (src/types/outcome.rs:213-227)
  Verdict / Cell            -> `#[derive(Serialize, Deserialize)] enum`
  CheckResult               -> `#[derive(Serialize)] struct` (frozen == moved by value)
  Proc                      -> `Outcome<Output, ProcessError>`; the booleans below are the
                               hand-rolled version of its `Cancelled` / `Panicked` variants
  run(argv, timeout_s=)     -> `Command::output_async(&mut self, cx: &Cx)` (src/process.rs:2190)
                               with `kill_on_drop(true)` (src/process.rs:1965). The DEADLINE does
                               NOT ride the call: it is region-scoped via
                               `time::deadline::with_deadline(scope, policy)`
                               (src/time/deadline.rs:203). Cancellation rides `&Cx`.
  atomic_write_*            -> `fs::write_atomic(path, contents)` (src/fs/path_ops.rs:580), or
                               the two-phase `stage_write_atomic(..)` -> `StagedAtomicWrite`
                               -> `.commit()` (src/fs/path_ops.rs:539, 515) when the caller must
                               hold the staged obligation open
  cancel checkpoints        -> `Cx::checkpoint(&self) -> Result<(), Error>` (src/cx/cx.rs — cite
                               the SYMBOL, not the line: it moved 2240 -> 2303 between 0.4.9 and
                               0.4.10, and cx.rs is the one file cited here that differs across
                               the two. outcome.rs, path_ops.rs, process.rs and deadline.rs are
                               byte-identical across both, so those line numbers are stable.
                               0.4.11 is published past both; re-measure before citing it.
  _uninterruptible()        -> NOTHING. It has no Rust counterpart, and that is the finding:
                               see the note under CANCEL-CORRECTNESS.

CANCEL-CORRECTNESS, stated precisely. A cancelled operation leaves the filesystem in exactly one
of two states: the artifact as it was before the operation, or the artifact complete. Never a
third. This is enforced by construction (write to a temp file in the SAME directory, fsync, then
`os.replace` — atomic on POSIX within a filesystem), not by a try/except that hopes to clean up.

What Asupersync does instead of our signal mask, and why it is better. Our `_uninterruptible()`
blocks SIGINT/SIGTERM around the write because a Python signal handler can raise INSIDE the
cleanup and orphan the temp file. Asupersync gets the same guarantee from a TYPE boundary
instead of a syscall: the cancellable work (`stage_write_atomic`) is `async`, and the part that
must not be interleaved (`StagedAtomicWrite::commit`) is a plain `fn` — a sync function has no
await points, so cancellation cannot land inside it. Its doc says so outright: "The rename is
synchronous: once this method begins, async cancellation cannot interleave with the target
mutation." The staged value is `#[must_use = "a staged atomic write must be committed or
deliberately dropped"]` and its `Drop` removes the temp file, so the obligation is in the type
and the cleanup cannot itself be cancelled.

So the mask is a Python-only workaround for a guarantee Rust gets structurally. When this ports,
`_uninterruptible()` disappears — it does not become an equivalent. That is the whole shape of
the port: our hand-rolled invariants become someone else's type system.

MEMORY SAFETY, as far as the word means anything in Python: no unbounded read of an
attacker-or-vendor-controlled byte stream. Every read of a file or a child process is capped, and
hitting the cap is a REPORTED FACT (`truncated=True`), never a silent short read.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import errno
import json
import os
import pathlib
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from typing import (
    Any,
    Callable,
    Dict,
    Final,
    Generic,
    Iterator,
    Literal,
    Mapping,
    Optional,
    Sequence,
    TextIO,
    Tuple,
    TypeVar,
    Union,
)

__all__ = [
    "Severity",
    "Verdict",
    "Cell",
    "CheckResult",
    "Coverage",
    "Proc",
    "Cancelled",
    "Cx",
    "Outcome",
    "OutcomeOk",
    "OutcomeErr",
    "OutcomeCancelled",
    "OutcomePanicked",
    "combine",
    "run",
    "read_text_capped",
    "read_json_capped",
    "atomic_write_text",
    "atomic_write_bytes",
    "atomic_write_json",
    "cancel_scope",
    "main",
    "MAX_FILE_BYTES",
    "MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT_S",
]

# ---------------------------------------------------------------------------------------------
# Bounds. Named, not sprinkled. Every one is a cap on bytes this process will hold from a source
# it does not control. Chosen from measurement: the largest real artifact in this repo is the
# 168-page llms.txt census at ~1.1 MB, and the largest child output is the market snapshot's 322
# plugin rows at ~380 KB. Both caps sit ~8x above the measured worst case.
# ---------------------------------------------------------------------------------------------
MAX_FILE_BYTES: Final[int] = 8 * 1024 * 1024
MAX_OUTPUT_BYTES: Final[int] = 4 * 1024 * 1024
DEFAULT_TIMEOUT_S: Final[float] = 60.0


class Severity(enum.IntEnum):
    """The four-valued severity lattice, transliterated from Asupersync.

    `Ok < Err < Cancelled < Panicked`, and worse dominates when outcomes combine
    (src/types/outcome.rs:213-227, `Severity::from_u8` pins the same 0..3 ordinals). `IntEnum`
    so `max()` IS the combination rule — the lattice is the ordering, not a lookup table.

    Why this repo needed it. Our board already orders GREEN < RED < ERROR and takes the worst,
    which turns out to agree with the lattice — but ours COLLAPSES the top two: a producer that
    was cancelled and a producer that crashed both land on ERROR. Before this tick that collapse
    was free, because nothing here could be cancelled cleanly. Now that a cancelled run exits 130
    with its artifact intact, "the scheduler stopped us" and "the code blew up" are different
    operational facts, and a type that cannot tell them apart is losing one.
    """

    OK = 0
    ERR = 1
    CANCELLED = 2
    PANICKED = 3

    @classmethod
    def worst(cls, severities: "Sequence[Severity]") -> "Severity":
        """The dominating severity, or OK over an empty set. `max()` because the enum IS ordered."""
        return max(severities, default=cls.OK)

    @property
    def verdict(self) -> "Verdict":
        """How this severity reads on the board. CANCELLED and PANICKED both map to ERROR —
        the board's vocabulary is three-valued — but they arrive here DISTINGUISHED, so a caller
        that wants the finer fact can still ask for it. Collapsing at the edge is a choice;
        collapsing at the source was the defect."""
        return (
            Verdict.GREEN
            if self is Severity.OK
            else (Verdict.RED if self is Severity.ERR else Verdict.ERROR)
        )


class Verdict(str, enum.Enum):
    """A gate check's answer. `str` mixin so `json.dumps` emits the bare word and existing
    consumers (`grep GREEN`, the tick's `verdict=` line) keep working unchanged.

    ERROR is not a synonym for RED. RED means "measured, and the answer is bad". ERROR means
    "could not measure" — a broken fetcher, an absent artifact. The repo's first rule is that
    those two never collapse into each other, so they are separate variants, not a bool.
    """

    GREEN = "GREEN"
    RED = "RED"
    ERROR = "ERROR"


class Cell(str, enum.Enum):
    """A coverage cell. EMPTY and UNMEASURED are the distinction the coverage ledger exists to
    preserve: "measured, and the true answer is zero" vs "nobody read it"."""

    MEASURED = "MEASURED"
    EMPTY = "EMPTY"
    UNMEASURED = "UNMEASURED"
    BLOCKED = "BLOCKED"


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """One gate check's outcome.

    Frozen on purpose: a verdict that can be mutated after it is computed is a verdict that can be
    laundered. In Rust this is a plain struct moved by value; here `frozen=True` is the closest
    available equivalent and it is load-bearing, not decoration.

    The field ORDER is (check, verdict, detail) to match the call sites it replaces, but the
    types now make the transposition that order invites impossible: `CheckResult("g8", "GREEN")`
    fails the checker because `str` is not `Verdict`.
    """

    check: str
    verdict: Verdict
    detail: str
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        # Runtime guard for the untyped callers that still exist. Enforced here rather than in a
        # linter because a wrong verdict is the one defect this repo cannot ship.
        if not isinstance(self.verdict, Verdict):
            raise TypeError(
                f"verdict must be a Verdict, got {type(self.verdict).__name__} "
                f"({self.verdict!r}) — a bare string here is how a RED becomes a GREEN"
            )
        if not self.check:
            raise ValueError(
                "a CheckResult with no check id cannot be attributed to anything"
            )

    @property
    def ok(self) -> bool:
        """GREEN only. ERROR is not ok — that is the whole point of having three variants."""
        return self.verdict is Verdict.GREEN

    def to_json(self) -> Dict[str, Any]:
        """The wire shape the existing artifacts and `gb-surface-gate --json` already emit."""
        return {
            "check": self.check,
            "verdict": self.verdict.value,
            "detail": self.detail,
            **dict(self.extra),
        }


@dataclasses.dataclass(frozen=True)
class Coverage:
    """One row of the coverage ledger: a named facet, how it was read, and what came back."""

    facet: str
    cell: Cell
    gate: str
    artifact: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "facet": self.facet,
            "verdict": self.cell.value,
            "gate": self.gate,
            "artifact": self.artifact,
        }


@dataclasses.dataclass(frozen=True)
class Proc:
    """A finished child process, with the two facts the untyped version threw away: whether it
    was killed on the deadline, and whether we stopped reading it at the cap.

    `subprocess.CompletedProcess` has neither, so a timeout looked like a nonzero exit and a
    truncated capture looked like short output. Both were reported as the child's answer.
    """

    argv: Tuple[str, ...]
    code: int
    out: str
    err: str
    timed_out: bool
    truncated: bool
    elapsed_s: float

    @property
    def severity(self) -> Severity:
        """Where this run sits on the lattice — the classification, not a boolean.

        Maps onto `Outcome<Output, ProcessError>` one variant at a time:

          OK        ran to completion, exit 0, and we read all of it
          ERR       ran to completion and said no (a nonzero exit is the child's ANSWER)
          CANCELLED we stopped it: the deadline expired, or we stopped reading at the cap. In
                    both cases we do not know what it would have said, which is a different
                    fact from "it failed"
          PANICKED  killed by a signal (negative returncode on POSIX) — it did not get to answer

        The old `ok` boolean folded all four into two and threw the distinction away at the
        source. Cancelled-vs-failed is exactly the distinction this repo spent the tick earning.
        """
        if self.timed_out or self.truncated:
            return Severity.CANCELLED
        if self.code < 0:
            return Severity.PANICKED
        return Severity.OK if self.code == 0 else Severity.ERR

    @property
    def ok(self) -> bool:
        """Exit 0, ran to completion, and we read all of it. Derived from `severity` so the two
        can never disagree; kept because callers read it and it says the common thing."""
        return self.severity is Severity.OK


class Cancelled(Exception):
    """Raised inside a `cancel_scope` when SIGINT/SIGTERM arrives.

    An exception, not a flag, because the correct response to cancellation is to unwind — and
    unwinding through `atomic_write_*` provably cannot leave a partial artifact: the temp file is
    unlinked and the real path is never touched.
    """

    def __init__(self, signum: int) -> None:
        super().__init__(f"cancelled by signal {signum}")
        self.signum = signum

    @property
    def exit_code(self) -> int:
        """128+signum, the shell convention: 130 for SIGINT, 143 for SIGTERM."""
        return 128 + self.signum


# ---------------------------------------------------------------------------------------------
# Cx and checkpoint — the capability bundle, additive and inert until collectors adopt it
# ---------------------------------------------------------------------------------------------
# Asupersync port: `Cx` (src/cx/cx.rs) + `time::deadline::with_deadline(scope, policy)`
# (src/time/deadline.rs:203). The deadline rides the REGION, not the call; cancellation rides
# `&Cx`. Here the region's values ride a frozen dataclass and `checkpoint` is the probe loops
# call to ask whether the region must stop.
CHECKPOINT_SIGNUM: Final[int] = 15
T = TypeVar("T")


@dataclasses.dataclass(frozen=True)
class Cx:
    """The region a loop runs in: its deadline, its remaining budget, and the hooks that can
    stop it early.

    Frozen like every other spine type: a region that can be mutated after it is entered is a
    deadline that can be laundered. `deadline` is a `time.monotonic()` timestamp, `inf` when
    the region carries none; `budget` is remaining loop spend, negative when unbounded.
    `cancel` is the polling hook the port fills in (SIGTERM today); `clock` is injectable so
    a checkpoint is provable without spending real seconds. `http`/`fs`/`proc`/`rng` are the
    capability slots the port fills — None today, which is why no caller is rewritten yet.
    """

    deadline: float = float("inf")
    budget: int = -1
    cancel: Callable[[], bool] = lambda: False  # noqa: E731 - a hook slot, not logic
    clock: Callable[[], float] = time.monotonic
    http: Optional[Callable[..., Any]] = None
    fs: Optional[Callable[..., Any]] = None
    proc: Optional[Callable[..., Any]] = None
    rng: Optional[Callable[..., Any]] = None

    def checkpoint(self, deadline: float, budget: int) -> None:
        """No-op-safe probe: returns None when the region is healthy, raises `Cancelled`
        when it must stop.

        Three stop conditions, worst first: the cancel hook fired, the clock passed the
        region deadline, or the loop spent its budget. The code is SIGTERM's (143): the
        region asked the loop to stop, which is what SIGTERM means to the scheduler, and
        `main` already exits 143 with the artifact intact for exactly that signal.

        Call AFTER the loop's own budget guard, not instead of it: the budget leg is the
        backstop that unwinds a loop whose guard was lost, and a loop that leans on it for
        policy defers nothing and writes nothing. `gb-links.build_document` calls it past
        its `budget <= 0` guard, so the budget leg never fires there today — the live legs
        are cancel and deadline only.
        """
        if self.cancel():
            raise Cancelled(CHECKPOINT_SIGNUM)
        if self.clock() > deadline:
            raise Cancelled(CHECKPOINT_SIGNUM)
        if budget <= 0:
            raise Cancelled(CHECKPOINT_SIGNUM)


# ---------------------------------------------------------------------------------------------
# Outcome — the four-valued result, transliterated from Asupersync's types::outcome
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class OutcomeOk(Generic[T]):
    """The operation answered. Carries the value; severity OK."""

    value: T

    @property
    def severity(self) -> Severity:
        return Severity.OK


@dataclasses.dataclass(frozen=True)
class OutcomeErr(Generic[T]):
    """The operation ran to completion and said no. A nonzero exit is the child's ANSWER,
    not a failure to measure — hence a variant, not an exception."""

    error: str

    @property
    def severity(self) -> Severity:
        return Severity.ERR


@dataclasses.dataclass(frozen=True)
class OutcomeCancelled(Generic[T]):
    """We stopped it: the deadline expired, the budget ran out, or the operator did. We do
    not know what it would have said, which is a different fact from "it failed"."""

    reason: str = ""

    @property
    def severity(self) -> Severity:
        return Severity.CANCELLED


@dataclasses.dataclass(frozen=True)
class OutcomePanicked(Generic[T]):
    """It did not get to answer: killed by a signal, an invariant violated mid-flight."""

    reason: str = ""

    @property
    def severity(self) -> Severity:
        return Severity.PANICKED


Outcome = Union[OutcomeOk[T], OutcomeErr[T], OutcomeCancelled[T], OutcomePanicked[T]]


def combine(outcomes: Sequence[Outcome[T]]) -> Severity:
    """Worse dominates; empty is OK. `max()` because `Severity` IS the lattice — the same
    rule as `Severity.worst`, over outcomes instead of bare severities, so the two can
    never disagree about what "worse" means."""
    return Severity.worst([o.severity for o in outcomes])


# ---------------------------------------------------------------------------------------------
# Bounded reads
# ---------------------------------------------------------------------------------------------
def read_text_capped(
    path: pathlib.Path, *, max_bytes: int = MAX_FILE_BYTES
) -> Optional[str]:
    """Read at most `max_bytes`. None when absent or unreadable — the same answer on purpose,
    because every caller treats "I could not read it" as "not measured".

    Raises ValueError when the file is LARGER than the cap. That is deliberate: silently
    returning the first 8 MB of a 40 MB file would be a short read presented as the whole file,
    which is the class of bug this module exists to make impossible.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > max_bytes:
        raise ValueError(
            f"{path} is {size} bytes, over the {max_bytes}-byte cap — refusing a short read"
        )
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def read_json_capped(
    path: pathlib.Path, *, max_bytes: int = MAX_FILE_BYTES
) -> Optional[Any]:
    """Bounded `load()`. Unreadable, absent, and unparseable all answer None."""
    raw = read_text_capped(path, max_bytes=max_bytes)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# Bounded, deadline-enforced child processes
# ---------------------------------------------------------------------------------------------
def _pump(
    proc: "subprocess.Popen[bytes]",
    buffers: Dict[int, bytearray],
    *,
    deadline: float,
    cap: int,
) -> Tuple[bool, bool]:
    """Stream both pipes until EOF, the deadline, or the cap. Returns (timed_out, truncated).

    Split out of `run` so the I/O loop can be read as one thing — and in the Asupersync port this
    function DISAPPEARS rather than being translated. `Child::wait_with_output_async(self, cx: &Cx)`
    (src/process.rs:2540) already does the bounded, cancellable wait, and the deadline comes from
    the enclosing region rather than from a selector loop we hand-roll. Where a real race is
    needed, the native drain-correct combinator is `Cx::race_drained` (src/cx/cx.rs:3970) or
    `Cx::race_drained_timeout` (:4034), which protocol-cancel AND drain every loser before
    returning — not a bare `select!` that leaks the branches it abandoned.
    """
    timed_out = truncated = False
    sel = selectors.DefaultSelector()
    try:
        assert proc.stdout is not None and proc.stderr is not None
        sel.register(proc.stdout, selectors.EVENT_READ, 1)
        sel.register(proc.stderr, selectors.EVENT_READ, 2)
        open_streams = 2
        while open_streams > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _mask in sel.select(timeout=min(remaining, 0.25)):
                chunk = os.read(key.fileobj.fileno(), 65536)  # type: ignore[union-attr]
                if not chunk:
                    sel.unregister(key.fileobj)
                    open_streams -= 1
                    continue
                buf = buffers[int(key.data)]
                room = cap - len(buf)
                buf += chunk[:room] if room > 0 else b""
                if len(buffers[1]) + len(buffers[2]) >= cap:
                    truncated = True
                    open_streams = 0
                    break
    finally:
        sel.close()
    return timed_out, truncated


def _reap(proc: "subprocess.Popen[bytes]") -> None:
    """Ensure the child is dead and its pipes are closed, whatever happened above.

    A child still running here missed its deadline or hit the cap — either way nobody is reading
    it any more, and a process that outlives the tick that spawned it is a leak. `SIGKILL` only
    after `SIGTERM`-by-`kill()` fails to reap within 5s.
    """
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError as exc:  # already gone between poll and kill
            if exc.errno != errno.ESRCH:
                raise
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def run(
    argv: Sequence[str],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    cwd: Optional[pathlib.Path] = None,
    env: Optional[Mapping[str, str]] = None,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> Proc:
    """Run a child with a MANDATORY deadline and a bounded capture.

    `timeout_s` is keyword-only with a finite default and no way to say "wait forever" — the
    unbounded wait is not expressible. Output is streamed through a selector and the child is
    killed the moment it crosses `max_output_bytes`, so a runaway producer cannot exhaust memory;
    the cap being hit is reported as `truncated`, never swallowed.

    Asupersync port: `Command::kill_on_drop(true)` (src/process.rs:1965) +
    `Command::output_async(&mut self, cx: &Cx)` (src/process.rs:2190), inside a region carrying
    `time::deadline::with_deadline(scope, policy)` (src/time/deadline.rs:203). Note what moves:
    the deadline stops being an argument to this call and becomes a property of the REGION the
    call runs in, and `kill_on_drop` replaces our explicit `_reap` — the child is killed by the
    same drop that unwinds the region, so a leaked child stops being possible rather than being
    cleaned up after the fact.
    """
    started = time.monotonic()
    argv_t = tuple(str(a) for a in argv)
    buffers: Dict[int, bytearray] = {1: bytearray(), 2: bytearray()}
    timed_out = truncated = False

    proc = subprocess.Popen(  # noqa: S603 - argv is a list, never a shell string
        list(argv_t),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=dict(env) if env is not None else None,
    )
    try:
        timed_out, truncated = _pump(
            proc, buffers, deadline=started + timeout_s, cap=max_output_bytes
        )
    finally:
        _reap(proc)

    return Proc(
        argv=argv_t,
        code=proc.returncode if proc.returncode is not None else -1,
        out=buffers[1].decode("utf-8", "replace"),
        err=buffers[2].decode("utf-8", "replace"),
        timed_out=timed_out,
        truncated=truncated,
        elapsed_s=round(time.monotonic() - started, 3),
    )


@contextlib.contextmanager
def _uninterruptible() -> Iterator[None]:
    """Block SIGINT/SIGTERM for the duration of a critical section; deliver them on exit.

    WHY (measured 2026-09-11 by this repo's own t5 proof, attempt 6 of 6): `try/finally` is NOT
    sufficient for cancel-correctness in Python. A signal handler that raises can fire at ANY
    bytecode boundary — including one INSIDE the `finally` that performs the cleanup, between
    `os.close(fd)` and `os.unlink(tmp)`. The cleanup is then itself cancelled and the temp file
    survives. Rust does not have this hazard: `Drop` runs to completion and cancellation is
    observed only at `.await` points, never injected mid-statement.

    Masking restores exactly that property: the signal stays PENDING in the kernel while the
    section runs, and is delivered the instant the mask lifts. Cancellation is therefore observed
    at a checkpoint of our choosing, which is the semantics the Rust port will have for free.

    Windows has no `pthread_sigmask`; there the section is unmasked and the weaker `finally`
    guarantee applies. Stated, not hidden — this repo does not run there, and a silent
    platform-dependent invariant would be worse than a named one.
    """
    mask = getattr(signal, "pthread_sigmask", None)
    if mask is None:
        yield
        return
    blocked = {signal.SIGINT, signal.SIGTERM}
    previous = mask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        mask(signal.SIG_SETMASK, previous)


# ---------------------------------------------------------------------------------------------
# Atomic writes — the cancel-correctness primitive
# ---------------------------------------------------------------------------------------------
def _atomic_write(path: pathlib.Path, data: Any, *, binary: bool) -> None:
    """Write `data` to `path` atomically, or leave `path` exactly as it was.

    Temp file in the SAME directory (rename(2) is only atomic within a filesystem), fsync the
    data before the rename so a power loss cannot land an empty inode at the real name, then
    `os.replace`. There is no window in which a reader can observe a partial artifact.

    The whole operation — create, write, fsync, rename, AND cleanup — runs inside
    `_uninterruptible()`. Without that, a cancel signal can land inside the cleanup path itself
    and orphan the temp file; this repo's t5 proof caught exactly that, so the mask is load-
    bearing and not belt-and-braces. A cancel raised during the section is delivered immediately
    after it, with the artifact already in one of its two legal states.

    One implementation, three typed entry points: text, bytes, and JSON differ only in how the
    payload is produced, and a second copy of a rename-dance is a second place for the invariant
    to rot.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with _uninterruptible():
        fd, tmp_name = None, ""
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
            )
            opener = (
                os.fdopen(fd, "wb") if binary else os.fdopen(fd, "w", encoding="utf-8")
            )
            with opener as fh:
                fd = None  # ownership moved to the file object
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, str(path))
            tmp_name = ""
        finally:
            if fd is not None:
                os.close(fd)
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass


def atomic_write_text(path: pathlib.Path, body: str) -> None:
    """Atomically write text. See `_atomic_write` for the guarantee and how it is enforced."""
    _atomic_write(path, body, binary=False)


def atomic_write_bytes(path: pathlib.Path, body: bytes) -> None:
    """Atomically write bytes — for vendor payloads captured verbatim (hashed page bodies), where
    decoding to text would change the thing whose digest we are about to record."""
    _atomic_write(path, body, binary=True)


def atomic_write_json(path: pathlib.Path, payload: Any, *, indent: int = 1) -> None:
    """`atomic_write_text` of a JSON document, newline-terminated like every artifact here.

    Serialisation happens BEFORE the file is created: an unserialisable payload must fail without
    having touched the filesystem at all.
    """
    body = json.dumps(payload, indent=indent, default=str) + "\n"
    atomic_write_text(path, body)


# ---------------------------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------------------------
class cancel_scope:  # noqa: N801 - lowercase: it is used as a context manager, not constructed
    """Install SIGINT/SIGTERM handlers that raise `Cancelled` at the next interpreter checkpoint.

    Restores the previous handlers on exit, so this composes and does not leak global state into
    a caller that installed its own. Rust: a `CancellationToken` passed down and selected on.
    """

    def __init__(
        self, signums: Sequence[int] = (signal.SIGINT, signal.SIGTERM)
    ) -> None:
        self._signums = tuple(signums)
        self._previous: Dict[int, Any] = {}

    def __enter__(self) -> "cancel_scope":
        def _raise(signum: int, _frame: Any) -> None:
            raise Cancelled(signum)

        for sig in self._signums:
            try:
                self._previous[sig] = signal.signal(sig, _raise)
            except ValueError:
                # Not the main thread: signals are not ours to install. Do not pretend otherwise.
                pass
        return self

    def __exit__(self, *_exc: Any) -> Literal[False]:
        for sig, prev in self._previous.items():
            try:
                signal.signal(sig, prev)
            except ValueError:
                pass
        return False


def main(fn: Callable[[], int]) -> None:
    """Entry-point wrapper: run `fn` under a cancel scope and exit with the right code.

    A cancelled producer exits 130/143 — the shell convention the scheduler and `gb-weekly.sh`
    already read — instead of a traceback and exit 1, which is indistinguishable from a real
    failure and would be recorded in the tick as one.

    TWO DEFECTS FIXED 2026-09-11, both measured independently by two producers against a real
    closed pipe on fd1+fd2:

    1. The cancellation notice is printed to stderr INSIDE `except Cancelled`. When stderr is
       also a closed pipe that print raises BrokenPipeError, and the `except BrokenPipeError`
       arm is a SIBLING — it cannot catch an exception raised inside its own try's handler. A
       cancelled run exited 1 instead of 130/143: safe (never 0) but a scheduled tick could not
       tell "the operator stopped it" from "it broke". The print is now guarded; a closed pipe
       may lose the MESSAGE, never the CODE.

    2. The BrokenPipeError arm exited 0 UNCONDITIONALLY, which erased a verdict. Measured: a
       producer whose `fn` returned 1 for a failing selftest reported success to anything that
       piped it. A pipe closing during ordinary output is still not a failure (exit 0), but if
       the EPIPE happened while REPORTING a cancellation, `__context__` carries that Cancelled
       and its code is the honest answer. We never invent a code for an unexplained EPIPE.
    """

    def _quiet(stream: "TextIO") -> None:
        """Point a stream at /dev/null so the interpreter's shutdown flush cannot complain."""
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), stream.fileno())
        except (OSError, ValueError):
            pass

    try:
        with cancel_scope():
            sys.exit(fn())
    except Cancelled as c:
        try:
            print(f"cancelled: {c} — no partial artifact written", file=sys.stderr)
        except BrokenPipeError:
            _quiet(sys.stderr)
        sys.exit(c.exit_code)
    except BrokenPipeError as e:
        # `| head` closed the pipe. Not a failure of the producer — unless the pipe broke while
        # we were reporting a cancellation, in which case that code is the truth.
        _quiet(sys.stdout)
        ctx = e.__context__
        sys.exit(ctx.exit_code if isinstance(ctx, Cancelled) else 0)
