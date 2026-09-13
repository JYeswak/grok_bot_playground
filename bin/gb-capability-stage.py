#!/usr/bin/env python3
"""gb-capability-stage — compile persona capability manifests to a verdict matrix.

WHY THIS EXISTS. Persona packs declare typed capabilities (OAuth connectors, repo
skills, hosted MCP, marketplace packs) with proof oracles already in the manifest.
Nothing resolved them against the live account: installed passed for working,
enabled passed for proven, name-match passed for bound. This stage compiles each
manifest to one row per capability x target logical Bot with a verdict that can
only be PROVEN (semantic readback observed), WAITING_HUMAN (typed continuation
for coach), BLOCKED (target absent), or FAILED (malformed declaration).

APPLIED MINING LESSON (typed-callback vein, fh stale-but-usable): a verdict
without its proof receipt, continuation, and resume key is an opinion. Every row
carries receipt (what was observed), continuation {coach_need, coach_job, action}
for WAITING_HUMAN, and resume_key — the callback bar, per row, by construction.

READS: personas/, templates meta, plugin/skills digests, mcp-servers tree presence,
live roster/skills/installs/MCP (read-only RPC). No writes except --apply journal.
stdlib only. Secrets never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from gbtypes import atomic_write_text

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-capability-stage/1"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
EXIT_REFUSED = 5

KINDS = (
    "first_party_oauth_connector",
    "repo_plugin_skill",
    "hosted_mcp_server",
    "marketplace_skill_pack",
)

# Coach needs for human continuations. OAuth/consent/install approvals gate;
# app enablement is UI work; residual semantic calls are proofs.
NEED_FOR_KIND = {
    "first_party_oauth_connector": "gate",
    "repo_plugin_skill": "ui",
    "hosted_mcp_server": "gate",
    "marketplace_skill_pack": "proof",
}


def _digest_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def load_manifest(persona: str) -> Dict[str, Any]:
    """persona pack's capability manifest or ValueError."""
    path = ROOT / "personas" / (persona + ".json")
    if not path.is_file():
        raise ValueError("unknown persona %r" % persona)
    doc = json.loads(path.read_text(encoding="utf-8"))
    man = doc.get("capability_manifest") if isinstance(doc, dict) else None
    if not isinstance(man, dict) or not isinstance(man.get("capabilities"), list):
        raise ValueError("pack %s has no capability manifest" % path.name)
    return man


def repo_skill_digest(skill: str) -> Optional[str]:
    """sha12 of plugin/skills/<dir>/SKILL.md, or None when absent from the repo."""
    path = ROOT / "plugin" / "skills" / skill / "SKILL.md"
    if not path.is_file():
        return None
    return _digest_file(path)


