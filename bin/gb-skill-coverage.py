#!/usr/bin/env python3
"""gb-skill-coverage — every operation reachable, every skill earning.

Third coverage leg: join (gb verbs x producers x gate checks x feature-matrix
ON-but-unadopted x open HUMAN lines) against (plugin skill triggers +
operator skill triggers) in BOTH directions:

  operations with zero covering skill  -> unreachable operations, each with a
     proposal naming which skill should grow a trigger or which new skill is
     warranted, with evidence (shared keywords / explicit mentions);
  skills whose triggers match nothing   -> unwired skills, each with a
     coldness heuristic until telemetry exists.

Coldness is a PROXY, not a measurement (proxy-coldness-v1): count of
references to the skill's domain in 30 days of artifacts (findings/, gaps/,
advise/, .beads/issues.jsonl). It dies the day per-skill telemetry conforming
to skillos.skill_telemetry.v1 lands, at which point lifecycle_stage is COMPUTED
from telemetry per the skill-builder maturity formula (no-telemetry default:
candidate). Until then every skill here is candidate-stage by that formula,
and "cold" below means "zero trigger overlap AND zero proxy references".

Side A is derived, never asserted: gb verbs from `bin/gb capabilities --json`,
producers from bin/gb-*.py files with a CLI, gate checks from the 25 ids in
CHECK_ORDER, ON-but-unadopted entries from `gb-feature-matrix.py --todo`,
HUMAN lines from the red_fix/error_fix entries in gb-triage-check.py CHECKS.
Side B is read-only: SKILL.md frontmatter descriptions + trigger language from
plugin/skills/*/SKILL.md and ~/.claude/skills/grokbot-*/SKILL.md (never modified).

  bin/gb-skill-coverage.py              # human table + top-10 lists, writes skill-coverage/<stamp>.json
  bin/gb-skill-coverage.py --json       # machine-readable envelope on stdout

Exit codes (house dictionary): 0 ran clean and wrote the artifact, 2 usage,
3 environment (capabilities unreadable, skill dirs absent). Uncovered
operations and cold skills are FINDINGS carried in the artifact, never an exit
code: the derivation must stay green so the ratchet can read it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gbtypes  # noqa: E402

SCHEMA = "gb-skill-coverage/1"
GATE_TIMEOUT_S = 120
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
IDF_COVER_WEIGHT = 4.0
IDF_TOP_TOKENS = 3
IDF_NEIGHBOR_WEIGHT = 1.5
PROXY_WINDOW_DAYS = 30
PROXY_LABEL = (
    "proxy-coldness-v1: zero trigger overlap AND zero references in "
    f"{PROXY_WINDOW_DAYS}d of artifacts (findings/, gaps/, advise/, "
    ".beads/issues.jsonl). PROXY, not measurement — expires when per-skill "
    "telemetry (skillos.skill_telemetry.v1) lands; until then candidate-stage "
    "default per the skill-builder maturity formula."
)

STOPWORDS = frozenset(
    """
    the a an and or for with from that this into your you your are our all any
    when what how its per via use used using ask asked grok bot bots skill skills
    about they them their then than than there these those they will would can
    its also each every any more most such only just like get got has have had
    who whom whose which while where does did done who upon onto unto they
    no nor not very could should shall might must across between within without
    through during under over once ever still yet though although much many same
    other another either both whether us
    gb it be as by in of on at to is was were been being am do if so up down
    out off own too now here because until further again
    """.split()
)


def tokens(text: str) -> Set[str]:
    """Significant keyword tokens: lowercase alnum, len>=2, stopwords out, naive singular."""
    out: Set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", text.lower()):
        if len(raw) < 2 or raw in STOPWORDS:
            continue
        out.add(raw)
        if len(raw) > 4 and raw.endswith("s") and not raw.endswith("ss"):
            out.add(raw[:-1])
    return out


def quoted_phrases(text: str) -> List[str]:
    """Trigger phrases are quoted in house-style descriptions; harvest them."""
    found: List[str] = []
    for m in re.finditer(r"'([^']{3,80})'|\"([^\"]{3,80})\"", text):
        found.append(m.group(1) or m.group(2))
    return found


class Op:
    def __init__(self, kind: str, oid: str, title: str) -> None:
        self.kind = kind
        self.oid = oid
        self.title = title
        self.keywords: Set[str] = tokens(
            oid.replace("-", " ").replace("_", " ") + " " + title
        )

    def row(self) -> Dict[str, Any]:
        return {"kind": self.kind, "id": self.oid, "title": self.title}


class Skill:
    def __init__(
        self, scope: str, name: str, description: str, triggers: List[str]
    ) -> None:
        self.scope = scope
        self.name = name
        self.description = description
        self.triggers = triggers
        self.text = (
            name.replace("-", " ") + " " + description + " " + " ".join(triggers)
        ).lower()
        self.keywords: Set[str] = tokens(self.text)

    def row(self) -> Dict[str, Any]:
        return {"scope": self.scope, "name": self.name, "triggers": self.triggers}


def run_json(argv: List[str], root: pathlib.Path) -> Any:
    p = subprocess.run(
        argv, capture_output=True, text=True, timeout=GATE_TIMEOUT_S, cwd=str(root)
    )
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} exited {p.returncode}: {p.stderr[:300]}")
    return json.loads(p.stdout)


def collect_verbs(root: pathlib.Path) -> List[Op]:
    data = run_json([str(root / "bin" / "gb"), "capabilities", "--json"], root)
    commands = data.get("commands")
    if not isinstance(commands, dict) or not commands:
        raise RuntimeError("capabilities --json carried no commands")
    ops = [Op("verb", str(name), str(desc)) for name, desc in commands.items()]
    return sorted(ops, key=lambda o: o.oid)


def collect_producers(root: pathlib.Path) -> List[Op]:
    ops: List[Op] = []
    for path in sorted((root / "bin").glob("gb-*.py")):
        stem = path.stem
        if stem == "gb-skill-coverage":
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if (
            "argparse" not in src
            and "add_argument" not in src
            and "sys.argv" not in src
        ):
            continue  # library, not an operation (same rule as gb-dogfood)
        doc = ""
        m = re.search(r'"""(.*?)"""', src, re.S)
        if m:
            doc = m.group(1).strip().splitlines()[0][:200]
        ops.append(Op("producer", stem, doc or stem))
    return ops


def gate_red_means(root: pathlib.Path) -> Dict[str, str]:
    """RED-means column per check from GATES.md (the gate's own semantics, not asserted)."""
    out: Dict[str, str] = {}
    try:
        text = (root / "GATES.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for m in re.finditer(r"^\|\s*`(g\d+-[a-z0-9-]+)`\s*\|\s*([^|]+)", text, re.M):
        out[m.group(1)] = re.sub(r"\s+", " ", m.group(2)).strip()[:300]
    return out


def collect_gates(root: pathlib.Path) -> Tuple[List[Op], List[Op]]:
    """25 gate checks as ops; HUMAN red/error fixes as human ops (operations no skill covers yet)."""
    src = (root / "bin" / "gb-triage-check.py").read_text(
        encoding="utf-8", errors="replace"
    )
    order = re.findall(
        r'"(g\d+-[a-z0-9-]+)"', src.split("CHECK_ORDER")[1].split(")")[0]
    )
    seen: List[str] = []
    for cid in order:
        if cid not in seen:
            seen.append(cid)
    red = gate_red_means(root)
    gate_ops = [
        Op("gate", cid, "gate check " + cid.replace("-", " ") + ". " + red.get(cid, ""))
        for cid in seen
    ]
    human_ops: List[Op] = []
    for m in re.finditer(r'"(g\d+-[a-z0-9-]+)":\s*\{', src):
        cid = m.group(1)
        block = src[m.end() : m.end() + 900]
        hm = re.search(r'"(?:red_fix|error_fix)":\s*"(HUMAN:[^"]{0,300})', block)
        if hm:
            human_ops.append(Op("human", "human:" + cid, hm.group(1)))
    return gate_ops, human_ops


def collect_features(root: pathlib.Path, notes: List[str]) -> List[Op]:
    try:
        data = run_json(
            [
                sys.executable,
                str(root / "bin" / "gb-feature-matrix.py"),
                "--todo",
                "--json",
            ],
            root,
        )
    except (
        Exception
    ) as exc:  # producer missing or failing: note and continue, never fatal
        notes.append(f"feature-matrix unreadable, ON-but-unadopted leg skipped: {exc}")
        return []
    feats = data.get("features", []) if isinstance(data, dict) else []
    ops: List[Op] = []
    for f in feats:
        if not isinstance(f, dict):
            continue
        if str(f.get("state", "")).upper() != "ON":
            continue
        title = str(f.get("title", f.get("id", "?")))
        extra = " ".join(str(f.get(k, "")) for k in ("check", "note", "where"))
        ops.append(
            Op(
                "feature",
                "feature:" + str(f.get("id", "?")),
                (title + ". " + extra)[:600],
            )
        )
    return sorted(ops, key=lambda o: o.oid)


def parse_skill(path: pathlib.Path, scope: str) -> Optional[Skill]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parts = text.split("---")
    if len(parts) < 3:
        return None
    front = parts[1]
    name_m = re.search(r"^name:\s*(\S+)", front, re.M)
    desc_m = re.search(r"^description:\s*(.+?)\s*$", front, re.M)
    if not name_m or not desc_m:
        return None
    desc = desc_m.group(1).strip().strip("'\"")
    body = "---".join(parts[2:])
    triggers = quoted_phrases(desc)
    for line in body.splitlines():
        if "Use when" in line and len(line.strip()) < 400:
            triggers.append(line.strip()[:300])
    return Skill(scope, name_m.group(1).strip(), desc, triggers)


def collect_skills(root: pathlib.Path, notes: List[str]) -> List[Skill]:
    skills: List[Skill] = []
    for path in sorted((root / "plugin" / "skills").glob("*/SKILL.md")):
        sk = parse_skill(path, "plugin")
        if sk is None:
            notes.append(f"unparseable skill file: {path.relative_to(root)}")
        else:
            skills.append(sk)
    home = pathlib.Path(os.path.expanduser("~")) / ".claude" / "skills"
    for path in sorted(home.glob("grokbot-*/SKILL.md")):
        sk = parse_skill(path, "operator")  # read-only; never modified
        if sk is not None:
            skills.append(sk)
    if not skills:
        raise RuntimeError(
            "no skills parsed from plugin/ or ~/.claude/skills/grokbot-*"
        )
    return sorted(skills, key=lambda s: (s.scope, s.name))


def explicit_mention(op: Op, sk: Skill) -> Optional[str]:
    """Word-boundary mention of an unambiguous id inside skill text.

    Only full ids count (gb-discover, g12-..., gb <verb>, gb-<verb>): bare
    short stems like 'discover' or 'monitor' appear in ordinary prose and
    would match dozens of unrelated skills. The one exception is the short
    gate form gNN (g12, g24) — word-boundary g+digits never occurs by
    accident and IS how skills name gate checks in trigger language.
    """
    oid = op.oid
    cands: List[str] = []
    if oid.startswith("human:"):
        check = oid[len("human:") :]
        cands += [check, check.split("-")[0]]
    elif oid.startswith("feature:"):
        cands.append(oid[len("feature:") :])
    elif oid.startswith("gb-"):
        cands.append(oid)
    elif op.kind == "verb":
        cands += ["gb " + oid, "gb-" + oid]
    elif op.kind == "gate":
        cands += [oid, oid.split("-")[0]]
    for cand in cands:
        if len(cand) < 3:
            continue
        if cand.startswith("g") and cand[1:].isdigit():
            pass  # gNN: precise by construction
        elif " " not in cand and "-" not in cand and ":" not in cand:
            continue  # bare prose word: not evidence
        if re.search(
            r"(?<![a-z0-9])" + re.escape(cand.lower()) + r"(?![a-z0-9])", sk.text
        ):
            return "explicit:" + cand
    return None


def idf_weights(skills: List[Skill]) -> Dict[str, float]:
    """Rare-across-skills tokens carry the match; glue like report/review weighs ~0."""
    n = max(1, len(skills))
    df: Dict[str, int] = {}
    for sk in skills:
        for tok in sk.keywords:
            df[tok] = df.get(tok, 0) + 1
    return {tok: math.log(n / c) for tok, c in df.items()}


def shared_weight(op: Op, sk: Skill, idf: Dict[str, float]) -> Tuple[float, List[str]]:
    """IDF weight of the top-N shared tokens, rarest-first for evidence.

    Only the top-N tokens count: without the cap, six generic overlaps
    (report/review/read/...) accumulate past any threshold and every skill
    covers every operation. With the cap, coverage needs rare shared
    vocabulary — one unique token (w=4.5), two tokens each in <=8 skills,
    or three each in <=20 — or an explicit id mention.
    """
    shared = sorted((op.keywords & sk.keywords), key=lambda t: (-idf.get(t, 0.0), t))
    top = shared[:IDF_TOP_TOKENS]
    return sum(idf.get(t, 0.0) for t in top), shared


def match(op: Op, sk: Skill, idf: Dict[str, float]) -> Tuple[bool, List[str]]:
    ev = explicit_mention(op, sk)
    if ev is not None:
        return True, [ev]
    weight, shared = shared_weight(op, sk, idf)
    if weight >= IDF_COVER_WEIGHT:
        shown = [f"{t}:{idf.get(t, 0.0):.1f}" for t in shared[:8]]
        return True, [f"shared(w={weight:.1f}):" + ",".join(shown)]
    return False, []


def recent_artifact_texts(root: pathlib.Path) -> List[Tuple[str, str]]:
    """30d of artifact text for the coldness proxy: (label, lowercased text)."""
    cutoff = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=PROXY_WINDOW_DAYS)
    ).timestamp()
    out: List[Tuple[str, str]] = []
    cands: List[pathlib.Path] = []
    for pat in ("findings/*", "gaps/*.json", "advise/*.json"):
        cands.extend(root.glob(pat))
    bead_issues = root / ".beads" / "issues.jsonl"
    if bead_issues.exists():
        cands.append(bead_issues)
    for path in cands:
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                continue
            if path.stat().st_size > 2_000_000:
                continue
            out.append(
                (
                    str(path.relative_to(root)),
                    path.read_text(encoding="utf-8", errors="replace").lower(),
                )
            )
        except OSError:
            continue
    return out


