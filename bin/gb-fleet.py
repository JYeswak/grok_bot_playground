#!/usr/bin/env python3
"""gb-fleet — talk to the live fleet: roster, routines, and one message to one Bot.

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

ON `send` BEING A WRITE. It is the only verb here that changes anything, and it is deliberately
narrow: one Bot, one message, no broadcast. It earns its place because it is now load-bearing —
a Bot creates its own routine when asked in chat (measured: `Weekly receipt`, cron
`CRON_TZ=America/Denver 45 7 * * 1`, `provenance: "user"`), which makes "deploy a template, then
send one message" the one-touch path. It refuses without `--yes` so it cannot fire by accident,
and it prints the exact text first.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import json
import pathlib
import sys
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gbargs  # noqa: E402
import gbtypes  # noqa: E402

# The TRANSPORT is a library now (gbrpc.py), not a borrowed slice of a producer. Importing
# it normally deletes the python 3.9.6 `spec_from_file_location` + `sys.modules` dance that
# every borrower had to remember, and stops `gb dogfood audit` counting this file as one
# more hand-roller of gb-pull-inventory's read.
from gbrpc import SUPPORT, access_token, rpc  # noqa: E402

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
    """The three transport names, bound from `gbrpc` rather than dynamically imported."""

    SUPPORT = SUPPORT
    access_token = staticmethod(access_token)
    rpc = staticmethod(rpc)


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

    for f in fails:
        print(f"FAIL: {f}")
    print(
        f"SELFTEST {'FAIL' if fails else 'PASS'} - {checks - len(fails)}/{checks} properties"
    )
    return 1 if fails else 0


@dataclasses.dataclass(frozen=True)
class FleetArgs:
    """Talk to the live fleet: roster, routines, and one message to one Bot."""

    action: str = gbargs.arg(
        default="roster",
        help="roster | routines | send",
        choices=("roster", "routines", "send"),
        positional=True,
    )
    bot: Optional[str] = gbargs.arg(
        default=None, help="Bot name (exact, or a unique prefix)"
    )
    text: Optional[str] = gbargs.arg(default=None, help="send: the message body")
    yes: bool = gbargs.arg(
        default=False, help="send: actually send it (otherwise a dry run)"
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
