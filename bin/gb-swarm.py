#!/usr/bin/env python3
"""gb-swarm — deploy every Bot in one persona pack.

Plans the template list. `--apply` walks the existing
CreateGrokBotAgentFromTemplate + Update path (`gb-templates.py deploy`).
Refuses the prove-the-tool swarm (hello-computer + first-file-desk +
plugin-proof) and any redirect into `gb role "first hour"`.
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
from gbtypes import main as gbmain  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
SCHEMA = "gb-swarm/1"
EXIT_OK, EXIT_USAGE, EXIT_ENVIRONMENT, EXIT_REFUSED = 0, 2, 3, 5
PROVE_SWARM = ("hello-computer", "first-file-desk", "plugin-proof")
FIRST_HOUR_NEXT = (
    "gb market bots, gb templates deploy <id> --apply, gb swarm <persona>, gb stack"
)


def emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def warn(text: str) -> None:
    sys.stderr.write(text if text.endswith("\n") else text + "\n")


def _normalise(value: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in value.casefold()).split())


def is_first_hour_persona(persona: str) -> bool:
    key = _normalise(persona)
    return key in {"first hour", "firsthour"} or key.replace(" ", "") == "firsthour"


def load_persona(root: pathlib.Path, persona: str) -> Optional[dict]:
    path = root / "personas" / ("%s.json" % persona)
    if not path.is_file():
        # allow a phrase like founder-operator already being the id
        matches = list((root / "personas").glob("*.json")) if (root / "personas").is_dir() else []
        for cand in matches:
            try:
                doc = json.loads(cand.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(doc, dict):
                continue
            names = {str(doc.get("id") or cand.stem), str(doc.get("name") or "")}
            if persona in names or _normalise(persona) in {_normalise(n) for n in names if n}:
                return doc
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def persona_templates(doc: dict) -> List[str]:
    ids: List[str] = []
    for row in doc.get("bots") or []:
        if isinstance(row, dict) and row.get("template"):
            ids.append(str(row["template"]))
    return ids


def is_prove_swarm(ids: Sequence[str]) -> bool:
    return tuple(ids) == PROVE_SWARM or set(ids) == set(PROVE_SWARM)


def refuse_prove(as_json: bool) -> int:
    reason = (
        "gb swarm: refused prove-the-tool pack "
        "(hello-computer + first-file-desk + plugin-proof). next: %s" % FIRST_HOUR_NEXT
    )
    if as_json:
        emit(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "status": "REFUSED",
                    "error": reason,
                    "next": FIRST_HOUR_NEXT,
                    "mutates": False,
                },
                indent=1,
            )
        )
    else:
        emit(reason)
    return EXIT_REFUSED


def cmd_swarm(
    root: pathlib.Path,
    persona: str,
    apply: bool,
    as_json: bool,
    deployer: Optional[Callable[[str, bool], int]] = None,
) -> int:
    if is_first_hour_persona(persona):
        return refuse_prove(as_json)
    doc = load_persona(root, persona)
    if doc is None:
        known = sorted(p.stem for p in (root / "personas").glob("*.json")) if (root / "personas").is_dir() else []
        msg = "gb swarm: unknown persona %r. try: gb swarm founder-operator" % persona
        if known:
            msg += "\n    known: " + " ".join(known)
        warn(msg)
        if as_json:
            emit(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "status": "USAGE",
                        "error": "unknown persona",
                        "persona": persona,
                    },
                    indent=1,
                )
            )
        return EXIT_USAGE
    ids = persona_templates(doc)
    if is_prove_swarm(ids) or str(doc.get("id") or "") == "first-hour":
        return refuse_prove(as_json)
    pid = str(doc.get("id") or persona)
    results: List[Dict[str, Any]] = []
    if apply:
        run_deploy = deployer or _deploy_one
        for tid in ids:
            rc = run_deploy(tid, True)
            results.append({"id": tid, "rc": rc, "applied": True})
    payload = {
        "schema": SCHEMA,
        "status": "OK",
        "persona": pid,
        "templates": ids,
        "count": len(ids),
        "applied": bool(apply),
        "results": results,
        "plan": ["gb", "templates", "deploy", "<id>"] if not apply else None,
        "next": None if apply else "gb swarm %s --apply" % pid,
    }
    if as_json:
        emit(json.dumps(payload, indent=1))
        return EXIT_OK
    emit("SWARM  %s  n=%d" % (pid, len(ids)))
    for tid in ids:
        emit("  %s" % tid)
    if apply:
        for row in results:
            emit("  deploy %s  rc=%s" % (row["id"], row["rc"]))
    else:
        emit("  plan  gb swarm %s --apply" % pid)
        emit("  each  gb templates deploy <id> --apply")
    return EXIT_OK


def _deploy_one(tid: str, apply: bool) -> int:
    argv = [sys.executable, str(BIN / "gb-templates.py"), "deploy", tid]
    if apply:
        argv.append("--apply")
    return subprocess.call(argv)


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    with tempfile.TemporaryDirectory(prefix="gb-swarm-selftest-") as tmp:
        root = pathlib.Path(tmp)
        personas = root / "personas"
        personas.mkdir()
        (personas / "first-hour.json").write_text(
            json.dumps(
                {
                    "id": "first-hour",
                    "name": "First Hour",
                    "bots": [
                        {"template": "hello-computer"},
                        {"template": "first-file-desk"},
                        {"template": "plugin-proof"},
                    ],
                }
            )
        )
        founder_ids = [
            "chief-of-staff",
            "morning-briefing",
            "workforce-check",
            "refusal-desk",
            "decision-ledger",
            "allowance-watch",
            "approval-desk",
            "maker-checker",
            "hiring-screen",
            "alfred-desk",
            "cap-watch",
            "calendar-owner",
            "plugin-watch",
        ]
        (personas / "founder-operator.json").write_text(
            json.dumps(
                {
                    "id": "founder-operator",
                    "name": "Founder Operator",
                    "bots": [{"template": t} for t in founder_ids],
                }
            )
        )
        (personas / "sneaky-prove.json").write_text(
            json.dumps(
                {
                    "id": "sneaky-prove",
                    "name": "Sneaky",
                    "bots": [
                        {"template": "hello-computer"},
                        {"template": "first-file-desk"},
                        {"template": "plugin-proof"},
                    ],
                }
            )
        )
        rc, out = _capture(lambda: cmd_swarm(root, "first-hour", False, False))
        check("first-hour-refuses", rc == EXIT_REFUSED, str(rc))
        check("first-hour-names-next", "gb market bots" in out and "gb swarm" in out, out)
        check("first-hour-not-a-plan", "hello-computer" not in out or "refused" in out, out)
        rc2, out2 = _capture(lambda: cmd_swarm(root, "first hour", False, False))
        check("first-hour-phrase-refuses", rc2 == EXIT_REFUSED, str(rc2))
        rc3, out3 = _capture(lambda: cmd_swarm(root, "sneaky-prove", False, False))
        check("prove-set-refuses-even-renamed", rc3 == EXIT_REFUSED, out3)
        rc4, out4 = _capture(lambda: cmd_swarm(root, "founder-operator", False, False))
        check("founder-plans", rc4 == EXIT_OK and "n=13" in out4, out4)
        check(
            "founder-lists-morning-briefing",
            "morning-briefing" in out4 and "hello-computer" not in out4,
            out4,
        )
        check("founder-not-role-redirect", "gb role \"first hour\"" not in out4, out4)
        deployed: List[str] = []

        def fake(tid: str, apply: bool) -> int:
            deployed.append(tid)
            check_apply = apply
            return 0 if check_apply else 1

        rc5, out5 = _capture(
            lambda: cmd_swarm(root, "founder-operator", True, False, deployer=fake)
        )
        check("apply-walks-existing-deploy", rc5 == EXIT_OK and deployed == founder_ids, str(deployed))
        check("apply-not-role-redirect", "first hour" not in out5, out5)
        rc6, _ = _capture(lambda: cmd_swarm(root, "nope", False, False))
        check("unknown-persona-usage", rc6 == EXIT_USAGE, str(rc6))

    live = ROOT / "personas" / "founder-operator.json"
    if live.is_file():
        doc = json.loads(live.read_text())
        ids = persona_templates(doc)
        check("live-founder-is-not-prove-swarm", not is_prove_swarm(ids), str(ids))
        check("live-founder-has-templates", len(ids) >= 1, str(len(ids)))

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
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        code = fn()
    return code, buf.getvalue() + err.getvalue()


def body(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gb-swarm.py",
        description="Deploy every Bot in one persona pack.",
    )
    ap.add_argument("persona", nargs="?", help="persona pack id, e.g. founder-operator")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    if not args.persona:
        warn("gb swarm: needs a persona. try: gb swarm founder-operator")
        return EXIT_USAGE
    root = pathlib.Path(args.root) if args.root else ROOT
    return cmd_swarm(root, args.persona, bool(args.apply), bool(args.json))


if __name__ == "__main__":
    gbmain(lambda: body())
