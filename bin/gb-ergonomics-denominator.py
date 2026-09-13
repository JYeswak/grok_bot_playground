#!/usr/bin/env python3
"""Freeze every ``gb`` command; any dispatcher edit invalidates the denominator."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime as dt
import hashlib
import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess
import sys
import time
import types
import typing
from collections import Counter
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
DISPATCHER = BIN / "gb"
ARTIFACT = ROOT / "ergonomics" / "gb-command-surface-v1.json"
SCHEMA = "gb-command-surface/1"
EXIT_OK, EXIT_FINDINGS, EXIT_USAGE, EXIT_ENVIRONMENT = 0, 1, 2, 3
REGEN_ARGV = ["python3", "bin/gb-ergonomics-denominator.py", "--write"]

EXIT_CODES = {
    "0": "ok",
    "1": "findings",
    "2": "usage",
    "3": "environment",
    "4": "upstream",
    "5": "refused",
    "130": "cancelled",
}

WAVE_GROUPS: dict[str, tuple[str, ...]] = {
    "W2": ("daily", "post"),
    "W4": (
        "capabilities",
        "robot-docs",
        "help",
        "info",
        "examples",
        "quickstart",
        "completion",
    ),
    "W5": ("sources", "github", "feeds", "x", "links", "grokbotdev"),
    "W6": ("role", "setup", "bootstrap", "walk", "templates", "galaxy", "fleet"),
    "W7": ("bot", "group", "dm", "handover", "skills", "jobs"),
    "W8": ("mcp", "plugins", "inventory", "deployment", "spend"),
    "W9": ("advise", "research", "mine", "monitor", "roadmap", "findings"),
    "W11": (
        "record",
        "matrix",
        "export",
        "gates",
        "bench",
        "store",
        "skill-coverage",
        "schema",
        "bot-conformance",
        "template-conformance",
        "skill-conformance",
        "plugin-conformance",
        "compliance",
        "teach",
        "digest",
        "mirror",
        "demand",
        "blast",
        "corpus",
        "dogfood",
        "gatesdoc",
        "readme",
        "platform",
        "triage",
        "work",
        "doctor",
        "health",
        "repair",
        "validate",
        "audit",
        "why",
    ),
}

WRITE_SIGNAL_FIELDS = frozenset(
    {
        "apply",
        "yes",
        "save",
        "write",
        "send",
        "collect",
        "refresh",
        "accept",
        "dispose",
        "share",
        "screened",
        "force",
        "no_write",
        "rollback",
        "resume",
    }
)
ARTIFACT_SIDE_EFFECTS = frozenset(
    {
        "monitor",
        "mcp",
        "mine",
        "advise",
        "findings",
        "roadmap",
        "deployment",
        "inventory",
        "skill-coverage",
        "doctor",
        "export",
        "readme",
    }
)


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def load_dispatcher() -> types.ModuleType:
    loader = importlib.machinery.SourceFileLoader(
        "gb_surface_dispatcher", str(DISPATCHER)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("cannot load bin/gb")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def type_name(tp: Any) -> str:
    return str(tp).replace("typing.", "").replace("<class '", "").replace("'>", "")


def json_default(value: Any) -> Any:
    if value is dataclasses.MISSING:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    return repr(value)


def field_shape(field: dataclasses.Field[Any], tp: Any) -> dict[str, Any]:
    spec = field.metadata.get("gbargs")
    positional = bool(getattr(spec, "positional", False))
    choices = list(getattr(spec, "choices", ()))
    metavar = str(getattr(spec, "metavar", "") or field.name.upper())
    option_strings: list[str] = []
    if not positional:
        short = str(getattr(spec, "short", "") or "")
        if short:
            option_strings.append(short if short.startswith("-") else "-" + short)
        option_strings.append("--" + field.name.replace("_", "-"))
        option_strings.extend(getattr(spec, "aliases", ()))
    origin = typing.get_origin(tp)
    repeated = origin in (list, tuple, set, frozenset)
    boolean = tp is bool
    value_token = "<" + ("|".join(choices) if choices else metavar) + ">"
    if positional:
        tokens = [value_token]
    elif boolean:
        tokens = [option_strings[0]]
    else:
        tokens = [option_strings[0], value_token]
    return {
        "name": field.name,
        "kind": "positional" if positional else "option",
        "tokens": tokens,
        "option_strings": option_strings,
        "type": type_name(tp),
        "required": bool(getattr(spec, "required", False)),
        "choices": choices,
        "repeatable": repeated,
        "default": json_default(field.default),
    }


def wave_owners(command_names: list[str]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {name: [] for name in command_names}
    for wave, names in WAVE_GROUPS.items():
        for name in names:
            owners.setdefault(name, []).append(wave)
    return owners


def mutability(command: str, field_names: set[str]) -> dict[str, Any]:
    signals = sorted(field_names & WRITE_SIGNAL_FIELDS)
    artifact_side_effect = command in ARTIFACT_SIDE_EFFECTS
    if signals and artifact_side_effect:
        kind = "artifact-and-explicit-write-mode"
    elif signals:
        kind = "explicit-write-mode"
    elif artifact_side_effect:
        kind = "artifact-side-effect"
    else:
        kind = "read-only"
    return {
        "class": kind,
        "signals": signals,
        "artifact_side_effect": artifact_side_effect,
    }


def role_grammar() -> dict[str, Any]:
    proc = subprocess.run(
        [str(DISPATCHER), "role", "--capabilities"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0 or proc.stderr:
        raise RuntimeError(
            "gb role --capabilities failed: exit=%d stderr=%r"
            % (proc.returncode, proc.stderr)
        )
    doc = json.loads(proc.stdout)
    grammar = doc.get("public_grammar")
    if not isinstance(grammar, dict):
        raise RuntimeError("role capabilities omitted public_grammar")
    return grammar


def build_surface() -> dict[str, Any]:
    module = load_dispatcher()
    commands = list(module.COMMANDS)
    owners = wave_owners(commands)
    grammar = role_grammar()
    rows: list[dict[str, Any]] = []
    for command, cls in module.COMMANDS.items():
        hints = typing.get_type_hints(cls)
        fields = [
            field_shape(field, hints[field.name]) for field in dataclasses.fields(cls)
        ]
        names = {field["name"] for field in fields}
        json_supported = "json" in names
        row: dict[str, Any] = {
            "command": command,
            "argv": {"prefix": ["gb", command], "fields": fields},
            "mutability": mutability(command, names),
            "json": {
                "disposition": "supported" if json_supported else "explicit-refusal",
                "flag": "--json" if json_supported else None,
                "recovery_argv": None if json_supported else ["gb", command, "--help"],
            },
            "exit_family": "gb-exit-codes/1",
            "wave_owners": owners.get(command, []),
        }
        if command == "role":
            row["journey_grammar"] = grammar
        rows.append(row)
    stable = {
        "schema": SCHEMA,
        "dispatcher": {
            "path": "bin/gb",
            "sha256": hashlib.sha256(DISPATCHER.read_bytes()).hexdigest(),
        },
        "command_count": len(commands),
        "row_order": "bin/gb COMMANDS insertion order",
        "command_order": commands,
        "exit_family": {"schema": "gb-exit-codes/1", "codes": EXIT_CODES},
        "rows": rows,
    }
    return {
        **stable,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "surface_digest": digest(stable),
    }


Problem = tuple[str, str, str]


def issue(code: str, path: str, detail: str) -> Problem:
    return code, path, detail


def validate(doc: dict[str, Any], live: dict[str, Any]) -> list[Problem]:
    problems: list[Problem] = []
    if doc.get("schema") != SCHEMA:
        problems.append(issue("SCHEMA_MISMATCH", "$.schema", str(doc.get("schema"))))
    rows = doc.get("rows")
    if not isinstance(rows, list):
        return [issue("ROWS_INVALID", "$.rows", "rows must be an array")]
    names = [str(row.get("command")) for row in rows if isinstance(row, dict)]
    counts = Counter(names)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    expected = set(live["command_order"])
    observed = set(names)
    for name in sorted(expected - observed):
        problems.append(issue("MISSING_COMMAND", "$.rows", name))
    for name in sorted(observed - expected):
        problems.append(issue("EXTRA_COMMAND", "$.rows", name))
    for name in duplicates:
        problems.append(issue("DUPLICATE_COMMAND", "$.rows", name))
    if doc.get("dispatcher", {}).get("sha256") != live["dispatcher"]["sha256"]:
        problems.append(
            issue("STALE_DISPATCHER_DIGEST", "$.dispatcher.sha256", "bin/gb changed")
        )
    if doc.get("command_order") != live["command_order"]:
        problems.append(
            issue("ROW_ORDER_DRIFT", "$.command_order", "COMMANDS order moved")
        )
    if doc.get("command_count") != len(names):
        problems.append(
            issue("COUNT_MISMATCH", "$.command_count", "count != row length")
        )
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(
                issue("ROW_INVALID", f"$.rows[{index}]", "row must be object")
            )
            continue
        if row.get("json", {}).get("disposition") not in (
            "supported",
            "explicit-refusal",
        ):
            problems.append(
                issue(
                    "MISSING_JSON_DISPOSITION",
                    f"$.rows[{index}].json.disposition",
                    str(row.get("command")),
                )
            )
        owners = row.get("wave_owners")
        if not isinstance(owners, list) or len(owners) != 1:
            problems.append(
                issue(
                    "WAVE_OWNER_CARDINALITY",
                    f"$.rows[{index}].wave_owners",
                    str(row.get("command")),
                )
            )
    live_rows = {row["command"]: row for row in live["rows"]}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("command") not in live_rows:
            continue
        name = str(row["command"])
        if row != live_rows[name]:
            problems.append(issue("ROW_DRIFT", f"$.rows[{index}]", name))
    role_rows = [
        row for row in rows if isinstance(row, dict) and row.get("command") == "role"
    ]
    live_role = next(row for row in live["rows"] if row["command"] == "role")
    if (
        len(role_rows) == 1
        and role_rows[0].get("journey_grammar") != live_role["journey_grammar"]
    ):
        problems.append(
            issue(
                "ROLE_GRAMMAR_DRIFT",
                "$.rows[role].journey_grammar",
                "plan/apply/status/resume/rollback changed",
            )
        )
    stable = {
        key: value
        for key, value in doc.items()
        if key not in ("generated_at", "surface_digest")
    }
    if doc.get("surface_digest") != digest(stable):
        problems.append(
            issue("SURFACE_DIGEST_MISMATCH", "$.surface_digest", "content moved")
        )
    return problems


def selftest(as_json: bool = False) -> int:
    started = time.monotonic()
    live = build_surface()
    legs: list[tuple[str, dict[str, Any], str]] = []
    missing = copy.deepcopy(live)
    missing["rows"].pop(0)
    legs.append(("missing-command", missing, "MISSING_COMMAND"))
    duplicate = copy.deepcopy(live)
    duplicate["rows"].append(copy.deepcopy(duplicate["rows"][0]))
    legs.append(("duplicate-command", duplicate, "DUPLICATE_COMMAND"))
    invented = copy.deepcopy(live)
    invented["rows"].append(
        {**copy.deepcopy(invented["rows"][0]), "command": "invented"}
    )
    legs.append(("invented-command", invented, "EXTRA_COMMAND"))
    stale = copy.deepcopy(live)
    stale["dispatcher"]["sha256"] = "0" * 64
    legs.append(("stale-dispatcher", stale, "STALE_DISPATCHER_DIGEST"))
    no_json = copy.deepcopy(live)
    no_json["rows"][0]["json"].pop("disposition")
    legs.append(("missing-json-disposition", no_json, "MISSING_JSON_DISPOSITION"))
    two_owners = copy.deepcopy(live)
    two_owners["rows"][0]["wave_owners"].append("W99")
    legs.append(("duplicate-wave-owner", two_owners, "WAVE_OWNER_CARDINALITY"))
    bad_role = copy.deepcopy(live)
    role = next(row for row in bad_role["rows"] if row["command"] == "role")
    role["journey_grammar"].pop("rollback")
    legs.append(("role-grammar-drift", bad_role, "ROLE_GRAMMAR_DRIFT"))
    bad_schema = copy.deepcopy(live)
    bad_schema["schema"] = "gb-command-surface/0"
    legs.append(("schema-mismatch", bad_schema, "SCHEMA_MISMATCH"))

    failures: list[str] = []
    if validate(live, live):
        failures.append("known-good")
    for name, specimen, wanted in legs:
        codes = {item[0] for item in validate(specimen, live)}
        if wanted not in codes:
            failures.append(f"{name}: wanted {wanted}, got {sorted(codes)}")
    if live["surface_digest"] != build_surface()["surface_digest"]:
        failures.append("stable-replay")
    elapsed = round((time.monotonic() - started) * 1000, 3)
    payload = {
        "schema": "gb-command-surface-selftest/1",
        "verdict": "RED" if failures else "GREEN",
        "fixtures": len(legs) + 2,
        "failed": failures,
        "timing_ms": elapsed,
    }
    text = (
        "SELFTEST FAIL " + " | ".join(failures)
        if failures
        else f"SELFTEST PASS - {len(legs) + 2}/{len(legs) + 2}"
    )
    print(json.dumps(payload, indent=1) if as_json else text)
    return EXIT_FINDINGS if failures else EXIT_OK


def emit_check(
    doc: dict[str, Any], live: dict[str, Any], as_json: bool, timing_ms: float
) -> int:
    problems = validate(doc, live)
    payload = {
        "schema": "gb-command-surface-check/1",
        "verdict": "RED" if problems else "GREEN",
        "command_count": live["command_count"],
        "dispatcher_sha256": live["dispatcher"]["sha256"],
        "surface_digest": live["surface_digest"],
        "problems": [
            {"code": code, "path": path, "detail": detail, "recovery_argv": REGEN_ARGV}
            for code, path, detail in problems
        ],
        "set_differences": {
            key: [detail for code, _, detail in problems if code == target]
            for key, target in (
                ("missing", "MISSING_COMMAND"),
                ("extra", "EXTRA_COMMAND"),
            )
        },
        "timing_ms": timing_ms,
        "recovery_argv": REGEN_ARGV if problems else None,
    }
    if as_json:
        print(json.dumps(payload, indent=1))
    elif problems:
        for code, path, detail in problems:
            print(f"{code}\t{path}\t{detail}")
    else:
        print(f"GREEN {live['command_count']} commands {live['surface_digest'][:12]}")
    return EXIT_FINDINGS if problems else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="write the versioned artifact"
    )
    parser.add_argument(
        "--check", action="store_true", help="validate the committed artifact"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest(args.json)
    try:
        live = build_surface()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"gb-ergonomics-denominator: {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    if args.write:
        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(BIN))
        from gbtypes import atomic_write_json

        atomic_write_json(ARTIFACT, live)
        print(
            json.dumps(
                {
                    "schema": "gb-command-surface-write/1",
                    "artifact": str(ARTIFACT.relative_to(ROOT)),
                    "command_count": live["command_count"],
                    "surface_digest": live["surface_digest"],
                    "dispatcher_sha256": live["dispatcher"]["sha256"],
                    "timing_ms": round((time.monotonic() - started) * 1000, 3),
                },
                indent=1,
            )
            if args.json
            else f"wrote {ARTIFACT.relative_to(ROOT)}"
        )
        return EXIT_OK
    if args.check or not args.json:
        if not ARTIFACT.is_file():
            print(
                f"missing {ARTIFACT.relative_to(ROOT)}; run {' '.join(REGEN_ARGV)}",
                file=sys.stderr,
            )
            return EXIT_ENVIRONMENT
        try:
            doc = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"unreadable denominator: {exc}", file=sys.stderr)
            return EXIT_ENVIRONMENT
        return emit_check(
            doc, live, args.json, round((time.monotonic() - started) * 1000, 3)
        )
    print(json.dumps(live, indent=1))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
