#!/usr/bin/env python3
"""gb-skills-ledger — assignment census, local trigger proxy, Thompson bandit.

Two skill surfaces (measured 2026-09-12):
  recipe  ListGrokBotAgentSkills  (source=recipe on this account)
  disk    /home/box/agent-data/workflows/<name>/SKILL.md  (private/workflow)

Vendor invocationCount / skillId-on-runs is ABSENT (findings 2026-09-11).
Local pulls are outcomes we record: gb skills outcome, and roundtrip/disk receipts.
Approved skill mutations use this module as a library: it seals full pre-state separately, writes
only digests and identities to the durable manifest/log, and rejects non-read ledger RPCs.

Bandit shape mirrors jsm bandit stats (Beta prior, posterior_mean, pull_count):
  alpha = 1 + successes, beta = 1 + failures, mean = alpha / (alpha+beta)
  suggest = argmax of one Thompson sample (random.betavariate)

    gb skills census [--bot NAME] [--json]
    gb skills outcome --bot NAME --skill ID --result success|failure
    gb skills bandit [--json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import random
import re
import sys
import types
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

BIN = pathlib.Path(__file__).resolve().parent
ROOT = BIN.parent
LEDGER = ROOT / "skills"
OUTCOMES = LEDGER / "outcomes.jsonl"
DISKLOG = LEDGER / "disk.jsonl"
MUTATIONS = LEDGER / "mutations"
EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2
EXIT_ENVIRONMENT, EXIT_UPSTREAM = 3, 4
GROKBOT = "aiserver.v1.GrokBotService"
sys.path.insert(0, str(BIN))
from gbtypes import atomic_write_json  # noqa: E402

VENDOR_TRIGGERS = (
    "UNMEASURED — no invocationCount / skillId on run records "
    "(findings/2026-09-11.md)"
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _append(path: pathlib.Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


MUTATION_LOG_FIELDS = (
    "schema",
    "at",
    "manifest_id",
    "command_mode",
    "candidate_rpc_class",
    "service",
    "write_attempt_count",
    "target_identity",
    "unique_identity",
    "manifest_path",
    "pre_state_path",
    "pre_state_digest",
    "expected_post_state_digest",
    "request_id",
    "request_method",
    "request_digest",
    "write_http",
    "readback_id",
    "readback_http",
    "readback_digest",
    "compensation_id",
    "compensation_method",
    "compensation_digest",
    "compensation_evidence",
    "compensation_http",
    "compensation_readback_id",
    "compensation_readback_http",
    "compensation_readback_digest",
    "partial_residue",
    "residue",
    "verdict",
    "exit",
    "recovery_argv",
)


def digest_json(value: Any) -> str:
    """Stable digest for receipts. The value itself never enters the mutation log."""
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _operation_path(directory: pathlib.Path, operation_id: str) -> pathlib.Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{7,127}", operation_id):
        raise ValueError("operation id must be an 8-128 character safe filename")
    return directory / f"{operation_id}.json"


def begin_mutation(
    operation_id: str,
    metadata: Dict[str, Any],
    pre_state: Any,
    directory: pathlib.Path = MUTATIONS,
) -> pathlib.Path:
    """Durably seal authoritative pre-state and a body-free manifest before a write.

    Request and compensation bodies deliberately have no parameter here. Callers may record only
    their digests, so a receipt cannot leak a skill body, bearer token, or request secret.
    """
    required = {
        "command_mode",
        "candidate_rpc_class",
        "service",
        "target_identity",
        "unique_identity",
        "pre_state_digest",
        "expected_post_state_digest",
        "request_id",
        "request_method",
        "request_digest",
        "readback_id",
        "compensation_id",
        "compensation_method",
        "compensation_digest",
        "compensation_evidence",
        "compensation_readback_id",
        "recovery_argv",
    }
    missing = sorted(k for k in required if metadata.get(k) in (None, "", []))
    extra_metadata = sorted(set(metadata) - required)
    if missing:
        raise ValueError(f"incomplete mutation manifest: {', '.join(missing)}")
    if extra_metadata:
        raise ValueError(
            f"unsafe mutation manifest fields: {', '.join(extra_metadata)}"
        )
    if metadata["command_mode"] != "approved-write":
        raise ValueError("mutation manifest requires explicit approved-write mode")
    if metadata["candidate_rpc_class"] not in ("WRITE", "IRREVERSIBLE"):
        raise ValueError("mutation manifest requires a declared write RPC class")
    if metadata["pre_state_digest"] != digest_json(pre_state):
        raise ValueError("pre-state digest does not match authoritative pre-state")
    recovery_argv = metadata["recovery_argv"]
    if not isinstance(recovery_argv, list) or not all(
        isinstance(part, str) and part for part in recovery_argv
    ):
        raise ValueError("recovery_argv must be an exact argv list")
    unsafe_argv = ("--payload", "--token", "authorization", "bearer ")
    if any(marker in " ".join(recovery_argv).lower() for marker in unsafe_argv):
        raise ValueError("recovery_argv must not embed request bodies or credentials")

    directory = pathlib.Path(directory)
    manifest_path = _operation_path(directory, operation_id)
    pre_state_path = directory / f"{operation_id}.pre-state.json"
    if manifest_path.exists() or pre_state_path.exists():
        raise FileExistsError(f"mutation manifest already exists: {operation_id}")

    # Pre-state lands first. If the manifest write fails, the caller has no manifest path and MUST
    # refuse the RPC; if it succeeds, recovery never points at an absent state artifact.
    atomic_write_json(pre_state_path, pre_state)
    doc = {
        "schema": "gb-skills-mutation/1",
        "at": _now(),
        "manifest_id": operation_id,
        **metadata,
        "manifest_path": str(manifest_path),
        "pre_state_path": str(pre_state_path),
        "write_attempt_count": 0,
        "write_http": None,
        "readback_http": None,
        "readback_digest": None,
        "compensation_http": None,
        "compensation_readback_http": None,
        "compensation_readback_digest": None,
        "partial_residue": 0,
        "residue": [],
        "verdict": "PREPARED",
        "exit": None,
    }
    atomic_write_json(manifest_path, doc)
    return manifest_path


def finish_mutation(
    manifest_path: pathlib.Path, updates: Dict[str, Any]
) -> Dict[str, Any]:
    """Atomically finish a manifest and fsync its body-free audit summary."""
    allowed = {
        "write_attempt_count",
        "write_http",
        "readback_http",
        "readback_digest",
        "compensation_http",
        "compensation_readback_http",
        "compensation_readback_digest",
        "partial_residue",
        "residue",
        "verdict",
        "exit",
    }
    extra = sorted(set(updates) - allowed)
    if extra:
        raise ValueError(f"unsafe mutation manifest fields: {', '.join(extra)}")
    path = pathlib.Path(manifest_path)
    doc = json.loads(path.read_text())
    if not isinstance(doc, dict) or doc.get("schema") != "gb-skills-mutation/1":
        raise ValueError("not a skill mutation manifest")
    if doc.get("exit") is not None:
        raise ValueError("mutation manifest is already terminal")
    prospective = {**doc, **updates}
    residue = prospective.get("residue")
    residue_count = prospective.get("partial_residue")
    if not isinstance(residue, list) or residue_count != len(residue):
        raise ValueError("partial_residue must equal the named residue count")
    if prospective.get("verdict") == "PARTIAL_RESIDUE" and residue_count < 1:
        raise ValueError("PARTIAL_RESIDUE requires nonzero named residue")
    if prospective.get("exit") == EXIT_OK and residue_count:
        raise ValueError("successful mutation receipt cannot carry residue")
    doc.update(updates)
    atomic_write_json(path, doc)
    if doc.get("exit") is not None:
        _append(
            path.parent / "mutations.jsonl",
            {key: doc.get(key) for key in MUTATION_LOG_FIELDS},
        )
    return doc


def _read_jsonl(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _pull() -> types.ModuleType:
    sys.path.insert(0, str(BIN))
    import gbrpc as mod

    return mod


def _token(mod: types.ModuleType) -> str:
    from gblib import support_dir

    try:
        return str(mod.access_token(support_dir()))
    except TypeError:
        return str(mod.access_token())


def rpc(
    mod: types.ModuleType,
    token: str,
    method: str,
    body: dict[str, Any],
) -> Tuple[int, Any]:
    if method not in ("ListGrokBotAgents", "ListGrokBotAgentSkills"):
        raise ValueError(f"gb-skills-ledger refuses non-read RPC {method}")
    req = urllib.request.Request(
        f"https://{mod.HOST}/{GROKBOT}/{method}",
        data=json.dumps(body).encode(),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "connect-protocol-version": "1",
            "user-agent": "gb-skills-ledger/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return int(resp.status), json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        try:
            return int(exc.code), json.loads(raw or "{}")
        except json.JSONDecodeError:
            return int(exc.code), raw
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def latest_disk() -> Dict[str, Dict[str, Any]]:
    """bot -> last disk-prove row."""
    out: Dict[str, Dict[str, Any]] = {}
    for rec in _read_jsonl(DISKLOG):
        bot = str(rec.get("bot") or "")
        if bot:
            out[bot.lower()] = rec
    return out


def arms_from_outcomes() -> Dict[str, Dict[str, Any]]:
    """JSM-shaped arms: skill_id -> alpha/beta/pulls. Key is bot/skill."""
    arms: Dict[str, Dict[str, Any]] = {}
    for rec in _read_jsonl(OUTCOMES):
        bot = str(rec.get("bot") or "")
        skill = str(rec.get("skill") or "")
        if not skill:
            continue
        key = f"{bot}/{skill}" if bot else skill
        arm = arms.setdefault(
            key,
            {
                "skill_id": skill,
                "bot": bot,
                "alpha": 1.0,
                "beta": 1.0,
                "pull_count": 0,
                "reward_sum": 0.0,
            },
        )
        arm["pull_count"] += 1
        if rec.get("result") == "success":
            arm["alpha"] += 1.0
            arm["reward_sum"] += 1.0
        else:
            arm["beta"] += 1.0
        arm["posterior_mean"] = arm["alpha"] / (arm["alpha"] + arm["beta"])
        arm["updated_at"] = rec.get("at")
    for arm in arms.values():
        arm.setdefault("posterior_mean", arm["alpha"] / (arm["alpha"] + arm["beta"]))
    return arms


def cmd_outcome(bot: str, skill: str, result: str, as_json: bool) -> int:
    if not skill or result not in ("success", "failure"):
        print(
            "gb-skills: outcome needs --skill ID --result success|failure",
            file=sys.stderr,
        )
        return EXIT_USAGE
    row = {
        "schema": "gb-skills-outcome/1",
        "at": _now(),
        "bot": bot,
        "skill": skill,
        "result": result,
    }
    _append(OUTCOMES, row)
    if as_json:
        print(json.dumps(row, indent=1))
    else:
        print(f"outcome {bot or '-'} {skill} {result}")
    return EXIT_OK


def cmd_bandit(as_json: bool) -> int:
    arms = arms_from_outcomes()
    ranked = []
    for key, arm in arms.items():
        sample = random.betavariate(arm["alpha"], arm["beta"])
        ranked.append({**arm, "key": key, "thompson": sample})
    ranked.sort(key=lambda a: a["thompson"], reverse=True)
    doc = {
        "schema": "gb-skills-bandit/1",
        "source": "jsm-shaped Beta(1+s,1+f) Thompson; local outcomes.jsonl only",
        "vendor_triggers": VENDOR_TRIGGERS,
        "total_arms": len(ranked),
        "total_feedback_events": sum(int(a["pull_count"]) for a in ranked),
        "suggest": ranked[0]["key"] if ranked else None,
        "arms": ranked,
    }
    if as_json:
        print(json.dumps(doc, indent=1))
        return EXIT_OK
    print(
        f"arms {doc['total_arms']} events {doc['total_feedback_events']} "
        f"suggest {doc['suggest'] or '-'}"
    )
    for a in ranked[:12]:
        print(
            f"  {a['posterior_mean']:.3f} n={a['pull_count']} "
            f"{a['key']}  α={a['alpha']:.0f} β={a['beta']:.0f}"
        )
    if not ranked:
        print("  (no outcomes yet — gb skills outcome --skill ID --result success)")
    return EXIT_OK


def cmd_census(mod: types.ModuleType, token: str, bot: str, as_json: bool) -> int:
    st, roster = rpc(mod, token, "ListGrokBotAgents", {})
    if st != 200 or not isinstance(roster, dict):
        print(f"gb-skills: roster HTTP {st}", file=sys.stderr)
        return EXIT_UPSTREAM
    want = bot.strip().lower()
    disk = latest_disk()
    arms = arms_from_outcomes()
    rows = []
    for agent in roster.get("agents") or []:
        if not isinstance(agent, dict):
            continue
        name = str(agent.get("name") or "")
        if want and name.lower() != want:
            continue
        uuid = str(agent.get("legacyAgentId") or agent.get("agentId") or "")
        if not uuid:
            continue
        sst, skills = rpc(mod, token, "ListGrokBotAgentSkills", {"agent_id": uuid})
        got = (
            skills.get("skills") if sst == 200 and isinstance(skills, dict) else None
        ) or []
        recipe = [
            {
                "name": s.get("name"),
                "source": s.get("source"),
                "body_chars": len(s.get("body") or ""),
            }
            for s in got
            if isinstance(s, dict)
        ]
        drow = disk.get(name.lower())
        local_pulls = sum(
            int(a["pull_count"])
            for a in arms.values()
            if str(a.get("bot") or "").lower() == name.lower()
        )
        rows.append(
            {
                "name": name,
                "uuid": uuid,
                "recipe_count": len(recipe),
                "recipe": recipe,
                "disk": drow,
                "local_pulls": local_pulls,
                "vendor_triggers": None,
            }
        )
    doc = {
        "schema": "gb-skills-census/1",
        "vendor_triggers": VENDOR_TRIGGERS,
        "surfaces": ["recipe=ListGrokBotAgentSkills", "disk=workflows/<id>/SKILL.md"],
        "bots": rows,
        "bot_count": len(rows),
        "with_recipe": sum(1 for r in rows if r["recipe_count"]),
        "with_disk": sum(1 for r in rows if r.get("disk")),
    }
    if as_json:
        print(json.dumps(doc, indent=1))
        return EXIT_OK
    print(
        f"census bots={doc['bot_count']} recipe={doc['with_recipe']} "
        f"disk_proven={doc['with_disk']}"
    )
    print(f"  vendor_triggers  {VENDOR_TRIGGERS}")
    for r in rows:
        rec = ",".join(str(s.get("name") or "") for s in r["recipe"]) or "-"
        dsk = (r["disk"] or {}).get("path") or "-"
        print(
            f"  {r['name']}\trecipe={r['recipe_count']}\t{rec}\t"
            f"disk={dsk}\tpulls={r['local_pulls']}"
        )
    return EXIT_OK


def record_disk(bot: str, path: str, ok: bool) -> None:
    _append(
        DISKLOG,
        {
            "schema": "gb-skills-disk/1",
            "at": _now(),
            "bot": bot,
            "path": path,
            "ok": ok,
        },
    )


def record_roundtrip(bot: str, skill: str, ok: bool) -> None:
    cmd_outcome(bot, skill, "success" if ok else "failure", as_json=False)


def selftest() -> int:
    fails = 0

    def check(name: str, cond: bool) -> None:
        nonlocal fails
        if not cond:
            print(f"FAIL {name}")
            fails += 1
            return
        print(f"ok   {name}")

    check("beta-one-success-mean-2-3", abs((1 + 1) / (1 + 1 + 1 + 0) - 2 / 3) < 1e-9)
    sample = random.betavariate(2.0, 1.0)
    check("thompson-sample-unit-interval", 0.0 <= sample <= 1.0)
    check("outcomes-path-under-skills", OUTCOMES.parent.name == "skills")
    required_receipt_fields = {
        "command_mode",
        "candidate_rpc_class",
        "write_attempt_count",
        "target_identity",
        "manifest_id",
        "pre_state_digest",
        "request_id",
        "readback_id",
        "compensation_id",
        "partial_residue",
        "residue",
        "verdict",
        "exit",
        "recovery_argv",
    }
    check(
        "mutation-log-fields-complete",
        required_receipt_fields <= set(MUTATION_LOG_FIELDS),
    )
    check(
        "digest-is-canonical",
        digest_json({"b": 2, "a": 1}) == digest_json({"a": 1, "b": 2}),
    )
    write_refused = False
    try:
        rpc(types.SimpleNamespace(HOST="fake.invalid"), "", "DeleteSkill", {})
    except ValueError:
        write_refused = True
    check("ledger-write-rpc-refused-before-network", write_refused)
    return EXIT_OK if fails == 0 else EXIT_FINDINGS


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-skills-ledger", description=__doc__)
    ap.add_argument(
        "action",
        nargs="?",
        default="census",
        choices=("census", "outcome", "bandit"),
    )
    ap.add_argument("--bot", default="")
    ap.add_argument("--skill", default="")
    ap.add_argument("--result", default="", help="success | failure")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.action == "outcome":
        return cmd_outcome(args.bot, args.skill, args.result, args.json)
    if args.action == "bandit":
        return cmd_bandit(args.json)
    try:
        mod = _pull()
        token = _token(mod)
    except Exception as exc:
        print(f"gb-skills: no client session ({type(exc).__name__})", file=sys.stderr)
        return EXIT_ENVIRONMENT
    return cmd_census(mod, token, args.bot, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
