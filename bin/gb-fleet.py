#!/usr/bin/env python3
"""gb-fleet — talk to the live fleet: roster, routines, one message, or a named delete.

WHY THIS EXISTS (measured 2026-09-11, and it is an indictment of the previous surface).

Across one evening the operator's agent hand-rolled the same three RPC calls SIX times as inline
`importlib.spec_from_file_location` scripts — listing Bots, reading a Bot's routines, and sending
a Bot a message. Every one of those was a gap in this CLI being papered over instead of closed,
and each hand-rolled copy got something subtly wrong:

  * `ListGrokBotAgentAutomations` was called with the NUMERIC id and answered
    `400 invalid_argument: Grok Bot agent id must be the agent's UUID`. The record carries BOTH
    `id` (numeric, what the write RPCs take) and `agentId` (the UUID, what the automation read
    takes), and picking the wrong one is a 400, not a wrong answer — but only if you look.
  * A roster read off `sand-client-persistence` reported a Bot as DELETED while it was live in
    the API and visible on another Mac. That blob is a per-machine CACHE. Absence there is never
    evidence a Bot does not exist. **So `roster` here reads the API and says so.**
  * `updatedAtMs` came back as a STRING from one path and an int from another; `int(x)/1000`
    raised `TypeError: unsupported operand type(s) for /: 'str' and 'int'` mid-analysis.

Three verbs, so none of that has to be rediscovered:

    gb fleet roster              every Bot the SERVER knows, with both ids
    gb fleet routines [--bot N]  every routine, its schedule, run count and provenance
    gb fleet send --bot N --text ...   one message to one Bot
    gb fleet delete NAME [NAME…]       plan a named delete; --apply --yes performs it

ON `send` BEING A WRITE. It is deliberately narrow: one Bot, one message, no broadcast. It
earns its place because it is now load-bearing — a Bot creates its own routine when asked in
chat (measured: `Weekly receipt`, cron `CRON_TZ=America/Denver 45 7 * * 1`,
`provenance: "user"`), which makes "deploy a template, then send one message" the one-touch
path. It refuses without `--yes` so it cannot fire by accident, and it prints the exact text
first.

ON `delete` BEING A WRITE. Rollback already deletes through `DeleteGrokBotAgent` with a string
id and the dual `--apply --yes` gate. Hire-on-demand had no inverse. This verb is that inverse:
named Bots only, never `--all`, never the rest of the fleet. Routines are NOT deleted by the
RPC (measured 2026-09-12); leftover automations print ORPHAN_AUTOMATION_RESIDUE, not CLEAN.
An unread routine list after confirmed absence prints DELETED plus `automation unread`,
not DELETE_FAILED — the Bot left; the check did not.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import json
import pathlib
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gbargs  # noqa: E402
import gbtypes  # noqa: E402

# The TRANSPORT is a library now (gbrpc.py), not a borrowed slice of a producer. Importing
# it normally deletes the python 3.9.6 `spec_from_file_location` + `sys.modules` dance that
# every borrower had to remember, and stops `gb dogfood audit` counting this file as one
# more hand-roller of gb-pull-inventory's read.
from gbrpc import SUPPORT, access_token, rpc, write_rpc  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


class FleetError(RuntimeError):
    """A live read or write failed. Carries the RPC so a caller cannot misattribute it."""


def load_module(name: str, path: pathlib.Path) -> Any:
    """Import a repo producer by path.

    `sys.modules[name] = mod` BEFORE `exec_module` is mandatory on this machine's python 3.9.6:
    without it, any module defining a `@dataclass` dies inside `dataclasses._is_type` with
    `AttributeError: 'NoneType' object has no attribute '__dict__'`, because `cls.__module__` is
    not yet registered. `gb-pull-inventory.py` defines dataclasses, so this is not hypothetical.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise FleetError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Transport:
    """The transport names, bound from `gbrpc` rather than dynamically imported."""

    SUPPORT = SUPPORT
    access_token = staticmethod(access_token)
    rpc = staticmethod(rpc)
    write_rpc = staticmethod(write_rpc)


def puller() -> Any:
    return _Transport


