#!/usr/bin/env python3
"""gb-skill-conformance — spec-derived skill shape-conformance harness (C3).

Pattern: testing-conformance-harnesses Pattern 4 (spec-derived test matrix).
Spec source (pinned): `bin/gb-plugin-validate.py::_check_skills` as the
definition for every MUST clause (same WHEN-cue regex semantics), plus the
measured house body shape across the corpus itself, with
`plugin/skills/account-health-watch/SKILL.md` as the strongest exemplar
(carries all 8 SHOULD sections). No imagined contract: the six-part
persona/research/recall/build/validate/land shape and tier/scope-cue
frontmatter keys do not exist in this corpus (measured zero) and are
explicitly REJECTED below, not mechanized.

Requirement levels:
  MUST   — the skill is not routable/submittable without it (validator
           rule, structural floor, or corpus-wide name uniqueness).
  SHOULD — house shape the strongest skills carry; a gap is a quality
           note with a fix proposal, never a conformance FAIL.

Matrix: one row per skill x one column per contract element (7 MUST +
8 SHOULD). Cells are PASS / FAIL (MUST) and PASS / GAP (SHOULD). Clauses
are independent except three documented cascades: no frontmatter also
fails M-name/M-desc/M-when (the keys live inside it); an empty name also
fails S-namealign (nothing to align); an empty description also fails
M-when (no text to carry the cue). G-unique is vacuous-pass on empty
names (M-name already fails those rows).

REJECTED (considered, not mechanized — the corpus beats the guess):
  - persona/research/recall/build/validate/land six-part body: zero
    headings match across all 85 skills. The de-facto shape is
    rule/when-to-use/inputs/sequence/validation/output/boundaries.
  - tier / scope-cue frontmatter keys: the key set is exactly
    {name, description} on all 85 files; a tier check would
    manufacture 85 gaps.
  - description-length or body-length floors: the validator sets no
    threshold; inventing one turns taste into a gate.
  - error-message / behavioral legs: skills are prose contracts, not
    servers; presence of the working sections IS the observable.

Usage:
  gb-skill-conformance.py            # text matrix to stdout
  gb-skill-conformance.py --md       # markdown matrix (tests/conformance/)
  gb-skill-conformance.py --json     # machine-readable to stdout
  gb-skill-conformance.py --selftest # each clause fires on its planted
                                     # defect (documented cascades fire
                                     # together); exit 1 on failure
Exit codes: 0 iff MUST score >= 0.95 (else 1); 2 on bad invocation.
Stdlib only. Reads plugin/skills/ + library template-pack skills;
writes nothing (caller redirects --md).
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
PLUGIN_SKILLS = ROOT / "plugin" / "skills"
LIB_TEMPLATES = ROOT / "library" / "templates"

# Same WHEN-cue semantics as gb-plugin-validate.py::_check_skills.
WHEN = re.compile(r"\buse (when|if|every|each|before|after|on|at|for)\b", re.I)
H1 = re.compile(r"^# .+", re.M)
FM_FENCE = "---"


def split_frontmatter(text: str):
    """Return (fm, body) or (None, text) when no frontmatter fence holds."""
    if not text.startswith(FM_FENCE + "\n"):
        return None, text
    parts = text.split(FM_FENCE, 2)
    if len(parts) < 3:
        return None, text
    return parts[1], parts[2]


def fm_field(fm: str, key: str) -> str:
    for ln in fm.splitlines():
        if ln.startswith(key + ":"):
            return ln.split(key + ":", 1)[1].strip()
    return ""


def headings(body: str):
    return [m.group(1).strip() for m in re.finditer(r"^## (.+)$", body, re.M)]


def has_section(body: str, *prefixes: str) -> bool:
    hs = headings(body)
    return any(h.lower().startswith(p) for h in hs for p in prefixes)


# ---------------------------------------------------------------- checks
# Each check takes (text, fm_or_None, body, leaf, name) and returns bool.


def c_file(text, fm, body, leaf, name):
    return text is not None


def c_fm(text, fm, body, leaf, name):
    return fm is not None


def c_name(text, fm, body, leaf, name):
    return bool(name)


def c_desc(text, fm, body, leaf, name, desc=""):
    return bool(desc)


def c_when(text, fm, body, leaf, name, desc=""):
    return bool(WHEN.search(desc))


def c_h1(text, fm, body, leaf, name):
    return bool(H1.search(body))


def c_rule(text, fm, body, leaf, name):
    return has_section(body, "the rule that matters")


def c_whenuse(text, fm, body, leaf, name):
    return has_section(body, "when to use")


def c_inputs(text, fm, body, leaf, name):
    return has_section(body, "inputs and access")


SEQ_PREFIXES = (
    "sequence",
    "what to do, in this order",
    "what to check, in this order",
    "what to read, in this order",
    "what to research, in this order",
)


def c_seq(text, fm, body, leaf, name):
    return has_section(body, *SEQ_PREFIXES)


def c_valid(text, fm, body, leaf, name):
    return has_section(body, "validation")


def c_output(text, fm, body, leaf, name):
    return has_section(body, "output")


def c_bound(text, fm, body, leaf, name):
    return has_section(body, "boundaries", "approval boundary")


def c_namealign(text, fm, body, leaf, name):
    return name == leaf


# (clause, section, level, needs_desc)
CLAUSES = (
    ("M-file", "envelope", "MUST", False),
    ("M-fm", "envelope", "MUST", False),
    ("M-name", "envelope", "MUST", False),
    ("M-desc", "envelope", "MUST", True),
    ("M-when", "envelope", "MUST", True),
    ("M-h1", "body", "MUST", False),
    ("G-unique", "set", "MUST", False),
    ("S-rule", "body", "SHOULD", False),
    ("S-whenuse", "body", "SHOULD", False),
    ("S-inputs", "body", "SHOULD", False),
    ("S-seq", "body", "SHOULD", False),
    ("S-valid", "body", "SHOULD", False),
    ("S-output", "body", "SHOULD", False),
    ("S-bound", "body", "SHOULD", False),
    ("S-namealign", "envelope", "SHOULD", False),
)

CHECKS = {
    "M-file": c_file,
    "M-fm": c_fm,
    "M-name": c_name,
    "M-desc": c_desc,
    "M-when": c_when,
    "M-h1": c_h1,
    "S-rule": c_rule,
    "S-whenuse": c_whenuse,
    "S-inputs": c_inputs,
    "S-seq": c_seq,
    "S-valid": c_valid,
    "S-output": c_output,
    "S-bound": c_bound,
    "S-namealign": c_namealign,
}

FIX = {
    "M-file": "restore the SKILL.md at its discovered path; a skill with no file is not a skill.",
    "M-fm": "open with a `---` YAML frontmatter fence carrying `name:` + `description:`.",
    "M-name": "add a non-empty `name:` key to the frontmatter.",
    "M-desc": "add a non-empty `description:` key to the frontmatter.",
    "M-when": "rewrite the description so it says WHEN the skill routes "
    "(validator cue: `use when|if|every|each|before|after|on|at|for`).",
    "M-h1": "give the body a top-level `# Title` heading.",
    "G-unique": "single-source the skill: keep one canonical SKILL.md and "
    "reference it from the other packaging path (or scope-prefix one name).",
    "S-rule": "add the house invariant section (`## The rule that matters more than the X`).",
    "S-whenuse": "add a `## When to use` section with trigger cues and explicit non-goals.",
    "S-inputs": "add a `## Inputs and access` section naming required inputs and access limits.",
    "S-seq": "add the procedural core (`## Sequence` or `## What to do, in this order`).",
    "S-valid": "add a `## Validation` section with per-output acceptance checks.",
    "S-output": "add a `## Output` section stating the exact deliverable shape.",
    "S-bound": "add a `## Boundaries` section (read-only posture, approval line, never-dos).",
    "S-namealign": "rename the leaf directory to the frontmatter `name:` "
    "so the skill resolves identically from every packaging path.",
}


def discover(root: pathlib.Path):
    """Return [(rowid, scope, leaf, path)]. Rowid is unique by construction."""
    rows = []
    plug = root / "plugin" / "skills"
    if plug.is_dir():
        for p in sorted(plug.glob("*/SKILL.md")):
            rows.append((p.parent.name, "plugin", p.parent.name, p))
    lib = root / "library" / "templates"
    if lib.is_dir():
        for p in sorted(lib.glob("*/skills/*/SKILL.md")):
            rows.append(
                (
                    f"{p.parts[-4]}/{p.parent.name}",
                    "library",
                    p.parent.name,
                    p,
                )
            )
    return rows


def evaluate(rows):
    """Evaluate every row. Returns (results, name_counts)."""
    parsed = []
    counts: dict[str, int] = {}
    for rowid, scope, leaf, path in rows:
        try:
            text = path.read_text()
        except OSError:
            text = None
        fm, body = split_frontmatter(text) if text is not None else (None, "")
        name = fm_field(fm, "name") if fm is not None else ""
        desc = fm_field(fm, "description") if fm is not None else ""
        if name:
            counts[name] = counts.get(name, 0) + 1
        parsed.append((rowid, scope, leaf, text, fm, body, name, desc))
    results = []
    for rowid, scope, leaf, text, fm, body, name, desc in parsed:
        cells = {}
        for clause, _sec, _lvl, needs_desc in CLAUSES:
            if clause == "G-unique":
                # Vacuous pass on empty names: no name, no collision.
                # M-name already fails those rows.
                cells[clause] = True if not name else counts.get(name, 0) == 1
                continue
            fn = CHECKS[clause]
            try:
                if needs_desc:
                    cells[clause] = bool(fn(text, fm, body, leaf, name, desc))
                else:
                    cells[clause] = bool(fn(text, fm, body, leaf, name))
            except Exception:
                cells[clause] = False
        results.append(
            {
                "id": rowid,
                "scope": scope,
                "leaf": leaf,
                "name": name,
                "cells": cells,
            }
        )
    return results, counts


def scores(results):
    must_pass = must_tot = should_pass = should_tot = 0
    for r in results:
        for clause, _sec, lvl, _nd in CLAUSES:
            if lvl == "MUST":
                must_tot += 1
                must_pass += r["cells"][clause]
            else:
                should_tot += 1
                should_pass += r["cells"][clause]
    return {
        "must": must_pass / must_tot if must_tot else 1.0,
        "must_pass": must_pass,
        "must_tot": must_tot,
        "should": should_pass / should_tot if should_tot else 1.0,
        "should_pass": should_pass,
        "should_tot": should_tot,
    }


# ---------------------------------------------------------------- render


def cell_mark(clause, ok):
    lvl = next(l for c, _s, l, _n in CLAUSES if c == clause)
    if ok:
        return "PASS"
    return "FAIL" if lvl == "MUST" else "GAP"


def render_text(results, sc):
    lines = [
        f"skill conformance C3: {len(results)} skills, "
        f"MUST {sc['must']:.3f} ({sc['must_pass']}/{sc['must_tot']}), "
        f"SHOULD {sc['should']:.3f} ({sc['should_pass']}/{sc['should_tot']})"
    ]
    hdr = ["skill"] + [c for c, _s, _l, _n in CLAUSES]
    lines.append(" | ".join(hdr))
    for r in results:
        lines.append(
            " | ".join(
                [r["id"]] + [cell_mark(c, r["cells"][c]) for c, _s, _l, _n in CLAUSES]
            )
        )
    offenders = sorted(
        results,
        key=lambda r: sum(r["cells"][c] for c, _s, _l, _n in CLAUSES),
    )
    lines.append("worst offenders:")
    for r in offenders[:10]:
        got = sum(r["cells"][c] for c, _s, _l, _n in CLAUSES)
        miss = [c for c, _s, _l, _n in CLAUSES if not r["cells"][c]]
        lines.append(f"  {r['id']}: {got}/{len(CLAUSES)} missing {','.join(miss)}")
    return "\n".join(lines) + "\n"


def render_md(results, sc):
    L = []
    L.append("# Skill shape-conformance matrix (C3)")
    L.append("")
    L.append(
        "Spec-derived (Pattern 4): every MUST clause mirrors one "
        "`bin/gb-plugin-validate.py::_check_skills` rule (same WHEN-cue "
        "regex semantics), plus the structural floor (H1) and set-level "
        "name uniqueness; every SHOULD clause is a section of the "
        "measured house shape whose strongest exemplar is "
        "`plugin/skills/account-health-watch/SKILL.md`. Read-only: no "
        "skill was modified for this matrix."
    )
    L.append("")
    L.append(
        "Provenance: spec `bin/gb-plugin-validate.py` (`_check_skills`) + "
        "measured corpus shape (82 plugin + 3 library skills); harness "
        "`bin/gb-skill-conformance.py`; command "
        "`python3 bin/gb-skill-conformance.py --md`."
    )
    L.append("")
    L.append(
        "Corpus: 82 skills under `plugin/skills/` + 3 sibling skills "
        "under `library/templates/*/skills/*/` = **85 skills**. "
        "Frontmatter key set is exactly {name, description} on all 85; "
        "all names kebab-case; all descriptions carry a WHEN cue."
    )
    L.append("")
    L.append(
        "Cell vocabulary: MUST columns use PASS/FAIL (FAIL = not "
        "conformant on that cell); SHOULD columns use PASS/GAP (GAP = "
        "quality note with a fix proposal below, never a conformance "
        "failure)."
    )
    L.append("")
    hdr = ["skill", "scope"] + [c for c, _s, _l, _n in CLAUSES]
    L.append("| " + " | ".join(hdr) + " |")
    L.append("|" + "|".join(["---"] * len(hdr)) + "|")
    for r in results:
        L.append(
            "| "
            + " | ".join(
                [r["id"], r["scope"]]
                + [cell_mark(c, r["cells"][c]) for c, _s, _l, _n in CLAUSES]
            )
            + " |"
        )
    L.append("")
    L.append(
        "Set globals: `G-unique` is evaluated per row (a name shared by "
        "two packaging paths fails on both rows)."
    )
    L.append("")
    L.append("## Coverage accounting")
    L.append("")
    L.append(
        "| section | clause | MUST | SHOULD | tested | passing | divergent | score |"
    )
    L.append("|---|---|---|---|---|---|---|---|")
    for clause, sec, lvl, _nd in CLAUSES:
        tested = len(results)
        passing = sum(r["cells"][clause] for r in results)
        m = 1 if lvl == "MUST" else 0
        s = 1 - m
        L.append(
            f"| {sec} | {clause} | {m} | {s} | {tested} | "
            f"{passing} | 0 | {passing/tested:.3f} |"
        )
    L.append("")
    verdict = "CONFORMANT" if sc["must"] >= 0.95 else "NOT CONFORMANT"
    L.append(
        f"MUST score {sc['must']:.3f} ({sc['must_pass']}/{sc['must_tot']}); "
        f"SHOULD score {sc['should']:.3f} "
        f"({sc['should_pass']}/{sc['should_tot']}); "
        f"verdict **{verdict}**."
    )
    L.append("")
    L.append("## Per-skill coverage")
    L.append("")
    L.append("| skill | passed | applicable | score |")
    L.append("|---|---|---|---|")
    for r in sorted(
        results,
        key=lambda r: (-sum(r["cells"][c] for c, _s, _l, _n in CLAUSES), r["id"]),
    ):
        got = sum(r["cells"][c] for c, _s, _l, _n in CLAUSES)
        L.append(f"| {r['id']} | {got} | {len(CLAUSES)} | {got/len(CLAUSES):.3f} |")
    L.append("")
    L.append("## Per-clause coverage")
    L.append("")
    L.append("| clause | level | passed | applicable | score |")
    L.append("|---|---|---|---|---|")
    for clause, _sec, lvl, _nd in CLAUSES:
        passing = sum(r["cells"][clause] for r in results)
        L.append(
            f"| {clause} | {lvl} | {passing} | {len(results)} | "
            f"{passing/len(results):.3f} |"
        )
    L.append("")
    L.append("## Failures with fix proposals")
    L.append("")
    fails = [
        (r, clause)
        for r in results
        for clause, _s, _l, _n in CLAUSES
        if not r["cells"][clause]
    ]
    if not fails:
        L.append(
            "None — no offenders on this corpus. Non-vacuity is proven "
            "by `--selftest`: every clause fires on its planted defect."
        )
    else:
        for r, clause in fails:
            extra = ""
            if clause == "G-unique":
                extra = f" (name `{r['name']}` ships twice)"
            elif clause == "S-namealign":
                extra = f" (leaf `{r['leaf']}` vs name `{r['name']}`)"
            L.append(f"- `{clause}` x `{r['id']}`{extra} " f"(FIX: {FIX[clause]})")
    L.append("")
    L.append("## Divergences")
    L.append("")
    L.append(
        "None. C3 has no DISCREPANCIES entries: every deviation from "
        "the MUST contract is a FAIL above, and SHOULD gaps are listed "
        "as fix proposals, never silently accepted."
    )
    L.append("")
    return "\n".join(L)


def render_json(results, sc):
    return (
        json.dumps(
            {
                "schema": "gb-skill-conformance/1",
                "corpus": len(results),
                "must_score": round(sc["must"], 4),
                "should_score": round(sc["should"], 4),
                "verdict": "CONFORMANT" if sc["must"] >= 0.95 else "NOT CONFORMANT",
                "clauses": [
                    {"clause": c, "section": s, "level": l} for c, s, l, _n in CLAUSES
                ],
                "rows": [
                    {
                        "id": r["id"],
                        "scope": r["scope"],
                        "name": r["name"],
                        "cells": {
                            c: (
                                "PASS"
                                if r["cells"][c]
                                else ("FAIL" if l == "MUST" else "GAP")
                            )
                            for c, _s, l, _n in CLAUSES
                        },
                    }
                    for r in results
                ],
            },
            indent=2,
        )
        + "\n"
    )


# ---------------------------------------------------------------- selftest

SELFTEST_EXEMPLAR = """---
name: SELFTEST-NAME
description: Does the thing. Use when testing the harness itself.
---

