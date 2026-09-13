#!/usr/bin/env python3
"""gb-market — live marketplace Bots from market/<stamp>.json.

Scan, not a 101. `refresh` re-runs the existing snapshot producer.
`bots` lists bot_marketplace.rows from the newest stamp: name, creator,
category, updated, plus new-since-previous. Does not invent share_id.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children  # noqa: E402
from gbtypes import main as gbmain  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
SCHEMA = "gb-market-bots/1"
EXIT_OK, EXIT_USAGE, EXIT_ENVIRONMENT = 0, 2, 3
PROVE_JOURNEY = frozenset({"hello-computer", "first-file-desk", "plugin-proof"})


def emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def snapshots(root: pathlib.Path) -> List[pathlib.Path]:
    return dated_children(root / "market", ".json")


def load_doc(path: pathlib.Path) -> Optional[dict]:
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def marketplace_rows(doc: dict) -> List[dict]:
    shelf = doc.get("bot_marketplace") if isinstance(doc.get("bot_marketplace"), dict) else {}
    rows = shelf.get("rows") or []
    return [r for r in rows if isinstance(r, dict)]


def fmt_updated(value: Any) -> str:
    if value in (None, ""):
        return "-"
    raw = str(value)
    if raw.isdigit():
        ms = int(raw)
        if ms > 10**12:
            ms //= 1000
        try:
            return datetime.fromtimestamp(ms, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            return raw
    return raw


def share_id_of(row: dict) -> str:
    sid = row.get("share_id") or row.get("shareId")
    return str(sid) if sid else ""


def cmd_refresh() -> int:
    snap = BIN / "gb-market-snapshot.py"
    if not snap.is_file():
        emit("gb market refresh: missing bin/gb-market-snapshot.py")
        return EXIT_ENVIRONMENT
    return subprocess.call([sys.executable, str(snap)])


def cmd_bots(root: pathlib.Path, as_json: bool) -> int:
    stamps = snapshots(root)
    if not stamps:
        emit("gb market bots: no market/<stamp>.json — run: gb market refresh")
        return EXIT_ENVIRONMENT
    path = stamps[-1]
    doc = load_doc(path)
    if doc is None:
        emit("gb market bots: unreadable %s — run: gb market refresh" % path.name)
        return EXIT_ENVIRONMENT
    rows = marketplace_rows(doc)
    prev_path = stamps[-2] if len(stamps) > 1 else None
    prev_doc = load_doc(prev_path) if prev_path else None
    old_slugs = {str(r.get("slug") or "") for r in marketplace_rows(prev_doc or {})}
    new_rows = [
        r
        for r in rows
        if str(r.get("slug") or "") and str(r.get("slug") or "") not in old_slugs
    ]
    with_share = sum(1 for r in rows if share_id_of(r))
    payload = {
        "schema": SCHEMA,
        "snapshot": path.name,
        "total": len(rows),
        "new_since": prev_path.name if prev_path else None,
        "new": [
            {
                "slug": r.get("slug"),
                "name": r.get("name"),
                "creator": r.get("creator"),
                "category": r.get("category"),
                "updated": fmt_updated(r.get("updated_at_ms") or r.get("updated")),
                "share_id": share_id_of(r) or None,
            }
            for r in new_rows
        ],
        "rows": [
            {
                "slug": r.get("slug"),
                "name": r.get("name"),
                "creator": r.get("creator"),
                "category": r.get("category"),
                "updated": fmt_updated(r.get("updated_at_ms") or r.get("updated")),
                "share_id": share_id_of(r) or None,
            }
            for r in rows
        ],
        "share_id_present": with_share,
        "one_click": with_share == len(rows) and len(rows) > 0,
        "limit": (
            "bot_marketplace rows lack share_id — one-click deploy blocked "
            "until the scan carries it"
            if with_share < len(rows)
            else None
        ),
    }
    if as_json:
        emit(json.dumps(payload, indent=1))
        return EXIT_OK
    emit("BOT MARKETPLACE  %s  n=%d" % (path.name, len(rows)))
    if prev_path:
        emit(
            "  new-since %s  %d"
            % (prev_path.name, len(new_rows))
        )
    else:
        emit("  new-since  (need a second market/ snapshot)")
    emit(
        "  share_id  %d/%d listings carry share_id — one-click deploy blocked "
        "until the scan carries it" % (with_share, len(rows))
        if with_share < len(rows)
        else "  share_id  %d/%d" % (with_share, len(rows))
    )
    emit("  plugin install  HUMAN Settings → Plugins. No install RPC.")
    emit("%-28s %-20s %-16s %s" % ("NAME", "CREATOR", "CATEGORY", "UPDATED"))
    for r in payload["rows"]:
        emit(
            "%-28s %-20s %-16s %s"
            % (
                str(r.get("name") or "-")[:28],
                str(r.get("creator") or "-")[:20],
                str(r.get("category") or "-")[:16],
                r.get("updated") or "-",
            )
        )
    return EXIT_OK


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    older = {
        "schema": "gb-market/1",
        "bot_marketplace": {
            "total": 1,
            "rows": [
                {
                    "slug": "old-bot",
                    "name": "Old Bot",
                    "creator": "ada",
                    "category": "Ops",
                    "updated_at_ms": "1789000000000",
                }
            ],
        },
    }
    newer = {
        "schema": "gb-market/1",
        "bot_marketplace": {
            "total": 2,
            "rows": [
                {
                    "slug": "old-bot",
                    "name": "Old Bot",
                    "creator": "ada",
                    "category": "Ops",
                    "updated_at_ms": "1789000000000",
                },
                {
                    "slug": "new-bot",
                    "name": "New Bot",
                    "creator": "bev",
                    "category": "Sales",
                    "updated_at_ms": "1789086400000",
                },
            ],
        },
    }
    with tempfile.TemporaryDirectory(prefix="gb-market-selftest-") as tmp:
        root = pathlib.Path(tmp)
        market = root / "market"
        market.mkdir()
        (market / "2026-09-04T0600.json").write_text(json.dumps(older) + "\n")
        (market / "2026-09-11T0600.json").write_text(json.dumps(newer) + "\n")
        code, out = _capture(lambda: cmd_bots(root, False))
        check("bots-exit-ok", code == 0, str(code))
        check("bots-shows-name", "New Bot" in out and "Old Bot" in out, out[:200])
        check("bots-shows-creator", "bev" in out and "ada" in out, out[:200])
        check("bots-shows-category", "Sales" in out and "Ops" in out, out[:200])
        check("bots-shows-updated", "UPDATED" in out, out[:200])
        check("bots-new-since-previous", "new-since 2026-09-04T0600.json  1" in out, out)
        check(
            "share-id-limit-printed",
            "share_id" in out and "one-click deploy blocked" in out,
            out,
        )
        check(
            "does-not-invent-share-id",
            "x.ai/bot/" not in out and "share_id  0/2" in out,
            out,
        )
        check(
            "not-a-101-or-prove-journey",
            not any(t in out for t in PROVE_JOURNEY) and "first hour" not in out,
            out,
        )
        jcode, jout = _capture(lambda: cmd_bots(root, True))
        payload = json.loads(jout) if jout.strip().startswith("{") else {}
        check("json-total-is-snapshot", payload.get("total") == 2, jout[:200])
        check(
            "json-share-ids-are-null",
            all(r.get("share_id") is None for r in payload.get("rows") or []),
            str(payload.get("rows")),
        )
        empty = root / "empty"
        empty.mkdir()
        ecode, eout = _capture(lambda: cmd_bots(empty, False))
        check("missing-snapshot-is-environment", ecode == EXIT_ENVIRONMENT, str(ecode))
        check("missing-names-refresh", "gb market refresh" in eout, eout)

    snap = BIN / "gb-market-snapshot.py"
    check("refresh-producer-exists", snap.is_file(), str(snap))

    failed = [n for n, ok, d in legs if not ok]
    for name, ok, detail in legs:
        if not ok:
            emit("FAIL %s: %s" % (name, detail))
    emit("SELFTEST %s - %d/%d" % ("FAIL" if failed else "PASS", len(legs) - len(failed), len(legs)))
    return 1 if failed else 0


def _capture(fn: Callable[[], int]) -> Tuple[int, str]:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn()
    return code, buf.getvalue()


def body(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gb-market.py",
        description="Scan live marketplace Bots from market/<stamp>.json.",
    )
    ap.add_argument(
        "action",
        nargs="?",
        choices=("refresh", "bots"),
        default="bots",
        help="refresh (network snapshot) | bots (read newest stamp)",
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default="", help="artifact root (tests)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    if args.action == "refresh":
        return cmd_refresh()
    root = pathlib.Path(args.root) if args.root else ROOT
    return cmd_bots(root, bool(args.json))


if __name__ == "__main__":
    gbmain(lambda: body())
