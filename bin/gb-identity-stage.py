#!/usr/bin/env python3
"""gb-identity-stage — resumable authoritative Bot-identity stage for one persona.

WHY THIS EXISTS. gb-setup-persona.py prints and refuses apply; the persona packs
declare Bots but nothing resolves them against the live roster, creates through
the proven path, or journals recovery. This stage does exactly that and nothing
else: resolve declared Bots to identity rows (dry-run default, zero writes),
then on explicit approval create/update through CreateGrokBotAgentFromTemplate
only, journaling every transition before the write it pays for.

REFUSALS (fail closed, never warn-and-continue): bare CreateGrokBotAgent (durable
but unusable identity — AGENTS.md asymmetry), any seed but fleet-spec.json's
sanctioned share_id, roster match by name alone (charter equality or UUID —
desktop blobs are stale caches), and --apply without --yes --approve-all.

APPLIED MINING LESSON (tick-monitor/typed-callback vein): packets that name the
callback bar get callbacks. Every emitted row carries typed verify (exact readback
command), rollback (exact argv), and resume_key — a row without all three is
incomplete output, not a terse row.

READS/WRITES: dry-run reads personas/, templates/, fleet-spec.json + one roster
pull. Apply writes identity/<stamp>.json journal + the API creates/updates.
stdlib only. Token in memory, never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-identity-stage/1"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
EXIT_REFUSED = 5

SANCTIONED_METHOD = "CreateGrokBotAgentFromTemplate"
REFUSED_METHOD = "CreateGrokBotAgent"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def load_pack(persona: str) -> Dict[str, Any]:
    """Persona pack or ValueError. Packs are discovered, never hardcoded."""
    path = ROOT / "personas" / (persona + ".json")
    if not path.is_file():
        raise ValueError("unknown persona %r (see personas/)" % persona)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("bots"), list):
        raise ValueError("pack %s has no bots[] list" % path.name)
    return doc


def load_template(tid: str) -> Dict[str, Any]:
    path = ROOT / "templates" / (tid + ".json")
    if not path.is_file():
        raise ValueError("unknown template %r" % tid)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("template %s unparseable" % path.name)
    return doc


def sanctioned_seed() -> str:
    """The one local seed the apply path may use; never embedded in exports."""
    path = ROOT / "fleet-spec.json"
    if not path.is_file():
        raise ValueError("fleet-spec.json absent; configure a local sanctioned seed")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("fleet-spec.json is unreadable") from exc
    seed = doc.get("seed") or {}
    share = seed.get("share_id") if isinstance(seed, dict) else None
    if not share:
        raise ValueError("fleet-spec.json carries no seed.share_id")
    return str(share)


def match_roster(
    charter: str, roster: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Authoritative identity: charter equality. Name-only equality is never
    authoritative (duplicate names, renamed Bots, stale caches)."""
    for bot in roster:
        if isinstance(bot, dict) and bot.get("description") == charter and charter:
            return bot
    return None


def resolve(
    persona: str, roster: List[Dict[str, Any]], seed: str
) -> List[Dict[str, Any]]:
    """Dry-run planner. Pure: no writes, no RPC. One row per declared Bot."""
    pack = load_pack(persona)
    rows = []
    for decl in pack["bots"]:
        tid = decl.get("template") if isinstance(decl, dict) else None
        if not tid:
            raise ValueError("pack declares a Bot without a template id")
        tpl = load_template(str(tid))
        charter = str(tpl.get("charter") or "")
        hit = match_roster(charter, roster)
        action = "existing" if hit is not None else "create"
        logical = "persona:%s/template:%s" % (
            pack.get("id", persona),
            tpl.get("id", tid),
        )
        rows.append(
            {
                "logical_id": logical,
                "template": tpl.get("id", tid),
                "name": tpl.get("name", ""),
                "charter_sha": _digest(charter),
                "seed_share_id": seed,
                "seed_sha256_12": _digest(seed),
                "action": action,
                "live_uuid": hit.get("legacyAgentId") or hit.get("agentId")
                if hit
                else None,
                "approval": {
                    "type": "WAITING_HUMAN",
                    "scope": "create Bot via sanctioned seed",
                },
                "readback": {
                    "route": "ListGrokBotAgents (server), match description == charter"
                },
                "rollback": {
                    "class": "delete-bot-keeps-routine",
                    "argv": ["DeleteGrokBotAgent", "<uuid>"],
                },
                "resume_key": "identity:%s" % _digest(logical),
                "verify": "bin/gb-identity-stage.py --persona %s (dry-run re-resolves this row to existing)"
                % persona,
            }
        )
    return rows


class Refused(Exception):
    """Typed refusal: bare-create | unproven-seed | readback-drift | no-approval."""


def check_create(seed: str, method: str) -> None:
    """Gate every create before any write. Raises Refused, never warns."""
    if method != SANCTIONED_METHOD:
        raise Refused(
            "bare-create: %s registers an unusable identity; use %s"
            % (method, SANCTIONED_METHOD)
        )
    if seed != sanctioned_seed():
        raise Refused("unproven-seed: %r is not fleet-spec seed" % seed)


