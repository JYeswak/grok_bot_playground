#!/usr/bin/env python3
"""gb-stack — plugins, attachable skills, and gb x methods.

Reprints plugin catalog totals from the newest market/<stamp>.json.
Lists attachable skills on disk. Invokes `gb x` for practitioner methods
ranked by distinct authors. Does not invent plugin counts or share_ids.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children  # noqa: E402
from gbtypes import main as gbmain  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
SCHEMA = "gb-stack/1"
EXIT_OK, EXIT_ENVIRONMENT = 0, 3


def emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def newest_market(root: pathlib.Path) -> Tuple[Optional[pathlib.Path], Optional[dict]]:
    rows = dated_children(root / "market", ".json")
    if not rows:
        return None, None
    path = rows[-1]
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return path, None
    return path, doc if isinstance(doc, dict) else None


def plugin_total(doc: dict) -> Any:
    plugins = doc.get("plugins") if isinstance(doc.get("plugins"), dict) else {}
    return plugins.get("total")


def disk_skills(root: pathlib.Path) -> List[str]:
    base = root / "plugin" / "skills"
    if not base.is_dir():
        return []
    names: List[str] = []
    for path in sorted(base.iterdir()):
        if (path / "SKILL.md").is_file():
            names.append(path.name)
    return names


def run_x(root: pathlib.Path) -> Tuple[int, str]:
    argv = [sys.executable, str(BIN / "gb-x-sweep.py"), "reclassify"]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 3, "%s" % exc
    text = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, text


def cmd_stack(root: pathlib.Path, as_json: bool, x_runner: Optional[Callable[[pathlib.Path], Tuple[int, str]]] = None) -> int:
    path, doc = newest_market(root)
    if path is None or doc is None:
        emit("gb stack: no market/<stamp>.json — run: gb market refresh")
        return EXIT_ENVIRONMENT
    total = plugin_total(doc)
    skills = disk_skills(root)
    runner = x_runner or run_x
    x_rc, x_out = runner(root)
    payload = {
        "schema": SCHEMA,
        "snapshot": path.name,
        "plugins_total": total,
        "plugins_source": "market snapshot / gb plugins catalog",
        "skills": skills,
        "skills_n": len(skills),
        "x_exit": x_rc,
        "x_preview": x_out.strip().splitlines()[:12],
        "limits": [
            "plugin install is HUMAN Settings → Plugins (no install RPC)",
            "bot_marketplace rows often lack share_id — one-click blocked until the scan carries it",
        ],
    }
    if as_json:
        emit(json.dumps(payload, indent=1))
        return EXIT_OK
    emit("STACK  snapshot %s" % path.name)
    emit("  plugins  %s  (from market snapshot / gb plugins catalog)" % total)
    emit("  plugin install  HUMAN Settings → Plugins. No install RPC.")
    emit("  skills  n=%d" % len(skills))
    for name in skills:
        emit("    %s" % name)
    emit("  x  gb x  (rank by distinct authors)  exit=%s" % x_rc)
    for line in payload["x_preview"]:
        emit("    %s" % line)
    emit(
        "  share_id  marketplace listings often lack share_id — "
        "one-click deploy blocked until the scan carries it"
    )
    return EXIT_OK


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    with tempfile.TemporaryDirectory(prefix="gb-stack-selftest-") as tmp:
        root = pathlib.Path(tmp)
        market = root / "market"
        market.mkdir()
        (market / "2026-09-11T0600.json").write_text(
            json.dumps({"schema": "gb-market/1", "plugins": {"total": 17, "rows": []}})
            + "\n"
        )
        skills = root / "plugin" / "skills" / "alpha-skill"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("---\nname: alpha-skill\n---\n")
        beta = root / "plugin" / "skills" / "beta-skill"
        beta.mkdir()
        (beta / "SKILL.md").write_text("---\nname: beta-skill\n---\n")

        def fake_x(_root: pathlib.Path) -> Tuple[int, str]:
            return 3, "gb x reclassify: needs one plain and one .sweep. artifact under x/"

        rc, out = _capture(lambda: cmd_stack(root, False, x_runner=fake_x))
        check("stack-ok-with-snapshot", rc == 0, str(rc))
        check("reprints-snapshot-plugin-total", "plugins  17" in out, out)
        check("does-not-invent-326", "326" not in out, out)
        check("lists-disk-skills", "alpha-skill" in out and "beta-skill" in out, out)
        check("names-gb-x", "gb x" in out and "distinct authors" in out, out)
        check("plugin-install-is-human", "Settings → Plugins" in out and "No install RPC" in out, out)
        check("share-id-limit", "lack share_id" in out, out)
        empty = root / "empty"
        empty.mkdir()
        erc, eout = _capture(lambda: cmd_stack(empty, False, x_runner=fake_x))
        check("missing-snapshot-environment", erc == EXIT_ENVIRONMENT, str(erc))
        check("missing-names-refresh", "gb market refresh" in eout, eout)

    failed = [n for n, ok, d in legs if not ok]
    for name, ok, detail in legs:
        if not ok:
            emit("FAIL %s: %s" % (name, detail))
    emit(
        "SELFTEST %s - %d/%d"
        % ("FAIL" if failed else "PASS", len(legs) - len(failed), len(legs))
    )
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
        prog="gb-stack.py",
        description="Plugins, attachable skills, and gb x methods.",
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    root = pathlib.Path(args.root) if args.root else ROOT
    return cmd_stack(root, bool(args.json))


if __name__ == "__main__":
    gbmain(lambda: body())
