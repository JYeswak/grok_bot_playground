#!/usr/bin/env python3
"""gb-stack — first-hour desk: skills on disk, plugin install URLs, existing gb methods.

Named persona lists only. Not a ranked champion. Not a plugin catalog dump.
Does not read market/<stamp>.json. Does not claim account install state.
Does not call an install RPC. --apply reprints the same cards and exits 0.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import main as gbmain  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-stack/2"
EXIT_OK, EXIT_USAGE = 0, 2
PLUGIN_ADD_PREFIX = "grokbot://app/v1/plugin/add?id="
# SearchPlugins, 2026-09-13. Do not invent ids. Do not add a fourth.
MEASURED_PLUGIN_IDS = {
    "45893410": "Gmail",
    "45893411": "Google Calendar",
    "48677658": "GitHub",
}
PERSONA_ALIASES = {
    "founder": "founder",
    "engineer": "engineer",
    "seller": "seller",
    "sales": "seller",
}
# Named lists only. Same persona ids as pack. Not a prior. Not a draw.
STACKS: Dict[str, Dict[str, Any]] = {
    "founder": {
        "jobs": ["brief", "calendar", "spend", "sell", "operate"],
        "skills": [
            "morning-briefing",
            "calendar-read",
            "inbox-triage",
            "expense-audit",
            "draft-message-desk",
        ],
        "plugins": [
            {"name": "Gmail", "plugin_id": "45893410"},
            {"name": "Google Calendar", "plugin_id": "45893411"},
        ],
        "methods": [
            {"verb": "gb market pack", "argv": ["founder"]},
            {"verb": "gb x", "argv": ["reclassify"]},
            {"verb": "gb findings", "argv": []},
            {"verb": "gb teach", "argv": ["forms"]},
            {"verb": "gb post", "argv": ["draft"]},
        ],
    },
    "engineer": {
        "jobs": ["ship", "operate", "brief", "handoff"],
        "skills": [
            "galaxy-engineering",
            "github-read",
            "pr-verify",
            "oncall-handoff",
            "standup-writer",
        ],
        "plugins": [
            {"name": "GitHub", "plugin_id": "48677658"},
            {"name": "Gmail", "plugin_id": "45893410"},
        ],
        "methods": [
            {"verb": "gb market pack", "argv": ["engineer"]},
            {"verb": "gb x", "argv": ["reclassify"]},
            {"verb": "gb findings", "argv": []},
            {"verb": "gb doctor", "argv": ["--scope", "gate"]},
        ],
    },
    "seller": {
        "jobs": ["sell", "market", "brief", "calendar"],
        "skills": [
            "galaxy-sdr-desk",
            "outbound-prospecting",
            "inbox-triage",
            "calendar-read",
            "first-reply-desk",
        ],
        "plugins": [
            {"name": "Gmail", "plugin_id": "45893410"},
            {"name": "Google Calendar", "plugin_id": "45893411"},
        ],
        "methods": [
            {"verb": "gb market pack", "argv": ["seller"]},
            {"verb": "gb x", "argv": ["reclassify"]},
            {"verb": "gb findings", "argv": []},
            {"verb": "gb post", "argv": ["draft"]},
        ],
    },
}
METHOD_ANSWERS = {
    ("gb market pack", ("founder",)): "persona shortlist of Thompson job draws",
    ("gb market pack", ("engineer",)): "persona shortlist of Thompson job draws",
    ("gb market pack", ("seller",)): "persona shortlist of Thompson job draws",
    ("gb x", ("reclassify",)): "practitioner methods from the X harvest",
    ("gb findings", ()): "measured teachable claims",
    ("gb teach", ("forms",)): "ranked teaching forms",
    ("gb post", ("draft",)): "draft a post from measured findings",
    ("gb doctor", ("--scope", "gate")): "the 24-check board over the live deployment",
}


def emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def dumps(payload: dict) -> str:
    return json.dumps(payload, indent=1, sort_keys=True)


def normalize_persona(token: Optional[str]) -> Optional[str]:
    """Accept founder|engineer|seller, plus sales → seller. Do not guess."""
    if token is None:
        return None
    key = str(token).strip().casefold()
    if not key:
        return None
    return PERSONA_ALIASES.get(key)


def plugin_install_url(plugin_id: str) -> str:
    """Click-to-install deep link. Refuse any id that is not one of the three measured."""
    pid = str(plugin_id or "")
    if pid not in MEASURED_PLUGIN_IDS:
        raise ValueError(
            "gb stack: refuse — plugin id is not one of the three measured ids"
        )
    return PLUGIN_ADD_PREFIX + pid


def skill_exists(root: pathlib.Path, slug: str) -> bool:
    return (root / "plugin" / "skills" / slug / "SKILL.md").is_file()


def x_harvest_note(root: pathlib.Path) -> Optional[str]:
    """Honest note when the X harvest a clone withholds is missing. Do not run gb x."""
    xdir = root / "x"
    plains: List[pathlib.Path] = []
    sweeps: List[pathlib.Path] = []
    if xdir.is_dir():
        for path in xdir.iterdir():
            if not path.is_file() or not path.name.endswith(".json"):
                continue
            if ".sweep." in path.name:
                sweeps.append(path)
            else:
                plains.append(path)
    if plains and sweeps:
        return None
    return (
        "ENVIRONMENT gb x reclassify: needs a collected X harvest under x/ "
        "(one plain and one .sweep artifact)"
    )


def method_command(row: dict) -> str:
    parts = [str(row.get("verb") or "")]
    parts.extend(str(a) for a in (row.get("argv") or []))
    return " ".join(p for p in parts if p)


def build_payload(root: pathlib.Path, persona: str) -> dict:
    spec = STACKS[persona]
    skills: List[str] = []
    blocked: List[dict] = []
    for slug in list(spec["skills"]):
        if skill_exists(root, slug):
            skills.append(slug)
        else:
            blocked.append(
                {
                    "kind": "skill",
                    "reason": "missing plugin/skills/%s/SKILL.md" % slug,
                    "slug": slug,
                }
            )
    plugins = []
    for row in list(spec["plugins"]):
        pid = str(row["plugin_id"])
        plugins.append(
            {
                "install_url": plugin_install_url(pid),
                "name": row["name"],
                "plugin_id": pid,
            }
        )
    methods = [
        {"argv": list(row["argv"]), "verb": row["verb"]} for row in list(spec["methods"])
    ]
    return {
        "blocked": blocked,
        "methods": methods,
        "persona": persona,
        "plugins": plugins,
        "schema": SCHEMA,
        "skills": skills,
    }


def print_stack_human(payload: dict, x_note: Optional[str]) -> None:
    emit("PERSONA %s" % (payload.get("persona") or "-"))
    for slug in payload.get("skills") or []:
        emit("SKILL %s" % slug)
    for plug in payload.get("plugins") or []:
        emit("PLUGIN %s" % (plug.get("name") or "-"))
        emit("INSTALL %s" % (plug.get("install_url") or ""))
    for row in payload.get("methods") or []:
        cmd = method_command(row)
        key = (row.get("verb") or "", tuple(row.get("argv") or []))
        answer = METHOD_ANSWERS.get(key, "")
        if answer:
            emit("METHOD %s  %s" % (cmd, answer))
        else:
            emit("METHOD %s" % cmd)
        if row.get("verb") == "gb x" and x_note:
            emit(x_note)
    for row in payload.get("blocked") or []:
        emit(
            "blocked  %s  %s  %s"
            % (
                row.get("kind") or "skill",
                row.get("slug") or "-",
                row.get("reason") or "missing",
            )
        )


def cmd_stack(
    root: pathlib.Path,
    persona_token: Optional[str],
    as_json: bool,
    apply: bool = False,
) -> int:
    # --apply reprints the same cards including INSTALL. No RPC.
    del apply
    persona = normalize_persona(persona_token)
    if persona is None or persona not in STACKS:
        emit("gb stack: refuse — persona is founder, engineer, or seller")
        return EXIT_USAGE
    payload = build_payload(root, persona)
    if as_json:
        emit(dumps(payload))
        return EXIT_OK
    print_stack_human(payload, x_harvest_note(root))
    return EXIT_OK


def _plant_skill(root: pathlib.Path, slug: str) -> None:
    path = root / "plugin" / "skills" / slug
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text("---\nname: %s\n---\n" % slug)


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    import inspect

    prod = (
        inspect.getsource(cmd_stack)
        + inspect.getsource(build_payload)
        + inspect.getsource(plugin_install_url)
        + inspect.getsource(print_stack_human)
        + inspect.getsource(normalize_persona)
        + inspect.getsource(x_harvest_note)
    )
    lists_blob = json.dumps(STACKS, sort_keys=True)
    check("schema-is-v2", SCHEMA == "gb-stack/2", SCHEMA)
    banned_id = "97" + "17366"
    check("pstack-id-absent", banned_id not in prod and banned_id not in lists_blob, banned_id)
    banned_name = "pst" + "ack"
    check(
        "pstack-name-absent",
        banned_name not in prod.lower() and banned_name not in lists_blob.lower(),
        banned_name,
    )
    check("no-install-rpc-name", "InstallPlugin" not in prod, "InstallPlugin leaked")
    check("no-create-bot-rpc", "CreateGrokBot" not in prod, "CreateGrokBot leaked")
    check(
        "no-swarm-invoke",
        "gb-swarm" not in prod and "gb swarm" not in prod,
        prod[:200],
    )
    check(
        "hello-computer-not-listed",
        all("hello-computer" not in list(STACKS[p]["skills"]) for p in STACKS),
        str({p: STACKS[p]["skills"] for p in STACKS}),
    )
    listed_ids = {
        str(row["plugin_id"])
        for spec in STACKS.values()
        for row in spec["plugins"]
    }
    check(
        "only-three-measured-plugin-ids",
        listed_ids == set(MEASURED_PLUGIN_IDS),
        str(listed_ids),
    )
    try:
        plugin_install_url("999")
        refused_unknown = False
    except ValueError as exc:
        refused_unknown = "three measured" in str(exc)
    try:
        plugin_install_url("9717366")
        refused_pstack = False
    except ValueError:
        refused_pstack = True
    check("plugin-id-refuse-unknown", refused_unknown, "999")
    check("plugin-id-refuse-unmeasured", refused_pstack, "unmeasured id")
    check(
        "plugin-id-gmail",
        plugin_install_url("45893410")
        == "grokbot://app/v1/plugin/add?id=45893410",
        plugin_install_url("45893410"),
    )

    with tempfile.TemporaryDirectory(prefix="gb-stack-selftest-") as tmp:
        root = pathlib.Path(tmp)
        for slug in (
            "morning-briefing",
            "calendar-read",
            "inbox-triage",
            "expense-audit",
            "draft-message-desk",
        ):
            _plant_skill(root, slug)
        extra = root / "plugin" / "skills" / "alpha-skill"
        extra.mkdir()
        (extra / "SKILL.md").write_text("---\nname: alpha-skill\n---\n")
        gate = root / "plugin" / "skills" / "hello-computer"
        gate.mkdir()
        (gate / "SKILL.md").write_text("---\nname: hello-computer\n---\n")

        rc, out = _capture(lambda: cmd_stack(root, "founder", False))
        check("founder-ok-without-market", rc == 0, str(rc) + out[:200])
        check("founder-persona-line", "PERSONA founder" in out, out)
        check(
            "founder-listed-skills",
            all(
                ("SKILL %s" % s) in out
                for s in (
                    "morning-briefing",
                    "calendar-read",
                    "inbox-triage",
                    "expense-audit",
                    "draft-message-desk",
                )
            ),
            out,
        )
        check("founder-no-extra-skill", "alpha-skill" not in out, out)
        check("founder-no-hello-computer", "hello-computer" not in out, out)
        check(
            "founder-gmail-install",
            "PLUGIN Gmail" in out
            and "INSTALL grokbot://app/v1/plugin/add?id=45893410" in out,
            out,
        )
        check(
            "founder-calendar-install",
            "PLUGIN Google Calendar" in out
            and "INSTALL grokbot://app/v1/plugin/add?id=45893411" in out,
            out,
        )
        check("no-plugin-catalog-total", "plugins_total" not in out and "plugins  " not in out, out)
        check("no-settings-only-path", "Settings → Plugins" not in out, out)
        check(
            "no-installed-claim",
            "installed" not in out.lower() and "connected" not in out.lower(),
            out,
        )
        check("no-x-preview-dump", "distinct authors" not in out and "x_preview" not in out, out)
        check("founder-names-x-method", "METHOD gb x reclassify" in out, out)
        check(
            "x-environment-when-no-harvest",
            "ENVIRONMENT" in out and "X harvest" in out,
            out,
        )
        check("founder-names-pack", "METHOD gb market pack founder" in out, out)
        check("founder-names-findings", "METHOD gb findings" in out, out)
        check("founder-names-teach", "METHOD gb teach forms" in out, out)
        check("founder-names-post", "METHOD gb post draft" in out, out)

        apply_rc, apply_out = _capture(lambda: cmd_stack(root, "founder", False, apply=True))
        check("apply-exits-0", apply_rc == 0, str(apply_rc))
        check(
            "apply-prints-install",
            "INSTALL grokbot://app/v1/plugin/add?id=45893410" in apply_out
            and "INSTALL grokbot://app/v1/plugin/add?id=45893411" in apply_out,
            apply_out,
        )
        check("apply-same-persona", "PERSONA founder" in apply_out, apply_out)

        jrc, jout = _capture(lambda: cmd_stack(root, "founder", True))
        payload = json.loads(jout) if jout.strip().startswith("{") else {}
        check("json-schema-v2", jrc == 0 and payload.get("schema") == "gb-stack/2", jout[:200])
        check("json-persona", payload.get("persona") == "founder", str(payload)[:200])
        check(
            "json-skills-only-listed",
            payload.get("skills")
            == [
                "morning-briefing",
                "calendar-read",
                "inbox-triage",
                "expense-audit",
                "draft-message-desk",
            ],
            str(payload.get("skills")),
        )
        plugs = payload.get("plugins") or []
        check(
            "json-plugins-shape",
            plugs
            == [
                {
                    "install_url": "grokbot://app/v1/plugin/add?id=45893410",
                    "name": "Gmail",
                    "plugin_id": "45893410",
                },
                {
                    "install_url": "grokbot://app/v1/plugin/add?id=45893411",
                    "name": "Google Calendar",
                    "plugin_id": "45893411",
                },
            ],
            str(plugs),
        )
        meths = payload.get("methods") or []
        check(
            "json-methods-shape",
            meths
            == [
                {"argv": ["founder"], "verb": "gb market pack"},
                {"argv": ["reclassify"], "verb": "gb x"},
                {"argv": [], "verb": "gb findings"},
                {"argv": ["forms"], "verb": "gb teach"},
                {"argv": ["draft"], "verb": "gb post"},
            ],
            str(meths),
        )
        check("json-blocked-empty", payload.get("blocked") == [], str(payload.get("blocked")))
        check(
            "json-no-catalog-fields",
            "plugins_total" not in payload
            and "snapshot" not in payload
            and "x_preview" not in payload,
            str(sorted(payload)),
        )

        unknown = _capture(lambda: cmd_stack(root, "intern", False))
        check(
            "unknown-persona-refuses",
            unknown[0] == EXIT_USAGE
            and "founder" in unknown[1]
            and "engineer" in unknown[1]
            and "seller" in unknown[1],
            unknown[1],
        )
        missing = _capture(lambda: cmd_stack(root, None, False))
        check(
            "missing-persona-refuses",
            missing[0] == EXIT_USAGE and "founder" in missing[1],
            missing[1],
        )
        sales = _capture(lambda: cmd_stack(root, "sales", True))
        sales_payload = json.loads(sales[1]) if sales[1].strip().startswith("{") else {}
        check(
            "sales-alias-seller",
            sales[0] == 0 and sales_payload.get("persona") == "seller",
            str(sales_payload)[:200] + sales[1][:200],
        )

        thin = root / "thin"
        for slug in (
            "morning-briefing",
            "calendar-read",
            "inbox-triage",
            "draft-message-desk",
        ):
            _plant_skill(thin, slug)
        blocked_rc, blocked_out = _capture(lambda: cmd_stack(thin, "founder", False))
        blocked_json = _capture(lambda: cmd_stack(thin, "founder", True))
        blocked_payload = (
            json.loads(blocked_json[1]) if blocked_json[1].strip().startswith("{") else {}
        )
        check("missing-skill-still-ok", blocked_rc == 0, str(blocked_rc))
        check(
            "missing-skill-blocked-human",
            "blocked  skill  expense-audit" in blocked_out
            and "SKILL expense-audit" not in blocked_out,
            blocked_out,
        )
        check(
            "missing-skill-blocked-json",
            blocked_json[0] == 0
            and "expense-audit" not in (blocked_payload.get("skills") or [])
            and any(
                b.get("slug") == "expense-audit" and b.get("kind") == "skill"
                for b in (blocked_payload.get("blocked") or [])
            ),
            str(blocked_payload),
        )

        eng = root / "engineer"
        for slug in (
            "galaxy-engineering",
            "github-read",
            "pr-verify",
            "oncall-handoff",
            "standup-writer",
        ):
            _plant_skill(eng, slug)
        erc, eout = _capture(lambda: cmd_stack(eng, "engineer", False))
        check("engineer-ok", erc == 0, str(erc))
        check(
            "engineer-github-install",
            "PLUGIN GitHub" in eout
            and "INSTALL grokbot://app/v1/plugin/add?id=48677658" in eout,
            eout,
        )
        check("engineer-names-doctor", "METHOD gb doctor --scope gate" in eout, eout)
        check("engineer-no-hello-first", not eout.strip().startswith("SKILL hello-computer"), eout)

        cli = _capture(lambda: body(["founder", "--root", str(root)]))
        check("cli-founder", cli[0] == 0 and "PERSONA founder" in cli[1], cli[1][:200])
        cli_apply = _capture(lambda: body(["founder", "--apply", "--root", str(root)]))
        check(
            "cli-apply-exits-0-with-install",
            cli_apply[0] == 0
            and "INSTALL grokbot://app/v1/plugin/add?id=45893410" in cli_apply[1],
            cli_apply[1][:300],
        )
        cli_json = _capture(lambda: body(["founder", "--json", "--root", str(root)]))
        cli_payload = json.loads(cli_json[1]) if cli_json[1].strip().startswith("{") else {}
        check(
            "cli-json",
            cli_json[0] == 0 and cli_payload.get("schema") == "gb-stack/2",
            cli_json[1][:200],
        )

    failed = [n for n, ok, d in legs if not ok]
    for name, ok, detail in legs:
        if not ok:
            emit("FAIL %s: %s" % (name, detail[:240]))
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
        prog="gb-stack.py",
        description=(
            "gb stack founder prints the desk (skills + plugin install URLs + methods). "
            "Named persona lists only."
        ),
    )
    ap.add_argument(
        "persona",
        nargs="?",
        default="",
        help="founder | engineer | seller (sales → seller)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="print the same cards including INSTALL (does not call an install RPC)",
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    root = pathlib.Path(args.root) if args.root else ROOT
    return cmd_stack(root, args.persona, bool(args.json), apply=bool(args.apply))


if __name__ == "__main__":
    gbmain(lambda: body())
