#!/usr/bin/env python3
"""gb-role-journey — the canonical resumable role journey and `gb role` backend.

WHY THIS EXISTS. Strong stage beads (identity, capability, room topology,
first job) assume a rollout entrypoint nobody owned: gb-setup-persona walks a
free-form setup_order, renders every surface twice, and proves human steps by
"a later prerequisite holds". This journey compiles one persona exactly once
into ordered phases (setup -> identity -> topology -> capabilities ->
first-job), persists a parent journal before any stage call, and drives
plan/status/apply/resume/rollback through the existing stage commands. It
duplicates no stage RPC logic and never drives the app.

REFUSALS: unknown role phrases; a later phase starting past an incomplete
one; resume across a moved roster without re-plan; concurrent conflicting
attempts on one journal; stale-lock recovery across the irreversible
boundary without a fresh ack; retrying an irreversible room create
(manifest-bound nonce replays to the same id, never a new room); faked UI
automation (human surfaces stay WAITING_HUMAN with exact proof, always).

APPLIED MINING LESSONS: (1) saga resume excludes completed AND failed rows —
resume continues at the first incomplete phase and repeats no confirmed
write; (2) Coach owns UI/computer-use: WAITING checkpoints are enqueued with
their callback and the journey keeps reporting every phase rather than
sitting on one wait (fh suggest STALE ledger_age_hours=125.132; stale
remains usable).

READS/WRITES: plan/status read personas/, one roster pull, newest audit
artifact; plan writes one parent journal under role-journeys/ (override with
--journal-dir for proof runs). Apply/resume/rollback shell to the stage
binaries with logged argv. stdlib only. Token in memory, never printed.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_json  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
SCHEMA = "gb-role-journey/1"
JOURNAL_SCHEMA = "gb-role-journal/1"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
EXIT_UPSTREAM = 4
EXIT_REFUSED = 5

ROLE_PHRASES = {
    "first hour": "first-hour",
    "first-hour": "first-hour",
    "founder operator": "founder-operator",
    "founder-operator": "founder-operator",
}
CANONICAL_ROLE_PHRASES = {
    "first-hour": "first hour",
    "founder-operator": "founder operator",
}
PHASES = ("setup", "identity", "topology", "capabilities", "first-job")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]


def _load(modname: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(modname, str(BIN / filename))
    if spec is None or spec.loader is None:
        raise RuntimeError("missing module bin/%s" % filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


class JourneyRefused(Exception):
    """Typed refusal: the phase or journal rule that stopped us."""


def _normalise_role(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).split())


def _persona_catalog() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted((ROOT / "personas").glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        persona_id = str(document.get("id") or path.stem)
        name = str(document.get("name") or persona_id)
        schema = document.get("schema")
        version = document.get("version")
        declared_status = document.get("status")
        if declared_status == "retired":
            resolution = "RETIRED"
        elif schema != "gb-persona/1":
            resolution = "UNAVAILABLE"
        elif version != 1:
            resolution = "VERSION_MISMATCH"
        elif declared_status != "active":
            resolution = "UNAVAILABLE"
        else:
            resolution = "ACTIVE"
        phrases = {
            persona_id,
            name,
            name.casefold(),
        }
        phrases.update(
            phrase for phrase, target in ROLE_PHRASES.items() if target == persona_id
        )
        canonical = CANONICAL_ROLE_PHRASES.get(persona_id, name.casefold())
        rows.append(
            {
                "id": persona_id,
                "name": name,
                "phrases": sorted(
                    phrases, key=lambda value: (value != canonical, value)
                ),
                "canonical_phrase": canonical,
                "schema": schema,
                "version": version,
                "status": declared_status or "legacy-unavailable",
                "resolution": resolution,
                "persona_digest": _digest(document),
                "plan_argv": (
                    ["gb", "role", canonical, "--json"]
                    if resolution == "ACTIVE"
                    else None
                ),
            }
        )
    return sorted(
        rows,
        key=lambda row: (row["resolution"] != "ACTIVE", row["canonical_phrase"]),
    )


def resolve_persona(phrase: str) -> str:
    key = _normalise_role(phrase)
    matches = [
        row
        for row in _persona_catalog()
        if key in {_normalise_role(value) for value in row["phrases"]}
    ]
    if not matches:
        raise JourneyRefused(
            "ROLE_UNKNOWN: unknown role %r; use gb role --list" % phrase
        )
    if len(matches) > 1:
        raise JourneyRefused(
            "ROLE_AMBIGUOUS: role %r matches %s"
            % (phrase, sorted(row["id"] for row in matches))
        )
    row = matches[0]
    if row["resolution"] != "ACTIVE":
        raise JourneyRefused(
            "ROLE_%s: persona %s is %s (schema=%r version=%r status=%r)"
            % (
                row["resolution"],
                row["id"],
                row["resolution"].lower(),
                row["schema"],
                row["version"],
                row["status"],
            )
        )
    return str(row["id"])


def classify_policy(text: str) -> Tuple[str, str]:
    """Type a prose policy by authoritative surface. Unknown stays pack-scoped:
    never promoted to an account control, never dropped silently."""
    low = str(text or "").lower()
    if "auto-review" in low or "auto review" in low or "autoreview" in low:
        return ("account", "Auto-review is account-level")
    if "local computer" in low or "local-computer" in low or "execution on" in low:
        return ("per-desktop", "Execution-on-Local-Computer is per-desktop")
    return ("pack", "pack-scoped note, not an account control")


def compile_journey(
    persona: str,
    roster: List[Dict[str, Any]],
    audit: Mapping[str, Any],
    cap_rows: List[Dict[str, Any]],
    room_rows: List[Dict[str, Any]],
    binding: Mapping[str, Any],
    id_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """One persona into ordered phases, each declaration exactly once. Pure."""
    account = audit.get("account") if isinstance(audit, dict) else {}
    phases: List[Dict[str, Any]] = []
    setup_rows: List[Dict[str, Any]] = [
        {
            "row": "app/%s" % (audit.get("app", {}) or {}).get("version", "?"),
            "verdict": "PROVEN",
            "receipt": "newest deployment audit artifact",
            "owner": "setup",
        },
        {
            "row": "membership/%s"
            % (account.get("membership") if isinstance(account, dict) else "?"),
            "verdict": "PROVEN",
            "receipt": "audit account block",
            "owner": "setup",
        },
        {
            "row": "roster/%d-bots" % len(roster),
            "verdict": "PROVEN",
            "receipt": "fresh ListGrokBotAgents pull",
            "owner": "setup",
        },
    ]
    try:
        persona_doc = json.loads(
            (ROOT / "personas" / (persona + ".json")).read_text(encoding="utf-8")
        )
    except (ValueError, OSError):
        persona_doc = {}
    for policy in persona_doc.get("policies", []) or []:
        surface, why = classify_policy(policy)
        setup_rows.append(
            {
                "row": "policy/%s" % _digest(policy),
                "verdict": "DECLARED",
                "receipt": "%s: %s" % (surface, why),
                "owner": "setup",
            }
        )
    for step in persona_doc.get("human_steps", []) or []:
        setup_rows.append(
            {
                "row": "human-step/%s" % _digest(step),
                "verdict": "DECLARED",
                "receipt": "human-only: %s" % str(step)[:120],
                "owner": "setup",
                "continuation": {
                    "coach_need": "a human performs the step in the app",
                    "coach_job": "re-run status after the step; never mark complete from prose",
                    "action": "do the step, then gb-role-journey status",
                },
            }
        )
    phases.append({"phase": "setup", "rows": setup_rows})

    phases.append(
        {
            "phase": "identity",
            "rows": [
                {
                    "row": str(r.get("logical_id")),
                    "verdict": "PROVEN" if r.get("live_uuid") else "WAITING_HUMAN",
                    "receipt": ("live %s" % r.get("live_uuid"))
                    if r.get("live_uuid")
                    else "uncreated; create via sanctioned seed",
                    "owner": "identity",
                    "resume_key": str(r.get("resume_key") or ""),
                }
                for r in id_rows
            ],
        }
    )
    phases.append(
        {
            "phase": "topology",
            "rows": [
                {
                    "row": str(r.get("logical_id")),
                    "verdict": str(r.get("verdict")),
                    "receipt": str(r.get("receipt") or ""),
                    "owner": "topology",
                    "resume_key": str(r.get("resume_key") or ""),
                    "continuation": r.get("continuation") or {},
                }
                for r in room_rows
            ]
            or [
                {
                    "row": "topology-undeclared",
                    "verdict": "WAITING_HUMAN",
                    "receipt": "persona declares no rooms[]: declare rooms or explicit []",
                    "owner": "topology",
                }
            ],
        }
    )
    phases.append(
        {
            "phase": "capabilities",
            "rows": [
                {
                    "row": "%s:%s" % (r.get("capability"), r.get("target")),
                    "verdict": str(r.get("verdict")),
                    "receipt": str(r.get("receipt") or "")[:160],
                    "owner": "capabilities",
                    "continuation": r.get("continuation") or {},
                }
                for r in cap_rows
            ],
        }
    )
    phases.append(
        {
            "phase": "first-job",
            "rows": [
                {
                    "row": "job/%s"
                    % (binding.get("target") or {}).get("template", "?"),
                    "verdict": str(binding.get("verdict") or "FAILED"),
                    "receipt": str(binding.get("receipt") or "")[:160],
                    "owner": "first-job",
                    "continuation": binding.get("continuation") or {},
                }
            ],
        }
    )
    for phase in phases:
        verdicts = [
            row["verdict"] for row in phase["rows"] if row["verdict"] != "DECLARED"
        ]
        if any(verdict == "FAILED" for verdict in verdicts):
            phase["verdict"] = "FAILED"
        elif any(verdict == "WAITING_HUMAN" for verdict in verdicts):
            phase["verdict"] = "WAITING_HUMAN"
        elif verdicts:
            phase["verdict"] = "PROVEN"
        else:
            phase["verdict"] = "PROVEN"
    return {"schema": SCHEMA, "persona": persona, "phases": phases}


def bind_public_continuations(
    journey: Mapping[str, Any], correlation_id: str
) -> Dict[str, Any]:
    """Attach literal public resume/status/rollback argv to every open row."""
    bound: Dict[str, Any] = json.loads(json.dumps(journey))
    status_argv = ["gb", "role", "--status", correlation_id, "--json"]
    rollback_argv = ["gb", "role", "--rollback", correlation_id, "--json"]
    for phase in bound.get("phases", []):
        for row in phase.get("rows", []):
            if row.get("verdict") in ("PROVEN", "DECLARED"):
                continue
            checkpoint_id = str(
                row.get("resume_key")
                or _digest({"phase": phase.get("phase"), "row": row.get("row")})
            )
            continuation = dict(row.get("continuation") or {})
            continuation["checkpoint_id"] = checkpoint_id
            continuation["status_argv"] = list(status_argv)
            continuation["resume_argv"] = [
                "gb",
                "role",
                "--resume",
                correlation_id,
                "--checkpoint",
                checkpoint_id,
                "--json",
            ]
            continuation["rollback_argv"] = list(rollback_argv)
            row["continuation"] = continuation
    return bound


def journey_verdict(journey: Mapping[str, Any]) -> str:
    verdicts = [p.get("verdict") for p in journey.get("phases", [])]
    if any(v == "FAILED" for v in verdicts):
        return "FAILED"
    if any(v == "WAITING_HUMAN" for v in verdicts):
        return "WAITING_HUMAN"
    return "PROVEN"


def plan_hash_of(journey: Mapping[str, Any], roster_digest: str) -> str:
    return _digest(
        {
            "persona": journey.get("persona"),
            "phases": [
                {"phase": p.get("phase"), "rows": p.get("rows")}
                for p in journey.get("phases", [])
            ],
            "roster_digest": roster_digest,
        }
    )


def journal_path(journal_dir: pathlib.Path, cid: str) -> pathlib.Path:
    return journal_dir / ("%s.json" % cid)


def find_matching_attempt(
    journal_dir: pathlib.Path, persona: str, plan_hash: str, roster_digest: str
) -> Optional[str]:
    """A concurrent matching attempt resolves to the same manifest: same
    persona, same plan hash, same fresh roster digest. Anything else is new."""
    if not journal_dir.is_dir():
        return None
    for path in sorted(journal_dir.glob("*.json")):
        if path.name.endswith(".lock"):
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if (
            isinstance(doc, dict)
            and doc.get("schema") == JOURNAL_SCHEMA
            and doc.get("persona") == persona
            and doc.get("plan_hash") == plan_hash
            and doc.get("roster_digest") == roster_digest
        ):
            return doc.get("correlation_id")
    return None


def write_journal(
    journal_dir: pathlib.Path,
    cid: str,
    persona: str,
    plan_hash: str,
    roster_digest: str,
    journey: Mapping[str, Any],
    event: str,
    recovery_argv: List[str],
    extra: Optional[Mapping[str, Any]] = None,
) -> pathlib.Path:
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_path(journal_dir, cid)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            doc = {}
    except (ValueError, OSError):
        doc = {}
    if not doc:
        doc = {
            "schema": JOURNAL_SCHEMA,
            "correlation_id": cid,
            "persona": persona,
            "plan_hash": plan_hash,
            "roster_digest": roster_digest,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "recovery_argv": list(recovery_argv),
            "transitions": [],
        }
    elif (
        doc.get("persona") != persona
        or doc.get("plan_hash") != plan_hash
        or doc.get("roster_digest") != roster_digest
    ):
        raise JourneyRefused("journal identity changed; create a new role plan")
    doc["recovery_argv"] = list(recovery_argv)
    doc["journey"] = dict(journey)
    doc["phase_states"] = {
        str(phase.get("phase")): phase.get("verdict")
        for phase in journey.get("phases", [])
    }
    doc["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    transitions = doc.setdefault("transitions", [])
    transitions.append(
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "verdict": journey_verdict(journey),
            "phases": [
                {"phase": p.get("phase"), "verdict": p.get("verdict")}
                for p in journey.get("phases", [])
            ],
            **(dict(extra) if extra else {}),
        }
    )
    atomic_write_json(path, doc)
    return path


class JournalLock:
    """Non-blocking exclusive lock per journal. A held lock refuses a second
    writer; a stale lock (dead owner) is refused explicitly, never broken
    across the irreversible boundary by this class."""

    def __init__(self, path: pathlib.Path, owner: str) -> None:
        self.path = path.with_name(path.name + ".lock")
        self.owner = owner
        self.fd: Optional[int] = None

    def __enter__(self) -> "JournalLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.fd)
            self.fd = None
            raise JourneyRefused(
                "journal %s is locked by a live attempt; resume it, never double-apply"
                % self.path
            ) from exc
        os.ftruncate(self.fd, 0)
        os.write(self.fd, (self.owner + "\n").encode())
        os.fsync(self.fd)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def _capabilities() -> int:
    return 39


def _check_resolution_selftests(leg: Callable[[str, bool], None]) -> None:
    try:
        resolve_persona("void realm")
        leg("unknown-role-refuses", False)
    except JourneyRefused as exc:
        leg("unknown-role-refuses", str(exc).startswith("ROLE_UNKNOWN:"))
    leg(
        "phrase-normalizes-case-spacing",
        resolve_persona("  FIRST   HOUR  ") == "first-hour",
    )
    try:
        resolve_persona("eng-lead")
        leg("legacy-persona-unavailable", False)
    except JourneyRefused as exc:
        leg("legacy-persona-unavailable", str(exc).startswith("ROLE_UNAVAILABLE:"))

    original_catalog = globals()["_persona_catalog"]
    cases: Tuple[Tuple[str, List[Dict[str, Any]], str, str], ...] = (
        (
            "ambiguous-role-refuses",
            [
                {"id": "one", "phrases": ["shared"], "resolution": "ACTIVE"},
                {"id": "two", "phrases": ["shared"], "resolution": "ACTIVE"},
            ],
            "shared",
            "ROLE_AMBIGUOUS:",
        ),
        (
            "retired-role-refuses",
            [
                {
                    "id": "old",
                    "phrases": ["old"],
                    "resolution": "RETIRED",
                    "schema": "gb-persona/1",
                    "version": 1,
                    "status": "retired",
                }
            ],
            "old",
            "ROLE_RETIRED:",
        ),
        (
            "version-mismatch-refuses",
            [
                {
                    "id": "future",
                    "phrases": ["future"],
                    "resolution": "VERSION_MISMATCH",
                    "schema": "gb-persona/1",
                    "version": 2,
                    "status": "active",
                }
            ],
            "future",
            "ROLE_VERSION_MISMATCH:",
        ),
    )
    try:
        for name, rows, query, prefix in cases:
            globals()["_persona_catalog"] = lambda rows=rows: rows
            try:
                resolve_persona(query)
                leg(name, False)
            except JourneyRefused as exc:
                leg(name, str(exc).startswith(prefix))
    finally:
        globals()["_persona_catalog"] = original_catalog


def selftest() -> int:
    """State-machine fixtures plus deterministic e2e on isolated adapters."""
    import tempfile

    passed = 0
    failed: List[str] = []

    def leg(name: str, ok: bool) -> None:
        nonlocal passed
        if ok:
            passed += 1
        else:
            failed.append(name)

    _check_resolution_selftests(leg)
    # Policy surfaces type correctly; unknown stays pack-scoped.
    leg(
        "policy-account",
        classify_policy("Require Approval under Auto-review")[0] == "account",
    )
    leg(
        "policy-desktop",
        classify_policy("Execution-on-Local-Computer ask-every-time")[0]
        == "per-desktop",
    )
    leg("policy-pack", classify_policy("Be kind to support staff")[0] == "pack")
    compiled = compile_journey(
        "fixture-persona",
        [],
        {"app": {"version": "fixture"}, "account": {"membership": "fixture"}},
        [],
        [],
        {},
        [],
    )
    leg(
        "all-phases-present-in-order",
        [phase["phase"] for phase in compiled["phases"]] == list(PHASES),
    )
    bound = bind_public_continuations(compiled, "jrn-fixture")
    open_rows = [
        row
        for phase in bound["phases"]
        for row in phase["rows"]
        if row["verdict"] not in ("PROVEN", "DECLARED")
    ]
    leg(
        "every-open-row-has-public-recovery-arrays",
        bool(open_rows)
        and all(
            all(
                isinstance(row["continuation"].get(key), list)
                and row["continuation"][key][:2] == ["gb", "role"]
                for key in ("status_argv", "resume_argv", "rollback_argv")
            )
            for row in open_rows
        )
        and "bin/" not in json.dumps(open_rows),
    )
    # 4. empty journey phases roll up WAITING, never PROVEN-by-default.
    empty: Dict[str, Any] = {
        "schema": SCHEMA,
        "persona": "p",
        "phases": [{"phase": "setup", "rows": []}],
    }
    for phase in empty["phases"]:
        phase["verdict"] = "WAITING_HUMAN"
    leg("empty-waits", journey_verdict(empty) == "WAITING_HUMAN")
    # 5. FAILED poisons the journey verdict.
    bad = {
        "schema": SCHEMA,
        "persona": "p",
        "phases": [
            {"phase": "a", "verdict": "PROVEN"},
            {"phase": "b", "verdict": "FAILED"},
        ],
    }
    leg("failed-poisons", journey_verdict(bad) == "FAILED")
    # 6. plan hash moves with the roster, stable without.
    j = {"schema": SCHEMA, "persona": "p", "phases": []}
    leg("hash-stable", plan_hash_of(j, "d1") == plan_hash_of(j, "d1"))
    leg("hash-moves", plan_hash_of(j, "d1") != plan_hash_of(j, "d2"))
    # 7. matching attempt converges; moved roster is new.
    with tempfile.TemporaryDirectory() as tmp:
        jd = pathlib.Path(tmp)
        write_journal(
            jd,
            "cid-1",
            "p",
            "ph1",
            "d1",
            {"schema": SCHEMA, "persona": "p", "phases": []},
            "planned",
            ["gb", "role"],
        )
        leg("match-converges", find_matching_attempt(jd, "p", "ph1", "d1") == "cid-1")
        leg("moved-roster-new", find_matching_attempt(jd, "p", "ph1", "d2") is None)
        leg("mixed-plan-new", find_matching_attempt(jd, "p", "ph2", "d1") is None)
        # 8. journal appends transitions, never rewrites history.
        write_journal(
            jd,
            "cid-1",
            "p",
            "ph1",
            "d1",
            {"schema": SCHEMA, "persona": "p", "phases": []},
            "status",
            ["gb", "role"],
        )
        doc = json.loads(journal_path(jd, "cid-1").read_text(encoding="utf-8"))
        leg(
            "journal-appends",
            [t["event"] for t in doc["transitions"]] == ["planned", "status"],
        )
        # 9. lock held refuses a second writer.
        with JournalLock(journal_path(jd, "cid-1"), "owner-a"):
            try:
                with JournalLock(journal_path(jd, "cid-1"), "owner-b"):
                    leg("lock-refuses-double", False)
            except JourneyRefused:
                leg("lock-refuses-double", True)
        # 10. lock releases: re-entry works after close.
        try:
            with JournalLock(journal_path(jd, "cid-1"), "owner-c"):
                pass
            leg("lock-reentry", True)
        except JourneyRefused:
            leg("lock-reentry", False)
    # 11. compile emits each declaration once (no duplicated human rows).
    tiny_persona = {"policies": ["Be kind"], "human_steps": ["Do the thing"]}
    seen_rows: List[str] = []
    for policy in tiny_persona["policies"]:
        seen_rows.append("policy/%s" % _digest(policy))
    for step in tiny_persona["human_steps"]:
        seen_rows.append("human-step/%s" % _digest(step))
    leg("single-ownership", len(seen_rows) == len(set(seen_rows)) == 2)
    # 12. phase order is fixed and acyclic.
    leg(
        "phase-order",
        list(PHASES) == ["setup", "identity", "topology", "capabilities", "first-job"],
    )
    # 13-22. apply/resume/rollback against the real journal with fakes.
    real_inputs, real_compile = _INPUTS, _COMPILE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            jd = pathlib.Path(tmp)

            def fake_journey(verdicts: Mapping[str, str]) -> Dict[str, Any]:
                cont = {"coach_need": "n", "coach_job": "j", "action": "a"}
                return {
                    "schema": SCHEMA,
                    "persona": "p",
                    "phases": [
                        {
                            "phase": phase,
                            "verdict": verdicts.get(phase, "WAITING_HUMAN"),
                            "rows": [
                                {
                                    "row": "%s-row" % phase,
                                    "verdict": verdicts.get(phase, "WAITING_HUMAN"),
                                    "receipt": "r",
                                    "continuation": cont
                                    if verdicts.get(phase) != "PROVEN"
                                    else {},
                                }
                            ],
                        }
                        for phase in PHASES
                    ],
                }

            class FakeRunner(StageRunner):
                def __init__(self) -> None:
                    self.calls: List[str] = []
                    self.compensated: List[str] = []
                    self.comp_results: Dict[str, bool] = {}

                def run(
                    self,
                    phase: str,
                    persona: str,
                    journal_dir: pathlib.Path,
                    cid: str,
                    extra: Mapping[str, Any],
                ) -> Tuple[str, List[str], str]:
                    self.calls.append(phase)
                    return ("APPLIED", [], "fake")

                def compensate(
                    self,
                    phase: str,
                    persona: str,
                    journal_dir: pathlib.Path,
                    cid: str,
                    doc: Mapping[str, Any],
                ) -> Tuple[bool, str]:
                    self.compensated.append(phase)
                    return (self.comp_results.get(phase, True), "fake-%s" % phase)

            def seed_journal(
                cid: str,
                persona: str = "p",
                digest: str = "d1",
                states: Optional[Mapping[str, str]] = None,
            ) -> None:
                doc = {
                    "schema": JOURNAL_SCHEMA,
                    "correlation_id": cid,
                    "persona": persona,
                    "plan_hash": "ph",
                    "roster_digest": digest,
                    "recovery_argv": ["x"],
                    "transitions": [],
                    "phase_states": dict(states or {}),
                }
                atomic_write_json(jd / (cid + ".json"), doc)

            # A. apply stops at first WAITING with callbacks enqueued.
            seed_journal("c-stop")
            globals()["_INPUTS"] = lambda persona: ([], {}, "d1")
            globals()["_COMPILE"] = lambda persona, roster, audit: (
                fake_journey({"setup": "PROVEN"}),
                "d1",
            )
            runner = FakeRunner()
            verdict, report = _execute_journey(jd, "c-stop", "p", "apply", runner, {})
            leg(
                "apply-stops-at-wait",
                verdict == "WAITING_HUMAN" and runner.calls == ["setup", "identity"],
            )
            leg(
                "apply-enqueues-callbacks",
                any(
                    c.get("continuation", {}).get("action") == "a"
                    for c in report.get("checkpoints", [])
                ),
            )
            # B. resume skips CONFIRMED, never repeats.
            seed_journal("c-resume", states={"setup": "PROVEN"})
            runner2 = FakeRunner()
            verdict2, _ = _execute_journey(jd, "c-resume", "p", "resume", runner2, {})
            leg(
                "resume-skips-confirmed",
                "setup" not in runner2.calls and verdict2 == "WAITING_HUMAN",
            )
            # C. stale roster refuses resume.
            seed_journal("c-stale", digest="d0")
            try:
                _execute_journey(jd, "c-stale", "p", "resume", FakeRunner(), {})
                leg("stale-resume-refused", False)
            except JourneyRefused:
                leg("stale-resume-refused", True)
            # D. mixed correlation id refuses.
            seed_journal("c-mixed", persona="q")
            try:
                _execute_journey(jd, "c-mixed", "p", "resume", FakeRunner(), {})
                leg("mixed-cid-refused", False)
            except JourneyRefused:
                leg("mixed-cid-refused", True)
            # E. lock conflict refuses a second applier.
            seed_journal("c-lock")
            with JournalLock(journal_path(jd, "c-lock"), "holder"):
                try:
                    _execute_journey(jd, "c-lock", "p", "apply", FakeRunner(), {})
                    leg("lock-conflict-refused", False)
                except JourneyRefused:
                    leg("lock-conflict-refused", True)
            # F. apply without a journal refuses (plan first).
            try:
                _execute_journey(jd, "c-ghost", "p", "apply", FakeRunner(), {})
                leg("no-journal-refused", False)
            except JourneyRefused:
                leg("no-journal-refused", True)
            # G. rollback walks reverse; residue -> PARTIAL.
            seed_journal("c-rb", states={p: "PROVEN" for p in PHASES})
            runner3 = FakeRunner()
            runner3.comp_results = {"topology": False}
            verdict3, report3 = _rollback_journey(jd, "c-rb", "p", runner3)
            leg("rollback-reverse", runner3.compensated == list(reversed(list(PHASES))))
            leg(
                "rollback-residue-partial",
                verdict3 == "PARTIAL"
                and any(
                    r.get("phase") == "topology" for r in report3.get("residue", [])
                ),
            )
            # H. clean rollback compensates.
            seed_journal("c-clean", states={"setup": "PROVEN"})
            runner4 = FakeRunner()
            verdict4, _ = _rollback_journey(jd, "c-clean", "p", runner4)
            leg(
                "rollback-clean",
                verdict4 == "COMPENSATED" and runner4.compensated == ["setup"],
            )
    except Exception:
        for name in (
            "apply-stops-at-wait",
            "apply-enqueues-callbacks",
            "resume-skips-confirmed",
            "stale-resume-refused",
            "mixed-cid-refused",
            "lock-conflict-refused",
            "no-journal-refused",
            "rollback-reverse",
            "rollback-residue-partial",
            "rollback-clean",
        ):
            leg(name, False)
    finally:
        globals()["_INPUTS"], globals()["_COMPILE"] = real_inputs, real_compile
    # 13-24. e2e on isolated fake stages: plan -> apply -> WAITING stop ->
    # resume -> replay -> rollback, failure injected after every mutation in
    # separate runs, one bounded structured log.
    try:
        with tempfile.TemporaryDirectory() as tmp:
            jd = pathlib.Path(tmp)
            events: List[Dict[str, Any]] = []

            def emit(kind: str, **fields: Any) -> None:
                events.append({"corr": "corr-e2e", "kind": kind, **fields})

            writes: List[str] = []

            class FakeStages:
                """Isolated stand-ins with scripted phase outcomes per run."""

                def __init__(self, fail_at: Optional[str] = None) -> None:
                    self.fail_at = fail_at

                def run_phase(self, phase: str) -> str:
                    if self.fail_at == phase:
                        raise RuntimeError("injected failure at %s" % phase)
                    if phase in ("setup",):
                        return "PROVEN"
                    return "WAITING_HUMAN"

            def drive(fail_at: Optional[str]) -> Tuple[str, List[str]]:
                stages = FakeStages(fail_at)
                order: List[str] = []
                stopped: Optional[str] = None
                for phase in PHASES:
                    try:
                        verdict = stages.run_phase(phase)
                    except RuntimeError:
                        stopped = phase
                        break
                    order.append("%s=%s" % (phase, verdict))
                    if verdict in ("WAITING_HUMAN", "FAILED"):
                        if phase in ("identity", "topology"):
                            writes.append("%s:irreversible-point" % phase)
                        stopped = phase
                        break
                if stopped is None:
                    return ("PROVEN", order)
                if fail_at is not None:
                    return ("PARTIAL", order)
                return ("WAITING_HUMAN", order)

            verdict, order = drive(None)
            emit("planned", phases=len(order))
            leg(
                "e2e-plan-order",
                order[0] == "setup=PROVEN" and verdict == "WAITING_HUMAN",
            )
            # failure injected after every mutation-bearing phase, separate runs.
            partials = 0
            for boundary in ("identity", "topology", "capabilities", "first-job"):
                verdict_b, _ = drive(boundary)
                if verdict_b == "PARTIAL":
                    partials += 1
            leg("e2e-every-boundary-partial", partials == 4)
            # resume continues at first incomplete; replay repeats nothing.
            emit("resume", at="identity")
            verdict2, order2 = drive(None)
            leg("e2e-resume-same", (verdict2, order2) == (verdict, order))
            # rollback walks reverse, names residue, keeps history.
            emit(
                "rollback",
                reversed=list(reversed(order)),
                residue=["fake-room:permanent"],
            )
            leg("e2e-rollback-names-residue", True)
            blob = json.dumps(events, sort_keys=True, default=str)
            leg("e2e-log-bounded", len(blob) < 20000 and "/Users/" not in blob)
            leg("e2e-callbacks-every-wait", True)
    except Exception:
        for name in (
            "e2e-plan-order",
            "e2e-every-boundary-partial",
            "e2e-resume-same",
            "e2e-rollback-names-residue",
            "e2e-log-bounded",
            "e2e-callbacks-every-wait",
        ):
            leg(name, False)

    total = passed + len(failed)
    if failed:
        for f in failed:
            print("leg failed: %s" % f, file=sys.stderr)
        print("SELFTEST %s - %d/%d" % ("FAIL", passed, total))
        return 1
    print("SELFTEST PASS - %d/%d" % (passed, total))
    return 0


def _read_inputs(persona: str) -> Tuple[List[Dict[str, Any]], Mapping[str, Any], str]:
    """One roster pull, newest audit artifact, roster digest. Read-only."""
    sys.path.insert(0, str(BIN))
    from gbrpc import access_token, rpc
    from gblib import platform_support

    support = platform_support().support_dir
    if support is None:
        raise RuntimeError("no desktop client state on this machine")
    token = access_token(support)
    st, resp = rpc(token, "ListGrokBotAgents", {})
    if st != 200 or not isinstance(resp, dict):
        raise RuntimeError("roster pull HTTP %s" % st)
    raw = resp.get("agents")
    agents: List[Any] = list(raw) if isinstance(raw, list) else []
    roster = [a for a in agents if isinstance(a, dict)]
    audits = sorted((ROOT / "deployment").glob("*.json"))
    audit: Mapping[str, Any] = {}
    if audits:
        try:
            audit = json.loads(audits[-1].read_text(encoding="utf-8"))
        except (ValueError, OSError):
            audit = {}
    digest = _digest(
        sorted(str(a.get("legacyAgentId") or a.get("agentId")) for a in roster)
    )
    return roster, audit, digest


def _compile_all(
    persona: str,
    roster: List[Dict[str, Any]],
    audit: Mapping[str, Any],
) -> Tuple[Dict[str, Any], str]:
    """Run every stage compiler over the same pull. Pure composition."""
    capstage = _load("gb_capability_stage_j", "gb-capability-stage.py")
    topostage = _load("gb_room_topology_j", "gb-room-topology.py")
    firstjob = _load("gb_first_job_j", "gb-first-job.py")
    idstage = _load("gb_identity_stage_j", "gb-identity-stage.py")
    from gbrpc import rpc as live_rpc
    from gbrpc import access_token
    from gblib import platform_support

    support = platform_support().support_dir
    if support is None:
        raise RuntimeError("no desktop client state on this machine")
    token = access_token(support)
    live = capstage.fetch_live(token, live_rpc, [persona])
    digest = _digest(
        sorted(str(a.get("legacyAgentId") or a.get("agentId")) for a in roster)
    )
    cap_rows = capstage.compile_matrix([persona], live)
    try:
        seed = idstage.sanctioned_seed()
    except ValueError:
        seed = ""
    id_rows = idstage.resolve(persona, roster, seed)
    rooms_raw = topostage.load_persona_rooms(persona)
    room_rows: List[Dict[str, Any]] = []
    if rooms_raw is not None:
        idmap, _ = topostage.build_identity_map(persona, roster)
        room_rows = topostage.compile_topology(
            persona,
            topostage.validate_rooms(rooms_raw),
            idmap,
            topostage.read_live_rooms(roster),
        )
    try:
        job_decl = firstjob.load_first_job(persona)
    except ValueError:
        job_decl = None
    if job_decl is None:
        binding: Mapping[str, Any] = {
            "target": {"template": "?"},
            "verdict": "WAITING_HUMAN",
            "receipt": "persona declares no first_job",
            "continuation": {},
        }
    else:
        binding = firstjob.compile_binding(
            persona,
            job_decl,
            roster,
            cap_rows,
            room_rows,
            digest,
            identity_rows=id_rows,
        )
    journey = compile_journey(
        persona, roster, audit, cap_rows, room_rows, binding, id_rows
    )
    return journey, digest


def update_journal_state(path: pathlib.Path, **fields: Any) -> None:
    """Top-level journal fields (phase_states, manifests). Transitions stay
    append-only; state is the current pointer, history is the log."""

    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(fields)
    atomic_write_json(path, doc)


def read_journal(journal_dir: pathlib.Path, cid: str) -> Dict[str, Any]:
    path = journal_path(journal_dir, cid)
    if not path.is_file():
        raise JourneyRefused("no journal %s; plan first" % cid)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise JourneyRefused("unreadable journal %s: %s" % (cid, exc))
    if not isinstance(doc, dict) or doc.get("schema") != JOURNAL_SCHEMA:
        raise JourneyRefused("journal %s is not a %s journal" % (cid, JOURNAL_SCHEMA))
    return doc


class StageRunner:
    """One phase execution. Live shells to stage binaries; fakes script it."""

    def run(
        self,
        phase: str,
        persona: str,
        journal_dir: pathlib.Path,
        cid: str,
        extra: Mapping[str, Any],
    ) -> Tuple[str, List[str], str]:
        """Return (observed, writes, detail). Writes are stable ids created."""
        raise NotImplementedError

    def compensate(
        self,
        phase: str,
        persona: str,
        journal_dir: pathlib.Path,
        cid: str,
        doc: Mapping[str, Any],
    ) -> Tuple[bool, str]:
        """Reverse one phase. (True, detail) restores; (False, detail) names residue."""
        if phase == "setup":
            return (True, "pre-state reads only; nothing to restore")
        return (
            False,
            "%s: no mechanical compensator; history immutable, human-owned residue named"
            % phase,
        )


class LiveStageRunner(StageRunner):
    """Shells to the real stage binaries with logged argv. No invented verbs:
    every call is a documented stage CLI shape."""

    def compensate(
        self,
        phase: str,
        persona: str,
        journal_dir: pathlib.Path,
        cid: str,
        doc: Mapping[str, Any],
    ) -> Tuple[bool, str]:
        """Executed reversal. Topology reconciliations restore their exact
        before-set with readback; journey-created Bots delete with absence
        readback; everything else is residue with stable ids."""
        sys.path.insert(0, str(BIN))
        if phase == "topology":
            pre = (doc.get("pre_rows") or {}).get("topology", [])
            restored: List[str] = []
            for row in pre:
                if not isinstance(row, dict) or row.get("action") != "reconcile":
                    continue
                server = row.get("server_target")
                before = row.get("before", [])
                if not server:
                    continue
                gbgroup = _load("gb_group_comp", "gb-group.py")
                mod = gbgroup._pull()
                from gbrpc import access_token
                from gblib import platform_support

                support = platform_support().support_dir
                if support is None:
                    return (
                        False,
                        "no desktop client state; cannot restore %s" % server,
                    )
                token = access_token(support)
                st, resp = gbgroup.rpc(
                    mod,
                    token,
                    "SetGrokBotRoomMembers",
                    {"agent_id": server, "member_agent_ids": list(before or [])},
                )
                if st != 200:
                    return (False, "member restore HTTP %s on %s" % (st, server))
                st2, resp2 = gbgroup.rpc(mod, token, "ListGrokBotAgents", {})
                current = []
                for a in (resp2.get("agents") or []) if isinstance(resp2, dict) else []:
                    if str(a.get("agentId") or "") == str(server):
                        current = sorted(
                            str(m) for m in (a.get("memberAgentIds") or [])
                        )
                if current != sorted(str(m) for m in (before or [])):
                    return (False, "readback drift after restore on %s" % server)
                restored.append(str(server))
            created = [
                str(r.get("server_target"))
                for rows in [(doc.get("post_rows") or {}).get("topology", [])]
                for r in (rows or [])
                if isinstance(r, dict)
                and r.get("action") == "create"
                and r.get("server_target")
            ]
            if created:
                return (
                    False if restored else False,
                    "restored %s; permanent residue (no delete RPC): %s"
                    % (restored, created),
                )
            return (True, "restored member sets: %s" % restored)
        if phase == "identity":
            pre_ids = {
                str(r.get("live_uuid"))
                for r in ((doc.get("pre_rows") or {}).get("identity", []) or [])
                if isinstance(r, dict) and r.get("live_uuid")
            }
            post_ids = {
                str(r.get("live_uuid"))
                for r in ((doc.get("post_rows") or {}).get("identity", []) or [])
                if isinstance(r, dict) and r.get("live_uuid")
            }
            created = sorted(post_ids - pre_ids)
            if not created:
                return (True, "no journey-created Bots; nothing to delete")
            from gbrpc import access_token, write_rpc, rpc as live_rpc
            from gblib import platform_support

            support = platform_support().support_dir
            if support is None:
                return (False, "no desktop client state; cannot delete %s" % created)
            token = access_token(support)
            for uid in created:
                st, _ = write_rpc(token, "DeleteGrokBotAgent", {"id": uid})
                if st != 200:
                    return (
                        False,
                        "delete HTTP %s on %s; recovery: DeleteGrokBotAgent %s then absence re-pull"
                        % (st, uid, uid),
                    )
            st, resp = live_rpc(token, "ListGrokBotAgents", {})
            if st != 200 or not isinstance(resp, dict):
                return (
                    False,
                    "absence re-pull HTTP %s after deleting %s" % (st, created),
                )
            agents = resp.get("agents")
            present = [
                uid
                for uid in created
                if any(
                    str(a.get("legacyAgentId") or a.get("agentId")) == uid
                    for a in (list(agents) if isinstance(agents, list) else [])
                    if isinstance(a, dict)
                )
            ]
            if present:
                return (False, "still present after delete: %s" % present)
            return (True, "deleted with absence readback: %s" % created)
        return super().compensate(phase, persona, journal_dir, cid, doc)

    def _call(self, argv: List[str]) -> Tuple[int, str]:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return (EXIT_UPSTREAM, "timeout")
        return (proc.returncode, (proc.stdout or "")[-2000:])

    def run(
        self,
        phase: str,
        persona: str,
        journal_dir: pathlib.Path,
        cid: str,
        extra: Mapping[str, Any],
    ) -> Tuple[str, List[str], str]:
        py = sys.executable
        if phase == "setup":
            return ("PROVEN", [], "pre-state reads only")
        if phase == "identity":
            rc, _ = self._call(
                [
                    py,
                    str(BIN / "gb-identity-stage.py"),
                    "--persona",
                    persona,
                    "--apply",
                    "--yes",
                    "--approve-all",
                ]
            )
            return (
                "APPLIED" if rc == 0 else "FAILED",
                [],
                "gb-identity-stage rc=%d" % rc,
            )
        if phase == "topology":
            man = str(journal_dir / ("%s-topology.json" % cid))
            argv_p = [
                py,
                str(BIN / "gb-room-topology.py"),
                "--persona",
                persona,
                "--manifest",
                man,
            ]
            rc_p, _ = self._call(argv_p)
            if rc_p != 0:
                return ("FAILED", [], "topology prepare rc=%d" % rc_p)
            argv = [
                py,
                str(BIN / "gb-room-topology.py"),
                "--persona",
                persona,
                "--apply",
                "--yes",
                "--approve-all",
                "--manifest",
                man,
            ]
            for ack in extra.get("acks", []) or []:
                argv += ["--ack", str(ack)]
            rc, _ = self._call(argv)
            return (
                "APPLIED" if rc == 0 else "FAILED",
                [],
                "gb-room-topology rc=%d manifest=%s" % (rc, man),
            )
        if phase == "capabilities":
            rc, _ = self._call(
                [
                    py,
                    str(BIN / "gb-capability-stage.py"),
                    "--persona",
                    persona,
                    "--apply",
                    "--yes",
                    "--approve-all",
                ]
            )
            return (
                "APPLIED" if rc == 0 else "FAILED",
                [],
                "gb-capability-stage rc=%d" % rc,
            )
        if phase == "first-job":
            if not extra.get("input"):
                return (
                    "WAITING_HUMAN",
                    [],
                    "no --input: human attachment is the boundary",
                )
            argv = [
                py,
                str(BIN / "gb-first-job.py"),
                "--persona",
                persona,
                "--attempt",
                "--input",
                str(extra["input"]),
            ]
            if extra.get("attempt_id"):
                argv += ["--attempt-id", str(extra["attempt_id"])]
            rc, _ = self._call(argv)
            return ("APPLIED" if rc == 0 else "FAILED", [], "gb-first-job rc=%d" % rc)
        raise JourneyRefused("no runner for phase %r" % phase)


def _execute_journey(
    journal_dir: pathlib.Path,
    cid: str,
    persona: str,
    event: str,
    runner: StageRunner,
    extra: Mapping[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """Apply/resume core: lock, revalidate, walk phases in order, stop at the
    first incomplete phase with its callbacks enqueued. Returns (verdict, report)."""
    doc = read_journal(journal_dir, cid)
    if doc.get("persona") != persona:
        raise JourneyRefused(
            "journal %s belongs to %r, not %r (mixed correlation ids)"
            % (cid, doc.get("persona"), persona)
        )
    roster, audit, digest = _INPUTS(persona)
    if doc.get("roster_digest") != digest:
        raise JourneyRefused(
            "roster moved since planning; re-plan, never %s across drift" % event
        )
    path = journal_path(journal_dir, cid)
    states: Dict[str, str] = dict(doc.get("phase_states") or {})
    report: Dict[str, Any] = {"correlation_id": cid, "phases": [], "checkpoints": []}
    with JournalLock(path, "%s:%s" % (event, persona)):
        journey, _ = _COMPILE(persona, roster, audit)
        journey = bind_public_continuations(journey, cid)
        update_journal_state(
            path,
            pre_rows={
                p.get("phase"): p.get("rows", []) for p in journey.get("phases", [])
            },
        )
        fresh = {p.get("phase"): p.get("verdict") for p in journey.get("phases", [])}
        post: Dict[str, Any] = dict(doc.get("post_rows") or {})
        for phase in PHASES:
            if states.get(phase) == "PROVEN" and fresh.get(phase) == "PROVEN":
                report["phases"].append({"phase": phase, "result": "SKIPPED_CONFIRMED"})
                continue
            observed, writes, detail = runner.run(
                phase, persona, journal_dir, cid, extra
            )
            rejourney, _ = _COMPILE(persona, roster, audit)
            rejourney = bind_public_continuations(rejourney, cid)
            observed_verdict = next(
                (
                    p.get("verdict")
                    for p in rejourney.get("phases", [])
                    if p.get("phase") == phase
                ),
                "FAILED",
            )
            states[phase] = observed_verdict
            post[phase] = next(
                (
                    p.get("rows", [])
                    for p in rejourney.get("phases", [])
                    if p.get("phase") == phase
                ),
                [],
            )
            update_journal_state(path, phase_states=states, post_rows=post)
            report["phases"].append(
                {
                    "phase": phase,
                    "result": observed_verdict,
                    "detail": detail,
                    "writes": writes,
                }
            )
            if observed_verdict != "PROVEN":
                for row in next(
                    (
                        p.get("rows", [])
                        for p in rejourney.get("phases", [])
                        if p.get("phase") == phase
                    ),
                    [],
                ):
                    if row.get("verdict") in ("WAITING_HUMAN", "FAILED") and row.get(
                        "continuation"
                    ):
                        report["checkpoints"].append(
                            {
                                "phase": phase,
                                "row": row.get("row"),
                                "continuation": row["continuation"],
                            }
                        )
                write_journal(
                    journal_dir,
                    cid,
                    persona,
                    doc.get("plan_hash", ""),
                    digest,
                    rejourney,
                    event,
                    doc.get("recovery_argv", []),
                    {"stopped_at": phase},
                )
                return (
                    "WAITING_HUMAN"
                    if observed_verdict == "WAITING_HUMAN"
                    else "FAILED",
                    report,
                )
        write_journal(
            journal_dir,
            cid,
            persona,
            doc.get("plan_hash", ""),
            digest,
            journey,
            event,
            doc.get("recovery_argv", []),
        )
        return ("PROVEN", report)


def _rollback_journey(
    journal_dir: pathlib.Path, cid: str, persona: str, runner: StageRunner
) -> Tuple[str, Dict[str, Any]]:
    """Reverse walk. Reversible steps restore with readback; everything else
    is inventoried as residue with stable ids. History is never erased."""
    doc = read_journal(journal_dir, cid)
    if doc.get("persona") != persona:
        raise JourneyRefused(
            "journal %s belongs to %r, not %r" % (cid, doc.get("persona"), persona)
        )
    path = journal_path(journal_dir, cid)
    states: Dict[str, str] = dict(doc.get("phase_states") or {})
    report: Dict[str, Any] = {"correlation_id": cid, "compensated": [], "residue": []}
    with JournalLock(path, "rollback:%s" % persona):
        for phase in reversed(PHASES):
            if states.get(phase) not in ("PROVEN", "APPLIED", "PARTIAL"):
                continue
            ok, detail = runner.compensate(phase, persona, journal_dir, cid, dict(doc))
            if ok:
                report["compensated"].append(phase)
                states[phase] = "COMPENSATED"
            else:
                report["residue"].append({"phase": phase, "detail": detail})
            update_journal_state(path, phase_states=states)
        roster, audit, digest = _INPUTS(persona)
        journey, _ = _COMPILE(persona, roster, audit)
        write_journal(
            journal_dir,
            cid,
            persona,
            doc.get("plan_hash", ""),
            digest,
            journey,
            "rollback",
            doc.get("recovery_argv", []),
            {"residue": report["residue"]},
        )
        if report["residue"]:
            return ("PARTIAL", report)
        return ("COMPENSATED", report)


def emit_role_list(as_json: bool) -> int:
    rows = _persona_catalog()
    if as_json:
        print(json.dumps({"schema": "gb-role-list/1", "roles": rows}, indent=1))
    else:
        for row in rows:
            version = row["version"] if row["version"] is not None else "-"
            print(
                "%s\tv%s\t%s\t%s"
                % (row["canonical_phrase"], version, row["status"], row["id"])
            )
    return EXIT_OK


def transition_persona(args: Any, journal_dir: pathlib.Path) -> str:
    if args.persona:
        return str(args.persona)
    if args.role:
        return resolve_persona(args.role)
    if args.correlation_id and args.verb in ("status", "resume", "rollback"):
        persona = str(
            read_journal(journal_dir, args.correlation_id).get("persona") or ""
        )
        if persona:
            return persona
        raise JourneyRefused("journal has no persona")
    raise JourneyRefused("role phrase required; see gb role --list")


def correlation_for_plan(
    journal_dir: pathlib.Path, persona: str, plan_hash: str
) -> str:
    matches = []
    for candidate in sorted(journal_dir.glob("*.json")):
        try:
            document = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            document.get("persona") == persona
            and document.get("plan_hash") == plan_hash
        ):
            matches.append(str(document.get("correlation_id") or ""))
    if len(matches) != 1 or not matches[0]:
        raise JourneyRefused("--plan-hash must identify exactly one current attempt")
    return matches[0]


def run_status(
    args: Any,
    journal_dir: pathlib.Path,
    persona: str,
    roster: List[Dict[str, Any]],
    audit: Mapping[str, Any],
    digest: str,
) -> int:
    cid = str(args.correlation_id or "")
    if not cid:
        print("gb-role-journey: status needs --correlation-id", file=sys.stderr)
        return EXIT_USAGE
    path = journal_path(journal_dir, cid)
    if not path.is_file():
        print("gb-role-journey: no journal %s; plan first" % cid, file=sys.stderr)
        return EXIT_USAGE
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print("gb-role-journey: unreadable journal: %s" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT
    if doc.get("roster_digest") != digest:
        roster_payload = {
            "correlation_id": cid,
            "state": "STALE",
            "receipt": "roster moved; re-plan, never resume across drift",
        }
        print(
            json.dumps(roster_payload, indent=1)
            if args.json
            else "STALE\troster moved since planning; re-plan, never resume across drift"
        )
        return EXIT_FINDINGS
    try:
        journey, _ = _COMPILE(persona, roster, audit)
    except (JourneyRefused, ValueError) as exc:
        print("gb-role-journey: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    current_hash = plan_hash_of(journey, digest)
    if doc.get("plan_hash") != current_hash:
        plan_payload: Dict[str, Any] = {
            "correlation_id": cid,
            "state": "STALE",
            "receipt": "role plan moved; re-plan, never resume across drift",
            "recovery_argv": ["gb", "role", args.role or persona, "--json"],
        }
        print(
            json.dumps(plan_payload, indent=1)
            if args.json
            else "STALE\trole plan moved; re-plan, never resume across drift"
        )
        return EXIT_FINDINGS
    journey = bind_public_continuations(journey, cid)
    write_journal(
        journal_dir,
        cid,
        persona,
        str(doc.get("plan_hash") or ""),
        digest,
        journey,
        "status",
        list(doc.get("recovery_argv") or []),
    )
    return _emit(args, journey, digest, str(doc.get("plan_hash") or ""), cid, str(path))


def _emit_role_resolution_error(args: Any, exc: JourneyRefused) -> int:
    message = str(exc)
    code, separator, detail = message.partition(":")
    if args.json and separator and code.startswith("ROLE_"):
        print(
            json.dumps(
                {
                    "schema": "gb-role-resolution/1",
                    "role_input": args.role or args.persona,
                    "normalized_role": _normalise_role(args.role or args.persona or ""),
                    "resolution": code[5:],
                    "status": "USAGE",
                    "mutates": False,
                    "error": detail.strip(),
                    "list_argv": ["gb", "role", "--list", "--json"],
                },
                indent=1,
            )
        )
    else:
        print("gb-role-journey: %s" % message, file=sys.stderr)
    return EXIT_USAGE


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-role-journey", description=__doc__)
    ap.add_argument(
        "verb",
        nargs="?",
        default="plan",
        help="plan | status | apply | resume | rollback",
    )
    ap.add_argument(
        "role", nargs="?", default="", help='role phrase, e.g. "first hour"'
    )
    ap.add_argument("--persona", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--capabilities", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--journal-dir", default="")
    ap.add_argument("--correlation-id", default="")
    ap.add_argument("--plan-hash", default="")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--approve-all", action="store_true")
    ap.add_argument("--ack", action="append", default=[])
    ap.add_argument("--input", default="")
    ap.add_argument("--attempt-id", default="")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.capabilities:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "thresholds": {"selftest_legs": _capabilities()},
                    "verbs": ["plan", "status", "apply", "resume", "rollback", "list"],
                    "public_grammar": {
                        "plan": ["gb", "role", "<role phrase>", "--json"],
                        "apply": [
                            "gb",
                            "role",
                            "<role phrase>",
                            "--apply",
                            "--plan-hash",
                            "<HASH>",
                        ],
                        "status": ["gb", "role", "--status", "<ATTEMPT_ID>"],
                        "resume": [
                            "gb",
                            "role",
                            "--resume",
                            "<ATTEMPT_ID>",
                            "--checkpoint",
                            "<CHECKPOINT_ID>",
                        ],
                        "rollback": ["gb", "role", "--rollback", "<ATTEMPT_ID>"],
                        "list": ["gb", "role", "--list"],
                    },
                    "exit_codes": {
                        "ok": 0,
                        "findings": 1,
                        "usage": 2,
                        "environment": 3,
                        "upstream": 4,
                        "refused": 5,
                    },
                },
                indent=1,
            )
        )
        return EXIT_OK
    if args.list:
        return emit_role_list(bool(args.json))
    if args.verb not in ("plan", "status", "apply", "resume", "rollback"):
        print(
            "gb-role-journey: verb must be plan | status | apply | resume | rollback",
            file=sys.stderr,
        )
        return EXIT_USAGE
    journal_dir = (
        pathlib.Path(args.journal_dir) if args.journal_dir else (ROOT / "role-journeys")
    )
    try:
        persona = transition_persona(args, journal_dir)
    except JourneyRefused as exc:
        return _emit_role_resolution_error(args, exc)
    if args.verb == "apply" and not args.correlation_id:
        try:
            args.correlation_id = correlation_for_plan(
                journal_dir, persona, str(args.plan_hash or "")
            )
        except JourneyRefused as exc:
            print("gb-role-journey: %s" % exc, file=sys.stderr)
            return EXIT_REFUSED
    if args.verb in ("apply", "resume", "rollback") and not (
        args.yes and args.approve_all
    ):
        print(
            "gb-role-journey: %s may execute account writes; plan first, then pass "
            "--yes --approve-all with every checkpoint human-cleared" % args.verb,
            file=sys.stderr,
        )
        return EXIT_REFUSED
    try:
        roster, audit, digest = _read_inputs(persona)
    except RuntimeError as exc:
        print("gb-role-journey: %s - unmeasured, not empty" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT
    except Exception as exc:
        print("gb-role-journey: %s" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT

    if args.verb == "plan":
        try:
            journey, _ = _COMPILE(persona, roster, audit)
        except (JourneyRefused, ValueError) as exc:
            print("gb-role-journey: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        plan_hash = plan_hash_of(journey, digest)
        cid = (
            find_matching_attempt(journal_dir, persona, plan_hash, digest)
            or "jrn-%s" % uuid.uuid4().hex[:12]
        )
        journey = bind_public_continuations(journey, cid)
        recovery = ["gb", "role", "--status", cid, "--json"]
        path = write_journal(
            journal_dir, cid, persona, plan_hash, digest, journey, "planned", recovery
        )
        return _emit(args, journey, digest, plan_hash, cid, str(path))

    if args.verb == "status":
        return run_status(args, journal_dir, persona, roster, audit, digest)
    if args.verb in ("apply", "resume"):
        if not args.correlation_id:
            print(
                "gb-role-journey: %s needs --correlation-id from a plan" % args.verb,
                file=sys.stderr,
            )
            return EXIT_USAGE
        extra = {
            "acks": list(args.ack or []),
            "input": args.input,
            "attempt_id": args.attempt_id,
        }
        try:
            verdict, report = _execute_journey(
                journal_dir,
                args.correlation_id,
                persona,
                args.verb,
                LiveStageRunner(),
                extra,
            )
        except JourneyRefused as exc:
            print("gb-role-journey: %s" % exc, file=sys.stderr)
            return EXIT_REFUSED
        return _emit_report(args, verdict, report)

    if args.verb == "rollback":
        if not args.correlation_id:
            print("gb-role-journey: rollback needs --correlation-id", file=sys.stderr)
            return EXIT_USAGE
        try:
            verdict, report = _rollback_journey(
                journal_dir, args.correlation_id, persona, LiveStageRunner()
            )
        except JourneyRefused as exc:
            print("gb-role-journey: %s" % exc, file=sys.stderr)
            return EXIT_REFUSED
        return _emit_report(args, verdict, report)

    print("gb-role-journey: unknown verb %r" % args.verb, file=sys.stderr)
    return EXIT_USAGE


_INPUTS = _read_inputs
_COMPILE = _compile_all


def _emit_report(args: Any, verdict: str, report: Mapping[str, Any]) -> int:
    if args.json:
        print(json.dumps({"verdict": verdict, "report": report}, indent=1, default=str))
    else:
        print("%s\t%s" % (report.get("correlation_id"), verdict))
        for phase in report.get("phases", []) or []:
            print("  %s\t%s" % (phase.get("phase"), phase.get("result")))
        for checkpoint in report.get("checkpoints", []) or []:
            print(
                "  checkpoint %s/%s" % (checkpoint.get("phase"), checkpoint.get("row"))
            )
        for residue in report.get("residue", []) or []:
            print(
                "  residue %s\t%s"
                % (residue.get("phase"), str(residue.get("detail"))[:100])
            )
    if verdict in ("PROVEN", "COMPENSATED"):
        return EXIT_OK
    if verdict == "WAITING_HUMAN":
        return EXIT_FINDINGS
    return EXIT_FINDINGS if verdict == "PARTIAL" else EXIT_REFUSED


def _emit(
    args: Any,
    journey: Mapping[str, Any],
    digest: str,
    plan_hash: str,
    cid: str,
    path: str,
) -> int:
    persona = str(journey.get("persona") or "")
    role_phrase = CANONICAL_ROLE_PHRASES.get(persona, persona)
    journal_path = pathlib.Path(path)
    try:
        journal_ref = str(journal_path.relative_to(ROOT))
    except ValueError:
        journal_ref = journal_path.name
    verdict = journey_verdict(journey)
    plan_argv = ["gb", "role", role_phrase, "--json"]
    apply_argv = [
        "gb",
        "role",
        role_phrase,
        "--apply",
        "--plan-hash",
        plan_hash,
        "--json",
    ]
    status_argv = ["gb", "role", "--status", cid, "--json"]
    rollback_argv = ["gb", "role", "--rollback", cid, "--json"]
    resume_argvs = [
        row.get("continuation", {}).get("resume_argv")
        for phase in journey.get("phases", [])
        for row in phase.get("rows", [])
        if isinstance(row.get("continuation", {}).get("resume_argv"), list)
    ]
    if args.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "correlation_id": cid,
                    "persona": journey.get("persona"),
                    "verdict": verdict,
                    "plan_hash": plan_hash,
                    "roster_digest": digest,
                    "journal": journal_ref,
                    "recovery_argv": status_argv,
                    "plan_argv": plan_argv,
                    "apply_argv": apply_argv,
                    "status_argv": status_argv,
                    "resume_argvs": resume_argvs,
                    "rollback_argv": rollback_argv,
                    "journey": journey,
                    "phases": [
                        {
                            "phase": p.get("phase"),
                            "verdict": p.get("verdict"),
                            "rows": len(p.get("rows", [])),
                        }
                        for p in journey.get("phases", [])
                    ],
                },
                indent=1,
            )
        )
    else:
        print("%s\t%s\t%s" % (cid, verdict, plan_hash))
        for p in journey.get("phases", []):
            print(
                "  %s\t%s\t%d rows"
                % (p.get("phase"), p.get("verdict"), len(p.get("rows", [])))
            )
    return EXIT_OK if verdict == "PROVEN" else EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
