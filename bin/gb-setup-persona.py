#!/usr/bin/env python3
"""gb-setup-persona — the persona walk: machine setup first, persona second.

Composes with `gb setup` (which owns preflight, device registration, and the
first measurements) rather than duplicating it. This walk PRINTS a tailored
procedure per persona; it never acts. --apply is refused (exit 5) and
names the print-only plan (`gb setup --persona first-hour`, or the
given --persona). A future --apply may execute the [mechanical] steps;
it does not exist yet.

Personas are DISCOVERED from personas/*.json at runtime — never hardcoded. A new
pack file appears in --list with zero code changes. A pack that fails schema
validation is skipped with a warning on stderr, never a crash (for --list and
the picker). Rendering (--persona) validates every referenced id against the
repo — templates/<id>.json, plugin/skills/<dir>/SKILL.md, mcp-servers/<name>/ or
a known first-party connector — and a stale reference fails loudly (exit 1)
instead of printing stale steps.

Usage:
  gb-setup-persona.py --list
  gb-setup-persona.py --persona <id> [--json]
  gb-setup-persona.py                  # interactive picker
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import nearest  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "personas"
TEMPLATES = ROOT / "templates"
SKILLS = ROOT / "plugin" / "skills"
MCP_SERVERS = ROOT / "mcp-servers"

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_USAGE = 2
EXIT_REFUSED = 5
SCHEMA = "gb-setup-persona/1"


FIRST_PARTY: Dict[str, str] = {
    "gmail": "Gmail (first-party OAuth connector, installed in-app)",
    "calendar": "Google Calendar (first-party OAuth connector, installed in-app)",
    "drive": "Google Drive (first-party OAuth connector, installed in-app)",
    "github": "GitHub (first-party OAuth connector, installed in-app)",
    "x": "X (first-party OAuth connector, installed in-app)",
    "notion": "Notion (first-party OAuth connector, installed in-app)",
}

POLICY_WHERE = (
    "desktop app Settings, per desktop (Execution-on-Local-Computer plus "
    "Auto-review rules; cf. policy/brain.json: ask-every-time and the five "
    "require-approval rules — external send, spend, publish, delete, prod change)"
)

REQUIRED_STR = ("id", "name", "tagline", "audience")
REQUIRED_LIST = (
    "bots",
    "skills",
    "routines",
    "mcps",
    "policies",
    "setup_order",
    "human_steps",
)


class Drift(Exception):
    """A persona references an id the repo no longer contains."""


def warn(msg: str) -> None:
    print(f"gb-setup-persona.py: warning: {msg}", file=sys.stderr)


def _few_ids(ids: List[str], prefer: str, n: int = 6) -> str:
    """Short known-id preview. Prefer the first-hour pack so a typo still names it."""
    ordered: List[str] = []
    if prefer in ids:
        ordered.append(prefer)
    for item in ids:
        if item not in ordered:
            ordered.append(item)
        if len(ordered) >= n:
            break
    extra = len(ids) - len(ordered)
    if not ordered:
        return "(none)"
    text = ", ".join(ordered)
    if extra > 0:
        text += f", ... +{extra} more"
    return text


def is_str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def validate_pack(data: Any, source: str) -> Optional[Dict[str, Any]]:
    """Schema-check one parsed pack. Returns the pack or None (warned, skipped)."""
    if not isinstance(data, dict):
        warn(f"{source}: not a JSON object — skipped")
        return None
    for k in REQUIRED_STR:
        if not isinstance(data.get(k), str) or not data[k]:
            warn(f"{source}: missing/non-empty string key {k!r} — skipped")
            return None
    for k in REQUIRED_LIST:
        if not isinstance(data.get(k), list):
            warn(f"{source}: missing/list key {k!r} — skipped")
            return None
    for i, b in enumerate(data["bots"]):
        if (
            not isinstance(b, dict)
            or not isinstance(b.get("template"), str)
            or not isinstance(b.get("why"), str)
        ):
            warn(f"{source}: bots[{i}] needs {{template, why}} strings — skipped")
            return None
    for i, r in enumerate(data["routines"]):
        if (
            not isinstance(r, dict)
            or not isinstance(r.get("name"), str)
            or not isinstance(r.get("schedule"), str)
            or not isinstance(r.get("owner_bot"), str)
            or not isinstance(r.get("prompt_hint"), str)
        ):
            warn(
                f"{source}: routines[{i}] needs {{name, schedule, owner_bot, "
                "prompt_hint}} strings — skipped"
            )
            return None
    for k in ("skills", "mcps", "policies", "setup_order", "human_steps"):
        if not is_str_list(data[k]):
            warn(f"{source}: {k} must be a list of strings — skipped")
            return None
    return data


def discover() -> Tuple[List[Dict[str, Any]], List[str]]:
    """Load all valid packs, sorted by id. Returns (packs, skipped_sources)."""
    packs: List[Dict[str, Any]] = []
    skipped: List[str] = []
    if not PERSONA_DIR.is_dir():
        warn(f"{PERSONA_DIR} does not exist — no personas")
        return packs, skipped
    for path in sorted(PERSONA_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            warn(f"{path.name}: unreadable ({e}) — skipped")
            skipped.append(path.name)
            continue
        pack = validate_pack(data, path.name)
        if pack is None:
            skipped.append(path.name)
            continue
        if pack["id"] != path.stem:
            warn(f"{path.name}: id {pack['id']!r} != filename {path.stem!r} — skipped")
            skipped.append(path.name)
            continue
        packs.append(pack)
    packs.sort(key=lambda p: str(p["id"]))
    return packs, skipped


def load_template(tid: str) -> Dict[str, Any]:
    path = TEMPLATES / f"{tid}.json"
    if not path.is_file():
        raise Drift(f"template {tid!r}: {path} does not exist (renamed or removed?)")
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise Drift(f"template {tid!r}: unreadable ({e})")
    if not isinstance(data, dict):
        raise Drift(f"template {tid!r}: not a JSON object")
    return data


def check_skill(sid: str) -> None:
    path = SKILLS / sid / "SKILL.md"
    if not path.is_file():
        raise Drift(f"skill {sid!r}: {path} does not exist (renamed or removed?)")


def classify_mcp(server: str) -> Tuple[str, str]:
    """Return (kind, description). Raise Drift when the repo knows neither."""
    if (MCP_SERVERS / server).is_dir():
        return "local", f"{server} (repo source: mcp-servers/{server}/)"
    if server in FIRST_PARTY:
        return "first-party", FIRST_PARTY[server]
    raise Drift(
        f"mcp {server!r}: neither mcp-servers/{server}/ nor a known "
        f"first-party connector ({sorted(FIRST_PARTY)})"
    )


def validate_refs(pack: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Resolve every pack reference. Returns template data keyed by template id."""
    for s in pack["skills"]:
        check_skill(s)
    for m in pack["mcps"]:
        classify_mcp(m)
    out: Dict[str, Dict[str, Any]] = {}
    for b in pack["bots"]:
        out[b["template"]] = load_template(b["template"])
    return out