def as_int(value: Any) -> int:
    """`updatedAtMs` arrives as a str on one path and an int on another. Normalise, never crash."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def iso(ms: int) -> str:
    if not ms:
        return "unknown"
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%SZ"
    )


@dataclasses.dataclass(frozen=True)
class Bot:
    """One Bot as the SERVER describes it. Both ids are carried because they are not
    interchangeable: writes take `numeric_id`, the automations read takes `uuid`."""

    name: str
    numeric_id: str
    uuid: str
    title: str
    charter_chars: int
    updated_ms: int

    def as_json(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "numeric_id": self.numeric_id,
            "uuid": self.uuid,
            "title": self.title,
            "charter_chars": self.charter_chars,
            "updated_at": iso(self.updated_ms),
        }


@dataclasses.dataclass(frozen=True)
class Routine:
    bot: str
    name: str
    schedule: str
    enabled: bool
    runs: int
    next_run_ms: int
    provenance: str

    def as_json(self) -> Dict[str, Any]:
        return {
            "bot": self.bot,
            "name": self.name,
            "schedule": self.schedule,
            "enabled": self.enabled,
            "runs": self.runs,
            "next_run": iso(self.next_run_ms),
            "provenance": self.provenance,
        }


def fetch_roster(pull: Any, token: str) -> List[Bot]:
    status, resp = pull.rpc(token, "ListGrokBotAgents", {})
    if status != 200 or not isinstance(resp, dict):
        raise FleetError(f"ListGrokBotAgents -> {status}")
    out: List[Bot] = []
    for a in resp.get("agents") or []:
        out.append(
            Bot(
                name=str(a.get("name") or "?"),
                numeric_id=str(a.get("id") or ""),
                uuid=str(a.get("agentId") or ""),
                title=str(a.get("title") or ""),
                charter_chars=len(str(a.get("description") or "")),
                updated_ms=as_int(a.get("updatedAtMs")),
            )
        )
    out.sort(key=lambda b: -b.updated_ms)
    return out


def fetch_routines(pull: Any, token: str, bots: Sequence[Bot]) -> List[Routine]:
    """Routines per Bot, addressed by UUID.

    The numeric id is REJECTED here with a 400 — `agent id must be the agent's UUID`. That is the
    single most expensive thing to rediscover, so it is encoded rather than commented.
    """
    out: List[Routine] = []
    for b in bots:
        if not b.uuid:
            continue
        status, resp = pull.rpc(
            token, "ListGrokBotAgentAutomations", {"agent_id": b.uuid}
        )
        if status != 200 or not isinstance(resp, dict):
            continue
        for a in resp.get("automations") or []:
            raw = a.get("recordJson")
            rec: Dict[str, Any] = {}
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        rec = parsed
                except ValueError:
                    rec = {}
            runs = rec.get("runs")
            out.append(
                Routine(
                    bot=b.name,
                    name=str(rec.get("name") or a.get("automationId") or "?"),
                    schedule=str(
                        rec.get("triggerDescription") or rec.get("schedule") or "?"
                    ),
                    enabled=bool(rec.get("isEnabled")),
                    runs=len(runs) if isinstance(runs, list) else 0,
                    next_run_ms=as_int(rec.get("nextRunAt")),
                    provenance=str(rec.get("provenance") or "?"),
                )
            )
    return out


def send_message(pull: Any, token: str, bot: Bot, text: str) -> Tuple[int, str]:
    """One message to one Bot.

    NOT through `gb-pull-inventory.rpc`: that module refuses every write by contract, and it
    refused this one — "refusing to call SendGrokBotUserMessage: this tool is read-only by
    contract". That guard is right and stays. The write path already exists in
    `gb-handover.py`, which owns the POST, so this reuses it rather than opening a second one.
    """
    hand = load_module("gb_handover", BIN / "gb-handover.py")
    status, resp = hand.send(token, bot.uuid, text)
    return status, resp if isinstance(resp, str) else json.dumps(resp)[:300]


def pick(bots: Sequence[Bot], needle: str) -> Optional[Bot]:
    """Exact name first, then unique case-insensitive prefix. An AMBIGUOUS match returns None
    rather than guessing which of the operator's Bots to message."""
    for b in bots:
        if b.name == needle:
            return b
    hits = [b for b in bots if b.name.lower().startswith(needle.lower())]
    return hits[0] if len(hits) == 1 else None


