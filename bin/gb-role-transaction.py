#!/usr/bin/env python3
"""Crash-safe identity and room transaction primitive for role rollout callers.

This module deliberately contains no credential loader or RPC transport.  A caller supplies a
``TransactionAdapter`` backed by the already-established Grok Bot clients.  The primitive owns the
parts that must not vary between callers: authoritative pre-state capture, a durable manifest,
compare-and-swap journal transitions, stable room create identities, acknowledgements scoped to
one exact room, fresh readback, reverse-order compensation, and permanent-room residue truth.

The standalone surface is offline only.  ``--selftest`` runs isolated failure fixtures;
``--inspect`` validates and summarizes a manifest without contacting an account.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import sys
import tempfile
import time
import uuid
from contextlib import AbstractContextManager
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - the deployment of record is POSIX.
    fcntl = None  # type: ignore[assignment]

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
from gbtypes import atomic_write_json, durable_transition  # noqa: E402

SCHEMA = "gb-role-transaction/1"
ACK_SCHEMA = "gb-role-room-ack/1"
TOOL = "bin/gb-role-transaction.py"
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_REFUSED = 5

ROW_STATES = frozenset(
    {"PLANNED", "RUNNING", "WAITING_HUMAN", "CONFIRMED", "COMPENSATED", "PARTIAL"}
)
TERMINAL_ROW_STATES = frozenset({"CONFIRMED", "COMPENSATED"})

JsonObject = Dict[str, Any]
Clock = Callable[[], dt.datetime]
IdFactory = Callable[[], str]
LogSink = Callable[[Mapping[str, Any]], None]
Injector = Callable[[str, Mapping[str, Any], pathlib.Path], None]
Writer = Callable[[pathlib.Path, Any], None]


class TransactionError(RuntimeError):
    """Base class for a fail-closed transaction outcome."""


class TransactionRefused(TransactionError):
    """The reviewed plan or authoritative state no longer permits execution."""


class TransactionConflict(TransactionError):
    """Another attempt owns or changed this manifest."""


class ManifestError(TransactionError):
    """The manifest is unreadable, malformed, or has lost integrity."""


class InjectedFailure(TransactionError):
    """A selftest-only interruption at one named failure point."""


class TransactionAdapter(Protocol):
    """Account operations supplied by a caller's existing RPC client.

    Every read must be authoritative.  Mutation return values are receipts, not proof; this module
    always calls a read method afterward.  Implementations should raise on a non-success RPC
    response.  ``restore_identity(..., before=None)`` removes an identity created by this attempt.
    """

    def read_identity(
        self, logical_target: str, server_target: Optional[str]
    ) -> Optional[Mapping[str, Any]]: ...

    def list_rooms(self) -> Sequence[Mapping[str, Any]]: ...

    def write_identity(
        self,
        logical_target: str,
        server_target: Optional[str],
        intended: Mapping[str, Any],
        request_id: str,
    ) -> Mapping[str, Any]: ...

    def restore_identity(
        self,
        logical_target: str,
        server_target: Optional[str],
        before: Optional[Mapping[str, Any]],
        request_id: str,
    ) -> Mapping[str, Any]: ...

    def create_room(
        self,
        server_target: str,
        name: str,
        description: str,
        member_ids: Sequence[str],
        request_id: str,
    ) -> Mapping[str, Any]: ...

    def set_room_members(
        self, server_target: str, member_ids: Sequence[str], request_id: str
    ) -> Mapping[str, Any]: ...


@dataclasses.dataclass(frozen=True)
class TransactionOutcome:
    verdict: str
    exit_code: int
    reason: str
    manifest: Mapping[str, Any]


class FailureInjector:
    """Raise exactly once at one exact point; used by public offline failure fixtures."""

    def __init__(self, point: str) -> None:
        self.point = point
        self.fired = False

    def __call__(
        self, point: str, _manifest: Mapping[str, Any], _path: pathlib.Path
    ) -> None:
        if point == self.point and not self.fired:
            self.fired = True
            raise InjectedFailure(point)

    def assert_fired(self) -> None:
        if not self.fired:
            raise AssertionError(f"failure point did not fire: {self.point}")


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _noop_inject(
    _point: str, _manifest: Mapping[str, Any], _path: pathlib.Path
) -> None:
    return None


def _stdout_log(row: Mapping[str, Any]) -> None:
    print(json.dumps(row, sort_keys=True, default=str))


def _iso(clock: Clock) -> str:
    return clock().astimezone(dt.timezone.utc).isoformat(timespec="milliseconds")


def _json_value(value: Any) -> Any:
    """Detach adapter-owned objects and reject values a durable manifest cannot reproduce."""
    try:
        return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError) as exc:
        raise TransactionRefused(f"state is not JSON-serializable: {exc}") from exc


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(raw).hexdigest()


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("journal_hash", None)
    return _digest(payload)


def _member_ids(value: Any, field: str) -> List[str]:
    if not isinstance(value, list):
        raise TransactionRefused(f"{field} must be a list")
    members: List[str] = []
    seen: set[str] = set()
    for index, member in enumerate(value):
        if not isinstance(member, str) or not member.strip():
            raise TransactionRefused(f"{field}[{index}] must be a non-empty string")
        normalized = member.strip()
        if normalized in seen:
            raise TransactionRefused(f"{field} repeats member {normalized!r}")
        seen.add(normalized)
        members.append(normalized)
    return sorted(members)


def _room_id(room: Mapping[str, Any]) -> Optional[str]:
    for key in ("server_id", "room_id", "agent_id", "agentId", "uuid", "id"):
        value = room.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _room_members(room: Mapping[str, Any]) -> List[str]:
    for key in ("member_ids", "memberAgentIds", "member_agent_ids"):
        if key in room:
            value = room[key]
            if not isinstance(value, list):
                raise TransactionRefused(
                    f"authoritative room {_room_id(room)!r} has malformed {key}"
                )
            return sorted(str(member) for member in value)
    raise TransactionRefused(f"authoritative room {_room_id(room)!r} has no member set")


def _normalize_room(room: Mapping[str, Any]) -> JsonObject:
    server_id = _room_id(room)
    name = room.get("name")
    if not server_id or not isinstance(name, str) or not name.strip():
        raise TransactionRefused("authoritative room lacks an exact server id or name")
    return {
        "server_id": server_id,
        "name": name,
        "description": str(room.get("description") or ""),
        "member_ids": _room_members(room),
    }


def _room_diff(
    before: Optional[Mapping[str, Any]], intended: Mapping[str, Any]
) -> JsonObject:
    old = set(before.get("member_ids", []) if before else [])
    new = set(intended.get("member_ids", []))
    return {"added": sorted(new - old), "removed": sorted(old - new)}


def _mapping_delta(
    before: Optional[Mapping[str, Any]], intended: Mapping[str, Any]
) -> JsonObject:
    prior = before or {}
    changed = sorted(key for key, value in intended.items() if prior.get(key) != value)
    removed = sorted(key for key in prior if key not in intended)
    return {"changed_fields": changed, "before_only_fields": removed}


def _matches_intended(
    current: Optional[Mapping[str, Any]], intended: Mapping[str, Any]
) -> bool:
    if current is None:
        return False
    return all(current.get(key) == value for key, value in intended.items())


def _require_text(plan: Mapping[str, Any], key: str) -> str:
    value = plan.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TransactionRefused(f"plan.{key} must be a non-empty string")
    return value.strip()


def _validate_plan(plan: Mapping[str, Any]) -> JsonObject:
    if not isinstance(plan, Mapping):
        raise TransactionRefused("plan must be an object")
    normalized: JsonObject = {
        "account_id": _require_text(plan, "account_id"),
        "role_id": _require_text(plan, "role_id"),
        "parent_correlation_id": _require_text(plan, "parent_correlation_id"),
    }
    recovery = plan.get("recovery_argv")
    if (
        not isinstance(recovery, list)
        or not recovery
        or not all(isinstance(part, str) and part for part in recovery)
    ):
        raise TransactionRefused("plan.recovery_argv must be a non-empty argv list")
    normalized["recovery_argv"] = list(recovery)

    identities = plan.get("identities", [])
    rooms = plan.get("rooms", [])
    if not isinstance(identities, list) or not isinstance(rooms, list):
        raise TransactionRefused("plan.identities and plan.rooms must be lists")

    normalized_identities: List[JsonObject] = []
    seen_identity: set[str] = set()
    for index, raw in enumerate(identities):
        if not isinstance(raw, Mapping):
            raise TransactionRefused(f"plan.identities[{index}] must be an object")
        logical = _require_text(raw, "logical_id")
        if logical in seen_identity:
            raise TransactionRefused(f"duplicate identity logical_id {logical!r}")
        seen_identity.add(logical)
        intended = raw.get("intended")
        if not isinstance(intended, Mapping) or not intended:
            raise TransactionRefused(
                f"identity {logical!r} needs a non-empty intended object"
            )
        target = raw.get("server_target")
        if target is not None and (not isinstance(target, str) or not target.strip()):
            raise TransactionRefused(
                f"identity {logical!r} has an invalid server_target"
            )
        normalized_identities.append(
            {
                "logical_id": logical,
                "server_target": target.strip() if isinstance(target, str) else None,
                "intended": _json_value(dict(intended)),
            }
        )

    normalized_rooms: List[JsonObject] = []
    seen_room: set[str] = set()
    for index, raw in enumerate(rooms):
        if not isinstance(raw, Mapping):
            raise TransactionRefused(f"plan.rooms[{index}] must be an object")
        logical = _require_text(raw, "logical_id")
        name = _require_text(raw, "name")
        if logical in seen_room:
            raise TransactionRefused(f"duplicate room logical_id {logical!r}")
        seen_room.add(logical)
        target = raw.get("server_target")
        if target is not None and (not isinstance(target, str) or not target.strip()):
            raise TransactionRefused(f"room {logical!r} has an invalid server_target")
        normalized_rooms.append(
            {
                "logical_id": logical,
                "name": name,
                "description": str(raw.get("description") or ""),
                "server_target": target.strip() if isinstance(target, str) else None,
                "member_ids": _member_ids(
                    raw.get("member_ids", []), f"rooms[{index}].member_ids"
                ),
            }
        )
    normalized["identities"] = normalized_identities
    normalized["rooms"] = normalized_rooms
    return normalized


def room_acknowledgement(manifest: Mapping[str, Any], logical_id: str) -> str:
    """Return the exact acknowledgement token for one reviewed irreversible room row."""
    matches = [
        row
        for row in manifest.get("steps", [])
        if row.get("kind") == "room_create" and row.get("logical_target") == logical_id
    ]
    if len(matches) != 1:
        raise TransactionRefused(f"manifest has no unique new room {logical_id!r}")
    token = matches[0].get("approval", {}).get("expected_sha256")
    if not isinstance(token, str) or not token:
        raise ManifestError(f"room {logical_id!r} has no acknowledgement binding")
    return token


class _ManifestLease(AbstractContextManager["_ManifestLease"]):
    """Non-blocking process lease; persistent inode, kernel-owned lifetime."""

    def __init__(self, path: pathlib.Path, attempt_id: str) -> None:
        self.path = path.with_name(path.name + ".lock")
        self.attempt_id = attempt_id
        self.fd: Optional[int] = None

    def __enter__(self) -> "_ManifestLease":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        if fcntl is None:
            os.close(self.fd)
            self.fd = None
            raise TransactionConflict(
                "manifest leases require POSIX flock on this deployment"
            )
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.fd)
            self.fd = None
            raise TransactionConflict(
                f"manifest is owned by another attempt: {self.path}"
            ) from exc
        body = (self.attempt_id + "\n").encode("utf-8")
        os.ftruncate(self.fd, 0)
        os.write(self.fd, body)
        os.fsync(self.fd)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None
        return None


class RoleTransaction:
    """Durable transaction over identity shape and zero or more room stages."""

    def __init__(
        self,
        plan: Mapping[str, Any],
        manifest_path: pathlib.Path,
        adapter: TransactionAdapter,
        *,
        attempt_id: Optional[str] = None,
        id_factory: IdFactory = _new_id,
        clock: Clock = _utcnow,
        logger: LogSink = _stdout_log,
        inject: Injector = _noop_inject,
        writer: Writer = atomic_write_json,
    ) -> None:
        self.plan = _validate_plan(plan)
        self.manifest_path = pathlib.Path(manifest_path)
        self.adapter = adapter
        self.id_factory = id_factory
        self.clock = clock
        self.logger = logger
        self.inject = inject
        self.writer = writer
        self.attempt_id = attempt_id or id_factory()
        self.plan_hash = _digest(self.plan)
        self.account_hash = _digest(self.plan["account_id"])
        self.role_hash = _digest(self.plan["role_id"])
        self.manifest_id = _digest(str(self.manifest_path.resolve()))
        self.manifest: JsonObject = {}
        self._disk_hash: Optional[str] = None
        self._started = time.monotonic()

    def _log(self, event: str, **fields: Any) -> None:
        base = {
            "event": event,
            "correlation_id": self.plan["parent_correlation_id"],
            "parent_correlation_id": self.plan["parent_correlation_id"],
            "transaction_id": self.manifest.get("transaction_id"),
            "attempt_id": self.attempt_id,
            "manifest_id": self.manifest_id,
            "manifest_hash": self.manifest.get("journal_hash"),
            "plan_hash": self.plan_hash,
            "account_hash": self.account_hash,
            "role_hash": self.role_hash,
            "elapsed_ms": round((time.monotonic() - self._started) * 1000, 3),
        }
        self.logger({**base, **fields})

    def _lease(self) -> _ManifestLease:
        return _ManifestLease(self.manifest_path, self.attempt_id)

    def _read_manifest(self) -> JsonObject:
        try:
            doc = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ManifestError(
                f"cannot read manifest {self.manifest_path}: {exc}"
            ) from exc
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            raise ManifestError(f"{self.manifest_path} is not a {SCHEMA} manifest")
        claimed = doc.get("journal_hash")
        actual = _manifest_digest(doc)
        if not isinstance(claimed, str) or not hmac.compare_digest(claimed, actual):
            raise ManifestError(f"manifest integrity mismatch: {self.manifest_path}")
        for row in doc.get("steps", []):
            if row.get("state") not in ROW_STATES:
                raise ManifestError(
                    f"manifest row {row.get('step_id')!r} has invalid state"
                )
        return doc

    def _validate_binding(self) -> None:
        expected = {
            "plan_hash": self.plan_hash,
            "account_hash": self.account_hash,
            "role_hash": self.role_hash,
            "parent_correlation_id": self.plan["parent_correlation_id"],
        }
        mismatches = [
            key for key, value in expected.items() if self.manifest.get(key) != value
        ]
        if mismatches:
            raise TransactionRefused(
                "resume binding mismatch: " + ", ".join(sorted(mismatches))
            )

    def _load(self) -> None:
        self.manifest = self._read_manifest()
        self._disk_hash = str(self.manifest["journal_hash"])
        self._validate_binding()
        self._log(
            "manifest_loaded",
            journal_state="FSYNCED",
            revision=self.manifest.get("revision"),
        )

    def _assert_disk_unchanged(self) -> None:
        if not self.manifest_path.exists():
            if self._disk_hash is not None:
                raise TransactionConflict(
                    "manifest disappeared during the active attempt"
                )
            return
        if self._disk_hash is None:
            raise TransactionConflict("a conflicting attempt created this manifest")
        current = self._read_manifest()
        actual = str(current["journal_hash"])
        if not hmac.compare_digest(actual, self._disk_hash):
            raise TransactionConflict("manifest changed outside the active attempt")

    def _persist(self, event: str, **fields: Any) -> None:
        self._assert_disk_unchanged()
        now = _iso(self.clock)
        self.manifest["revision"] = int(self.manifest.get("revision", 0)) + 1
        self.manifest["updated_at"] = now
        self.manifest["previous_manifest_hash"] = self._disk_hash
        self.manifest.setdefault("transitions", []).append(
            {
                "sequence": self.manifest["revision"],
                "at": now,
                "event": event,
                "attempt_id": self.attempt_id,
                **_json_value(fields),
            }
        )
        self.manifest["journal_hash"] = _manifest_digest(self.manifest)
        self._log(
            "manifest_transition",
            transition_event=event,
            revision=self.manifest["revision"],
            journal_state="FSYNC_STARTING",
            **fields,
        )
        self.inject(f"journal.before:{event}", self.manifest, self.manifest_path)
        self.inject(f"journal.fsync:{event}", self.manifest, self.manifest_path)
        self.writer(self.manifest_path, self.manifest)
        self._disk_hash = str(self.manifest["journal_hash"])
        self._log(
            "manifest_fsync",
            transition_event=event,
            revision=self.manifest["revision"],
            journal_state="FSYNCED",
            recovery_argv=self.manifest.get("recovery_argv"),
        )
        self.inject(f"journal.after:{event}", self.manifest, self.manifest_path)

    def _authoritative_identities(self) -> List[Optional[JsonObject]]:
        rows: List[Optional[JsonObject]] = []
        for identity in self.plan["identities"]:
            current = self.adapter.read_identity(
                identity["logical_id"], identity.get("server_target")
            )
            normalized = None if current is None else _json_value(dict(current))
            rows.append(normalized)
            self._log(
                "authoritative_prestate",
                kind="identity",
                logical_id=identity["logical_id"],
                server_id=identity.get("server_target"),
                pre_digest=_digest(normalized),
                readback_id=self.id_factory(),
            )
        return rows

    def _authoritative_rooms(self) -> List[JsonObject]:
        raw = self.adapter.list_rooms()
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise TransactionRefused(
                "authoritative room read did not return a sequence"
            )
        rooms = [_normalize_room(dict(room)) for room in raw]
        ids = [room["server_id"] for room in rooms]
        if len(set(ids)) != len(ids):
            raise TransactionRefused("authoritative room roster repeats a server id")
        self._log(
            "authoritative_prestate",
            kind="room_roster",
            pre_digest=_digest(rooms),
            room_count=len(rooms),
            readback_id=self.id_factory(),
        )
        return rooms

    def _resolve_room(
        self, intended: Mapping[str, Any], rooms: Sequence[JsonObject]
    ) -> Optional[JsonObject]:
        server_target = intended.get("server_target")
        by_name = [
            room
            for room in rooms
            if room["name"].casefold() == intended["name"].casefold()
        ]
        if len(by_name) > 1:
            raise TransactionRefused(
                f"ambiguous intended room {intended['name']!r}: {len(by_name)} authoritative rows"
            )
        if server_target:
            by_id = [room for room in rooms if room["server_id"] == server_target]
            if len(by_id) > 1:
                raise TransactionRefused(
                    f"ambiguous intended room server id {server_target!r}"
                )
            if by_id:
                if by_id[0]["name"].casefold() != intended["name"].casefold():
                    raise TransactionRefused(
                        f"room target {server_target!r} moved from reviewed name {intended['name']!r}"
                    )
                return copy.deepcopy(by_id[0])
            if by_name:
                raise TransactionRefused(
                    f"room name {intended['name']!r} now belongs to {by_name[0]['server_id']!r}"
                )
            return None
        return copy.deepcopy(by_name[0]) if by_name else None

    def _step_base(
        self,
        *,
        step_id: str,
        kind: str,
        logical_target: str,
        server_target: Optional[str],
        before: Optional[Mapping[str, Any]],
        intended: Mapping[str, Any],
        reversible: bool,
        compensation: Mapping[str, Any],
        state: str,
        operation: str,
    ) -> JsonObject:
        delta = (
            _room_diff(before, intended)
            if kind in {"room_members", "room_create"}
            else _mapping_delta(before, intended)
        )
        return {
            "step_id": step_id,
            "kind": kind,
            "operation": operation,
            "logical_target": logical_target,
            "server_target": server_target,
            "state": state,
            "before": copy.deepcopy(before),
            "before_digest": _digest(before),
            "intended": copy.deepcopy(intended),
            "intended_digest": _digest(intended),
            "post": None,
            "post_digest": None,
            "delta": delta,
            "reversibility": "REVERSIBLE" if reversible else "IRREVERSIBLE",
            "compensation": _json_value(compensation),
            "approval": {
                "required": not reversible,
                "state": "NOT_REQUIRED" if reversible else "WAITING_HUMAN",
                "challenge": None,
                "expected_sha256": None,
                "acknowledgement_sha256": None,
            },
            "receipt": {
                "phase": "NOT_STARTED",
                "request_id": None,
                "response_digest": None,
                "error_type": None,
            },
            "proof_readback": {
                "state": "NOT_STARTED",
                "request_id": None,
                "post_digest": None,
            },
            "write_count": 0,
            "compensation_eligible": False,
            "noop": operation == "NO_OP",
        }

    def _ack_challenge(self, row: Mapping[str, Any]) -> JsonObject:
        intended = row["intended"]
        return {
            "schema": ACK_SCHEMA,
            "account_hash": self.account_hash,
            "role_hash": self.role_hash,
            "plan_hash": self.plan_hash,
            "parent_correlation_id": self.plan["parent_correlation_id"],
            "room": {
                "logical_id": row["logical_target"],
                "name": intended["name"],
                "server_id": row["server_target"],
                "member_ids": list(intended["member_ids"]),
                "member_set_hash": _digest(intended["member_ids"]),
            },
        }

    def _build_manifest(
        self,
        identities: Sequence[Optional[JsonObject]],
        rooms: Sequence[JsonObject],
    ) -> JsonObject:
        transaction_id = self.id_factory()
        steps: List[JsonObject] = []
        for spec, before in zip(self.plan["identities"], identities):
            intended = copy.deepcopy(spec["intended"])
            noop = _matches_intended(before, intended)
            compensation = {
                "action": "restore_identity_full"
                if before is not None
                else "remove_created_identity",
                "before_digest": _digest(before),
                "status": "NOT_STARTED",
                "request_id": None,
                "readback_id": None,
            }
            steps.append(
                self._step_base(
                    step_id=f"identity:{spec['logical_id']}",
                    kind="identity",
                    logical_target=spec["logical_id"],
                    server_target=spec.get("server_target"),
                    before=before,
                    intended=intended,
                    reversible=True,
                    compensation=compensation,
                    state="CONFIRMED" if noop else "PLANNED",
                    operation="NO_OP" if noop else "WRITE_IDENTITY",
                )
            )

        for spec in self.plan["rooms"]:
            before = self._resolve_room(spec, rooms)
            if before is not None:
                intended = {
                    "server_id": before["server_id"],
                    "name": before["name"],
                    "description": before["description"],
                    "member_ids": list(spec["member_ids"]),
                }
                noop = before["member_ids"] == intended["member_ids"]
                steps.append(
                    self._step_base(
                        step_id=f"room:{spec['logical_id']}:members",
                        kind="room_members",
                        logical_target=spec["logical_id"],
                        server_target=before["server_id"],
                        before=before,
                        intended=intended,
                        reversible=True,
                        compensation={
                            "action": "restore_exact_full_member_set",
                            "member_ids": list(before["member_ids"]),
                            "member_set_hash": _digest(before["member_ids"]),
                            "status": "NOT_STARTED",
                            "request_id": None,
                            "readback_id": None,
                        },
                        state="CONFIRMED" if noop else "PLANNED",
                        operation="NO_OP" if noop else "REPLACE_FULL_MEMBER_SET",
                    )
                )
                continue

            server_id = spec.get("server_target") or self.id_factory()
            intended = {
                "server_id": server_id,
                "name": spec["name"],
                "description": spec["description"],
                "member_ids": list(spec["member_ids"]),
            }
            row = self._step_base(
                step_id=f"room:{spec['logical_id']}:create",
                kind="room_create",
                logical_target=spec["logical_id"],
                server_target=server_id,
                before=None,
                intended=intended,
                reversible=False,
                compensation={
                    "action": "none",
                    "reason": "no proven DeleteRoom RPC; any created room is permanent residue",
                    "status": "IRREVERSIBLE",
                },
                state="WAITING_HUMAN",
                operation="CREATE_ROOM_POINT_OF_NO_RETURN",
            )
            challenge = self._ack_challenge(row)
            row["approval"]["challenge"] = challenge
            row["approval"]["expected_sha256"] = _digest(challenge)
            steps.append(row)

        state = (
            "WAITING_HUMAN"
            if any(row["state"] == "WAITING_HUMAN" for row in steps)
            else (
                "CONFIRMED"
                if all(row["state"] == "CONFIRMED" for row in steps)
                else "PLANNED"
            )
        )
        now = _iso(self.clock)
        cursor = next(
            (
                index
                for index, row in enumerate(steps)
                if row["state"] not in TERMINAL_ROW_STATES
            ),
            len(steps),
        )
        recovery_argv = [
            part.replace("{manifest}", str(self.manifest_path))
            for part in self.plan["recovery_argv"]
        ]
        return {
            "schema": SCHEMA,
            "tool": TOOL,
            "transaction_id": transaction_id,
            "attempt_id": self.attempt_id,
            "parent_correlation_id": self.plan["parent_correlation_id"],
            "account_hash": self.account_hash,
            "role_hash": self.role_hash,
            "plan_hash": self.plan_hash,
            "manifest_id": self.manifest_id,
            "created_at": now,
            "updated_at": now,
            "state": state,
            "revision": 0,
            "previous_manifest_hash": None,
            "journal_hash": None,
            "resume_cursor": cursor,
            "resume_identity": _digest(
                {
                    "manifest_id": self.manifest_id,
                    "parent_correlation_id": self.plan["parent_correlation_id"],
                    "plan_hash": self.plan_hash,
                }
            ),
            "recovery_argv": recovery_argv,
            "steps": steps,
            "residue": [],
            "failures": [],
            "counters": {
                "writes": 0,
                "noops": sum(bool(row["noop"]) for row in steps),
                "compensations": 0,
            },
            "transitions": [],
        }

    def _prepare_locked(self) -> JsonObject:
        if self.manifest_path.exists():
            self._load()
            self._validate_existing_preconditions()
            return copy.deepcopy(self.manifest)
        identities = self._authoritative_identities()
        rooms = self._authoritative_rooms()
        self.manifest = self._build_manifest(identities, rooms)
        self._persist(
            "manifest.prepared",
            resume_cursor=self.manifest["resume_cursor"],
            step_count=len(self.manifest["steps"]),
            writes=0,
            noops=self.manifest["counters"]["noops"],
        )
        for row in self.manifest["steps"]:
            self._log(
                "planned_step",
                step_id=row["step_id"],
                kind=row["kind"],
                logical_id=row["logical_target"],
                server_id=row["server_target"],
                state=row["state"],
                reversibility=row["reversibility"],
                operation=row["operation"],
                pre_digest=row["before_digest"],
                intended_digest=row["intended_digest"],
                post_digest=None,
                member_diff=row["delta"] if row["kind"].startswith("room") else None,
                approval=row["approval"]["state"],
                receipt=row["receipt"]["phase"],
                proof_readback=row["proof_readback"]["state"],
                compensation=row["compensation"]["action"],
                writes=0,
                noop=row["noop"],
            )
        return copy.deepcopy(self.manifest)

    def prepare(self) -> JsonObject:
        """Fresh-read all pre-state and durably prepare the manifest; no account write occurs."""
        with self._lease():
            return self._prepare_locked()

    def _rooms_now(self) -> List[JsonObject]:
        return self._authoritative_rooms()

    def _current(self, row: Mapping[str, Any]) -> Optional[JsonObject]:
        if row["kind"] == "identity":
            current = self.adapter.read_identity(
                row["logical_target"], row.get("server_target")
            )
            return None if current is None else _json_value(dict(current))
        rooms = self._rooms_now()
        intended = {
            "name": row["intended"]["name"],
            "server_target": row["server_target"],
        }
        return self._resolve_room(intended, rooms)

    def _prestate_matches(
        self, row: Mapping[str, Any], current: Optional[Mapping[str, Any]]
    ) -> bool:
        return hmac.compare_digest(_digest(current), str(row["before_digest"]))

    def _intended_matches(
        self, row: Mapping[str, Any], current: Optional[Mapping[str, Any]]
    ) -> bool:
        if row["kind"] == "identity":
            return _matches_intended(current, row["intended"])
        return current == row["intended"]

    def _validate_existing_preconditions(self) -> None:
        for row in self.manifest.get("steps", []):
            if row["state"] == "COMPENSATED":
                continue
            current = self._current(row)
            if row["state"] == "CONFIRMED":
                if not self._intended_matches(row, current):
                    raise TransactionRefused(
                        f"confirmed step {row['step_id']} moved from its proven post-state"
                    )
            elif row["state"] in {"PLANNED", "WAITING_HUMAN"}:
                if not self._prestate_matches(row, current):
                    raise TransactionRefused(
                        f"pre-state moved for {row['step_id']}; reviewed plan is stale"
                    )
            elif row["state"] == "PARTIAL":
                raise TransactionRefused(
                    f"step {row['step_id']} is PARTIAL; recover or compensate before continuing"
                )

    def _advance_cursor(self) -> None:
        self.manifest["resume_cursor"] = next(
            (
                index
                for index, row in enumerate(self.manifest["steps"])
                if row["state"] not in TERMINAL_ROW_STATES
            ),
            len(self.manifest["steps"]),
        )

    def _set_partial(
        self,
        row: JsonObject,
        reason: str,
        *,
        compensation_eligible: bool = False,
        persist: bool = True,
    ) -> None:
        row["state"] = "PARTIAL"
        row["compensation_eligible"] = compensation_eligible
        self.manifest["state"] = "PARTIAL"
        self.manifest.setdefault("failures", []).append(
            {
                "step_id": row["step_id"],
                "reason": reason,
                "at": _iso(self.clock),
                "recovery_argv": list(self.manifest["recovery_argv"]),
            }
        )
        self._advance_cursor()
        if persist:
            self._persist(
                "step.partial",
                step_id=row["step_id"],
                reason=reason,
                compensation_eligible=compensation_eligible,
                resume_cursor=self.manifest["resume_cursor"],
            )

    def _verify_ack(self, row: JsonObject, token: Optional[str]) -> None:
        expected = str(row["approval"]["expected_sha256"])
        if token is None:
            raise TransactionRefused(
                f"room {row['logical_target']!r} awaits its exact acknowledgement"
            )
        supplied_hash = _digest(str(token))
        if not hmac.compare_digest(str(token), expected):
            self._persist(
                "room.ack_refused",
                step_id=row["step_id"],
                logical_id=row["logical_target"],
                server_id=row["server_target"],
                acknowledgement_hash=supplied_hash,
            )
            raise TransactionRefused(
                f"acknowledgement does not bind room {row['logical_target']!r} to this plan"
            )
        row["approval"]["state"] = "ACKNOWLEDGED"
        row["approval"]["acknowledgement_sha256"] = supplied_hash
        self._persist(
            "room.acknowledged",
            step_id=row["step_id"],
            logical_id=row["logical_target"],
            server_id=row["server_target"],
            acknowledgement_hash=supplied_hash,
            member_set_hash=_digest(row["intended"]["member_ids"]),
        )

    def _reconcile_running(self, row: JsonObject) -> bool:
        current = self._current(row)
        if self._intended_matches(row, current):
            row["post"] = copy.deepcopy(current)
            row["post_digest"] = _digest(current)
            row["state"] = "CONFIRMED"
            row["proof_readback"].update(
                {
                    "state": "CONFIRMED",
                    "request_id": self.id_factory(),
                    "post_digest": row["post_digest"],
                }
            )
            self._advance_cursor()
            self._persist(
                "step.reconciled",
                step_id=row["step_id"],
                request_id=row["receipt"]["request_id"],
                readback_id=row["proof_readback"]["request_id"],
                post_digest=row["post_digest"],
                writes=0,
            )
            return True
        if self._prestate_matches(row, current):
            phase = row["receipt"].get("phase")
            if row["kind"] == "room_create" and phase == "DISPATCHING":
                self._set_partial(
                    row,
                    "irreversible create dispatch outcome is unknown; refusing a duplicate create",
                )
                return False
            return True
        self._set_partial(
            row,
            "authoritative state matches neither reviewed pre-state nor intended state",
            compensation_eligible=row["receipt"].get("phase")
            in {"DISPATCHING", "RECEIPTED"},
        )
        return False

    def _call_write(self, row: JsonObject, request_id: str) -> Mapping[str, Any]:
        if row["kind"] == "identity":
            return self.adapter.write_identity(
                row["logical_target"],
                row.get("server_target"),
                row["intended"],
                request_id,
            )
        if row["kind"] == "room_members":
            return self.adapter.set_room_members(
                str(row["server_target"]), row["intended"]["member_ids"], request_id
            )
        if row["kind"] == "room_create":
            intended = row["intended"]
            return self.adapter.create_room(
                str(row["server_target"]),
                intended["name"],
                intended["description"],
                intended["member_ids"],
                request_id,
            )
        raise ManifestError(f"unknown transaction step kind {row['kind']!r}")

    def _proof(self, row: JsonObject) -> bool:
        readback_id = self.id_factory()
        row["proof_readback"]["state"] = "RUNNING"
        row["proof_readback"]["request_id"] = readback_id
        self._persist(
            "readback.before",
            step_id=row["step_id"],
            readback_id=readback_id,
        )
        current = self._current(row)
        row["post"] = copy.deepcopy(current)
        row["post_digest"] = _digest(current)
        row["proof_readback"]["post_digest"] = row["post_digest"]
        confirmed = self._intended_matches(row, current)
        row["proof_readback"]["state"] = "CONFIRMED" if confirmed else "DEVIANT"
        row["state"] = "CONFIRMED" if confirmed else "PARTIAL"
        self._advance_cursor()
        self._persist(
            "readback.after",
            step_id=row["step_id"],
            readback_id=readback_id,
            pre_digest=row["before_digest"],
            intended_digest=row["intended_digest"],
            post_digest=row["post_digest"],
            member_diff=row["delta"] if row["kind"].startswith("room") else None,
            confirmed=confirmed,
        )
        self._log(
            "authoritative_readback",
            step_id=row["step_id"],
            logical_id=row["logical_target"],
            server_id=row["server_target"],
            readback_id=readback_id,
            pre_digest=row["before_digest"],
            intended_digest=row["intended_digest"],
            post_digest=row["post_digest"],
            member_diff=row["delta"] if row["kind"].startswith("room") else None,
            confirmed=confirmed,
        )
        return confirmed

    def _execute_row(self, row: JsonObject, acknowledgement: Optional[str]) -> bool:
        if row["state"] == "CONFIRMED":
            current = self._current(row)
            if not self._intended_matches(row, current):
                self._set_partial(row, "confirmed step moved before replay")
                return False
            self._log(
                "step_replay_noop",
                step_id=row["step_id"],
                logical_id=row["logical_target"],
                server_id=row["server_target"],
                writes=0,
                noop=True,
                post_digest=_digest(current),
            )
            return True
        if row["state"] == "COMPENSATED":
            return True
        if row["state"] == "PARTIAL":
            return False
        if row["state"] == "RUNNING" and not self._reconcile_running(row):
            return False
        if row["state"] == "CONFIRMED":
            return True

        if row["kind"] == "room_create":
            try:
                self._verify_ack(row, acknowledgement)
            except TransactionRefused:
                return False

        current = self._current(row)
        if not self._prestate_matches(row, current):
            self._set_partial(row, "pre-state moved immediately before mutation")
            return False

        request_id = row["receipt"].get("request_id") or self.id_factory()
        row["state"] = "RUNNING"
        row["receipt"].update(
            {
                "phase": "PREPARED",
                "request_id": request_id,
                "response_digest": None,
                "error_type": None,
            }
        )
        self.manifest["state"] = "RUNNING"
        self._persist(
            "step.running",
            step_id=row["step_id"],
            logical_id=row["logical_target"],
            server_id=row["server_target"],
            request_id=request_id,
            pre_digest=row["before_digest"],
            intended_digest=row["intended_digest"],
            acknowledgement_hash=row["approval"].get("acknowledgement_sha256"),
        )
        self.inject(f"before_write:{row['step_id']}", self.manifest, self.manifest_path)
        row["receipt"]["phase"] = "DISPATCHING"
        self._persist(
            "mutation.dispatching", step_id=row["step_id"], request_id=request_id
        )
        self._log(
            "mutation_request",
            step_id=row["step_id"],
            logical_id=row["logical_target"],
            server_id=row["server_target"],
            request_id=request_id,
            pre_digest=row["before_digest"],
            intended_digest=row["intended_digest"],
            acknowledgement_hash=row["approval"].get("acknowledgement_sha256"),
            writes=1,
        )
        try:
            with durable_transition():
                receipt = self._call_write(row, request_id)
                row["receipt"].update(
                    {
                        "phase": "RECEIPTED",
                        "response_digest": _digest(receipt),
                        "error_type": None,
                    }
                )
                row["write_count"] = int(row["write_count"]) + 1
                self.manifest["counters"]["writes"] += 1
                self._persist(
                    "mutation.receipted",
                    step_id=row["step_id"],
                    request_id=request_id,
                    response_digest=row["receipt"]["response_digest"],
                    writes=1,
                )
        except InjectedFailure:
            raise
        except Exception as exc:
            row["receipt"]["error_type"] = type(exc).__name__
            self._persist(
                "mutation.failed",
                step_id=row["step_id"],
                request_id=request_id,
                error_type=type(exc).__name__,
            )
            current_after = self._current(row)
            if self._intended_matches(row, current_after):
                return self._proof(row)
            self._set_partial(
                row,
                f"mutation failed: {type(exc).__name__}",
                compensation_eligible=True,
            )
            return False

        self._log(
            "mutation_result",
            step_id=row["step_id"],
            logical_id=row["logical_target"],
            server_id=row["server_target"],
            request_id=request_id,
            response_digest=row["receipt"]["response_digest"],
            writes=1,
        )
        self.inject(f"after_write:{row['step_id']}", self.manifest, self.manifest_path)
        if not self._proof(row):
            self._set_partial(
                row,
                "authoritative readback did not equal intended state",
                compensation_eligible=True,
            )
            return False
        return True

    def _restore_row(self, row: JsonObject) -> bool:
        current = self._current(row)
        if self._prestate_matches(row, current):
            row["state"] = "COMPENSATED"
            row["compensation"]["status"] = "CONFIRMED"
            row["compensation"]["readback_id"] = self.id_factory()
            self._advance_cursor()
            self._persist(
                "compensation.reconciled",
                step_id=row["step_id"],
                readback_id=row["compensation"]["readback_id"],
                writes=0,
            )
            return True
        if row["state"] == "CONFIRMED" and not self._intended_matches(row, current):
            row["compensation"]["status"] = "PARTIAL"
            row["compensation_eligible"] = False
            row["state"] = "PARTIAL"
            self._persist(
                "compensation.refused",
                step_id=row["step_id"],
                reason="authoritative post-state moved; refusing to overwrite external state",
                writes=0,
            )
            return False
        request_id = row["compensation"].get("request_id") or self.id_factory()
        row["compensation"].update({"status": "RUNNING", "request_id": request_id})
        self._persist(
            "compensation.running",
            step_id=row["step_id"],
            request_id=request_id,
            target_digest=row["before_digest"],
        )
        try:
            with durable_transition():
                if row["kind"] == "identity":
                    receipt = self.adapter.restore_identity(
                        row["logical_target"],
                        row.get("server_target"),
                        row["before"],
                        request_id,
                    )
                else:
                    receipt = self.adapter.set_room_members(
                        str(row["server_target"]),
                        row["before"]["member_ids"],
                        request_id,
                    )
                self.manifest["counters"]["compensations"] += 1
                self._persist(
                    "compensation.receipted",
                    step_id=row["step_id"],
                    request_id=request_id,
                    response_digest=_digest(receipt),
                )
        except Exception as exc:
            row["compensation"]["status"] = "PARTIAL"
            row["state"] = "PARTIAL"
            self._persist(
                "compensation.failed",
                step_id=row["step_id"],
                request_id=request_id,
                error_type=type(exc).__name__,
            )
            return False
        readback_id = self.id_factory()
        restored = self._current(row)
        clean = self._prestate_matches(row, restored)
        row["compensation"].update(
            {
                "status": "CONFIRMED" if clean else "PARTIAL",
                "readback_id": readback_id,
                "post_digest": _digest(restored),
            }
        )
        row["state"] = "COMPENSATED" if clean else "PARTIAL"
        self._advance_cursor()
        self._persist(
            "compensation.readback",
            step_id=row["step_id"],
            request_id=request_id,
            readback_id=readback_id,
            pre_digest=row["before_digest"],
            post_digest=_digest(restored),
            confirmed=clean,
        )
        self._log(
            "compensation_result",
            step_id=row["step_id"],
            logical_id=row["logical_target"],
            server_id=row["server_target"],
            request_id=request_id,
            readback_id=readback_id,
            compensation="CONFIRMED" if clean else "PARTIAL",
            pre_digest=row["before_digest"],
            post_digest=_digest(restored),
        )
        return clean

    def _compensate_reversible(self) -> bool:
        clean = True
        for row in reversed(self.manifest["steps"]):
            if row["reversibility"] != "REVERSIBLE" or row["noop"]:
                continue
            if row["state"] not in {"RUNNING", "CONFIRMED", "PARTIAL"}:
                continue
            if row["state"] == "PARTIAL" and not row.get("compensation_eligible"):
                continue
            clean = self._restore_row(row) and clean
        return clean

    def _refresh_residue(self) -> bool:
        create_rows = [
            row for row in self.manifest["steps"] if row["kind"] == "room_create"
        ]
        if not create_rows:
            self.manifest["residue"] = []
            return True
        try:
            rooms = self._rooms_now()
            readable = True
        except Exception:
            rooms = []
            readable = False
        residue: List[JsonObject] = []
        for row in create_rows:
            room = next(
                (item for item in rooms if item["server_id"] == row["server_target"]),
                None,
            )
            phase = row["receipt"].get("phase")
            if (
                room is None
                and row["write_count"] == 0
                and phase in {"NOT_STARTED", "PREPARED"}
            ):
                continue
            presence = (
                "PRESENT" if room is not None else ("ABSENT" if readable else "UNKNOWN")
            )
            residue.append(
                {
                    "logical_id": row["logical_target"],
                    "server_id": row["server_target"],
                    "name": row["intended"]["name"],
                    "presence": presence,
                    "outcome_certainty": (
                        "CONFIRMED_PRESENT"
                        if room is not None
                        else (
                            "UNKNOWN_AFTER_DISPATCH"
                            if phase == "DISPATCHING"
                            else "CONFIRMED_ABSENT"
                        )
                    ),
                    "permanent": True,
                    "member_ids": list(room["member_ids"]) if room else None,
                    "member_set_hash": _digest(room["member_ids"]) if room else None,
                    "intended_member_set_hash": _digest(row["intended"]["member_ids"]),
                    "state": row["state"],
                }
            )
        self.manifest["residue"] = residue
        return readable

    def _outcome(self, verdict: str, exit_code: int, reason: str) -> TransactionOutcome:
        self.manifest["state"] = verdict
        self._advance_cursor()
        self._refresh_residue()
        self._persist(
            "transaction.verdict",
            verdict=verdict,
            exit=exit_code,
            reason=reason,
            residue_count=len(self.manifest["residue"]),
            writes=self.manifest["counters"]["writes"],
            noops=self.manifest["counters"]["noops"],
            compensations=self.manifest["counters"]["compensations"],
            recovery_argv=self.manifest["recovery_argv"],
        )
        self._log(
            "transaction_verdict",
            verdict=verdict,
            exit=exit_code,
            reason=reason,
            residue=copy.deepcopy(self.manifest["residue"]),
            writes=self.manifest["counters"]["writes"],
            noops=self.manifest["counters"]["noops"],
            compensation=self.manifest["counters"]["compensations"],
            recovery_argv=self.manifest["recovery_argv"],
        )
        return TransactionOutcome(
            verdict, exit_code, reason, copy.deepcopy(self.manifest)
        )

    def apply(
        self, acknowledgements: Optional[Mapping[str, str]] = None
    ) -> TransactionOutcome:
        """Apply or resume the exact plan, stopping at the first refusal or unproven mutation."""
        acknowledgements = acknowledgements or {}
        with self._lease():
            if self.manifest_path.exists():
                self._load()
            else:
                self._prepare_locked()
            if self.manifest["state"] == "COMPENSATED":
                return self._outcome(
                    "COMPENSATED", EXIT_OK, "transaction already compensated"
                )
            for row in self.manifest["steps"]:
                acknowledgement = acknowledgements.get(row["logical_target"])
                before_state = row["state"]
                try:
                    ok = self._execute_row(row, acknowledgement)
                except InjectedFailure:
                    raise
                except (TransactionConflict, ManifestError):
                    raise
                except Exception as exc:
                    phase = row["receipt"].get("phase")
                    compensation_eligible = row[
                        "reversibility"
                    ] == "REVERSIBLE" and phase in {"DISPATCHING", "RECEIPTED"}
                    self._set_partial(
                        row,
                        f"authoritative operation failed: {type(exc).__name__}",
                        compensation_eligible=compensation_eligible,
                    )
                    code = (
                        EXIT_FINDINGS
                        if compensation_eligible or row["kind"] == "room_create"
                        else EXIT_REFUSED
                    )
                    return self._outcome(
                        "PARTIAL",
                        code,
                        "authoritative state could not be proven; execution stopped",
                    )
                if ok:
                    continue
                if row["kind"] == "room_create" and row["state"] == "WAITING_HUMAN":
                    return self._outcome(
                        "WAITING_HUMAN",
                        EXIT_REFUSED,
                        "exact room acknowledgement required",
                    )
                if (
                    row["kind"] == "room_create"
                    and before_state == "WAITING_HUMAN"
                    and row["state"] != "PARTIAL"
                ):
                    return self._outcome(
                        "WAITING_HUMAN", EXIT_REFUSED, "room acknowledgement refused"
                    )
                if row["reversibility"] == "REVERSIBLE" and not row.get(
                    "compensation_eligible"
                ):
                    return self._outcome(
                        "PARTIAL",
                        EXIT_REFUSED,
                        "authoritative state moved; refused without an account write",
                    )
                compensation_clean = self._compensate_reversible()
                reason = (
                    "mutation unconfirmed; reversible pre-state restored"
                    if compensation_clean
                    else ("mutation unconfirmed and compensation remains partial")
                )
                return self._outcome("PARTIAL", EXIT_FINDINGS, reason)
            return self._outcome(
                "CONFIRMED", EXIT_OK, "all planned steps authoritatively confirmed"
            )

    def compensate(self) -> TransactionOutcome:
        """Restore every reversible full pre-state in reverse order and census room residue."""
        with self._lease():
            self._load()
            clean = self._compensate_reversible()
            residue_readable = self._refresh_residue()
            residue = list(self.manifest["residue"])
            if not clean or not residue_readable or residue:
                reason = (
                    "reversible pre-state restored; permanent room residue remains"
                    if clean and residue_readable and residue
                    else "compensation or residue readback remains partial"
                )
                return self._outcome("PARTIAL", EXIT_FINDINGS, reason)
            return self._outcome(
                "COMPENSATED", EXIT_OK, "reversible pre-state restored"
            )


def prepare_transaction(
    plan: Mapping[str, Any],
    manifest_path: pathlib.Path,
    adapter: TransactionAdapter,
    **kwargs: Any,
) -> JsonObject:
    """Functional entry point for a fresh-read, locally durable, zero-account-write plan."""
    return RoleTransaction(plan, manifest_path, adapter, **kwargs).prepare()


def apply_transaction(
    plan: Mapping[str, Any],
    manifest_path: pathlib.Path,
    adapter: TransactionAdapter,
    acknowledgements: Optional[Mapping[str, str]] = None,
    **kwargs: Any,
) -> TransactionOutcome:
    """Functional entry point for apply and same-manifest resume."""
    return RoleTransaction(plan, manifest_path, adapter, **kwargs).apply(
        acknowledgements
    )


def compensate_transaction(
    plan: Mapping[str, Any],
    manifest_path: pathlib.Path,
    adapter: TransactionAdapter,
    **kwargs: Any,
) -> TransactionOutcome:
    """Functional entry point for reverse-order compensation and residue census."""
    return RoleTransaction(plan, manifest_path, adapter, **kwargs).compensate()


class _FakeAdapter:
    """In-memory authoritative service used only by the offline public selftest."""

    def __init__(
        self,
        identities: Optional[Mapping[str, Optional[Mapping[str, Any]]]] = None,
        rooms: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> None:
        self.identities: Dict[str, Optional[JsonObject]] = {
            key: None if value is None else _json_value(dict(value))
            for key, value in (identities or {}).items()
        }
        self.rooms: List[JsonObject] = [_normalize_room(room) for room in (rooms or [])]
        self.calls: List[Tuple[str, str]] = []
        self.failure: Optional[Tuple[str, str]] = None
        self.failed_once = False

    def _trip(self, method: str, timing: str) -> None:
        if self.failure == (method, timing) and not self.failed_once:
            self.failed_once = True
            raise RuntimeError(f"offline {method} {timing}")

    def read_identity(
        self, logical_target: str, _server_target: Optional[str]
    ) -> Optional[Mapping[str, Any]]:
        value = self.identities.get(logical_target)
        return None if value is None else copy.deepcopy(value)

    def list_rooms(self) -> Sequence[Mapping[str, Any]]:
        return copy.deepcopy(self.rooms)

    def write_identity(
        self,
        logical_target: str,
        _server_target: Optional[str],
        intended: Mapping[str, Any],
        request_id: str,
    ) -> Mapping[str, Any]:
        self.calls.append(("write_identity", request_id))
        self._trip("write_identity", "before")
        self.identities[logical_target] = _json_value(dict(intended))
        self._trip("write_identity", "after")
        return {"request_id": request_id, "accepted": True}

    def restore_identity(
        self,
        logical_target: str,
        _server_target: Optional[str],
        before: Optional[Mapping[str, Any]],
        request_id: str,
    ) -> Mapping[str, Any]:
        self.calls.append(("restore_identity", request_id))
        self._trip("restore_identity", "before")
        self.identities[logical_target] = (
            None if before is None else _json_value(dict(before))
        )
        self._trip("restore_identity", "after")
        return {"request_id": request_id, "accepted": True}

    def create_room(
        self,
        server_target: str,
        name: str,
        description: str,
        member_ids: Sequence[str],
        request_id: str,
    ) -> Mapping[str, Any]:
        self.calls.append(("create_room", request_id))
        self._trip("create_room", "before")
        room = {
            "server_id": server_target,
            "name": name,
            "description": description,
            "member_ids": sorted(member_ids),
        }
        if self.failure == ("create_room", "deviant") and not self.failed_once:
            self.failed_once = True
            room["member_ids"] = list(room["member_ids"][:1])
            self.rooms.append(room)
            raise RuntimeError("offline deviant create")
        self.rooms.append(room)
        self._trip("create_room", "after")
        return {"request_id": request_id, "agent": {"agentId": server_target}}

    def set_room_members(
        self, server_target: str, member_ids: Sequence[str], request_id: str
    ) -> Mapping[str, Any]:
        self.calls.append(("set_room_members", request_id))
        self._trip("set_room_members", "before")
        room = next(room for room in self.rooms if room["server_id"] == server_target)
        if self.failure == ("set_room_members", "partial") and not self.failed_once:
            self.failed_once = True
            room["member_ids"] = sorted(member_ids)[:1]
            raise RuntimeError("offline partial member replace")
        room["member_ids"] = sorted(member_ids)
        self._trip("set_room_members", "after")
        return {"request_id": request_id, "agent": {"agentId": server_target}}


class _Ids:
    def __init__(self, prefix: int = 1) -> None:
        self.value = prefix

    def __call__(self) -> str:
        value = f"00000000-0000-0000-0000-{self.value:012d}"
        self.value += 1
        return value


def _fixture_plan(
    *,
    identities: Optional[Sequence[Mapping[str, Any]]] = None,
    rooms: Optional[Sequence[Mapping[str, Any]]] = None,
    parent: str = "parent-transaction-1",
) -> JsonObject:
    return {
        "account_id": "offline-account",
        "role_id": "offline-role",
        "parent_correlation_id": parent,
        "recovery_argv": [
            "python3",
            "offline-role-driver.py",
            "--resume",
            "{manifest}",
        ],
        "identities": list(identities or []),
        "rooms": list(rooms or []),
    }


def _quiet() -> Tuple[List[Mapping[str, Any]], LogSink]:
    rows: List[Mapping[str, Any]] = []
    return rows, rows.append


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _case_apply_replay_compensation() -> None:
    with tempfile.TemporaryDirectory(prefix="gb-role-tx-happy-") as tmp:
        path = pathlib.Path(tmp) / "manifest.json"
        old_identity = {"name": "Builder", "title": "old"}
        adapter = _FakeAdapter(
            {"builder": old_identity},
            [
                {
                    "server_id": "existing-room",
                    "name": "Existing",
                    "description": "kept",
                    "member_ids": ["a", "b"],
                }
            ],
        )
        plan = _fixture_plan(
            identities=[
                {
                    "logical_id": "builder",
                    "server_target": "bot-1",
                    "intended": {"name": "Builder", "title": "new"},
                }
            ],
            rooms=[
                {
                    "logical_id": "existing",
                    "name": "Existing",
                    "member_ids": ["b", "c"],
                },
                {
                    "logical_id": "new",
                    "name": "New Room",
                    "member_ids": ["bot-1", "bot-2"],
                },
            ],
        )
        logs, logger = _quiet()
        tx = RoleTransaction(plan, path, adapter, id_factory=_Ids(), logger=logger)
        manifest = tx.prepare()
        new_id = next(row for row in manifest["steps"] if row["kind"] == "room_create")[
            "server_target"
        ]
        _assert(
            path.is_file() and manifest["counters"]["writes"] == 0,
            "prepare must precede account writes",
        )
        token = room_acknowledgement(manifest, "new")
        outcome = RoleTransaction(
            plan, path, adapter, id_factory=_Ids(100), logger=logger
        ).apply({"new": token})
        _assert(outcome.verdict == "CONFIRMED", "happy transaction did not confirm")
        first_calls = len(adapter.calls)
        replay = RoleTransaction(
            plan, path, adapter, id_factory=_Ids(200), logger=logger
        ).apply({"new": token})
        _assert(
            replay.verdict == "CONFIRMED" and len(adapter.calls) == first_calls,
            "replay repeated a write",
        )
        rendered = RoleTransaction(
            plan, path, adapter, id_factory=_Ids(300), logger=logger
        ).prepare()
        stable_id = next(
            row for row in rendered["steps"] if row["kind"] == "room_create"
        )["server_target"]
        _assert(stable_id == new_id, "re-render regenerated the room nonce")
        compensation = RoleTransaction(
            plan, path, adapter, id_factory=_Ids(400), logger=logger
        ).compensate()
        _assert(
            compensation.verdict == "PARTIAL",
            "permanent residue must keep compensation partial",
        )
        _assert(
            adapter.identities["builder"] == old_identity,
            "identity pre-state was not restored",
        )
        existing = next(
            room for room in adapter.rooms if room["server_id"] == "existing-room"
        )
        _assert(
            existing["member_ids"] == ["a", "b"], "FULL-SET pre-state was not restored"
        )
        residue = compensation.manifest["residue"]
        _assert(
            len(residue) == 1
            and residue[0]["server_id"] == new_id
            and residue[0]["presence"] == "PRESENT",
            "permanent room residue was not stated exactly",
        )
        required_log_fields = {
            "correlation_id",
            "manifest_hash",
            "plan_hash",
            "account_hash",
            "elapsed_ms",
        }
        _assert(
            logs and all(required_log_fields <= set(row) for row in logs),
            "structured log binding incomplete",
        )


def _case_interruptions() -> None:
    fixtures = (
        (
            "identity:bot",
            _fixture_plan(
                identities=[
                    {"logical_id": "bot", "intended": {"name": "Bot", "title": "new"}}
                ]
            ),
            lambda: _FakeAdapter({"bot": {"name": "Bot", "title": "old"}}),
            {},
            "write_identity",
        ),
        (
            "room:existing:members",
            _fixture_plan(
                rooms=[
                    {
                        "logical_id": "existing",
                        "name": "Existing",
                        "member_ids": ["b", "c"],
                    }
                ]
            ),
            lambda: _FakeAdapter(
                rooms=[
                    {
                        "server_id": "room-1",
                        "name": "Existing",
                        "member_ids": ["a", "b"],
                    }
                ]
            ),
            {},
            "set_room_members",
        ),
        (
            "room:new:create",
            _fixture_plan(
                rooms=[{"logical_id": "new", "name": "New", "member_ids": ["a", "b"]}]
            ),
            lambda: _FakeAdapter(),
            None,
            "create_room",
        ),
    )
    for step_id, plan, make_adapter, fixed_acks, method in fixtures:
        for position in ("before_write", "after_write"):
            with tempfile.TemporaryDirectory(prefix="gb-role-tx-interrupt-") as tmp:
                path = pathlib.Path(tmp) / "manifest.json"
                adapter = make_adapter()
                logs, logger = _quiet()
                prepared = RoleTransaction(
                    plan, path, adapter, id_factory=_Ids(), logger=logger
                ).prepare()
                acks = (
                    fixed_acks
                    if fixed_acks is not None
                    else {"new": room_acknowledgement(prepared, "new")}
                )
                injector = FailureInjector(f"{position}:{step_id}")
                try:
                    RoleTransaction(
                        plan,
                        path,
                        adapter,
                        id_factory=_Ids(100),
                        logger=logger,
                        inject=injector,
                    ).apply(acks)
                except InjectedFailure:
                    pass
                else:
                    raise AssertionError(f"{position}:{step_id} did not interrupt")
                injector.assert_fired()
                calls_after_interrupt = sum(call[0] == method for call in adapter.calls)
                resumed = RoleTransaction(
                    plan, path, adapter, id_factory=_Ids(200), logger=logger
                ).apply(acks)
                _assert(
                    resumed.verdict == "CONFIRMED",
                    f"resume failed for {position}:{step_id}",
                )
                calls_after_resume = sum(call[0] == method for call in adapter.calls)
                expected = 1
                _assert(
                    calls_after_resume == expected,
                    f"resume repeated {method} for {position}:{step_id}",
                )
                if position == "before_write":
                    _assert(
                        calls_after_interrupt == 0,
                        "before-write interruption reached adapter",
                    )


def _case_journal_and_concurrency() -> None:
    plan = _fixture_plan(
        identities=[{"logical_id": "bot", "intended": {"name": "Bot"}}]
    )
    with tempfile.TemporaryDirectory(prefix="gb-role-tx-journal-") as tmp:
        path = pathlib.Path(tmp) / "manifest.json"
        adapter = _FakeAdapter({"bot": None})
        injector = FailureInjector("journal.fsync:manifest.prepared")
        logs, logger = _quiet()
        try:
            RoleTransaction(
                plan, path, adapter, id_factory=_Ids(), logger=logger, inject=injector
            ).prepare()
        except InjectedFailure:
            pass
        else:
            raise AssertionError("journal fsync interruption did not fire")
        injector.assert_fired()
        _assert(
            not path.exists() and not adapter.calls,
            "journal failure allowed a write or partial manifest",
        )

        RoleTransaction(
            plan, path, adapter, id_factory=_Ids(100), logger=logger
        ).prepare()
        owner = RoleTransaction(
            plan, path, adapter, attempt_id="owner", id_factory=_Ids(200), logger=logger
        )
        contender = RoleTransaction(
            plan,
            path,
            adapter,
            attempt_id="contender",
            id_factory=_Ids(300),
            logger=logger,
        )
        with owner._lease():
            try:
                contender.apply()
            except TransactionConflict:
                pass
            else:
                raise AssertionError(
                    "conflicting concurrent attempt acquired the manifest"
                )


def _case_refusals_and_bindings() -> None:
    with tempfile.TemporaryDirectory(prefix="gb-role-tx-refuse-") as tmp:
        root = pathlib.Path(tmp)
        duplicate_plan = _fixture_plan(
            rooms=[{"logical_id": "dup", "name": "Duplicate", "member_ids": []}]
        )
        duplicate = _FakeAdapter(
            rooms=[
                {"server_id": "room-1", "name": "Duplicate", "member_ids": []},
                {"server_id": "room-2", "name": "duplicate", "member_ids": []},
            ]
        )
        try:
            RoleTransaction(
                duplicate_plan,
                root / "duplicate.json",
                duplicate,
                id_factory=_Ids(),
                logger=lambda _r: None,
            ).prepare()
        except TransactionRefused:
            pass
        else:
            raise AssertionError("ambiguous room was accepted")
        _assert(
            not duplicate.calls and not (root / "duplicate.json").exists(),
            "ambiguous room caused a write",
        )

        plan = _fixture_plan(
            identities=[
                {"logical_id": "bot", "intended": {"name": "Bot", "title": "new"}}
            ]
        )
        path = root / "drift.json"
        adapter = _FakeAdapter({"bot": {"name": "Bot", "title": "old"}})
        RoleTransaction(
            plan, path, adapter, id_factory=_Ids(), logger=lambda _r: None
        ).prepare()
        adapter.identities["bot"] = {"name": "Bot", "title": "moved"}
        outcome = RoleTransaction(
            plan, path, adapter, id_factory=_Ids(100), logger=lambda _r: None
        ).apply()
        _assert(
            outcome.verdict == "PARTIAL"
            and outcome.exit_code == EXIT_REFUSED
            and not adapter.calls,
            "moved pre-state was not refused",
        )
        replay_path = root / "replay-drift.json"
        replay_adapter = _FakeAdapter({"bot": {"name": "Bot", "title": "old"}})
        RoleTransaction(
            plan,
            replay_path,
            replay_adapter,
            id_factory=_Ids(400),
            logger=lambda _r: None,
        ).apply()
        calls_before_drift = len(replay_adapter.calls)
        replay_adapter.identities["bot"] = {"name": "Bot", "title": "external"}
        replay_refusal = RoleTransaction(
            plan,
            replay_path,
            replay_adapter,
            id_factory=_Ids(500),
            logger=lambda _r: None,
        ).apply()
        _assert(
            replay_refusal.exit_code == EXIT_REFUSED
            and len(replay_adapter.calls) == calls_before_drift
            and replay_adapter.identities["bot"]["title"] == "external",
            "replay overwrote a moved confirmed post-state",
        )
        changed = copy.deepcopy(plan)
        changed["identities"][0]["intended"]["title"] = "different-plan"
        try:
            RoleTransaction(
                changed, path, adapter, id_factory=_Ids(200), logger=lambda _r: None
            ).prepare()
        except TransactionRefused:
            pass
        else:
            raise AssertionError("plan hash drift was accepted")
        wrong_parent = copy.deepcopy(plan)
        wrong_parent["parent_correlation_id"] = "other-parent"
        try:
            RoleTransaction(
                wrong_parent,
                path,
                adapter,
                id_factory=_Ids(300),
                logger=lambda _r: None,
            ).prepare()
        except TransactionRefused:
            pass
        else:
            raise AssertionError("parent correlation drift was accepted")


def _case_ack_scope_and_multi_room_residue() -> None:
    with tempfile.TemporaryDirectory(prefix="gb-role-tx-ack-") as tmp:
        path = pathlib.Path(tmp) / "manifest.json"
        plan = _fixture_plan(
            rooms=[
                {"logical_id": "one", "name": "One", "member_ids": ["a"]},
                {"logical_id": "two", "name": "Two", "member_ids": ["b"]},
            ]
        )
        adapter = _FakeAdapter()
        manifest = RoleTransaction(
            plan, path, adapter, id_factory=_Ids(), logger=lambda _r: None
        ).prepare()
        one = room_acknowledgement(manifest, "one")
        two = room_acknowledgement(manifest, "two")
        _assert(one != two, "room acknowledgements are not independently bound")
        refused = RoleTransaction(
            plan, path, adapter, id_factory=_Ids(100), logger=lambda _r: None
        ).apply({"one": one, "two": one})
        _assert(
            refused.exit_code == EXIT_REFUSED,
            "cross-room acknowledgement was not refused",
        )
        _assert(
            len(adapter.rooms) == 1 and adapter.rooms[0]["name"] == "One",
            "later room was created",
        )
        _assert(
            len(refused.manifest["residue"]) == 1
            and refused.manifest["residue"][0]["presence"] == "PRESENT",
            "first permanent room missing from residue census",
        )
        confirmed = RoleTransaction(
            plan, path, adapter, id_factory=_Ids(200), logger=lambda _r: None
        ).apply({"two": two})
        _assert(
            confirmed.verdict == "CONFIRMED" and len(adapter.rooms) == 2,
            "correct second ack did not resume",
        )


def _case_partial_and_failed_compensation() -> None:
    member_plan = _fixture_plan(
        rooms=[{"logical_id": "existing", "name": "Existing", "member_ids": ["b", "c"]}]
    )
    initial_room = {
        "server_id": "room-1",
        "name": "Existing",
        "member_ids": ["a", "b", "z"],
    }
    with tempfile.TemporaryDirectory(prefix="gb-role-tx-partial-") as tmp:
        path = pathlib.Path(tmp) / "manifest.json"
        adapter = _FakeAdapter(rooms=[initial_room])
        adapter.failure = ("set_room_members", "partial")
        outcome = RoleTransaction(
            member_plan, path, adapter, id_factory=_Ids(), logger=lambda _r: None
        ).apply()
        restored = next(room for room in adapter.rooms if room["server_id"] == "room-1")
        _assert(outcome.verdict == "PARTIAL", "partial member replace reported success")
        _assert(
            restored["member_ids"] == ["a", "b", "z"],
            "partial replace did not restore full set",
        )

    with tempfile.TemporaryDirectory(prefix="gb-role-tx-compfail-") as tmp:
        path = pathlib.Path(tmp) / "manifest.json"
        adapter = _FakeAdapter(rooms=[initial_room])
        prepared = RoleTransaction(
            member_plan, path, adapter, id_factory=_Ids(), logger=lambda _r: None
        ).prepare()
        applied = RoleTransaction(
            member_plan, path, adapter, id_factory=_Ids(100), logger=lambda _r: None
        ).apply()
        _assert(applied.verdict == "CONFIRMED", "member fixture did not apply")
        adapter.failure = ("set_room_members", "before")
        adapter.failed_once = False
        compensated = RoleTransaction(
            member_plan, path, adapter, id_factory=_Ids(200), logger=lambda _r: None
        ).compensate()
        _assert(
            compensated.verdict == "PARTIAL", "failed compensation reported success"
        )
        row = compensated.manifest["steps"][0]
        _assert(
            row["state"] == "PARTIAL",
            "failed compensation did not preserve PARTIAL row",
        )
        _assert(
            prepared["steps"][0]["before"]["member_ids"] == ["a", "b", "z"],
            "full pre-state absent",
        )


def _case_irreversible_failure_stops_later_rooms() -> None:
    with tempfile.TemporaryDirectory(prefix="gb-role-tx-residue-") as tmp:
        path = pathlib.Path(tmp) / "manifest.json"
        plan = _fixture_plan(
            rooms=[
                {"logical_id": "one", "name": "One", "member_ids": ["a"]},
                {"logical_id": "two", "name": "Two", "member_ids": ["b", "c"]},
                {"logical_id": "three", "name": "Three", "member_ids": ["d"]},
            ]
        )
        adapter = _FakeAdapter()
        manifest = RoleTransaction(
            plan, path, adapter, id_factory=_Ids(), logger=lambda _r: None
        ).prepare()
        acks = {
            logical: room_acknowledgement(manifest, logical)
            for logical in ("one", "two", "three")
        }
        original_create = adapter.create_room
        calls = 0

        def fail_second(
            server_target: str,
            name: str,
            description: str,
            member_ids: Sequence[str],
            request_id: str,
        ) -> Mapping[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 2:
                adapter.calls.append(("create_room", request_id))
                adapter.rooms.append(
                    {
                        "server_id": server_target,
                        "name": name,
                        "description": description,
                        "member_ids": sorted(member_ids)[:1],
                    }
                )
                raise RuntimeError("offline second create returned deviant residue")
            return original_create(
                server_target, name, description, member_ids, request_id
            )

        adapter.create_room = fail_second  # type: ignore[method-assign]
        outcome = RoleTransaction(
            plan, path, adapter, id_factory=_Ids(100), logger=lambda _r: None
        ).apply(acks)
        _assert(
            outcome.verdict == "PARTIAL", "deviant irreversible create reported success"
        )
        _assert(
            [room["name"] for room in adapter.rooms] == ["One", "Two"],
            "later room was not stopped",
        )
        residue = outcome.manifest["residue"]
        _assert(
            [row["name"] for row in residue] == ["One", "Two"]
            and all(row["permanent"] for row in residue),
            "irreversible residue census is incomplete",
        )


SELFTEST_CASES: Mapping[str, Callable[[], None]] = {
    "apply-replay-compensation": _case_apply_replay_compensation,
    "interruptions-before-after-every-write": _case_interruptions,
    "journal-fsync-and-concurrency": _case_journal_and_concurrency,
    "prestate-plan-parent-refusals": _case_refusals_and_bindings,
    "per-room-ack-scope": _case_ack_scope_and_multi_room_residue,
    "partial-fullset-and-failed-compensation": _case_partial_and_failed_compensation,
    "irreversible-residue-stops-later": _case_irreversible_failure_stops_later_rooms,
}


def selftest(only: Optional[str] = None) -> int:
    """Run offline, isolated known-bads; ``only`` preserves exclusive causal injection."""
    if only is not None and only not in SELFTEST_CASES:
        print(
            json.dumps(
                {"event": "selftest_verdict", "verdict": "ERROR", "unknown_case": only}
            )
        )
        return EXIT_USAGE
    selected = [only] if only is not None else list(SELFTEST_CASES)
    failures: List[str] = []
    for name in selected:
        assert name is not None
        started = time.monotonic()
        try:
            SELFTEST_CASES[name]()
        except Exception as exc:
            failures.append(name)
            print(
                json.dumps(
                    {
                        "event": "selftest_case",
                        "case": name,
                        "verdict": "FAIL",
                        "error_type": type(exc).__name__,
                        "detail": str(exc),
                        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                    },
                    sort_keys=True,
                )
            )
        else:
            print(
                json.dumps(
                    {
                        "event": "selftest_case",
                        "case": name,
                        "verdict": "PASS",
                        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                    },
                    sort_keys=True,
                )
            )
    print(
        json.dumps(
            {
                "event": "selftest_verdict",
                "schema": SCHEMA,
                "verdict": "FAIL" if failures else "PASS",
                "passed": len(selected) - len(failures),
                "total": len(selected),
                "failed_cases": failures,
                "offline": True,
                "exclusive_case": only,
            },
            sort_keys=True,
        )
    )
    return EXIT_FINDINGS if failures else EXIT_OK


def inspect_manifest(path: pathlib.Path) -> JsonObject:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ManifestError(f"{path} is not a {SCHEMA} manifest")
    claimed = doc.get("journal_hash")
    actual = _manifest_digest(doc)
    if not isinstance(claimed, str) or not hmac.compare_digest(claimed, actual):
        raise ManifestError(f"manifest integrity mismatch: {path}")
    return {
        "schema": SCHEMA,
        "manifest": str(path),
        "manifest_hash": claimed,
        "transaction_id": doc.get("transaction_id"),
        "parent_correlation_id": doc.get("parent_correlation_id"),
        "plan_hash": doc.get("plan_hash"),
        "account_hash": doc.get("account_hash"),
        "state": doc.get("state"),
        "resume_cursor": doc.get("resume_cursor"),
        "steps": [
            {
                "step_id": row.get("step_id"),
                "state": row.get("state"),
                "logical_id": row.get("logical_target"),
                "server_id": row.get("server_target"),
                "reversibility": row.get("reversibility"),
            }
            for row in doc.get("steps", [])
        ],
        "residue": doc.get("residue", []),
        "recovery_argv": doc.get("recovery_argv"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--selftest", action="store_true", help="run offline isolated failure fixtures"
    )
    mode.add_argument(
        "--list-selftests", action="store_true", help="list exact fixture names"
    )
    mode.add_argument(
        "--inspect",
        metavar="MANIFEST",
        help="validate and summarize a manifest offline",
    )
    parser.add_argument(
        "--selftest-case",
        choices=sorted(SELFTEST_CASES),
        help="with --selftest, run exactly one causal failure fixture",
    )
    args = parser.parse_args(argv)
    if args.selftest_case and not args.selftest:
        parser.error("--selftest-case requires --selftest")
    if args.list_selftests:
        print(
            json.dumps(
                {"schema": SCHEMA, "selftests": sorted(SELFTEST_CASES)}, sort_keys=True
            )
        )
        return EXIT_OK
    if args.selftest:
        return selftest(args.selftest_case)
    try:
        print(
            json.dumps(
                inspect_manifest(pathlib.Path(args.inspect)), indent=1, sort_keys=True
            )
        )
    except ManifestError as exc:
        print(
            json.dumps(
                {"schema": SCHEMA, "verdict": "ERROR", "detail": str(exc)},
                sort_keys=True,
            )
        )
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
