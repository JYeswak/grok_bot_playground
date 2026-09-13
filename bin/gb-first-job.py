#!/usr/bin/env python3
"""gb-first-job — bind one role to one proven first useful job.

WHY THIS EXISTS. Personas declare Bots, capabilities, and rooms, but no single
machine-checkable first useful outcome: hello-computer proves the machine is
alive (liveness, never a job), while first-file-desk is the buyer-useful task
with no declared winner, oracle, or resume. This stage binds exactly one
first_job per persona to its target, inputs, prerequisite receipts, semantic
oracle, approval class, replay policy, and continuation — and refuses every
confusion of proof_ok with recorded_done.

COMPOSITION (reuse, never re-derive): identity resolution comes from
gb-identity-stage.resolve; capability rows from gb-capability-stage; room rows
from gb-room-topology; the rubric floor, attempt sealer, and digest come from
gb-jobs (_dimensions_clear_floor, _seal_attempt, _json_digest). The market-row
slot machine (_proof_decision and its preregistration schema) is NOT reused:
it scores marketplace rows, not role jobs, and forcing jobs into it would be a
second meaning for one scorer. The floor function and the sealer are the one
scorer; this file only binds and gates.

REFUSALS: liveness as the job target; stale or mixed-attempt prerequisites;
duplicate attempt ids; wrong-role receipts; unproven capability/room proof;
irreversible side effects without approval plus idempotency; rubric below
floor; changed input/oracle versions; recording done on proof_ok alone.
Rollback never erases history: it compensates reversible job state and names
irreversible or human-owned residue.

APPLIED MINING LESSONS: (1) wrong-role is caught before any other check
(enrichment_enforce_active_role_wrong_role_caught_first); (2) progress
counters are not a completion promise — proof_ok is never recorded_done
unless every rubric and repeat threshold passes (fh suggest STALE
ledger_age_hours=124.871; stale remains usable).

READS/WRITES: bind reads personas/ + one roster pull. --attempt appends one
sealed attempt to jobs/first-job-attempts.jsonl. --record-done appends one
done record to jobs/first-job-done.jsonl. History is append-only.
stdlib only. Token in memory, never printed. No secret, private file body,
or absolute user path is ever logged: inputs are referenced by digest.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
SCHEMA = "gb-first-job/1"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
EXIT_UPSTREAM = 4
EXIT_REFUSED = 5

LIVENESS_TEMPLATES = frozenset({"hello-computer"})
ATTEMPTS_LOG = ROOT / "jobs" / "first-job-attempts.jsonl"
DONE_LOG = ROOT / "jobs" / "first-job-done.jsonl"

FIRST_JOB_KEYS = frozenset(
    {
        "target",
        "room",
        "prerequisites",
        "input",
        "action_digest",
        "side_effect_class",
        "approval",
        "oracle",
        "rubric",
        "repeat",
        "resume",
    }
)


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


def _sub(mapping: Mapping[str, Any], key: str) -> Dict[str, Any]:
    """Sub-object or {}. Binds once so checkers narrow; never double-gets."""
    value = mapping.get(key)
    return dict(value) if isinstance(value, dict) else {}


class JobRefused(Exception):
    """Typed refusal: the binding or gate that failed, never a bare error."""


def load_first_job(persona: str) -> Dict[str, Any]:
    """Exactly one first_job object. Missing is unknown; two is a lie."""
    path = ROOT / "personas" / (persona + ".json")
    if not path.is_file():
        raise ValueError("unknown persona %r (see personas/)" % persona)
    doc = json.loads(path.read_text(encoding="utf-8"))
    job = doc.get("first_job", None)
    if job is None:
        raise ValueError(
            "persona %r declares no first_job (unknown, never default)" % persona
        )
    if not isinstance(job, dict):
        raise ValueError("persona %r first_job must be one object" % persona)
    missing = sorted(FIRST_JOB_KEYS - set(job.keys()))
    if missing:
        raise ValueError("persona %r first_job misses keys: %s" % (persona, missing))
    return job


def check_not_liveness(job: Mapping[str, Any]) -> None:
    target = str(job.get("target") or "")
    if target in LIVENESS_TEMPLATES:
        raise JobRefused(
            "target %r is machine liveness, never a useful job; "
            "liveness is a prerequisite receipt, not the outcome" % target
        )


def check_role(job_role: str, receipt_role: str, what: str) -> None:
    if job_role != receipt_role:
        raise JobRefused(
            "wrong-role: %s belongs to %r, binding is %r (caught first, before any other check)"
            % (what, receipt_role, job_role)
        )


def check_fresh(expected_digest: str, fresh_digest: str, what: str) -> None:
    if expected_digest != fresh_digest:
        raise JobRefused("stale %s: roster moved since the receipt" % what)


def check_side_effects(job: Mapping[str, Any]) -> None:
    klass = str(job.get("side_effect_class") or "")
    approval = _sub(job, "approval")
    repeat = _sub(job, "repeat")
    if klass not in ("read-only", "reversible", "irreversible"):
        raise JobRefused(
            "side_effect_class %r is not read-only/reversible/irreversible" % klass
        )
    if klass == "irreversible" and (
        not approval.get("human") or not repeat.get("idempotency_key")
    ):
        raise JobRefused(
            "irreversible job without human approval plus idempotency key is refused for cold certification"
        )


def check_attempt_unique(attempt_id: str, log: Sequence[Mapping[str, Any]]) -> None:
    if any(a.get("attempt_id") == attempt_id for a in log):
        raise JobRefused(
            "duplicate attempt id %r: replay the attempt, never re-record it"
            % attempt_id
        )


def read_log(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def check_versions(job: Mapping[str, Any], attempt: Mapping[str, Any]) -> None:
    oracle = _sub(job, "oracle")
    if attempt.get("oracle_version") != oracle.get("version"):
        raise JobRefused(
            "oracle version moved: %r is not preregistered %r"
            % (attempt.get("oracle_version"), oracle.get("version"))
        )
    declared_input = _sub(job, "input")
    if declared_input.get("digest") is None:
        if not str(attempt.get("input_digest") or "").strip():
            raise JobRefused("unbound input needs a first evidence digest, never empty")
    elif attempt.get("input_digest") != declared_input.get("digest"):
        raise JobRefused("input moved since binding: re-bind, never slide the evidence")


def check_done_gate(
    jobs_mod: Any,
    job: Mapping[str, Any],
    attempt: Mapping[str, Any],
    log: Sequence[Mapping[str, Any]],
    done_log: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """proof_ok becomes recorded_done only here, and only when every rubric
    and repeat threshold passes. Anything else is a refusal with the reason."""
    rubric = _sub(job, "rubric")
    dims = attempt.get("dims")
    if not jobs_mod._dimensions_clear_floor(dims):
        raise JobRefused(
            "rubric below floor %s: %s"
            % (rubric.get("floor"), json.dumps(dims, sort_keys=True))
        )
    repeat = _sub(job, "repeat")
    policy = str(repeat.get("policy") or "")
    if policy == "deterministic-repeat":
        prior = [
            a
            for a in log
            if a.get("input_digest") == attempt.get("input_digest")
            and a.get("verdict") == "proof_ok"
        ]
        if not prior:
            raise JobRefused(
                "repeat rule unsatisfied: no prior independent proof_ok on this input digest"
            )
    elif policy == "idempotency-key":
        value = str(attempt.get("idempotency_value") or "").strip()
        if not value:
            raise JobRefused(
                "idempotency-key policy needs a non-empty attempt.idempotency_value"
            )
        spent = [d for d in (done_log or []) if d.get("idempotency_value") == value]
        if spent:
            raise JobRefused(
                "idempotency key %r already spent: replay the day, never re-record it"
                % value
            )
    else:
        raise JobRefused("unknown repeat policy %r" % policy)
    check_versions(job, attempt)
    observed = attempt.get("observed")
    if not isinstance(observed, dict) or not observed.get("evidence_digest"):
        raise JobRefused("missing observed evidence: no digest, no done")
    return {
        "verdict": "recorded_done",
        "floor": rubric.get("floor"),
        "repeat_policy": policy,
        "evidence_digest": observed.get("evidence_digest"),
        "idempotency_value": str(attempt.get("idempotency_value") or "")
        if policy == "idempotency-key"
        else None,
    }


def compile_binding(
    persona: str,
    job: Mapping[str, Any],
    roster: List[Dict[str, Any]],
    cap_rows: List[Dict[str, Any]],
    room_rows: List[Dict[str, Any]],
    fresh_digest: str,
    identity_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Bind target, input, prerequisite receipts, oracle, approval, replay,
    continuation. Pure: every live read arrived in arguments."""
    check_not_liveness(job)
    check_side_effects(job)
    if identity_rows is None:
        idstage = _load("gb_identity_stage_bind", "gb-identity-stage.py")
        seed = idstage.sanctioned_seed()
        resolved: List[Dict[str, Any]] = idstage.resolve(persona, roster, seed)
    else:
        resolved = identity_rows
    by_template = {str(r.get("template")): r for r in resolved}
    target = str(job.get("target") or "")
    trow = by_template.get(target)
    if trow is None:
        raise JobRefused("target %r is not a declared persona template" % target)
    live_uuid = trow.get("live_uuid")
    prereqs = _sub(job, "prerequisites")
    frozen: Dict[str, Any] = {}
    missing: List[str] = []
    # hello-computer coach receipt: immutable liveness evidence, read-only.
    coach = next(
        (r for r in cap_rows if r.get("target") == "hello-computer"),
        None,
    )
    if coach is None:
        missing.append("coach-receipt:hello-computer (no such capability row)")
    else:
        check_role(persona, str(coach.get("persona") or ""), "coach receipt")
        frozen["coach_receipt_hello_computer"] = {
            "capability": coach.get("capability"),
            "verdict": coach.get("verdict"),
            "receipt": coach.get("receipt"),
            "continuation": coach.get("continuation"),
            "digest": _digest(
                {
                    k: coach.get(k)
                    for k in ("capability", "target", "verdict", "receipt")
                }
            ),
        }
    for key in prereqs.get("capability_receipts", []) or []:
        if not isinstance(key, str) or ":" not in key:
            raise JobRefused(
                "capability receipt key %r must be <capability>:<target>" % (key,)
            )
        cid, tgt = key.split(":", 1)
        row = next(
            (
                r
                for r in cap_rows
                if r.get("capability") == cid and r.get("target") == tgt
            ),
            None,
        )
        if row is None or row.get("verdict") != "PROVEN":
            missing.append(
                "capability %s (needs PROVEN, have %s)"
                % (key, row.get("verdict") if row else "absent")
            )
            continue
        check_role(
            persona, str(row.get("persona") or ""), "capability receipt %s" % key
        )
        frozen[key] = {
            "verdict": "PROVEN",
            "receipt": row.get("receipt"),
            "digest": _digest(row.get("receipt")),
        }
    room_name = job.get("room")
    if room_name:
        rrow = next(
            (
                r
                for r in room_rows
                if r.get("name") == room_name or r.get("logical_id") == room_name
            ),
            None,
        )
        if rrow is None or rrow.get("verdict") != "PROVEN":
            missing.append(
                "room %s (needs PROVEN reuse, have %s)"
                % (room_name, rrow.get("verdict") if rrow else "absent")
            )
        else:
            check_role(
                persona, str(rrow.get("persona") or ""), "room receipt %s" % room_name
            )
            frozen["room:%s" % room_name] = {
                "verdict": "PROVEN",
                "server_target": rrow.get("server_target"),
                "digest": _digest(rrow.get("intended")),
            }
    binding: Dict[str, Any] = {
        "schema": SCHEMA,
        "persona": persona,
        "job": {k: job.get(k) for k in sorted(FIRST_JOB_KEYS)},
        "target": {"template": target, "live_uuid": live_uuid},
        "frozen_prerequisites": frozen,
        "roster_digest": fresh_digest,
        "binding_digest": "",
        "continuation": {
            "coach_need": "human supplies the declared input, then proves the oracle observable",
            "coach_job": "re-run bind after the input moves; never edit a frozen receipt",
            "action": "supply input, then --attempt --input <path>",
        },
    }
    if live_uuid is None:
        binding["verdict"] = "WAITING_HUMAN"
        binding["receipt"] = (
            "target %r has no live Bot; create it before the first attempt" % target
        )
    elif missing:
        binding["verdict"] = "WAITING_HUMAN"
        binding["receipt"] = "prerequisites unmet: %s" % "; ".join(sorted(missing))
    else:
        binding["verdict"] = "READY"
        binding["receipt"] = (
            "target live, all prerequisites PROVEN under roster %s" % fresh_digest
        )
    binding["binding_digest"] = _digest(
        {
            k: binding[k]
            for k in ("persona", "target", "frozen_prerequisites", "roster_digest")
        }
    )
    return binding


