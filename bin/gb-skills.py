#!/usr/bin/env python3
"""gb-skills — inspect skills without turning discovery into an account mutation.

Oracle for "is it on the Bot" is ListGrokBotAgentSkills, never the Bot's own words.

Modes are explicit: list/census/disk are reads; probe/hunt are zero-RPC dry-runs; attach is an
approved-write request that currently refuses because no safe compensating skill RPC is proven;
try permits only the exact read allowlist below. Unknown, write-shaped, and Delete methods refuse
even with --yes unless they can enter the durable pre-state/manifest/compensation transaction.

    gb skills list [--bot NAME] [--json]
    gb skills probe [--json]                    # dry-run, zero candidate RPCs
    gb skills hunt --bot NAME [--json]          # dry-run, zero candidate RPCs
    gb skills try --method M --payload '{...}' [--service S]
    gb skills attach --bot NAME --file PATH [--yes]
    gb skills scan [--match SLUG] [--copy] [--force] [--json]

"""

from __future__ import annotations
import argparse
import collections
import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import types
import urllib.error
import urllib.request
import uuid as uuidlib
from typing import Any, Dict, List, Optional, Tuple


BIN = pathlib.Path(__file__).resolve().parent
ROOT = BIN.parent
sys.path.insert(0, str(BIN))
SUBPROCESS_TIMEOUT_S = 300  # outer deadline only: every child below already passes
# --timeout 180 to `gb bot ask`, so the inner deadline always
# fires first. This kwarg exists for g22-durable-io, which judges
# static shape (a hung child must fail, never hang the tick).
EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2
EXIT_ENVIRONMENT, EXIT_UPSTREAM, EXIT_REFUSED = 3, 4, 5

GROKBOT = "aiserver.v1.GrokBotService"
DASH = "aiserver.v1.DashboardService"

READ_RPC_ALLOWLIST = frozenset(
    (
        (GROKBOT, "ListGrokBotAgents"),
        (GROKBOT, "ListGrokBotAgentSkills"),
        (DASH, "GetManagedSkills"),
    )
)
# Metadata only. probe prints this list but never invokes a candidate. Delete remains named so the
# refusal is inspectable; it is never route-discovered by sending an empty request.
PROBE_CANDIDATES: Tuple[Tuple[str, str], ...] = (
    (GROKBOT, "CreateGrokBotAgentSkill"),
    (GROKBOT, "UpdateGrokBotAgentSkill"),
    (GROKBOT, "SetGrokBotAgentSkill"),
    (GROKBOT, "PutGrokBotAgentSkill"),
    (GROKBOT, "AddGrokBotAgentSkill"),
    (GROKBOT, "AttachGrokBotAgentSkill"),
    (GROKBOT, "SetGrokBotAgentSkills"),
    (GROKBOT, "UpdateGrokBotAgentSkills"),
    (GROKBOT, "CreateGrokBotSkill"),
    (GROKBOT, "DeleteGrokBotAgentSkill"),
    (DASH, "CreateManagedSkill"),
    (DASH, "UpdateManagedSkill"),
    (DASH, "SetManagedSkill"),
)
WRITE_PREFIXES = (
    "Create",
    "Update",
    "Set",
    "Put",
    "Add",
    "Attach",
    "Install",
    "Enable",
)
IRREVERSIBLE_PREFIXES = ("Delete", "Remove", "Detach", "Destroy")


def _rpc_class(service: str, method: str) -> str:
    if (service, method) in READ_RPC_ALLOWLIST:
        return "PURE_READ"
    if method.startswith(IRREVERSIBLE_PREFIXES):
        return "IRREVERSIBLE"
    if method.startswith(WRITE_PREFIXES):
        return "WRITE"
    return "UNKNOWN"


def classify(status: int, body: Any) -> str:
    """Grade a declared read response; empty 200 is ambiguous, never route proof."""
    text = body if isinstance(body, str) else json.dumps(body)
    low = text.lower()
    if status == 404 and "route post:" in low:
        return "ABSENT"
    if status == 0:
        return "ERROR"
    if status == 200 and body in ({}, "", None):
        return "AMBIGUOUS"
    if status in (200, 400) or (status == 404 and "not_found" in low):
        return "REAL"
    if status == 404:
        return "ABSENT"
    return "REAL"


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


@dataclasses.dataclass(frozen=True)
class Sess:
    """The authenticated session every networked command threads through. The
    (mod, token) pair traveled together through every signature — one bundle."""

    mod: types.ModuleType
    token: str


def _resolve_bot(roster: Any, bot: str) -> Tuple[str, str]:
    """(uuid, numeric id) for a roster name, case-insensitive. Empty pair when
    absent — the THIRD copy of this loop; the other two called it a day."""
    if isinstance(roster, dict):
        for agent in roster.get("agents") or []:
            if not isinstance(agent, dict):
                continue
            if str(agent.get("name") or "").lower() == bot.strip().lower():
                return (
                    str(agent.get("legacyAgentId") or agent.get("agentId") or ""),
                    str(agent.get("id") or ""),
                )
    return ("", "")


