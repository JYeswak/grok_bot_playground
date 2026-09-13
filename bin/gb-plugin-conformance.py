#!/usr/bin/env python3
"""gb-plugin-conformance — spec-derived plugin-manifest conformance harness (C4a).

Skill: testing-conformance-harnesses, Pattern 4 (SPEC-DERIVED TEST MATRIX).
Loop mapping: IDENTIFY (marketplace checklist via bin/gb-plugin-validate.py +
MARKETPLACE.md) -> EXTRACT (CLAUSES table below, one row per testable rule)
-> FIXTURE (the live plugin/ tree is the SUT; no frozen fixture needed) ->
HARNESS (this file: manifest loader + per-clause predicates) -> COVER (one
check per clause, tagged MUST/SHOULD) -> DIVERGE
(tests/conformance/DISCREPANCIES.md, C4a has none) -> MATRIX (stdout: clause
x manifest matrix + coverage accounting + score).

Corpus note: the repo ships exactly ONE plugin manifest
(plugin/.cursor-plugin/plugin.json), so the matrix is one row per CLAUSE
evaluated against that single live manifest — not one row per manifest.
"One row per plugin/manifest" is trivially satisfied (1 manifest, fully
covered); the skill-count columns below prove the reference-resolution
clauses scale across all 82 discovered skills.

Contract levels: MUST = enforced by gb-plugin-validate.py (a marketplace
submission breaking one is sent back); SHOULD = harness-proposed listing
quality (semver version, repository/author presence, browsable description),
marked as such — the validator does not enforce them.

Usage: python3 bin/gb-plugin-conformance.py [--md] [--json] [--selftest]
  default prints the text matrix; --md prints the markdown matrix artifact.
Exit: 0 = MUST score >= 0.95 (conformant) · 1 = honest-red · 2 = harness error.
Stdlib only. Read-only: never writes under plugin/.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_text  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+([+-][0-9A-Za-z.-]+)?$")
GENERIC = {
    "ai",
    "agent",
    "bot",
    "tool",
    "tools",
    "plugin",
    "grok",
    "utility",
    "helper",
    "mcp",
}
PLACEHOLDER = re.compile(r"\b(todo|tbd|lorem|your plugin|short description)\b", re.I)
WHEN = re.compile(r"\buse (when|if|every|each|before|after|on|at|for)\b", re.I)
MANIFESTS = ("plugin.json", ".cursor-plugin/plugin.json", ".grok-plugin/plugin.json")

# (clause, level, validator_source, description)
CLAUSES = (
    (
        "M-locate",
        "MUST",
        "check:160-173",
        "exactly one manifest at a documented location",
    ),
    ("M-json", "MUST", "check:174-177", "manifest is valid JSON"),
    ("M-name", "MUST", "_check_identity:61-65", "name present and kebab-case"),
    (
        "M-desc",
        "MUST",
        "_check_identity:66-70",
        "description >=30 chars, no placeholder",
    ),
    ("M-homepage", "MUST", "_check_identity:71-73", "homepage present"),
    ("M-license", "MUST", "_check_identity:71-73", "license present"),
    (
        "M-keywords",
        "MUST",
        "_check_identity:74-81",
        "keywords present and brand-scoped",
    ),
    (
        "M-skills-resolve",
        "MUST",
        "_check_skills:88-104",
        "every declared-or-discovered skill path holds SKILL.md",
    ),
    (
        "M-skill-route",
        "MUST",
        "_check_skills:105-128",
        "every SKILL.md has frontmatter name + WHEN description",
    ),
    (
        "M-variables",
        "MUST",
        "_check_variables:135-156",
        "variables shape holds; no secret outside ${NAME}",
    ),
    ("M-readme", "MUST", "check:181-182", "README.md exists"),
    (
        "S-version",
        "SHOULD",
        "harness-proposed",
        "version is semver x.y.z (MARKETPLACE.md:162 example)",
    ),
    (
        "S-repository",
        "SHOULD",
        "harness-proposed",
        "repository field present (listing provenance)",
    ),
    (
        "S-author",
        "SHOULD",
        "harness-proposed",
        "author present (listing accountability)",
    ),
    (
        "S-desc-rich",
        "SHOULD",
        "harness-proposed",
        "description >=100 chars (browsable card quality)",
    ),
)


def load(root: pathlib.Path):
    """Return (found_paths, manifest_or_None, problems)."""
    found = [root / m for m in MANIFESTS if (root / m).is_file()]
    if not found:
        return found, None, ["no-manifest"]
    try:
        man = json.loads(found[0].read_text(encoding="utf-8"))
    except Exception:
        return found, None, ["bad-json"]
    return found, man, []


def skill_paths(root: pathlib.Path, man: dict) -> list[str]:
    declared = man.get("skills")
    if isinstance(declared, str):
        declared = [declared]
    return declared or [
        str(d.relative_to(root))
        for d in sorted((root / "skills").glob("*"))
        if (d / "SKILL.md").is_file()
    ]


def fm_desc(fm: str) -> str:
    for ln in fm.splitlines():
        if ln.startswith("description:"):
            return ln.split("description:", 1)[1].strip()
    return ""


def evaluate(root: pathlib.Path):
    """Evaluate every clause. Returns (cells, notes, evidence)."""
    cells, notes, ev = {}, {}, {}
    found, man, probs = load(root)
    try:
        ev["manifest"] = str(found[0].relative_to(ROOT)) if found else "(none)"
    except ValueError:
        ev["manifest"] = found[0].name if found else "(none)"

    def put(cid, ok, note="", evidence=""):
        cells[cid] = "PASS" if ok else ("FAIL" if _level(cid) == "MUST" else "GAP")
        if note and not ok:
            notes[cid] = note
        if evidence:
            ev[cid] = evidence

    put(
        "M-locate",
        len(found) == 1,
        "0 manifests" if not found else f"{len(found)} manifests — ship exactly one",
        ev["manifest"],
    )
    put(
        "M-json",
        man is not None,
        "manifest is not valid JSON" if man is None else "",
        "valid JSON" if man is not None else "parse error",
    )
    if man is None:
        for cid, lvl, _s, _d in CLAUSES:
            if cid not in cells:
                put(cid, False, "unmeasurable: no parseable manifest", "n/a")
        return cells, notes, ev

    name = man.get("name") or ""
    put(
        "M-name",
        bool(name) and bool(KEBAB.match(name)),
        f"name {name!r} missing or not kebab-case",
        repr(name),
    )
    desc = (man.get("description") or "").strip()
    put(
        "M-desc",
        len(desc) >= 30 and not PLACEHOLDER.search(desc),
        "description missing/short/placeholder",
        f"{len(desc)} chars",
    )
    put(
        "M-homepage",
        bool(man.get("homepage")),
        "homepage missing",
        str(man.get("homepage") or "(missing)"),
    )
    put(
        "M-license",
        bool(man.get("license")),
        "license missing",
        str(man.get("license") or "(missing)"),
    )
    kws = [k.lower() for k in (man.get("keywords") or [])]
    gen = sorted(set(kws) & GENERIC)
    put(
        "M-keywords",
        bool(kws) and not gen,
        "keywords missing" if not kws else f"generic {gen}",
        ",".join(man.get("keywords") or []) or "(missing)",
    )

    rels = skill_paths(root, man)
    missing = [r for r in rels if not (root / r / "SKILL.md").is_file()]
    put(
        "M-skills-resolve",
        bool(rels) and not missing,
        "no skills" if not rels else f"SKILL.md missing: {missing[:3]}",
        f"{len(rels) - len(missing)}/{len(rels)} resolve",
    )
    bad_route = []
    for r in rels:
        f = root / r / "SKILL.md"
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            bad_route.append(f"{r}:no-fm")
            continue
        fm = text.split("---", 2)[1]
        if "name:" not in fm or not WHEN.search(fm_desc(fm)):
            bad_route.append(f"{r}:unroutable")
    put(
        "M-skill-route",
        not bad_route,
        f"unroutable: {bad_route[:3]}" if bad_route else "",
        f"{len(rels) - len(missing) - len(bad_route)}/{len(rels)} routable",
    )

    v = man.get("variables")
    vok, vnote = True, ""
    if v is not None:
        if v.get("type") != "object" or "properties" not in v:
            vok, vnote = False, "variables must be {type: object, properties: {...}}"
        else:
            for vn, spec in (v.get("properties") or {}).items():
                if not (spec or {}).get("description"):
                    vok, vnote = False, f"variable {vn}: no description"
                    break
            if vok:
                for f in root.rglob("*.json"):
                    if f.name == "plugin.json":
                        continue
                    txt = f.read_text(errors="replace")
                    for vn in v.get("properties") or {}:
                        if vn in txt and "${" + vn + "}" not in txt:
                            vok, vnote = False, f"{vn} leaks in {f.name}"
                            break
                    if not vok:
                        break
    put(
        "M-variables",
        vok,
        vnote,
        f"{len((v or {}).get('properties', {}) or {})} declared"
        if v
        else "absent (vacuous pass)",
    )
    put(
        "M-readme",
        (root / "README.md").is_file(),
        "README.md missing",
        "present" if (root / "README.md").is_file() else "missing",
    )

    ver = str(man.get("version") or "")
    put(
        "S-version",
        bool(SEMVER.match(ver)),
        f"version {ver!r} is not semver",
        ver or "(missing)",
    )
    put(
        "S-repository",
        bool(man.get("repository")),
        "repository missing",
        str(man.get("repository") or "(missing)"),
    )
    auth = man.get("author")
    put(
        "S-author",
        bool(auth),
        "author missing",
        (auth.get("name") if isinstance(auth, dict) else str(auth)) or "(missing)",
    )
    put(
        "S-desc-rich",
        len(desc) >= 100,
        f"description {len(desc)} chars < 100",
        f"{len(desc)} chars",
    )
    return cells, notes, ev


def _level(cid):
    return next(l for c, l, _s, _d in CLAUSES if c == cid)


def scores(cells):
    mp = mt = sp = st = 0
    for cid, lvl, _s, _d in CLAUSES:
        if lvl == "MUST":
            mt += 1
            mp += cells.get(cid) == "PASS"
        else:
            st += 1
            sp += cells.get(cid) == "PASS"
    return {
        "must": mp / mt if mt else 1.0,
        "must_pass": mp,
        "must_total": mt,
        "should": sp / st if st else 1.0,
        "should_pass": sp,
        "should_total": st,
    }


def render_text(cells, sc, ev):
    L = [
        "Plugin manifest conformance matrix (C4a)",
        f"manifest: {ev.get('manifest', '?')} (single live manifest; one row per clause)",
        "",
        f"{'clause':<16} {'level':<7} {'verdict':<7} evidence",
    ]
    for cid, lvl, _s, d in CLAUSES:
        L.append(f"{cid:<16} {lvl:<7} {cells.get(cid, '?'):<7} {ev.get(cid, '')} — {d}")
    L.append("")
    L.append(
        f"MUST cells: pass={sc['must_pass']}/{sc['must_total']} score={sc['must']:.3f}"
    )
    L.append(
        f"SHOULD cells: pass={sc['should_pass']}/{sc['should_total']} score={sc['should']:.3f}"
    )
    L.append(f"verdict: {'CONFORMANT' if sc['must'] >= 0.95 else 'NOT CONFORMANT'}")
    return "\n".join(L) + "\n"


def render_md(cells, sc, ev, notes):
    L = [
        "# Plugin manifest-conformance matrix (C4a)",
        "",
        "Spec-derived (Pattern 4): every MUST clause mirrors one "
        "`bin/gb-plugin-validate.py` rule (validator source cited per row); every SHOULD "
        "clause is harness-proposed listing quality, marked as such. Read-only: nothing "
        "under `plugin/` was modified for this matrix.",
        "",
        f"Provenance: spec `bin/gb-plugin-validate.py` + `MARKETPLACE.md`; harness "
        f"`bin/gb-plugin-conformance.py`; command `python3 bin/gb-plugin-conformance.py --md`.",
        "",
        f"Corpus: exactly one live manifest (`{ev.get('manifest', '?')}`); "
        "matrix is one row per clause against it. Skill-resolution clauses range over all "
        "discovered skills (folder discovery is the documented default).",
        "",
        "Cell vocabulary: MUST columns use PASS/FAIL; SHOULD columns use PASS/GAP.",
        "",
        "| clause | level | spec source | verdict | evidence |",
        "|---|---|---|---|---|",
    ]
    for cid, lvl, src, d in CLAUSES:
        L.append(
            f"| {cid} | {lvl} | {src} | {cells.get(cid, '?')} | {ev.get(cid, '')} — {d} |"
        )
    L.append("")
    L.append("## Coverage accounting")
    L.append("")
    L.append(
        "| section | clause | MUST | SHOULD | tested | passing | divergent | score |"
    )
    L.append("|---|---|---|---|---|---|---|---|")
    for cid, lvl, _s, _d in CLAUSES:
        m, s = (1, 0) if lvl == "MUST" else (0, 1)
        p = 1 if cells.get(cid) == "PASS" else 0
        L.append(f"| manifest | {cid} | {m} | {s} | 1 | {p} | 0 | {p:.3f} |")
    L.append("")
    L.append(
        f"MUST score {sc['must']:.3f} ({sc['must_pass']}/{sc['must_total']}); "
        f"SHOULD score {sc['should']:.3f} ({sc['should_pass']}/{sc['should_total']}); "
        f"verdict **{'CONFORMANT' if sc['must'] >= 0.95 else 'NOT CONFORMANT'}**."
    )
    if notes:
        L.append("")
        L.append("## Failures with fix proposals")
        L.append("")
        for cid, note in notes.items():
            L.append(f"- `{cid}` ({note})")
    else:
        L.append("")
        L.append(
            "No failures: the live manifest satisfies every MUST clause and every "
            "harness-proposed SHOULD clause. C4a has no DISCREPANCIES entries."
        )
    return "\n".join(L) + "\n"


def render_json(cells, sc, ev, notes):
    return (
        json.dumps(
            {
                "schema": "gb-plugin-conformance/1",
                "manifest": ev.get("manifest"),
                "must_score": round(sc["must"], 4),
                "should_score": round(sc["should"], 4),
                "verdict": "CONFORMANT" if sc["must"] >= 0.95 else "NOT CONFORMANT",
                "cells": cells,
                "evidence": ev,
                "notes": notes,
            },
            indent=1,
        )
        + "\n"
    )


# ---------------------------------------------------------------- selftest
SELFTEST_MANIFEST = {
    "name": "Bad_Name",
    "version": "tomorrow",
    "description": "TODO short",
    "keywords": ["ai", "bot"],
    "skills": ["skills/ghost"],
    "variables": {"type": "wrong"},
}

# clause -> fragment that MUST appear in the failure note/explanation, proving
# the clause fires on the planted defect rather than passing vacuously.
EXPECTED_FIRES = {
    "M-locate": "ship exactly one",
    "M-name": "kebab",
    "M-desc": "placeholder",
    "M-homepage": "homepage",
    "M-license": "license",
    "M-keywords": "generic",
    "M-skills-resolve": "SKILL.md missing",
    "M-skill-route": None,  # ghost skill never parses: covered via M-skills-resolve
    "M-variables": "variables must be",
    "M-readme": "README.md",
    "S-version": "semver",
    "S-repository": "repository",
    "S-author": "author",
    "S-desc-rich": "chars < 100",
}


def _write_single_bad(root: pathlib.Path):
    """One manifest breaking every content rule (M-locate/M-json stay PASS)."""
    root.mkdir(parents=True)
    atomic_write_text(root / "plugin.json", json.dumps(SELFTEST_MANIFEST))
    (root / "skills").mkdir()


def selftest() -> int:
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        # Root A: content rules — every MUST content clause FAILs, envelope PASSes.
        bad = base / "badplug"
        _write_single_bad(bad)
        cells, notes, _ev = evaluate(bad)
        assert cells["M-locate"] == "PASS", "selftest: single manifest should locate"
        assert cells["M-json"] == "PASS", "selftest: manifest is valid JSON"
        for cid, lvl, _s, _d in CLAUSES:
            if lvl != "MUST" or cid in ("M-locate", "M-json", "M-skill-route"):
                continue
            assert (
                cells.get(cid) == "FAIL"
            ), f"selftest: {cid} did not fire ({cells.get(cid)})"
        # Fire-evidence: each note names the planted defect (not a vacuous FAIL).
        for cid, frag in EXPECTED_FIRES.items():
            if frag is None or cid in ("M-locate", "M-json"):
                continue
            assert (
                frag.lower().split()[0] in notes.get(cid, "").lower()
            ), f"selftest: {cid} fired without evidence ({notes.get(cid, '')!r})"
        # M-skill-route fires on a present-but-WHEN-less skill (ghost skills can
        # only fail resolution, so route needs its own planted defect).
        r2 = base / "routeplug"
        (r2 / "skills" / "whenless").mkdir(parents=True)
        atomic_write_text(
            r2 / "plugin.json",
            json.dumps(
                {
                    "name": "ok-name",
                    "description": "x" * 120,
                    "homepage": "h",
                    "license": "l",
                    "keywords": ["brand-scoped-term"],
                    "version": "1.2.3",
                }
            ),
        )
        atomic_write_text(
            r2 / "skills" / "whenless" / "SKILL.md",
            "---\nname: whenless\ndescription: does stuff generally\n---\n# T\n",
        )
        atomic_write_text(r2 / "README.md", "hi")
        c2, _n2, _e2 = evaluate(r2)
        assert (
            c2["M-skill-route"] == "FAIL"
        ), "selftest: M-skill-route did not fire on WHEN-less skill"
        assert c2["M-skills-resolve"] == "PASS", "selftest: resolution should pass here"
        # Root B: envelope rules — ambiguity + invalid JSON.
        bad2 = base / "badenv"
        (bad2 / ".cursor-plugin").mkdir(parents=True)
        atomic_write_text(bad2 / "plugin.json", "{not json")
        atomic_write_text(
            bad2 / ".cursor-plugin" / "plugin.json", json.dumps({"name": "x"})
        )
        c3, _n3, _e3 = evaluate(bad2)
        assert c3["M-locate"] == "FAIL", "selftest: ambiguity did not fire"
        assert c3["M-json"] == "FAIL", "selftest: invalid JSON did not fire"
        # Live manifest validates clean (read-only sanity, not a modification).
        c4, _n4, _e4 = evaluate(PLUGIN)
        must_fail = [
            c for c, l, _s, _d in CLAUSES if l == "MUST" and c4.get(c) != "PASS"
        ]
        assert not must_fail, f"selftest: live manifest fails {must_fail}"
    print(
        f"SELFTEST PASS — {len(CLAUSES)} clauses fire on planted defects, live manifest clean"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--md", action="store_true")
    g.add_argument("--json", action="store_true")
    g.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        try:
            return selftest()
        except AssertionError as e:
            print(f"SELFTEST FAILED: {e}")
            return 1
    if not PLUGIN.is_dir():
        print("plugin/ not found", file=sys.stderr)
        return 2
    cells, notes, ev = evaluate(PLUGIN)
    sc = scores(cells)
    if args.md:
        sys.stdout.write(render_md(cells, sc, ev, notes))
    elif args.json:
        sys.stdout.write(render_json(cells, sc, ev, notes))
    else:
        sys.stdout.write(render_text(cells, sc, ev))
    return 0 if sc["must"] >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main())