# Delete is identity-strict: a name collision is ambiguous, and a human may paste a
# numeric_id or uuid from `gb fleet roster`. send keeps pick() so two "Template"
# clones still message the first exact name (that path is load-bearing).
def resolve(bots: Sequence[Bot], needle: str) -> Optional[Bot]:
    """Unique Bot for a delete needle: unique exact name, unique numeric_id, unique uuid,
    else unique case-insensitive prefix. Anything else (missing or collision) is None."""
    exact = [b for b in bots if b.name == needle]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    by_id = [b for b in bots if b.numeric_id == needle]
    if len(by_id) == 1:
        return by_id[0]
    if len(by_id) > 1:
        return None
    by_uuid = [b for b in bots if b.uuid == needle]
    if len(by_uuid) == 1:
        return by_uuid[0]
    if len(by_uuid) > 1:
        return None
    hits = [b for b in bots if b.name.lower().startswith(needle.lower())]
    return hits[0] if len(hits) == 1 else None


Rpc = Callable[[str, str, Dict[str, Any]], Tuple[int, Any]]
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_REFUSED = 5


def delete_bots(
    needles: Sequence[str],
    bots: Sequence[Bot],
    *,
    apply: bool,
    yes: bool,
    write_rpc: Optional[Rpc] = None,
    read_rpc: Optional[Rpc] = None,
    token: str = "offline",
    routines: Optional[Sequence[Routine]] = None,
    delete_and_confirm: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Tuple[int, List[str]]:
    """Plan or apply a named delete. Writes only when both --apply and --yes survive.

    Returns (exit_code, human lines). Resolves every needle before the first write.
    Two needles for one Bot delete it once. Residue is not CLEAN.
    """
    lines: List[str] = []
    if apply ^ yes:
        lines.append(
            "REFUSED: deletion requires both --apply and --yes; nothing was written"
        )
        return EXIT_REFUSED, lines
    if not needles:
        lines.append("gb-fleet: delete needs a Bot name, numeric id, or uuid")
        return EXIT_USAGE, lines

    resolved: List[Bot] = []
    seen: Set[str] = set()
    for needle in needles:
        bot = resolve(bots, needle)
        if bot is None:
            lines.append(f"gb-fleet: no unique Bot matching {needle!r}")
            return EXIT_USAGE, lines
        key = bot.uuid or bot.numeric_id
        if key in seen:
            continue
        seen.add(key)
        resolved.append(bot)

    counts: Dict[str, int] = {}
    if routines:
        for r in routines:
            counts[r.bot] = counts.get(r.bot, 0) + 1

    if not apply:
        for bot in resolved:
            n = counts.get(bot.name)
            extra = f"  routines={n}" if n is not None else ""
            lines.append(
                f"  {bot.name}  numeric_id={bot.numeric_id}  uuid={bot.uuid}{extra}"
            )
        shown = " ".join(needles)
        lines.append("Nothing deleted. Apply with: gb fleet delete … --apply --yes")
        lines.append(f"  gb fleet delete {shown} --apply --yes")
        return EXIT_OK, lines

    if write_rpc is None or read_rpc is None:
        raise FleetError("delete apply needs write_rpc and read_rpc")
    confirm = delete_and_confirm
    if confirm is None:
        rebuild = load_module("gb_rebuild_fleet_delete", BIN / "gb-rebuild-fleet.py")
        confirm = rebuild.delete_and_confirm

    failed = False
    for bot in resolved:
        outcome = confirm(
            write_rpc=write_rpc,
            read_rpc=read_rpc,
            token=token,
            numeric_id=bot.numeric_id,
            agent_uuid=bot.uuid,
            emit=False,
        )
        verdict = str(outcome.get("verdict") or "DELETE_FAILED")
        lines.append(
            f"  {verdict} {bot.name} numeric_id={bot.numeric_id} uuid={bot.uuid}"
        )
        if (
            verdict == "DELETED"
            and str(outcome.get("automation_state") or "") == "UNKNOWN"
        ):
            lines.append("  automation unread")
        if verdict != "DELETED":
            failed = True
    return (EXIT_FINDINGS if failed else EXIT_OK), lines


def selftest() -> int:
    fails: List[str] = []
    checks = 0

    def check(ok: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            fails.append(label)

    # as_int: the crash that actually happened, plus the shapes around it
    check(as_int("1789") == 1789, "as_int did not accept a numeric string")
    check(as_int(1789) == 1789, "as_int did not accept an int")
    check(
        as_int(None) == 0 and as_int("x") == 0,
        "as_int did not degrade a bad value to 0",
    )
    # fires-on-known-bad: raw int() must actually fail on the string, else as_int proves nothing
    raised = False
    try:
        int(None) / 1000  # type: ignore[operator]
    except TypeError:
        raised = True
    check(raised, "known-bad: int(None) did not raise, so as_int's guard is vacuous")

    fleet = [
        Bot("CRM", "1", "uuid-1", "t", 1020, 5),
        Bot("Chief of Staff", "2", "uuid-2", "t", 1213, 9),
        Bot("Chief Analyst", "3", "uuid-3", "t", 700, 7),
    ]
    check(pick(fleet, "CRM") is not None, "exact name did not match")
    check(
        pick(fleet, "crm") is not None, "unique prefix did not match case-insensitively"
    )
    # ambiguity must REFUSE: two Bots start with "Chief"
    check(
        pick(fleet, "Chief") is None,
        "an ambiguous prefix picked a Bot instead of refusing",
    )
    check(pick(fleet, "nope") is None, "an unknown name matched something")

    r = Routine(
        "CRM", "Weekly receipt", "Every Monday 7:45", True, 0, 1789393500000, "user"
    )
    check(r.as_json()["runs"] == 0, "routine run count lost")
    check(
        r.as_json()["next_run"].startswith("2026-"), "next_run not rendered as a date"
    )
    b = fleet[0].as_json()
    check(
        b["numeric_id"] == "1" and b["uuid"] == "uuid-1",
        "both ids must survive to json",
    )

    # delete resolve: unique id / uuid, and a name collision is not identity
    twins = [
        Bot("Template", "10", "uuid-a", "t", 10, 1),
        Bot("Template", "11", "uuid-b", "t", 10, 2),
        Bot("Optima", "42", "uuid-opt", "t", 100, 3),
    ]
    check(resolve(twins, "Optima") is twins[2], "resolve exact unique name")
    check(resolve(twins, "42") is twins[2], "resolve unique numeric_id")
    check(resolve(twins, "uuid-opt") is twins[2], "resolve unique uuid")
    check(resolve(twins, "Template") is None, "resolve refuses a name collision")
    check(resolve(twins, "Chief") is None, "resolve refuses a missing name")
    check(pick(twins, "Template") is twins[0], "pick still first-exact-name for send")

    class _WriteSpy:
        def __init__(self) -> None:
            self.calls: List[Tuple[str, Dict[str, Any]]] = []

        def __call__(
            self, token: str, method: str, body: Dict[str, Any]
        ) -> Tuple[int, Any]:
            self.calls.append((method, body))
            return 200, {}

    def _read_still(
        _token: str, method: str, _body: Dict[str, Any]
    ) -> Tuple[int, Any]:
        if method == "ListGrokBotAgents":
            return 200, {
                "agents": [
                    {"id": "42", "agentId": "uuid-opt", "name": "Optima"},
                ]
            }
        return 200, {"automations": []}

    def _read_absent_clean(
        _token: str, method: str, _body: Dict[str, Any]
    ) -> Tuple[int, Any]:
        if method == "ListGrokBotAgents":
            return 200, {"agents": []}
        return 200, {"automations": []}

    def _read_absent_orphan(
        _token: str, method: str, _body: Dict[str, Any]
    ) -> Tuple[int, Any]:
        if method == "ListGrokBotAgents":
            return 200, {"agents": []}
        return 200, {"automations": [{"automationId": "routine-7"}]}

    def _read_absent_unread(
        _token: str, method: str, _body: Dict[str, Any]
    ) -> Tuple[int, Any]:
        if method == "ListGrokBotAgents":
            return 200, {"agents": []}
        return 404, "not found"

    def _confirm(
        *,
        write_rpc: Rpc,
        read_rpc: Rpc,
        token: str,
        numeric_id: str,
        agent_uuid: str,
        emit: bool = False,
    ) -> Dict[str, Any]:
        rebuild = load_module(
            "gb_rebuild_fleet_delete_selftest", BIN / "gb-rebuild-fleet.py"
        )
        return rebuild.delete_and_confirm(
            write_rpc=write_rpc,
            read_rpc=read_rpc,
            token=token,
            numeric_id=numeric_id,
            agent_uuid=agent_uuid,
            emit=emit,
        )

    # 1. plan without --apply/--yes never calls write_rpc
    spy = _WriteSpy()
    rc, out = delete_bots(
        ["Optima"],
        twins,
        apply=False,
        yes=False,
        write_rpc=spy,
        routines=[Routine("Optima", "tick", "daily", True, 0, 0, "user")],
    )
    check(rc == 0 and not spy.calls, "plan without apply/yes wrote")
    check(
        any("numeric_id=42" in line and "uuid=uuid-opt" in line for line in out),
        "plan card lost identity",
    )
    check(
        any("routines=1" in line for line in out),
        "plan card lost routine count",
    )
    check(
        any("Nothing deleted. Apply with: gb fleet delete" in line for line in out),
        "plan lost the apply hint",
    )

    # 2. --apply without --yes (and the reverse) never calls write_rpc, exit 5
    spy = _WriteSpy()
    rc, _out = delete_bots(
        ["Optima"], twins, apply=True, yes=False, write_rpc=spy
    )
    check(rc == 5 and not spy.calls, "apply without yes did not refuse")
    spy = _WriteSpy()
    rc, _out = delete_bots(
        ["Optima"], twins, apply=False, yes=True, write_rpc=spy
    )
    check(rc == 5 and not spy.calls, "yes without apply did not refuse")

    # 3. ambiguous prefix refuses, zero writes
    spy = _WriteSpy()
    chiefs = fleet
    rc, _out = delete_bots(
        ["Chief"], chiefs, apply=True, yes=True, write_rpc=spy, read_rpc=_read_absent_clean
    )
    check(rc == 2 and not spy.calls, "ambiguous prefix did not refuse before write")

    # 7. two names, one missing: refuse before any write (before 4 so the spy is clean)
    spy = _WriteSpy()
    rc, _out = delete_bots(
        ["Optima", "Missing"],
        twins,
        apply=True,
        yes=True,
        write_rpc=spy,
        read_rpc=_read_absent_clean,
        delete_and_confirm=_confirm,
    )
    check(rc == 2 and not spy.calls, "missing name among two did not refuse before write")

    # 8. two needles for the same Bot: one delete
    spy = _WriteSpy()
    rc, _out = delete_bots(
        ["Optima", "42"],
        twins,
        apply=True,
        yes=True,
        write_rpc=spy,
        read_rpc=_read_absent_clean,
        delete_and_confirm=_confirm,
    )
    check(
        rc == 0 and len(spy.calls) == 1,
        "two needles for one Bot did not delete once",
    )

    # 4. Delete body id is a str, never an int
    spy = _WriteSpy()
    rc, _out = delete_bots(
        ["Optima"],
        twins,
        apply=True,
        yes=True,
        write_rpc=spy,
        read_rpc=_read_absent_clean,
        delete_and_confirm=_confirm,
    )
    check(spy.calls, "apply+yes never called write_rpc")
    if spy.calls:
        method, body = spy.calls[0]
        check(method == "DeleteGrokBotAgent", "delete used the wrong method")
        check(
            body.get("id") == "42" and isinstance(body.get("id"), str),
            "Delete body id was not a JSON string",
        )
        check(not isinstance(body.get("id"), int), "Delete body id was an int")

    # 5. 200 + still on roster = STILL_PRESENT
    spy = _WriteSpy()
    rc, out = delete_bots(
        ["Optima"],
        twins,
        apply=True,
        yes=True,
        write_rpc=spy,
        read_rpc=_read_still,
        delete_and_confirm=_confirm,
    )
    check(rc != 0, "still-present delete exited 0")
    check(
        any("STILL_PRESENT" in line for line in out),
        "200 + still on roster did not print STILL_PRESENT",
    )

    # 6. 200 + absent + leftover automation = ORPHAN / non-zero
    spy = _WriteSpy()
    rc, out = delete_bots(
        ["Optima"],
        twins,
        apply=True,
        yes=True,
        write_rpc=spy,
        read_rpc=_read_absent_orphan,
        delete_and_confirm=_confirm,
    )
    check(rc != 0, "orphan residue exited 0 (lied CLEAN)")
    check(
        any("ORPHAN_AUTOMATION_RESIDUE" in line for line in out),
        "leftover automation did not print ORPHAN_AUTOMATION_RESIDUE",
    )
    check(
        not any(line.strip().startswith("DELETED") for line in out),
        "orphan residue printed DELETED (lied CLEAN)",
    )

    # 9. 200 + absent + automations UNKNOWN (non-200) = DELETED, not FAILED
    spy = _WriteSpy()
    rc, out = delete_bots(
        ["Optima"],
        twins,
        apply=True,
        yes=True,
        write_rpc=spy,
        read_rpc=_read_absent_unread,
        delete_and_confirm=_confirm,
    )
    check(rc == 0, "unread automation after confirmed absence exited non-zero")
    check(
        any("DELETED" in line for line in out),
        "unread automation after confirmed absence did not print DELETED",
    )
    check(
        not any("DELETE_FAILED" in line for line in out),
        "unread automation after confirmed absence printed DELETE_FAILED",
    )
    check(
        any("automation unread" in line for line in out),
        "unread automation after confirmed absence did not say unread",
    )

    for f in fails:
        print(f"FAIL: {f}")
    print(
        f"SELFTEST {'FAIL' if fails else 'PASS'} - {checks - len(fails)}/{checks} properties"
    )
    return 1 if fails else 0


@dataclasses.dataclass(frozen=True)
class FleetArgs:
    """Talk to the live fleet: roster, routines, one message, or a named delete."""

    action: str = gbargs.arg(
        default="roster",
        help="roster | routines | send | delete",
        choices=("roster", "routines", "send", "delete"),
        positional=True,
    )
    names: Tuple[str, ...] = gbargs.arg(
        default=(),
        help="delete: Bot name(s), numeric id, or uuid",
        positional=True,
    )
    bot: Optional[str] = gbargs.arg(
        default=None, help="Bot name (exact, or a unique prefix)"
    )
    text: Optional[str] = gbargs.arg(default=None, help="send: the message body")
    apply: bool = gbargs.arg(
        default=False, help="delete: actually delete (requires --yes)"
    )
    yes: bool = gbargs.arg(
        default=False,
        help="send: actually send it; delete: separately confirm destructive deletion",
    )
    json_out: bool = gbargs.arg(
        default=False, help="machine-readable envelope on stdout"
    )
    selftest: bool = gbargs.arg(default=False, help="prove this module's properties")


def body() -> int:
    # `--selftest` is answered BEFORE argparse, because the verb is positional-and-required and
    # `gb-fleet.py --selftest` would otherwise die on "the following arguments are required".
    # A selftest that cannot be invoked without also naming a verb is a selftest nobody runs.
    if "--selftest" in sys.argv[1:]:
        return selftest()
    parser = gbargs.build_parser(
        FleetArgs, prog="gb-fleet", description=(__doc__ or "").splitlines()[0]
    )
    ns = parser.parse_args()

    action = str(ns.action)
    as_json = bool(getattr(ns, "json_out", False))
    names = tuple(getattr(ns, "names", None) or ())
    apply = bool(getattr(ns, "apply", False))
    yes = bool(ns.yes)
    if action != "delete" and names:
        print("gb-fleet: unexpected arguments for this action", file=sys.stderr)
        return EXIT_USAGE
    if action == "delete":
        if apply ^ yes:
            print(
                "REFUSED: deletion requires both --apply and --yes; nothing was written",
                file=sys.stderr,
            )
            return EXIT_REFUSED
        if not names:
            print(
                "gb-fleet: delete needs a Bot name, numeric id, or uuid",
                file=sys.stderr,
            )
            return EXIT_USAGE
    try:
        pull = puller()
        token = pull.access_token(pull.SUPPORT)
        bots = fetch_roster(pull, token)
    except (FleetError, OSError, AttributeError) as exc:
        print(f"gb-fleet: could not read the fleet: {exc}", file=sys.stderr)
        return 3

    if action == "roster":
        payload = {
            "schema": "gb-fleet/1",
            "source": "ListGrokBotAgents (the SERVER, not the " "local cache)",
            "bots": [b.as_json() for b in bots],
        }
        if as_json:
            print(json.dumps(payload, indent=1))
        else:
            print(
                f"  {len(bots)} Bot(s) — per the server, not this machine's roster cache"
            )
            print(f"  {'name':<24}{'numeric':>9}  {'charter':>7}  updated")
            for b in bots:
                print(
                    f"  {b.name[:24]:<24}{b.numeric_id:>9}  {b.charter_chars:>7}  {iso(b.updated_ms)}"
                )
        return 0

    if action == "routines":
        chosen = [
            b
            for b in bots
            if not ns.bot or b.name == (pick(bots, str(ns.bot)) or b).name
        ]
        if ns.bot:
            one = pick(bots, str(ns.bot))
            if one is None:
                print(f"gb-fleet: no unique Bot matching {ns.bot!r}", file=sys.stderr)
                return 2
            chosen = [one]
        rs = fetch_routines(pull, token, chosen)
        if as_json:
            print(
                json.dumps(
                    {
                        "schema": "gb-fleet-routines/1",
                        "routines": [r.as_json() for r in rs],
                    },
                    indent=1,
                )
            )
        else:
            if not rs:
                print(
                    "  no routines on the selected Bot(s) — that is a measurement, not an error"
                )
            for r in rs:
                mark = "ran" if r.runs else "NEVER RAN"
                print(
                    f"  {r.bot[:18]:<18} {r.name[:28]:<28} {r.schedule[:30]:<30} "
                    f"{mark:<9} prov={r.provenance}"
                )
        return 0

    if action == "delete":
        routines: Optional[List[Routine]] = None
        if not apply:
            chosen: List[Bot] = []
            seen: Set[str] = set()
            for needle in names:
                one = resolve(bots, needle)
                if one is None:
                    chosen = []
                    break
                key = one.uuid or one.numeric_id
                if key in seen:
                    continue
                seen.add(key)
                chosen.append(one)
            if chosen:
                routines = fetch_routines(pull, token, chosen)
        rc, lines = delete_bots(
            names,
            bots,
            apply=apply,
            yes=yes,
            write_rpc=pull.write_rpc if apply else None,
            read_rpc=pull.rpc if apply else None,
            token=token,
            routines=routines,
        )
        dest = sys.stderr if rc in (EXIT_USAGE, EXIT_REFUSED) else sys.stdout
        if as_json:
            print(
                json.dumps(
                    {"schema": "gb-fleet-delete/1", "exit": rc, "lines": lines},
                    indent=1,
                )
            )
        else:
            for line in lines:
                print(line, file=dest)
        return rc

    # send
    if not ns.bot or not ns.text:
        print("gb-fleet: send needs --bot and --text", file=sys.stderr)
        return 2
    target = pick(bots, str(ns.bot))
    if target is None:
        print(f"gb-fleet: no unique Bot matching {ns.bot!r}", file=sys.stderr)
        return 2
    print(f"  to   {target.name} ({target.uuid})")
    print(f"  text {str(ns.text)[:400]}")
    if not ns.yes:
        print("  DRY RUN — nothing sent. Re-run with --yes to send it.")
        return 0
    status, resp = send_message(pull, token, target, str(ns.text))
    print(f"  SendGrokBotUserMessage -> {status}")
    print(f"  {resp[:200]}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    gbtypes.main(body)