def routine_text(tdata: Dict[str, Any]) -> str:
    r = tdata.get("routine") or {}
    if not isinstance(r, dict):
        return "(no routine block in template)"
    return (
        f"cadence {r.get('cadence', '?')} · when {r.get('when', 'on demand')} · "
        f"prompt: {r.get('prompt', '(no prompt in template)')} "
        f"· writes: {r.get('writes', '(unspecified)')}"
    )


Step = Dict[str, Any]


def build_steps(pack: Dict[str, Any], tdata: Dict[str, Dict[str, Any]]) -> List[Step]:
    steps: List[Step] = []
    n = 0

    def add(kind: str, title: str, detail: str, command: Optional[str] = None) -> None:
        nonlocal n
        n += 1
        s: Step = {"n": n, "kind": kind, "title": title, "detail": detail}
        if command is not None:
            s["command"] = command
        steps.append(s)

    add(
        "mechanical",
        "Machine setup first",
        "Run the machine walk before any persona step — preflight, device registration, "
        "first measurements. The persona walk assumes a measured instance.",
        "gb setup  # plan; then: gb setup --apply",
    )

    deployed: List[str] = []
    for sid in pack["setup_order"]:
        if (
            sid.startswith("deploy-")
            and (TEMPLATES / f"{sid[len('deploy-'):]}.json").is_file()
        ):
            t = sid[len("deploy-") :]
            deployed.append(t)
            why = next((b["why"] for b in pack["bots"] if b["template"] == t), "")
            add(
                "mechanical",
                f"Read the {t} charter",
                f"Why this Bot: {why}"
                if why
                else "See the pack's bots[] entry for why.",
                f"gb templates show {t}",
            )
            add(
                "mechanical",
                f"Deploy {t} (dry run, then real)",
                "Dry run first; --apply only when the plan names the right Bot.",
                f"gb templates deploy {t}  # then: gb templates deploy {t} --apply",
            )
        else:
            add(
                "human",
                f"Setup step: {sid}",
                "Pack-ordered step with no direct gb command — do it in-app/in person in "
                "setup_order sequence. Proof: the next step's prerequisite holds.",
                None,
            )
    for b in pack["bots"]:
        if b["template"] not in deployed:
            add(
                "mechanical",
                f"Read the {b['template']} charter",
                f"Why this Bot: {b['why']}",
                f"gb templates show {b['template']}",
            )
            add(
                "mechanical",
                f"Deploy {b['template']} (dry run, then real)",
                "Dry run first; --apply only when the plan names the right Bot.",
                f"gb templates deploy {b['template']}  # then: "
                f"gb templates deploy {b['template']} --apply",
            )

    for s in pack["skills"]:
        add(
            "human",
            f"Enable skill: {s}",
            f"In-app: enable the {s} skill on the owning Bot. Proof: ask the Bot "
            f"what its rule forbids, and it cites the skill.",
            None,
        )

    for r in pack["routines"]:
        owner = r["owner_bot"]
        txt = (
            routine_text(tdata[owner])
            if owner in tdata
            else (
                "(owner is a Bot name, not a template — schedule it on that Bot in-app)"
            )
        )
        add(
            "human",
            f"Schedule routine: {r['name']} ({r['schedule']}, owner {owner})",
            f"Hint: {r['prompt_hint']} Template routine: {txt} "
            f"In-app: create the scheduled run with exactly that prompt. "
            f"Proof: one dated run lands in-thread on schedule.",
            None,
        )

    for m in pack["mcps"]:
        kind, resolved = classify_mcp(m)
        if kind == "local":
            add(
                "mechanical",
                f"Verify MCP server: {m}",
                f"{resolved} Proof-call its tools from the account; cut an allowlist "
                f"to least privilege.",
                f"gb mcp list && gb mcp tools {m}",
            )
        else:
            add(
                "human",
                f"Install connector: {resolved}",
                "In-app OAuth install on the Bot that needs it. "
                "Proof: the connector appears in the Bot's installed list.",
                None,
            )

    for pol in pack["policies"]:
        add(
            "human",
            f"Set policy: {pol}",
            f"Where: {POLICY_WHERE}. Proof: trigger the boundary once (e.g. ask for an "
            f"external send) and confirm the Bot asks first.",
            None,
        )
    for h in pack["human_steps"]:
        add("human", "Human-only step", h, None)
    add(
        "mechanical",
        "Verify the persona is live",
        "One call: roster, routines firing, no new ERRORs.",
        "gb triage  # then: gb health",
    )
    return steps


