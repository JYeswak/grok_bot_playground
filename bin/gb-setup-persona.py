#!/usr/bin/env python3
"""gb-setup-persona — the persona walk: machine setup first, persona second.

Composes with `gb setup` (which owns preflight, device registration, and the
first measurements) rather than duplicating it. This walk PRINTS a tailored
procedure per persona; it never acts. --apply is refused (exit 5) and
names the print-only plan (`gb setup --persona founder-operator`, or the
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
import contextlib
import copy
import hashlib
import io
import json
import pathlib
import shlex
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import nearest  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "personas"
TEMPLATES = ROOT / "templates"

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_USAGE = 2
EXIT_REFUSED = 5
SCHEMA = "gb-setup-persona/2"
CAPABILITY_SCHEMA = "gb-persona-capabilities/1"
SELFTEST_SCHEMA = "gb-setup-persona-selftest/1"


FIRST_PARTY = frozenset(("gmail", "calendar", "drive", "github", "x", "notion"))

CAPABILITY_SURFACES: Dict[str, str] = {
    "first_party_oauth_connector": "grok_bot_settings_oauth",
    "server_recipe_skill": "grok_bot_recipe",
    "disk_private_skill": "grok_bot_private_skill_directory",
    "repo_plugin_skill": "repo_plugin_artifact",
    "marketplace_skill_pack": "shared_cloud_cli",
    "hosted_mcp_server": "hosted_mcp",
}
LEGACY_CAPABILITY_KEYS = ("skills", "mcps", "tools", "capabilities", "setup_order")
GENERIC_CAPABILITY_TOKENS = frozenset(
    ("skill", "skills", "plugin", "one-plugin", "connector", "mcp", "mcps", "core-mcps")
)
POLICY_WHERE = (
    "desktop app Settings, per desktop (Execution-on-Local-Computer plus "
    "Auto-review rules; cf. policy/brain.json: ask-every-time and the five "
    "require-approval rules — external send, spend, publish, delete, prod change)"
)

REQUIRED_STR = ("id", "name", "tagline", "audience")
REQUIRED_LIST = (
    "bots",
    "routines",
    "policies",
    "human_steps",
)


class Drift(Exception):
    """A persona references an id the repo no longer contains."""


def warn(msg: str) -> None:
    print(f"gb-setup-persona.py: warning: {msg}", file=sys.stderr)


def _few_ids(ids: List[str], prefer: str, n: int = 6) -> str:
    """Short known-id preview. Prefer a still-active pack so a typo still names it."""
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


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def is_nonempty_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(_nonempty_string(item) for item in value)


def _string_dict(value: Any, keys: Tuple[str, ...]) -> bool:
    return isinstance(value, dict) and all(
        _nonempty_string(value.get(key)) for key in keys
    )


def _warn_invalid(source: str, field: str, message: str) -> None:
    warn(f"{source}: {field}: {message} — skipped")


def _validate_capability(
    row: Any, index: int, source: str, bot_ids: set[str], seen: set[str]
) -> bool:
    field = f"capability_manifest.capabilities[{index}]"
    if not isinstance(row, dict):
        _warn_invalid(source, field, "must be an object")
        return False
    required = (
        "id",
        "kind",
        "targets",
        "source",
        "surface",
        "boundary",
        "least_privilege",
        "proof",
        "approval",
        "recovery",
        "resume",
        "depends_on",
    )
    missing = [key for key in required if key not in row]
    if missing:
        _warn_invalid(source, field, f"missing fields {missing}")
        return False
    serialized = json.dumps(row, sort_keys=True).lower()
    if any(
        marker in serialized
        for marker in ("sk_live_", "ghp_", "xoxb-", "bearer ey", "api_key=", "token=")
    ):
        _warn_invalid(source, field, "credential-shaped data is forbidden")
        return False

    capability_id = row["id"]
    kind = row["kind"]
    if (
        not _nonempty_string(capability_id)
        or capability_id.strip().lower() in GENERIC_CAPABILITY_TOKENS
    ):
        _warn_invalid(source, f"{field}.id", "needs a stable, non-generic identity")
        return False
    if capability_id in seen:
        _warn_invalid(source, f"{field}.id", f"duplicate identity {capability_id!r}")
        return False
    seen.add(capability_id)
    if kind not in CAPABILITY_SURFACES:
        _warn_invalid(source, f"{field}.kind", f"unknown kind {kind!r}")
        return False

    targets = row["targets"]
    if (
        not is_nonempty_str_list(targets)
        or not targets
        or len(set(targets)) != len(targets)
    ):
        _warn_invalid(source, f"{field}.targets", "needs unique logical Bot ids")
        return False
    unknown_targets = [target for target in targets if target not in bot_ids]
    if unknown_targets:
        _warn_invalid(
            source, f"{field}.targets", f"unknown logical Bot ids {unknown_targets}"
        )
        return False

    source_spec = row["source"]
    if not _string_dict(source_spec, ("authority", "locator")):
        _warn_invalid(source, f"{field}.source", "needs authority and locator strings")
        return False
    has_version = _nonempty_string(source_spec.get("version"))
    resolution = source_spec.get("resolution")
    has_resolution = _string_dict(resolution, ("mode", "receipt"))
    if has_version == has_resolution:
        _warn_invalid(
            source,
            f"{field}.source",
            "needs exactly one immutable version or declared runtime resolution",
        )
        return False
    if has_version and str(source_spec["version"]).strip().lower() in (
        "latest",
        "head",
        "runtime",
        "unknown",
    ):
        _warn_invalid(source, f"{field}.source.version", "version must be immutable")
        return False
    locator = str(source_spec["locator"])
    if locator.strip().lower() in GENERIC_CAPABILITY_TOKENS:
        _warn_invalid(
            source, f"{field}.source.locator", "generic fallback token is forbidden"
        )
        return False
    if (
        locator.startswith(("/", "~"))
        or "/Users/" in locator
        or ".." in pathlib.PurePosixPath(locator).parts
    ):
        _warn_invalid(
            source,
            f"{field}.source.locator",
            "absolute or escaping paths are forbidden",
        )
        return False

    surface = row["surface"]
    if (
        not isinstance(surface, dict)
        or surface.get("type") != CAPABILITY_SURFACES[kind]
    ):
        _warn_invalid(
            source,
            f"{field}.surface.type",
            f"{kind!r} requires {CAPABILITY_SURFACES[kind]!r}",
        )
        return False
    if not _nonempty_string(surface.get("action")) or not is_str_list(
        surface.get("plan_argv")
    ):
        _warn_invalid(source, f"{field}.surface", "needs action and plan_argv")
        return False
    if surface["action"].strip().lower() in GENERIC_CAPABILITY_TOKENS:
        _warn_invalid(
            source, f"{field}.surface.action", "generic fallback action is forbidden"
        )
        return False

    boundary = row["boundary"]
    if not _string_dict(boundary, ("actor", "action", "state")):
        _warn_invalid(source, f"{field}.boundary", "needs actor, action, and state")
        return False
    if boundary["actor"] not in ("human", "mechanical"):
        _warn_invalid(source, f"{field}.boundary.actor", "must be human or mechanical")
        return False
    if boundary["actor"] == "human" and surface["plan_argv"]:
        _warn_invalid(
            source,
            f"{field}.surface.plan_argv",
            "human actions cannot carry executable argv",
        )
        return False
    if boundary["actor"] == "mechanical" and not surface["plan_argv"]:
        _warn_invalid(
            source, f"{field}.surface.plan_argv", "mechanical action needs exact argv"
        )
        return False

    least = row["least_privilege"]
    if (
        not isinstance(least, dict)
        or not _nonempty_string(least.get("mode"))
        or not is_nonempty_str_list(least.get("scopes"))
        or not least["scopes"]
    ):
        _warn_invalid(
            source, f"{field}.least_privilege", "needs mode and explicit scopes"
        )
        return False
    if not _string_dict(row["proof"], ("oracle", "semantic_expectation")):
        _warn_invalid(
            source,
            f"{field}.proof",
            "needs an authoritative semantic oracle and expectation",
        )
        return False
    approval = row["approval"]
    if (
        not isinstance(approval, dict)
        or not isinstance(approval.get("required"), bool)
        or not _nonempty_string(approval.get("boundary"))
    ):
        _warn_invalid(
            source, f"{field}.approval", "needs required boolean and boundary"
        )
        return False
    if boundary["actor"] == "human" and not approval["required"]:
        _warn_invalid(
            source, f"{field}.approval.required", "human action requires approval"
        )
        return False
    recovery = row["recovery"]
    if (
        not isinstance(recovery, dict)
        or not is_str_list(recovery.get("argv"))
        or not _nonempty_string(recovery.get("residue"))
    ):
        _warn_invalid(source, f"{field}.recovery", "needs argv and residue")
        return False
    resume = row["resume"]
    if (
        not isinstance(resume, dict)
        or not _nonempty_string(resume.get("key"))
        or not is_nonempty_str_list(resume.get("requires"))
        or not resume["requires"]
    ):
        _warn_invalid(source, f"{field}.resume", "needs key and non-empty requirements")
        return False
    if not is_str_list(row["depends_on"]) or capability_id in row["depends_on"]:
        _warn_invalid(
            source,
            f"{field}.depends_on",
            "must be capability ids and cannot include self",
        )
        return False

    exclusive_fields = {
        "selection": "first_party_oauth_connector",
        "mcp_servers": "marketplace_skill_pack",
        "cli": "marketplace_skill_pack",
        "tool_allowlist": "hosted_mcp_server",
    }
    for exclusive_field, owner_kind in exclusive_fields.items():
        if exclusive_field in row and kind != owner_kind:
            _warn_invalid(
                source,
                f"{field}.{exclusive_field}",
                f"belongs only to {owner_kind!r}; capability kinds cannot be conflated",
            )
            return False

    if kind == "first_party_oauth_connector":
        selection = row.get("selection")
        if not isinstance(selection, dict) or selection.get("mode") not in (
            "fixed",
            "runtime",
        ):
            _warn_invalid(
                source,
                f"{field}.selection",
                "connector needs fixed or runtime selection",
            )
            return False
        if selection["mode"] == "fixed":
            if selection.get("identity") not in FIRST_PARTY:
                _warn_invalid(
                    source,
                    f"{field}.selection.identity",
                    "unknown first-party connector",
                )
                return False
            if not locator.endswith("/" + str(selection["identity"])):
                _warn_invalid(
                    source,
                    f"{field}.source.locator",
                    "fixed connector source must name its identity",
                )
                return False
        else:
            eligible = selection.get("eligible")
            if (
                not is_nonempty_str_list(eligible)
                or not eligible
                or len(set(eligible)) != len(eligible)
                or not set(eligible).issubset(FIRST_PARTY)
                or selection.get("state") != "WAITING_HUMAN"
                or not _nonempty_string(selection.get("receipt"))
            ):
                _warn_invalid(
                    source,
                    f"{field}.selection",
                    "runtime connector needs bounded eligible identities, WAITING_HUMAN, and selection receipt",
                )
                return False
            selection_receipt = str(selection["receipt"])
            if (
                not isinstance(resolution, dict)
                or resolution.get("receipt") != selection_receipt
                or resume["key"] != selection_receipt
            ):
                _warn_invalid(
                    source,
                    f"{field}.resume.key",
                    "runtime connector source, selection, and resume receipts must match",
                )
                return False
            resume_requirements = " ".join(resume["requires"]).lower()
            if (
                "authoritative account binding" not in resume_requirements
                or "read-only proof" not in resume_requirements
            ):
                _warn_invalid(
                    source,
                    f"{field}.resume.requires",
                    "runtime connector resume needs authoritative account binding and read-only proof",
                )
                return False
        if boundary["actor"] != "human" or boundary["state"] != "WAITING_HUMAN":
            _warn_invalid(
                source, f"{field}.boundary", "OAuth connector must wait for a human"
            )
            return False
        if (
            row["proof"]["oracle"]
            != "authoritative_account_binding_plus_read_only_call"
        ):
            _warn_invalid(
                source,
                f"{field}.proof.oracle",
                "connector proof must bind account then read",
            )
            return False
        if not approval["required"]:
            _warn_invalid(
                source, f"{field}.approval.required", "OAuth requires human approval"
            )
            return False

    if kind == "server_recipe_skill" and (
        source_spec["authority"] != "grok-bot-account"
        or not locator.startswith("account/recipes/")
    ):
        _warn_invalid(
            source, f"{field}.source", "recipe skill needs exact account/recipes source"
        )
        return False
    if kind == "disk_private_skill" and (
        source_spec["authority"] != "grok-bot-private-skills"
        or not locator.startswith("private-skills/")
    ):
        _warn_invalid(
            source,
            f"{field}.source",
            "disk skill needs a logical private-skills source",
        )
        return False
    if kind == "repo_plugin_skill" and (
        source_spec["authority"] != "repository"
        or not locator.startswith("plugin/skills/")
        or not locator.endswith("/SKILL.md")
    ):
        _warn_invalid(
            source,
            f"{field}.source",
            "repo skill needs exact plugin/skills/.../SKILL.md source",
        )
        return False
    if kind == "marketplace_skill_pack":
        exposed = row.get("mcp_servers")
        cli = row.get("cli")
        if (
            not isinstance(exposed, dict)
            or exposed.get("state") != "explicit"
            or not is_str_list(exposed.get("servers"))
            or exposed["servers"]
            or not _nonempty_string(cli)
            or boundary["actor"] != "human"
            or boundary["state"] != "WAITING_HUMAN"
            or "login" not in (surface["action"] + " " + boundary["action"]).lower()
            or str(cli).lower() not in row["proof"]["semantic_expectation"].lower()
            or row["proof"]["oracle"] != "shared_cloud_cli_status_plus_semantic_read"
            or recovery["argv"][:1] != [cli]
        ):
            _warn_invalid(
                source,
                field,
                "CLI-backed skill-pack needs human login, named CLI proof/recovery, and explicit zero-MCP declaration",
            )
            return False
        if source_spec["authority"] != "grok-bot-marketplace" or not locator.startswith(
            "catalog/plugins/"
        ):
            _warn_invalid(
                source,
                f"{field}.source",
                "skill-pack needs exact marketplace catalog source",
            )
            return False
    if kind == "hosted_mcp_server":
        if (
            not is_nonempty_str_list(row.get("tool_allowlist"))
            or not row["tool_allowlist"]
        ):
            _warn_invalid(
                source,
                f"{field}.tool_allowlist",
                "hosted MCP needs a non-empty allowlist",
            )
            return False
        if source_spec["authority"] == "repository" and not locator.startswith(
            "mcp-servers/"
        ):
            _warn_invalid(
                source,
                f"{field}.source",
                "repository MCP needs exact mcp-servers source",
            )
            return False
    return True


def validate_pack(data: Any, source: str) -> Optional[Dict[str, Any]]:
    """Schema-check one parsed pack. Returns the pack or None (warned, skipped)."""
    if not isinstance(data, dict):
        warn(f"{source}: not a JSON object — skipped")
        return None
    for key in REQUIRED_STR:
        if not _nonempty_string(data.get(key)):
            warn(f"{source}: missing/non-empty string key {key!r} — skipped")
            return None
    for key in REQUIRED_LIST:
        if not isinstance(data.get(key), list):
            warn(f"{source}: missing/list key {key!r} — skipped")
            return None
    legacy = [key for key in LEGACY_CAPABILITY_KEYS if key in data]
    if legacy:
        _warn_invalid(
            source, "/", f"legacy capability declarations are forbidden: {legacy}"
        )
        return None

    for index, bot in enumerate(data["bots"]):
        if (
            not isinstance(bot, dict)
            or not _nonempty_string(bot.get("template"))
            or not _nonempty_string(bot.get("why"))
        ):
            warn(f"{source}: bots[{index}] needs {{template, why}} strings — skipped")
            return None
    bot_ids = [str(bot["template"]) for bot in data["bots"]]
    if len(set(bot_ids)) != len(bot_ids):
        _warn_invalid(source, "bots", "duplicate logical Bot identity")
        return None

    for index, routine in enumerate(data["routines"]):
        if (
            not isinstance(routine, dict)
            or not _nonempty_string(routine.get("name"))
            or not _nonempty_string(routine.get("schedule"))
            or not _nonempty_string(routine.get("owner_bot"))
            or not _nonempty_string(routine.get("prompt_hint"))
        ):
            warn(
                f"{source}: routines[{index}] needs {{name, schedule, owner_bot, prompt_hint}} strings — skipped"
            )
            return None
        if routine["owner_bot"] not in bot_ids:
            _warn_invalid(
                source, f"routines[{index}].owner_bot", "unknown logical Bot id"
            )
            return None
    for key in ("policies", "human_steps"):
        if not is_str_list(data[key]):
            warn(f"{source}: {key} must be a list of strings — skipped")
            return None
    capability_words = (
        "connector",
        "plugin",
        " mcp",
        "skill-pack",
        "skill pack",
        "oauth",
        "gmail",
        "calendar",
        "drive",
        "firecrawl",
    )
    for index, step in enumerate(data["human_steps"]):
        if any(word in step.lower() for word in capability_words):
            _warn_invalid(
                source,
                f"human_steps[{index}]",
                "capabilities may only be declared in the manifest",
            )
            return None

    manifest = data.get("capability_manifest")
    if not isinstance(manifest, dict) or manifest.get("schema") != CAPABILITY_SCHEMA:
        _warn_invalid(
            source, "capability_manifest.schema", f"must be {CAPABILITY_SCHEMA!r}"
        )
        return None
    rows = manifest.get("capabilities")
    if not isinstance(rows, list):
        _warn_invalid(source, "capability_manifest.capabilities", "must be a list")
        return None
    seen: set[str] = set()
    if not all(
        _validate_capability(row, index, source, set(bot_ids), seen)
        for index, row in enumerate(rows)
    ):
        return None
    semantic_identities = [
        (row["kind"], row["source"]["authority"], row["source"]["locator"])
        for row in rows
    ]
    if len(set(semantic_identities)) != len(semantic_identities):
        _warn_invalid(
            source,
            "capability_manifest.capabilities",
            "one source is declared more than once under different capability identities",
        )
        return None
    for index, row in enumerate(rows):
        unknown_dependencies = [
            dependency for dependency in row["depends_on"] if dependency not in seen
        ]
        if unknown_dependencies:
            _warn_invalid(
                source,
                f"capability_manifest.capabilities[{index}].depends_on",
                f"unknown ids {unknown_dependencies}",
            )
            return None
    mcp_set = manifest.get("mcp_set")
    hosted_count = sum(row["kind"] == "hosted_mcp_server" for row in rows)
    if (
        not isinstance(mcp_set, dict)
        or mcp_set.get("state") != "explicit"
        or not isinstance(mcp_set.get("expected_count"), int)
        or isinstance(mcp_set.get("expected_count"), bool)
        or mcp_set["expected_count"] != hosted_count
    ):
        _warn_invalid(
            source,
            "capability_manifest.mcp_set",
            f"needs explicit expected_count={hosted_count}; unknown and count drift are rejected",
        )
        return None
    return data


def discover() -> Tuple[List[Dict[str, Any]], List[str]]:
    """Discover pack descriptors; compilation remains strict in load_persona()."""
    packs: List[Dict[str, Any]] = []
    skipped: List[str] = []
    if not PERSONA_DIR.is_dir():
        warn(f"{PERSONA_DIR} does not exist — no personas")
        return packs, skipped
    for path in sorted(PERSONA_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            warn(f"{path.name}: unreadable ({exc}) — skipped")
            skipped.append(path.name)
            continue
        if (
            not isinstance(data, dict)
            or any(not _nonempty_string(data.get(key)) for key in REQUIRED_STR)
            or data.get("id") != path.stem
        ):
            warn(f"{path.name}: invalid persona descriptor — skipped")
            skipped.append(path.name)
            continue
        packs.append(data)
    packs.sort(key=lambda pack: str(pack["id"]))
    return packs, skipped


def load_persona(persona_id: str) -> Optional[Dict[str, Any]]:
    """Load only the requested pack so unrelated legacy packs cannot pollute its plan."""
    path = PERSONA_DIR / f"{persona_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise Drift(f"persona {persona_id!r}: unreadable ({exc})")
    pack = validate_pack(data, path.name)
    if pack is None:
        raise Drift(f"persona {persona_id!r}: invalid capability manifest")
    if pack["id"] != persona_id:
        raise Drift(f"persona {persona_id!r}: file declares id {pack['id']!r}")
    return pack


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


def validate_refs(pack: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Resolve every repository-backed capability and logical Bot reference."""
    out: Dict[str, Dict[str, Any]] = {}
    for bot in pack["bots"]:
        template = bot["template"]
        out[template] = load_template(template)
    for row in pack["capability_manifest"]["capabilities"]:
        locator = row["source"]["locator"]
        if row["kind"] == "repo_plugin_skill":
            path = ROOT / locator
            if not path.is_file():
                raise Drift(
                    f"capability {row['id']!r}: repository skill source {locator!r} does not exist"
                )
        elif (
            row["kind"] == "hosted_mcp_server"
            and row["source"]["authority"] == "repository"
        ):
            path = ROOT / locator
            if not path.is_dir():
                raise Drift(
                    f"capability {row['id']!r}: hosted MCP source {locator!r} does not exist"
                )
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


