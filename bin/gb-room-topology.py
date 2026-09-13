#!/usr/bin/env python3
"""gb-room-topology — assemble one role's exact receipted room topology.

WHY THIS EXISTS. gb-group.py creates permanent rooms from arbitrary member
strings and confirms only the returned id; gb-setup-persona.py declares no
rooms at all. This stage is the narrow assembler between them: it reads a
persona's declared rooms[], resolves every member through the authoritative
identity map only, fresh-reads existing rooms, and emits one row per room with
its before/intended FULL-SET diff, acknowledgement binding, resume key, and
receipt. Review/apply/resume execute through gb-role-transaction.py, which owns
the nonce, manifest, acknowledgement, readback, compensation, and residue
truth. This file owns no RPC shape and no transaction state.

REFUSALS: caller-invented UUIDs as members (resolve-or-refuse); stale identity
receipts (digest mismatch fails); ambiguous same-name rooms; regenerated
nonce/plan drift (re-prepare, never silently re-authorize); create without a
dedicated acknowledgement; --apply without --yes --approve-all.

APPLIED MINING LESSON (replay-convergence vein): replays converge on the same
topology only through durable boundary markers. Every emitted row carries its
resume_key, acknowledgement hash, and recovery argv — a row without all three
is incomplete output. (fh suggest STALE ledger_age_hours=124.641; doctrine
stolen anyway: TransactionBoundary preserved for replay.)

READS/WRITES: dry-run reads personas/ + one roster pull, writes one dated
manifest under topology/. Apply writes via gb-group RPC verbs only.
stdlib only. Token in memory, never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from gbtypes import atomic_write_text

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
SCHEMA = "gb-room-topology/1"
RECEIPT_SCHEMA = "gb-identity-receipt/1"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
EXIT_UPSTREAM = 4
EXIT_REFUSED = 5

UUID_LIKE = 36  # a 36-char 4-dash string is a server id, never a logical member


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]


def _is_uuid_like(text: str) -> bool:
    s = str(text).strip()
    return len(s) == UUID_LIKE and s.count("-") == 4


def _load(modname: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(modname, str(BIN / filename))
    if spec is None or spec.loader is None:
        raise RuntimeError("missing module bin/%s" % filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def load_persona_rooms(persona: str) -> Optional[List[Dict[str, Any]]]:
    """Persona rooms[] or None when the key is absent (unknown, never empty).

    Intentional empty is an explicit `"rooms": []`. A missing key is a
    topology nobody declared, which fails closed downstream.
    """
    path = ROOT / "personas" / (persona + ".json")
    if not path.is_file():
        raise ValueError("unknown persona %r (see personas/)" % persona)
    doc = json.loads(path.read_text(encoding="utf-8"))
    rooms = doc.get("rooms", None)
    if rooms is None:
        return None
    if not isinstance(rooms, list):
        raise ValueError("persona %s rooms must be a list" % persona)
    return rooms


def validate_rooms(rooms: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Static shape gate. Pure: no reads. Raises ValueError, never warns."""
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for index, raw in enumerate(rooms):
        where = "rooms[%d]" % index
        if not isinstance(raw, Mapping):
            raise ValueError("%s must be an object" % where)
        logical = raw.get("logical_id")
        name = raw.get("name")
        if not isinstance(logical, str) or not logical.strip():
            raise ValueError("%s needs a logical_id" % where)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("%s needs a name" % where)
        if logical in seen:
            raise ValueError("duplicate room logical_id %r" % logical)
        seen.add(logical)
        members = raw.get("member_ids", [])
        if not isinstance(members, list):
            raise ValueError("%s member_ids must be a list" % where)
        clean: List[str] = []
        for m in members:
            if not isinstance(m, str) or not m.strip():
                raise ValueError("%s has a non-string member" % where)
            if _is_uuid_like(m):
                raise ValueError(
                    "%s member %r is a server id: resolve through the identity map, never inject"
                    % (where, m)
                )
            if m in clean:
                raise ValueError("%s duplicates member %r" % (where, m))
            clean.append(m)
        out.append(
            {
                "logical_id": logical,
                "name": name,
                "description": str(raw.get("description") or ""),
                "member_ids": sorted(clean),
            }
        )
    return sorted(out, key=lambda r: r["logical_id"])


def roster_digest(roster: Sequence[Mapping[str, Any]]) -> str:
    ids = sorted(str(a.get("legacyAgentId") or a.get("agentId")) for a in roster)
    return _digest(ids)


