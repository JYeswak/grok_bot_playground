#!/usr/bin/env python3
"""gb-group — rooms (multi-Bot groups) and Bot-to-Bot DMs, receipted.

Rooms ARE agent rows (CreateGrokBotRoom returns an agent; read via memberAgentIds presence
of ListGrokBotAgents — there is no ListRooms RPC). Member sets are FULL-SET
replace (no add/remove delta exists — the CLI must not fake one). Dashboard
Groups are enterprise-directory objects, never chat rooms: never wired here.

Write verbs are dry-run plans unless --yes (exit 5 REFUSED pattern): rooms are
persistent and no DeleteRoom RPC exists, so creation is the point of no return.

Bot-to-Bot DMs (SendGrokBotAgentMessage) are CONFIRMED server-side (delivery
enum: DELIVERED_TEMPORAL/BOX/DUPLICATE/TARGET_NOT_FOUND/FORBIDDEN/...).
gb dm is a Bot->Bot nudge wire, not a private-room replacement.

Exit codes follow the gb dictionary: 0 OK · 2 USAGE · 3 ENVIRONMENT · 4 UPSTREAM · 5 REFUSED.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import types
from typing import Any
import urllib.error
import urllib.request
import uuid

BIN = pathlib.Path(__file__).resolve().parent
EXIT_OK, EXIT_USAGE, EXIT_ENVIRONMENT, EXIT_UPSTREAM, EXIT_REFUSED = 0, 2, 3, 4, 5

GROKBOT = "aiserver.v1.GrokBotService"


def _pull() -> types.ModuleType:
    sys.path.insert(0, str(BIN))
    import gbrpc as mod

    return mod


def _token(mod: types.ModuleType) -> str:
    from gblib import support_dir

    tok: Any = None
    try:
        tok = mod.access_token(support_dir())
    except TypeError:
        tok = mod.access_token()
    return str(tok)


def rpc(
    mod: types.ModuleType,
    token: str,
    method: str,
    body: dict[str, Any],
    timeout: float = 60.0,
) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"https://{mod.HOST}/{GROKBOT}/{method}",
        data=json.dumps(body).encode(),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "user-agent": "gb-group/1 (+local group ops)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"_error": f"{type(e).__name__}: {e}"}


def resolve_room(mod: types.ModuleType, token: str, name_or_uuid: str) -> str | None:
    """Room name -> agent_id via member-list rows; uuids pass through."""
    s = name_or_uuid.strip()
    if len(s) == 36 and s.count("-") == 4:
        return s
    st, resp = rpc(mod, token, "ListGrokBotAgents", {})
    if st != 200 or not isinstance(resp, dict):
        return None
    for b in resp.get("agents") or []:
        if (b.get("isGroup") or "memberAgentIds" in b) and (
            b.get("name") or ""
        ).lower() == s.lower():
            found = b.get("agentId") or b.get("uuid") or b.get("id")
            return str(found) if found else None
    return None


def cmd_list(mod: types.ModuleType, token: str, as_json: bool) -> int:
    st, resp = rpc(mod, token, "ListGrokBotAgents", {})
    if st != 200 or not isinstance(resp, dict):
        print(f"gb-group: roster read failed HTTP {st}", file=sys.stderr)
        return EXIT_UPSTREAM
    # Rooms are agent rows carrying memberAgentIds. isGroup is renderer-side and ABSENT
    # from list responses (measured 2026-09-12: a created room carries the member list
    # but no isGroup key), so member-list presence is the test, with isGroup kept.
    rooms = [
        {
            "room_id": b.get("agentId") or b.get("uuid") or b.get("id"),
            "name": b.get("name"),
        }
        for b in resp.get("agents") or []
        if b.get("isGroup") or "memberAgentIds" in b
    ]
    if as_json:
        print(json.dumps(rooms, indent=1))
    elif not rooms:
        print("no rooms (member-list test over ListGrokBotAgents)")
    else:
        for r in rooms:
            print(f"{r['name']}  {r['room_id']}")
    return EXIT_OK


def cmd_create(
    mod: types.ModuleType,
    token: str,
    name: str,
    members: str,
    description: str,
    yes: bool,
    as_json: bool,
) -> int:
    member_ids = [m.strip() for m in members.split(",") if m.strip()]
    nonce = str(uuid.uuid4())
    plan = {
        "name": name,
        "members": member_ids,
        "description": description,
        "nonce": nonce,
    }
    if not yes:
        if as_json:
            print(json.dumps({"dry_run": True, **plan}, indent=1))
        else:
            print(
                f"would create room {name!r} with {len(member_ids)} member(s); re-run with --yes"
            )
        return EXIT_REFUSED
    st, resp = rpc(
        mod,
        token,
        "CreateGrokBotRoom",
        {
            "agent_id": nonce,
            "name": name,
            "description": description,
            "member_agent_ids": member_ids,
        },
    )
    if st != 200 or not isinstance(resp, dict):
        print(f"gb-group: create failed HTTP {st}", file=sys.stderr)
        return EXIT_UPSTREAM
    agent = resp.get("agent") or {}
    if agent.get("agentId") != nonce:
        print(
            "gb-group: server did not confirm room identity — aborting", file=sys.stderr
        )
        return EXIT_UPSTREAM
    if as_json:
        print(json.dumps({"room_id": nonce, **plan}, indent=1))
    else:
        print(f"created room {name!r} -> {nonce}")
    return EXIT_OK


def cmd_members(
    mod: types.ModuleType, token: str, room: str, set_ids: str, yes: bool, as_json: bool
) -> int:
    room_id = resolve_room(mod, token, room)
    if not room_id:
        print(f"gb-group: no such room {room!r} (see `gb group list`)", file=sys.stderr)
        return EXIT_USAGE
    member_ids = [m.strip() for m in set_ids.split(",") if m.strip()]
    if not yes:
        if as_json:
            print(
                json.dumps(
                    {"dry_run": True, "room_id": room_id, "set": member_ids}, indent=1
                )
            )
        else:
            print(
                f"would set {len(member_ids)} member(s) on room (FULL-SET replace); re-run with --yes"
            )
        return EXIT_REFUSED
    st, resp = rpc(
        mod,
        token,
        "SetGrokBotRoomMembers",
        {
            "agent_id": room_id,
            "member_agent_ids": member_ids,
        },
    )
    if st != 200 or not isinstance(resp, dict):
        print(f"gb-group: members failed HTTP {st}", file=sys.stderr)
        return EXIT_UPSTREAM
    agent = resp.get("agent") or {}
    if agent.get("agentId") != room_id:
        print("gb-group: server reseated the room id — aborting", file=sys.stderr)
        return EXIT_UPSTREAM
    if as_json:
        print(json.dumps({"room_id": room_id, "set": member_ids}, indent=1))
    else:
        print(f"room members set ({len(member_ids)})")
    return EXIT_OK


def cmd_send(
    mod: types.ModuleType,
    token: str,
    room: str,
    text: str,
    mention: str,
    yes: bool,
    as_json: bool,
) -> int:
    import uuid as _uuid

    room_id = resolve_room(mod, token, room)
    if not room_id:
        print(f"gb-group: no such room {room!r} (see `gb group list`)", file=sys.stderr)
        return EXIT_USAGE
    if mention:
        text = f"@{mention} {text}"
    if not yes:
        if as_json:
            print(
                json.dumps(
                    {"dry_run": True, "room_id": room_id, "text": text}, indent=1
                )
            )
        else:
            print(
                f"would send to room ({len(text)} chars, spends allowance); re-run with --yes"
            )
        return EXIT_REFUSED
    st, resp = rpc(
        mod,
        token,
        "SendGrokBotUserMessage",
        {
            "agent_id": room_id,
            "message_id": str(_uuid.uuid4()),
            "text": text,
        },
    )
    if st != 200:
        print(f"gb-group: send failed HTTP {st}", file=sys.stderr)
        return EXIT_UPSTREAM
    if as_json:
        print(json.dumps({"room_id": room_id, "send": resp}, indent=1))
    else:
        print(f"sent to room — dispatched={resp.get('dispatched')}")
    return EXIT_OK


def cmd_dm(
    mod: types.ModuleType,
    token: str,
    frm: str,
    to: str,
    text: str,
    yes: bool,
    as_json: bool,
) -> int:
    import time
    import uuid as _uuid  # noqa: E402

    from_id, to_id = frm.strip(), to.strip()
    if not yes:
        if as_json:
            print(json.dumps({"dry_run": True, "from": from_id, "to": to_id}, indent=1))
        else:
            print("would send Bot->Bot DM (spends allowance); re-run with --yes")
        return EXIT_REFUSED
    st, resp = rpc(
        mod,
        token,
        "SendGrokBotAgentMessage",
        {
            "from_agent_id": from_id,
            "to_agent_id": to_id,
            "message_id": str(_uuid.uuid4()),
            "text": text,
            "sent_at_ms": int(time.time() * 1000),
        },
    )
    if st != 200:
        print(f"gb-group: dm failed HTTP {st}", file=sys.stderr)
        return EXIT_UPSTREAM
    if as_json:
        print(json.dumps(resp, indent=1))
    else:
        print(f"dm delivery={resp.get('delivery')} target={resp.get('target_name')}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-group", description=__doc__)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("list", help="rooms as member-list rows (read-only)")
    p = sub.add_parser(
        "create", help="create a room (dry run unless --yes; no delete RPC exists)"
    )
    p.add_argument("name")
    p.add_argument("--members", default="")
    p.add_argument("--description", default="")
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("members", help="FULL-SET member replace (dry run unless --yes)")
    p.add_argument("room")
    p.add_argument("--set", required=True)
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("send", help="message a room (dry run unless --yes)")
    p.add_argument("room")
    p.add_argument("text")
    p.add_argument("--mention")
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("dm", help="Bot->Bot DM (dry run unless --yes)")
    p.add_argument("from_bot")
    p.add_argument("to_bot")
    p.add_argument("text")
    p.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    try:
        mod = _pull()
        from gblib import support_dir

        try:
            token = mod.access_token(support_dir())
        except TypeError:
            token = mod.access_token()
    except Exception as e:
        print(f"gb-group: no client session ({type(e).__name__})", file=sys.stderr)
        return EXIT_ENVIRONMENT
    if a.verb == "list":
        return cmd_list(mod, token, a.json)
    if a.verb == "create":
        return cmd_create(mod, token, a.name, a.members, a.description, a.yes, a.json)
    if a.verb == "members":
        return cmd_members(mod, token, a.room, a.set, a.yes, a.json)
    if a.verb == "send":
        return cmd_send(mod, token, a.room, a.text, a.mention or "", a.yes, a.json)
    return cmd_dm(mod, token, a.from_bot, a.to_bot, a.text, a.yes, a.json)


if __name__ == "__main__":
    raise SystemExit(main())
