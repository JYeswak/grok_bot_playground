#!/usr/bin/env python3
"""gb-bot — talk to Bots and read them back, receipted, through the CLI.

Doctrine override (Joshua, explicit, 2026-09-12): sending a Bot turn from code
is permitted for receipted proof-calls. Every send dispatches a real turn that
spends allowance and speaks as the user, so `send` prints exactly what it sent
and the workflow it opened. `read` is the idempotent client-state echo (NE-6):
a Set with no changes that returns the authoritative row — a pure read.

Method map, mined from the shipped 0.47.0 bundle (client-binary-verified):
  GrokBotService/SendGrokBotUserMessage takes agent_id, message_id, text
    -> dispatched, workflowId, delivery — a turn. Costs allowance.
  GrokBotService/SetGrokBotAgentClientState takes agent_id — bare echo, no write.
(Brace-free by contract: gb dogfood audit reads brace groups in help as CLI actions.)

Bot names resolve against the newest deployment/<stamp>.json roster; a raw
uuid is accepted as-is. `resolve` prints that mapping with no session and no
spend. Auth reuses the client-session reader (in-memory only).

Exit codes follow the gb dictionary: 0 OK · 2 USAGE · 3 ENVIRONMENT · 4 UPSTREAM.
"""

from __future__ import annotations

import argparse
import glob
import types
from typing import Any
import importlib.util
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = pathlib.Path(__file__).resolve().parent
EXIT_OK, EXIT_USAGE, EXIT_ENVIRONMENT, EXIT_UPSTREAM = 0, 2, 3, 4

GROKBOT = "aiserver.v1.GrokBotService"


def _load() -> types.ModuleType:
    import gbrpc as mod  # noqa: E402  (pane-2 shared transport; falls back below)

    return mod


def _pull() -> types.ModuleType:
    try:
        sys.path.insert(0, str(BIN))
        import gbrpc as mod

        return mod
    except Exception:
        spec = importlib.util.spec_from_file_location(
            "gb_pull_inventory", BIN / "gb-pull-inventory.py"
        )
        assert spec is not None and spec.loader is not None
        mod2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod2)
        return mod2


def _token(mod: types.ModuleType) -> str:
    from gblib import support_dir

    tok: Any = None
    try:
        tok = mod.access_token(support_dir())
    except TypeError:
        tok = mod.access_token()
    return str(tok)