def derive(root: pathlib.Path) -> Dict[str, Any]:
    notes: List[str] = []
    verbs = collect_verbs(root)
    producers = collect_producers(root)
    gates, humans = collect_gates(root)
    features = collect_features(root, notes)
    skills = collect_skills(root, notes)
    ops: List[Op] = verbs + producers + gates + features + humans

    idf = idf_weights(skills)
    op_cover: Dict[str, List[Dict[str, Any]]] = {}
    skill_hits: Dict[str, int] = {s.name: 0 for s in skills}
    for op in ops:
        covering: List[Dict[str, Any]] = []
        for sk in skills:
            ok, ev = match(op, sk, idf)
            if ok:
                covering.append({"skill": sk.name, "scope": sk.scope, "evidence": ev})
                skill_hits[sk.name] += 1
        op_cover[op.oid] = sorted(covering, key=lambda c: str(c["skill"]))
    # nearest-neighbor proposal for every uncovered operation (by IDF weight, not count)
    uncovered: List[Dict[str, Any]] = []
    for op in ops:
        if op_cover[op.oid]:
            continue
        best: Optional[Skill] = None
        best_w = 0.0
        best_shared: List[str] = []
        for sk in skills:
            w, shared = shared_weight(op, sk, idf)
            if w > best_w or (best is not None and w == best_w and sk.name < best.name):
                best, best_w, best_shared = sk, w, shared
        if best is not None and best_w >= IDF_NEIGHBOR_WEIGHT:
            proposal: Dict[str, Any] = {
                "action": "grow-trigger",
                "skill": best.name,
                "evidence": [f"shares(w={best_w:.1f}):" + ",".join(best_shared[:8])],
            }
        else:
            proposal = {
                "action": "new-skill-warranted",
                "skill": None,
                "evidence": [
                    f"max neighbor weight {best_w:.1f} across {len(skills)} skills"
                ],
            }
        uncovered.append({**op.row(), "covering": [], "proposal": proposal})

    proxy_texts = recent_artifact_texts(root)
    proxy_files = len(proxy_texts)
    cold: List[Dict[str, Any]] = []
    for sk in skills:
        if skill_hits[sk.name] > 0:
            continue
        refs = sum(1 for _label, text in proxy_texts if sk.name.lower() in text)
        if refs == 0:
            cold.append(
                {
                    **sk.row(),
                    "covering_ops": 0,
                    "proxy_refs_30d": 0,
                    "proxy_files_scanned": proxy_files,
                    "coldness": PROXY_LABEL,
                }
            )
    cold.sort(key=lambda c: str(c["name"]))

    covered_ops = sum(1 for op in ops if op_cover[op.oid])
    return {
        "schema": SCHEMA,
        "captured_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "counts": {
            "ops": len(ops),
            "ops_by_kind": {
                k: sum(1 for o in ops if o.kind == k)
                for k in ("verb", "producer", "gate", "feature", "human")
            },
            "skills": len(skills),
            "skills_by_scope": {
                k: sum(1 for s in skills if s.scope == k)
                for k in ("plugin", "operator")
            },
            "covered_ops": covered_ops,
            "uncovered_ops": len(uncovered),
            "cold_skills": len(cold),
            "proxy_files_scanned": proxy_files,
        },
        "uncovered": uncovered,
        "cold": cold,
        "covering": {op.oid: op_cover[op.oid] for op in ops if op_cover[op.oid]},
        "notes": notes,
    }


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M")


