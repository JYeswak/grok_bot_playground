#!/usr/bin/env python3
"""gb-stack — hire plan for a pack team, plus the old plugin census.

Default `gb stack founder|engineer|seller` is a PLAN: named ROOM, one seat per
pack job, full share_id, INSTALL grokbot:// URL. Thompson fills jobs the same
way `gb market pack` does (import that picker; do not fork pick_jobs).

`--apply` is the hire gate (same family as templates deploy / fleet rebuild).
A printed URL is not a hire. If CreateGrokBot / share-install from a catalog
share_id is unproven, --apply REFUSES (exit 5) and names the missing RPC plus
the human path. CreateChannel/room is gated the same way.

`gb stack plugins` keeps the gb-stack/1 census. Bare `gb stack` is usage.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import inspect
import io
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
SCHEMA_PLUGINS = "gb-stack/1"
SCHEMA = "gb-stack/2"
EXIT_OK, EXIT_USAGE, EXIT_ENVIRONMENT, EXIT_REFUSED = 0, 2, 3, 5
# Proven fleet create is CreateGrokBotAgentFromTemplate (seed + Update). That
# instantiates the local seed template, not a marketplace catalog share.
# No share-install method is allowlisted. CreateChannel is a human tap.
HIRE_RPC: Optional[str] = None
ROOM_RPC: Optional[str] = None
MISSING_HIRE = "proven CreateGrokBot / share-install RPC from catalog share_id"
MISSING_ROOM = "proven CreateChannel / create-room path after hired member ids"
HUMAN_HIRE = (
    "tap INSTALL grokbot://app/v1/bot-template?id=<share_id> "
    "(this account's hire / CreateAgent flow)"
)
HUMAN_ROOM = "Joshua taps CreateChannel"
INSTALL_LINE = "INSTALL grokbot://app/v1/bot-template?id=%s"


def emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _load_market() -> Any:
    name = "gb_market_for_stack"
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    path = BIN / "gb-market.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load gb-market.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def hire_rpc_status() -> Dict[str, Any]:
    """Honest gate: a name here is a proven method, not a wish."""
    return {
        "hire_rpc": HIRE_RPC,
        "room_rpc": ROOM_RPC,
        "hire_proven": HIRE_RPC is not None,
        "room_proven": ROOM_RPC is not None,
    }


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


def cmd_plugins(
    root: pathlib.Path,
    as_json: bool,
    x_runner: Optional[Callable[[pathlib.Path], Tuple[int, str]]] = None,
) -> int:
    """Old census. Schema gb-stack/1. Does not invent plugin counts or share_ids."""
    path, doc = newest_market(root)
    if path is None or doc is None:
        emit("gb stack: no market/<stamp>.json — run: gb market refresh")
        return EXIT_ENVIRONMENT
    total = plugin_total(doc)
    skills = disk_skills(root)
    runner = x_runner or run_x
    x_rc, x_out = runner(root)
    payload = {
        "schema": SCHEMA_PLUGINS,
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


def usage_persona() -> int:
    emit(
        "gb stack: refuse — persona is founder, engineer, or seller "
        "(or: gb stack plugins)"
    )
    return EXIT_USAGE


def _pack_payload(
    root: pathlib.Path,
    persona: str,
    offline: bool,
    rng: Any = None,
) -> Tuple[int, Dict[str, Any], str]:
    """Reuse pack's Thompson picker. Do not fork pick_jobs."""
    market = _load_market()
    buf = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = market.cmd_pack(root, persona, True, offline, False, rng=rng)
    text = buf.getvalue() + err.getvalue()
    if rc != 0:
        return rc, {}, text.replace("gb market pack:", "gb stack:")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return EXIT_ENVIRONMENT, {}, text.replace("gb market pack:", "gb stack:")
    if not isinstance(doc, dict):
        return EXIT_ENVIRONMENT, {}, "gb stack: refuse — pack payload is not an object"
    return EXIT_OK, doc, ""