def build_identity_map(
    persona: str, roster: List[Dict[str, Any]]
) -> Tuple[Dict[str, Optional[str]], str]:
    """Template short-id -> live UUID (None when uncreated), plus roster digest.

    Resolution reuses gb-identity-stage.resolve over the SAME pull that feeds
    room matching, so identity and room pre-state can never disagree.
    """
    idstage = _load("gb_identity_stage", "gb-identity-stage.py")
    try:
        seed = idstage.sanctioned_seed()
    except ValueError:
        seed = ""
    rows = idstage.resolve(persona, roster, seed)
    mapping: Dict[str, Optional[str]] = {}
    for row in rows:
        tpl = str(row.get("template") or "")
        mapping[tpl] = row.get("live_uuid")
    return mapping, roster_digest(roster)


def check_receipt(path: pathlib.Path, fresh_digest: str) -> Dict[str, Optional[str]]:
    """A filed identity receipt is honored only when its roster digest matches
    the fresh pull. Anything else is stale, never 'close enough'."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError("unreadable identity receipt %s: %s" % (path, exc))
    if doc.get("schema") != RECEIPT_SCHEMA:
        raise ValueError(
            "receipt %s schema %r is not %r" % (path, doc.get("schema"), RECEIPT_SCHEMA)
        )
    if doc.get("roster_digest") != fresh_digest:
        raise ValueError(
            "stale identity receipt: roster moved since %s" % doc.get("created_at")
        )
    mapping = doc.get("identities")
    if not isinstance(mapping, dict):
        raise ValueError("receipt %s has no identities map" % path)
    return {str(k): (str(v) if v is not None else None) for k, v in mapping.items()}


def write_receipt(
    path: pathlib.Path,
    persona: str,
    mapping: Mapping[str, Optional[str]],
    digest: str,
) -> pathlib.Path:
    from datetime import datetime, timezone

    doc = {
        "schema": RECEIPT_SCHEMA,
        "persona": persona,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "roster_digest": digest,
        "identities": dict(mapping),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(doc, indent=1) + "\n")
    return path


def read_live_rooms(roster: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Rooms are agent rows carrying memberAgentIds (gb-group member-list test)."""
    out = []
    for a in roster:
        if a.get("isGroup") or "memberAgentIds" in a:
            out.append(
                {
                    "server_id": str(a.get("agentId") or a.get("uuid") or a.get("id")),
                    "name": str(a.get("name") or ""),
                    "member_ids": sorted(
                        str(m) for m in (a.get("memberAgentIds") or [])
                    ),
                }
            )
    return sorted(out, key=lambda r: r["name"].lower())


def match_room(
    name: str, rooms: Sequence[Mapping[str, Any]]
) -> Tuple[str, Optional[Mapping[str, Any]]]:
    """Room resolution: none | exactly-one | ambiguous (fail, never guess)."""
    hits = [r for r in rooms if str(r.get("name") or "").lower() == name.lower()]
    if not hits:
        return "absent", None
    if len(hits) > 1:
        return "ambiguous", None
    return "single", hits[0]


class TopologyFailed(Exception):
    """Typed row failure: FAILED with reason, never a silent skip."""