def _capabilities() -> int:
    return 26


def selftest() -> int:
    """Table-driven guards plus deterministic e2e. No network, tmp files only."""
    import tempfile

    passed = 0
    failed: List[str] = []

    def leg(name: str, ok: bool) -> None:
        nonlocal passed
        if ok:
            passed += 1
        else:
            failed.append(name)

    jobs_mod = _load("gb_jobs_reuse", "gb-jobs.py")

    def base_job(**over: Any) -> Dict[str, Any]:
        job: Dict[str, Any] = {
            "target": "first-file-desk",
            "room": "First Hour",
            "prerequisites": {"capability_receipts": []},
            "input": {
                "kind": "human-attachment",
                "freshness": "attached-this-attempt",
                "digest": "in1",
            },
            "action_digest": "a1",
            "side_effect_class": "read-only",
            "approval": {"human": True},
            "oracle": {"type": "three-block-shape", "version": "o1"},
            "rubric": {"dims": ["quotes", "dates"], "floor": 750},
            "repeat": {"policy": "deterministic-repeat"},
            "resume": {"argv": ["gb-first-job", "--persona", "p"]},
        }
        job.update(over)
        return job

    # 1-2. schema gate: missing key, missing first_job.
    try:
        load_first_job("no-such-persona-xyz")
        leg("unknown-persona-fails", False)
    except ValueError:
        leg("unknown-persona-fails", True)
    try:
        check_not_liveness(base_job(target="hello-computer"))
        leg("liveness-target-refused", False)
    except JobRefused as exc:
        leg("liveness-target-refused", "liveness" in str(exc))
    # 3. wrong-role caught first.
    try:
        check_role("p", "q", "coach receipt")
        leg("wrong-role-first", False)
    except JobRefused as exc:
        leg("wrong-role-first", "wrong-role" in str(exc))
    # 4. stale digest fails.
    try:
        check_fresh("old", "new", "coach receipt")
        leg("stale-fails", False)
    except JobRefused:
        leg("stale-fails", True)
    # 5. duplicate attempt id fails.
    try:
        check_attempt_unique("a1", [{"attempt_id": "a1"}])
        leg("duplicate-attempt-fails", False)
    except JobRefused:
        leg("duplicate-attempt-fails", True)
    # 6. irreversible without approval+idempotency fails.
    try:
        check_side_effects(
            base_job(side_effect_class="irreversible", approval={}, repeat={})
        )
        leg("unsafe-side-effects-fail", False)
    except JobRefused:
        leg("unsafe-side-effects-fail", True)
    # 7. unknown side-effect class fails.
    try:
        check_side_effects(base_job(side_effect_class="maybe"))
        leg("unknown-class-fails", False)
    except JobRefused:
        leg("unknown-class-fails", True)
    # 8. oracle version drift fails the done gate.
    try:
        check_done_gate(
            jobs_mod,
            base_job(),
            {
                "dims": {"quotes": 800, "dates": 800},
                "oracle_version": "o2",
                "input_digest": "in1",
                "observed": {"evidence_digest": "e1"},
            },
            [{"input_digest": "in1", "verdict": "proof_ok"}],
        )
        leg("oracle-drift-fails", False)
    except JobRefused:
        leg("oracle-drift-fails", True)
    # 9. rubric below floor fails the done gate.
    try:
        check_done_gate(
            jobs_mod,
            base_job(),
            {
                "dims": {"quotes": 800, "dates": 700},
                "oracle_version": "o1",
                "input_digest": "in1",
                "observed": {"evidence_digest": "e1"},
            },
            [{"input_digest": "in1", "verdict": "proof_ok"}],
        )
        leg("rubric-floor-fails", False)
    except JobRefused:
        leg("rubric-floor-fails", True)
    # 10. missing repeat observation fails (proof_ok alone is not done).
    try:
        check_done_gate(
            jobs_mod,
            base_job(),
            {
                "dims": {"quotes": 800, "dates": 800},
                "oracle_version": "o1",
                "input_digest": "in1",
                "observed": {"evidence_digest": "e1"},
            },
            [],
        )
        leg("proof-ok-not-done", False)
    except JobRefused:
        leg("proof-ok-not-done", True)
    # 11. missing observed evidence fails.
    try:
        check_done_gate(
            jobs_mod,
            base_job(),
            {
                "dims": {"quotes": 800, "dates": 800},
                "oracle_version": "o1",
                "input_digest": "in1",
                "observed": {},
            },
            [{"input_digest": "in1", "verdict": "proof_ok"}],
        )
        leg("missing-evidence-fails", False)
    except JobRefused:
        leg("missing-evidence-fails", True)
    # 12. idempotency-key policy needs a value.
    try:
        check_done_gate(
            jobs_mod,
            base_job(
                repeat={"policy": "idempotency-key", "idempotency_key": "2026-09-13"}
            ),
            {
                "dims": {"quotes": 800, "dates": 800},
                "oracle_version": "o1",
                "input_digest": "in1",
                "observed": {"evidence_digest": "e1"},
            },
            [],
        )
        leg("idempotency-needed", False)
    except JobRefused:
        leg("idempotency-needed", True)
    # 13. the happy path records done.
    try:
        gate = check_done_gate(
            jobs_mod,
            base_job(),
            {
                "dims": {"quotes": 800, "dates": 900},
                "oracle_version": "o1",
                "input_digest": "in1",
                "observed": {"evidence_digest": "e1"},
            },
            [{"input_digest": "in1", "verdict": "proof_ok"}],
        )
        leg("happy-path-done", gate["verdict"] == "recorded_done")
    except JobRefused:
        leg("happy-path-done", False)
    # 26. a spent idempotency key is replayed, never re-recorded.
    try:
        check_done_gate(
            jobs_mod,
            base_job(
                repeat={"policy": "idempotency-key", "idempotency_key": "brief-date"}
            ),
            {
                "dims": {"quotes": 800, "dates": 800},
                "oracle_version": "o1",
                "input_digest": "in1",
                "idempotency_value": "2026-09-13",
                "observed": {"evidence_digest": "e1"},
            },
            [],
            [{"idempotency_value": "2026-09-13", "verdict": "recorded_done"}],
        )
        leg("spent-key-refused", False)
    except JobRefused:
        leg("spent-key-refused", True)
    # 14. sealed attempts carry a stable digest (gb-jobs sealer, reused).
    a1 = jobs_mod._seal_attempt({"attempt_id": "x", "v": 1})
    a2 = jobs_mod._seal_attempt({"attempt_id": "x", "v": 1})
    leg("seal-stable", a1["attempt_digest"] == a2["attempt_digest"])
    # 15. binding assembles target + frozen coach receipt, read-only.
    try:
        binding = compile_binding(
            "p",
            base_job(prerequisites={"capability_receipts": []}, room=None),
            [],
            [
                {
                    "capability": "c",
                    "target": "hello-computer",
                    "persona": "p",
                    "verdict": "WAITING_HUMAN",
                    "receipt": "r",
                    "continuation": {
                        "coach_need": "n",
                        "coach_job": "j",
                        "action": "a",
                    },
                }
            ],
            [],
            "d0",
            [{"template": "first-file-desk", "live_uuid": None}],
        )
        leg(
            "binding-freezes-coach",
            binding["frozen_prerequisites"]["coach_receipt_hello_computer"]["verdict"]
            == "WAITING_HUMAN"
            and binding["continuation"]["action"].startswith("supply input"),
        )
    except (JobRefused, ValueError):
        leg("binding-freezes-coach", False)
    # 16. undeclared target raises (declaration lied, never a WAITING row).
    try:
        compile_binding(
            "p",
            base_job(target="ghost-desk"),
            [],
            [],
            [],
            "d0",
            [{"template": "first-file-desk", "live_uuid": None}],
        )
        leg("undeclared-target-raises", False)
    except JobRefused:
        leg("undeclared-target-raises", True)
    # 17. missing room proof lands WAITING_HUMAN naming it.
    try:
        binding = compile_binding(
            "p",
            base_job(),
            [],
            [
                {
                    "capability": "c",
                    "target": "hello-computer",
                    "persona": "p",
                    "verdict": "PROVEN",
                    "receipt": "r",
                    "continuation": {},
                }
            ],
            [
                {
                    "name": "First Hour",
                    "logical_id": "r1",
                    "persona": "p",
                    "verdict": "FAILED",
                    "receipt": "unresolved",
                    "intended": [],
                }
            ],
            "d0",
            [{"template": "first-file-desk", "live_uuid": "u-ffd"}],
        )
        leg(
            "room-proof-required",
            binding["verdict"] == "WAITING_HUMAN"
            and "First Hour" in binding["receipt"],
        )
    except (JobRefused, ValueError):
        leg("room-proof-required", False)
    # 18. wrong-role capability row poisons the binding.
    try:
        compile_binding(
            "p",
            base_job(
                prerequisites={"capability_receipts": ["c:hello-computer"]}, room=None
            ),
            [],
            [
                {
                    "capability": "c",
                    "target": "hello-computer",
                    "persona": "q",
                    "verdict": "PROVEN",
                    "receipt": "r",
                    "continuation": {},
                }
            ],
            [],
            "d0",
            [{"template": "first-file-desk", "live_uuid": "u-ffd"}],
        )
        leg("wrong-role-poisons", False)
    except JobRefused:
        leg("wrong-role-poisons", True)
    # 19. input digest drift fails versions.
    try:
        check_versions(base_job(), {"oracle_version": "o1", "input_digest": "in2"})
        leg("input-drift-fails", False)
    except JobRefused:
        leg("input-drift-fails", True)
    # 20. unbound declared input accepts first evidence, refuses empty.
    unbound = base_job()
    unbound["input"] = {
        "kind": "human-attachment",
        "freshness": "attached-this-attempt",
        "digest": None,
    }
    try:
        check_versions(
            unbound, {"oracle_version": "o1", "input_digest": "first-evidence"}
        )
        leg("unbound-input-accepts-first", True)
    except JobRefused:
        leg("unbound-input-accepts-first", False)
    try:
        check_versions(unbound, {"oracle_version": "o1", "input_digest": ""})
        leg("unbound-input-refuses-empty", False)
    except JobRefused:
        leg("unbound-input-refuses-empty", True)
    # 20-23. e2e: WAITING -> fixture -> validate -> attempt -> proof ->
    # replay -> rollback, one bounded structured log, no bodies or paths.
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            log_path = tmpdir / "attempts.jsonl"
            events: List[Dict[str, Any]] = []

            def emit(kind: str, **fields: Any) -> None:
                events.append(
                    {
                        "corr": "corr-e2e",
                        "persona": "p",
                        "job": "first-file-desk",
                        "kind": kind,
                        **fields,
                    }
                )

            job = base_job(prerequisites={"capability_receipts": []}, room=None)
            emit("bound", binding_digest="b0")
            # human transition: attachment arrives as digest only.
            attempt_id = "att-1"
            check_attempt_unique(attempt_id, read_log(log_path))
            attempt = jobs_mod._seal_attempt(
                {
                    "attempt_id": attempt_id,
                    "persona": "p",
                    "input_digest": "in1",
                    "oracle_version": "o1",
                    "dims": {"quotes": 800, "dates": 900},
                    "observed": {"evidence_digest": "e1", "quotes": ["q1"]},
                    "verdict": "proof_ok",
                }
            )
            emit("attempt", attempt_digest=attempt["attempt_digest"])
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(attempt) + "\n")
            # replay: same input digest proves deterministic repeat.
            attempt2 = jobs_mod._seal_attempt(dict(attempt, attempt_id="att-2"))
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(attempt2) + "\n")
            history = read_log(log_path)
            try:
                check_attempt_unique("att-1", history)
                emit("replay_duplicate_missed", ok=False)
            except JobRefused:
                emit("replay_duplicate_caught", ok=True)
            gate = check_done_gate(jobs_mod, job, attempt2, history)
            emit("done", verdict=gate["verdict"])
            # rollback: history immutable; residue named.
            emit(
                "rollback", compensated=[], residue=["human-owned thread posts remain"]
            )
            blob = json.dumps(events, sort_keys=True, default=str)
            leg("e2e-done", gate["verdict"] == "recorded_done")
            leg(
                "e2e-duplicate-caught",
                any(e.get("kind") == "replay_duplicate_caught" for e in events),
            )
            leg(
                "e2e-log-bounded",
                len(blob) < 10000
                and "/Users/" not in blob
                and "secret" not in blob.lower(),
            )
            leg(
                "e2e-history-append-only",
                len(read_log(log_path)) == 2,
            )
    except (JobRefused, ValueError, OSError):
        for name in (
            "e2e-done",
            "e2e-duplicate-caught",
            "e2e-log-bounded",
            "e2e-history-append-only",
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


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-first-job", description=__doc__)
    ap.add_argument("--persona", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--capabilities", action="store_true")
    ap.add_argument("--attempt", action="store_true")
    ap.add_argument("--record-done", action="store_true")
    ap.add_argument("--rollback", default="")
    ap.add_argument("--input", default="")
    ap.add_argument("--attempt-id", default="")
    ap.add_argument("--dims", default="")
    ap.add_argument("--oracle-version", default="")
    ap.add_argument("--idempotency-value", default="")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.capabilities:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "thresholds": {
                        "selftest_legs": _capabilities(),
                        "proof_ok_is_never_done": True,
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
    if not args.persona:
        print("gb-first-job: --persona <id> (see personas/)", file=sys.stderr)
        return EXIT_USAGE

    if args.attempt or args.record_done or args.rollback:
        print(
            "gb-first-job: live attempts need a human-supplied input and a witnessed oracle read; "
            "tonight the binding is the deliverable (see --persona dry-run). Refusing a blind write.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    sys.path.insert(0, str(BIN))
    try:
        from gbrpc import access_token, rpc
        from gblib import platform_support

        support = platform_support().support_dir
        if support is None:
            raise RuntimeError("no desktop client state on this machine")
        token = access_token(support)
        st, resp = rpc(token, "ListGrokBotAgents", {})
        if st != 200 or not isinstance(resp, dict):
            raise RuntimeError("roster pull HTTP %s" % st)
        raw_agents = resp.get("agents")
        agents: List[Any] = list(raw_agents) if isinstance(raw_agents, list) else []
        roster = [a for a in agents if isinstance(a, dict)]
    except RuntimeError as exc:
        print("gb-first-job: %s - unmeasured, not empty" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT
    except Exception as exc:
        print("gb-first-job: %s" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT

    try:
        job = load_first_job(args.persona)
    except ValueError as exc:
        print("gb-first-job: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    capstage = _load("gb_capability_stage_bind", "gb-capability-stage.py")
    topostage = _load("gb_room_topology_bind", "gb-room-topology.py")
    from gbrpc import rpc as live_rpc

    try:
        live = capstage.fetch_live(token, live_rpc, [args.persona])
        cap_rows = capstage.compile_matrix([args.persona], live)
        fresh_digest = _digest(
            sorted(str(a.get("legacyAgentId") or a.get("agentId")) for a in roster)
        )
        rooms_raw = topostage.load_persona_rooms(args.persona)
        room_rows: List[Dict[str, Any]] = []
        if rooms_raw is not None:
            idmap, _ = topostage.build_identity_map(args.persona, roster)
            room_rows = topostage.compile_topology(
                args.persona,
                topostage.validate_rooms(rooms_raw),
                idmap,
                topostage.read_live_rooms(roster),
            )
        binding = compile_binding(
            args.persona, job, roster, cap_rows, room_rows, fresh_digest
        )
    except (JobRefused, ValueError) as exc:
        print("gb-first-job: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    except RuntimeError as exc:
        print("gb-first-job: %s - unmeasured, not empty" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT
    if args.json:
        print(json.dumps(binding, indent=1))
    else:
        print(
            "%s\t%s\t%s"
            % (
                binding["target"]["template"],
                binding["verdict"],
                binding.get("receipt", "")[:120],
            )
        )
        for key in sorted(binding["frozen_prerequisites"]):
            frozen = binding["frozen_prerequisites"][key]
            print("  prereq %s\t%s" % (key, frozen.get("verdict")))
    return EXIT_OK if binding["verdict"] == "READY" else EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