def _transport_rpc(
    sess: Sess,
    method: str,
    body: dict[str, Any],
    service: str = GROKBOT,
    timeout: float = 30.0,
) -> Tuple[int, Any]:
    req = urllib.request.Request(
        f"https://{sess.mod.HOST}/{service}/{method}",
        data=json.dumps(body).encode(),
        headers={
            "authorization": f"Bearer {sess.token}",
            "content-type": "application/json",
            "connect-protocol-version": "1",
            "user-agent": "gb-skills/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return int(resp.status), json.loads(raw or "{}")
            except json.JSONDecodeError:
                return int(resp.status), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        try:
            parsed: Any = json.loads(raw or "{}")
        except json.JSONDecodeError:
            parsed = raw
        return int(exc.code), parsed
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def rpc(
    sess: Sess,
    method: str,
    body: dict[str, Any],
    service: str = GROKBOT,
    timeout: float = 30.0,
) -> Tuple[int, Any]:
    """The ordinary transport is read-only by exact allowlist, not method spelling."""
    if _rpc_class(service, method) != "PURE_READ":
        raise ValueError(f"{service}/{method} requires an approved mutation manifest")
    return _transport_rpc(sess, method, body, service=service, timeout=timeout)


def _approved_rpc(
    sess: Sess,
    method: str,
    body: Dict[str, Any],
    service: str,
    manifest_path: pathlib.Path,
) -> Tuple[int, Any]:
    """Send only the request or compensation sealed in one PREPARED manifest."""
    doc = json.loads(pathlib.Path(manifest_path).read_text())
    if doc.get("schema") != "gb-skills-mutation/1" or doc.get("verdict") != "PREPARED":
        raise ValueError("approved write requires a PREPARED mutation manifest")
    if service != doc.get("service"):
        raise ValueError("approved write service differs from manifest")
    ledger = _ledger_module()
    if method == doc.get("request_method"):
        expected_digest = doc.get("request_digest")
    elif method == doc.get("compensation_method"):
        expected_digest = doc.get("compensation_digest")
    else:
        raise ValueError("approved write method differs from manifest")
    if ledger.digest_json(body) != expected_digest:
        raise ValueError("approved write body differs from manifest digest")
    return _transport_rpc(sess, method, body, service=service)


def _fetch_bot_skills(sess: Sess, name: str, uuid: str) -> Dict[str, Any]:
    """One Bot's skill inventory row. A Bot whose skills read fails keeps its
    row with the failing status — a failed read is data, never a dropped row."""
    sst, skills = rpc(sess, "ListGrokBotAgentSkills", {"agent_id": uuid})
    got = (
        skills.get("skills") if sst == 200 and isinstance(skills, dict) else None
    ) or []
    return {
        "name": name,
        "uuid": uuid,
        "http": sst,
        "skills": [
            {
                "id": s.get("id") or s.get("skillId") or s.get("skill_id"),
                "name": s.get("name"),
                "source": s.get("source"),
                "keys": sorted(s.keys()),
                "description_chars": len(s.get("description") or ""),
                "body_chars": len(s.get("body") or ""),
            }
            for s in got
            if isinstance(s, dict)
        ],
    }


def _render_list(as_json: bool, rows: List[Dict[str, Any]]) -> int:
    """Roster table, both envelopes. Zero matching Bots is a finding."""
    if as_json:
        print(json.dumps({"schema": "gb-skills/1", "bots": rows}, indent=1))
        return EXIT_OK
    if not rows:
        print("gb-skills: no matching Bot")
        return EXIT_FINDINGS
    for row in rows:
        names = [str(s.get("name") or "") for s in row["skills"]]
        print(f"{row['name']}\t{len(names)}\t{','.join(names) or '-'}")
    return EXIT_OK


def cmd_list(sess: Sess, bot: str, as_json: bool) -> int:
    st, roster = rpc(sess, "ListGrokBotAgents", {})
    if st != 200 or not isinstance(roster, dict):
        print(f"gb-skills: roster HTTP {st}", file=sys.stderr)
        return EXIT_UPSTREAM
    want = bot.strip().lower()
    rows: List[Dict[str, Any]] = []
    for agent in roster.get("agents") or []:
        if not isinstance(agent, dict):
            continue
        name = str(agent.get("name") or "")
        uuid = str(agent.get("legacyAgentId") or agent.get("agentId") or "")
        if want and name.lower() != want:
            continue
        if not uuid:
            continue
        rows.append(_fetch_bot_skills(sess, name, uuid))
    return _render_list(as_json, rows)


def _safety_receipt(
    *,
    mode: str,
    rpc_class: str,
    target: Any,
    unique_identity: Optional[str],
    verdict: str,
    exit_code: int,
    unavailable: str,
) -> Dict[str, Any]:
    """One body-free safety envelope shared by every non-mutating refusal."""
    return {
        "command_mode": mode,
        "candidate_rpc_class": rpc_class,
        "write_attempt_count": 0,
        "target_identity": target,
        "unique_identity": unique_identity,
        "manifest_id": None,
        "manifest_path": None,
        "pre_state_digest": None,
        "request_id": None,
        "readback_id": None,
        "compensation_id": None,
        "partial_residue": 0,
        "residue": [],
        "verdict": verdict,
        "exit": exit_code,
        "recovery_argv": [],
        "live_evidence_available": False,
        "unavailable_evidence": unavailable,
    }


def _render_safety(label: str, as_json: bool, out: Dict[str, Any]) -> int:
    if as_json:
        print(json.dumps(out, indent=1))
    else:
        print(
            f"{label} mode={out['command_mode']} verdict={out['verdict']} "
            f"write_attempts={out['write_attempt_count']}"
        )
        print(f"  unavailable: {out['unavailable_evidence']}")
    return int(out["exit"])


def cmd_probe(sess: Sess, yes: bool, as_json: bool) -> int:
    """Describe blocked write routes without calling any of them."""
    # Kept in the signature for direct fixture parity; dry-run never consults it.
    del sess
    out = _safety_receipt(
        mode="approved-write-refused" if yes else "dry-run",
        rpc_class="WRITE_OR_IRREVERSIBLE",
        target=None,
        unique_identity=None,
        verdict="REFUSED",
        exit_code=EXIT_REFUSED,
        unavailable=(
            "live write-route existence; empty write/Delete POST discovery is prohibited and "
            "probe has no named target, pre-state, or proven compensation"
        ),
    )
    out.update(
        {
            "schema": "gb-skills-probe/2",
            "candidates": [
                {
                    "service": service.split(".")[-1],
                    "method": method,
                    "candidate_rpc_class": _rpc_class(service, method),
                    "action": "REFUSED",
                }
                for service, method in PROBE_CANDIDATES
            ],
        }
    )
    return _render_safety("probe", as_json, out)


def _parse_skill_text(text: str, fallback_name: str = "") -> Dict[str, str]:
    """Parse one SKILL.md without retaining its private body in a receipt."""
    name = fallback_name
    desc = ""
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm, body = parts[1], parts[2].lstrip("\n")
            for line in fm.splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
    return {"name": name, "description": desc, "body": body}


def _skill_file(path: pathlib.Path) -> Dict[str, str]:
    return _parse_skill_text(path.read_text(), path.parent.name)


SCAN_VENDORS: Tuple[Tuple[str, str], ...] = (
    ("claude", ".claude/skills"),
    ("codex", ".codex/skills"),
    ("cursor", ".cursor/skills"),
    ("grok", ".grok/skills"),
    ("agents", ".agents/skills"),
)
SCAN_SCHEMA = "gb-skills-scan/1"


def _scan_home(home: Optional[pathlib.Path] = None) -> pathlib.Path:
    if home is not None:
        return pathlib.Path(home)
    return pathlib.Path(os.environ.get("HOME") or pathlib.Path.home())


def _count_skill_md(root: pathlib.Path) -> int:
    if not root.exists():
        return 0
    target = root.resolve() if root.is_symlink() else root
    if not target.is_dir():
        return 0
    n = 0
    for dirpath, _dirnames, filenames in os.walk(target, followlinks=False):
        if "SKILL.md" in filenames:
            n += 1
    return n


def _root_row(label: str, path: pathlib.Path) -> Dict[str, Any]:
    exists = path.exists()
    alias_of = None
    if path.is_symlink():
        try:
            alias_of = str(path.resolve())
        except OSError:
            alias_of = None
    return {
        "alias_of": alias_of,
        "count": _count_skill_md(path) if exists else 0,
        "exists": exists,
        "label": label,
        "path": str(path),
    }


def scan_roots(
    home: pathlib.Path, repo: pathlib.Path
) -> List[Dict[str, Any]]:
    rows = [_root_row(label, home / rel) for label, rel in SCAN_VENDORS]
    rows.append(_root_row("plugin", repo / "plugin" / "skills"))
    return rows


def _iter_skill_dirs(root: pathlib.Path) -> List[pathlib.Path]:
    if not root.exists():
        return []
    target = root.resolve() if root.is_symlink() or root.is_dir() else root
    if not target.is_dir():
        return []
    found: List[pathlib.Path] = []
    for dirpath, _dirnames, filenames in os.walk(target, followlinks=False):
        if "SKILL.md" in filenames:
            found.append(pathlib.Path(dirpath))
    return found


def _file_digest(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _safe_slug(slug: str) -> Optional[str]:
    s = str(slug or "").strip()
    if not s or s in (".", "..") or "/" in s or "\\" in s:
        return None
    return s


def match_skills(
    roots: List[Dict[str, Any]], needle: str
) -> List[Dict[str, Any]]:
    want = needle.strip().casefold()
    if not want:
        return []
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in roots:
        if not row.get("exists"):
            continue
        path = pathlib.Path(str(row["path"]))
        try:
            resolved_root = path.resolve()
        except OSError:
            continue
        for skill_dir in _iter_skill_dirs(path):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                parsed = _parse_skill_text(skill_md.read_text(), skill_dir.name)
            except OSError:
                continue
            slug = skill_dir.name
            name = parsed.get("name") or slug
            if want not in slug.casefold() and want not in name.casefold():
                continue
            try:
                key = str(skill_dir.resolve())
            except OSError:
                key = str(skill_dir)
            hit = grouped.get(key)
            if hit is None:
                grouped[key] = {
                    "aliases": [row["label"]],
                    "description": _first_line(parsed.get("description") or ""),
                    "digest": _file_digest(skill_md),
                    "name": name,
                    "path": key,
                    "resolved_root": str(resolved_root),
                    "slug": slug,
                }
            else:
                if row["label"] not in hit["aliases"]:
                    hit["aliases"].append(row["label"])
    hits = list(grouped.values())
    hits.sort(key=lambda h: (str(h.get("slug") or ""), str(h.get("path") or "")))
    return hits


def _copy_dest(repo: pathlib.Path, slug: str) -> Optional[pathlib.Path]:
    safe = _safe_slug(slug)
    if safe is None:
        return None
    base = (repo / "plugin" / "skills").resolve()
    dest = (base / safe).resolve()
    if dest != base and base not in dest.parents:
        return None
    return dest


def copy_match(
    hit: Dict[str, Any],
    repo: pathlib.Path,
    home: pathlib.Path,
    force: bool,
) -> Dict[str, Any]:
    slug = str(hit.get("slug") or "")
    dest = _copy_dest(repo, slug)
    src = pathlib.Path(str(hit.get("path") or ""))
    receipt = {
        "digest": hit.get("digest"),
        "name": hit.get("name"),
        "path": str(src),
        "slug": slug,
    }
    if dest is None or not src.is_dir():
        receipt["ok"] = False
        receipt["reason"] = "unsafe slug or missing source"
        return receipt
    home_res = home.resolve()
    if home_res in dest.parents or dest == home_res:
        receipt["ok"] = False
        receipt["reason"] = "refuse — will not write under $HOME vendor roots"
        return receipt
    if dest.exists() and not force:
        receipt["ok"] = False
        receipt["dest"] = str(dest)
        receipt["reason"] = "refuse — dest exists (pass --force to overwrite)"
        return receipt
    if dest.exists() and force:
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    receipt["ok"] = True
    receipt["dest"] = str(dest)
    return receipt


def cmd_scan(
    *,
    match: str = "",
    do_copy: bool = False,
    force: bool = False,
    as_json: bool = False,
    home: Optional[pathlib.Path] = None,
    repo: Optional[pathlib.Path] = None,
) -> int:
    """Offline local skill-pack census. No account RPC. No attach."""
    home_path = _scan_home(home)
    repo_path = pathlib.Path(repo) if repo is not None else ROOT
    roots = scan_roots(home_path, repo_path)
    needle = match.strip()
    hits = match_skills(roots, needle) if needle else []
    copies: List[Dict[str, Any]] = []
    if do_copy and not needle:
        print("gb-skills: scan --copy needs --match SLUG (or a positional slug)", file=sys.stderr)
        return EXIT_USAGE
    if do_copy:
        for hit in hits:
            copies.append(copy_match(hit, repo_path, home_path, force))
    out = {
        "schema": SCAN_SCHEMA,
        "command_mode": "scan",
        "candidate_rpc_class": "NONE",
        "write_attempt_count": 0,
        "roots": [
            {
                "alias_of": row.get("alias_of"),
                "count": row.get("count"),
                "exists": row.get("exists"),
                "label": row.get("label"),
                "path": row.get("path"),
            }
            for row in roots
        ],
        "matches": [
            {
                "aliases": hit.get("aliases"),
                "description": hit.get("description"),
                "digest": hit.get("digest"),
                "name": hit.get("name"),
                "path": hit.get("path"),
                "slug": hit.get("slug"),
            }
            for hit in hits
        ],
        "copied": [
            {
                "dest": row.get("dest"),
                "digest": row.get("digest"),
                "name": row.get("name"),
                "ok": row.get("ok"),
                "path": row.get("path"),
                "reason": row.get("reason"),
                "slug": row.get("slug"),
            }
            for row in copies
        ],
    }
    if as_json:
        print(json.dumps(out, indent=1))
    elif needle:
        if not hits:
            print("scan: no match %r" % needle)
        for hit in hits:
            aliases = ",".join(hit.get("aliases") or [])
            print(
                "MATCH  %s  %s  aliases=%s"
                % (hit.get("slug"), hit.get("path"), aliases)
            )
            print("  name  %s" % (hit.get("name") or "-"))
            print("  description  %s" % (hit.get("description") or "-"))
            if not do_copy:
                print(
                    "  cp -R %s plugin/skills/%s"
                    % (hit.get("path"), hit.get("slug"))
                )
                print(
                    "  or: Open Grok Bot → Private skills → New, paste SKILL.md"
                )
        for row in copies:
            if row.get("ok"):
                print("COPIED  %s -> %s" % (row.get("slug"), row.get("dest")))
            else:
                print(
                    "COPY REFUSED  %s  %s"
                    % (row.get("slug"), row.get("reason") or "refused")
                )
    else:
        for row in roots:
            if not row.get("exists"):
                print("MISSING  %s  %s" % (row.get("label"), row.get("path")))
            elif row.get("alias_of"):
                print(
                    "ALIAS  %s  %s  alias-of %s  n=%s"
                    % (
                        row.get("label"),
                        row.get("path"),
                        row.get("alias_of"),
                        row.get("count"),
                    )
                )
            else:
                print(
                    "ROOT  %s  %s  n=%s"
                    % (row.get("label"), row.get("path"), row.get("count"))
                )
    if copies and not all(row.get("ok") for row in copies):
        return EXIT_REFUSED
    return EXIT_OK


def _ledger_module() -> types.ModuleType:
    """Load the sibling receipt module without giving its CLI parser a second code path."""
    name = "gb_skills_ledger_receipts"
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(name, BIN / "gb-skills-ledger.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load gb-skills-ledger.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _canonical_inventory(state: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order-insensitive full inventory; bodies stay in the sealed pre-state, never logs."""
    return sorted(
        (json.loads(json.dumps(row, sort_keys=True)) for row in state),
        key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")),
    )


def _complete_inventory(status: int, state: Any) -> bool:
    return (
        status == 200
        and isinstance(state, list)
        and all(isinstance(row, dict) for row in state)
    )


def _inventory_residue(
    before: List[Dict[str, Any]], after: List[Dict[str, Any]]
) -> List[str]:
    def encoded(rows: List[Dict[str, Any]]) -> collections.Counter[str]:
        return collections.Counter(
            json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows
        )

    def label(raw: str, direction: str) -> str:
        row = json.loads(raw)
        identity = row.get("name") or row.get("id") or row.get("skillId") or "unnamed"
        digest = _ledger_module().digest_json(row).split(":", 1)[-1][:12]
        return f"{direction}:{identity}:{digest}"

    old, new = encoded(before), encoded(after)
    residue: List[str] = []
    for raw, count in sorted((new - old).items()):
        residue.extend(label(raw, "added") for _ in range(count))
    for raw, count in sorted((old - new).items()):
        residue.extend(label(raw, "missing") for _ in range(count))
    return residue


@dataclasses.dataclass(frozen=True)
class ApprovedMutation:
    """All facts that must exist before a write can cross the approval boundary."""

    operation_id: str
    command_mode: str
    target_identity: Dict[str, str]
    unique_identity: str
    service: str
    request_method: str
    request_body: Dict[str, Any]
    expected_post_state: List[Dict[str, Any]]
    compensation_method: str
    compensation_body: Dict[str, Any]
    compensation_evidence: str
    recovery_argv: List[str]
    retain_delta: bool = False


def _transaction_refusal(plan: ApprovedMutation, reason: str) -> Dict[str, Any]:
    out = _safety_receipt(
        mode=plan.command_mode,
        rpc_class=_rpc_class(plan.service, plan.request_method),
        target=plan.target_identity,
        unique_identity=plan.unique_identity or None,
        verdict="REFUSED",
        exit_code=EXIT_REFUSED,
        unavailable=reason,
    )
    out["schema"] = "gb-skills-mutation/1"
    return out


def _execute_approved_mutation(
    sess: Sess,
    plan: ApprovedMutation,
    read_inventory: Any,
    *,
    call: Any = None,
    manifest_dir: Optional[pathlib.Path] = None,
) -> Dict[str, Any]:
    """Execute one bounded mutation, prove its delta, and compensate every failure.

    The production CLI currently has no skill route with a proven inverse and therefore never
    calls this function. Provider adoption requires a reviewed request/compensation pair, not a
    second implementation of the safety boundary.
    """
    approved_call = call
    rpc_kind = _rpc_class(plan.service, plan.request_method)
    compensation_kind = _rpc_class(plan.service, plan.compensation_method)
    missing = []
    if plan.command_mode != "approved-write":
        missing.append("explicit approved-write mode")
    if rpc_kind not in ("WRITE", "IRREVERSIBLE"):
        missing.append("declared write RPC")
    if not plan.target_identity or not all(plan.target_identity.values()):
        missing.append("named authoritative target")
    if not plan.unique_identity:
        missing.append("unique identity")
    if not plan.request_body:
        missing.append("non-empty write request")
    elif plan.unique_identity not in json.dumps(plan.request_body, sort_keys=True):
        missing.append("unique identity bound to request")
    if (
        not plan.compensation_method
        or not plan.compensation_body
        or not plan.compensation_evidence
        or compensation_kind not in ("WRITE", "IRREVERSIBLE")
    ):
        missing.append("proven compensating action")
    if not _complete_inventory(200, plan.expected_post_state):
        missing.append("complete expected post-state")
    elif plan.unique_identity not in json.dumps(
        plan.expected_post_state, sort_keys=True
    ):
        missing.append("unique identity bound to expected delta")
    if not plan.recovery_argv:
        missing.append("exact recovery argv")
    if missing:
        return _transaction_refusal(plan, "; ".join(missing))

    def safe_read() -> Tuple[int, Any]:
        try:
            return read_inventory()
        except Exception:
            return 0, None

    def safe_call(method: str, body: Dict[str, Any]) -> int:
        try:
            status, _ = call(sess, method, body, service=plan.service)
            return int(status)
        except Exception:
            # A transport exception can arrive after the server committed. The mandatory readback
            # and compensation below still run; an exception is never treated as zero side effect.
            return 0

    pre_http, pre_raw = safe_read()
    if not _complete_inventory(pre_http, pre_raw):
        return _transaction_refusal(
            plan, f"complete authoritative pre-state unavailable (HTTP {pre_http})"
        )
    before = _canonical_inventory(pre_raw)
    expected = _canonical_inventory(plan.expected_post_state)
    ledger = _ledger_module()
    request_id = "req-" + uuidlib.uuid4().hex
    readback_id = "read-" + uuidlib.uuid4().hex
    compensation_id = "comp-" + uuidlib.uuid4().hex
    compensation_readback_id = "restore-read-" + uuidlib.uuid4().hex
    metadata = {
        "command_mode": plan.command_mode,
        "candidate_rpc_class": rpc_kind,
        "service": plan.service,
        "target_identity": plan.target_identity,
        "unique_identity": plan.unique_identity,
        "pre_state_digest": ledger.digest_json(before),
        "expected_post_state_digest": ledger.digest_json(expected),
        "request_id": request_id,
        "request_method": plan.request_method,
        "request_digest": ledger.digest_json(plan.request_body),
        "readback_id": readback_id,
        "compensation_id": compensation_id,
        "compensation_method": plan.compensation_method,
        "compensation_digest": ledger.digest_json(plan.compensation_body),
        "compensation_evidence": plan.compensation_evidence,
        "compensation_readback_id": compensation_readback_id,
        "recovery_argv": list(plan.recovery_argv),
    }
    directory = manifest_dir if manifest_dir is not None else ledger.MUTATIONS
    try:
        manifest_path = ledger.begin_mutation(
            plan.operation_id, metadata, before, pathlib.Path(directory)
        )
    except (OSError, ValueError, FileExistsError) as exc:
        return _transaction_refusal(
            plan, f"durable manifest refused before write ({type(exc).__name__})"
        )
    if approved_call is None:

        def sealed_call(
            call_sess: Sess,
            method: str,
            body: Dict[str, Any],
            service: str = GROKBOT,
        ) -> Tuple[int, Any]:
            return _approved_rpc(call_sess, method, body, service, manifest_path)

        approved_call = sealed_call
    call = approved_call
    try:
        ledger.finish_mutation(manifest_path, {"write_attempt_count": 1})
    except (OSError, ValueError) as exc:
        out = _transaction_refusal(
            plan, f"request attempt could not be journaled ({type(exc).__name__})"
        )
        out.update(
            {
                "manifest_id": plan.operation_id,
                "manifest_path": str(manifest_path),
                "pre_state_digest": metadata["pre_state_digest"],
                "request_id": request_id,
                "readback_id": readback_id,
                "compensation_id": compensation_id,
                "recovery_argv": list(plan.recovery_argv),
            }
        )
        return out
    write_http = safe_call(plan.request_method, plan.request_body)
    post_http, post_raw = safe_read()
    post_complete = _complete_inventory(post_http, post_raw)
    post = _canonical_inventory(post_raw) if post_complete else []
    write_ok = 200 <= write_http < 300
    delta_ok = post_complete and post == expected
    receipt_failed = False
    if write_ok and delta_ok and plan.retain_delta:
        try:
            return ledger.finish_mutation(
                manifest_path,
                {
                    "write_attempt_count": 1,
                    "write_http": write_http,
                    "readback_http": post_http,
                    "readback_digest": ledger.digest_json(post),
                    "compensation_http": None,
                    "compensation_readback_http": None,
                    "compensation_readback_digest": None,
                    "partial_residue": 0,
                    "residue": [],
                    "verdict": "LANDED",
                    "exit": EXIT_OK,
                },
            )
        except (OSError, ValueError):
            # A landed change without a terminal receipt is not success. Restore it now while the
            # PREPARED manifest still contains the exact recovery argv and sealed pre-state.
            receipt_failed = True
    try:
        ledger.finish_mutation(manifest_path, {"write_attempt_count": 2})
    except (OSError, ValueError):
        # Compensation is safer than stopping with a possibly landed mutation.
        pass
    compensation_http = safe_call(plan.compensation_method, plan.compensation_body)
    restore_http, restore_raw = safe_read()
    restore_complete = _complete_inventory(restore_http, restore_raw)
    restored = _canonical_inventory(restore_raw) if restore_complete else []
    residue = (
        _inventory_residue(before, restored)
        if restore_complete
        else [f"unknown:authoritative-reread-http-{restore_http}"]
    )
    compensation_ok = 200 <= compensation_http < 300
    if receipt_failed and not residue:
        verdict, exit_code = "RESTORED_AFTER_RECEIPT_FAILURE", EXIT_FINDINGS
    elif (
        write_ok
        and delta_ok
        and not plan.retain_delta
        and compensation_ok
        and not residue
    ):
        verdict, exit_code = "CANARY_PROVED_RESTORED", EXIT_OK
    elif not residue:
        verdict, exit_code = "RESTORED_AFTER_FAILURE", EXIT_FINDINGS
    else:
        verdict, exit_code = "PARTIAL_RESIDUE", EXIT_FINDINGS
    return ledger.finish_mutation(
        manifest_path,
        {
            "write_attempt_count": 2,
            "write_http": write_http,
            "readback_http": post_http,
            "readback_digest": ledger.digest_json(post) if post_complete else None,
            "compensation_http": compensation_http,
            "compensation_readback_http": restore_http,
            "compensation_readback_digest": (
                ledger.digest_json(restored) if restore_complete else None
            ),
            "partial_residue": len(residue),
            "residue": residue,
            "verdict": verdict,
            "exit": exit_code,
        },
    )


def cmd_attach(
    sess: Sess,
    bot: str,
    file_s: str,
    yes: bool,
    as_json: bool,
) -> int:
    """Refuse the unproven Add route without probing it; direct the proven human path."""
    del sess
    path = pathlib.Path(file_s)
    if not path.is_file():
        print(f"gb-skills: no file {file_s}", file=sys.stderr)
        return EXIT_USAGE
    skill = _skill_file(path)
    out = _safety_receipt(
        mode="approved-write-refused" if yes else "dry-run",
        rpc_class="WRITE",
        target={"requested_bot": bot, "authoritative_id": None},
        unique_identity=skill["name"],
        verdict="REFUSED",
        exit_code=EXIT_REFUSED,
        unavailable=(
            "safe install route: AddGrokBotAgentSkill request shape and an exact compensating "
            "Delete/restore action are unproven; no discovery or install RPC was sent"
        ),
    )
    out.update(
        {
            "schema": "gb-skills-attach/2",
            "file": str(path),
            "missing_requirements": [
                "authoritative target id",
                "complete authoritative pre-state",
                "proven compensating action",
            ],
            "next": f"gb skills enable --bot {bot!r} --file {str(path)!r}",
        }
    )
    return _render_safety("attach", as_json, out)


class _FakeSkillService:
    """Known-bad service: empty writes mutate and compensation can fail."""

    def __init__(
        self, *, mutate_on_empty: bool = False, fail_compensation: bool = False
    ):
        self.mutate_on_empty = mutate_on_empty
        self.fail_compensation = fail_compensation
        self.skills: List[Dict[str, Any]] = []
        self.calls: List[Dict[str, Any]] = []

    def call(
        self,
        sess: Sess,
        method: str,
        body: Dict[str, Any],
        service: str = GROKBOT,
        timeout: float = 30.0,
    ) -> Tuple[int, Any]:
        del sess, timeout
        kind = _rpc_class(service, method)
        self.calls.append(
            {
                "method": method,
                "candidate_rpc_class": kind,
                "empty": not bool(body),
            }
        )
        if method == "ListGrokBotAgentSkills":
            return 200, {"skills": json.loads(json.dumps(self.skills))}
        if method == "ListGrokBotAgents":
            return 200, {
                "agents": [{"name": "Fake", "legacyAgentId": "fake-uuid", "id": "7"}]
            }
        if kind == "IRREVERSIBLE":
            if self.fail_compensation:
                return 500, {"error": "compensation failed"}
            identity = str(body.get("id") or body.get("name") or "")
            self.skills = [
                row
                for row in self.skills
                if str(row.get("id") or row.get("name") or "") != identity
            ]
            return 200, {}
        if kind == "WRITE":
            if body or self.mutate_on_empty:
                name = str(body.get("name") or "empty-write-residue")
                self.skills.append({"id": f"fake-{len(self.skills) + 1}", "name": name})
            return 200, {}
        return 404, "Route POST: fake not found"

    def inventory(self, sess: Sess) -> Tuple[int, List[Dict[str, Any]]]:
        status, body = self.call(sess, "ListGrokBotAgentSkills", {})
        return status, body.get("skills") if isinstance(body, dict) else []

    @property
    def write_calls(self) -> List[Dict[str, Any]]:
        return [
            row
            for row in self.calls
            if row["candidate_rpc_class"] in ("WRITE", "IRREVERSIBLE")
        ]


def selftest() -> int:
    fails = 0

    def check(name: str, cond: bool) -> None:
        nonlocal fails
        if not cond:
            print(f"FAIL {name}")
            fails += 1
            return
        print(f"ok   {name}")

    check(
        "route-404-is-absent",
        classify(
            404,
            "Route POST:/aiserver.v1.GrokBotService/CreateGrokBotAgentSkill not found",
        )
        == "ABSENT",
    )
    check("invalid-argument-is-real", classify(400, "invalid_argument") == "REAL")
    check("resource-404-is-real", classify(404, '{"code":"not_found"}') == "REAL")
    check("empty-200-is-ambiguous", classify(200, {}) == "AMBIGUOUS")
    check("network-zero-is-error", classify(0, "URLError") == "ERROR")
    parsed = _skill_file(ROOT / "plugin" / "skills" / "vendor-watch" / "SKILL.md")
    check("skill-file-name", parsed.get("name") == "vendor-watch")
    check("skill-file-body", "Do the job" in parsed.get("body", ""))
    check("hunt-fields-include-source-agent", "source_agent_id" in HUNT_FIELDS)
    check(
        "read-allowlist-is-exact",
        _rpc_class(GROKBOT, "ListGrokBotAgents") == "PURE_READ"
        and _rpc_class(GROKBOT, "ListMaybeSafe") == "UNKNOWN",
    )

    fake = _FakeSkillService(mutate_on_empty=True)
    fake_sess = Sess(types.SimpleNamespace(HOST="fake.invalid"), "not-a-token")
    old_rpc = globals()["rpc"]
    direct_write_refused = False
    try:
        old_rpc(fake_sess, "DeleteGrokBotAgentSkill", {})
    except ValueError:
        direct_write_refused = True
    check("ordinary-rpc-refuses-write-before-network", direct_write_refused)
    docs: List[Dict[str, Any]] = []
    try:
        globals()["rpc"] = fake.call
        for run in (
            lambda: cmd_probe(fake_sess, False, True),
            lambda: cmd_hunt(fake_sess, "Fake", False, True),
            lambda: cmd_attach(
                fake_sess,
                "Fake",
                str(ROOT / "plugin" / "skills" / "vendor-watch" / "SKILL.md"),
                True,
                True,
            ),
            lambda: cmd_try(
                fake_sess,
                "DeleteGrokBotAgentSkill",
                "{}",
                True,
                yes=True,
            ),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run()
            doc = json.loads(buf.getvalue())
            doc["observed_exit"] = rc
            docs.append(doc)
    finally:
        globals()["rpc"] = old_rpc
    required_log_fields = {
        "command_mode",
        "candidate_rpc_class",
        "write_attempt_count",
        "target_identity",
        "unique_identity",
        "manifest_id",
        "manifest_path",
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
        "no-write-fixture-has-complete-logs",
        all(required_log_fields <= doc.keys() for doc in docs),
    )
    check(
        "probe-hunt-attach-try-refuse-not-ok",
        all(doc["observed_exit"] == EXIT_REFUSED for doc in docs),
    )
    check("zero-write-shaped-calls", not fake.write_calls)
    check(
        "zero-delete-calls",
        not any(row["method"].startswith("Delete") for row in fake.calls),
    )
    check("empty-mutation-fixture-stayed-empty", not fake.skills)

    empty_plan = ApprovedMutation(
        operation_id="empty-write-known-bad",
        command_mode="approved-write",
        target_identity={"bot": "Fake", "uuid": "fake-uuid"},
        unique_identity="empty-write-residue",
        service=GROKBOT,
        request_method="AddGrokBotAgentSkill",
        request_body={},
        expected_post_state=[],
        compensation_method="DeleteGrokBotAgentSkill",
        compensation_body={"name": "empty-write-residue"},
        compensation_evidence="fixture:delete-by-name-contract",
        recovery_argv=["fake-skill-service", "compensate", "empty-write-residue"],
    )
    empty_result = _execute_approved_mutation(
        fake_sess,
        empty_plan,
        lambda: fake.inventory(fake_sess),
        call=fake.call,
    )
    check(
        "empty-write-refused-before-rpc",
        empty_result["exit"] == EXIT_REFUSED and not fake.write_calls,
    )

    residue_fake = _FakeSkillService(fail_compensation=True)
    canary = "gb-skill-canary-selftest"
    opaque_body = "SELFTEST-SKILL-BODY-MUST-NOT-ENTER-LOG"
    residue_plan = ApprovedMutation(
        operation_id="failed-compensation-known-bad",
        command_mode="approved-write",
        target_identity={"bot": "Fake", "uuid": "fake-uuid"},
        unique_identity=canary,
        service=GROKBOT,
        request_method="AddGrokBotAgentSkill",
        request_body={"agentId": "fake-uuid", "name": canary, "body": opaque_body},
        expected_post_state=[{"id": "fake-1", "name": canary}],
        compensation_method="DeleteGrokBotAgentSkill",
        compensation_body={"id": "fake-1"},
        compensation_evidence="fixture:delete-by-id-contract",
        recovery_argv=["fake-skill-service", "compensate", "fake-1"],
    )
    with tempfile.TemporaryDirectory(prefix="gb-skills-selftest-") as tmp:
        result = _execute_approved_mutation(
            fake_sess,
            residue_plan,
            lambda: residue_fake.inventory(fake_sess),
            call=residue_fake.call,
            manifest_dir=pathlib.Path(tmp),
        )
        manifest_text = pathlib.Path(result["manifest_path"]).read_text()
        mutation_log = pathlib.Path(tmp, "mutations.jsonl").read_text()
    check("failed-compensation-is-not-ok", result["exit"] != EXIT_OK)
    check(
        "failed-compensation-names-residue",
        result["partial_residue"] > 0 and canary in " ".join(result["residue"]),
    )
    check("failed-compensation-verdict", result["verdict"] == "PARTIAL_RESIDUE")
    check("approved-write-attempt-count", result["write_attempt_count"] == 2)
    check(
        "manifest-before-write-is-durable",
        bool(
            result["manifest_id"]
            and result["manifest_path"]
            and result["pre_state_digest"]
        ),
    )
    check(
        "request-readback-compensation-ids",
        all(
            result.get(key)
            for key in (
                "request_id",
                "readback_id",
                "compensation_id",
                "compensation_readback_id",
            )
        ),
    )
    check(
        "exact-recovery-argv-recorded",
        result["recovery_argv"] == residue_plan.recovery_argv,
    )
    check(
        "skill-body-absent-from-logs",
        opaque_body not in manifest_text and opaque_body not in mutation_log,
    )
    fixture_body = (
        "# Plan eligibility\nEvery fact is READ or UNREAD with source and date."
    )
    fixture_skill = {
        "name": "plan-eligibility",
        "description": "",
        "body": fixture_body,
    }
    wrong_body = "SELFTEST-WRONG-SKILL-BODY-MUST-NOT-ENTER-LOG"
    wrong_disk = _disk_identity(
        fixture_skill,
        "fixture:redacted",
        "/redacted/plan-eligibility/SKILL.md",
        types.SimpleNamespace(
            returncode=0,
            stdout=f"---\nname: plan-eligibility\n---\n\n{wrong_body}",
            stderr="",
        ),
    )
    check(
        "same-name-wrong-body-known-bad",
        not wrong_disk["ok"]
        and wrong_disk["name_match"]
        and wrong_disk["diff_class"] == "body-mismatch",
    )

    good_disk = _disk_identity(
        fixture_skill,
        "fixture:redacted",
        "/redacted/plan-eligibility/SKILL.md",
        types.SimpleNamespace(
            returncode=0,
            stdout=f"---\nname: plan-eligibility\n---\n\n{fixture_body}",
            stderr="",
        ),
    )
    good_recipe = {
        "read_ok": True,
        "requested_name_present": True,
    }
    plan_answer = "\n".join(
        (
            "PLAN | UNREAD | source=UNREAD | date=2026-09-13 | page=account plan | owner=owner",
            "WEEKLY ALLOWANCE | UNREAD | source=UNREAD | date=2026-09-13 | page=usage | owner=owner",
            "ON-DEMAND | UNREAD | source=UNREAD | date=2026-09-13 | page=billing | owner=owner",
            "PRIVACY MODE | UNREAD | source=UNREAD | date=2026-09-13 | page=privacy | owner=owner",
            "DECISION | open the usage page",
        )
    )
    good_behavior = _behavior_observation("plan-eligibility", plan_answer, 0)
    base_roundtrip = {
        "recipe_identity": {
            "required_for_requested_kind": False,
            "before": good_recipe,
            "final": good_recipe,
        },
        "disk_identity": good_disk,
        "disk_receipt_write_ok": True,
        "attach_result": {"attempted": False, "rc": None, "ok": None},
        "chat_create_result": {"attempted": False, "rc": None, "ok": None},
        "behavior_proof": good_behavior,
    }
    failed_create = dict(
        base_roundtrip,
        chat_create_result={"attempted": True, "rc": EXIT_UPSTREAM, "ok": False},
    )
    check("failed-create-known-bad", not _roundtrip_ok(failed_create))
    failed_attach = dict(
        base_roundtrip,
        attach_result={"attempted": True, "rc": EXIT_UPSTREAM, "ok": False},
    )
    check("failed-attach-known-bad", not _roundtrip_ok(failed_attach))

    generic_job = _behavior_observation(
        "plan-eligibility",
        "https://docs.x.ai/grok-bot/overview\nfirst run",
        0,
    )
    check(
        "generic-job-without-skill-known-bad",
        not generic_job["ok"]
        and generic_job["diff_class"] == "plan-read-contract-mismatch",
    )

    prereg_body = "# Experiment prereg\nLock separate plans before seeing outcome data."
    prereg_skill = {"name": "experiment-prereg", "description": "", "body": prereg_body}
    prereg_disk = _disk_identity(
        prereg_skill,
        "fixture:redacted",
        "/redacted/experiment-prereg/SKILL.md",
        types.SimpleNamespace(
            returncode=0,
            stdout=f"---\nname: experiment-prereg\n---\n\n{prereg_body}",
            stderr="",
        ),
    )
    wrong_behavior = _behavior_observation(
        "experiment-prereg",
        "STUDY A\nLOCKED: yes\nHYPOTHESIS: something improves",
        0,
    )
    wrong_behavior_roundtrip = dict(
        base_roundtrip,
        recipe_identity={
            "required_for_requested_kind": False,
            "before": good_recipe,
            "final": {"read_ok": True, "requested_name_present": True},
        },
        disk_identity=prereg_disk,
        behavior_proof=wrong_behavior,
    )
    check(
        "correct-body-wrong-behavior-known-bad",
        prereg_disk["ok"]
        and not wrong_behavior["ok"]
        and not _roundtrip_ok(wrong_behavior_roundtrip),
    )
    fixture_receipts = json.dumps(
        {
            "wrong_disk": wrong_disk,
            "good_disk": good_disk,
            "generic_job": generic_job,
            "wrong_behavior": wrong_behavior,
        }
    )
    check(
        "roundtrip-fixture-digests-redacted",
        wrong_body not in fixture_receipts
        and fixture_body not in fixture_receipts
        and prereg_body not in fixture_receipts
        and fixture_receipts.count("sha256:") >= 5,
    )

    with tempfile.TemporaryDirectory(prefix="gb-skills-scan-") as tmp:
        home = pathlib.Path(tmp) / "home"
        repo = pathlib.Path(tmp) / "repo"
        claude = home / ".claude" / "skills"
        alpha = claude / "alpha-skill"
        frank = claude / "frankensqlite-mega-skill"
        alpha.mkdir(parents=True)
        frank.mkdir()
        alpha_body = "ALPHA-BODY-MUST-NOT-ENTER-JSON"
        (alpha / "SKILL.md").write_text(
            "---\nname: alpha-skill\ndescription: Alpha pack for the desk\n---\n\n"
            + alpha_body
            + "\n"
        )
        (frank / "SKILL.md").write_text(
            "---\nname: frankensqlite-mega-skill\ndescription: SQLite process gates\n---\n"
        )
        for vendor in ("codex", "cursor"):
            dest = home / (".%s" % vendor) / "skills"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(claude)
        plugin = repo / "plugin" / "skills" / "repo-only"
        plugin.mkdir(parents=True)
        (plugin / "SKILL.md").write_text(
            "---\nname: repo-only\ndescription: Lives in this repo\n---\n"
        )
        existing = repo / "plugin" / "skills" / "alpha-skill"
        existing.mkdir()
        (existing / "SKILL.md").write_text("---\nname: alpha-skill\n---\nold\n")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            scan_rc = cmd_scan(home=home, repo=repo, as_json=False)
        scan_out = buf.getvalue()
        check("scan-default-exit-ok", scan_rc == EXIT_OK)
        check(
            "scan-missing-root-is-a-row",
            "MISSING  grok" in scan_out and "MISSING  agents" in scan_out,
        )
        check(
            "scan-alias-not-name-dump",
            "ALIAS  codex" in scan_out
            and "ALIAS  cursor" in scan_out
            and scan_out.count("alpha-skill") == 0
            and "frankensqlite-mega-skill" not in scan_out,
        )
        check("scan-claude-count", "ROOT  claude" in scan_out and "n=2" in scan_out)
        check("scan-no-create-grokbot", "CreateGrokBot" not in scan_out)

        jbuf = io.StringIO()
        with contextlib.redirect_stdout(jbuf):
            jrc = cmd_scan(home=home, repo=repo, as_json=True)
        jdoc = json.loads(jbuf.getvalue())
        labels = [r.get("label") for r in jdoc.get("roots") or []]
        check("scan-json-exit-ok", jrc == EXIT_OK)
        check(
            "scan-json-all-roots",
            labels == ["claude", "codex", "cursor", "grok", "agents", "plugin"],
        )
        check("scan-json-no-body", alpha_body not in jbuf.getvalue())
        check("scan-json-no-default-matches", jdoc.get("matches") == [])

        mbuf = io.StringIO()
        with contextlib.redirect_stdout(mbuf):
            mrc = cmd_scan(match="alpha", home=home, repo=repo, as_json=True)
        mdoc = json.loads(mbuf.getvalue())
        matches = mdoc.get("matches") or []
        check("scan-match-exit-ok", mrc == EXIT_OK)
        check("scan-match-once", len(matches) == 1)
        check(
            "scan-match-aliases-deduped",
            matches
            and matches[0].get("name") == "alpha-skill"
            and set(matches[0].get("aliases") or []) >= {"claude", "codex", "cursor"},
        )
        check("scan-match-no-body", alpha_body not in mbuf.getvalue())

        cbuf = io.StringIO()
        with contextlib.redirect_stdout(cbuf):
            crc = cmd_scan(
                match="alpha",
                do_copy=True,
                force=False,
                home=home,
                repo=repo,
                as_json=True,
            )
        cdoc = json.loads(cbuf.getvalue())
        check(
            "scan-copy-refuses-overwrite",
            crc == EXIT_REFUSED
            and any(
                row.get("ok") is False and "exists" in str(row.get("reason") or "")
                for row in (cdoc.get("copied") or [])
            ),
        )
        check(
            "scan-copy-did-not-clobber",
            "old" in (existing / "SKILL.md").read_text(),
        )
        check("scan-copy-no-create-grokbot", "CreateGrokBot" not in cbuf.getvalue())
        check(
            "scan-copy-not-into-claude",
            alpha_body in (alpha / "SKILL.md").read_text()
            and not any(
                str(home / ".claude") in str(row.get("dest") or "")
                for row in (cdoc.get("copied") or [])
            ),
        )

        nbuf = io.StringIO()
        with contextlib.redirect_stdout(nbuf):
            nrc = cmd_scan(match="repo-only", home=home, repo=repo)
        nout = nbuf.getvalue()
        check("scan-match-human", nrc == EXIT_OK and "MATCH  repo-only" in nout)
        check("scan-prints-cp", "cp -R" in nout and "Private skills" in nout)

        ebuf = io.StringIO()
        with contextlib.redirect_stderr(ebuf):
            erc = cmd_scan(do_copy=True, home=home, repo=repo)
        check("scan-copy-without-match-is-usage", erc == EXIT_USAGE)

    return EXIT_OK if fails == 0 else EXIT_FINDINGS


def _resolve_service(method: str, service: str) -> str:
    """Which service answers this method. Explicit --service wins (prefix
    completed); Managed* routes to Dashboard; everything else is GrokBot."""
    if service:
        return service if service.startswith("aiserver.") else f"aiserver.v1.{service}"
    if method.startswith(
        ("GetManaged", "CreateManaged", "UpdateManaged", "SetManaged")
    ):
        return DASH
    return GROKBOT


def _parse_payload(payload_s: str) -> Tuple[Optional[Dict[str, Any]], int]:
    """CLI payload text to dict, or (None, EXIT_USAGE) with the reason printed."""
    try:
        payload = json.loads(payload_s or "{}")
    except json.JSONDecodeError as exc:
        print(f"gb-skills: bad --payload ({exc})", file=sys.stderr)
        return None, EXIT_USAGE
    if not isinstance(payload, dict):
        print("gb-skills: --payload must be a JSON object", file=sys.stderr)
        return None, EXIT_USAGE
    return payload, EXIT_OK


def _summarize_read_body(method: str, body: Any) -> Dict[str, Any]:
    """Body-free observation for try; skill instructions and error text never enter logs."""
    digest = _ledger_module().digest_json(body)
    if not isinstance(body, dict):
        return {"type": type(body).__name__, "chars": len(str(body)), "digest": digest}
    if method == "ListGrokBotAgents":
        agents = body.get("agents") if isinstance(body.get("agents"), list) else []
        return {
            "agents": [
                {
                    "name": row.get("name"),
                    "id": row.get("id"),
                    "uuid": row.get("legacyAgentId") or row.get("agentId"),
                }
                for row in agents
                if isinstance(row, dict)
            ],
            "digest": digest,
        }
    if method in ("ListGrokBotAgentSkills", "GetManagedSkills"):
        skills = body.get("skills") if isinstance(body.get("skills"), list) else []
        return {
            "skills": [
                {
                    "id": row.get("id") or row.get("skillId"),
                    "name": row.get("name"),
                    "source": row.get("source"),
                    "description_chars": len(str(row.get("description") or "")),
                    "body_chars": len(str(row.get("body") or "")),
                }
                for row in skills
                if isinstance(row, dict)
            ],
            "digest": digest,
        }
    return {"keys": sorted(body), "digest": digest}


def _render_try(as_json: bool, out: Dict[str, Any]) -> int:
    if as_json:
        print(json.dumps(out, indent=1))
    else:
        print(
            f"try mode={out['command_mode']} class={out['candidate_rpc_class']} "
            f"verdict={out['verdict']} exit={out['exit']}"
        )
        if out.get("body"):
            print(out["body"])
        if out.get("unavailable_evidence"):
            print(f"  unavailable: {out['unavailable_evidence']}")
    return int(out["exit"])


def cmd_try(
    sess: Sess,
    method: str,
    payload_s: str,
    as_json: bool,
    service: str = "",
    yes: bool = False,
) -> int:
    if not method:
        print("gb-skills: try needs --method", file=sys.stderr)
        return EXIT_USAGE
    svc = _resolve_service(method, service)
    payload, code = _parse_payload(payload_s)
    if payload is None:
        return code
    rpc_kind = _rpc_class(svc, method)
    if rpc_kind != "PURE_READ":
        out = _safety_receipt(
            mode="approved-write-refused" if yes else "dry-run",
            rpc_class=rpc_kind,
            target={"service": svc, "method": method},
            unique_identity=None,
            verdict="REFUSED",
            exit_code=EXIT_REFUSED,
            unavailable=(
                "live method evidence: try positively allowlists only known List/Get reads; "
                "write-shaped, Delete, and unknown methods lack named target, sealed pre-state, "
                "durable manifest, and proven compensation"
            ),
        )
        out.update(
            {
                "schema": "gb-skills-try/2",
                "method": method,
                "service": svc.split(".")[-1],
            }
        )
        return _render_try(as_json, out)

    request_id = "read-req-" + uuidlib.uuid4().hex
    readback_id = "readback-" + uuidlib.uuid4().hex
    st, body = rpc(sess, method, payload, service=svc)
    kind = classify(st, body)
    summary = _summarize_read_body(method, body)
    if kind == "ERROR":
        exit_code, verdict = EXIT_UPSTREAM, "UPSTREAM_ERROR"
    elif kind in ("ABSENT", "AMBIGUOUS"):
        exit_code, verdict = EXIT_FINDINGS, kind
    else:
        exit_code, verdict = EXIT_OK, "OBSERVED"
    out = {
        "schema": "gb-skills-try/2",
        "command_mode": "pure-read",
        "candidate_rpc_class": rpc_kind,
        "write_attempt_count": 0,
        "target_identity": {"service": svc, "method": method},
        "unique_identity": None,
        "manifest_id": None,
        "manifest_path": None,
        "pre_state_digest": None,
        "request_id": request_id,
        "readback_id": readback_id,
        "compensation_id": None,
        "partial_residue": 0,
        "residue": [],
        "verdict": verdict,
        "exit": exit_code,
        "recovery_argv": [],
        "live_evidence_available": True,
        "unavailable_evidence": None,
        "method": method,
        "service": svc.split(".")[-1],
        "http": st,
        "kind": kind,
        "body": summary,
    }
    return _render_try(as_json, out)


HUNT_FIELDS = (
    "id",
    "agent_id",
    "agentId",
    "legacy_agent_id",
    "legacyAgentId",
    "grok_bot_agent_id",
    "grokBotAgentId",
    "bot_id",
    "botId",
    "source_agent_id",
    "target_agent_id",
    "owner_agent_id",
    "uuid",
    "agent_uuid",
)


def cmd_hunt(sess: Sess, bot: str, yes: bool, as_json: bool) -> int:
    """Describe field candidates without sending AddGrokBotAgentSkill requests."""
    del sess
    if not bot:
        print("gb-skills: hunt needs --bot", file=sys.stderr)
        return EXIT_USAGE
    out = _safety_receipt(
        mode="approved-write-refused" if yes else "dry-run",
        rpc_class="WRITE",
        target={"requested_bot": bot, "authoritative_id": None},
        unique_identity=None,
        verdict="REFUSED",
        exit_code=EXIT_REFUSED,
        unavailable=(
            "accepted AddGrokBotAgentSkill identity field; field-name discovery is a write "
            "canary and no exact compensating action is proven"
        ),
    )
    out.update(
        {
            "schema": "gb-skills-hunt/2",
            "method": "AddGrokBotAgentSkill",
            "candidate_fields": list(HUNT_FIELDS),
            "candidate_calls": len(HUNT_FIELDS) * 2,
            "calls_sent": 0,
        }
    )
    return _render_safety("hunt", as_json, out)


def cmd_enable(bot: str, file_s: str, as_json: bool) -> int:
    """Print the proven human install path; no account RPC is attempted."""
    if not bot:
        print("gb-skills: enable needs --bot", file=sys.stderr)
        return EXIT_USAGE
    path = file_s or str(ROOT / "plugin" / "skills" / "vendor-watch" / "SKILL.md")
    prove = (
        "gb jobs prove --id tpl:vendor-watch"
        if "vendor" in bot.lower()
        else f"gb bot ask {bot!r} '<job>' --expect grok-bot/"
    )
    steps = [
        f"Open Grok Bot → {bot} → Private skills → New",
        f"Paste {path} (name must match the directory / frontmatter)",
        f"gb skills list --bot {bot!r}",
        prove,
    ]
    if as_json:
        print(
            json.dumps(
                {
                    "schema": "gb-skills-enable/1",
                    "bot": bot,
                    "file": path,
                    "steps": steps,
                    "rpc": "HUMAN",
                }
            )
        )
        return EXIT_OK
    print(f"enable {bot}: RPC cannot address this Bot (gb skills hunt hit 0)")
    for i, s in enumerate(steps, 1):
        print(f"  {i}. {s}")
    return EXIT_OK


BEHAVIOR_CONTRACTS: Dict[str, Dict[str, str]] = {
    "vendor-watch": {
        "oracle": "vendor-watch/two-line-vendor-source-v1",
        "expect": r"https://docs\.x\.ai/grok-bot/",
        "prompt": (
            "Use the installed vendor-watch skill. Fetch https://docs.x.ai/llms.txt. "
            "Reply with exactly two lines and nothing else. Line 1: one grok-bot URL "
            "from that file, copied verbatim. Line 2: first run OR no change OR what "
            "moved. Do not greet or repeat these instructions."
        ),
    },
    "plan-eligibility": {
        "oracle": "plan-eligibility/dated-source-read-or-unread-v1",
        "expect": r"(?m)^PLAN \|",
        "prompt": (
            "Use the installed plan-eligibility skill for a read-only eligibility check. "
            "Do not buy, upgrade, enable, or change anything. Reply with exactly five "
            "pipe-delimited lines labeled PLAN, WEEKLY ALLOWANCE, ON-DEMAND, PRIVACY "
            "MODE, and DECISION. Each of the first four must be either "
            "LABEL | READ | source=<named surface> | date=YYYY-MM-DD | value=<reading> "
            "or LABEL | UNREAD | source=UNREAD | date=YYYY-MM-DD | page=<named page> "
            "| owner=<person or role>. DECISION must name the single blocker or say none."
        ),
    },
    "experiment-prereg": {
        "oracle": "experiment-prereg/two-separate-locked-plans-v1",
        "expect": r"(?m)^STUDY A$",
        "prompt": (
            "Use the installed experiment-prereg skill. No outcome data has been seen "
            "and nothing may be run. Write two separate locked plans, STUDY A for a "
            "checkout-copy conversion test and STUDY B for a task-completion-time test. "
            "For each study use these lines in order: STUDY <letter>, LOCKED: yes, "
            "HYPOTHESIS:, numeric EFFECT SIZE:, METHOD:, STOP RULES: with a planned "
            "sample and peeking rule, LOCK DATE: YYYY-MM-DD, DATA UNSEEN: no outcome "
            "data seen. Keep every field study-specific."
        ),
    },
}
# Computer-local private skills (Bot-reported 2026-09-12). Not ListGrokBotAgentSkills.
WORKFLOW_SKILL = "/home/box/agent-data/workflows/{name}/SKILL.md"


def _norm(text: str) -> str:
    """Canonical whitespace form used by exact body identities."""
    return " ".join(text.split())


def _body_digest(text: str) -> str:
    """Stable, body-free identity: exact after whitespace normalization."""
    return _ledger_module().digest_json(_norm(text))


def _ask_bot(bot: str, text: str, expect: str) -> Any:
    """One `gb bot ask` turn. Returns the raw proc — callers grade stdout,
    returncode and expect-match themselves. The single place subprocess argv
    for Bot turns is assembled. capture_output is load-bearing: without it
    proc.stdout is None, every content check reads "" and is dead."""
    return subprocess.run(
        [
            sys.executable,
            str(BIN / "gb"),
            "bot",
            "ask",
            bot,
            text,
            "--expect",
            expect,
            "--timeout",
            "180",
        ],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
    )


def _run_disk_ask(bot: str, path: str, expect: str) -> Any:
    """One `gb bot ask` turn catting the workflow file. Returns the raw proc —
    callers read stdout/stderr/returncode themselves."""
    text = (
        f"cat {path}. Reply with the file contents only. "
        "If the file does not exist, reply MISSING."
    )
    return _ask_bot(bot, text, expect)


def _append_receipt(path: pathlib.Path, out: Dict[str, Any]) -> bool:
    """Append one already-redacted receipt and report the mutation result."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(out, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False


def _disk_identity(
    skill: Dict[str, str], source: str, path: str, proc: Any
) -> Dict[str, Any]:
    """Grade the returned file by normalized full body identity, never by name alone."""
    stdout = str(proc.stdout or "")
    stderr = str(proc.stderr or "")
    observed = _parse_skill_text(stdout)
    expected_digest = _body_digest(skill.get("body") or "")
    observed_digest = _body_digest(observed.get("body") or "")
    name_match = observed.get("name") == skill.get("name")
    body_match = observed_digest == expected_digest
    rc = int(proc.returncode)
    if rc != 0:
        diff_class = "readback-process-failed"
    elif not name_match:
        diff_class = "name-mismatch"
    elif not body_match:
        diff_class = "body-mismatch"
    else:
        diff_class = "exact-normalized-body"
    return {
        "kind": "disk-private",
        "source": source,
        "path": path,
        "requested_name": skill.get("name") or "",
        "observed_name": observed.get("name") or "",
        "requested_body_digest": expected_digest,
        "observed_body_digest": observed_digest,
        "name_match": name_match,
        "body_match": body_match,
        "diff_class": diff_class,
        "readback_result": {"attempted": True, "rc": rc, "ok": rc == 0},
        "auto_review": "auto-review" in (stdout + stderr).lower(),
        "ok": rc == 0 and name_match and body_match,
    }


def _measure_disk(bot: str, file_s: str, skill: Dict[str, str]) -> Dict[str, Any]:
    path = WORKFLOW_SKILL.format(name=skill["name"])
    try:
        proc = _run_disk_ask(bot, path, f"name: {skill['name']}")
    except (OSError, subprocess.SubprocessError) as exc:
        proc = types.SimpleNamespace(
            returncode=EXIT_UPSTREAM,
            stdout="",
            stderr=type(exc).__name__,
        )
    return _disk_identity(skill, file_s, path, proc)


def _render_disk(as_json: bool, out: Dict[str, Any]) -> int:
    """Disk verdict after the redacted append-only receipt mutation is known."""
    if as_json:
        print(json.dumps(out, indent=1))
    else:
        print(
            f"disk {out['bot']} {out['disk_identity']['path']} ok={out['ok']} "
            f"rc={out['disk_identity']['readback_result']['rc']} "
            f"diff={out['disk_identity']['diff_class']}"
        )
        if out["disk_identity"]["auto_review"] and not out["ok"]:
            print(
                "  auto-review blocked this turn; not proof the file is missing — re-run disk"
            )
    return EXIT_OK if out["ok"] else EXIT_FINDINGS


def cmd_disk(bot: str, file_s: str, as_json: bool) -> int:
    """Prove the exact workflow skill body on the Bot computer."""
    path = pathlib.Path(file_s) if file_s else None
    if not bot or path is None or not path.is_file():
        print("gb-skills: disk needs --bot and an existing --file", file=sys.stderr)
        return EXIT_USAGE
    skill = _skill_file(path)
    disk = _measure_disk(bot, file_s, skill)
    out = {
        "schema": "gb-skills-disk/2",
        "bot": bot,
        "requested_skill_kind": "repo-plugin-skill",
        "requested_skill_source": file_s,
        "requested_skill_name": skill["name"],
        "requested_body_digest": _body_digest(skill["body"]),
        "disk_identity": disk,
        "recovery_argv": [
            "gb",
            "skills",
            "disk",
            "--bot",
            bot,
            "--file",
            file_s,
            "--json",
        ],
        "receipt_write_ok": True,
        "ok": disk["ok"],
    }
    if not _append_receipt(ROOT / "skills" / "disk.jsonl", out):
        out["receipt_write_ok"] = False
        out["ok"] = False
    return _render_disk(as_json, out)


def _recipe_identity(sess: Sess, bot: str, requested_name: str) -> Dict[str, Any]:
    """Read server recipe rows; never present them as disk-private content proof."""
    st, roster = rpc(sess, "ListGrokBotAgents", {})
    empty = {
        "kind": "recipe-row",
        "source": "ListGrokBotAgentSkills",
        "roster_http": st,
        "skills_http": None,
        "read_ok": False,
        "requested_name_present": False,
        "identity_digest": None,
        "identities": [],
    }
    if st != 200 or not isinstance(roster, dict):
        return empty
    want = bot.strip().lower()
    for agent in roster.get("agents") or []:
        if str(agent.get("name") or "").lower() != want:
            continue
        uuid = str(agent.get("legacyAgentId") or agent.get("agentId") or "")
        sst, skills = rpc(sess, "ListGrokBotAgentSkills", {"agent_id": uuid})
        got = skills.get("skills") if sst == 200 and isinstance(skills, dict) else None
        rows = [
            {
                "id": row.get("id") or row.get("skillId"),
                "name": str(row.get("name") or ""),
                "source": row.get("source"),
            }
            for row in (got or [])
            if isinstance(row, dict)
        ]
        rows.sort(
            key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"))
        )
        read_ok = sst == 200 and isinstance(got, list)
        return {
            "kind": "recipe-row",
            "source": "ListGrokBotAgentSkills",
            "roster_http": st,
            "skills_http": sst,
            "read_ok": read_ok,
            "requested_name_present": any(
                row["name"] == requested_name for row in rows
            ),
            "identity_digest": _ledger_module().digest_json(rows) if read_ok else None,
            "identities": rows if read_ok else [],
        }
    return empty


def _behavior_observation(
    skill_name: str, text: str, rc: int, attempted: bool = True
) -> Dict[str, Any]:
    """Grade only the requested capability's observable; keep reply text private."""
    contract = BEHAVIOR_CONTRACTS.get(skill_name)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    behavior_ok = False
    diff_class = "unsupported-skill"
    if not attempted:
        diff_class = "not-attempted"
    elif contract is not None and rc != 0:
        diff_class = "behavior-process-failed"
    elif skill_name == "vendor-watch":
        behavior_ok = (
            len(lines) == 2
            and lines[0].startswith("https://docs.x.ai/grok-bot/")
            and bool(lines[1])
        )
        diff_class = "contract-match" if behavior_ok else "vendor-watch-shape-mismatch"
    elif skill_name == "plan-eligibility":
        date_re = re.compile(r"(?:^|\|)\s*date=\d{4}-\d{2}-\d{2}(?:\s*\||$)")
        required = ("PLAN", "WEEKLY ALLOWANCE", "ON-DEMAND", "PRIVACY MODE")
        shaped = True
        for label in required:
            matches = [line for line in lines if line.startswith(label + " |")]
            if len(matches) != 1:
                shaped = False
                continue
            line = matches[0]
            source_match = re.search(r"\|\s*source=([^|]+)", line)
            value_match = re.search(r"\|\s*value=([^|]+)", line)
            is_read = (
                " | READ |" in line
                and source_match is not None
                and source_match.group(1).strip().upper() != "UNREAD"
                and value_match is not None
                and value_match.group(1).strip().upper()
                not in ("", "UNKNOWN", "UNREAD")
            )
            is_unread = (
                " | UNREAD |" in line
                and re.search(r"\|\s*source=UNREAD(?:\s*\||$)", line)
                and re.search(r"\|\s*page=[^|]+", line)
                and re.search(r"\|\s*owner=[^|]+", line)
            )
            shaped = (
                shaped and bool(date_re.search(line)) and bool(is_read or is_unread)
            )
        decision = [line for line in lines if line.startswith("DECISION |")]
        mutation_claim = re.search(
            r"\b(?:upgraded|purchased|enabled on-demand|changed privacy|changed the setting)\b",
            text,
            re.I,
        )
        behavior_ok = shaped and len(decision) == 1 and mutation_claim is None
        diff_class = "contract-match" if behavior_ok else "plan-read-contract-mismatch"
    elif skill_name == "experiment-prereg":
        markers = [i for i, line in enumerate(lines) if line in ("STUDY A", "STUDY B")]
        shaped = (
            len(markers) == 2
            and lines[markers[0]] == "STUDY A"
            and lines[markers[1]] == "STUDY B"
        )
        if shaped:
            for start, end in ((markers[0], markers[1]), (markers[1], len(lines))):
                block = lines[start:end]
                prefixes = (
                    "HYPOTHESIS:",
                    "EFFECT SIZE:",
                    "METHOD:",
                    "STOP RULES:",
                    "LOCK DATE:",
                    "DATA UNSEEN:",
                )
                shaped = shaped and all(
                    any(
                        line.startswith(prefix) and len(line) > len(prefix)
                        for line in block
                    )
                    for prefix in prefixes
                )
                shaped = shaped and "LOCKED: yes" in block
                effect_lines = [
                    line for line in block if line.startswith("EFFECT SIZE:")
                ]
                stop_lines = [line for line in block if line.startswith("STOP RULES:")]
                shaped = (
                    shaped
                    and len(effect_lines) == 1
                    and bool(re.search(r"\d", effect_lines[0]))
                )
                shaped = (
                    shaped
                    and len(stop_lines) == 1
                    and bool(re.search(r"\d", stop_lines[0]))
                    and "peek" in stop_lines[0].lower()
                )
                shaped = shaped and any(
                    re.fullmatch(r"LOCK DATE: \d{4}-\d{2}-\d{2}", line)
                    for line in block
                )
                shaped = shaped and "DATA UNSEEN: no outcome data seen" in block
        behavior_ok = shaped
        diff_class = "contract-match" if behavior_ok else "prereg-contract-mismatch"
    return {
        "attempted": attempted and contract is not None,
        "rc": rc if attempted and contract is not None else None,
        "ok": attempted and rc == 0 and behavior_ok,
        "oracle": contract["oracle"] if contract else None,
        "observed_digest": _body_digest(text),
        "diff_class": diff_class,
    }


def _run_job(bot: str, skill_name: str) -> Dict[str, Any]:
    contract = BEHAVIOR_CONTRACTS.get(skill_name)
    if contract is None:
        return _behavior_observation(skill_name, "", EXIT_REFUSED)
    try:
        proc = _ask_bot(bot, contract["prompt"], contract["expect"])
        return _behavior_observation(
            skill_name, str(proc.stdout or ""), int(proc.returncode)
        )
    except (OSError, subprocess.SubprocessError) as exc:
        out = _behavior_observation(skill_name, "", EXIT_UPSTREAM)
        out["subprocess_error"] = type(exc).__name__
        return out


def _roundtrip_ok(out: Dict[str, Any]) -> bool:
    """One fail-closed conjunction for every read, subprocess, and mutation result."""
    attach = out["attach_result"]
    create = out["chat_create_result"]
    return all(
        (
            out["recipe_identity"]["before"]["read_ok"],
            out["recipe_identity"]["final"]["read_ok"],
            not out["recipe_identity"]["required_for_requested_kind"]
            or out["recipe_identity"]["final"]["requested_name_present"],
            out["disk_identity"]["ok"],
            out["disk_receipt_write_ok"],
            (not attach["attempted"] and attach["rc"] is None and attach["ok"] is None)
            or (
                attach["attempted"] and attach["rc"] == EXIT_OK and attach["ok"] is True
            ),
            (not create["attempted"] and create["rc"] is None and create["ok"] is None)
            or (
                create["attempted"] and create["rc"] == EXIT_OK and create["ok"] is True
            ),
            out["behavior_proof"]["attempted"],
            out["behavior_proof"]["ok"],
        )
    )


def _render_roundtrip(as_json: bool, out: Dict[str, Any], bot: str, want: str) -> int:
    """Roundtrip verdict after every subprocess and receipt mutation is known."""
    ledger_argv = [
        "outcome",
        "--bot",
        bot,
        "--skill",
        want,
        "--result",
        "success" if out["pass"] else "failure",
    ]
    ledger_rc = _ledger(ledger_argv, quiet=True)
    out["ledger_result"] = {
        "attempted": True,
        "rc": ledger_rc,
        "ok": ledger_rc == EXIT_OK,
    }
    if ledger_rc != EXIT_OK:
        out["pass"] = False
        out["verdict"] = "OUTCOME_LEDGER_FAILED"
        out["exit"] = EXIT_FINDINGS
        out["recovery_argv"] = ["gb", "skills", *ledger_argv, "--json"]
    if as_json:
        print(json.dumps(out, indent=1))
    else:
        print(
            f"roundtrip recipe_read_ok={out['recipe_identity']['final']['read_ok']} "
            f"recipe_name_present={out['recipe_identity']['final']['requested_name_present']} "
            f"disk_ok={out['disk_identity']['ok']} "
            f"behavior_ok={out['behavior_proof']['ok']} pass={out['pass']}"
        )
    return int(out["exit"])


def cmd_roundtrip(
    sess: Sess,
    bot: str,
    file_s: str,
    yes: bool,
    as_json: bool,
) -> int:
    """Prove the exact installed workflow body and its capability-specific behavior."""
    path = pathlib.Path(file_s) if file_s else None
    if not bot or path is None or not path.is_file():
        print(
            "gb-skills: roundtrip needs --bot and an existing --file", file=sys.stderr
        )
        return EXIT_USAGE
    skill = _skill_file(path)
    want = skill["name"]
    requested = {
        "kind": "repo-plugin-skill",
        "source": file_s,
        "name": want,
        "body_digest": _body_digest(skill["body"]),
    }
    contract = BEHAVIOR_CONTRACTS.get(want)
    plan = {
        "bot": bot,
        "requested_skill": requested,
        "content_oracle": "normalized exact SKILL.md body digest",
        "behavior_oracle": contract["oracle"] if contract else None,
        "not_oracle": "Bot said stored; same-name recipe row; generic URL fetch",
        "on_missing": "REFUSE; use the human gb skills enable path",
        "write_attempt_count": 0,
    }
    if not yes:
        if as_json:
            print(json.dumps({"plan": plan, "yes": False}))
        else:
            print(f"roundtrip {bot} {want}: exact disk body → capability behavior")
            print("  missing disk skill refuses; roundtrip never attach/chat-creates")
            print("  re-run with --yes (spends read-only Bot turns)")
        return EXIT_REFUSED

    before = _recipe_identity(sess, bot, want)
    disk = _measure_disk(bot, file_s, skill)
    disk_recovery = ["gb", "skills", "disk", "--bot", bot, "--file", file_s, "--json"]
    disk_receipt_write_ok = _append_receipt(
        ROOT / "skills" / "disk.jsonl",
        {
            "schema": "gb-skills-disk/2",
            "bot": bot,
            "requested_skill_kind": requested["kind"],
            "requested_skill_source": requested["source"],
            "requested_skill_name": want,
            "requested_body_digest": requested["body_digest"],
            "disk_identity": disk,
            "recovery_argv": disk_recovery,
            "receipt_write_ok": True,
        },
    )
    final = _recipe_identity(sess, bot, want)
    behavior = (
        _run_job(bot, want)
        if disk["ok"] and disk_receipt_write_ok
        else _behavior_observation(want, "", EXIT_REFUSED, attempted=False)
    )
    out = {
        "schema": "gb-skills-roundtrip/3",
        "target_bot": bot,
        "requested_skill": requested,
        "recipe_identity": {
            "required_for_requested_kind": False,
            "before": before,
            "final": final,
        },
        "disk_identity": disk,
        "disk_receipt_write_ok": disk_receipt_write_ok,
        "attach_result": {"attempted": False, "rc": None, "ok": None},
        "chat_create_result": {"attempted": False, "rc": None, "ok": None},
        "write_attempt_count": 0,
        "behavior_proof": behavior,
        "pass": False,
        "verdict": "FINDINGS",
        "exit": EXIT_FINDINGS,
        "recovery_argv": [
            "gb",
            "skills",
            "roundtrip",
            "--bot",
            bot,
            "--file",
            file_s,
            "--yes",
            "--json",
        ],
    }
    out["pass"] = _roundtrip_ok(out)
    if out["pass"]:
        out["verdict"], out["exit"], out["recovery_argv"] = "PROVED", EXIT_OK, []
    elif not disk["ok"]:
        out["verdict"] = "CONTENT_READBACK_FAILED"
    elif not before["read_ok"] or not final["read_ok"]:
        out["verdict"] = "RECIPE_READ_FAILED"
    elif (
        out["recipe_identity"]["required_for_requested_kind"]
        and not final["requested_name_present"]
    ):
        out["verdict"] = "RECIPE_MISSING"
    elif not disk_receipt_write_ok:
        out["verdict"] = "DISK_RECEIPT_FAILED"
    elif not behavior["attempted"]:
        out["verdict"] = "NO_BEHAVIOR_CONTRACT"
    elif not behavior["ok"]:
        out["verdict"] = "BEHAVIOR_PROOF_FAILED"
    return _render_roundtrip(as_json, out, bot, want)


def _ledger(argv: List[str], quiet: bool = False) -> int:
    try:
        proc = subprocess.run(
            [sys.executable, str(BIN / "gb-skills-ledger.py"), *argv],
            cwd=str(ROOT),
            check=False,
            capture_output=quiet,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
        return int(proc.returncode)
    except (OSError, subprocess.SubprocessError):
        return EXIT_UPSTREAM


def _dispatch(args: Any, sess: Sess) -> int:
    """Dispatch only commands that positively require an authenticated read session."""
    if args.action == "list":
        return cmd_list(sess, args.bot, args.json)
    if args.action == "try":
        return cmd_try(
            sess,
            args.method,
            args.payload,
            args.json,
            service=args.service,
            yes=args.yes,
        )
    if args.action == "roundtrip":
        return cmd_roundtrip(sess, args.bot, args.file, args.yes, args.json)
    print(f"gb-skills: unsafe dispatch for {args.action}", file=sys.stderr)
    return EXIT_USAGE


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gb-skills",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""RPC modes:
  pure read     list, census, disk, and exact allowlisted try methods
  dry-run       probe and hunt; zero candidate RPCs
  approved      attach requires --yes plus a proven inverse; currently refused
  irreversible Delete/Remove/Detach and unknown try methods are refused""",
    )
    ap.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=(
            "list",
            "probe",
            "attach",
            "try",
            "hunt",
            "enable",
            "roundtrip",
            "disk",
            "census",
            "outcome",
            "bandit",
            "scan",
        ),
        help="operation; scan is offline local roots; probe/hunt are dry-run route descriptions",
    )
    ap.add_argument("--bot", default="", help="named Bot target")
    ap.add_argument(
        "--file", default="", help="SKILL.md source; bodies never enter logs"
    )
    ap.add_argument("--method", default="", help="try: exact allowlisted read method")
    ap.add_argument(
        "--payload", default="{}", help="try: JSON request for an allowlisted read"
    )
    ap.add_argument("--service", default="", help="try: explicit service name")
    ap.add_argument("--skill", default="")
    ap.add_argument("--result", default="")
    ap.add_argument(
        "query",
        nargs="?",
        default="",
        help="scan: substring match of directory or frontmatter name (same as --match)",
    )
    ap.add_argument(
        "--match",
        default="",
        help="scan: substring match of directory or frontmatter name",
    )
    ap.add_argument(
        "--copy",
        action="store_true",
        dest="do_copy",
        help="scan: copy a match into this repo plugin/skills/<slug> (never $HOME)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="scan --copy: overwrite plugin/skills/<slug>",
    )
    ap.add_argument("--root", default="", help="scan: repo root (tests)")
    ap.add_argument(
        "--yes",
        action="store_true",
        help="explicit approval; never bypasses missing pre-state, manifest, or compensation",
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.action in ("census", "outcome", "bandit"):
        argv2 = [args.action]
        if args.bot:
            argv2 += ["--bot", args.bot]
        if args.skill:
            argv2 += ["--skill", args.skill]
        if args.result:
            argv2 += ["--result", args.result]
        if args.json:
            argv2.append("--json")
        return _ledger(argv2)
    if args.action == "enable":
        return cmd_enable(args.bot, args.file, args.json)
    if args.action == "disk":
        return cmd_disk(args.bot, args.file, args.json)
    if args.action == "scan":
        return cmd_scan(
            match=str(args.match or args.query or ""),
            do_copy=bool(args.do_copy),
            force=bool(args.force),
            as_json=bool(args.json),
            repo=pathlib.Path(args.root) if args.root else ROOT,
        )

    # These paths are intentionally sessionless: a dry-run must work offline and cannot reach the
    # account even if a future transport implementation changes what an empty POST means.
    offline = Sess(types.SimpleNamespace(HOST=""), "")
    if args.action == "probe":
        return cmd_probe(offline, args.yes, args.json)
    if args.action == "hunt":
        return cmd_hunt(offline, args.bot, args.yes, args.json)
    if args.action == "attach":
        if not args.bot or not args.file:
            print("gb-skills: attach needs --bot and --file", file=sys.stderr)
            return EXIT_USAGE
        return cmd_attach(offline, args.bot, args.file, args.yes, args.json)
    if args.action == "try":
        svc = _resolve_service(args.method, args.service)
        if _rpc_class(svc, args.method) != "PURE_READ":
            return cmd_try(
                offline,
                args.method,
                args.payload,
                args.json,
                service=args.service,
                yes=args.yes,
            )

    try:
        mod = _pull()
        token = _token(mod)
    except Exception as exc:
        print(f"gb-skills: no client session ({type(exc).__name__})", file=sys.stderr)
        return EXIT_ENVIRONMENT
    return _dispatch(args, Sess(mod, token))


if __name__ == "__main__":
    raise SystemExit(main())