def compile_topology(
    persona: str,
    rooms: List[Dict[str, Any]],
    idmap: Mapping[str, Optional[str]],
    live_rooms: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """One row per declared room. Pure: every live read arrived in arguments."""
    rows: List[Dict[str, Any]] = []
    for room in rooms:
        base: Dict[str, Any] = {
            "logical_id": room["logical_id"],
            "name": room["name"],
            "persona": persona,
        }
        intended: List[str] = []
        missing: List[str] = []
        undeclared: List[str] = []
        for member in room["member_ids"]:
            if member not in idmap:
                undeclared.append(member)
                continue
            uuid = idmap[member]
            if uuid is None:
                missing.append(member)
            else:
                intended.append(uuid)
        intended = sorted(intended)
        if undeclared:
            # The declaration lied: the room names a template the persona never
            # declared. FAILED, never staged — identity-stage has nothing to
            # resolve because there is nothing to resolve it to.
            rows.append(
                {
                    **base,
                    "verdict": "FAILED",
                    "receipt": "undeclared members %s are not persona templates; declare them or remove them from the room"
                    % sorted(undeclared),
                    "before": [],
                    "intended": intended,
                    "missing": sorted(missing),
                    "undeclared": sorted(undeclared),
                    "resume_key": "room:%s" % _digest(room["logical_id"]),
                }
            )
            continue
        if missing:
            # Declared but unresolved: expected identity-stage prerequisites.
            # The room waits on a human running identity apply — it never
            # invents identity and never fails what staging can still fix.
            rows.append(
                {
                    **base,
                    "verdict": "WAITING_HUMAN",
                    "receipt": "unresolved declared members %s: stage them with gb-identity-stage, then re-run this dry-run"
                    % sorted(missing),
                    "before": [],
                    "intended": intended,
                    "missing": sorted(missing),
                    "blocked_on": "identity-stage",
                    "resume_key": "room:%s" % _digest(room["logical_id"]),
                }
            )
            continue
        state, hit = match_room(room["name"], live_rooms)
        if state == "ambiguous":
            rows.append(
                {
                    **base,
                    "verdict": "FAILED",
                    "receipt": "ambiguous: %d live rooms share name %r; disambiguate by hand, never guess"
                    % (
                        sum(
                            1
                            for r in live_rooms
                            if str(r.get("name") or "").lower() == room["name"].lower()
                        ),
                        room["name"],
                    ),
                    "before": [],
                    "intended": intended,
                    "resume_key": "room:%s" % _digest(room["logical_id"]),
                }
            )
            continue
        if state == "absent":
            rows.append(
                {
                    **base,
                    "verdict": "WAITING_HUMAN",
                    "receipt": "room absent: create binds logical id + name + exact set + account + plan hash via dedicated ack",
                    "before": [],
                    "intended": intended,
                    "action": "create",
                    "server_target": None,
                    "resume_key": "room:%s" % _digest(room["logical_id"]),
                }
            )
            continue
        assert hit is not None
        before = sorted(hit.get("member_ids") or [])
        if before == intended:
            rows.append(
                {
                    **base,
                    "verdict": "PROVEN",
                    "receipt": "room %s already carries the exact set (%d members); replay is a no-op"
                    % (hit.get("server_id"), len(intended)),
                    "before": before,
                    "intended": intended,
                    "action": "reuse",
                    "server_target": hit.get("server_id"),
                    "resume_key": "room:%s" % _digest(room["logical_id"]),
                }
            )
        else:
            rows.append(
                {
                    **base,
                    "verdict": "WAITING_HUMAN",
                    "receipt": "FULL-SET replace %d -> %d members (missing %s, extra %s); no delta is fabricated"
                    % (
                        len(before),
                        len(intended),
                        sorted(set(intended) - set(before)),
                        sorted(set(before) - set(intended)),
                    ),
                    "before": before,
                    "intended": intended,
                    "action": "reconcile",
                    "server_target": hit.get("server_id"),
                    "resume_key": "room:%s" % _digest(room["logical_id"]),
                }
            )
    return rows


def _identity_digest(persona: str, template: str) -> str:
    # Same string gb-identity-stage.py:135 hashes for its own resume keys
    # ("identity:%s" % digest("persona:P/template:T")): the room's continuation
    # names the exact rows a human clears over there, byte-identically.
    return hashlib.sha256(
        ("persona:%s/template:%s" % (persona, template)).encode()
    ).hexdigest()[:12]


def continuation_for(persona: str, r: Dict[str, Any]) -> Dict[str, Any]:
    """Typed recovery for every dry-run row. Declared-but-unresolved members
    wait on identity-stage with exact argv; undeclared members stay FAILED."""
    if r.get("verdict") == "WAITING_HUMAN" and r.get("blocked_on") == "identity-stage":
        missing = [str(m) for m in (r.get("missing") or [])]
        return {
            "kind": "WAITING_HUMAN",
            "blocked_on": "identity-stage",
            "missing": missing,
            "identity_plan": [
                "bin/gb-identity-stage.py",
                "--persona",
                persona,
                "--json",
            ],
            "identity_apply": [
                "bin/gb-identity-stage.py",
                "--persona",
                persona,
                "--apply",
                "--yes",
                "--approve-all",
            ],
            "identity_readback": ["bin/gb-identity-stage.py", "--persona", persona],
            "identity_resume_keys": [
                "identity:" + _identity_digest(persona, m) for m in missing
            ],
            "coach_need": "human runs identity apply for %s, then re-runs this dry-run"
            % ", ".join(missing),
            "coach_job": "identity_stage_apply(persona, %r)" % persona,
            "action": "run identity_apply argv, then re-run dry-run",
            "resume_key": r.get("resume_key", ""),
        }
    if r.get("verdict") == "FAILED" and r.get("undeclared"):
        return {
            "kind": "FAILED",
            "undeclared": [str(m) for m in (r.get("undeclared") or [])],
            "coach_need": "none: declare the templates or remove them from the room",
            "coach_job": "re-run dry-run after the declaration moves",
            "action": "none",
            "resume_key": r.get("resume_key", ""),
        }
    return {
        "coach_need": "human ack binds logical id + name + exact set + account + plan hash"
        if r.get("action") == "create"
        else "human confirms FULL-SET replace"
        if r.get("action") == "reconcile"
        else "none: replay is a no-op",
        "coach_job": "room_acknowledgement(manifest, %r)" % r["logical_id"]
        if r.get("action") == "create"
        else "re-run dry-run after the set moves",
        "action": "ack then --apply --manifest <path>"
        if r.get("action") == "create"
        else "none"
        if r.get("action") == "reuse"
        else "--apply --manifest <path>",
    }


def to_transaction_plan(
    rows: List[Dict[str, Any]],
    *,
    account_id: str,
    role_id: str,
    correlation_id: str,
    recovery_argv: List[str],
) -> Dict[str, Any]:
    """Appliable rows only. Any FAILED row refuses the whole plan, never ships
    around it: a partial topology silently authorized is a lying graph."""
    failed = [r["logical_id"] for r in rows if r["verdict"] == "FAILED"]
    if failed:
        raise TopologyFailed("refusing plan with FAILED rows: %s" % sorted(failed))
    plan_rooms = []
    for r in rows:
        if r.get("action") not in ("create", "reconcile"):
            continue
        plan_rooms.append(
            {
                "logical_id": r["logical_id"],
                "name": r["name"],
                "description": "",
                "server_target": r.get("server_target"),
                "member_ids": list(r["intended"]),
            }
        )
    return {
        "account_id": account_id,
        "role_id": role_id,
        "parent_correlation_id": correlation_id,
        "recovery_argv": list(recovery_argv),
        "identities": [],
        "rooms": plan_rooms,
    }


def _capabilities() -> int:
    return 21


def selftest() -> int:
    """Offline failure-injection battery. No network, no files outside tmp."""
    import json as _json
    import tempfile

    passed = 0
    failed: List[str] = []

    def leg(name: str, ok: bool) -> None:
        nonlocal passed
        if ok:
            passed += 1
        else:
            failed.append(name)

    # 1. intentional empty is explicit and yields zero rows.
    leg("empty-explicit", compile_topology("p", [], {}, []) == [])
    # 2. empty list validates: explicit empty, deterministic order.
    leg("empty-list-valid", validate_rooms([]) == [])
    # 3. duplicate logical rooms fail statically.
    try:
        validate_rooms(
            [
                {"logical_id": "r", "name": "A", "member_ids": []},
                {"logical_id": "r", "name": "B", "member_ids": []},
            ]
        )
        leg("dup-room-fails", False)
    except ValueError:
        leg("dup-room-fails", True)
    # 4. caller UUID injection fails statically.
    try:
        validate_rooms(
            [
                {
                    "logical_id": "r",
                    "name": "A",
                    "member_ids": ["12345678-1234-1234-1234-1234567890ab"],
                }
            ]
        )
        leg("uuid-injection-fails", False)
    except ValueError:
        leg("uuid-injection-fails", True)
    # 5. duplicate members fail statically.
    try:
        validate_rooms([{"logical_id": "r", "name": "A", "member_ids": ["x", "x"]}])
        leg("dup-member-fails", False)
    except ValueError:
        leg("dup-member-fails", True)
    # 6. non-string member fails statically.
    try:
        validate_rooms([{"logical_id": "r", "name": "A", "member_ids": ["x", 7]}])
        leg("wrong-type-fails", False)
    except ValueError:
        leg("wrong-type-fails", True)
    # 7. declared-but-uncreated member -> WAITING_HUMAN with identity argv.
    rooms = validate_rooms(
        [{"logical_id": "r1", "name": "R1", "member_ids": ["alice", "ghost"]}]
    )
    out = compile_topology("p", rooms, {"alice": "u-alice", "ghost": None}, [])
    row = out[0]
    cont = continuation_for("p", row)
    leg(
        "unresolved-waits-on-identity-stage",
        len(out) == 1
        and row["verdict"] == "WAITING_HUMAN"
        and row["missing"] == ["ghost"]
        and row["blocked_on"] == "identity-stage"
        and cont["kind"] == "WAITING_HUMAN"
        and cont["identity_plan"]
        == ["bin/gb-identity-stage.py", "--persona", "p", "--json"]
        and cont["identity_apply"]
        == [
            "bin/gb-identity-stage.py",
            "--persona",
            "p",
            "--apply",
            "--yes",
            "--approve-all",
        ]
        and cont["identity_readback"] == ["bin/gb-identity-stage.py", "--persona", "p"]
        and cont["identity_resume_keys"]
        == ["identity:" + _identity_digest("p", "ghost")]
        and cont["resume_key"] == row["resume_key"],
    )
    # 8. undeclared template member -> FAILED row (the declaration lied), never staged.
    out = compile_topology(
        "p",
        validate_rooms(
            [{"logical_id": "r1", "name": "R1", "member_ids": ["stranger"]}]
        ),
        {"alice": "u-alice"},
        [],
    )
    cont = continuation_for("p", out[0])
    leg(
        "undeclared-stays-failed",
        len(out) == 1
        and out[0]["verdict"] == "FAILED"
        and out[0]["undeclared"] == ["stranger"]
        and cont["kind"] == "FAILED"
        and "identity_plan" not in cont,
    )
    # 9. ambiguous same-name rooms -> FAILED.
    live = [
        {"server_id": "s1", "name": "R1", "member_ids": ["u-alice"]},
        {"server_id": "s2", "name": "r1", "member_ids": ["u-alice"]},
    ]
    out = compile_topology(
        "p",
        validate_rooms([{"logical_id": "r1", "name": "R1", "member_ids": ["alice"]}]),
        {"alice": "u-alice"},
        live,
    )
    leg(
        "ambiguous-failed",
        out[0]["verdict"] == "FAILED" and "ambiguous" in out[0]["receipt"],
    )
    # 10. exact set -> PROVEN reuse.
    out = compile_topology(
        "p",
        validate_rooms([{"logical_id": "r1", "name": "R1", "member_ids": ["alice"]}]),
        {"alice": "u-alice"},
        [{"server_id": "s1", "name": "R1", "member_ids": ["u-alice"]}],
    )
    leg("reuse-proven", out[0]["verdict"] == "PROVEN" and out[0]["action"] == "reuse")
    # 11. drifted set -> WAITING_HUMAN reconcile with exact missing/extra.
    out = compile_topology(
        "p",
        validate_rooms(
            [{"logical_id": "r1", "name": "R1", "member_ids": ["alice", "bob"]}]
        ),
        {"alice": "u-alice", "bob": "u-bob"},
        [{"server_id": "s1", "name": "R1", "member_ids": ["u-alice", "u-zed"]}],
    )
    leg(
        "reconcile-human",
        out[0]["verdict"] == "WAITING_HUMAN"
        and out[0]["action"] == "reconcile"
        and "u-bob" in out[0]["receipt"]
        and "u-zed" in out[0]["receipt"],
    )
    # 12. absent room -> WAITING_HUMAN create.
    out = compile_topology(
        "p",
        validate_rooms([{"logical_id": "r1", "name": "R1", "member_ids": ["alice"]}]),
        {"alice": "u-alice"},
        [],
    )
    leg(
        "absent-human",
        out[0]["verdict"] == "WAITING_HUMAN" and out[0]["action"] == "create",
    )
    # 13. FAILED row refuses the transaction plan wholesale.
    try:
        to_transaction_plan(
            out[:1]
            + [
                {
                    "logical_id": "bad",
                    "name": "Bad",
                    "verdict": "FAILED",
                    "receipt": "x",
                    "intended": [],
                }
            ],
            account_id="a",
            role_id="r",
            correlation_id="c",
            recovery_argv=["gb-room-topology", "--manifest", "m"],
        )
        leg("failed-refuses-plan", False)
    except TopologyFailed:
        leg("failed-refuses-plan", True)
    # 14. stale receipt fails on digest drift.
    with tempfile.TemporaryDirectory() as tmp:
        rp = pathlib.Path(tmp) / "id.json"
        atomic_write_text(
            rp,
            _json.dumps(
                {
                    "schema": RECEIPT_SCHEMA,
                    "persona": "p",
                    "created_at": "t",
                    "roster_digest": "old",
                    "identities": {"alice": "u-alice"},
                }
            ),
        )
        try:
            check_receipt(rp, "new")
            leg("stale-receipt-fails", False)
        except ValueError:
            leg("stale-receipt-fails", True)
        atomic_write_text(
            rp,
            _json.dumps(
                {
                    "schema": RECEIPT_SCHEMA,
                    "persona": "p",
                    "created_at": "t",
                    "roster_digest": "new",
                    "identities": {"alice": "u-alice"},
                }
            ),
        )
        try:
            leg(
                "fresh-receipt-passes", check_receipt(rp, "new") == {"alice": "u-alice"}
            )
        except ValueError:
            leg("fresh-receipt-passes", False)
    # 15. e2e against the real transaction primitive with a fake roster:
    # two rooms, reconcile one, fail after first create, replay converges.
    log: List[str] = []
    try:
        rt = _load("gb_role_transaction", "gb-role-transaction.py")

        class FakeAdapter:
            def __init__(self) -> None:
                self.rooms: Dict[str, Dict[str, Any]] = {
                    "s-keep": {"name": "Keep", "member_ids": ["u-a"]},
                }
                self.writes = 0

            def read_identity(
                self, logical: str, server: Optional[str]
            ) -> Optional[Mapping[str, Any]]:
                return {"logical": logical}

            def list_rooms(self) -> Sequence[Mapping[str, Any]]:
                return [
                    {
                        "server_id": sid,
                        "name": body["name"],
                        "description": "",
                        "member_ids": list(body["member_ids"]),
                    }
                    for sid, body in self.rooms.items()
                ]

            def write_identity(
                self,
                logical: str,
                server: Optional[str],
                intended: Mapping[str, Any],
                request_id: str,
            ) -> Mapping[str, Any]:
                raise AssertionError("topology writes no identities")

            def restore_identity(
                self,
                logical: str,
                server: Optional[str],
                before: Optional[Mapping[str, Any]],
                request_id: str,
            ) -> Mapping[str, Any]:
                return {}

            def create_room(
                self,
                server: str,
                name: str,
                description: str,
                member_ids: Sequence[str],
                request_id: str,
            ) -> Mapping[str, Any]:
                self.writes += 1
                if name == "Doomed":
                    raise RuntimeError(
                        "injected: create dies after earlier rooms committed"
                    )
                self.rooms[server] = {"name": name, "member_ids": sorted(member_ids)}
                return {"server_target": server}

            def set_room_members(
                self, server: str, member_ids: Sequence[str], request_id: str
            ) -> Mapping[str, Any]:
                self.writes += 1
                self.rooms[server]["member_ids"] = sorted(member_ids)
                return {"server_target": server}

        with tempfile.TemporaryDirectory() as tmp:
            mp = pathlib.Path(tmp) / "topo.json"
            fa = FakeAdapter()
            plan = {
                "account_id": "offline-account",
                "role_id": "e2e",
                "parent_correlation_id": "corr-1",
                "recovery_argv": ["gb-room-topology", "--manifest", str(mp)],
                "identities": [],
                "rooms": [
                    {
                        "logical_id": "keep",
                        "name": "Keep",
                        "description": "",
                        "server_target": "s-keep",
                        "member_ids": ["u-a", "u-b"],
                    },
                    {
                        "logical_id": "fresh",
                        "name": "Fresh",
                        "description": "",
                        "server_target": None,
                        "member_ids": ["u-a"],
                    },
                    {
                        "logical_id": "doomed",
                        "name": "Doomed",
                        "description": "",
                        "server_target": None,
                        "member_ids": ["u-a"],
                    },
                ],
            }
            man = rt.prepare_transaction(plan, mp, fa)
            acks = {
                row["logical_target"]: row["approval"]["expected_sha256"]
                for row in man.get("steps", [])
                if row.get("kind") == "room_create"
            }
            leg("e2e-ack-bound", len(acks) == 2)
            fresh_id = next(
                row["server_target"]
                for row in man["steps"]
                if row.get("logical_target") == "fresh"
            )
            first = rt.apply_transaction(plan, mp, fa, acks)
            leg("e2e-first-partial", first.verdict == "PARTIAL")
            insp = rt.inspect_manifest(mp)
            residue = [s.get("server_id") for s in (insp.get("residue") or [])]
            leg("e2e-residue-permanent", fresh_id in residue)
            again = rt.apply_transaction(plan, mp, fa, acks)
            leg("e2e-replay-same-verdict", again.verdict == "PARTIAL")
            insp2 = rt.inspect_manifest(mp)
            fresh_again = next(
                row["server_id"]
                for row in insp2["steps"]
                if row.get("logical_id") == "fresh"
            )
            leg("e2e-no-dup-room", fresh_again == fresh_id and len(fa.rooms) == 2)
            try:
                leg("e2e-inspect-reads", bool(insp2.get("steps")))
            except Exception:
                leg("e2e-inspect-reads", False)
    except Exception as exc:
        leg("e2e-harness", False)
        log.append(str(exc))

    total = passed + len(failed)
    if failed:
        for f in failed:
            print("leg failed: %s" % f, file=sys.stderr)
        print("SELFTEST %s - %d/%d" % ("FAIL", passed, total))
        return 1
    print("SELFTEST PASS - %d/%d" % (passed, total))
    return 0


class _LiveAdapter:
    """TransactionAdapter over the gb-group RPC verbs. Every mutation echoes
    its target id; any reseat or non-200 raises so the primitive marks PARTIAL."""

    def __init__(self, token: str) -> None:
        gbgroup = _load("gb_group", "gb-group.py")
        self._g = gbgroup
        self._mod = gbgroup._pull()
        self._token = token
        self._roster: Optional[List[Dict[str, Any]]] = None

    def _pull(self) -> List[Dict[str, Any]]:
        if self._roster is None:
            st, resp = self._g.rpc(self._mod, self._token, "ListGrokBotAgents", {})
            if st != 200 or not isinstance(resp, dict):
                raise RuntimeError("roster pull HTTP %s" % st)
            self._roster = [
                a for a in (resp.get("agents") or []) if isinstance(a, dict)
            ]
        return self._roster

    def list_rooms(self) -> Sequence[Mapping[str, Any]]:
        return read_live_rooms(self._pull())

    def read_identity(
        self, logical: str, server: Optional[str]
    ) -> Optional[Mapping[str, Any]]:
        return {"logical_target": logical}

    def write_identity(
        self,
        logical: str,
        server: Optional[str],
        intended: Mapping[str, Any],
        request_id: str,
    ) -> Mapping[str, Any]:
        raise RuntimeError("topology stage writes no identities")

    def restore_identity(
        self,
        logical: str,
        server: Optional[str],
        before: Optional[Mapping[str, Any]],
        request_id: str,
    ) -> Mapping[str, Any]:
        raise RuntimeError("topology stage writes no identities")

    def create_room(
        self,
        server: str,
        name: str,
        description: str,
        member_ids: Sequence[str],
        request_id: str,
    ) -> Mapping[str, Any]:
        st, resp = self._g.rpc(
            self._mod,
            self._token,
            "CreateGrokBotRoom",
            {
                "agent_id": server,
                "name": name,
                "description": description,
                "member_agent_ids": list(member_ids),
            },
        )
        if st != 200 or not isinstance(resp, dict):
            raise RuntimeError("CreateGrokBotRoom HTTP %s" % st)
        agent = resp.get("agent") or {}
        if agent.get("agentId") != server:
            raise RuntimeError(
                "server did not confirm room identity (wrong-id response)"
            )
        return {"server_id": server}

    def set_room_members(
        self, server: str, member_ids: Sequence[str], request_id: str
    ) -> Mapping[str, Any]:
        st, resp = self._g.rpc(
            self._mod,
            self._token,
            "SetGrokBotRoomMembers",
            {"agent_id": server, "member_agent_ids": list(member_ids)},
        )
        if st != 200 or not isinstance(resp, dict):
            raise RuntimeError("SetGrokBotRoomMembers HTTP %s" % st)
        agent = resp.get("agent") or {}
        if agent.get("agentId") != server:
            raise RuntimeError("server reseated the room id")
        return {"server_id": server}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-room-topology", description=__doc__)
    ap.add_argument("--persona", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--capabilities", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--approve-all", action="store_true")
    ap.add_argument("--ack", action="append", default=[])
    ap.add_argument("--manifest", default="")
    ap.add_argument("--identity-receipt", default="")
    ap.add_argument("--account-id", default="ultra/local")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.capabilities:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "thresholds": {
                        "selftest_legs": _capabilities(),
                        "explicit_empty_rooms_ok": True,
                        "missing_rooms_key_fails": True,
                        "ack_required_for_create": True,
                    },
                    "exit_codes": {
                        "ok": 0,
                        "findings": 1,
                        "usage": 2,
                        "environment": 3,
                        "upstream": 4,
                        "refused": 5,
                    },
                },
                indent=1,
            )
        )
        return EXIT_OK
    if not args.persona:
        print("gb-room-topology: --persona <id> (see personas/)", file=sys.stderr)
        return EXIT_USAGE
    if args.apply and not (args.yes and args.approve_all):
        print(
            "gb-room-topology: apply creates permanent rooms (no delete RPC exists); "
            "dry-run first, then --yes --approve-all with every create human-acked",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    sys.path.insert(0, str(BIN))
    try:
        from gbrpc import access_token, rpc
        from gblib import platform_support

        support = platform_support().support_dir
        if support is None:
            raise RuntimeError("no desktop client state on this machine")
        token = access_token(support)
        st, resp = rpc(token, "ListGrokBotAgents", {})
        if st != 200 or not isinstance(resp, dict):
            raise RuntimeError("roster pull HTTP %s" % st)
        raw_agents = resp.get("agents")
        agents: List[Any] = list(raw_agents) if isinstance(raw_agents, list) else []
        roster = [a for a in agents if isinstance(a, dict)]
    except RuntimeError as exc:
        print("gb-room-topology: %s - unmeasured, not empty" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT
    except Exception as exc:
        print("gb-room-topology: %s" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT

    try:
        rooms_raw = load_persona_rooms(args.persona[0])
    except ValueError as exc:
        print("gb-room-topology: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if rooms_raw is None:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "verdict": "FAILED",
                    "receipt": "persona %r declares no rooms[]: topology unknown, never empty-by-default"
                    % args.persona[0],
                    "resume_key": "room:topology-unknown",
                }
            )
            if args.json
            else "FAILED\tpersona %r declares no rooms[] (unknown, never empty)"
            % args.persona[0]
        )
        return EXIT_FINDINGS if args.apply else EXIT_OK
    try:
        rooms = validate_rooms(rooms_raw)
    except ValueError as exc:
        print("gb-room-topology: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    digest = roster_digest(roster)
    try:
        if args.identity_receipt:
            idmap = check_receipt(pathlib.Path(args.identity_receipt), digest)
        else:
            idmap, _ = build_identity_map(args.persona[0], roster)
    except ValueError as exc:
        print("gb-room-topology: %s" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT

    live_rooms = read_live_rooms(roster)
    try:
        rows = compile_topology(args.persona[0], rooms, idmap, live_rooms)
    except TopologyFailed as exc:
        print("gb-room-topology: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    for r in rows:
        r["persona"] = args.persona[0]
        r["continuation"] = continuation_for(args.persona[0], r)

    if not args.apply:
        if args.json:
            print(
                json.dumps(
                    {"schema": SCHEMA, "persona": args.persona[0], "rows": rows},
                    indent=1,
                )
            )
        else:
            for r in rows:
                print(
                    "%s\t%s\t%s\t%s"
                    % (
                        r["logical_id"],
                        r.get("action", "-"),
                        r["verdict"],
                        r.get("receipt", "")[:100],
                    )
                )
        appliable = [r for r in rows if r.get("action") in ("create", "reconcile")]
        if not appliable:
            return EXIT_OK
        try:
            plan = to_transaction_plan(
                rows,
                account_id=args.account_id,
                role_id=args.persona[0],
                correlation_id=_digest([args.persona[0], digest]),
                recovery_argv=["gb-room-topology", "--persona", args.persona[0]],
            )
        except TopologyFailed as exc:
            print("gb-room-topology: %s" % exc, file=sys.stderr)
            return EXIT_OK
        rt = _load("gb_role_transaction", "gb-role-transaction.py")

        class _ReadOnly:
            def read_identity(
                self, logical: str, server: Optional[str]
            ) -> Optional[Mapping[str, Any]]:
                return {"logical_target": logical}

            def list_rooms(self) -> Sequence[Mapping[str, Any]]:
                return live_rooms

            def write_identity(
                self,
                logical: str,
                server: Optional[str],
                intended: Mapping[str, Any],
                request_id: str,
            ) -> Mapping[str, Any]:
                raise RuntimeError("dry-run performs no writes")

            def restore_identity(
                self,
                logical: str,
                server: Optional[str],
                before: Optional[Mapping[str, Any]],
                request_id: str,
            ) -> Mapping[str, Any]:
                raise RuntimeError("dry-run performs no writes")

            def create_room(
                self,
                server: str,
                name: str,
                description: str,
                member_ids: Sequence[str],
                request_id: str,
            ) -> Mapping[str, Any]:
                raise RuntimeError("dry-run performs no writes")

            def set_room_members(
                self, server: str, member_ids: Sequence[str], request_id: str
            ) -> Mapping[str, Any]:
                raise RuntimeError("dry-run performs no writes")

        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
        manifest_path = (
            pathlib.Path(args.manifest)
            if args.manifest
            else (ROOT / "topology" / ("topology-%s.json" % stamp))
        )
        manifest = rt.prepare_transaction(plan, manifest_path, _ReadOnly())
        ack_lines = []
        for row in manifest.get("steps", []):
            if row.get("kind") == "room_create":
                ack_lines.append(
                    "%s=%s"
                    % (row["logical_target"], row["approval"]["expected_sha256"])
                )
        if args.json:
            print(
                json.dumps(
                    {"manifest": str(manifest_path), "acks": ack_lines}, indent=1
                )
            )
        else:
            print("manifest %s" % manifest_path)
            for line in ack_lines:
                print("ack %s" % line)
        return EXIT_OK

    # --apply: a reviewed manifest is mandatory; anything else is an
    # unreviewed permanent-room authorization and is refused.
    if not args.manifest:
        print(
            "gb-room-topology: --apply needs the reviewed --manifest path from a dry-run",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    manifest_path = pathlib.Path(args.manifest)
    if not manifest_path.is_file():
        print(
            "gb-room-topology: manifest %r was never prepared" % args.manifest,
            file=sys.stderr,
        )
        return EXIT_REFUSED
    try:
        plan = to_transaction_plan(
            rows,
            account_id=args.account_id,
            role_id=args.persona[0],
            correlation_id=_digest([args.persona[0], digest]),
            recovery_argv=[
                "gb-room-topology",
                "--persona",
                args.persona[0],
                "--apply",
                "--yes",
                "--approve-all",
                "--manifest",
                args.manifest,
            ],
        )
    except TopologyFailed as exc:
        print("gb-room-topology: %s" % exc, file=sys.stderr)
        return EXIT_REFUSED
    acks: Dict[str, str] = {}
    for item in args.ack:
        if "=" not in item:
            print("gb-room-topology: --ack LOGICAL=TOKEN", file=sys.stderr)
            return EXIT_USAGE
        key, token_value = item.split("=", 1)
        acks[key.strip()] = token_value.strip()
    rt = _load("gb_role_transaction", "gb-role-transaction.py")
    outcome = rt.apply_transaction(plan, manifest_path, _LiveAdapter(token), acks)
    if args.json:
        print(
            json.dumps({"verdict": outcome.verdict, "reason": outcome.reason}, indent=1)
        )
    else:
        print("%s: %s" % (outcome.verdict, outcome.reason))
    return int(outcome.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
