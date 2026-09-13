#!/usr/bin/env python3
"""gb-rebuild-fleet — create Bots from fleet-spec.json, reversibly, the way the product actually works.

THE MECHANISM, measured 2026-09-11 (do not "simplify" this back to one call):
  CreateGrokBotAgent alone registers a durable identity and NOTHING ELSE. The desktop never shows
  that Bot, on any machine, across restarts — no agent store is ever built for it. Ten Bots were
  created that way and had to be rolled back.
  CreateGrokBotAgentFromTemplate returns a blobGetUrl, the client materialises the agent store,
  and the Bot appears. It is the only creation path that produces a usable Bot.
So every Bot here is: instantiate from the seed template, then immediately UpdateGrokBotAgent with
the reviewed name / title / description / avatar. No seed text survives the second call.

The seed is declared in fleet-spec.json. It is currently a public marketplace template because the
API's own CreateGrokBotTemplate requires a hand-authored profile blob whose schema we have not
decoded ("template profile is not readable"). One UI action — Bot -> Share as template — makes the
client author a valid blob; swap that share_id into fleet-spec.json and the seed becomes ours.

What a rebuild carries and what it does NOT:
  carries      name, title, avatar, description
  does NOT     conversation history, learned memory, per-Bot skills, connector logins
  routines     NOT carried by the create, and NOT manual either: no create RPC exists, but a
               Bot asked in ONE chat turn schedules itself. `gb templates deploy <id> --apply`
               does both halves and CONFIRMS the routine by reading it back from the server.
               This line said "rebuild them in the UI" until 2026-09-12, and the same false
               sentence printed directly above a "ROUTINE CONFIRMED" line in one run's output
               — the tool contradicting itself on screen, seconds apart, in one terminal.
  DELETE       measured 2026-09-12: `DeleteGrokBotAgent` removes the Bot and NOT its routine
               record. After two rollbacks `ListGrokBotAgentAutomations` still returned 200
               with one automation per deleted uuid, while `ListGrokBotAgents` no longer
               carried the name. So "the manifest deletes exactly what that run created" is
               true of the BOT and false of the ROUTINE, and a routine read is never proof
               that its Bot exists. Whether an orphaned routine can still fire is UNKNOWN:
               nothing was observed either way and nothing is claimed either way.

  gb-rebuild-fleet.py --plan
  gb-rebuild-fleet.py --apply [--only NAME]
  gb-rebuild-fleet.py --rollback <manifest>             # read-only plan
  gb-rebuild-fleet.py --rollback <manifest> --apply --yes
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import shlex
import sys
import tempfile
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_json, durable_transition, main as typed_main  # noqa: E402


SCHEMA = "gb-fleet-recovery/2"
TOOL = "bin/gb-rebuild-fleet.py"
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_REFUSED = 5

Rpc = Callable[[str, str, Dict[str, Any]], Tuple[int, Any]]
Clock = Callable[[], dt.datetime]
IdFactory = Callable[[], str]
Injector = Callable[[str, Dict[str, Any], pathlib.Path], None]


class InjectedInterruption(Exception):
    """Offline selftest interruption, raised only after a post-write receipt is durable."""


def _load() -> Any:
    """Import the one shared credential and RPC transport."""
    import gbrpc

    return gbrpc


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _noop_inject(_point: str, _manifest: Dict[str, Any], _path: pathlib.Path) -> None:
    return None


def _iso(clock: Clock) -> str:
    return clock().astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _emit(event: str, **fields: Any) -> None:
    """Structured audit output. Callers pass digests, never profile or secret bodies."""
    print(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


def _recovery_argv(path: pathlib.Path, root: pathlib.Path) -> List[str]:
    try:
        named = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        named = str(path.resolve())
    return [TOOL, "--rollback", named, "--apply", "--yes"]


def _persist(
    manifest: Dict[str, Any],
    path: pathlib.Path,
    clock: Clock,
    event: str,
    **fields: Any,
) -> None:
    """Append one transition and durably publish the complete recovery manifest."""
    seq = int(manifest.get("transition_seq") or 0) + 1
    when = _iso(clock)
    manifest["transition_seq"] = seq
    manifest["updated_at"] = when
    manifest.setdefault("transitions", []).append(
        {"seq": seq, "at": when, "event": event, **fields}
    )
    atomic_write_json(path, manifest)
    _emit(
        "manifest_fsync",
        path=str(path),
        transition=seq,
        transition_event=event,
        status=manifest.get("status"),
        recovery_argv=manifest.get("recovery_argv"),
    )


def _alias(row: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _read_roster(
    read_rpc: Rpc, token: str, context: str
) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """Read and strictly classify the authoritative server roster."""
    status, response = read_rpc(token, "ListGrokBotAgents", {})
    if status != 200:
        detail = f"http-{status}"
        _emit("roster_read", context=context, classification="UNREADABLE", http=status)
        return None, detail
    if not isinstance(response, dict) or not isinstance(response.get("agents"), list):
        _emit("roster_read", context=context, classification="MALFORMED", http=status)
        return None, "response must contain an agents list"

    roster: List[Dict[str, Any]] = []
    seen_names, seen_ids, seen_uuids = set(), set(), set()
    for index, raw in enumerate(response["agents"]):
        if not isinstance(raw, dict):
            _emit("roster_read", context=context, classification="MALFORMED", row=index)
            return None, f"agents[{index}] is not an object"
        name = raw.get("name")
        agent_uuid = raw.get("agentId")
        numeric_raw = raw.get("id")
        try:
            numeric_id = int(numeric_raw)
        except (TypeError, ValueError):
            numeric_id = 0
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(agent_uuid, str)
            or not agent_uuid
            or numeric_id <= 0
        ):
            _emit("roster_read", context=context, classification="MALFORMED", row=index)
            return None, f"agents[{index}] lacks name, numeric id, or UUID"
        # Name collisions are real (unshaped seed clones share "Template").
        # Authoritative identity is numeric id / UUID only.
        if numeric_id in seen_ids or agent_uuid in seen_uuids:
            _emit("roster_read", context=context, classification="MALFORMED", row=index)
            return None, f"agents[{index}] duplicates an authoritative identity"
        seen_names.add(name)
        seen_ids.add(numeric_id)
        seen_uuids.add(agent_uuid)
        roster.append(
            {
                "name": name,
                "numeric_id": numeric_id,
                "uuid": agent_uuid,
                "title": _alias(raw, "title"),
                "description": _alias(raw, "description"),
                "avatar_shape": _alias(raw, "avatarShape", "avatar_shape"),
                "avatar_color": _alias(raw, "avatarColor", "avatar_color"),
            }
        )
    _emit(
        "roster_read",
        context=context,
        classification="AUTHORITATIVE",
        http=200,
        rows=len(roster),
    )
    return roster, ""


def _expected(bot: Dict[str, Any]) -> Dict[str, str]:
    return {
        "name": str(bot["name"]),
        "title": str(bot.get("title") or ""),
        "description": str(bot["description"]),
        "avatar_shape": str(bot.get("avatar_shape") or "hex"),
        "avatar_color": str(bot.get("avatar_color") or "gray"),
    }


def _verify_bot(
    bot: Dict[str, Any],
    roster: Sequence[Dict[str, Any]],
    expected_uuid: Optional[str],
    manifest: Dict[str, Any],
    path: pathlib.Path,
    clock: Clock,
    context: str,
) -> bool:
    expected = _expected(bot)
    candidates = [
        row
        for row in roster
        if (expected_uuid and row["uuid"] == expected_uuid)
        or (not expected_uuid and row["name"] == expected["name"])
    ]
    mismatches: List[str] = []
    actual: Optional[Dict[str, Any]] = candidates[0] if len(candidates) == 1 else None
    if actual is None:
        mismatches.append(
            "identity_missing" if not candidates else "identity_ambiguous"
        )
    else:
        for field in ("name", "title", "description", "avatar_shape", "avatar_color"):
            if actual.get(field) != expected[field]:
                mismatches.append(field)
        if expected_uuid and actual["uuid"] != expected_uuid:
            mismatches.append("uuid")
    receipt = {
        "context": context,
        "expected_name": expected["name"],
        "authoritative_name": actual.get("name") if actual else None,
        "expected_uuid": expected_uuid,
        "authoritative_uuid": actual.get("uuid") if actual else None,
        "authoritative_numeric_id": actual.get("numeric_id") if actual else None,
        "expected_title": expected["title"],
        "authoritative_title": actual.get("title") if actual else None,
        "expected_description_sha256": _digest(expected["description"]),
        "authoritative_description_sha256": (
            _digest(actual.get("description")) if actual else None
        ),
        "expected_avatar_shape": expected["avatar_shape"],
        "authoritative_avatar_shape": actual.get("avatar_shape") if actual else None,
        "expected_avatar_color": expected["avatar_color"],
        "authoritative_avatar_color": actual.get("avatar_color") if actual else None,
        "mismatches": mismatches,
        "confirmed": not mismatches,
    }
    manifest.setdefault("readbacks", []).append(receipt)
    _persist(manifest, path, clock, "authoritative_field_readback", **receipt)
    _emit(
        "authoritative_readback",
        context=context,
        expected_name=expected["name"],
        authoritative_name=receipt["authoritative_name"],
        uuid=receipt["authoritative_uuid"],
        numeric_id=receipt["authoritative_numeric_id"],
        description_sha256=receipt["authoritative_description_sha256"],
        mismatches=mismatches,
        confirmed=not mismatches,
    )
    return not mismatches


def _mutate(
    *,
    action: str,
    method: str,
    body: Dict[str, Any],
    bot_name: str,
    manifest: Dict[str, Any],
    path: pathlib.Path,
    clock: Clock,
    id_factory: IdFactory,
    inject: Injector,
    write_rpc: Rpc,
    token: str,
    capture: Optional[Callable[[int, Any], Dict[str, Any]]] = None,
) -> Tuple[int, Any, str]:
    """Persist intent, perform one account write, persist result, then expose interruption."""
    request_id = id_factory()
    request_sha = _digest(body)
    _persist(
        manifest,
        path,
        clock,
        f"{action}.before",
        action=action,
        method=method,
        bot=bot_name,
        request_id=request_id,
        request_sha256=request_sha,
    )
    _emit(
        "mutation_request",
        action=action,
        method=method,
        bot=bot_name,
        request_id=request_id,
    )
    with durable_transition():
        status, response = write_rpc(token, method, body)
        extra = capture(status, response) if capture else {}
        _persist(
            manifest,
            path,
            clock,
            f"{action}.after",
            action=action,
            method=method,
            bot=bot_name,
            request_id=request_id,
            http=status,
            response_sha256=_digest(response),
            response_preview=(str(response)[:240] if status != 200 else None),
            **extra,
        )
    _emit(
        "mutation_result",
        action=action,
        bot=bot_name,
        request_id=request_id,
        http=status,
    )
    point = f"after_{action}"
    _emit(
        "interruption_point",
        point=point,
        request_id=request_id,
        recovery_argv=manifest.get("recovery_argv"),
    )
    inject(point, manifest, path)
    return status, response, request_id


def _automation_inventory(
    read_rpc: Rpc, token: str, agent_uuid: str, context: str
) -> Dict[str, Any]:
    status, response = read_rpc(
        token, "ListGrokBotAgentAutomations", {"agent_id": agent_uuid}
    )
    if status != 200:
        result = {"state": "UNKNOWN", "http": status, "ids": []}
    elif not isinstance(response, dict) or not isinstance(
        response.get("automations"), list
    ):
        result = {"state": "UNKNOWN", "http": status, "ids": []}
    else:
        ids: List[str] = []
        for automation in response["automations"]:
            if not isinstance(automation, dict):
                result = {"state": "UNKNOWN", "http": status, "ids": ids}
                break
            identity = automation.get("automationId") or automation.get("id")
            if not identity and isinstance(automation.get("recordJson"), str):
                try:
                    record = json.loads(automation["recordJson"])
                except ValueError:
                    record = {}
                if isinstance(record, dict):
                    identity = record.get("automationId") or record.get("id")
            ids.append(
                str(identity) if identity else f"sha256:{_digest(automation)[:16]}"
            )
        else:
            result = {
                "state": "RESIDUE" if ids else "ABSENT",
                "http": status,
                "ids": ids,
            }
    _emit(
        "automation_residue",
        context=context,
        uuid=agent_uuid,
        classification=result["state"],
        http=result["http"],
        automation_ids=result["ids"],
    )
    return result


def _delete_and_check(
    *,
    action: str,
    row: Dict[str, Any],
    manifest: Dict[str, Any],
    path: pathlib.Path,
    clock: Clock,
    id_factory: IdFactory,
    inject: Injector,
    read_rpc: Rpc,
    write_rpc: Rpc,
    token: str,
) -> bool:
    numeric_id = row.get("new_numeric_id")
    agent_uuid = str(row.get("new_uuid") or row.get("requested_uuid") or "")
    rpc_id = row.get("new_rpc_id")
    if not rpc_id:
        if isinstance(numeric_id, int) and numeric_id > 0:
            rpc_id = str(numeric_id)
        elif isinstance(numeric_id, str) and numeric_id.strip():
            rpc_id = numeric_id.strip()
    if not rpc_id:
        row["state"] = "DELETE_UNRESOLVED_ID"
        _persist(
            manifest,
            path,
            clock,
            "delete.unresolved",
            action=action,
            bot=row.get("name"),
            uuid=agent_uuid or None,
        )
        return False

    def capture(status: int, _response: Any) -> Dict[str, Any]:
        row["delete_http"] = status
        row["state"] = "DELETE_REPLIED" if status == 200 else "DELETE_FAILED"
        return {"numeric_id": numeric_id, "uuid": agent_uuid or None}

    status, _response, request_id = _mutate(
        action=action,
        method="DeleteGrokBotAgent",
        body={"id": str(rpc_id)},
        bot_name=str(row.get("name") or "?"),
        manifest=manifest,
        path=path,
        clock=clock,
        id_factory=id_factory,
        inject=inject,
        write_rpc=write_rpc,
        token=token,
        capture=capture,
    )
    roster, roster_error = _read_roster(read_rpc, token, f"{action}.absence")
    matches = (
        []
        if roster is None
        else [
            agent
            for agent in roster
            if agent["numeric_id"] == numeric_id
            or (agent_uuid and agent["uuid"] == agent_uuid)
        ]
    )
    absence = roster is not None and not matches
    deletion = {
        "action": action,
        "name": row.get("name"),
        "numeric_id": numeric_id,
        "uuid": agent_uuid or None,
        "request_id": request_id,
        "delete_http": status,
        "absence": "CONFIRMED"
        if absence
        else ("UNKNOWN" if roster is None else "STILL_PRESENT"),
        "roster_error": roster_error or None,
        "present_ids": [agent["numeric_id"] for agent in matches],
    }
    manifest.setdefault("deletions", []).append(deletion)
    _persist(manifest, path, clock, "delete.absence_readback", **deletion)
    _emit("delete_absence", **deletion)

    automation = (
        _automation_inventory(read_rpc, token, agent_uuid, action)
        if agent_uuid
        else {"state": "UNKNOWN", "http": 0, "ids": []}
    )
    automation_receipt = {
        "action": action,
        "name": row.get("name"),
        "uuid": agent_uuid or None,
        "classification": automation["state"],
        "http": automation["http"],
        "automation_ids": automation["ids"],
    }
    manifest.setdefault("automation_checks", []).append(automation_receipt)
    _persist(manifest, path, clock, "delete.automation_readback", **automation_receipt)
    clean = status == 200 and absence and automation["state"] == "ABSENT"
    if clean:
        row["state"] = "DELETED_CONFIRMED"
    elif automation["state"] == "RESIDUE":
        row["state"] = "ORPHAN_AUTOMATION_RESIDUE"
    else:
        row["state"] = "DELETE_PARTIAL_UNKNOWN"
    _persist(
        manifest,
        path,
        clock,
        "delete.verdict",
        name=row.get("name"),
        clean=clean,
        state=row["state"],
    )
    return clean


def _new_manifest(
    *,
    rows: Sequence[Dict[str, Any]],
    seed: str,
    spec_path: pathlib.Path,
    spec_version: Any,
    path: pathlib.Path,
    root: pathlib.Path,
    clock: Clock,
    run_id: str,
) -> Dict[str, Any]:
    requested: List[Dict[str, Any]] = []
    for bot in rows:
        expected = _expected(bot)
        requested.append(
            {
                "name": expected["name"],
                "title": expected["title"],
                "description_sha256": _digest(expected["description"]),
                "avatar_shape": expected["avatar_shape"],
                "avatar_color": expected["avatar_color"],
            }
        )
    return {
        "schema": SCHEMA,
        "tool": TOOL,
        "run_id": run_id,
        "manifest_relpath": str(path.resolve().relative_to(root.resolve())),
        "created_at": _iso(clock),
        "updated_at": _iso(clock),
        "status": "PREPARED",
        "transition_seq": 0,
        "spec": {
            "path": str(spec_path),
            "version": spec_version,
            "sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        },
        "seed_share_id_sha256": _digest(seed),
        "requested": requested,
        "created": [],
        "deletions": [],
        "automation_checks": [],
        "readbacks": [],
        "failures": [],
        "transitions": [],
        "recovery_argv": _recovery_argv(path, root),
    }


def apply_fleet(
    *,
    rows: Sequence[Dict[str, Any]],
    seed: str,
    spec_path: pathlib.Path,
    spec_version: Any,
    manifest_path: pathlib.Path,
    root: pathlib.Path,
    read_rpc: Rpc,
    write_rpc: Rpc,
    token: str,
    clock: Clock = _utcnow,
    id_factory: IdFactory = _new_id,
    inject: Injector = _noop_inject,
) -> int:
    initial, error = _read_roster(read_rpc, token, "apply.initial")
    if initial is None:
        _emit(
            "fleet_verdict", verdict="ERROR", exit=EXIT_FINDINGS, reason=error, writes=0
        )
        return EXIT_FINDINGS

    run_id = id_factory()
    manifest = _new_manifest(
        rows=rows,
        seed=seed,
        spec_path=spec_path,
        spec_version=spec_version,
        path=manifest_path,
        root=root,
        clock=clock,
        run_id=run_id,
    )
    _persist(
        manifest, manifest_path, clock, "manifest.prepared", roster_rows=len(initial)
    )
    present = {agent["name"] for agent in initial}
    if present:
        print(f"already present, verifying: {', '.join(sorted(present))}")

    failed = False
    created_by_name: Dict[str, Dict[str, Any]] = {}
    for bot in rows:
        expected = _expected(bot)
        if expected["name"] in present:
            continue
        requested_uuid = id_factory()
        created = {
            "name": expected["name"],
            "requested_uuid": requested_uuid,
            "new_numeric_id": None,
            "new_uuid": requested_uuid,
            "source_uuid": bot.get("source_uuid"),
            "harness": None,
            "state": "CREATE_PENDING",
        }
        manifest["created"].append(created)
        created_by_name[expected["name"]] = created

        def capture_create(
            status: int, response: Any, row: Dict[str, Any] = created
        ) -> Dict[str, Any]:
            agent = (
                response.get("agent")
                if status == 200 and isinstance(response, dict)
                else None
            )
            numeric = agent.get("id") if isinstance(agent, dict) else None
            row["new_rpc_id"] = str(numeric) if numeric is not None and str(numeric) != "" else None
            try:
                numeric_id = int(str(numeric))
            except (TypeError, ValueError):
                numeric_id = 0
            actual_uuid = agent.get("agentId") if isinstance(agent, dict) else None
            row["new_numeric_id"] = numeric_id or None
            if isinstance(actual_uuid, str) and actual_uuid:
                row["new_uuid"] = actual_uuid
            row["harness"] = agent.get("harness") if isinstance(agent, dict) else None
            complete = bool(numeric_id and isinstance(actual_uuid, str) and actual_uuid)
            row["state"] = (
                "INSTANTIATED" if status == 200 and complete else "CREATE_FAILED"
            )
            return {
                "numeric_id": row["new_numeric_id"],
                "uuid": row["new_uuid"],
                "identity_complete": complete,
            }

        create_status, _create_response, _create_request = _mutate(
            action="create",
            method="CreateGrokBotAgentFromTemplate",
            body={"share_id": seed, "agent_id": requested_uuid},
            bot_name=expected["name"],
            manifest=manifest,
            path=manifest_path,
            clock=clock,
            id_factory=id_factory,
            inject=inject,
            write_rpc=write_rpc,
            token=token,
            capture=capture_create,
        )
        create_ok = (
            create_status == 200
            and isinstance(created.get("new_numeric_id"), int)
            and created.get("new_uuid") == requested_uuid
        )
        if not create_ok:
            manifest["failures"].append(
                {"name": expected["name"], "phase": "create", "http": create_status}
            )
            if isinstance(created.get("new_numeric_id"), int):
                _delete_and_check(
                    action="compensation_delete",
                    row=created,
                    manifest=manifest,
                    path=manifest_path,
                    clock=clock,
                    id_factory=id_factory,
                    inject=inject,
                    read_rpc=read_rpc,
                    write_rpc=write_rpc,
                    token=token,
                )
            failed = True
            break

        def capture_update(
            status: int, _response: Any, row: Dict[str, Any] = created
        ) -> Dict[str, Any]:
            row["state"] = "SHAPED_RESPONSE" if status == 200 else "SHAPE_FAILED"
            return {"numeric_id": row["new_numeric_id"], "uuid": row["new_uuid"]}

        update_status, _update_response, _update_request = _mutate(
            action="update",
            method="UpdateGrokBotAgent",
            body={
                "id": str(created.get("new_rpc_id") or created["new_numeric_id"]),
                "name": expected["name"],
                "title": expected["title"],
                "description": expected["description"],
                "avatar_shape": expected["avatar_shape"],
                "avatar_color": expected["avatar_color"],
            },
            bot_name=expected["name"],
            manifest=manifest,
            path=manifest_path,
            clock=clock,
            id_factory=id_factory,
            inject=inject,
            write_rpc=write_rpc,
            token=token,
            capture=capture_update,
        )
        if update_status != 200:
            manifest["failures"].append(
                {"name": expected["name"], "phase": "update", "http": update_status}
            )
            _delete_and_check(
                action="compensation_delete",
                row=created,
                manifest=manifest,
                path=manifest_path,
                clock=clock,
                id_factory=id_factory,
                inject=inject,
                read_rpc=read_rpc,
                write_rpc=write_rpc,
                token=token,
            )
            failed = True
            break

        fresh, fresh_error = _read_roster(read_rpc, token, "apply.after_update")
        if fresh is None:
            created["state"] = "SHAPE_READBACK_UNKNOWN"
            manifest["failures"].append(
                {"name": expected["name"], "phase": "readback", "reason": fresh_error}
            )
            _persist(
                manifest,
                manifest_path,
                clock,
                "shape.readback_unknown",
                name=expected["name"],
                reason=fresh_error,
            )
            failed = True
            break
        if not _verify_bot(
            bot,
            fresh,
            str(created["new_uuid"]),
            manifest,
            manifest_path,
            clock,
            "apply.after_update",
        ):
            manifest["failures"].append(
                {"name": expected["name"], "phase": "shape_mismatch"}
            )
            _delete_and_check(
                action="compensation_delete",
                row=created,
                manifest=manifest,
                path=manifest_path,
                clock=clock,
                id_factory=id_factory,
                inject=inject,
                read_rpc=read_rpc,
                write_rpc=write_rpc,
                token=token,
            )
            failed = True
            break
        created["state"] = "CONFIRMED"
        _persist(
            manifest,
            manifest_path,
            clock,
            "shape.confirmed",
            name=expected["name"],
            uuid=created["new_uuid"],
        )
        print(
            f"created {expected['name']:<20} id={created['new_numeric_id']:<9} "
            f"description_sha256={_digest(expected['description'])}"
        )

    if not failed:
        final, final_error = _read_roster(read_rpc, token, "apply.final")
        if final is None:
            manifest["failures"].append(
                {"phase": "final_roster", "reason": final_error}
            )
            failed = True
        else:
            for bot in rows:
                row = created_by_name.get(str(bot["name"]))
                expected_uuid = str(row["new_uuid"]) if row else None
                if not _verify_bot(
                    bot,
                    final,
                    expected_uuid,
                    manifest,
                    manifest_path,
                    clock,
                    "apply.final",
                ):
                    failed = True

    manifest["status"] = (
        "PARTIAL"
        if failed and manifest["created"]
        else ("FAILED" if failed else "APPLIED")
    )
    exit_code = EXIT_FINDINGS if failed else 0
    _persist(
        manifest,
        manifest_path,
        clock,
        "apply.verdict",
        verdict=manifest["status"],
        exit=exit_code,
    )
    recovery = manifest["recovery_argv"]
    _emit(
        "fleet_verdict",
        verdict=manifest["status"],
        exit=exit_code,
        recovery_argv=recovery,
    )
    print(f"manifest {manifest_path}")
    print(f"recovery with: {shlex.join(recovery)}")
    return exit_code


def _validate_manifest(path: pathlib.Path, root: pathlib.Path) -> Dict[str, Any]:
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"manifest is unreadable: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != SCHEMA
        or manifest.get("tool") != TOOL
    ):
        raise ValueError(f"manifest must be {SCHEMA} written by {TOOL}")
    run_id = manifest.get("run_id")
    relpath = manifest.get("manifest_relpath")
    created = manifest.get("created")
    if (
        not isinstance(run_id, str)
        or not run_id
        or not isinstance(relpath, str)
        or not isinstance(created, list)
    ):
        raise ValueError("manifest lacks its run binding or created identity list")
    try:
        canonical_run_id = str(uuid.UUID(run_id))
    except ValueError as exc:
        raise ValueError("manifest run_id is not a UUID") from exc
    if canonical_run_id != run_id:
        raise ValueError("manifest run_id is not canonical")
    expected_path = (root / relpath).resolve()
    if not expected_path.name.endswith(f"-{run_id[:8]}.json"):
        raise ValueError("manifest filename is not bound to its run_id")
    if (
        path.resolve() != expected_path
        or expected_path.parent != (root / "rebuild").resolve()
    ):
        raise ValueError("manifest path is not bound to this tool's rebuild directory")
    if manifest.get("recovery_argv") != _recovery_argv(path, root):
        raise ValueError("manifest recovery argv does not match its bound path")
    seen = set()
    for index, row in enumerate(created):
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError(f"manifest created[{index}] is malformed")
        agent_uuid = row.get("new_uuid") or row.get("requested_uuid")
        if not isinstance(agent_uuid, str) or not agent_uuid or agent_uuid in seen:
            raise ValueError(f"manifest created[{index}] lacks a unique UUID")
        numeric = row.get("new_numeric_id")
        if numeric is not None and (not isinstance(numeric, int) or numeric <= 0):
            raise ValueError(f"manifest created[{index}] has an invalid numeric id")
        seen.add(agent_uuid)
    return manifest


def rollback_fleet(
    *,
    manifest: Dict[str, Any],
    manifest_path: pathlib.Path,
    read_rpc: Rpc,
    write_rpc: Rpc,
    token: str,
    clock: Clock = _utcnow,
    id_factory: IdFactory = _new_id,
    inject: Injector = _noop_inject,
) -> int:
    roster, error = _read_roster(read_rpc, token, "rollback.initial")
    if roster is None:
        manifest["status"] = "ROLLBACK_BLOCKED"
        manifest.setdefault("failures", []).append(
            {"phase": "rollback_initial_roster", "reason": error}
        )
        _persist(
            manifest, manifest_path, clock, "rollback.blocked", reason=error, writes=0
        )
        _emit(
            "fleet_verdict",
            verdict="ROLLBACK_BLOCKED",
            exit=EXIT_FINDINGS,
            recovery_argv=manifest["recovery_argv"],
        )
        return EXIT_FINDINGS

    manifest["status"] = "ROLLING_BACK"
    _persist(
        manifest, manifest_path, clock, "rollback.prepared", roster_rows=len(roster)
    )
    failed = False
    for row_index, row in enumerate(manifest["created"]):
        known_uuids = {
            str(value)
            for value in (row.get("new_uuid"), row.get("requested_uuid"))
            if value
        }
        numeric = row.get("new_numeric_id")
        matches = [
            agent
            for agent in roster
            if agent["uuid"] in known_uuids
            or (isinstance(numeric, int) and agent["numeric_id"] == numeric)
        ]
        if len(matches) > 1:
            row["state"] = "ROLLBACK_IDENTITY_AMBIGUOUS"
            _persist(
                manifest,
                manifest_path,
                clock,
                "rollback.identity_ambiguous",
                name=row["name"],
            )
            failed = True
            continue
        if not matches:
            agent_uuid = str(row.get("new_uuid") or row.get("requested_uuid") or "")
            automation = (
                _automation_inventory(
                    read_rpc, token, agent_uuid, "rollback.already_absent"
                )
                if agent_uuid
                else {"state": "UNKNOWN", "http": 0, "ids": []}
            )
            receipt = {
                "action": "rollback_already_absent",
                "name": row["name"],
                "uuid": agent_uuid or None,
                "classification": automation["state"],
                "http": automation["http"],
                "automation_ids": automation["ids"],
            }
            manifest.setdefault("automation_checks", []).append(receipt)
            row["state"] = (
                "ALREADY_ABSENT"
                if automation["state"] == "ABSENT"
                else "ORPHAN_AUTOMATION_RESIDUE"
            )
            _persist(
                manifest, manifest_path, clock, "rollback.already_absent", **receipt
            )
            if automation["state"] != "ABSENT":
                failed = True
            continue

        target = matches[0]
        # Name is not identity. An unshaped seed clone keeps the seed name
        # ("Template") while the manifest still names the intended Bot.
        identity_disagrees = (
            (bool(known_uuids) and target["uuid"] not in known_uuids)
            or (isinstance(numeric, int) and target["numeric_id"] != numeric)
        )
        if identity_disagrees:
            row["state"] = "ROLLBACK_IDENTITY_MISMATCH"
            _persist(
                manifest,
                manifest_path,
                clock,
                "rollback.identity_mismatch",
                expected_name=row["name"],
                expected_uuids=sorted(known_uuids),
                expected_numeric_id=numeric,
                authoritative_name=target["name"],
                authoritative_uuid=target["uuid"],
                authoritative_numeric_id=target["numeric_id"],
            )
            failed = True
            continue
        row["new_numeric_id"] = target["numeric_id"]
        row["new_uuid"] = target["uuid"]
        _persist(
            manifest,
            manifest_path,
            clock,
            "rollback.identity_resolved",
            name=row["name"],
            uuid=target["uuid"],
            numeric_id=target["numeric_id"],
        )
        clean = _delete_and_check(
            action="rollback_delete",
            row=row,
            manifest=manifest,
            path=manifest_path,
            clock=clock,
            id_factory=id_factory,
            inject=inject,
            read_rpc=read_rpc,
            write_rpc=write_rpc,
            token=token,
        )
        if not clean:
            failed = True
        fresh, fresh_error = _read_roster(read_rpc, token, "rollback.next")
        if fresh is None:
            manifest.setdefault("failures", []).append(
                {"phase": "rollback_refresh", "reason": fresh_error}
            )
            failed = True
            for pending in manifest["created"][row_index + 1 :]:
                pending["state"] = "ROLLBACK_NOT_ATTEMPTED_ROSTER_UNREADABLE"
                _persist(
                    manifest,
                    manifest_path,
                    clock,
                    "rollback.not_attempted",
                    name=pending.get("name"),
                    reason=fresh_error,
                )
            break
        roster = fresh

    manifest["status"] = "ROLLBACK_PARTIAL" if failed else "ROLLED_BACK"
    exit_code = EXIT_FINDINGS if failed else 0
    _persist(
        manifest,
        manifest_path,
        clock,
        "rollback.verdict",
        verdict=manifest["status"],
        exit=exit_code,
    )
    _emit(
        "fleet_verdict",
        verdict=manifest["status"],
        exit=exit_code,
        recovery_argv=manifest["recovery_argv"],
    )
    print(f"manifest {manifest_path}")
    print(f"recovery with: {shlex.join(manifest['recovery_argv'])}")
    return exit_code


class ScriptedRpc:
    """Deterministic offline transport for failure fixtures; any unscripted call fails."""

    def __init__(
        self,
        reads: Dict[str, List[Tuple[int, Any]]],
        writes: Dict[str, List[Tuple[int, Any]]],
        before_write: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.reads = {name: list(values) for name, values in reads.items()}
        self.writes = {name: list(values) for name, values in writes.items()}
        self.before_write = before_write
        self.write_calls: List[str] = []

    def read(self, _token: str, method: str, _body: Dict[str, Any]) -> Tuple[int, Any]:
        scripted = self.reads.get(method) or []
        if not scripted:
            raise AssertionError(f"unscripted read {method}")
        return scripted.pop(0)

    def write(self, _token: str, method: str, _body: Dict[str, Any]) -> Tuple[int, Any]:
        if self.before_write:
            self.before_write(method)
        self.write_calls.append(method)
        scripted = self.writes.get(method) or []
        if not scripted:
            raise AssertionError(f"unscripted write {method}")
        return scripted.pop(0)


def _agent(
    *,
    name: str = "Probe",
    numeric_id: int = 101,
    agent_uuid: str = "00000000-0000-0000-0000-000000000101",
    title: str = "Probe title",
    description: str = "Probe charter",
    avatar_shape: str = "hex",
    avatar_color: str = "blue",
) -> Dict[str, Any]:
    return {
        "id": numeric_id,
        "agentId": agent_uuid,
        "name": name,
        "title": title,
        "description": description,
        "avatarShape": avatar_shape,
        "avatarColor": avatar_color,
        "harness": "box",
    }


def selftest() -> int:
    """Known-bad failure legs. No credential, network, or live account is reachable."""
    failures: List[str] = []
    checks = 0
    fixed = dt.datetime(2026, 9, 12, tzinfo=dt.timezone.utc)
    bot = {
        "name": "Probe",
        "title": "Probe title",
        "description": "Probe charter",
        "avatar_shape": "hex",
        "avatar_color": "blue",
        "source_uuid": None,
    }

    def check(ok: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            failures.append(label)

    def ids() -> IdFactory:
        values = iter(f"00000000-0000-0000-0000-{n:012d}" for n in range(1, 100))
        return lambda: next(values)

    def manifest_doc(
        path: pathlib.Path, root: pathlib.Path, state: str = "CONFIRMED"
    ) -> Dict[str, Any]:
        doc = {
            "schema": SCHEMA,
            "tool": TOOL,
            "run_id": "00000000-0000-0000-0000-000000000001",
            "manifest_relpath": str(path.resolve().relative_to(root.resolve())),
            "created_at": fixed.isoformat(),
            "updated_at": fixed.isoformat(),
            "status": "APPLIED",
            "transition_seq": 0,
            "created": [
                {
                    "name": "Probe",
                    "requested_uuid": "00000000-0000-0000-0000-000000000101",
                    "new_numeric_id": 101,
                    "new_uuid": "00000000-0000-0000-0000-000000000101",
                    "state": state,
                }
            ],
            "deletions": [],
            "automation_checks": [],
            "readbacks": [],
            "failures": [],
            "transitions": [],
            "recovery_argv": _recovery_argv(path, root),
        }
        atomic_write_json(path, doc)
        return doc

    with tempfile.TemporaryDirectory(prefix="gb-fleet-selftest-") as tmp:
        root = pathlib.Path(tmp)
        rebuild = root / "rebuild"
        rebuild.mkdir()
        spec_path = root / "spec.json"
        atomic_write_json(
            spec_path, {"version": 1, "seed": {"share_id": "seed"}, "bots": [bot]}
        )
        clock = lambda: fixed

        # 1 Principal guard: unreadable and malformed authoritative rosters permit zero writes.
        for label, roster_reply in (
            ("unreadable", (503, "down")),
            ("malformed", (200, {"agents": "not-a-list"})),
        ):
            path = rebuild / f"{label}.json"
            rpc = ScriptedRpc({"ListGrokBotAgents": [roster_reply]}, {})
            rc = apply_fleet(
                rows=[bot],
                seed="seed",
                spec_path=spec_path,
                spec_version=1,
                manifest_path=path,
                root=root,
                read_rpc=rpc.read,
                write_rpc=rpc.write,
                token="offline",
                clock=clock,
                id_factory=ids(),
            )
            check(
                rc != 0 and not rpc.write_calls and not path.exists(),
                f"{label}-roster-no-write",
            )

        # 2 Interruption immediately after instantiate: both ids are already durable.
        path = rebuild / "interrupt-create.json"
        apply_uuid = "00000000-0000-0000-0000-000000000002"
        apply_live = _agent(agent_uuid=apply_uuid)
        rpc = ScriptedRpc(
            {"ListGrokBotAgents": [(200, {"agents": []})]},
            {"CreateGrokBotAgentFromTemplate": [(200, {"agent": apply_live})]},
            before_write=lambda _method: check(
                path.is_file()
                and json.loads(path.read_text())["transitions"][-1]["event"]
                == "create.before",
                "manifest-durable-before-first-write",
            ),
        )
        try:
            apply_fleet(
                rows=[bot],
                seed="seed",
                spec_path=spec_path,
                spec_version=1,
                manifest_path=path,
                root=root,
                read_rpc=rpc.read,
                write_rpc=rpc.write,
                token="offline",
                clock=clock,
                id_factory=ids(),
                inject=lambda point, _m, _p: (_ for _ in ()).throw(
                    InjectedInterruption(point)
                )
                if point == "after_create"
                else None,
            )
            interrupted = False
        except InjectedInterruption:
            interrupted = True
        recovered = json.loads(path.read_text())
        identity = recovered["created"][0]
        check(
            interrupted
            and identity["new_numeric_id"] == 101
            and identity["new_uuid"] == apply_uuid
            and recovered["transitions"][-1]["event"] == "create.after",
            "interrupt-after-instantiate-keeps-ids",
        )

        # 3 Update failure plus failed compensation cannot be reported as success.
        failed_live = apply_live
        path = rebuild / "failed-compensation.json"

        def before_failed_write(method: str) -> None:
            receipt = json.loads(path.read_text())
            if method == "UpdateGrokBotAgent":
                created = receipt["created"][0]
                check(
                    created["new_numeric_id"] == 101
                    and created["new_uuid"] == apply_uuid
                    and any(
                        step["event"] == "create.after"
                        for step in receipt["transitions"]
                    ),
                    "created-identity-journaled-before-shape",
                )
            elif method == "DeleteGrokBotAgent":
                check(
                    receipt["transitions"][-1]["event"] == "compensation_delete.before",
                    "compensation-intent-journaled-before-delete",
                )

        rpc = ScriptedRpc(
            {
                "ListGrokBotAgents": [
                    (200, {"agents": []}),
                    (200, {"agents": [failed_live]}),
                ],
                "ListGrokBotAgentAutomations": [(200, {"automations": []})],
            },
            {
                "CreateGrokBotAgentFromTemplate": [(200, {"agent": failed_live})],
                "UpdateGrokBotAgent": [(500, "shape failed")],
                "DeleteGrokBotAgent": [(503, "delete failed")],
            },
            before_write=before_failed_write,
        )
        rc = apply_fleet(
            rows=[bot],
            seed="seed",
            spec_path=spec_path,
            spec_version=1,
            manifest_path=path,
            root=root,
            read_rpc=rpc.read,
            write_rpc=rpc.write,
            token="offline",
            clock=clock,
            id_factory=ids(),
        )
        failed_doc = json.loads(path.read_text())
        check(
            rc != 0
            and failed_doc["status"] == "PARTIAL"
            and failed_doc["deletions"][-1]["delete_http"] == 503
            and failed_doc["deletions"][-1]["absence"] == "STILL_PRESENT",
            "update-and-compensation-failure-fail-closed",
        )

        live = _agent(agent_uuid="00000000-0000-0000-0000-000000000101")
        # 4 A 200 delete reply is not deletion proof when the fresh roster still has the Bot.
        path = rebuild / "delete-still-present.json"
        doc = manifest_doc(path, root)
        rpc = ScriptedRpc(
            {
                "ListGrokBotAgents": [
                    (200, {"agents": [live]}),
                    (200, {"agents": [live]}),
                    (200, {"agents": [live]}),
                ],
                "ListGrokBotAgentAutomations": [(200, {"automations": []})],
            },
            {"DeleteGrokBotAgent": [(200, {})]},
        )
        rc = rollback_fleet(
            manifest=doc,
            manifest_path=path,
            read_rpc=rpc.read,
            write_rpc=rpc.write,
            token="offline",
            clock=clock,
            id_factory=ids(),
        )
        check(
            rc != 0
            and json.loads(path.read_text())["deletions"][-1]["absence"]
            == "STILL_PRESENT",
            "delete-reply-without-absence-fails",
        )

        # 5 Bot absence plus an automation row is PARTIAL residue, with its id preserved.
        path = rebuild / "orphan-residue.json"
        doc = manifest_doc(path, root)
        rpc = ScriptedRpc(
            {
                "ListGrokBotAgents": [
                    (200, {"agents": [live]}),
                    (200, {"agents": []}),
                    (200, {"agents": []}),
                ],
                "ListGrokBotAgentAutomations": [
                    (200, {"automations": [{"automationId": "routine-7"}]})
                ],
            },
            {"DeleteGrokBotAgent": [(200, {})]},
        )
        rc = rollback_fleet(
            manifest=doc,
            manifest_path=path,
            read_rpc=rpc.read,
            write_rpc=rpc.write,
            token="offline",
            clock=clock,
            id_factory=ids(),
        )
        residue_doc = json.loads(path.read_text())
        check(
            rc != 0
            and residue_doc["status"] == "ROLLBACK_PARTIAL"
            and residue_doc["automation_checks"][-1]["automation_ids"] == ["routine-7"],
            "orphan-automation-residue-is-partial",
        )

        # 6 Every mutation's injected interruption lands after its post-write receipt.
        for point, last_event in (
            ("after_update", "update.after"),
            ("after_rollback_delete", "rollback_delete.after"),
        ):
            path = rebuild / f"interrupt-{point}.json"
            if point == "after_update":
                rpc = ScriptedRpc(
                    {"ListGrokBotAgents": [(200, {"agents": []})]},
                    {
                        "CreateGrokBotAgentFromTemplate": [
                            (200, {"agent": apply_live})
                        ],
                        "UpdateGrokBotAgent": [(200, {})],
                    },
                )
                try:
                    apply_fleet(
                        rows=[bot],
                        seed="seed",
                        spec_path=spec_path,
                        spec_version=1,
                        manifest_path=path,
                        root=root,
                        read_rpc=rpc.read,
                        write_rpc=rpc.write,
                        token="offline",
                        clock=clock,
                        id_factory=ids(),
                        inject=lambda seen, _m, _p, wanted=point: (_ for _ in ()).throw(
                            InjectedInterruption(seen)
                        )
                        if seen == wanted
                        else None,
                    )
                except InjectedInterruption:
                    pass
            else:
                doc = manifest_doc(path, root)
                rpc = ScriptedRpc(
                    {"ListGrokBotAgents": [(200, {"agents": [live]})]},
                    {"DeleteGrokBotAgent": [(200, {})]},
                )
                try:
                    rollback_fleet(
                        manifest=doc,
                        manifest_path=path,
                        read_rpc=rpc.read,
                        write_rpc=rpc.write,
                        token="offline",
                        clock=clock,
                        id_factory=ids(),
                        inject=lambda seen, _m, _p, wanted=point: (_ for _ in ()).throw(
                            InjectedInterruption(seen)
                        )
                        if seen == wanted
                        else None,
                    )
                except InjectedInterruption:
                    pass
            check(
                json.loads(path.read_text())["transitions"][-1]["event"] == last_event,
                f"{point}-receipt-before-interrupt",
            )

        # 7 A shaped response is not success when any fresh field differs.
        path = rebuild / "shape-mismatch.json"
        wrong = _agent(agent_uuid=apply_uuid, title="wrong")
        rpc = ScriptedRpc(
            {
                "ListGrokBotAgents": [
                    (200, {"agents": []}),
                    (200, {"agents": [wrong]}),
                    (200, {"agents": []}),
                ],
                "ListGrokBotAgentAutomations": [(200, {"automations": []})],
            },
            {
                "CreateGrokBotAgentFromTemplate": [(200, {"agent": apply_live})],
                "UpdateGrokBotAgent": [(200, {})],
                "DeleteGrokBotAgent": [(200, {})],
            },
        )
        rc = apply_fleet(
            rows=[bot],
            seed="seed",
            spec_path=spec_path,
            spec_version=1,
            manifest_path=path,
            root=root,
            read_rpc=rpc.read,
            write_rpc=rpc.write,
            token="offline",
            clock=clock,
            id_factory=ids(),
        )
        check(
            rc != 0
            and "title" in json.loads(path.read_text())["readbacks"][-1]["mismatches"],
            "fresh-shaped-field-mismatch-fails",
        )

    for failure in failures:
        print(f"FAIL: {failure}")
    print(
        f"SELFTEST {'FAIL' if failures else 'PASS'} - {checks - len(failures)}/{checks} failure fixtures"
    )
    return EXIT_FINDINGS if failures else 0


def _manifest_path(root: pathlib.Path, clock: Clock, run_id: str) -> pathlib.Path:
    stamp = f"{clock():%Y-%m-%dT%H%M%S}"
    return root / "rebuild" / f"{stamp}-{run_id[:8]}.json"


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--yes", action="store_true", help="confirm an explicit rollback deletion"
    )
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--only", default=None, help="rebuild just this source Bot name")
    ap.add_argument("--rollback", default=None, metavar="MANIFEST")
    ap.add_argument(
        "--spec",
        default=None,
        metavar="PATH",
        help="Bot list PATH instead of fleet-spec.json",
    )
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.plan and args.apply:
        print("--plan and --apply are mutually exclusive", file=sys.stderr)
        return EXIT_USAGE

    if args.rollback:
        path = pathlib.Path(args.rollback)
        try:
            manifest = _validate_manifest(path, root)
        except ValueError as exc:
            print(f"REFUSED rollback: {exc}", file=sys.stderr)
            return EXIT_REFUSED
        recovery = manifest["recovery_argv"]
        if not args.apply and not args.yes:
            for row in manifest["created"]:
                print(
                    f"would delete {row['name']} numeric_id={row.get('new_numeric_id')} "
                    f"uuid={row.get('new_uuid') or row.get('requested_uuid')}"
                )
            print("Nothing deleted. Apply exactly with:")
            print(f"  {shlex.join(recovery)}")
            return 0
        if not args.apply or not args.yes:
            print(
                "REFUSED rollback: deletion requires both --apply and --yes; nothing was written",
                file=sys.stderr,
            )
            print(f"recovery argv: {shlex.join(recovery)}", file=sys.stderr)
            return EXIT_REFUSED
        mod = _load()
        token = mod.access_token(mod.SUPPORT)
        return rollback_fleet(
            manifest=manifest,
            manifest_path=path,
            read_rpc=mod.rpc,
            write_rpc=mod.write_rpc,
            token=token,
        )

    spec_path = pathlib.Path(args.spec) if args.spec else root / "fleet-spec.json"
    if not spec_path.is_file():
        print(
            f"{spec_path} is missing — the spec is reviewed text, not a derived file",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        spec = json.loads(spec_path.read_text())
    except ValueError as exc:
        print(f"{spec_path} is not valid JSON: {exc}", file=sys.stderr)
        return EXIT_USAGE
    bots = spec.get("bots") if isinstance(spec, dict) else None
    if not isinstance(bots, list) or not all(isinstance(bot, dict) for bot in bots):
        print(f"{spec_path} must contain a bots list", file=sys.stderr)
        return EXIT_USAGE
    rows = [bot for bot in bots if not args.only or bot.get("name") == args.only]
    if not rows:
        print(f"no Bot named {args.only!r} in {spec_path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        for bot in rows:
            _expected(bot)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"{spec_path} has a malformed Bot row: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.plan or not args.apply:
        print(
            f"{'source Bot':<22}{'title':<22}{'desc':>6}  first line of the new description"
        )
        for bot in rows:
            expected = _expected(bot)
            first = (
                expected["description"].strip().splitlines()[0]
                if expected["description"].strip()
                else ""
            )
            print(
                f"{expected['name'][:21]:<22}{expected['title'][:21]:<22}{len(expected['description']):>6}  {first[:70]}"
            )
        print()
        print(
            f"{len(rows)} Bot(s) would be created on the box harness. Nothing was written."
        )
        return 0

    seed = (spec.get("seed") or {}).get("share_id")
    if not isinstance(seed, str) or not seed:
        print(f"{spec_path} has no seed.share_id", file=sys.stderr)
        return EXIT_USAGE
    mod = _load()
    token = mod.access_token(mod.SUPPORT)
    run_id = _new_id()
    path = _manifest_path(root, _utcnow, run_id)
    # apply_fleet owns its run id; use a factory whose first value is the path-bound id.
    pending = [run_id]

    def ids() -> str:
        return pending.pop(0) if pending else _new_id()

    return apply_fleet(
        rows=rows,
        seed=seed,
        spec_path=spec_path,
        spec_version=spec.get("version"),
        manifest_path=path,
        root=root,
        read_rpc=mod.rpc,
        write_rpc=mod.write_rpc,
        token=token,
        id_factory=ids,
    )


if __name__ == "__main__":
    typed_main(main)
