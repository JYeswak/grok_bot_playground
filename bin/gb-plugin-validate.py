#!/usr/bin/env python3
"""gb-plugin-validate — refuse to submit a plugin the marketplace would send back.

Checks `plugin/` against the rules measured from `xai-org/plugin-marketplace/CONTRIBUTING.md` and
its README on 2026-09-11, locally and offline, so a bad manifest is caught here rather than in a
public PR.

Rules enforced (each maps to a stated requirement):
  - `.grok-plugin/plugin.json` exists and is valid JSON
  - `name` present and kebab-case                       ("name in kebab-case and unique")
  - `description` present and not a placeholder          ("a clear description")
  - `homepage` and `license` present                     ("a homepage"; "the license is stated")
  - `keywords` brand-scoped, not generic                 ("not generic terms - they power the CTA")
  - every path in `skills` exists and holds a SKILL.md
  - every SKILL.md carries YAML frontmatter with `name` + `description`, and the description says
    WHEN to use it - a skill nobody can route to is a skill nobody runs
  - README.md exists                                     ("local plugins include a README.md")

  gb-plugin-validate.py            # validate plugin/
  gb-plugin-validate.py --selftest # prove the checks fire on a known-bad manifest
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

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
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


# The two formats the plugin reference documents, in the order it lists them. `.grok-plugin/` is
# what the marketplace repo uses for its OWN catalog manifest and is accepted for local-source
# submissions — it is NOT one of the two plugin manifest locations, which is what this validator
# originally assumed and got wrong.
MANIFESTS = ("plugin.json", ".cursor-plugin/plugin.json", ".grok-plugin/plugin.json")


def _check_identity(man: dict) -> list[str]:
    """The fields the marketplace checklist names explicitly."""
    bad = []
    name = man.get("name") or ""
    if not name:
        bad.append("name missing")
    elif not KEBAB.match(name):
        bad.append(f"name {name!r} is not kebab-case")
    desc = (man.get("description") or "").strip()
    if len(desc) < 30:
        bad.append("description missing or too short to browse by")
    if PLACEHOLDER.search(desc):
        bad.append(f"description still contains placeholder text: {desc[:40]!r}")
    for field in ("homepage", "license"):
        if not man.get(field):
            bad.append(f"{field} missing — the marketplace checklist requires it")
    kws = [k.lower() for k in (man.get("keywords") or [])]
    if not kws:
        bad.append("keywords missing — they power the plugin CTA")
    elif sorted(set(kws) & GENERIC):
        bad.append(
            f"generic keyword(s) {sorted(set(kws) & GENERIC)} — the checklist demands "
            f"brand-scoped terms"
        )
    return bad


def _check_skills(root: pathlib.Path, man: dict) -> list[str]:
    """Folder discovery is the documented default; a manifest field REPLACES it, never adds."""
    bad = []
    declared = man.get("skills")
    if isinstance(declared, str):
        declared = [declared]
    skills = declared or [
        str(d.relative_to(root))
        for d in sorted((root / "skills").glob("*"))
        if (d / "SKILL.md").is_file()
    ]
    if not skills:
        return [
            "no skills declared and none discoverable under skills/ — nothing to run"
        ]
    for rel in skills:
        sfile = (root / rel).resolve() / "SKILL.md"
        if not sfile.is_file():
            bad.append(f"skill {rel}: SKILL.md missing")
            continue
        text = sfile.read_text()
        if not text.startswith("---"):
            bad.append(f"skill {rel}: no YAML frontmatter")
            continue
        fm = text.split("---", 2)[1]
        if "name:" not in fm:
            bad.append(f"skill {rel}: frontmatter has no name")
        sdesc = next(
            (
                ln.split("description:", 1)[1].strip()
                for ln in fm.splitlines()
                if ln.startswith("description:")
            ),
            "",
        )
        if not sdesc:
            bad.append(f"skill {rel}: frontmatter has no description")
        elif not re.search(
            r"\buse (when|if|every|each|before|after|on|at|for)\b", sdesc, re.I
        ):
            bad.append(
                f"skill {rel}: description does not say WHEN to use it — "
                f"a skill nobody can route to is a skill nobody runs"
            )
    return bad


def _check_variables(root: pathlib.Path, man: dict) -> list[str]:
    """`variables` declares NAMES. The reference is explicit that values never live in the repo,
    so a declared name appearing outside a `${NAME}` placeholder is a committed secret."""
    v = man.get("variables")
    if v is None:
        return []
    bad = []
    if v.get("type") != "object" or "properties" not in v:
        bad.append(
            'variables must be {"type": "object", "properties": {...}} per the reference'
        )
    for vn, spec in (v.get("properties") or {}).items():
        if not spec.get("description"):
            bad.append(
                f"variable {vn}: no description — the user sees this when pasting a key"
            )
        for f in root.rglob("*.json"):
            if f.name == "plugin.json":
                continue
            txt = f.read_text(errors="replace")
            if vn in txt and "${" + vn + "}" not in txt:
                bad.append(
                    f"variable {vn} appears in {f.name} outside a ${{{vn}}} placeholder — "
                    f"secret values never live in the repo"
                )
    return bad


def check(root: pathlib.Path) -> list[str]:
    """Return a list of problems. Empty means submittable."""
    found = [root / m for m in MANIFESTS if (root / m).is_file()]
    if not found:
        return [
            f"no manifest at any documented location ({', '.join(MANIFESTS)}) — "
            f"the manifest is the plugin"
        ]
    bad = []
    if len(found) > 1:
        bad.append(
            f"{len(found)} manifests present ({', '.join(f.parent.name for f in found)}) — "
            f"ambiguous; ship exactly one format"
        )
    try:
        man = json.loads(found[0].read_text())
    except Exception as e:
        return bad + [f"{found[0].name} is not valid JSON: {e}"]
    bad += _check_identity(man)
    bad += _check_skills(root, man)
    bad += _check_variables(root, man)
    if not (root / "README.md").is_file():
        bad.append("README.md missing — required for a local-source submission")
    return bad


def selftest() -> int:
    """Fires-on-known-bad: a manifest that breaks every rule must produce every finding."""
    with tempfile.TemporaryDirectory() as td:
        bad_root = pathlib.Path(td)
        (bad_root / ".grok-plugin").mkdir(parents=True)
        atomic_write_text(
            (bad_root / ".grok-plugin" / "plugin.json"),
            json.dumps(
                {
                    "name": "Bad_Name",
                    "description": "TODO",
                    "keywords": ["ai", "bot"],
                    "skills": ["./skills/ghost"],
                }
            ),
        )
        (bad_root / "skills" / "loose").mkdir(parents=True)
        atomic_write_text(
            (bad_root / "skills" / "loose" / "SKILL.md"), "no frontmatter here\n"
        )
        problems = check(bad_root)
        expect = [
            "kebab-case",
            "placeholder",
            "homepage",
            "license",
            "generic keyword",
            "SKILL.md missing",
            "README.md missing",
        ]
        missed = [e for e in expect if not any(e in p for p in problems)]
        for p in problems:
            print(f"  [bad fixture] {p}")
        if missed:
            print(
                f"SELFTEST FAIL — checks that did not fire: {missed}", file=sys.stderr
            )
            return 1

        good = pathlib.Path(__file__).resolve().parents[1] / "plugin"
        real = check(good)
        if real:
            print(
                "SELFTEST FAIL — the repo's own plugin does not validate:",
                file=sys.stderr,
            )
            for p in real:
                print(f"  {p}", file=sys.stderr)
            return 1
    print(
        f"SELFTEST PASS — {len(expect)} check(s) fire on a known-bad manifest, "
        f"and plugin/ validates clean"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument(
        "--path", default=str(pathlib.Path(__file__).resolve().parents[1] / "plugin")
    )
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    problems = check(pathlib.Path(args.path))
    if problems:
        print(f"NOT SUBMITTABLE — {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        f"submittable: {args.path} passes every rule measured from the marketplace CONTRIBUTING"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