def render_text(pack: Dict[str, Any], steps: List[Step]) -> str:
    lines = [
        f"# {pack['name']} — {pack['tagline']}",
        f"Audience: {pack['audience']}",
        "",
        "DRY-RUN PHILOSOPHY: this walk PRINTS. [mechanical] steps show the exact",
        "command for you (or a future --apply) to run; [human] steps need your hands",
        "in-app and name their proof. Nothing here executes anything.",
        "",
    ]
    for s in steps:
        cmd = f"\n  $ {s['command']}" if "command" in s else ""
        lines.append(f"{s['n']}. [{s['kind']}] {s['title']}\n   {s['detail']}{cmd}")
    return "\n".join(lines) + "\n"


def envelope(pack: Dict[str, Any], steps: List[Step]) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "tool": "gb",
        "command": "setup",
        "persona": pack["id"],
        "name": pack["name"],
        "dry_run": True,
        "steps": steps,
    }


def list_envelope(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "tool": "gb",
        "command": "setup",
        "personas": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gb-setup-persona.py",
        description="Print the tailored setup walk for one persona.",
    )
    ap.add_argument("--list", action="store_true", help="list personas with taglines")
    ap.add_argument(
        "--persona", metavar="ID", help="render the procedure for one persona"
    )
    ap.add_argument(
        "--json", action="store_true", help="machine-readable envelope on stdout"
    )
    ap.add_argument("--apply", action="store_true", help="NOT IMPLEMENTED (refused)")
    args = ap.parse_args(argv)

    if args.apply:
        pid = args.persona or "first-hour"
        plan = f"gb setup --persona {pid}"
        print(
            "gb-setup-persona.py: --apply is refused — this walk only prints.\n"
            f"    plan first:  {plan}\n"
            "    (drop --apply)",
            file=sys.stderr,
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "tool": "gb",
                        "command": "setup",
                        "status": "REFUSED",
                        "applied": False,
                        "error": "--apply is refused; persona walk only prints",
                        "hint": plan,
                        "plan": plan,
                        "did_you_mean": plan,
                    },
                    indent=1,
                )
            )
        return EXIT_REFUSED

    if args.list:
        packs, _ = discover()
        rows = [
            {"id": p["id"], "name": p["name"], "tagline": p["tagline"]} for p in packs
        ]
        if args.json:
            print(json.dumps(list_envelope(rows), indent=1))
        else:
            for r in rows:
                print(f"{r['id']:24} {r['name']:22} {r['tagline']}")
        return EXIT_OK

    if args.persona is not None:
        packs, _ = discover()
        pack = next((p for p in packs if p["id"] == args.persona), None)
        if pack is None:
            known = [str(p["id"]) for p in packs]
            preview = _few_ids(known, "first-hour")
            listing = "gb setup --list-personas"
            near = nearest(args.persona, known)
            hint = (
                f"gb setup --persona {near}"
                if near
                else "gb setup --persona first-hour"
            )
            print(
                f"gb-setup-persona.py: unknown persona {args.persona!r}. "
                f"Known packs: {preview}",
                file=sys.stderr,
            )
            print(f"    did you mean:  {hint}", file=sys.stderr)
            print(f"    list: {listing}", file=sys.stderr)
            if args.json:
                print(
                    json.dumps(
                        {
                            "schema": SCHEMA,
                            "tool": "gb",
                            "command": "setup",
                            "status": "USAGE",
                            "error": f"unknown persona {args.persona!r}",
                            "hint": hint,
                            "did_you_mean": hint,
                        },
                        indent=1,
                    )
                )
            return EXIT_USAGE
        try:
            tdata = validate_refs(pack)
            steps = build_steps(pack, tdata)
        except Drift as e:
            print(f"gb-setup-persona.py: DRIFT: {e}", file=sys.stderr)
            return EXIT_DRIFT
        if args.json:
            print(json.dumps(envelope(pack, steps), indent=1))
        else:
            print(render_text(pack, steps), end="")
        return EXIT_OK

    if args.json:
        print("gb-setup-persona.py: --json needs --list or --persona", file=sys.stderr)
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "tool": "gb",
                    "command": "setup",
                    "status": "USAGE",
                    "error": "--json needs --list or --persona",
                },
                indent=1,
            )
        )
        return EXIT_USAGE

    packs, _ = discover()
    if not packs:
        print("gb-setup-persona.py: no valid personas found.", file=sys.stderr)
        return EXIT_DRIFT
    print("Pick a persona:")
    for i, p in enumerate(packs, 1):
        print(f"  {i:2}. {p['id']:24} {p['tagline']}")
    try:
        raw = input("number> ").strip()
    except EOFError:
        print("\ngb-setup-persona.py: no selection.", file=sys.stderr)
        return EXIT_USAGE
    try:
        idx = int(raw)
    except ValueError:
        print(f"gb-setup-persona.py: {raw!r} is not a number.", file=sys.stderr)
        return EXIT_USAGE
    if not 1 <= idx <= len(packs):
        print(f"gb-setup-persona.py: pick 1..{len(packs)}.", file=sys.stderr)
        return EXIT_USAGE
    pack = packs[idx - 1]
    try:
        tdata = validate_refs(pack)
        steps = build_steps(pack, tdata)
    except Drift as e:
        print(f"gb-setup-persona.py: DRIFT: {e}", file=sys.stderr)
        return EXIT_DRIFT
    print(render_text(pack, steps), end="")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
