"""gblib — the three readers every script in bin/ needs, in one place.

Extracted 2026-09-11 after `ripwire . --quality-delta --scope=bin` flagged the fourth copy of the
same JSON loader (`new-clone-of-reused-helper`, plus two `duplication` groups). Rule of Three: the
second copy is honest, the fourth is a maintenance liability — a fix to one of them is a fix to
none of the others.

Deliberately tiny. This is a shared-reader module, not a framework: anything with policy in it
belongs in the script that owns the policy, so the gate cannot start depending on the puller.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib

# Where the desktop client keeps its own state. One definition, because three scripts read it.
DEFAULT_SUPPORT = os.path.expanduser("~/Library/Application Support/Grok Bot")


def load(p: pathlib.Path | str):
    """Parse JSON, or None. Unreadable and absent are the same answer on purpose — every caller
    here treats 'I could not read it' as 'not measured', never as an empty value."""
    try:
        return json.loads(pathlib.Path(p).read_text(errors="replace"))
    except Exception:
        return None


def dated_children(d: pathlib.Path, suffix: str = "") -> list[pathlib.Path]:
    """Children named `YYYY-MM-DD…`, ascending. The date prefix is the sort key and the filter:
    anything not date-stamped is not an artifact of a tick."""
    if not d.is_dir():
        return []
    rows = [c for c in d.iterdir() if c.name.endswith(suffix) and c.name[:10].count("-") == 2]
    return sorted(rows, key=lambda c: c.name)


def newest_audit(root: pathlib.Path) -> tuple[pathlib.Path | None, dict | None]:
    """The most recent deployment audit, as (path, parsed). (None, None) when there is none —
    callers decide whether that is an ERROR, because for some of them it is."""
    rows = dated_children(root / "deployment", ".json")
    return (rows[-1], load(rows[-1])) if rows else (None, None)


def statsig_config(support: pathlib.Path | str = DEFAULT_SUPPORT) -> dict:
    """The client's evaluated Statsig payload, or {}. `config` is JSON-inside-JSON in the
    bootstrap file — the double decode is the vendor's shape, not ours."""
    boot = load(pathlib.Path(support) / "sand-statsig-bootstrap.json") or {}
    raw = boot.get("config")
    if not isinstance(raw, str):
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def unb32(name: str) -> str:
    """Decode the desktop client's base32 blob filenames back into their slice keys.

    The client stores `sand-client-persistence/<base32>.blob`; the key inside names what the blob
    holds (roster, transcript replica, drafts). Padding is restored because the client strips it.
    Returns "" for anything that does not decode — an unreadable name is not an error, it is a
    blob this repo has no business reading.
    """
    s = name.removesuffix(".blob").upper()
    s += "=" * ((8 - len(s) % 8) % 8)
    try:
        return base64.b32decode(s).decode()
    except Exception:
        return ""