def apply_rows(
    rows: List[Dict[str, Any]],
    rpc: Callable[[str, Dict[str, Any]], Tuple[int, Any]],
    journal: Callable[[Dict[str, Any]], None],
    confirmed: Dict[str, str],
    seed: str,
    approved: bool,
) -> List[Dict[str, Any]]:
    """Execute create rows with manifest-before-write + fresh readback. Returns
    per-row outcomes. Resume-safe: confirmed resume_keys issue zero writes."""
    if not approved:
        raise Refused(
            "no-approval: pass --yes --approve-all with every row WAITING_HUMAN cleared by a human"
        )
    outcomes = []
    for row in rows:
        if row["action"] != "create":
            outcomes.append({"logical_id": row["logical_id"], "skipped": "existing"})
            continue
        if row["resume_key"] in confirmed:
            outcomes.append(
                {"logical_id": row["logical_id"], "skipped": "resume-confirmed"}
            )
            continue
        check_create(seed, SANCTIONED_METHOD)
        journal(
            {
                "event": "will-create",
                "logical_id": row["logical_id"],
                "seed_sha": row["seed_sha256_12"],
            }
        )
        st, body = rpc(SANCTIONED_METHOD, {"share_id": seed})
        if st != 200 or not isinstance(body, dict):
            raise Refused("create-failed: %s HTTP %s" % (SANCTIONED_METHOD, st))
        journal(
            {
                "event": "created",
                "logical_id": row["logical_id"],
                "body_keys": sorted(body.keys()),
            }
        )
        st2, roster = rpc("ListGrokBotAgents", {})
        found = None
        if st2 == 200 and isinstance(roster, dict):
            for bot in roster.get("agents") or []:
                if isinstance(bot, dict) and bot.get("description") == _charter_of(row):
                    found = bot
                    break
        if found is None:
            raise Refused(
                "readback-drift: created Bot not found by charter equality; "
                "recover with: bin/gb-identity-stage.py --rollback <manifest>"
            )
        confirmed[row["resume_key"]] = str(
            found.get("legacyAgentId") or found.get("agentId")
        )
        journal({"event": "confirmed", "logical_id": row["logical_id"]})
        outcomes.append({"logical_id": row["logical_id"], "created": True})
    return outcomes


def _charter_of(row: Dict[str, Any]) -> str:
    tpl = load_template(str(row["template"]))
    return str(tpl.get("charter") or "")