def seats_from_pack(pack: dict) -> List[dict]:
    seats: List[dict] = []
    for i, row in enumerate(list(pack.get("rows") or []), 1):
        seats.append(
            {
                "install_url": row.get("install_url"),
                "job": row.get("job"),
                "name": row.get("name"),
                "seat": i,
                "share_id": row.get("share_id"),
            }
        )
    return seats


def team_payload(pack: dict, persona: str, apply: bool, status: Dict[str, Any]) -> dict:
    seats = seats_from_pack(pack)
    hire_ok = bool(status.get("hire_proven"))
    room_ok = bool(status.get("room_proven"))
    if apply:
        verdict = "HIRED" if hire_ok and room_ok else "REFUSED"
    else:
        verdict = "PLAN"
    missing: List[str] = []
    human: List[str] = []
    if apply and not hire_ok:
        missing.append(MISSING_HIRE)
        human.append(HUMAN_HIRE)
    if apply and not room_ok:
        missing.append(MISSING_ROOM)
        human.append(HUMAN_ROOM)
    return {
        "applied": bool(apply),
        "blocked": list(pack.get("blocked") or []),
        "human": human,
        "jobs": list(pack.get("jobs") or []),
        "missing": missing,
        "persona": persona,
        "room": persona,
        "schema": SCHEMA,
        "seats": seats,
        "selector": pack.get("selector") or {},
        "verdict": verdict,
    }


def print_team_human(payload: dict) -> None:
    emit("ROOM %s" % (payload.get("room") or "-"))
    for seat in payload.get("seats") or []:
        sid = seat.get("share_id") or ""
        inst = seat.get("install_url") or ""
        line = "SEAT %s  %s  %s  %s" % (
            seat.get("seat"),
            seat.get("job") or "-",
            seat.get("name") or "-",
            sid,
        )
        if inst:
            line = "%s  INSTALL %s" % (line, inst)
        emit(line)
    for row in payload.get("blocked") or []:
        emit(
            "blocked  %s  %s  %s"
            % (row.get("job"), row.get("count"), row.get("reason") or "no share_id")
        )


def print_hire_refuse(payload: dict) -> None:
    emit("gb stack: REFUSED — share-install RPC is unproven")
    for item in payload.get("missing") or []:
        emit("  missing: %s" % item)
    for item in payload.get("human") or []:
        emit("  human: %s" % item)
    emit("  nothing hired")