def _capability_rows(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Stable topological order; declaration order breaks ties."""
    rows = pack["capability_manifest"]["capabilities"]
    pending = {row["id"]: row for row in rows}
    emitted: set[str] = set()
    ordered: List[Dict[str, Any]] = []
    while pending:
        ready = [
            row
            for row in rows
            if row["id"] in pending and set(row["depends_on"]).issubset(emitted)
        ]
        if not ready:
            raise Drift(f"capability dependency cycle among {list(pending)}")
        for row in ready:
            ordered.append(row)
            emitted.add(row["id"])
            del pending[row["id"]]
    return ordered


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _source_digest(source_spec: Dict[str, Any]) -> str:
    if source_spec["authority"] != "repository":
        return _digest(source_spec)
    path = ROOT / source_spec["locator"]
    if path.is_file():
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(
        candidate for candidate in path.rglob("*") if candidate.is_file()
    ):
        relative = child.relative_to(path).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(child.read_bytes())
    return "sha256:" + digest.hexdigest()


def build_steps(pack: Dict[str, Any], tdata: Dict[str, Dict[str, Any]]) -> List[Step]:
    steps: List[Step] = []
    n = 0

    def add(
        kind: str,
        title: str,
        detail: str,
        command: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        nonlocal n
        n += 1
        step: Step = {"n": n, "kind": kind, "title": title, "detail": detail}
        if command is not None:
            step["command"] = command
        if metadata:
            step.update(metadata)
        steps.append(step)

    add(
        "mechanical",
        "Machine setup plan first",
        "Print the machine preflight and device-registration plan before persona work; do not mutate.",
        "gb setup",
    )

    for bot in pack["bots"]:
        template = bot["template"]
        add(
            "mechanical",
            f"Read the {template} charter",
            f"Logical target {template}. Why this Bot: {bot['why']}",
            f"gb templates show {shlex.quote(template)}",
        )
        add(
            "mechanical",
            f"Plan deployment of {template}",
            "Dry-run only; this step names the exact logical Bot and does not create or update it.",
            f"gb templates deploy {shlex.quote(template)}",
        )

    field_positions = {
        row["id"]: index
        for index, row in enumerate(pack["capability_manifest"]["capabilities"])
    }
    for row in _capability_rows(pack):
        index = field_positions[row["id"]]
        source_spec = row["source"]
        digest_started = time.perf_counter_ns()
        source_digest = _source_digest(source_spec)
        timing_ms = round((time.perf_counter_ns() - digest_started) / 1_000_000, 3)
        source_version = source_spec.get("version")
        if source_version is None:
            source_version = (
                f"runtime:{source_spec['resolution']['mode']} "
                f"receipt={source_spec['resolution']['receipt']}"
            )
        surface = row["surface"]
        approval = row["approval"]
        recovery = row["recovery"]
        resume = row["resume"]
        least = row["least_privilege"]
        proof = row["proof"]
        detail = (
            f"Targets: {', '.join(row['targets'])}. "
            f"Source: {source_spec['authority']}:{source_spec['locator']}@{source_version}. "
            f"Action surface: {surface['type']} / {surface['action']}. "
            f"Boundary: {row['boundary']['state']} — {row['boundary']['action']}. "
            f"Least privilege: {least['mode']} [{', '.join(least['scopes'])}]. "
            f"Semantic proof: {proof['oracle']} => {proof['semantic_expectation']}. "
            f"Approval: {'required' if approval['required'] else 'not required'} at "
            f"{approval['boundary']}. Recovery residue: {recovery['residue']}. "
            f"Resume: {resume['key']} requires {', '.join(resume['requires'])}."
        )
        argv = surface["plan_argv"]
        add(
            row["boundary"]["actor"],
            f"Capability {row['id']} ({row['kind']})",
            detail,
            shlex.join(argv) if argv else None,
            {
                "action_class": row["boundary"]["actor"],
                "capability_id": row["id"],
                "capability_kind": row["kind"],
                "targets": list(row["targets"]),
                "field_path": f"capability_manifest.capabilities[{index}]",
                "expected_kind": row["kind"],
                "observed_kind": row["kind"],
                "source_digest": source_digest,
                "verdict": "PLANNED",
                "exit": 0,
                "recovery_argv": list(recovery["argv"]),
                "timing_ms": timing_ms,
            },
        )

    for routine in pack["routines"]:
        owner = routine["owner_bot"]
        add(
            "human",
            f"Schedule routine: {routine['name']} ({routine['schedule']}, owner {owner})",
            f"Hint: {routine['prompt_hint']} Template routine: {routine_text(tdata[owner])} "
            "In-app: create the scheduled run with exactly that prompt. "
            "Proof: one dated run lands in-thread on schedule.",
        )

    for policy in pack["policies"]:
        add(
            "human",
            f"Set policy: {policy}",
            f"Where: {POLICY_WHERE}. Proof: trigger the named boundary once and confirm the Bot asks first.",
        )
    for human_step in pack["human_steps"]:
        add("human", "Human-only step", human_step)
    add(
        "mechanical",
        "Verify the persona is live",
        "Read-only check: roster, routines firing, and no new ERRORs.",
        "gb triage",
    )
    return steps


def render_text(pack: Dict[str, Any], steps: List[Step]) -> str:
    lines = [
        f"# {pack['name']} — {pack['tagline']}",
        f"Audience: {pack['audience']}",
        "",
        "DRY-RUN PHILOSOPHY: this walk only prints. [mechanical] steps contain",
        "the exact declared read-only planning argv; [human] steps name the handoff",
        "and semantic proof. Nothing here executes or mutates anything.",
        "",
    ]
    for s in steps:
        cmd = f"\n  $ {s['command']}" if "command" in s else ""
        lines.append(f"{s['n']}. [{s['kind']}] {s['title']}\n   {s['detail']}{cmd}")
    return "\n".join(lines) + "\n"


def envelope(pack: Dict[str, Any], steps: List[Step]) -> Dict[str, Any]:
    capability_log = [
        {
            key: step[key]
            for key in (
                "capability_id",
                "capability_kind",
                "targets",
                "field_path",
                "action_class",
                "expected_kind",
                "observed_kind",
                "source_digest",
                "verdict",
                "exit",
                "recovery_argv",
                "timing_ms",
            )
        }
        for step in steps
        if "capability_id" in step
    ]
    return {
        "schema": SCHEMA,
        "schema_digest": _digest({"setup": SCHEMA, "capability": CAPABILITY_SCHEMA}),
        "tool": "gb",
        "command": "setup",
        "persona": pack["id"],
        "persona_digest": _digest(pack),
        "name": pack["name"],
        "capability_manifest": {
            "schema": CAPABILITY_SCHEMA,
            "digest": _digest(pack["capability_manifest"]),
            "mcp_set": pack["capability_manifest"]["mcp_set"],
        },
        "dry_run": True,
        "applied": False,
        "steps": steps,
        "capability_log": capability_log,
    }


def list_envelope(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "tool": "gb",
        "command": "setup",
        "personas": rows,
    }


SELFTEST_POSITIVES: Tuple[Tuple[str, str], ...] = (
    ("accept-runtime-connector-choice-resume", "first_party_oauth_connector"),
    ("accept-server-recipe-skill", "server_recipe_skill"),
    ("accept-disk-private-skill", "disk_private_skill"),
    ("accept-repo-plugin-skill", "repo_plugin_skill"),
    ("accept-cli-pack-explicit-zero-mcp", "marketplace_skill_pack"),
    ("accept-hosted-mcp-allowlist", "hosted_mcp_server"),
)

SELFTEST_NEGATIVES: Tuple[Dict[str, Any], ...] = (
    {
        "id": "reject-unknown-kind",
        "kind": "repo_plugin_skill",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "kind"),
        "value": "unknown_capability_kind",
        "field": "capability_manifest.capabilities[0].kind",
        "message": "unknown kind",
    },
    {
        "id": "reject-wrong-source",
        "kind": "server_recipe_skill",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "source", "authority"),
        "value": "repository",
        "field": "capability_manifest.capabilities[0].source",
        "message": "recipe skill needs exact account/recipes source",
    },
    {
        "id": "reject-unknown-target",
        "kind": "disk_private_skill",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "targets"),
        "value": ["missing-logical-bot"],
        "field": "capability_manifest.capabilities[0].targets",
        "message": "unknown logical Bot ids",
    },
    {
        "id": "reject-kind-surface-mismatch",
        "kind": "repo_plugin_skill",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "surface", "type"),
        "value": "hosted_mcp",
        "field": "capability_manifest.capabilities[0].surface.type",
        "message": "requires 'repo_plugin_artifact'",
    },
    {
        "id": "reject-connector-proof-conflation",
        "kind": "first_party_oauth_connector",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "proof", "oracle"),
        "value": "installed_or_enabled",
        "field": "capability_manifest.capabilities[0].proof.oracle",
        "message": "connector proof must bind account then read",
    },
    {
        "id": "reject-human-action-without-approval",
        "kind": "marketplace_skill_pack",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "approval", "required"),
        "value": False,
        "field": "capability_manifest.capabilities[0].approval.required",
        "message": "human action requires approval",
    },
    {
        "id": "reject-empty-hosted-mcp-allowlist",
        "kind": "hosted_mcp_server",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "tool_allowlist"),
        "value": [],
        "field": "capability_manifest.capabilities[0].tool_allowlist",
        "message": "hosted MCP needs a non-empty allowlist",
    },
    {
        "id": "reject-hosted-mcp-count-drift",
        "kind": "hosted_mcp_server",
        "op": "set",
        "path": ("capability_manifest", "mcp_set", "expected_count"),
        "value": 0,
        "field": "capability_manifest.mcp_set",
        "message": "unknown and count drift are rejected",
    },
    {
        "id": "reject-kind-field-conflation",
        "kind": "repo_plugin_skill",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "tool_allowlist"),
        "value": ["documents.get"],
        "field": "capability_manifest.capabilities[0].tool_allowlist",
        "message": "capability kinds cannot be conflated",
    },
    {
        "id": "reject-duplicate-identity",
        "kind": "repo_plugin_skill",
        "op": "duplicate",
        "field": "capability_manifest.capabilities[1].id",
        "message": "duplicate identity",
        "row_index": 1,
    },
    {
        "id": "reject-duplicate-source-declaration",
        "kind": "repo_plugin_skill",
        "op": "duplicate-source",
        "field": "capability_manifest.capabilities",
        "message": "one source is declared more than once",
        "row_index": 1,
    },
    {
        "id": "reject-legacy-capability-declaration",
        "kind": "repo_plugin_skill",
        "op": "set",
        "path": ("skills",),
        "value": [],
        "field": "/",
        "message": "legacy capability declarations are forbidden",
    },
    {
        "id": "reject-generic-fallback-action",
        "kind": "repo_plugin_skill",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "surface", "action"),
        "value": "plugin",
        "field": "capability_manifest.capabilities[0].surface.action",
        "message": "generic fallback action is forbidden",
    },
    {
        "id": "reject-unknown-mcp-set",
        "kind": "repo_plugin_skill",
        "op": "set",
        "path": ("capability_manifest", "mcp_set", "state"),
        "value": "unknown",
        "field": "capability_manifest.mcp_set",
        "message": "unknown and count drift are rejected",
    },
    {
        "id": "reject-runtime-connector-resume-receipt-drift",
        "kind": "first_party_oauth_connector",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "resume", "key"),
        "value": "different-selection-receipt",
        "field": "capability_manifest.capabilities[0].resume.key",
        "message": "source, selection, and resume receipts must match",
    },
    {
        "id": "reject-runtime-connector-unproven-resume",
        "kind": "first_party_oauth_connector",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "resume", "requires"),
        "value": ["selection receipt only"],
        "field": "capability_manifest.capabilities[0].resume.requires",
        "message": "needs authoritative account binding and read-only proof",
    },
    {
        "id": "reject-cli-pack-mcp-conflation",
        "kind": "marketplace_skill_pack",
        "op": "set",
        "path": ("capability_manifest", "capabilities", 0, "mcp_servers", "servers"),
        "value": ["guessed-mcp-server"],
        "field": "capability_manifest.capabilities[0]",
        "message": "explicit zero-MCP declaration",
    },
)

SELFTEST_RECOVERY_ARGV = ["bin/gb-setup-persona.py", "--selftest"]


def _selftest_capability(kind: str) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": f"fixture.{kind}",
        "kind": kind,
        "targets": ["fixture-bot"],
        "source": {
            "authority": "fixture-authority",
            "locator": f"fixture/{kind}",
            "version": "fixture-v1",
        },
        "surface": {
            "type": CAPABILITY_SURFACES[kind],
            "action": f"perform_exact_{kind}",
            "plan_argv": [],
        },
        "boundary": {
            "actor": "human",
            "action": f"Approve and perform the exact {kind} fixture action.",
            "state": "READY",
        },
        "least_privilege": {"mode": "read_only", "scopes": ["fixture record read"]},
        "proof": {
            "oracle": "fixture_semantic_read",
            "semantic_expectation": "One fixture record returns by identity without mutation.",
        },
        "approval": {"required": True, "boundary": "before the fixture action"},
        "recovery": {"argv": [], "residue": "Remove only the fixture binding."},
        "resume": {
            "key": f"fixture.{kind}.receipt",
            "requires": ["matching fixture receipt"],
        },
        "depends_on": [],
    }
    if kind == "first_party_oauth_connector":
        receipt = "fixture.runtime-connector-selection"
        row.update(
            {
                "source": {
                    "authority": "grok-bot-first-party-catalog",
                    "locator": "account/plugins/first-party",
                    "resolution": {
                        "mode": "human_catalog_selection",
                        "receipt": receipt,
                    },
                },
                "selection": {
                    "mode": "runtime",
                    "state": "WAITING_HUMAN",
                    "eligible": ["gmail", "calendar"],
                    "receipt": receipt,
                },
                "surface": {
                    "type": CAPABILITY_SURFACES[kind],
                    "action": "human_select_oauth_and_bind_fixture-bot",
                    "plan_argv": [],
                },
                "boundary": {
                    "actor": "human",
                    "action": "Select one eligible connector, record it, complete OAuth, and bind it.",
                    "state": "WAITING_HUMAN",
                },
                "proof": {
                    "oracle": "authoritative_account_binding_plus_read_only_call",
                    "semantic_expectation": "The selected account binding is authoritative and one read-only call returns a real value.",
                },
                "approval": {"required": True, "boundary": "before OAuth consent"},
                "resume": {
                    "key": receipt,
                    "requires": [
                        "durable selected identity receipt",
                        "authoritative account binding",
                        "declared read-only proof result",
                    ],
                },
            }
        )
    elif kind == "server_recipe_skill":
        row["source"] = {
            "authority": "grok-bot-account",
            "locator": "account/recipes/fixture-recipe",
            "version": "fixture-recipe-v1",
        }
    elif kind == "disk_private_skill":
        row["source"] = {
            "authority": "grok-bot-private-skills",
            "locator": "private-skills/fixture-private-skill",
            "version": "sha256:fixture-private-skill",
        }
    elif kind == "repo_plugin_skill":
        row["source"] = {
            "authority": "repository",
            "locator": "plugin/skills/plugin-enable-verify/SKILL.md",
            "version": "sha256:fixture-repo-skill",
        }
    elif kind == "marketplace_skill_pack":
        row.update(
            {
                "source": {
                    "authority": "grok-bot-marketplace",
                    "locator": "catalog/plugins/fixture-cli-pack",
                    "version": "fixture-catalog-ref-v1",
                },
                "surface": {
                    "type": CAPABILITY_SURFACES[kind],
                    "action": "human_login_then_fixture-cli_status_and_semantic_read",
                    "plan_argv": [],
                },
                "boundary": {
                    "actor": "human",
                    "action": "Log in to fixture-cli on the shared cloud computer.",
                    "state": "WAITING_HUMAN",
                },
                "proof": {
                    "oracle": "shared_cloud_cli_status_plus_semantic_read",
                    "semantic_expectation": "fixture-cli status identifies the account and one semantic read returns a real value.",
                },
                "recovery": {
                    "argv": ["fixture-cli", "logout"],
                    "residue": "Remove only the fixture-cli session and retain the proof receipt.",
                },
                "mcp_servers": {"state": "explicit", "servers": []},
                "cli": "fixture-cli",
            }
        )
    elif kind == "hosted_mcp_server":
        row.update(
            {
                "source": {
                    "authority": "fixture-hosted-catalog",
                    "locator": "catalog/mcp/fixture-read-server",
                    "version": "fixture-server-v1",
                },
                "least_privilege": {
                    "mode": "tool_allowlist",
                    "scopes": ["fixture record read"],
                },
                "tool_allowlist": ["fixture.records.get"],
            }
        )
    return row


def _selftest_pack(kind: str) -> Dict[str, Any]:
    row = _selftest_capability(kind)
    return {
        "id": f"selftest-{kind}",
        "name": "Selftest Persona",
        "tagline": "Inline validation fixture",
        "audience": "The offline capability validator",
        "bots": [{"template": "fixture-bot", "why": "Own the fixture exactly once."}],
        "routines": [],
        "policies": [],
        "human_steps": [],
        "capability_manifest": {
            "schema": CAPABILITY_SCHEMA,
            "mcp_set": {
                "state": "explicit",
                "expected_count": 1 if kind == "hosted_mcp_server" else 0,
            },
            "capabilities": [row],
        },
    }


def _selftest_mutate(pack: Dict[str, Any], case: Dict[str, Any]) -> None:
    rows = pack["capability_manifest"]["capabilities"]
    if case["op"] == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
        return
    if case["op"] == "duplicate-source":
        duplicate = copy.deepcopy(rows[0])
        duplicate["id"] += ".second-declaration"
        rows.append(duplicate)
        return
    target: Any = pack
    path = case["path"]
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = copy.deepcopy(case["value"])


def _redacted_diagnostic(value: str) -> str:
    text = value.strip()
    lower = text.lower()
    if any(
        marker in lower
        for marker in ("sk_live_", "ghp_", "xoxb-", "bearer ey", "api_key=", "token=")
    ):
        return "<redacted credential-shaped diagnostic>"
    for private_root, replacement in (
        (str(ROOT), "<repo>"),
        (str(pathlib.Path.home()), "<home>"),
    ):
        text = text.replace(private_root, replacement)
    return text[:1000]


def _redacted_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redacted_diagnostic(value)
    if isinstance(value, list):
        return [_redacted_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redacted_value(item) for key, item in value.items()}
    return value


def _selftest_log(
    fixture_id: str,
    fixture_class: str,
    pack: Dict[str, Any],
    row_index: int,
    expected_kind: str,
    expected_outcome: str,
    observed_outcome: str,
    field_path: str,
    diagnostic: str,
    ok: bool,
    started_ns: int,
) -> Dict[str, Any]:
    rows = pack.get("capability_manifest", {}).get("capabilities", [])
    row = rows[row_index] if row_index < len(rows) else {}
    source_spec = row.get("source") if isinstance(row.get("source"), dict) else {}
    boundary = row.get("boundary") if isinstance(row.get("boundary"), dict) else {}
    targets = row.get("targets") if is_str_list(row.get("targets")) else []
    return {
        "fixture_id": fixture_id,
        "fixture_class": fixture_class,
        "persona": pack.get("id"),
        "persona_digest": _digest(pack),
        "schema_digest": _digest({"setup": SCHEMA, "capability": CAPABILITY_SCHEMA}),
        "source_digest": _digest(source_spec),
        "row_identity": row.get("id"),
        "target_logical_ids": targets,
        "field_path": field_path,
        "action_class": boundary.get("actor"),
        "expected_kind": expected_kind,
        "observed_kind": row.get("kind"),
        "expected_outcome": expected_outcome,
        "observed_outcome": observed_outcome,
        "diagnostic": _redacted_diagnostic(diagnostic),
        "verdict": "GREEN" if ok else "RED",
        "exit": 0 if ok else 1,
        "validator_exit": 0 if observed_outcome == "ACCEPT" else 1,
        "recovery_argv": list(SELFTEST_RECOVERY_ARGV),
        "timing_ms": round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
    }


def _compiled_plan_problems(pack: Dict[str, Any], steps: List[Step]) -> List[str]:
    rows = pack["capability_manifest"]["capabilities"]
    capability_steps = [step for step in steps if "capability_id" in step]
    problems: List[str] = []
    ordered_ids = [row["id"] for row in _capability_rows(pack)]
    observed_ids = [step["capability_id"] for step in capability_steps]
    if observed_ids != ordered_ids:
        problems.append(
            "capability steps are missing, duplicated, or out of dependency order"
        )
    by_id = {step["capability_id"]: step for step in capability_steps}
    for index, row in enumerate(rows):
        matches = [
            step for step in capability_steps if step["capability_id"] == row["id"]
        ]
        if len(matches) != 1:
            problems.append(
                f"{row['id']}: expected exactly one compiled capability step"
            )
            continue
        step = by_id[row["id"]]
        expected_command = shlex.join(row["surface"]["plan_argv"])
        observed_command = step.get("command", "")
        required_pairs = (
            ("capability_kind", row["kind"]),
            ("expected_kind", row["kind"]),
            ("observed_kind", row["kind"]),
            ("targets", row["targets"]),
            ("field_path", f"capability_manifest.capabilities[{index}]"),
            ("action_class", row["boundary"]["actor"]),
            ("recovery_argv", row["recovery"]["argv"]),
        )
        if any(step.get(key) != value for key, value in required_pairs):
            problems.append(
                f"{row['id']}: compiled metadata differs from its manifest row"
            )
        if observed_command != expected_command:
            problems.append(
                f"{row['id']}: compiled command differs from declared plan_argv"
            )
        detail = step["detail"]
        for exact_value in (
            row["source"]["locator"],
            row["surface"]["action"],
            row["proof"]["oracle"],
            row["proof"]["semantic_expectation"],
        ):
            if exact_value not in detail:
                problems.append(
                    f"{row['id']}: compiled plan omitted a declared exact value"
                )
                break
        if any(
            value.strip().lower() in GENERIC_CAPABILITY_TOKENS
            for value in (row["id"], row["source"]["locator"], row["surface"]["action"])
        ):
            problems.append(f"{row['id']}: generic fallback token reached compilation")
    generic_prefixes = (
        "capability ",
        "install skill",
        "enable skill",
        "connect connector",
        "enable connector",
        "connect mcp",
        "install mcp",
        "install plugin",
    )
    for step in steps:
        title = str(step.get("title", "")).lower()
        if "capability_id" not in step and title.startswith(generic_prefixes):
            problems.append(
                f"step {step.get('n')}: untyped generic capability fallback"
            )
    return problems


def selftest() -> int:
    """Offline, table-driven schema fixtures plus no-write compilation of both real personas."""
    logs: List[Dict[str, Any]] = []

    positive_kinds = {kind for _fixture_id, kind in SELFTEST_POSITIVES}
    matrix_started = time.perf_counter_ns()
    matrix_ok = positive_kinds == set(CAPABILITY_SURFACES)
    matrix_pack = _selftest_pack("repo_plugin_skill")
    logs.append(
        _selftest_log(
            "positive-kind-matrix-is-exhaustive",
            "coverage",
            matrix_pack,
            0,
            "all-six-capability-kinds",
            "ACCEPT",
            "ACCEPT" if matrix_ok else "REJECT",
            "capability_manifest.capabilities[*].kind",
            ""
            if matrix_ok
            else "positive fixture matrix differs from the capability union",
            matrix_ok,
            matrix_started,
        )
    )

    for fixture_id, kind in SELFTEST_POSITIVES:
        started = time.perf_counter_ns()
        pack = _selftest_pack(kind)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            accepted = validate_pack(pack, fixture_id) is not None
        diagnostic = stderr.getvalue()
        ok = accepted and not diagnostic.strip()
        logs.append(
            _selftest_log(
                fixture_id,
                "positive",
                pack,
                0,
                kind,
                "ACCEPT",
                "ACCEPT" if accepted else "REJECT",
                "capability_manifest.capabilities[0]",
                diagnostic,
                ok,
                started,
            )
        )

    for case in SELFTEST_NEGATIVES:
        started = time.perf_counter_ns()
        pack = _selftest_pack(case["kind"])
        _selftest_mutate(pack, case)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            accepted = validate_pack(pack, case["id"]) is not None
        diagnostic = stderr.getvalue()
        ok = (
            not accepted
            and case["field"] in diagnostic
            and case["message"] in diagnostic
        )
        logs.append(
            _selftest_log(
                case["id"],
                "known-bad",
                pack,
                int(case.get("row_index", 0)),
                case["kind"],
                "REJECT",
                "ACCEPT" if accepted else "REJECT",
                case["field"],
                diagnostic,
                ok,
                started,
            )
        )

    compiled_rows: List[Dict[str, Any]] = []
    generic_fallback_failures = 0
    ownership_failures = 0
    for persona_id in ("first-hour", "founder-operator"):
        started = time.perf_counter_ns()
        stderr = io.StringIO()
        pack: Optional[Dict[str, Any]] = None
        problems: List[str] = []
        doc: Optional[Dict[str, Any]] = None
        try:
            with contextlib.redirect_stderr(stderr):
                pack = load_persona(persona_id)
                if pack is None:
                    raise Drift(f"persona {persona_id!r}: missing")
                steps = build_steps(pack, validate_refs(pack))
                problems.extend(_compiled_plan_problems(pack, steps))
                generic_fallback_failures += sum(
                    "fallback" in problem for problem in problems
                )
                ownership_failures += sum(
                    "exactly one compiled" in problem
                    or "missing, duplicated" in problem
                    for problem in problems
                )
                doc = envelope(pack, steps)
        except (Drift, OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(str(exc))
        diagnostics = stderr.getvalue()
        if diagnostics.strip():
            problems.append(diagnostics)
        if pack is None:
            pack = _selftest_pack("repo_plugin_skill")
            pack["id"] = persona_id
        ok = doc is not None and not problems
        logs.append(
            _selftest_log(
                f"compile-real-{persona_id}",
                "real-manifest",
                pack,
                0,
                "typed-capability-plan",
                "ACCEPT",
                "ACCEPT" if ok else "REJECT",
                "capability_manifest.capabilities",
                "\n".join(problems),
                ok,
                started,
            )
        )
        if doc is not None:
            for row in doc["capability_log"]:
                compiled_rows.append(
                    {
                        "fixture_id": f"compile-real-{persona_id}:{row['capability_id']}",
                        "fixture_class": "real-manifest-row",
                        "persona": persona_id,
                        "persona_digest": doc["persona_digest"],
                        "schema_digest": doc["schema_digest"],
                        "source_digest": row["source_digest"],
                        "row_identity": row["capability_id"],
                        "target_logical_ids": row["targets"],
                        "field_path": row["field_path"],
                        "action_class": row["action_class"],
                        "expected_kind": row["expected_kind"],
                        "observed_kind": row["observed_kind"],
                        "expected_outcome": "COMPILED",
                        "observed_outcome": "COMPILED" if ok else "REJECT",
                        "diagnostic": "",
                        "verdict": "GREEN" if ok else "RED",
                        "exit": 0 if ok else 1,
                        "validator_exit": row["exit"],
                        "recovery_argv": row["recovery_argv"],
                        "timing_ms": row["timing_ms"],
                    }
                )

    logs.extend(compiled_rows)
    failed = sum(row["verdict"] != "GREEN" for row in logs)
    payload: Dict[str, Any] = {
        "schema": SELFTEST_SCHEMA,
        "tool": "gb-setup-persona.py",
        "command": "selftest",
        "status": "PASS" if failed == 0 else "FAIL",
        "redacted": True,
        "offline": True,
        "applied": False,
        "fixtures": logs,
        "summary": {
            "positive_variants": len(SELFTEST_POSITIVES),
            "known_bad": len(SELFTEST_NEGATIVES),
            "real_personas": 2,
            "compiled_capabilities": len(compiled_rows),
            "generic_fallback_steps": generic_fallback_failures,
            "ownership_failures": ownership_failures,
            "total": len(logs),
            "passed": len(logs) - failed,
            "failed": failed,
        },
        "recovery_argv": list(SELFTEST_RECOVERY_ARGV),
    }
    payload = _redacted_value(payload)
    serialized = json.dumps(payload, indent=1, sort_keys=True)
    leak_markers = (
        str(ROOT),
        str(pathlib.Path.home()),
        "/Users/",
        "sk_live_",
        "ghp_",
        "xoxb-",
        "bearer ey",
        "api_key=",
        "token=",
    )
    if any(marker.lower() in serialized.lower() for marker in leak_markers):
        failed += 1
        payload = {
            "schema": SELFTEST_SCHEMA,
            "tool": "gb-setup-persona.py",
            "command": "selftest",
            "status": "FAIL",
            "redacted": False,
            "error": "structured diagnostic redaction invariant failed",
            "recovery_argv": list(SELFTEST_RECOVERY_ARGV),
        }
        serialized = json.dumps(payload, indent=1, sort_keys=True)
    print(serialized)
    return EXIT_OK if failed == 0 else EXIT_DRIFT


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
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="run offline capability fixtures and compile both real personas",
    )
    args = ap.parse_args(argv)

    if args.selftest:
        if args.apply or args.list or args.persona is not None:
            print(
                json.dumps(
                    {
                        "schema": SELFTEST_SCHEMA,
                        "tool": "gb-setup-persona.py",
                        "command": "selftest",
                        "status": "USAGE",
                        "redacted": True,
                        "error": "--selftest is exclusive with --apply, --list, and --persona",
                        "recovery_argv": SELFTEST_RECOVERY_ARGV,
                    },
                    indent=1,
                    sort_keys=True,
                )
            )
            return EXIT_USAGE
        return selftest()

    if args.apply:
        pid = args.persona or "founder-operator"
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
        try:
            pack = load_persona(args.persona)
        except Drift as exc:
            print(f"gb-setup-persona.py: DRIFT: {exc}", file=sys.stderr)
            return EXIT_DRIFT
        if pack is None:
            known = sorted(path.stem for path in PERSONA_DIR.glob("*.json"))
            preview = _few_ids(known, "founder-operator")
            listing = "gb setup --list-personas"
            near = nearest(args.persona, known)
            hint = (
                f"gb setup --persona {near}"
                if near
                else "gb templates"
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
    persona_id = packs[idx - 1]["id"]
    try:
        pack = load_persona(persona_id)
        if pack is None:
            raise Drift(f"persona {persona_id!r}: disappeared after discovery")
        tdata = validate_refs(pack)
        steps = build_steps(pack, tdata)
    except Drift as exc:
        print(f"gb-setup-persona.py: DRIFT: {exc}", file=sys.stderr)
        return EXIT_DRIFT
    print(render_text(pack, steps), end="")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