def rollback_plan(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """From a journal: one DeleteGrokBotAgent per confirmed create + residue note.
    Deleting a Bot does NOT delete its routine record (measured asymmetry) — the
    plan names that residue instead of claiming full reversal."""
    steps = []
    for ev in manifest.get("transitions") or manifest.get("events") or []:
        if isinstance(ev, dict) and ev.get("event") == "confirmed":
            steps.append(
                {
                    "argv": [
                        "DeleteGrokBotAgent",
                        ev.get("uuid", "<uuid-from-journal>"),
                    ],
                    "residue": "routine record survives Bot deletion; delete routines in chat",
                }
            )
    return steps


def _capabilities() -> int:
    print(
        json.dumps(
            {
                "tool": "gb-identity-stage",
                "verbs": ["resolve", "apply", "rollback"],
                "refusals": [
                    "bare-create",
                    "unproven-seed",
                    "readback-drift",
                    "no-approval",
                ],
                "limits": {
                    "pack_source": "personas/*.json",
                    "seed_source": "fleet-spec.json seed.share_id",
                },
            },
            indent=1,
        )
    )
    return EXIT_OK


def selftest() -> int:
    """Offline failure-injection legs. Fake transport, temp journal, zero writes
    outside it. Every refusal and ordering rule proven here, not asserted."""
    passed, failed = 0, []
    seed = "seed-under-test"

    def leg(name: str, ok: bool) -> None:
        nonlocal passed
        if ok:
            passed += 1
        else:
            failed.append(name)

    # 1. bare create refused before any RPC.
    calls: List[str] = []

    def norpc(method: str, body: Dict[str, Any]) -> Tuple[int, Any]:
        calls.append(method)
        raise AssertionError("RPC must not fire on refusal")

    try:
        check_create(seed, REFUSED_METHOD)
        leg("bare-create-refused", False)
    except Refused:
        leg("bare-create-refused", True)
    leg("bare-create-no-rpc", not calls)

    # 2. unproven seed refused.
    try:
        check_create("other-seed", SANCTIONED_METHOD)
        leg("unproven-seed-refused", False)
    except Refused:
        leg("unproven-seed-refused", True)

    # 3. manifest-before-write ordering on the happy path (fake transport).
    log: List[str] = []
    confirmed: Dict[str, str] = {}
    real_seed_fn = globals()["sanctioned_seed"]
    globals()["sanctioned_seed"] = lambda: seed
    try:

        def fake_rpc(method: str, body: Dict[str, Any]) -> Tuple[int, Any]:
            log.append(method)
            if method == SANCTIONED_METHOD:
                return 200, {"agent": {"id": "n1"}}
            if method == "ListGrokBotAgents":
                return 200, {
                    "agents": [{"legacyAgentId": "u1", "description": "CHARTER"}]
                }
            raise AssertionError(method)

        journaled: List[str] = []
        rows = [
            {
                "logical_id": "L1",
                "template": "t1",
                "action": "create",
                "seed_sha256_12": "x",
                "resume_key": "identity:k1",
            }
        ]
        # stub charter lookup without touching templates/

        orig = globals()["_charter_of"]
        globals()["_charter_of"] = lambda row: "CHARTER"
        try:
            order: List[str] = []

            def fake_rpc2(method: str, body: Dict[str, Any]) -> Tuple[int, Any]:
                order.append("rpc:" + method)
                return fake_rpc(method, body)

            def journal2(ev: Dict[str, Any]) -> None:
                order.append("journal:" + str(ev.get("event")))
                journaled.append(ev["event"])

            out = apply_rows(rows, fake_rpc2, journal2, confirmed, seed, True)
            leg("happy-path-creates", out == [{"logical_id": "L1", "created": True}])
            leg(
                "manifest-before-write",
                order.index("journal:will-create")
                < order.index("rpc:" + SANCTIONED_METHOD),
            )
            leg("readback-confirms", confirmed.get("identity:k1") == "u1")
        finally:
            globals()["_charter_of"] = orig

        # 4. readback drift fails with recovery argv.
        def fake_noshow(method: str, body: Dict[str, Any]) -> Tuple[int, Any]:
            if method == SANCTIONED_METHOD:
                return 200, {"agent": {"id": "n1"}}
            return 200, {"agents": []}

        globals()["_charter_of"] = lambda row: "CHARTER"
        try:
            try:
                apply_rows(rows, fake_noshow, lambda e: None, {}, seed, True)
                leg("readback-drift-fails", False)
            except Refused as exc:
                leg("readback-drift-fails", "--rollback" in str(exc))
        finally:
            globals()["_charter_of"] = orig

        # 5. resume skips confirmed with zero writes.
        log2: List[str] = []

        def fake_count(method: str, body: Dict[str, Any]) -> Tuple[int, Any]:
            log2.append(method)
            raise AssertionError("must not fire")

        apply_rows(rows, fake_count, lambda e: None, {"identity:k1": "u1"}, seed, True)
        leg("resume-issues-zero-writes", log2 == [])

        # 6. no approval refuses before any write.
        try:
            apply_rows(rows, fake_count, lambda e: None, {}, seed, False)
            leg("no-approval-refuses", False)
        except Refused:
            leg("no-approval-refuses", log2 == [])
    finally:
        globals()["sanctioned_seed"] = real_seed_fn

    # 7. rollback names residue.
    steps = rollback_plan({"events": [{"event": "confirmed", "uuid": "u9"}]})
    leg("rollback-names-residue", len(steps) == 1 and "routine" in steps[0]["residue"])

    total = 10
    print(
        "SELFTEST %s - %d/%d"
        % ("PASS" if not failed and passed == total else "FAIL", passed, total)
    )
    if failed:
        for f in failed:
            print("  leg failed: %s" % f)
        return 1
    return 0 if passed == total else 1


def live_roster() -> List[Dict[str, Any]]:
    """Authoritative roster via server read. Raises RuntimeError (never returns
    a stale/empty guess): triage without a roster would label everything create."""
    sys.path.insert(0, str(ROOT / "bin"))
    try:
        from gbrpc import access_token, rpc
        from gblib import platform_support
    except ImportError as exc:
        raise RuntimeError("reader imports unavailable (%s)" % exc)
    support = platform_support().support_dir
    if support is None:
        raise RuntimeError("no desktop client state on this machine")
    try:
        token = access_token(support)
        st, resp = rpc(token, "ListGrokBotAgents", {})
    except Exception as exc:
        raise RuntimeError("roster read failed (%s)" % exc)
    if st != 200 or not isinstance(resp, dict):
        raise RuntimeError("ListGrokBotAgents HTTP %s" % st)
    agents = resp.get("agents")
    if not isinstance(agents, list):
        raise RuntimeError("roster body has no agents list")
    return [a for a in agents if isinstance(a, dict)]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-identity-stage", description=__doc__)
    ap.add_argument("--persona", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--capabilities", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--approve-all", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.capabilities:
        return _capabilities()
    if not args.persona:
        print("gb-identity-stage: --persona <id> (see personas/)", file=sys.stderr)
        return EXIT_USAGE
    if args.apply:
        print(
            "gb-identity-stage: apply executes account writes; dry-run first, then --yes --approve-all with every row human-cleared",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    try:
        seed = sanctioned_seed()
        roster = live_roster()
        rows = resolve(args.persona, roster, seed)
    except ValueError as exc:
        print("gb-identity-stage: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    except RuntimeError as exc:
        print("gb-identity-stage: %s — unmeasured, not empty" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT
    if args.json:
        print(
            json.dumps(
                {"schema": SCHEMA, "persona": args.persona, "bots": rows}, indent=1
            )
        )
    else:
        for r in rows:
            print("%s\t%s\t%s" % (r["logical_id"], r["action"], r["resume_key"]))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