def cmd_team(
    root: pathlib.Path,
    persona_token: Optional[str],
    as_json: bool,
    offline: bool = False,
    apply: bool = False,
    rng: Any = None,
    status: Optional[Dict[str, Any]] = None,
) -> int:
    market = _load_market()
    persona = market.normalize_pack_persona(persona_token)
    if persona is None or persona not in market.PACKS:
        return usage_persona()
    rc, pack, err = _pack_payload(root, persona, offline, rng=rng)
    if rc != 0:
        emit(err.rstrip() or "gb stack: refuse — catalog missing")
        return rc if rc in (EXIT_USAGE, EXIT_ENVIRONMENT, EXIT_REFUSED) else EXIT_ENVIRONMENT
    gate = status if status is not None else hire_rpc_status()
    payload = team_payload(pack, persona, apply, gate)
    if as_json:
        emit(json.dumps(payload, indent=1, sort_keys=True))
        if apply and payload.get("verdict") != "HIRED":
            return EXIT_REFUSED
        return EXIT_OK
    print_team_human(payload)
    if not apply:
        emit("PLAN  gb stack %s --apply   (hire gate; nothing hired)" % persona)
        return EXIT_OK
    if payload.get("verdict") == "HIRED":
        return EXIT_OK
    print_hire_refuse(payload)
    return EXIT_REFUSED


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    with tempfile.TemporaryDirectory(prefix="gb-stack-selftest-") as tmp:
        root = pathlib.Path(tmp)
        market_dir = root / "market"
        market_dir.mkdir()
        (market_dir / "2026-09-11T0600.json").write_text(
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

        rc, out = _capture(lambda: cmd_plugins(root, False, x_runner=fake_x))
        check("stack-ok-with-snapshot", rc == 0, str(rc))
        check("reprints-snapshot-plugin-total", "plugins  17" in out, out)
        check("does-not-invent-326", "326" not in out, out)
        check("lists-disk-skills", "alpha-skill" in out and "beta-skill" in out, out)
        check("names-gb-x", "gb x" in out and "distinct authors" in out, out)
        check("plugin-install-is-human", "Settings → Plugins" in out and "No install RPC" in out, out)
        check("share-id-limit", "lack share_id" in out, out)
        empty = root / "empty"
        empty.mkdir()
        erc, eout = _capture(lambda: cmd_plugins(empty, False, x_runner=fake_x))
        check("missing-snapshot-environment", erc == EXIT_ENVIRONMENT, str(erc))
        check("missing-names-refresh", "gb market refresh" in eout, eout)

        via = _capture(lambda: body(["plugins", "--root", str(root)]))
        check("plugins-cli-action", via[0] == 0 and "plugins  17" in via[1], via[1][:200])

        bare = _capture(lambda: body(["--root", str(root)]))
        check(
            "bare-stack-is-usage",
            bare[0] == EXIT_USAGE
            and "founder" in bare[1]
            and "engineer" in bare[1]
            and "seller" in bare[1]
            and "plugins" in bare[1],
            bare[1],
        )

        market = _load_market()
        mdb = market._market_db()
        mb = market._market_bandit()

        def sid(tag: str) -> str:
            token = (tag + "XXXXXXXXXXXXXXXXXXXXX")[:21]
            return token

        founder_sids = {
            "brief": sid("briefShare"),
            "calendar": sid("calenShare"),
            "spend": sid("spendShare"),
            "sell": sid("sellsShare"),
            "operate": sid("operShareX"),
        }
        categories = {
            "brief": "research-briefings",
            "calendar": "inbox-calendar",
            "spend": "finance-ops",
            "sell": "Sales",
            "operate": "Ops",
        }
        full_root = root / "full-pack"
        (full_root / "usecases").mkdir(parents=True)
        planted = [
            mdb.canonicalize_row(
                {
                    "name": "%s Desk" % job.title(),
                    "category": categories[job],
                    "share_id": founder_sids[job],
                    "from_shares": True,
                    "origin": "shares",
                    "charter": "%s charter" % job,
                }
            )
            for job in ("brief", "calendar", "spend", "sell", "operate")
        ]
        mdb.rebuild(full_root / "usecases" / mdb.DB_NAME, planted, [])
        plan = _capture(
            lambda: body(["founder", "--offline", "--root", str(full_root)])
        )
        check("founder-plan-exit-ok", plan[0] == 0, str(plan[0]) + plan[1][:200])
        check("founder-prints-room", "ROOM founder" in plan[1], plan[1][:300])
        seat_jobs = [
            line.split()[2]
            for line in plan[1].splitlines()
            if line.startswith("SEAT ")
        ]
        check(
            "founder-five-seats",
            plan[1].count("SEAT ") == 5
            and seat_jobs == ["brief", "calendar", "spend", "sell", "operate"],
            plan[1],
        )
        check(
            "founder-full-share-ids-and-install",
            all(founder_sids[j] in plan[1] for j in founder_sids)
            and all(
                ("INSTALL grokbot://app/v1/bot-template?id=%s" % founder_sids[j])
                in plan[1]
                for j in founder_sids
            ),
            plan[1],
        )
        check(
            "plan-is-not-a-hire",
            plan[0] == 0
            and "PLAN" in plan[1]
            and "REFUSED" not in plan[1]
            and "CreateGrokBotAgentFromTemplate" not in plan[1],
            plan[1],
        )

        pj = _capture(
            lambda: body(["founder", "--offline", "--json", "--root", str(full_root)])
        )
        payload = json.loads(pj[1]) if pj[1].strip().startswith("{") else {}
        check(
            "json-schema-v2",
            pj[0] == 0
            and payload.get("schema") == SCHEMA
            and payload.get("persona") == "founder"
            and payload.get("room") == "founder"
            and payload.get("verdict") == "PLAN"
            and payload.get("applied") is False
            and len(payload.get("seats") or []) == 5
            and (payload.get("selector") or {}).get("method") == "thompson"
            and (payload.get("selector") or {}).get("prior") == "beta(1,1)"
            and (payload.get("selector") or {}).get("candidate_cold") == mb.CANDIDATE_COLD
            and "weights" not in (payload.get("selector") or {}),
            str(payload)[:400],
        )
        check(
            "seats-identity-is-share-id",
            all(s.get("share_id") and len(str(s.get("share_id"))) == 21 for s in payload.get("seats") or []),
            str(payload.get("seats"))[:300],
        )

        applied = _capture(
            lambda: body(
                ["founder", "--apply", "--offline", "--root", str(full_root)]
            )
        )
        check(
            "apply-unproven-hire-is-refused",
            applied[0] == EXIT_REFUSED
            and "REFUSED" in applied[1]
            and MISSING_HIRE in applied[1]
            and HUMAN_ROOM in applied[1]
            and "nothing hired" in applied[1],
            applied[1],
        )
        check(
            "apply-is-not-install-only-success",
            applied[0] != 0
            and "CreateGrokBotAgentFromTemplate" not in applied[1]
            and "CreateGrokBotRoom" not in applied[1],
            applied[1],
        )
        aj = _capture(
            lambda: body(
                [
                    "founder",
                    "--apply",
                    "--offline",
                    "--json",
                    "--root",
                    str(full_root),
                ]
            )
        )
        apayload = json.loads(aj[1]) if aj[1].strip().startswith("{") else {}
        check(
            "apply-json-refused-not-ok",
            aj[0] == EXIT_REFUSED
            and apayload.get("verdict") == "REFUSED"
            and apayload.get("applied") is True
            and MISSING_HIRE in (apayload.get("missing") or [])
            and HUMAN_ROOM in (apayload.get("human") or []),
            str(apayload)[:400],
        )

        blocked_root = root / "blocked-pack"
        (blocked_root / "usecases").mkdir(parents=True)
        blocked_rows = [
            mdb.canonicalize_row(
                {
                    "name": "%s Desk" % job.title(),
                    "category": categories[job],
                    "share_id": founder_sids[job],
                    "from_shares": True,
                    "origin": "shares",
                }
            )
            for job in ("brief", "calendar", "spend", "operate")
        ]
        blocked_rows.append(
            mdb.canonicalize_row(
                {
                    "name": "Aaa Sales Ghost",
                    "category": "Sales",
                    "origin": "directory",
                }
            )
        )
        mdb.rebuild(blocked_root / "usecases" / mdb.DB_NAME, blocked_rows, [])
        blocked = _capture(
            lambda: body(
                ["founder", "--offline", "--json", "--root", str(blocked_root)]
            )
        )
        bpayload = json.loads(blocked[1]) if blocked[1].strip().startswith("{") else {}
        bjobs = [s.get("job") for s in bpayload.get("seats") or []]
        blocked_jobs = [b.get("job") for b in (bpayload.get("blocked") or [])]
        check(
            "blocked-job-not-filled",
            blocked[0] == 0
            and "sell" in blocked_jobs
            and "sell" not in bjobs
            and all(s.get("name") != "Aaa Sales Ghost" for s in bpayload.get("seats") or []),
            str({"seats": bjobs, "blocked": blocked_jobs}),
        )

        ban_root = root / "ban-pack"
        (ban_root / "usecases").mkdir(parents=True)
        banned_sid = sid("bannedOper")
        other_sid = sid("otherOperX")
        ban_rows = [
            mdb.canonicalize_row(
                {
                    "name": "%s Desk" % job.title(),
                    "category": categories[job],
                    "share_id": founder_sids[job],
                    "from_shares": True,
                    "origin": "shares",
                }
            )
            for job in ("brief", "calendar", "spend", "sell")
        ]
        ban_rows.extend(
            [
                mdb.canonicalize_row(
                    {
                        "name": "Banned Operate",
                        "category": "Ops",
                        "share_id": banned_sid,
                        "from_shares": True,
                        "origin": "shares",
                    }
                ),
                mdb.canonicalize_row(
                    {
                        "name": "Other Operate",
                        "category": "Ops",
                        "share_id": other_sid,
                        "from_shares": True,
                        "origin": "shares",
                    }
                ),
            ]
        )
        mdb.rebuild(ban_root / "usecases" / mdb.DB_NAME, ban_rows, [])
        store = mb.store_path(ban_root)
        mb.record_impression(
            store, job="operate", name_key="banned operate", share_id=banned_sid
        )
        mb.apply_verdict(store, banned_sid, "ban")
        banned = _capture(
            lambda: body(["founder", "--offline", "--json", "--root", str(ban_root)])
        )
        ban_payload = json.loads(banned[1]) if banned[1].strip().startswith("{") else {}
        ban_sids = [s.get("share_id") for s in ban_payload.get("seats") or []]
        check(
            "banned-arm-absent",
            banned[0] == 0 and banned_sid not in ban_sids and other_sid in ban_sids,
            str(ban_sids),
        )

        miss = _capture(
            lambda: body(["founder", "--offline", "--root", str(empty)])
        )
        check(
            "missing-catalog-is-environment",
            miss[0] == EXIT_ENVIRONMENT
            and "Traceback" not in miss[1]
            and miss[1].strip() != "",
            miss[1],
        )

        unknown = _capture(lambda: body(["wizard", "--root", str(full_root)]))
        check(
            "unknown-persona-is-usage",
            unknown[0] == EXIT_USAGE
            and "founder" in unknown[1]
            and "plugins" in unknown[1],
            unknown[1],
        )

        team_src = inspect.getsource(cmd_team) + inspect.getsource(_pack_payload)
        check(
            "reuses-pack-not-forked-pick-jobs",
            "cmd_pack" in team_src and "def pick_jobs" not in team_src,
            team_src[:200],
        )
        check(
            "no-hardcoded-winners-or-create-rpc",
            "job_winners" not in team_src
            and "CreateGrokBotAgentFromTemplate" not in team_src
            and "CreateChannel" not in team_src
            and "gb-swarm" not in team_src,
            team_src[:200],
        )
        check(
            "hire-rpc-unproven-by-default",
            HIRE_RPC is None and ROOM_RPC is None and not hire_rpc_status()["hire_proven"],
            str(hire_rpc_status()),
        )

        help_txt = _build_parser().format_help()
        check(
            "help-names-personas-plugins-apply",
            "founder" in help_txt
            and "plugins" in help_txt
            and "--apply" in help_txt
            and "CreateGrokBot" in help_txt,
            help_txt,
        )

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
    buf = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        code = fn()
    return code, buf.getvalue() + err.getvalue()


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="gb-stack.py",
        description=(
            "Hire plan for founder|engineer|seller (Thompson pack seats). "
            "`gb stack plugins` is the old census. "
            "--apply is the hire gate (refuses if CreateGrokBot / share-install is unproven)."
        ),
    )
    ap.add_argument(
        "persona",
        nargs="?",
        default="",
        help="founder | engineer | seller | plugins (sales → seller)",
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--apply",
        action="store_true",
        help=(
            "hire gate: create into this local account if a proven CreateGrokBot / "
            "share-install RPC exists; otherwise REFUSE (does not treat a printed URL as a hire)"
        ),
    )
    ap.add_argument(
        "--offline",
        action="store_true",
        help="valid catalog cache only (same refuse rules as gb market pack)",
    )
    ap.add_argument("--root", default="")
    ap.add_argument("--selftest", action="store_true")
    return ap


def body(argv: Optional[Sequence[str]] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    root = pathlib.Path(args.root) if args.root else ROOT
    token = str(getattr(args, "persona", "") or "").strip()
    if not token:
        return usage_persona()
    if token.casefold() == "plugins":
        return cmd_plugins(root, bool(args.json))
    return cmd_team(
        root,
        token,
        bool(args.json),
        bool(args.offline),
        bool(args.apply),
    )


if __name__ == "__main__":
    gbmain(lambda: body())