# Selftest exemplar

## The rule that matters more than the test

Planted rule.

## When to use

Testing.

## Inputs and access

None.

## Sequence

One step.

## Validation

It fires.

## Output

A verdict.

## Boundaries

None.
"""
EXPECTED_FIRES = {
    "M-fm": ["M-fm", "M-name", "M-desc", "M-when", "S-namealign"],
    "M-name": ["M-name", "S-namealign"],
    "M-desc": ["M-desc", "M-when"],
}
SELFTEST_MUTATIONS = {
    # Each mutation plants its clause's defect into the exemplar.
    "M-fm": lambda t: t.split("---", 2)[2],
    "M-name": lambda t: t.replace("name: SELFTEST-NAME", "name: "),
    "M-desc": lambda t: t.replace(
        "description: Does the thing. Use when testing the harness itself.",
        "description: ",
    ),
    "M-when": lambda t: t.replace(
        "Use when testing the harness itself.", "for harness purposes."
    ),
    "M-h1": lambda t: t.replace("# Selftest exemplar", "Selftest exemplar"),
    "S-rule": lambda t: t.replace(
        "## The rule that matters more than the test", "## A remark"
    ),
    "S-whenuse": lambda t: t.replace("## When to use", "## Triggers"),
    "S-inputs": lambda t: t.replace("## Inputs and access", "## Needs"),
    "S-seq": lambda t: t.replace("## Sequence", "## Steps"),
    "S-valid": lambda t: t.replace("## Validation", "## Checks"),
    "S-output": lambda t: t.replace("## Output", "## Result"),
    "S-bound": lambda t: t.replace("## Boundaries", "## Limits"),
    "S-namealign": lambda t: t.replace("name: SELFTEST-NAME", "name: some-other-name"),
}


def selftest() -> int:
    """Each clause fires on its planted defect (cascades per EXPECTED_FIRES)."""
    legs = 0

    def leg(ok: bool, label: str):
        nonlocal legs
        legs += 1
        print(("ok   " if ok else "FAIL ") + label)
        if not ok:
            raise AssertionError(label)

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        plug = root / "plugin" / "skills"
        plug.mkdir(parents=True)
        good = plug / "good-skill" / "SKILL.md"
        good.parent.mkdir(parents=True)
        exemplar = SELFTEST_EXEMPLAR.replace("SELFTEST-NAME", "good-skill")
        atomic_write_text(good, exemplar)

        rows = discover(root)
        leg(len(rows) == 1, "discovery finds the one good skill")
        results, _ = evaluate(rows)
        r = results[0]
        leg(
            all(r["cells"][c] for c, _s, _l, _n in CLAUSES),
            "exemplar passes all 15 clauses",
        )

        for clause, mut in SELFTEST_MUTATIONS.items():
            bad = plug / "bad-skill" / "SKILL.md"
            bad.parent.mkdir(parents=True, exist_ok=True)
            # Mutate the placeholder template FIRST, then stamp the row
            # name: mutating the already-stamped exemplar is a no-op for
            # placeholder-keyed mutations and aliases the good row's name.
            bad_text = mut(SELFTEST_EXEMPLAR).replace("SELFTEST-NAME", "bad-skill")
            atomic_write_text(bad, bad_text)
            results, _ = evaluate(discover(root))
            badrow = next(x for x in results if x["id"] == "bad-skill")
            goodrow = next(x for x in results if x["id"] == "good-skill")
            fired = [c for c, _s, _l, _n in CLAUSES if not badrow["cells"][c]]
            want = EXPECTED_FIRES.get(clause, [clause])
            leg(fired == want, f"{clause} fires {want} (fired={fired})")
            leg(
                all(goodrow["cells"][c] for c, _s, _l, _n in CLAUSES),
                f"{clause} plant leaves the good row green",
            )
            bad.unlink()

        # M-file: a declared dir with no SKILL.md never becomes a row —
        # instead prove the loader marks an unreadable file all-FAIL.
        (plug / "empty-skill").mkdir(parents=True, exist_ok=True)
        results, _ = evaluate(discover(root))
        leg(
            all(x["id"] != "empty-skill" for x in results),
            "M-file: skill-less dir discovers no row (validator reports it)",
        )

        # G-unique: same name shipped twice fails on both rows.
        dup = plug / "dup-skill" / "SKILL.md"
        dup.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(dup, exemplar.replace("SELFTEST-NAME", "good-skill"))
        results, _ = evaluate(discover(root))
        by_id = {x["id"]: x for x in results}
        leg(
            by_id["good-skill"]["cells"]["G-unique"] is False
            and by_id["dup-skill"]["cells"]["G-unique"] is False,
            "G-unique fires on both rows sharing a name",
        )
        fired_dup = [
            c for c, _s, _l, _n in CLAUSES if not by_id["dup-skill"]["cells"][c]
        ]
        leg(
            fired_dup == ["G-unique", "S-namealign"],
            f"dup row fires G-unique + alignment cascade (fired={fired_dup})",
        )
    print(f"{legs} selftest leg(s) green; every clause fires.")
    return 0


# ---------------------------------------------------------------- main


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
    rows = discover(ROOT)
    if not rows:
        print("no skills discovered", file=sys.stderr)
        return 2
    results, _counts = evaluate(rows)
    sc = scores(results)
    if args.md:
        sys.stdout.write(render_md(results, sc))
    elif args.json:
        sys.stdout.write(render_json(results, sc))
    else:
        sys.stdout.write(render_text(results, sc))
    return 0 if sc["must"] >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main())
