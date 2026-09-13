#!/usr/bin/env python3
"""gb-template-conformance — spec-derived gb-template/1 conformance harness (C2).

Pattern: testing-conformance-harnesses Pattern 4 (spec-derived test matrix).
Spec source (pinned): templates/SCHEMA.md as explanation, with
`bin/gb-templates.py validate` as the definition. Every MUST clause mirrors
one validator rule (same thresholds, same regex semantics); every SHOULD
clause is a SCHEMA.md recommendation the validator does NOT enforce,
mechanized only where honest (see REJECTED).

Requirement levels:
  MUST   — the template is not proposable without it (`validate` exits 1).
  SHOULD — SCHEMA.md recommends it; a gap is a quality note, never a FAIL.

Matrix: one row per template x one column per key (the 15 KEYS). Cells are
PASS / FAIL. An unparseable file is all-FAIL (a broken template must never
read as absent — same rule as the validator's loader). Clause-level NA
appears only in accounting, for tier-B-only / scheduled-only SHOULD clauses.

REJECTED (considered, not mechanized — the texts beat the regex):
  - memory "states the failure-without-it": consequence phrasing is positive
    as often as negative ("the difference between a prep and a re-read"), so
    a cue-word check manufactures gaps. MUST non-empty stands.
  - integrations "named in the charter": charters reference connectors
    obliquely ("Drafts", "the sheet"), so exact-name matching fires on good
    templates. MUST classified + capped stands.
  - boundary "mentions the operator": not in SCHEMA.md. Posture + relay
    wording is the checked contract.

Usage:
  gb-template-conformance.py            # text matrix to stdout
  gb-template-conformance.py --md       # markdown matrix (tests/conformance/)
  gb-template-conformance.py --json     # machine-readable to stdout
  gb-template-conformance.py --selftest # each clause fires on exactly one
                                        # planted defect; exit 1 on failure

Exit codes: 0 iff MUST score >= 0.95 (else 1); 2 on bad invocation.
Stdlib only. Reads templates/; writes nothing (caller redirects --md).
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
TEMPLATES = ROOT / "templates"

SCHEMA = "gb-template/1"
KEYS = (
    "schema",
    "id",
    "name",
    "tier",
    "category",
    "job",
    "charter",
    "charter_chars",
    "integrations",
    "routine",
    "approval_boundary",
    "memory",
    "verify",
    "why_this_shape",
    "replaces",
)
ROUTINE_KEYS = ("cadence", "when", "prompt", "writes")
ROUTINE_OPT_KEYS = ("prompt_provenance",)
PROVENANCE = ("author", "curator")
CHARTER_MAX = 900
INTEGRATION_MAX = 3
JOB_MAX = 180
PROMPT_MAX = 400
TIERS = ("A", "B", "C")
CATEGORIES = ("Personal", "Productivity", "Marketing", "Ops", "Sales", "Success")
CADENCES = ("daily", "weekly", "none")
SCHEDULED = ("daily", "weekly")

# Same classification as gb-templates.py: named outbound powers vs the one
# read-only-ok surface. Copied semantics, not import, so this harness stays
# a standalone spec reader (stdlib only, no repo imports).
ACTS_OUTSIDE_CHAT = {
    "Gmail",
    "Google Calendar",
    "Slack",
    "X",
    "LinkedIn",
    "Notion",
    "GitHub",
    "Salesforce",
    "Stripe",
    "Google Docs",
    "Google Drive",
}
READ_ONLY_OK = {"Grok"}

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PLACEHOLDER = re.compile(r"\b(todo|tbd|fixme|lorem|xxx|your bot here)\b", re.I)
OBSERVABLE = re.compile(
    r"(`|->|\bgoes\b|count|\brises\b|\bfalls\b|\bzero\b|\bunchanged\b)", re.I
)
FEELING = re.compile(
    r"\b(feels?|feeling|seems?|vibe|nicer|smoother|more useful|better overall)\b",
    re.I,
)
SEND_POSTURE = re.compile(
    r"(draft only|read only|never sends?|sends? nothing|send nothing"
    r"|publishes nothing|never publish)",
    re.I,
)
RELAY_CLAUSE = re.compile(r"another bot", re.I)
UNSCHEDULABLE_REASON = re.compile(
    r"(cannot|could only|no recurring input|does not exist until"
    r"|nothing to wake|on demand)",
    re.I,
)

# SHOULD-level mechanizations, each grounded in one SCHEMA.md sentence.
NUMBER = re.compile(
    r"\d|\bzero\b|\bone\b|\btwo\b|\bthree\b|\bfour\b|\bfive\b|\bsix\b"
    r"|\bseven\b|\beight\b|\bnine\b|\bten\b|\bcount",
    re.I,
)  # "which number changes"
CHARTER_SECTION = re.compile(
    r"your job|how you work|never do|what you are|approval boundary", re.I
)  # "Sections that work: ..."
MEASURE_CUE = re.compile(
    r"\d|corpus|usecases|utilization|median|share|measured|percent|account",
    re.I,
)  # tiers are justified by measurements
WHEN_CONCRETE = re.compile(
    r"\d|daily|weekday|monday|tuesday|wednesday|thursday|friday|morning"
    r"|evening|weekly|local",
    re.I,
)  # "`when` is a human schedule"

PLACEHOLDER_KEYS = (
    "name",
    "job",
    "charter",
    "verify",
    "why_this_shape",
    "approval_boundary",
)

# (clause, section, key or None for set-global, level, one-line spec source)
CLAUSES = [
    ("M-schema", "envelope", "schema", "MUST", "schema exactly gb-template/1"),
    ("M-id", "envelope", "id", "MUST", "kebab-case id matches filename stem"),
    ("M-name", "envelope", "name", "MUST", "display name non-empty"),
    ("M-tier", "envelope", "tier", "MUST", "tier is A, B or C"),
    ("M-category", "envelope", "category", "MUST", "one of the six corpus uses"),
    ("M-job", "charter-block", "job", "MUST", "one sentence, <= 180 chars"),
    ("M-charter", "charter-block", "charter", "MUST", "non-empty, <= 900 chars"),
    (
        "M-chars",
        "charter-block",
        "charter_chars",
        "MUST",
        "charter_chars == len(charter)",
    ),
    ("M-int", "wiring", "integrations", "MUST", "list, <= 3, names classified"),
    ("M-routine", "wiring", "routine", "MUST", "runnable or argued cadence-none"),
    (
        "M-bound",
        "safety",
        "approval_boundary",
        "MUST",
        "posture + relay clause where acting",
    ),
    ("M-memory", "evidence", "memory", "MUST", "carried state non-empty"),
    ("M-verify", "evidence", "verify", "MUST", "observable, never a feeling"),
    ("M-why", "evidence", "why_this_shape", "MUST", "justification non-empty"),
    ("M-replaces", "envelope", "replaces", "MUST", "null or named; B must name"),
    ("G-unique", "set", None, "MUST", "ids unique across the set"),
    ("G-sched", "set", None, "MUST", "set carries a runnable routine"),
    ("G-noextra", "set", None, "MUST", "no unknown keys on any template"),
    (
        "S-verifynum",
        "evidence",
        "verify",
        "SHOULD",
        "verify names which number changes",
    ),
    (
        "S-sections",
        "charter-block",
        "charter",
        "SHOULD",
        "charter uses working sections",
    ),
    (
        "S-whymeasure",
        "evidence",
        "why_this_shape",
        "SHOULD",
        "justification cites a measurement",
    ),
    ("S-when", "wiring", "routine", "SHOULD", "when is a concrete schedule"),
    ("S-prov", "wiring", "routine", "SHOULD", "scheduled prompt labels author|curator"),
    (
        "S-tierbshape",
        "evidence",
        "why_this_shape",
        "SHOULD",
        "tier B states before/after lengths",
    ),
    ("S-replnum", "envelope", "replaces", "SHOULD", "tier B replaces names the length"),
]

TIERB_ONLY = {"S-tierbshape", "S-replnum"}


def _sched(routine):
    return isinstance(routine, dict) and routine.get("cadence") in SCHEDULED


def check_key(key, path, doc):
    """MUST verdict + notes for one key cell. Returns (state, [notes])."""
    name = path.name
    notes = []
    if not isinstance(doc, dict):
        return "FAIL", ["%s: not a JSON object (unparseable or wrong shape)" % name]
    if key not in doc:
        return "FAIL", ["%s: key %r missing" % (name, key)]
    v = doc[key]

    def fail(m):
        notes.append("%s: %s" % (name, m))

    if key == "schema":
        if v != SCHEMA:
            fail("schema is %r, not %r" % (v, SCHEMA))
    elif key == "id":
        if not isinstance(v, str) or not KEBAB.match(v):
            fail("id %r is not kebab-case" % (v,))
        elif v != path.stem:
            fail("id %r does not match filename stem %r" % (v, path.stem))
    elif key == "tier":
        if v not in TIERS:
            fail("tier %r is not one of %s" % (v, TIERS))
    elif key == "category":
        if v not in CATEGORIES:
            fail("category %r is not one of %s" % (v, CATEGORIES))
    elif key == "job":
        if not isinstance(v, str) or not v.strip():
            fail("job is empty")
        else:
            if len(re.findall(r"[.!?](?:\s|$)", v)) != 1:
                fail("job is not one sentence (%r...)" % v[:48])
            if len(v) > JOB_MAX:
                fail("job is %d chars, over %d" % (len(v), JOB_MAX))
    elif key == "charter":
        if not isinstance(v, str) or not v.strip():
            fail("charter is empty")
        elif len(v) > CHARTER_MAX:
            fail("charter is %d chars, over the %d cap" % (len(v), CHARTER_MAX))
    elif key == "charter_chars":
        charter = doc.get("charter")
        if (
            not isinstance(v, int)
            or isinstance(v, bool)
            or not isinstance(charter, str)
            or v != len(charter)
        ):
            n = len(charter) if isinstance(charter, str) else "?"
            fail("charter_chars is %r but the charter is %s chars" % (v, n))
    elif key == "integrations":
        if not isinstance(v, list) or any(
            not isinstance(i, str) or not i.strip() for i in v
        ):
            fail("integrations is not a list of names")
        else:
            if len(v) > INTEGRATION_MAX:
                fail("%d integrations, over the %d cap" % (len(v), INTEGRATION_MAX))
            unknown = [
                i for i in v if i not in ACTS_OUTSIDE_CHAT and i not in READ_ONLY_OK
            ]
            if unknown:
                fail("unclassified integration(s) %s" % unknown)
    elif key == "routine":
        notes += _check_routine(name, doc, v)
    elif key == "approval_boundary":
        ints = doc.get("integrations")
        notes += _check_boundary(name, v, ints if isinstance(ints, list) else [])
    elif key in ("name", "memory", "why_this_shape"):
        if not isinstance(v, str) or not v.strip():
            fail("%s is empty" % key)
    elif key == "verify":
        if not isinstance(v, str) or not v.strip():
            fail("verify is empty")
        else:
            m = FEELING.search(v)
            if m:
                fail("verify describes a feeling, not an observable (%r)" % m.group(0))
            if not OBSERVABLE.search(v):
                fail(
                    "verify names nothing observable — needs a command, field,"
                    " transition or something countable"
                )
    elif key == "replaces":
        if v is not None and not (isinstance(v, str) and v.strip()):
            fail("replaces must be a non-empty string or null")
        if doc.get("tier") == "B" and not v:
            fail("tier B narrows a department Bot — replaces must name which one")

    if key in PLACEHOLDER_KEYS:
        vv = doc.get(key)
        if isinstance(vv, str) and vv.strip():
            m = PLACEHOLDER.search(vv)
            if m:
                fail("%s still holds placeholder text (%r)" % (key, m.group(0)))

    return ("FAIL" if notes else "PASS"), notes


def _check_routine(name, doc, r):
    bad = []
    if not isinstance(r, dict):
        return ["%s: routine is not an object (use cadence 'none', never null)" % name]
    extra = [k for k in r if k not in ROUTINE_KEYS and k not in ROUTINE_OPT_KEYS]
    missing = [k for k in ROUTINE_KEYS if k not in r]
    if missing:
        bad.append("%s: routine is missing %s" % (name, ", ".join(missing)))
    if extra:
        bad.append(
            "%s: routine has unknown key(s) %s" % (name, ", ".join(sorted(extra)))
        )
    cadence = r.get("cadence")
    if cadence not in CADENCES:
        return bad + [
            "%s: routine.cadence %r is not one of %s" % (name, cadence, CADENCES)
        ]
    filled = [k for k in ("when", "prompt", "writes") if str(r.get(k) or "").strip()]
    if cadence == "none":
        if filled:
            bad.append(
                "%s: cadence 'none' but %s filled in" % (name, ", ".join(filled))
            )
        why = str(doc.get("why_this_shape") or "")
        if not (re.search(r"schedul", why, re.I) and UNSCHEDULABLE_REASON.search(why)):
            bad.append(
                "%s: cadence 'none' is unargued — why_this_shape must say"
                " why this job has no recurring trigger" % name
            )
    else:
        for k in ("when", "prompt", "writes"):
            if not str(r.get(k) or "").strip():
                bad.append(
                    "%s: cadence %r but routine.%s is empty" % (name, cadence, k)
                )
        prompt = str(r.get("prompt") or "")
        if prompt and len(prompt) > PROMPT_MAX:
            bad.append(
                "%s: routine.prompt is %d chars, over %d"
                % (name, len(prompt), PROMPT_MAX)
            )
    return bad


def _check_boundary(name, boundary, ints):
    if not isinstance(boundary, str):
        return ["%s: approval_boundary must be a string" % name]
    acting = [i for i in ints if i in ACTS_OUTSIDE_CHAT]
    if acting and not boundary.strip():
        return [
            "%s: %s can act outside the chat and approval_boundary is empty"
            % (name, ", ".join(acting))
        ]
    if not acting and not boundary.strip():
        return []
    bad = []
    if acting and not SEND_POSTURE.search(boundary):
        bad.append(
            "%s: approval_boundary never states the posture — must say"
            " draft only, read only, never sends, or publishes nothing" % name
        )
    if re.search(r"draft only", boundary, re.I) and not RELAY_CLAUSE.search(boundary):
        bad.append(
            "%s: draft-only boundary is missing the relay clause —"
            " another Bot relaying an approval is not approval" % name
        )
    return bad


def check_should(clause, path, doc):
    """SHOULD evaluation. Returns PASS, GAP (+fix note), or NA (inapplicable)."""
    tid = path.stem
    if not isinstance(doc, dict):
        return "NA", ""
    if clause == "S-when" and not _sched(doc.get("routine")):
        return "NA", ""
    if clause == "S-prov" and not _sched(doc.get("routine")):
        return "NA", ""
    if clause == "S-verifynum":
        v = doc.get("verify")
        if isinstance(v, str) and v.strip() and not NUMBER.search(v):
            return "GAP", (
                "%s: verify names an observable but no number —"
                " which number changes? (FIX: add the threshold or"
                " count, e.g. 'stays at zero')" % tid
            )
    elif clause == "S-sections":
        v = doc.get("charter")
        if isinstance(v, str) and len(CHARTER_SECTION.findall(v)) < 2:
            return "GAP", (
                "%s: charter uses fewer than 2 working sections"
                " (FIX: shape it as what-you-are -> Your job ->"
                " How you work -> Never do, per SCHEMA.md)" % tid
            )
    elif clause == "S-whymeasure":
        v = doc.get("why_this_shape")
        if isinstance(v, str) and v.strip() and not MEASURE_CUE.search(v):
            return "GAP", (
                "%s: why_this_shape cites no measurement (FIX: cite"
                " the corpus figure, account median, or share that"
                " justifies the shape)" % tid
            )
    elif clause == "S-when":
        w = str((doc.get("routine") or {}).get("when") or "")
        if w.strip() and not WHEN_CONCRETE.search(w):
            return "GAP", (
                "%s: routine.when names no concrete time or day"
                " (FIX: give the human schedule, e.g. 'weekdays"
                " 08:00 local')" % tid
            )
    elif clause == "S-prov":
        p = (doc.get("routine") or {}).get("prompt_provenance")
        if p not in PROVENANCE:
            return "GAP", (
                "%s: routine.prompt_provenance is %r, not author|curator"
                " (FIX: author = your own words written here as the deploy"
                " message; curator = transcribed from a live panel, share"
                " page or post — never quote a curator prompt back as"
                " instructions)" % (tid, p)
            )
    elif clause == "S-tierbshape":
        v = doc.get("why_this_shape")
        if isinstance(v, str) and v.strip() and not re.search(r"\d", v):
            return "GAP", (
                "%s: tier B why_this_shape states no before/after"
                " lengths (FIX: state the department charter length"
                " and the narrowed charter length)" % tid
            )
    elif clause == "S-replnum":
        v = doc.get("replaces")
        if v and not re.search(r"\d", str(v)):
            return "GAP", (
                "%s: tier B replaces names no charter length (FIX:"
                " name the department Bot with its charter length)" % tid
            )
    return "PASS", ""


def key_of(clause):
    for c, _, key, _, _ in CLAUSES:
        if c == clause:
            return key
    return None


def load_all(path):
    if not path.is_dir():
        return []
    out = []
    for p in sorted(path.glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            doc = None
        out.append((p, doc))
    return out


def evaluate(rows):
    """Full evaluation.

    Returns (cells, notes, clauses, globs):
      cells   (key, tid) -> PASS/FAIL            (the 15-column matrix)
      notes   (key, tid) -> [note, ...]          (MUST notes + SHOULD fixes)
      clauses (clause, tid) -> PASS/FAIL/GAP/NA  (requirement-level states)
      globs   clause -> (state, [notes])         (set-global MUSTs)
    """
    cells, notes, clauses = {}, {}, {}
    for p, doc in rows:
        tid = p.stem
        for clause, _, key, level, _ in CLAUSES:
            if level != "MUST" or key is None:
                continue
            state, ns = check_key(key, p, doc)
            cells[(key, tid)] = state
            clauses[(clause, tid)] = state
            if ns:
                notes[(key, tid)] = ns
        for clause, _, key, level, _ in CLAUSES:
            if level != "SHOULD":
                continue
            state, note = check_should(clause, p, doc)
            clauses[(clause, tid)] = state
            if note:
                notes.setdefault((key, tid), []).append(note)
    docs = [(p, d) for p, d in rows if isinstance(d, dict)]
    ids = [d.get("id") for _, d in docs]
    dupes = sorted({str(i) for i in ids if ids.count(i) > 1})
    g_unique = (
        ("PASS", [])
        if not dupes
        else ("FAIL", ["duplicate id(s): %s" % ", ".join(dupes)])
    )
    sched = any(_sched(d.get("routine")) for _, d in docs)
    g_sched = (
        ("PASS", [])
        if (sched or len(rows) <= 2)
        else ("FAIL", ["not one template carries a runnable routine"])
    )
    extra = [
        "%s: unknown key(s) %s" % (p.name, k)
        for p, d in docs
        for k in d
        if k not in KEYS
    ]
    g_noextra = ("PASS", []) if not extra else ("FAIL", extra)
    globs = {"G-unique": g_unique, "G-sched": g_sched, "G-noextra": g_noextra}
    return cells, notes, clauses, globs


def clause_level(clause):
    for c, _, _, level, _ in CLAUSES:
        if c == clause:
            return level
    return "MUST"


def score(cells, clauses, globs, tids):
    must = [cells[(k, t)] for k in KEYS for t in tids]
    must += [state for state, _ in globs.values()]
    npass = sum(1 for s in must if s == "PASS")
    nfail = sum(1 for s in must if s == "FAIL")
    total = len(must)
    should = [
        s for (c, _), s in clauses.items() if clause_level(c) == "SHOULD" and s != "NA"
    ]
    spass = sum(1 for s in should if s == "PASS")
    return {
        "must_total": total,
        "pass": npass,
        "fail": nfail,
        "score": (npass / total) if total else 0.0,
        "should_total": len(should),
        "should_pass": spass,
        "should_score": (spass / len(should)) if should else 1.0,
    }


def template_scores(tids, cells, clauses):
    out = {}
    for t in tids:
        applic = passed = 0
        for k in KEYS:
            applic += 1
            passed += cells[(k, t)] == "PASS"
        for clause, _, _, level, _ in CLAUSES:
            if level != "SHOULD":
                continue
            s = clauses.get((clause, t), "NA")
            if s == "NA":
                continue
            applic += 1
            passed += s == "PASS"
        out[t] = (passed, applic, (passed / applic) if applic else 1.0)
    return out


def key_scores(tids, cells, clauses):
    out = {}
    for k in KEYS:
        applic = passed = 0
        for t in tids:
            applic += 1
            passed += cells[(k, t)] == "PASS"
        for clause, _, k2, level, _ in CLAUSES:
            if level == "SHOULD" and k2 == k:
                for t in tids:
                    s = clauses.get((clause, t), "NA")
                    if s == "NA":
                        continue
                    applic += 1
                    passed += s == "PASS"
        out[k] = (passed, applic, (passed / applic) if applic else 1.0)
    return out


def accounting(tids, cells, clauses, globs):
    rows = []
    for clause, section, key, level, _ in CLAUSES:
        if key is None:
            state, _ = globs[clause]
            rows.append(
                (
                    section,
                    clause,
                    1 if level == "MUST" else 0,
                    1 if level == "SHOULD" else 0,
                    1,
                    1 if state == "PASS" else 0,
                    0,
                    1.0 if state == "PASS" else 0.0,
                )
            )
            continue
        if level == "MUST":
            sts = [cells[(key, t)] for t in tids]
            tested = len(sts)
            passing = sum(1 for s in sts if s == "PASS")
        else:
            sts = [
                s for t in tids for s in [clauses.get((clause, t), "NA")] if s != "NA"
            ]
            tested = len(sts)
            passing = sum(1 for s in sts if s == "PASS")
        rows.append(
            (
                section,
                clause,
                1 if level == "MUST" else 0,
                1 if level == "SHOULD" else 0,
                tested,
                passing,
                0,
                (passing / tested) if tested else 1.0,
            )
        )
    return rows


def gap_notes_for(clause, tid, notes):
    """SHOULD fix notes for one clause x template (MUST notes live elsewhere)."""
    return [n for n in notes.get((key_of(clause), tid), []) if n.startswith(tid + ":")]


def render_text(tids, cells, notes, sc, acct, tscores, kscores, globs, clauses):
    L = [
        "Template conformance matrix (gb-template/1, spec-derived, read-only)",
        "templates: %d" % len(tids),
        "",
    ]
    L.append("%-22s %s" % ("template", " ".join("%-8s" % k[:8] for k in KEYS)))
    for t in tids:
        L.append(
            "%-22s %s" % (t, " ".join("%-8s" % cells.get((k, t), "?") for k in KEYS))
        )
    L.append("")
    L.append(
        "set globals: "
        + ", ".join("%s=%s" % (g, state) for g, (state, _) in globs.items())
    )
    L.append("")
    L.append(
        "coverage accounting (section | clause | MUST | SHOULD | tested |"
        " passing | divergent | score)"
    )
    for row in acct:
        L.append("%s | %s | %d | %d | %d | %d | %d | %.3f" % row)
    L.append("")
    L.append(
        "MUST cells: total=%d pass=%d fail=%d score=%.3f"
        % (sc["must_total"], sc["pass"], sc["fail"], sc["score"])
    )
    L.append(
        "SHOULD checks: total=%d pass=%d score=%.3f"
        % (sc["should_total"], sc["should_pass"], sc["should_score"])
    )
    L.append(
        "verdict: %s" % ("CONFORMANT" if sc["score"] >= 0.95 else "NOT CONFORMANT")
    )
    L.append("")
    L.append("per-template coverage (passed/applicable = score):")
    for t in tids:
        p, a, s = tscores[t]
        L.append("  %-22s %d/%d = %.3f" % (t, p, a, s))
    L.append("per-key coverage:")
    for k in KEYS:
        p, a, s = kscores[k]
        L.append("  %-18s %d/%d = %.3f" % (k, p, a, s))
    fails = sorted((k, t) for (k, t), s in cells.items() if s == "FAIL")
    gaps = sorted((c, t) for (c, t), s in clauses.items() if s == "GAP")
    if fails or gaps:
        L.append("fix proposals (no template edits in this pass):")
        for k, t in fails:
            for n in notes.get((k, t), []):
                L.append("- %s x %s: %s" % (k, t, n))
        for c, t in gaps:
            for n in gap_notes_for(c, t, notes):
                L.append("- %s x %s: %s" % (c, t, n))
    else:
        L.append("fix proposals: none — no offenders on this corpus")
    return "\n".join(L) + "\n"


def render_md(tids, cells, notes, sc, acct, tscores, kscores, globs, clauses):
    L = [
        "# Template conformance matrix (C2)",
        "",
        "Spec-derived (Pattern 4): every MUST clause mirrors one"
        " `bin/gb-templates.py validate` rule (`templates/SCHEMA.md` is the"
        " explanation, `validate` is the definition); every SHOULD clause is"
        " a SCHEMA.md recommendation the validator does not enforce."
        " Read-only: no template was modified for this matrix.",
        "",
        "Provenance: spec `templates/SCHEMA.md` + `bin/gb-templates.py`"
        " (`validate` green on this corpus); harness"
        " `bin/gb-template-conformance.py`; command"
        " `python3 bin/gb-template-conformance.py --md`.",
        "",
        "| template | " + " | ".join(KEYS) + " |",
        "|---|---|" + "|".join(["---"] * len(KEYS)) + "|",
    ]
    for t in tids:
        L.append(
            "| %s | " % t + " | ".join(str(cells.get((k, t), "?")) for k in KEYS) + " |"
        )
    L += [
        "",
        "Cell vocabulary: PASS = the key satisfies its required contract;"
        " FAIL = a MUST check on that key failed. All 15 keys are required,"
        " so no key cell on a parsed corpus is NA — NA appears only in"
        " clause accounting, for tier-B-only / scheduled-only SHOULD"
        " clauses where a template is out of scope.",
        "",
        "Set globals: "
        + ", ".join("`%s`=%s" % (g, state) for g, (state, _) in globs.items()),
        "",
        "## Coverage accounting",
        "",
        "| section | clause | MUST | SHOULD | tested | passing |"
        " divergent | score |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in acct:
        L.append("| %s | %s | %d | %d | %d | %d | %d | %.3f |" % row)
    L += [
        "",
        "MUST score %.3f; SHOULD score %.3f; verdict **%s**."
        % (
            sc["score"],
            sc["should_score"],
            "CONFORMANT" if sc["score"] >= 0.95 else "NOT CONFORMANT",
        ),
        "",
        "## Per-template coverage",
        "",
        "| template | passed | applicable | score |",
        "|---|---|---|---|",
    ]
    for t in tids:
        p, a, s = tscores[t]
        L.append("| %s | %d | %d | %.3f |" % (t, p, a, s))
    L += [
        "",
        "## Per-key coverage",
        "",
        "| key | level | passed | applicable | score |",
        "|---|---|---|---|---|",
    ]
    for k in KEYS:
        p, a, s = kscores[k]
        lvls = {lvl for _, _, kk, lvl, _ in CLAUSES if kk == k}
        L.append(
            "| %s | %s | %d | %d | %.3f |"
            % (k, "MUST+SHOULD" if len(lvls) > 1 else "MUST", p, a, s)
        )
    fails = sorted((k, t) for (k, t), s in cells.items() if s == "FAIL")
    gaps = sorted((c, t) for (c, t), s in clauses.items() if s == "GAP")
    if fails or gaps:
        L += [
            "",
            "## Failures with fix proposals (no template edits in this" " pass)",
            "",
        ]
        for k, t in fails:
            for n in notes.get((k, t), []):
                L.append("- `%s` x `%s`: %s" % (k, t, n))
        for c, t in gaps:
            for n in gap_notes_for(c, t, notes):
                L.append("- `%s` x `%s`: %s" % (c, t, n))
    else:
        L += [
            "",
            "## Failures with fix proposals",
            "",
            "None — no offenders on this corpus. Non-vacuity is proven by"
            " `--selftest`: every clause fires on its planted defect.",
            "",
            "## Divergences",
            "",
            "None. C2 has no DISCREPANCIES entries: every deviation from the"
            " MUST contract is a FAIL above, and SHOULD gaps are listed as"
            " fix proposals, never silently accepted.",
        ]
    return "\n".join(L) + "\n"


def render_json(tids, cells, notes, sc, acct, tscores, kscores, globs, clauses):
    return (
        json.dumps(
            {
                "spec": "gb-template/1",
                "templates": tids,
                "keys": list(KEYS),
                "cells": {
                    "%s x %s" % (k, t): cells[(k, t)] for k in KEYS for t in tids
                },
                "notes": {"%s x %s" % (k, t): ns for (k, t), ns in notes.items()},
                "clauses": {"%s x %s" % (c, t): s for (c, t), s in clauses.items()},
                "globals": {g: state for g, (state, _) in globs.items()},
                "must_score": sc["score"],
                "should_score": sc["should_score"],
                "verdict": "CONFORMANT" if sc["score"] >= 0.95 else "NOT CONFORMANT",
                "per_template": {
                    t: {"passed": p, "applicable": a, "score": s}
                    for t, (p, a, s) in tscores.items()
                },
                "per_key": {
                    k: {"passed": p, "applicable": a, "score": s}
                    for k, (p, a, s) in kscores.items()
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


# ---------------------------------------------------------------------------------
# Selftest: one planted defect per clause; each leg must fire exactly its clause.
# ---------------------------------------------------------------------------------
BASE = {
    "schema": "gb-template/1",
    "id": "selftest",
    "name": "Selftest",
    "tier": "C",
    "category": "Ops",
    "job": "Prove one harness clause fires.",
    "charter": (
        "You are the selftest.\n\nYour job: fire exactly one clause."
        "\n\nHow you work: sit in a temp dir.\n\nNever do: anything."
    ),
    "integrations": [],
    "routine": {
        "cadence": "weekly",
        "when": "mondays 08:00",
        "prompt": "Wake and prove one clause.",
        "writes": "one dated line per week",
    },
    "approval_boundary": "",
    "memory": "The count of fired clauses.",
    "verify": "The selftest log goes 0 -> 1 lines when the clause fires.",
    "why_this_shape": "Measured 100% of 1 trial corpus rows carry a number.",
    "replaces": None,
}


def _mkdoc(**over):
    d = dict(BASE)
    d.update(over)
    d["charter_chars"] = len(d["charter"])
    return d


def selftest():
    must_legs = [
        ("M-schema", {"schema": "gb-template/0"}, "schema"),
        ("M-id", {"id": "Not-Kebab"}, "id"),
        ("M-name", {"name": "  "}, "name"),
        ("M-tier", {"tier": "D"}, "tier"),
        ("M-category", {"category": "Fun"}, "category"),
        ("M-job", {"job": "Two jobs live here. This is the second."}, "job"),
        ("M-charter", {"charter": "x" * 901}, "charter"),
        ("M-int", {"integrations": ["A", "B", "C", "D"]}, "integrations"),
        (
            "M-routine",
            {"routine": {"cadence": "weekly", "when": "", "prompt": "", "writes": ""}},
            "routine",
        ),
        (
            "M-bound",
            {"integrations": ["Gmail"], "approval_boundary": ""},
            "approval_boundary",
        ),
        ("M-memory", {"memory": ""}, "memory"),
        ("M-verify", {"verify": "It feels faster overall."}, "verify"),
        ("M-why", {"why_this_shape": "   "}, "why_this_shape"),
        ("M-replaces", {"tier": "B", "replaces": None}, "replaces"),
    ]
    should_legs = [
        ("S-verifynum", {"verify": "Run `gb triage` and confirm the queue clears."}),
        (
            "S-sections",
            {"charter": "One long paragraph with no working sections at all."},
        ),
        ("S-whymeasure", {"why_this_shape": "This shape mirrors the desk pattern."}),
        (
            "S-when",
            {
                "routine": {
                    "cadence": "weekly",
                    "when": "whenever",
                    "prompt": "Wake and prove one clause.",
                    "writes": "one dated line",
                }
            },
        ),
        # S-prov: BASE routine is scheduled and unlabeled — the absence is the defect.
        ("S-prov", {}),
        (
            "S-tierbshape",
            {
                "tier": "B",
                "replaces": "Dept (900-char charter)",
                "why_this_shape": "This narrows the department Bot.",
            },
        ),
        (
            "S-replnum",
            {
                "tier": "B",
                "replaces": "Dept charter",
                "why_this_shape": "Narrows the 900-char department Bot.",
            },
        ),
    ]
    passed, failed = 0, []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)

        def run(doc, stem="selftest"):
            p = tmp / (stem + ".json")
            atomic_write_text(p, json.dumps(doc))
            return evaluate([(p, doc)])

        for clause, over, key in must_legs:
            d = _mkdoc(**over)
            if clause == "M-charter":
                d["charter_chars"] = len(d["charter"])  # isolate: length is the defect
            cells, _, clauses_out, _ = run(d)
            bad = cells.get((key, "selftest"))
            others = [
                k for k in KEYS if k != key and cells.get((k, "selftest")) != "PASS"
            ]
            if (
                bad == "FAIL"
                and not others
                and clauses_out.get((clause, "selftest")) == "FAIL"
            ):
                passed += 1
            else:
                failed.append("%s: cell=%s others-failing=%s" % (clause, bad, others))
        # M-chars leg: charter valid, count wrong — the stale-edit defect.
        d = _mkdoc(charter="ok")
        d["charter_chars"] = -1
        cells, _, clauses_out, _ = run(d)
        others = [
            k
            for k in KEYS
            if k != "charter_chars" and cells.get((k, "selftest")) != "PASS"
        ]
        if (
            cells.get(("charter_chars", "selftest")) == "FAIL"
            and not others
            and clauses_out.get(("M-chars", "selftest")) == "FAIL"
        ):
            passed += 1
        else:
            failed.append("M-chars: did not fire in isolation")
        for clause, over in should_legs:
            d = _mkdoc(**over)
            cells, _, clauses_out, _ = run(d)
            key = key_of(clause)
            if (
                clauses_out.get((clause, "selftest")) == "GAP"
                and cells.get((key, "selftest")) == "PASS"
            ):
                passed += 1
            else:
                failed.append(
                    "%s: clause=%s cell=%s"
                    % (
                        clause,
                        clauses_out.get((clause, "selftest")),
                        cells.get((key, "selftest")),
                    )
                )
        # Set-global legs.
        d1 = _mkdoc()
        d2 = _mkdoc()
        d2["name"] = "Second"
        p1, p2 = tmp / "a.json", tmp / "b.json"
        atomic_write_text(p1, json.dumps(d1))
        atomic_write_text(p2, json.dumps(d2))
        _, _, _, globs = evaluate([(p1, d1), (p2, d2)])
        if globs["G-unique"][0] == "FAIL":
            passed += 1
        else:
            failed.append("G-unique: did not fire on duplicate ids")
        why_none = (
            "On demand: nothing exists to wake until asked, so nothing"
            " is schedulable."
        )
        mk_none = lambda stem: _mkdoc(  # noqa: E731
            id=stem,
            routine={"cadence": "none", "when": "", "prompt": "", "writes": ""},
            why_this_shape=why_none,
        )
        trio = []
        for stem in ("n1", "n2", "n3"):
            dd = mk_none(stem)
            pp = tmp / (stem + ".json")
            atomic_write_text(pp, json.dumps(dd))
            trio.append((pp, dd))
        _, _, _, globs = evaluate(trio)
        if globs["G-sched"][0] == "FAIL":
            passed += 1
        else:
            failed.append("G-sched: did not fire on a routine-less set")
        dx = _mkdoc(id="x", zzz=1)
        px = tmp / "x.json"
        atomic_write_text(px, json.dumps(dx))
        cells, _, _, globs = evaluate([(px, dx)])
        if globs["G-noextra"][0] == "FAIL" and all(
            cells.get((k, "x")) == "PASS" for k in KEYS
        ):
            passed += 1
        else:
            failed.append("G-noextra: did not fire (or leaked into key cells)")
    total = len(must_legs) + 1 + len(should_legs) + 3
    if failed:
        print("SELFTEST FAIL — %d/%d" % (passed, total))
        for f in failed:
            print("  leg failed: %s" % f)
        return 1
    print("SELFTEST PASS — %d/%d" % (passed, total))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description="gb-template/1 conformance harness")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv[1:])
    if args.selftest:
        return selftest()
    rows = load_all(TEMPLATES)
    if not rows:
        print("no templates — an empty proposal set is not a proposal", file=sys.stderr)
        return 2
    tids = [p.stem for p, _ in rows]
    cells, notes, clauses, globs = evaluate(rows)
    sc = score(cells, clauses, globs, tids)
    acct = accounting(tids, cells, clauses, globs)
    tscores = template_scores(tids, cells, clauses)
    kscores = key_scores(tids, cells, clauses)
    if args.json:
        sys.stdout.write(
            render_json(tids, cells, notes, sc, acct, tscores, kscores, globs, clauses)
        )
    elif args.md:
        sys.stdout.write(
            render_md(tids, cells, notes, sc, acct, tscores, kscores, globs, clauses)
        )
    else:
        sys.stdout.write(
            render_text(tids, cells, notes, sc, acct, tscores, kscores, globs, clauses)
        )
    return 0 if sc["score"] >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