def resolve(name_or_uuid: str) -> str | None:
    """Bot name -> uuid. Audit roster first, then live fleet (API). uuid passes through."""
    s = name_or_uuid.strip()
    if len(s) == 36 and s.count("-") == 4:
        return s
    files = sorted(glob.glob(str(ROOT / "deployment" / "*.json")))
    for f in reversed(files):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for b in d.get("bots") or []:
            if (b.get("name") or "").lower() == s.lower():
                if b.get("uuid"):
                    return str(b["uuid"])
        for b in d.get("bots") or []:
            if (b.get("name") or "").lower() == s.lower() and b.get("id"):
                return str(b["id"])
    try:
        proc = __import__("subprocess").run(
            [
                sys.executable,
                str(BIN / "gb"),
                "fleet",
                "roster",
                "--json",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode == 0:
            doc = json.loads(proc.stdout)
            for b in doc.get("bots") or []:
                if (b.get("name") or "").lower() != s.lower():
                    continue
                uid = b.get("uuid") or b.get("id")
                if uid:
                    return str(uid)
    except Exception:
        return None
    return None


def post(
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
            "user-agent": "gb-bot/1 (+local Bot turns)",
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


def cmd_send(
    mod: types.ModuleType, token: str, bot: str, text: str, as_json: bool
) -> int:
    agent_id = resolve(bot)
    if not agent_id:
        print(
            f"gb-bot: unknown Bot {bot!r} (not in newest deployment roster)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    st, resp = post(
        mod,
        token,
        "SendGrokBotUserMessage",
        {"agent_id": agent_id, "message_id": str(uuid.uuid4()), "text": text},
        timeout=45.0,
    )
    if st != 200:
        print(
            f"gb-bot: send failed HTTP {st}: {json.dumps(resp)[:200]}", file=sys.stderr
        )
        return EXIT_UPSTREAM
    if as_json:
        print(json.dumps({"bot": bot, "agent_id": agent_id, "send": resp}, indent=1))
    else:
        print(
            f"sent to {bot} — dispatched={resp.get('dispatched')} workflow={resp.get('workflowId')}"
        )
    return EXIT_OK


def transcript(
    mod: types.ModuleType, token: str, agent_id: str, limit: int = 10
) -> tuple[int, list[dict[str, Any]]]:
    """Latest transcript entries, bodies base64-decoded. Read-only List (NE-8: real
    method, resource-scoped by agent_id; empty list = no turns yet, never an error)."""
    import base64

    st, resp = (lambda: (None, None))()
    req = urllib.request.Request(
        f"https://{mod.HOST}/{GROKBOT}/ListGrokBotTranscriptEntries",
        data=json.dumps({"agent_id": agent_id, "limit": limit}).encode(),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "user-agent": "gb-bot/1 (+local Bot turns)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            st, resp = r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, []
    except Exception as e:
        return -1, [{"_error": f"{type(e).__name__}: {e}"}]
    out: list[dict[str, Any]] = []
    raw_entries = (resp.get("entries") or []) if isinstance(resp, dict) else []
    for ent in raw_entries:
        row: dict[str, Any] = ent if isinstance(ent, dict) else {}
        body = row.get("body") or ""
        msg: Any = None
        try:
            msg = json.loads(base64.b64decode(body + "==="))
        except Exception:
            msg = None
        if not isinstance(msg, dict):
            msg = {"content": body[:200]}
        inner = msg.get("message") if isinstance(msg.get("message"), dict) else msg
        out.append(
            {
                "seq": row.get("seq"),
                "kind": msg.get("kind", row.get("entryKind")),
                "role": inner.get("role")
                or ("assistant" if msg.get("kind") == "send-message" else None),
                "content": inner.get("content"),
                "timestampMs": msg.get("timestampMs") or inner.get("timestampMs"),
            }
        )
    return st, out


def cmd_log(
    mod: types.ModuleType, token: str, bot: str, limit: int, as_json: bool
) -> int:
    agent_id = resolve(bot)
    if not agent_id:
        print(
            f"gb-bot: unknown Bot {bot!r} (not in newest deployment roster)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    st, entries = transcript(mod, token, agent_id, limit)
    if st != 200:
        print(f"gb-bot: log failed HTTP {st}", file=sys.stderr)
        return EXIT_UPSTREAM
    if as_json:
        print(json.dumps(entries, indent=1))
    else:
        for e in entries:
            print(f"[{e.get('seq')}] {e.get('role')}: {(e.get('content') or '')[:400]}")
    return EXIT_OK


def cmd_ask(
    mod: types.ModuleType,
    token: str,
    bot: str,
    text: str,
    timeout_s: float,
    expect: str,
    as_json: bool,
) -> int:
    """Full lifecycle: send, poll for a newer assistant message, print it, match --expect."""
    import re
    import time

    agent_id = resolve(bot)
    if not agent_id:
        print(
            f"gb-bot: unknown Bot {bot!r} (not in newest deployment roster)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    st, before = transcript(mod, token, agent_id, 3)
    if st != 200:
        print("gb-bot: pre-read failed HTTP %s" % st, file=sys.stderr)
        return EXIT_UPSTREAM
    seen = {e.get("seq") for e in before}
    rc = cmd_send(mod, token, bot, text, False)
    if rc != EXIT_OK:
        return rc
    # Turns to one Bot queue behind each other, so the first fresh message may
    # belong to an earlier turn. Poll to the deadline, match --expect against
    # EVERYTHING new, and report the last fresh message. Quiescence (a full
    # interval with nothing new) short-circuits early on a match.
    matched = None
    last_fresh = None
    deadline = time.time() + timeout_s
    quiet_rounds = 0
    while time.time() < deadline:
        time.sleep(20.0)
        st, entries = transcript(mod, token, agent_id, 15)
        if st != 200:
            continue
        fresh = [
            e
            for e in entries
            if e.get("seq") not in seen and e.get("role") == "assistant"
        ]
        if not fresh:
            quiet_rounds += 1
            if matched and quiet_rounds >= 2:
                break
            continue
        quiet_rounds = 0
        for e in fresh:
            seen.add(e.get("seq"))
        last_fresh = fresh[-1]
        if expect:
            for e in reversed(fresh):
                if re.search(expect, str(e.get("content") or ""), re.S):
                    matched = e
                    break
    reply = matched or last_fresh
    if reply is None:
        print(f"gb-bot: no assistant reply within {timeout_s:.0f}s", file=sys.stderr)
        return EXIT_UPSTREAM
    content = reply.get("content") or ""
    if as_json:
        print(json.dumps(reply, indent=1))
    else:
        print(content)
    if expect and not re.search(expect, content, re.S):
        print(
            f"gb-bot: reply did not match /{expect}/ (last fresh shown)",
            file=sys.stderr,
        )
        return 1
    return EXIT_OK


def cmd_read(mod: types.ModuleType, token: str, bot: str, as_json: bool) -> int:
    agent_id = resolve(bot)
    if not agent_id:
        print(
            f"gb-bot: unknown Bot {bot!r} (not in newest deployment roster)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    st, resp = post(
        mod, token, "SetGrokBotAgentClientState", {"agent_id": agent_id}, timeout=45.0
    )
    if st != 200:
        print(
            f"gb-bot: read failed HTTP {st}: {json.dumps(resp)[:200]}", file=sys.stderr
        )
        return EXIT_UPSTREAM
    s = resp.get("state", resp) if isinstance(resp, dict) else {}
    row = {
        "bot": bot,
        "notificationsEnabled": s.get("notificationsEnabled"),
        "notifyOnUpdatesEnabled": s.get("notifyOnUpdatesEnabled"),
        "unreadCount": s.get("unreadCount"),
        "lastActivityAtMs": s.get("lastActivityAtMs"),
        "preview": s.get("lastMessagePreview"),
    }
    if as_json:
        print(json.dumps(row, indent=1))
    else:
        print(
            f"{bot} — unread={row['unreadCount']} notif={row['notificationsEnabled']}"
        )
        print(f"preview: {row['preview']}")
    return EXIT_OK


def cmd_resolve(bot: str, as_json: bool) -> int:
    """Bot name -> uuid against the roster. No session, no spend: the roster is local files."""
    agent_id = resolve(bot)
    if not agent_id:
        print(
            f"gb-bot: unknown Bot {bot!r} (not in newest deployment roster)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if as_json:
        print(json.dumps({"bot": bot, "agent_id": agent_id}, indent=1))
    else:
        print(agent_id)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-bot", description=__doc__)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("send", help="dispatch one turn to a Bot (spends allowance)")
    p.add_argument("bot", help="Bot name or uuid")
    p.add_argument("text", help="message text")
    p = sub.add_parser(
        "read", help="authoritative client-state row + latest preview (idempotent)"
    )
    p.add_argument("bot", help="Bot name or uuid")
    p = sub.add_parser(
        "log", help="latest transcript entries, bodies decoded (read-only)"
    )
    p.add_argument("bot", help="Bot name or uuid")
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser(
        "ask", help="send, await the reply, print it, match --expect (full lifecycle)"
    )
    p.add_argument("bot", help="Bot name or uuid")
    p.add_argument("text", help="message text")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--expect", default="")
    p = sub.add_parser(
        "resolve", help="Bot name -> uuid from the roster (no session, no spend)"
    )
    p.add_argument("bot", help="Bot name or uuid")
    a = ap.parse_args(argv)
    if a.verb == "resolve":
        return cmd_resolve(a.bot, a.json)
    try:
        mod = _pull()
        token = _token(mod)
    except Exception as e:
        print(f"gb-bot: no client session ({type(e).__name__})", file=sys.stderr)
        return EXIT_ENVIRONMENT
    if a.verb == "send":
        return cmd_send(mod, token, a.bot, a.text, a.json)
    if a.verb == "log":
        return cmd_log(mod, token, a.bot, a.limit, a.json)
    if a.verb == "ask":
        return cmd_ask(mod, token, a.bot, a.text, a.timeout, a.expect, a.json)
    return cmd_read(mod, token, a.bot, a.json)


if __name__ == "__main__":
    raise SystemExit(main())