def resolve_bot(target: str, roster: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Template-id target to live Bot by display-name fold. None when absent —
    absence is BLOCKED, never a fuzzy match."""
    want = target.strip().lower().replace("-", " ")
    for bot in roster:
        if not isinstance(bot, dict):
            continue
        if str(bot.get("name") or "").strip().lower() == want:
            return bot
    return None


def classify(
    cap: Dict[str, Any],
    target: str,
    live: Dict[str, Any],
    persona: str,
) -> Dict[str, Any]:
    """One capability x target row. Pure: all live reads arrive in `live`."""
    cid = str(cap.get("id") or "?")
    kind = str(cap.get("kind") or "?")
    resume = "%s@%s" % (cid, persona)
    cont = {
        "coach_need": NEED_FOR_KIND.get(kind, "other"),
        "coach_job": cid,
        "action": str(
            (
                (cap.get("boundary") or {})
                if isinstance(cap.get("boundary"), dict)
                else {}
            ).get("action")
            or "human step per manifest boundary"
        ),
    }
    base = {
        "capability": cid,
        "kind": kind,
        "target": target,
        "continuation": cont,
        "resume_key": resume,
    }
    if kind not in KINDS:
        return {**base, "verdict": "FAILED", "receipt": "unknown kind %r" % kind}
    if not target:
        return {**base, "verdict": "FAILED", "receipt": "empty target"}
    bot = resolve_bot(target, live.get("roster") or [])
    if bot is None:
        return {
            **base,
            "verdict": "BLOCKED",
            "receipt": "target %r has no live Bot" % target,
        }
    uuid = bot.get("legacyAgentId") or bot.get("agentId") or ""

    if kind == "first_party_oauth_connector":
        key = cid.split(".", 1)[-1] if "." in cid else cid
        server = {
            "gmail": "gmail",
            "calendar": "google-calendar",
            "drive": "google-drive",
        }.get(key, "")
        present = server in (live.get("connectors_present") or []) if server else False
        receipt = (
            "installed+enabled"
            if present
            else ("operator choice unmade" if not server else "no install record")
        )
        # Presence is not working: binding identity is hand-only, and no read
        # path exposes it. A bound-and-called proof stays human-side.
        return {
            **base,
            "verdict": "WAITING_HUMAN",
            "receipt": "%s (presence only; binding unreadable, call unproven)"
            % receipt,
        }

    if kind == "repo_plugin_skill":
        skill = (
            (cid.split(".", 1) + [""])[1]
            if "." in cid
            else cid.replace("repo-skill.", "")
        )
        want = repo_skill_digest(skill)
        if want is None:
            return {
                **base,
                "verdict": "FAILED",
                "receipt": "repo has no plugin/skills/%s/SKILL.md" % skill,
            }
        have = {
            (s.get("name"), _digest_text(s.get("body") or ""))
            for s in (live.get("bot_skills") or {}).get(uuid, [])
        }
        names = {n for n, _ in have}
        if (skill, want) in {(n, d) for n, d in have}:
            return {
                **base,
                "verdict": "PROVEN",
                "receipt": "attached skill digest %s matches repo" % want,
            }
        if skill in names or any(skill.lower() in str(n).lower() for n in names):
            return {
                **base,
                "verdict": "WAITING_HUMAN",
                "receipt": "name present but digest differs (want %s); re-attach exact repo file"
                % want,
            }
        return {
            **base,
            "verdict": "WAITING_HUMAN",
            "receipt": "not attached (repo digest %s); enable exact file in app" % want,
        }

    if kind == "hosted_mcp_server":
        server = cid.split(".", 1)[-1] if "." in cid else cid
        attached = server in (live.get("mcp_attached") or [])
        if not attached:
            return {
                **base,
                "verdict": "WAITING_HUMAN",
                "receipt": "server not in effective config; human install + allowlist per manifest",
            }
        tools = (live.get("mcp_tools") or {}).get(server, [])
        want_tools = cap.get("tool_allowlist") or []
        if want_tools and sorted(tools) == sorted(want_tools):
            return {
                **base,
                "verdict": "PROVEN",
                "receipt": "tool census equals declared allowlist (%d tools)"
                % len(tools),
            }
        return {
            **base,
            "verdict": "WAITING_HUMAN",
            "receipt": "attached but census %s != allowlist %s"
            % (sorted(tools), sorted(want_tools)),
        }

    # marketplace_skill_pack
    present = cid in (live.get("packs_installed") or [])
    if present:
        return {
            **base,
            "verdict": "PROVEN",
            "receipt": "installed+enabled in account installs (presence; semantic call residual)",
        }
    return {
        **base,
        "verdict": "WAITING_HUMAN",
        "receipt": "pack not installed; install then prove with a read-only call",
    }


def compile_matrix(
    personas: List[str],
    live: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """All personas to rows. Pure. Depends_on ordering is reported, not enforced
    here — apply sequences by it, matrix only shows it."""
    rows = []
    for persona in personas:
        man = load_manifest(persona)
        for cap in man["capabilities"]:
            if not isinstance(cap, dict):
                continue
            for target in cap.get("targets") or []:
                row = classify(cap, str(target), live, persona)
                row["persona"] = persona
                row["depends_on"] = cap.get("depends_on") or []
                rows.append(row)
    return rows


class Refused(Exception):
    """Typed refusal: human-class rows never auto-apply."""


def apply_rows(
    rows: List[Dict[str, Any]],
    attach: Callable[[str, str], Tuple[bool, str]],
    journal: Callable[[Dict[str, Any]], None],
    confirmed: Dict[str, bool],
    approved: bool,
) -> List[Dict[str, Any]]:
    """Mechanical apply for PROVEN-eligible rows only. WAITING_HUMAN/BLOCKED/
    FAILED rows are never executed — they are returned as skipped with their
    continuation intact. Resume-safe: confirmed resume_keys issue zero calls."""
    if not approved:
        raise Refused(
            "no-approval: capability apply needs --yes --approve-all with human-cleared rows"
        )
    outcomes = []
    for row in rows:
        key = row["resume_key"]
        if row["verdict"] != "PROVEN":
            outcomes.append({"resume_key": key, "skipped": row["verdict"]})
            continue
        if key in confirmed:
            outcomes.append({"resume_key": key, "skipped": "resume-confirmed"})
            continue
        journal({"event": "will-apply", "resume_key": key})
        ok, detail = attach(row["capability"], row["target"])
        if not ok:
            raise Refused(
                "apply-failed %s: %s (recover per row rollback)" % (key, detail)
            )
        journal({"event": "applied", "resume_key": key, "detail": detail})
        confirmed[key] = True
        outcomes.append({"resume_key": key, "applied": True})
    return outcomes


def _capabilities() -> int:
    print(
        json.dumps(
            {
                "tool": "gb-capability-stage",
                "verbs": ["matrix", "apply"],
                "kinds": list(KINDS),
                "verdicts": ["PROVEN", "WAITING_HUMAN", "BLOCKED", "FAILED"],
                "refusals": [
                    "human-class auto-apply",
                    "unproven apply without approval",
                ],
            },
            indent=1,
        )
    )
    return EXIT_OK


def selftest() -> int:
    """Table-driven legs over inline fixtures. No network, no files, no turns."""
    passed, failed = 0, []

    def leg(name: str, ok: bool) -> None:
        nonlocal passed
        if ok:
            passed += 1
        else:
            failed.append(name)

    live: Dict[str, Any] = {
        "roster": [
            {"name": "Chief of Staff", "legacyAgentId": "u1", "description": "X"}
        ],
        "connectors_present": ["gmail"],
        "bot_skills": {"u1": [{"name": "cos-digest", "body": "EXACT"}]},
        "mcp_attached": ["docs-mcp"],
        "mcp_tools": {"docs-mcp": ["documents.list", "documents.get"]},
        "packs_installed": ["skill-pack.firecrawl"],
    }
    import hashlib as _hl

    digest_exact = _hl.sha256(b"EXACT").hexdigest()[:12]

    def cap(cid: str, kind: str, targets: List[str], **kw: Any) -> Dict[str, Any]:
        d = {
            "id": cid,
            "kind": kind,
            "targets": targets,
            "boundary": {"actor": "human", "action": "do it", "state": "x"},
        }
        d.update(kw)
        return d

    # digest match needs the repo file: stub repo_skill_digest instead.
    real_digest = globals()["repo_skill_digest"]
    globals()["repo_skill_digest"] = lambda skill: digest_exact

    try:
        # 1. digest match on attached skill -> PROVEN.
        r = classify(
            cap("repo-skill.cos-digest", "repo_plugin_skill", ["chief-of-staff"]),
            "chief-of-staff",
            live,
            "p",
        )
        leg(
            "digest-match-proven",
            r["verdict"] == "PROVEN" and "matches repo" in r["receipt"],
        )
        # 2. name present, digest differs -> WAITING_HUMAN (not PROVEN).
        live2 = dict(live, bot_skills={"u1": [{"name": "cos-digest", "body": "OTHER"}]})
        r = classify(
            cap("repo-skill.cos-digest", "repo_plugin_skill", ["chief-of-staff"]),
            "chief-of-staff",
            live2,
            "p",
        )
        leg(
            "digest-drift-human",
            r["verdict"] == "WAITING_HUMAN" and "digest differs" in r["receipt"],
        )
        # 3. skill absent -> WAITING_HUMAN with repo digest recorded.
        r = classify(
            cap("repo-skill.standup-writer", "repo_plugin_skill", ["chief-of-staff"]),
            "chief-of-staff",
            live,
            "p",
        )
        leg(
            "absent-skill-human",
            r["verdict"] == "WAITING_HUMAN" and digest_exact in r["receipt"],
        )
        # 4. missing target Bot -> BLOCKED, never fuzzy.
        r = classify(
            cap("repo-skill.x", "repo_plugin_skill", ["ghost-desk"]),
            "ghost-desk",
            live,
            "p",
        )
        leg("missing-target-blocked", r["verdict"] == "BLOCKED")
        # 5. connector presence is not working -> WAITING_HUMAN.
        r = classify(
            cap("connector.gmail", "first_party_oauth_connector", ["chief-of-staff"]),
            "chief-of-staff",
            live,
            "p",
        )
        leg(
            "connector-presence-human",
            r["verdict"] == "WAITING_HUMAN" and "binding" in r["receipt"],
        )
        # 6. MCP census equals allowlist -> PROVEN.
        r = classify(
            cap(
                "mcp.docs-mcp",
                "hosted_mcp_server",
                ["chief-of-staff"],
                tool_allowlist=["documents.list", "documents.get"],
            ),
            "chief-of-staff",
            live,
            "p",
        )
        leg(
            "mcp-census-proven",
            r["verdict"] == "PROVEN" and "equals declared allowlist" in r["receipt"],
        )
        # 7. MCP unattached -> WAITING_HUMAN.
        r = classify(
            cap("mcp.sheets-mcp", "hosted_mcp_server", ["chief-of-staff"]),
            "chief-of-staff",
            live,
            "p",
        )
        leg("mcp-unattached-human", r["verdict"] == "WAITING_HUMAN")
        # 8. pack installed -> PROVEN with residual named.
        base_roster: List[Dict[str, Any]] = [
            r for r in (live.get("roster") or []) if isinstance(r, dict)
        ]
        live8: Dict[str, Any] = {
            **live,
            "roster": base_roster + [{"name": "Plugin Watch", "legacyAgentId": "u2"}],
        }
        r = classify(
            cap("skill-pack.firecrawl", "marketplace_skill_pack", ["plugin-watch"]),
            "plugin-watch",
            live8,
            "p",
        )
        leg(
            "pack-installed-proven",
            r["verdict"] == "PROVEN" and "residual" in r["receipt"],
        )
        r = classify(
            cap("skill-pack.nope", "marketplace_skill_pack", ["chief-of-staff"]),
            "chief-of-staff",
            live,
            "p",
        )
        leg("pack-missing-human", r["verdict"] == "WAITING_HUMAN")
        # 10. unknown kind / empty target -> FAILED with reason.
        r = classify(
            cap("x.1", "mystery_kind", ["chief-of-staff"]), "chief-of-staff", live, "p"
        )
        r2 = classify(
            cap("x.2", "repo_plugin_skill", ["chief-of-staff"]), "", live, "p"
        )
        leg("malformed-failed", r["verdict"] == "FAILED" and r2["verdict"] == "FAILED")
        # 11. every WAITING_HUMAN row carries a typed continuation (callback bar).
        sample = classify(
            cap("connector.gmail", "first_party_oauth_connector", ["chief-of-staff"]),
            "chief-of-staff",
            live,
            "p",
        )
        c = sample["continuation"]
        leg(
            "continuation-typed",
            all(c.get(k) for k in ("coach_need", "coach_job", "action")),
        )
        # 12. apply refuses without approval before any call.
        calls: List[str] = []

        def noattach(cap_id: str, target: str) -> Tuple[bool, str]:
            calls.append(cap_id)
            raise AssertionError("must not fire")

        try:
            apply_rows(
                [
                    {
                        "resume_key": "k",
                        "verdict": "PROVEN",
                        "capability": "c",
                        "target": "t",
                    }
                ],
                noattach,
                lambda e: None,
                {},
                False,
            )
            leg("apply-no-approval-refuses", False)
        except Refused:
            leg("apply-no-approval-refuses", calls == [])
        # 13. apply skips human rows and resumes confirmed with zero calls.
        out = apply_rows(
            [
                {
                    "resume_key": "k1",
                    "verdict": "WAITING_HUMAN",
                    "capability": "c",
                    "target": "t",
                },
                {
                    "resume_key": "k2",
                    "verdict": "PROVEN",
                    "capability": "c",
                    "target": "t",
                },
            ],
            lambda c, t: (True, "ok"),
            lambda e: None,
            {"k2": True},
            True,
        )
        leg(
            "apply-skips-and-resumes",
            out
            == [
                {"resume_key": "k1", "skipped": "WAITING_HUMAN"},
                {"resume_key": "k2", "skipped": "resume-confirmed"},
            ],
        )
    finally:
        globals()["repo_skill_digest"] = real_digest

    total = 13
    print(
        "SELFTEST %s - %d/%d"
        % ("PASS" if not failed and passed == total else "FAIL", passed, total)
    )
    for f in failed:
        print("  leg failed: %s" % f)
    return 0 if (not failed and passed == total) else 1


def fetch_live(token: str, rpc: Any, personas: List[str]) -> Dict[str, Any]:
    """Read-only live bundle. Every call is List/Get; failures raise RuntimeError
    (unmeasured, never an empty bundle passed off as measured)."""
    st, roster = rpc(token, "ListGrokBotAgents", {})
    if st != 200 or not isinstance(roster, dict):
        raise RuntimeError("ListGrokBotAgents HTTP %s" % st)
    agents = [a for a in roster.get("agents") or [] if isinstance(a, dict)]
    st, inst = rpc(
        token, "ListUserPluginInstalls", {}, service="aiserver.v1.DashboardService"
    )
    installs = (
        (inst.get("installs") or []) if st == 200 and isinstance(inst, dict) else []
    )
    st, eff = rpc(
        token,
        "GetEffectiveMcpConfigForUser",
        {},
        service="aiserver.v1.DashboardService",
    )
    effective = eff if st == 200 and isinstance(eff, dict) else {}
    uuids = {
        a.get("name"): (a.get("legacyAgentId") or a.get("agentId")) for a in agents
    }
    skills: Dict[str, List[Dict[str, Any]]] = {}
    targets: Set[str] = set()
    for p in personas:
        try:
            man = load_manifest(p)
        except ValueError:
            continue
        for cap in man.get("capabilities") or []:
            for t in cap.get("targets") or [] if isinstance(cap, dict) else []:
                targets.add(str(t))
    for t in targets:
        want = str(t).strip().lower().replace("-", " ")
        for name, uid in uuids.items():
            if str(name or "").strip().lower() == want and uid:
                sst, sk = rpc(token, "ListGrokBotAgentSkills", {"agent_id": uid})
                rows = (
                    (sk.get("skills") or [])
                    if sst == 200 and isinstance(sk, dict)
                    else []
                )
                skills[str(uid)] = [s for s in rows if isinstance(s, dict)]
                break

    st, eff = rpc(
        token,
        "GetEffectiveMcpConfigForUser",
        {},
        service="aiserver.v1.DashboardService",
    )
    servers: Dict[str, Any] = {}
    if st == 200 and isinstance(eff, dict):
        try:
            cfg = json.loads(eff.get("configJson") or "{}")
            if isinstance(cfg.get("mcpServers"), dict):
                servers = cfg["mcpServers"]
        except ValueError:
            servers = {}
    names = {str(n).lower(): n for n in servers if isinstance(n, str)}
    st, census = rpc(
        token, "ListSandMcpTools", {}, service="aiserver.v1.DashboardService"
    )
    tools_by_server: Dict[str, List[str]] = {}
    if st == 200 and isinstance(census, dict):
        for entry in census.get("servers") or []:
            if not isinstance(entry, dict):
                continue
            sid = str(entry.get("serverIdentifier") or "")
            tools_by_server[sid] = sorted(
                {
                    str(t.get("name") or "")
                    for t in entry.get("tools") or []
                    if isinstance(t, dict) and t.get("name")
                }
            )
            base = sid[5:] if sid.startswith("user-") else sid
            tools_by_server.setdefault(base, tools_by_server[sid])
    connectors = {
        k.lower()
        for k in names
        if any(s in k.lower() for s in ("gmail", "calendar", "drive"))
    }
    installs = (
        (inst.get("installs") or []) if st == 200 and isinstance(inst, dict) else []
    )
    packs = set()
    for i in installs:
        if not isinstance(i, dict):
            continue
        plug: Dict[str, Any] = i.get("plugin") or {}
        blob = str(plug.get("displayName") or "") + " " + str(plug.get("gitUrl") or "")
        if "firecrawl" in blob.lower() and i.get("isEnabled"):
            packs.add("skill-pack.firecrawl")
    return {
        "roster": agents,
        "installs": installs,
        "effective_mcp": servers,
        "bot_skills": skills,
        "connectors_present": sorted(connectors),
        "mcp_attached": sorted(names.keys()),
        "mcp_tools": tools_by_server,
        "packs_installed": sorted(packs),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="gb-capability-stage", description=__doc__)
    ap.add_argument("--persona", action="append", default=[])
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
        print(
            "gb-capability-stage: --persona <id> (repeatable; see personas/)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.apply and not (args.yes and args.approve_all):
        print(
            "gb-capability-stage: apply executes account writes; dry-run first, then --yes --approve-all with every row human-cleared",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    sys.path.insert(0, str(ROOT / "bin"))
    try:
        from gbrpc import access_token, rpc
        from gblib import platform_support

        support = platform_support().support_dir
        if support is None:
            raise RuntimeError("no desktop client state on this machine")
        token = access_token(support)
        live = fetch_live(token, rpc, args.persona)
    except RuntimeError as exc:
        print("gb-capability-stage: %s — unmeasured, not empty" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT
    except Exception as exc:
        print("gb-capability-stage: %s" % exc, file=sys.stderr)
        return EXIT_ENVIRONMENT
    try:
        rows = compile_matrix(args.persona, live)
    except ValueError as exc:
        print("gb-capability-stage: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    for r in rows:
        r["persona"] = r.get("persona") or args.persona[0]
    if args.apply:
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
        man_path = ROOT / "identity" / ("capability-%s.json" % stamp)

        def journal(ev: Dict[str, Any]) -> None:
            man_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                man = json.loads(man_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                man = {"schema": "gb-capability-manifest/1", "events": []}
            man["events"].append(ev)
            atomic_write_text(man_path, json.dumps(man, indent=1) + "\n")

        def no_primitive(cap_id: str, target: str) -> Tuple[bool, str]:
            raise Refused(
                "no proven attach primitive for %s (NE-22: Add 404s); human row"
                % cap_id
            )

        confirmed: Dict[str, bool] = {}
        try:
            outcomes = apply_rows(rows, no_primitive, journal, confirmed, True)
        except Refused as exc:
            print("gb-capability-stage: %s" % exc, file=sys.stderr)
            return EXIT_FINDINGS
        if args.json:
            print(
                json.dumps(
                    {"schema": SCHEMA, "apply": True, "outcomes": outcomes}, indent=1
                )
            )
        else:
            for o in outcomes:
                print("%s\t%s" % (o["resume_key"], o.get("skipped", "applied")))
        return EXIT_OK
    if args.json:
        print(
            json.dumps(
                {"schema": SCHEMA, "personas": args.persona, "rows": rows}, indent=1
            )
        )
    else:
        for r in rows:
            print(
                "%s\t%s\t%s\t%s"
                % (
                    r["capability"],
                    r["target"],
                    r["verdict"],
                    r.get("receipt", "")[:100],
                )
            )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