# REMOVED 2026-09-12: a hand-rolled atomic_write lived here and g22-durable-io flagged it.
# It did tmp-write + os.replace but NO fsync, so the rename could land ahead of the bytes and
# a crash between them leaves a zero-length file where a valid artifact used to be. The shared
# helper already does the fsync; re-implementing it locally is how one file quietly opts out of
# a durability guarantee every other producer has.
def atomic_write(path: pathlib.Path, text: str) -> None:
    gbtypes.atomic_write_text(path, text)


def main() -> int:
    ap = argparse.ArgumentParser(description="derive skill coverage both directions")
    ap.add_argument("--root", default=None, help="repo root (default: this checkout)")
    ap.add_argument("--json", action="store_true", help="machine-readable envelope")
    ap.add_argument("--top", type=int, default=10, help="top-N lists to print")
    args = ap.parse_args()
    root = (
        pathlib.Path(args.root)
        if args.root
        else pathlib.Path(__file__).resolve().parents[1]
    )
    if not (root / "bin" / "gb").exists() or not (root / "plugin" / "skills").is_dir():
        print(
            "error: not a grokbot checkout (bin/gb or plugin/skills absent)",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT
    try:
        result = derive(root)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT

    outdir = root / "skill-coverage"
    outdir.mkdir(exist_ok=True)
    artifact = outdir / (stamp() + ".json")
    atomic_write(artifact, json.dumps(result, indent=1) + "\n")
    result["artifact"] = str(artifact.relative_to(root))

    if args.json:
        print(json.dumps(result, indent=1))
        return EXIT_OK

    top: int = max(1, int(args.top))
    print(
        f"# Skill coverage — {result['counts']['covered_ops']}/{result['counts']['ops']} ops covered, "
        f"{result['counts']['cold_skills']} cold skills (proxy) -> {result['artifact']}"
    )
    print(
        f"ops by kind: {result['counts']['ops_by_kind']} | "
        f"skills: {result['counts']['skills_by_scope']}"
    )
    print(f"\n## Top-{top} uncovered operations")
    for u in result["uncovered"][:top]:
        p = u["proposal"]
        print(f"- [{u['kind']}] {u['id']} — {u['title'][:90]}")
        print(f"    -> {p['action']}: {p['skill'] or '—'} ({'; '.join(p['evidence'])})")
    print(
        f"\n## Top-{top} coldest skills ({PROXY_WINDOW_DAYS}d proxy, expires with telemetry)"
    )
    for c in result["cold"][:top]:
        print(
            f"- [{c['scope']}] {c['name']} — refs={c['proxy_refs_30d']}/{c['proxy_files_scanned']}"
        )
    if result["notes"]:
        print("\n## Notes")
        for n in result["notes"]:
            print(f"- {n}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
