#!/usr/bin/env python3
"""Docs vs producers drift check: ids and counts, not semantics.

TWO halves, both mechanical, both fail-closed.

1. NORMATIVE — AGENTS.md, GATES.md, and SCRIPTS.md against the one count tuple emitted by
   bin/gb-surface-gate.py --counts, plus GATES.md's version and Check-column ids. Each document
   must carry exactly one well-formed tuple; every field, the verdict-split arithmetic, and the
   mutation-coverage bound are checked.

2. SELF-DESCRIPTION — every human-facing doc the EXPORTER publishes at the root of the
   public tree, against the LIVE runner, by subprocess. Which docs travel is read out of
   `bin/gb-export-public.py` rather than guessed: the ALLOWLIST is parsed with `ast` and
   every `Member` whose destination is a root-level `*.md` is scanned. The counts are
   MEASURED, never hardcoded here:

     verbs      len(json(`gb capabilities --json`)["commands"])
     checks     json(`gb-surface-gate.py --counts --json`)["checks"]
     fixtures   json(`gb-surface-gate.py --counts --json`)["fixtures"]
     templates  json(`gb-templates.py stats --json`)["ours"]["templates"]
     producers  len(json(`gb info`)["producers"])

   This half exists because it was ABSENT and the public docs had drifted behind the
   producers while GATES.md stayed green: README.public.md said `24 checks` and
   `54/54 fixtures`, QUICKSTART.md said `19 verbs`, against a live 25 / 55 / 52.

   THE EXEMPTION. A doc may legitimately state a count that is not the total, when the
   same paragraph also states the total — "the 19 CORE verbs of 52 live" is true and must
   pass, while a bare "19 verbs" is drift and must fail. A paragraph that narrates PAST
   drift ("it claimed 44 fixtures ... ") is exempt for the same reason: it is reporting a
   number, not claiming it. Both exemptions are PRINTED, never silent — an exemption
   nobody can see is a hole.

   ENFORCED vs ADVISORY. The enforced forms are `N verbs|checks|fixtures|producers|
   templates` and the ratio `N/M <those>`. The adjectival forms a human also writes
   (`24-check gate`, `54-case corpus`, `54 cases`) are reported as ADVISORIES and do not
   fail the run, because this checker may not edit the prose it grades: when the advisory
   tier was first switched on it found three more drifted counts in README.public.md
   (`24-check` at L40, `54-case` at L235, `54 cases` at L259, against a live 25 / 55 / 55)
   and failing on them would have made a red gate the only way to report them. They have
   since been corrected; when this tier has stayed empty for a while, promote it.

Exit 0 when the docs and the producers agree, 1 on any drift. Deliberately blind to what
each row MEANS — semantic drift (a row describing old behavior) is a human review finding,
not a count mismatch.

`--json` prints a `gb-gatesdoc/1` envelope — the same verdict, plus the measured totals it
judged against, so a reader never has to re-derive them.

`--selftest` runs both the self-description rule and the per-document normative matrix against
inline known-good and known-bad fixtures with SYNTHETIC measurements. It proves current, stale,
malformed, arithmetic, missing-document, and extra-count cases offline.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
from types import ModuleType
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "GATES.md"
BIN = ROOT / "bin"
GATE = BIN / "gb-surface-gate.py"
EXPORTER = BIN / "gb-export-public.py"

# The two docs a stranger meets first. If the exporter ever stops shipping either of them
# the scan set would silently shrink and this check would pass by scanning nothing, so the
# derived set is held against this positive control.
# `packaging/AGENTS.public.md` joined this list on 2026-09-12. Until that day a clone carried NO
# agent file at all — not AGENTS.md, not CLAUDE.md, not .cursorrules, not .cursor/ — because the
# only AGENTS.md in the project is the 51 kB operator manual, excluded by name for containing one
# account's measurements. An agent landing in a clone greps for AGENTS.md, finds nothing, and
# invents a plan out of `bin/`. Listing it here means dropping it from the exporter is a FAILURE
# rather than a quiet regression, exactly as it already is for the README.
REQUIRED_DOCS: Tuple[str, ...] = (
    "packaging/README.public.md",
    "QUICKSTART.md",
    "packaging/AGENTS.public.md",
)

ENFORCED = "enforced"
ADVISORY = "advisory"


# ---------------------------------------------------------------------------------------------
# 1. The normative half: GATES.md vs the gate producer
# ---------------------------------------------------------------------------------------------
def load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gb_surface_gate", GATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{GATE} is not importable")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def normative_problems(text: str, gate: ModuleType) -> Tuple[List[str], List[str], str]:
    """Check GATES.md's ids and version; the count tuple is checked for all normative docs."""
    problems: List[str] = []

    doc_ids = re.findall(r"^\| \x60(g\d+-[a-z0-9-]+)\x60 \|", text, re.M)
    code_ids: List[str] = list(gate.CHECKS)
    if sorted(set(doc_ids)) != sorted(code_ids):
        problems.append(
            "check ids differ: doc-only=%s code-only=%s"
            % (
                sorted(set(doc_ids) - set(code_ids)),
                sorted(set(code_ids) - set(doc_ids)),
            )
        )
    if len(doc_ids) != len(set(doc_ids)):
        problems.append("doc Check column holds duplicate ids")

    version = str(gate.VERSION)
    m = re.search(r"Version \x60([\d.]+)\x60", text)
    if not m:
        problems.append("doc states no Version x.y.z to check against")
    elif m.group(1) != version:
        problems.append(f"doc claims {m.group(1)}, producer is {version}")

    return problems, code_ids, version


# ---------------------------------------------------------------------------------------------
# 2a. Which docs ship — read out of the exporter, not guessed
# ---------------------------------------------------------------------------------------------
# Docs that do NOT ship publicly but MUST still be checked. `AGENTS.md` is the operating
# manual every fresh agent is instructed to read end to end before its first tool call, and
# it is not a Member of the public export, so the exporter-derived set below cannot see it.
#
# It had drifted badly by 2026-09-12 and the drift was actively harmful, not cosmetic:
#   - "if it is not 8/8, the instrument is broken"          live: 55/55
#   - "16 fixtures; must stay 16/16"                        live: 55
#   - "--selftest is N/N — 12/12 ... 9/9 disables"          live: 55/55, 25 of 25
#   - the gate's nine check ids listed by name              live: 25 (g1..g25)
#   - "Roster — 10 Bots"                                    live: 13
#   - "2 760 gates evaluated, 1 208 enabled"                live: 2785 / 1224
#   - "Routines: 2 exist, 1 has now RUN"                    live: 3 exist, 2 have run
# and, worst, §2 said one routine had run while lever 9 said none had — the manual
# contradicted ITSELF about its own flagship metric, so a reader could take whichever number
# suited the paragraph. A wrong instruction file is worse than a wrong README: the README
# misinforms a stranger, this one misdirects every agent that touches the repo. Its headline
# also asserted "routines cannot [be created programmatically]", which is the single sentence
# that guarantees nobody builds the capability — and nobody did, for a day.
UNSHIPPED_ENFORCED: Tuple[Tuple[str, str], ...] = (("AGENTS.md", "AGENTS.md"),)

NORMATIVE_DOCS: Tuple[str, ...] = ("AGENTS.md", "GATES.md", "SCRIPTS.md")
COUNT_PREFIX = "Gate count tuple (producer:"
COUNT_PATTERN = re.compile(
    r"^Gate count tuple \(producer: \x60bin/gb-surface-gate\.py --counts\x60\): "
    r"\x60checks=(?P<checks>\d+) fixtures=(?P<fixtures>\d+) "
    r"GREEN=(?P<GREEN>\d+) RED=(?P<RED>\d+) ERROR=(?P<ERROR>\d+) "
    r"mutation-covered=(?P<mutation_covered>\d+)\x60\.$",
    re.M,
)
COUNT_RECOVERY_ARGV = ["bin/gb-surface-gate.py", "--counts"]


def count_fields(contract: Dict[str, object]) -> Dict[str, int]:
    split = contract.get("verdict_split")
    if not isinstance(split, dict):
        raise ValueError("producer tuple has no verdict_split object")
    fields = {
        "checks": contract.get("checks"),
        "fixtures": contract.get("fixtures"),
        "GREEN": split.get("GREEN"),
        "RED": split.get("RED"),
        "ERROR": split.get("ERROR"),
        "mutation_covered": contract.get("mutation_covered"),
    }
    if any(not isinstance(value, int) or value < 0 for value in fields.values()):
        raise ValueError("producer tuple fields must be non-negative integers")
    typed = {key: int(value) for key, value in fields.items()}
    if typed["GREEN"] + typed["RED"] + typed["ERROR"] != typed["fixtures"]:
        raise ValueError("producer verdict split does not equal fixture total")
    if typed["mutation_covered"] > typed["checks"]:
        raise ValueError("producer mutation-covered count exceeds enabled checks")
    return typed


def count_tuple_text(contract: Dict[str, object]) -> str:
    fields = count_fields(contract)
    tick = chr(96)
    return (
        f"Gate count tuple (producer: {tick}bin/gb-surface-gate.py --counts{tick}): "
        f"{tick}checks={fields['checks']} fixtures={fields['fixtures']} "
        f"GREEN={fields['GREEN']} RED={fields['RED']} ERROR={fields['ERROR']} "
        f"mutation-covered={fields['mutation_covered']}{tick}."
    )


def normative_count_problems(
    documents: Dict[str, Optional[str]],
    contract: Dict[str, object],
    fixture_id: str = "in-tree",
) -> Tuple[List[str], List[Dict[str, object]]]:
    """Require one exact producer tuple in each normative document."""
    expected = count_fields(contract)
    problems: List[str] = []
    records: List[Dict[str, object]] = []

    def record(
        document: str,
        claim_id: str,
        observed: object,
        wanted: object,
        verdict: str,
        started: float,
    ) -> None:
        records.append(
            {
                "producer_tuple": expected,
                "document": document,
                "path": document,
                "claim_id": claim_id,
                "expected": wanted,
                "observed": observed,
                "fixture_id": fixture_id,
                "verdict": verdict,
                "recovery_argv": COUNT_RECOVERY_ARGV,
                "exit": 0 if verdict == "GREEN" else 1,
                "timing_ms": round(
                    (datetime.datetime.now().timestamp() - started) * 1000, 3
                ),
            }
        )

    for document in NORMATIVE_DOCS:
        started = datetime.datetime.now().timestamp()
        text = documents.get(document)
        if text is None:
            problems.append(f"{document}: normative document is missing")
            record(document, "document-present", None, "present", "RED", started)
            continue
        candidates = [line for line in text.splitlines() if COUNT_PREFIX in line]
        matches = list(COUNT_PATTERN.finditer(text))
        if not candidates:
            problems.append(f"{document}: missing normative gate count tuple")
            record(
                document,
                "count-tuple-present",
                None,
                count_tuple_text(contract),
                "RED",
                started,
            )
            continue
        if len(candidates) != 1 or len(matches) > 1:
            problems.append(
                f"{document}: expected one normative gate count tuple, found {len(candidates)}"
            )
            record(
                document, "count-tuple-cardinality", len(candidates), 1, "RED", started
            )
            continue
        if not matches:
            problems.append(f"{document}: malformed normative gate count tuple")
            record(
                document,
                "count-tuple-shape",
                candidates[0],
                count_tuple_text(contract),
                "RED",
                started,
            )
            continue

        observed = {key: int(value) for key, value in matches[0].groupdict().items()}
        split_sum = observed["GREEN"] + observed["RED"] + observed["ERROR"]
        arithmetic_ok = split_sum == observed["fixtures"]
        if not arithmetic_ok:
            problems.append(
                f"{document}: verdict split sums to {split_sum}, fixture total is {observed['fixtures']}"
            )
        record(
            document,
            "verdict-sum",
            split_sum,
            observed["fixtures"],
            "GREEN" if arithmetic_ok else "RED",
            started,
        )
        coverage_ok = observed["mutation_covered"] <= observed["checks"]
        if not coverage_ok:
            problems.append(
                f"{document}: mutation-covered {observed['mutation_covered']} exceeds checks {observed['checks']}"
            )
        record(
            document,
            "mutation-bound",
            observed["mutation_covered"],
            f"<= {observed['checks']}",
            "GREEN" if coverage_ok else "RED",
            started,
        )
        for claim_id, wanted in expected.items():
            got = observed[claim_id]
            ok = got == wanted
            if not ok:
                problems.append(
                    f"{document}: {claim_id} claims {got}, producer has {wanted}"
                )
            record(document, claim_id, got, wanted, "GREEN" if ok else "RED", started)
    return problems, records


def shipped_root_docs(source: str) -> List[Tuple[str, str]]:
    """Every `Member(src, dest, why)` in the exporter whose DEST is a root-level `*.md`,
    plus the unshipped docs this repo enforces anyway (see UNSHIPPED_ENFORCED).

    Parsed rather than imported: the exporter is a 1200-line module with an import-time
    sys.path mutation, and this checker has no business running any of it. The generated
    `bin/` members are f-strings and are skipped automatically — they are producers, not
    prose."""
    pairs: List[Tuple[str, str]] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "Member":
            continue
        if len(node.args) < 2:
            continue
        src, dest = node.args[0], node.args[1]
        if not isinstance(src, ast.Constant) or not isinstance(dest, ast.Constant):
            continue
        if not isinstance(src.value, str) or not isinstance(dest.value, str):
            continue
        if dest.value.endswith(".md") and "/" not in dest.value:
            pairs.append((src.value, dest.value))
    for pair in UNSHIPPED_ENFORCED:
        if pair not in pairs:
            pairs.append(pair)
    return pairs


# ---------------------------------------------------------------------------------------------
# 2b. The live measurements
# ---------------------------------------------------------------------------------------------
class MeasureError(Exception):
    pass


def _json_obj(out: str) -> Dict[str, object]:
    obj = json.loads(out)
    if not isinstance(obj, dict):
        raise MeasureError("envelope is not a JSON object")
    return obj


def _len_of(key: str) -> Callable[[str], int]:
    """`len(envelope[key])` for a collection, list OR object. Both shapes are live:
    `gb info` lists its producers, while `gb capabilities` maps each verb to its summary —
    and the capabilities envelope grew three keys mid-session, so a probe pinned to one
    container type reports "absent" for a number that is right there."""

    def extract(out: str) -> int:
        value = _json_obj(out).get(key)
        if not isinstance(value, (list, dict)):
            raise MeasureError(
                f"envelope has no `{key}` collection (got {type(value).__name__})"
            )
        return len(value)

    return extract


def _templates(out: str) -> int:
    ours = _json_obj(out).get("ours")
    if not isinstance(ours, dict):
        raise MeasureError("envelope has no `ours` object")
    value = ours.get("templates")
    if not isinstance(value, int):
        raise MeasureError("envelope has no `ours.templates` integer")
    return value


def _int_of(key: str) -> Callable[[str], int]:
    def extract(out: str) -> int:
        value = _json_obj(out).get(key)
        if not isinstance(value, int):
            raise MeasureError(f"envelope has no `{key}` integer")
        return value

    return extract


@dataclasses.dataclass(frozen=True)
class Probe:
    metric: str
    argv: Tuple[str, ...]
    timeout: float
    extract: Callable[[str], int]


# `gb` is invoked through the interpreter running this file so a clone without the
# executable bit still measures. Every child has a deadline; a probe that can hang is a
# checker that can be ignored.
PROBES: Tuple[Probe, ...] = (
    Probe("verbs", ("bin/gb", "capabilities", "--json"), 120.0, _len_of("commands")),
    Probe(
        "checks",
        ("bin/gb-surface-gate.py", "--counts", "--json"),
        300.0,
        _int_of("checks"),
    ),
    Probe(
        "fixtures",
        ("bin/gb-surface-gate.py", "--counts", "--json"),
        300.0,
        _int_of("fixtures"),
    ),
    Probe("templates", ("bin/gb-templates.py", "stats", "--json"), 120.0, _templates),
    Probe("producers", ("bin/gb", "info"), 120.0, _len_of("producers")),
)

_PROBE_CACHE: Dict[Tuple[str, ...], subprocess.CompletedProcess[str]] = {}


def measure(metric: str) -> int:
    """Run the one producer that owns this number and read it back. Never recounted here:
    a checker that re-implements the count can only prove itself right."""
    probe = next((p for p in PROBES if p.metric == metric), None)
    if probe is None:
        raise MeasureError(f"no probe measures `{metric}`")
    path = ROOT / probe.argv[0]
    if not path.is_file():
        raise MeasureError(f"{probe.argv[0]} absent")
    argv = [sys.executable, str(path), *probe.argv[1:]]
    proc = _PROBE_CACHE.get(probe.argv)
    if proc is None:
        try:
            proc = subprocess.run(
                argv,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=probe.timeout,
            )
        except subprocess.TimeoutExpired:
            raise MeasureError(
                f"`{' '.join(probe.argv)}` exceeded {probe.timeout:.0f}s"
            )
        except OSError as exc:
            raise MeasureError(f"`{' '.join(probe.argv)}` did not start: {exc}")
        _PROBE_CACHE[probe.argv] = proc
    try:
        if proc.returncode != 0:
            raise MeasureError("producer returned nonzero")
        return probe.extract(proc.stdout)
    except (MeasureError, ValueError, KeyError) as exc:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        hint = tail[-1][:160] if tail else "no output"
        raise MeasureError(
            f"`{' '.join(probe.argv)}` (rc={proc.returncode}): {exc} — last line: {hint}"
        )


# ---------------------------------------------------------------------------------------------
# 2c. The extraction
# ---------------------------------------------------------------------------------------------
_METRIC_OF: Dict[str, str] = {
    "verb": "verbs",
    "check": "checks",
    "fixture": "fixtures",
    "producer": "producers",
    "template": "templates",
    # `case` is the README's word for a fixture. ADVISORY only: it is a synonym this
    # checker inferred, not a term the producers emit.
    "case": "fixtures",
}

# A number is only a count when nothing binds it tighter: `08:00`, `3.12`, `g24-...` and
# `55/55` (handled separately) must not read as one.
_LEFT = r"(?<![\w./:-])"
_NUM = r"\d{1,5}"
# Up to two intervening modifiers, lazily, so "19 CORE verbs" and "25 remaining checks"
# are found while "12 known-bad ERROR keep it honest" is not.
_FILLER = r"(?:[A-Za-z][A-Za-z0-9-]*\s+){0,2}?"
_ENFORCED_NOUN = r"(?P<noun>verb|check|fixture|producer|template)s?"
_ADVISORY_NOUN = r"(?P<noun>verb|check|fixture|producer|template|case)s?"

RATIO_RE = re.compile(
    _LEFT
    + r"(?P<a>"
    + _NUM
    + r")\s*/\s*(?P<b>"
    + _NUM
    + r")\s+"
    + _FILLER
    + _ENFORCED_NOUN
    + r"\b",
    re.I,
)
PLAIN_RE = re.compile(
    _LEFT + r"(?P<n>" + _NUM + r")\s+" + _FILLER + _ENFORCED_NOUN + r"\b", re.I
)
# `24-check gate`, `54-case corpus`: the same claim worn as an adjective.
HYPHEN_RE = re.compile(_LEFT + r"(?P<n>" + _NUM + r")-" + _ADVISORY_NOUN + r"\b", re.I)
# `54 cases` — the enforced nouns are excluded because PLAIN_RE already owns them.
SYNONYM_RE = re.compile(_LEFT + r"(?P<n>" + _NUM + r")\s+(?P<noun>case)s?\b", re.I)

# A paragraph that narrates drift rather than claiming a number. Closed set, matched
# whole-word, and every hit is printed so a reader can see what it let through.
HISTORICAL_RE = re.compile(
    r"\b(?:claimed|used to|previously|formerly|historically|no longer|once said|"
    r"at the time)\b",
    re.I,
)


@dataclasses.dataclass(frozen=True)
class Claim:
    """One self-describing number found in one shipped doc."""

    doc: str
    line: int
    metric: str
    value: int
    snippet: str
    tier: str
    window: str


@dataclasses.dataclass(frozen=True)
class Para:
    """One run of non-blank lines, joined. Both the SCAN unit and the exemption window:
    markdown wraps a sentence across lines, so `it claimed 44\nfixtures` is one claim and
    scanning line by line would miss it. Splitting the join back into sentences is not
    attempted — a `.` in a document full of `gb-surface-gate.py` is usually a filename."""

    body: str
    # (offset into `body`, source line number), ascending. Turns a match position back
    # into the file:line a human has to go edit.
    starts: Tuple[Tuple[int, int], ...]

    def line_at(self, pos: int) -> int:
        line = self.starts[0][1]
        for offset, number in self.starts:
            if offset > pos:
                break
            line = number
        return line


def paragraphs(text: str) -> List[Para]:
    out: List[Para] = []
    buf: List[str] = []
    starts: List[Tuple[int, int]] = []
    width = 0

    def flush() -> None:
        nonlocal buf, starts, width
        if buf:
            out.append(Para(" ".join(buf), tuple(starts)))
        buf, starts, width = [], [], 0

    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        starts.append((width, i))
        buf.append(stripped)
        width += len(stripped) + 1
    flush()
    return out


def find_claims(doc: str, text: str) -> List[Claim]:
    """Every self-describing count in one doc, enforced and advisory."""
    claims: List[Claim] = []
    seen = set()

    for para in paragraphs(text):

        def add(pos: int, metric: str, value: int, snippet: str, tier: str) -> None:
            line = para.line_at(pos)
            key = (line, metric, value, tier, snippet)
            if key in seen:
                return
            seen.add(key)
            claims.append(
                Claim(
                    doc=doc,
                    line=line,
                    metric=metric,
                    value=value,
                    snippet=snippet.strip(),
                    tier=tier,
                    window=para.body,
                )
            )

        for m in RATIO_RE.finditer(para.body):
            metric = _METRIC_OF[m.group("noun").lower()]
            # Both halves are checked. When they are equal this collapses to one claim,
            # because `54/54 fixtures` is one drifted sentence, not two.
            for group in ("a", "b"):
                add(m.start(), metric, int(m.group(group)), m.group(0), ENFORCED)
        for m in PLAIN_RE.finditer(para.body):
            metric = _METRIC_OF[m.group("noun").lower()]
            add(m.start(), metric, int(m.group("n")), m.group(0), ENFORCED)
        for regex in (HYPHEN_RE, SYNONYM_RE):
            for m in regex.finditer(para.body):
                metric = _METRIC_OF[m.group("noun").lower()]
                add(m.start(), metric, int(m.group("n")), m.group(0), ADVISORY)
    return claims


# ---------------------------------------------------------------------------------------------
# 2d. The judgement
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Judgement:
    problems: List[str]
    exemptions: List[str]
    advisories: List[str]
    agreed: int
    # What the producers said, per metric. Carried out so the JSON envelope reports the
    # number it judged against rather than leaving a reader to re-derive it.
    measured: Dict[str, int]


def _states_total(window: str, total: int) -> bool:
    return re.search(r"(?<![\w./:-])" + str(total) + r"(?![\w./-])", window) is not None


def judge(claims: Sequence[Claim], measured: Dict[str, int]) -> Judgement:
    """Compare every claim against the measured total. `measured` is what the producers
    said — this function never counts anything itself."""
    problems: List[str] = []
    exemptions: List[str] = []
    advisories: List[str] = []
    agreed = 0

    for c in claims:
        where = f"{c.doc}:{c.line}"
        if c.metric not in measured:
            problems.append(
                f"{where}: claims {c.value} {c.metric} (`{c.snippet}`) and nothing measured it"
            )
            continue
        total = measured[c.metric]
        if c.value == total:
            agreed += 1
            continue
        if HISTORICAL_RE.search(c.window):
            exemptions.append(
                f"{where}: `{c.snippet}` != {total} live, exempt — the paragraph narrates "
                f"past drift rather than claiming the number"
            )
            continue
        if _states_total(c.window, total):
            exemptions.append(
                f"{where}: `{c.snippet}` != {total} live, exempt — the paragraph also "
                f"states the live total {total}, so the count is scoped"
            )
            continue
        if c.tier == ADVISORY:
            advisories.append(
                f"{where}: `{c.snippet}` reads as {c.value} {c.metric}, live is {total}"
            )
            continue
        problems.append(
            f"{where}: doc says {c.value} {c.metric} (`{c.snippet}`), live is {total}"
        )
    return Judgement(problems, exemptions, advisories, agreed, dict(measured))


def self_description(
    docs: Sequence[Tuple[str, str]],
    read: Callable[[str], Optional[str]],
    measurer: Callable[[str], int],
    require_claims: bool = True,
) -> Judgement:
    """Scan every shipped doc and judge it. `read` returns None for an absent file;
    `measurer` is the live runner (injected so the selftest can stay offline).

    `require_claims` is the positive control over the WHOLE scan: the shipped docs do
    carry counts, so a run that finds none has a broken extractor, not a clean tree. It is
    off for a single-document fixture, where finding nothing is a legitimate answer."""
    problems: List[str] = []
    claims: List[Claim] = []
    for src, dest in docs:
        text = read(src)
        if text is None:
            problems.append(
                f"{src}: exporter publishes it as `{dest}` but it is absent"
            )
            continue
        claims.extend(find_claims(src, text))

    if not claims and require_claims:
        problems.append(
            "no self-describing count found in any shipped doc — the extractor found "
            "nothing to check, which is a defect of this checker, not a clean tree"
        )
        return Judgement(problems, [], [], 0, {})

    measured: Dict[str, int] = {}
    for metric in sorted({c.metric for c in claims}):
        try:
            measured[metric] = measurer(metric)
        except MeasureError as exc:
            problems.append(f"cannot measure `{metric}`: {exc}")

    verdict = judge(claims, measured)
    return Judgement(
        problems + verdict.problems,
        verdict.exemptions,
        verdict.advisories,
        verdict.agreed,
        verdict.measured,
    )


# ---------------------------------------------------------------------------------------------
# The selftest: the rule against known-good and known-bad, offline
# ---------------------------------------------------------------------------------------------
# Synthetic totals. The fixtures prove the RULE fires and abstains; the measurement path is
# proven by running the checker for real against the tree.
FIXTURE_MEASURED: Dict[str, int] = {
    "verbs": 52,
    "checks": 25,
    "fixtures": 55,
    "templates": 43,
    "producers": 53,
}


@dataclasses.dataclass(frozen=True)
class Fixture:
    """One inline document and the verdict the rule must reach on it."""

    name: str
    body: str
    problems: int = 0
    exemptions: int = 0
    advisories: int = 0
    # Only the fixture that proves the empty-scan guard turns this on.
    require_claims: bool = False
    # The one fixture that proves an unmeasurable metric FAILS instead of passing
    # quietly: its measurer refuses every metric, as a broken producer would.
    blind: bool = False


FIXTURES: Tuple[Fixture, ...] = (
    Fixture(
        "good-correct-counts",
        "Version `1.6.0`, **25 checks**, **55 fixtures**.\n",
    ),
    Fixture(
        "good-scoped-count-states-the-total",
        "Eleven stops covering the 19 CORE verbs of 52 live, ordered by the question\n"
        "you actually have rather than by the alphabet.\n",
        exemptions=1,
    ),
    Fixture(
        "good-scoped-total-wraps-to-the-next-line",
        "A tour of the 19 CORE verbs, chosen out of\nthe 52 the CLI registers.\n",
        exemptions=1,
    ),
    Fixture(
        "good-no-counts-at-all",
        "# Grok Bot ops\n\nA tool that measures a deployment and says what is wrong.\n",
    ),
    Fixture(
        "good-historical-narration",
        "- Fail-closed: it cannot drift ahead of the code again (it did: it claimed 44\n"
        "  fixtures and 18 checks against a real 54 and 25).\n",
        exemptions=2,
    ),
    Fixture(
        "good-ratio-correct",
        "python3 bin/gb-surface-gate.py --selftest   # 55/55 fixtures\n",
    ),
    Fixture(
        "good-clock-time-is-not-a-count",
        "- Weekly Mon 08:00 open-incident check; verify it matches the operator's list.\n",
    ),
    Fixture(
        "good-unrelated-ratio",
        "python3 bin/gb-walk.py --selftest  # 52/52 - inline fixtures, no templates/\n",
    ),
    Fixture(
        "good-version-strings-are-not-counts",
        "The CI matrix runs on Ubuntu, Windows and macOS x Python 3.9 and 3.12.\n",
    ),
    Fixture(
        "bad-bare-wrong-verb-count",
        "A tour of the 19 verbs, ordered by the question you actually have.\n",
        problems=1,
    ),
    Fixture(
        "bad-bare-wrong-check-count",
        "$ gb triage\nverdict GREEN - 24 checks - 0 not green\n",
        problems=1,
    ),
    Fixture(
        "bad-wrong-ratio",
        "python3 bin/gb-surface-gate.py --selftest   # 54/54 fixtures\n",
        problems=1,
    ),
    Fixture(
        "bad-ratio-half-wrong",
        "python3 bin/gb-surface-gate.py --selftest   # 54/55 fixtures\n",
        problems=1,
    ),
    Fixture(
        "bad-wrong-count-with-a-modifier",
        "The gate ships 44 known-bad fixtures and nothing else.\n",
        problems=1,
    ),
    Fixture(
        "bad-scoped-by-the-wrong-total",
        "A tour of the 19 CORE verbs of 48 live.\n",
        problems=1,
    ),
    Fixture(
        "bad-metric-cannot-be-measured",
        "Version `1.6.0`, **25 checks**, **55 fixtures**.\n",
        problems=4,
        blind=True,
    ),
    Fixture(
        "bad-extractor-found-nothing",
        "# Grok Bot ops\n\nProse with no numbers in it at all.\n",
        problems=1,
        require_claims=True,
    ),
    Fixture(
        "advisory-hyphenated-adjective",
        "a 24-check gate that judges the result, and says what is wrong\n",
        advisories=1,
    ),
    Fixture(
        "advisory-synonym-noun",
        "fixtures/  the gate's known-good and known-bad corpus - synthetic, 54 cases\n",
        advisories=1,
    ),
)


def _fixture_measurer(metric: str) -> int:
    if metric not in FIXTURE_MEASURED:
        raise MeasureError(f"no probe measures `{metric}`")
    return FIXTURE_MEASURED[metric]


def _blind_measurer(metric: str) -> int:
    raise MeasureError(f"`{metric}` producer did not answer")


COUNT_FIXTURE_CONTRACT: Dict[str, object] = {
    "checks": 30,
    "fixtures": 66,
    "verdict_split": {"GREEN": 5, "RED": 47, "ERROR": 14},
    "mutation_covered": 30,
}


@dataclasses.dataclass(frozen=True)
class NormativeFixture:
    document: str
    case: str

    @property
    def name(self) -> str:
        return f"normative-{self.document.lower().replace('.', '-')}-{self.case}"


NORMATIVE_FIXTURES: Tuple[NormativeFixture, ...] = tuple(
    NormativeFixture(document, case)
    for document in NORMATIVE_DOCS
    for case in (
        "current",
        "stale-checks",
        "stale-fixtures",
        "stale-verdict-split",
        "stale-mutation-coverage",
        "malformed",
        "arithmetic-mismatch",
        "missing-document",
        "extra-normative-count",
    )
)


def normative_fixture_docs(fixture: NormativeFixture) -> Dict[str, Optional[str]]:
    current = count_tuple_text(COUNT_FIXTURE_CONTRACT)
    docs: Dict[str, Optional[str]] = {document: current for document in NORMATIVE_DOCS}
    if fixture.case == "stale-checks":
        docs[fixture.document] = current.replace("checks=30", "checks=29")
    elif fixture.case == "stale-fixtures":
        docs[fixture.document] = current.replace("fixtures=66", "fixtures=67").replace(
            "ERROR=14", "ERROR=15"
        )
    elif fixture.case == "stale-verdict-split":
        docs[fixture.document] = current.replace("GREEN=5 RED=47", "GREEN=4 RED=48")
    elif fixture.case == "stale-mutation-coverage":
        docs[fixture.document] = current.replace(
            "mutation-covered=30", "mutation-covered=29"
        )
    elif fixture.case == "malformed":
        docs[fixture.document] = "Gate count tuple (producer: malformed): malformed."
    elif fixture.case == "arithmetic-mismatch":
        docs[fixture.document] = current.replace("fixtures=66", "fixtures=67")
    elif fixture.case == "missing-document":
        docs[fixture.document] = None
    elif fixture.case == "extra-normative-count":
        docs[fixture.document] = current + "\n" + current
    return docs


def selftest(as_json: bool = False) -> int:
    passed = 0
    logs: List[Dict[str, object]] = []
    for fx in FIXTURES:
        started = datetime.datetime.now().timestamp()
        verdict = self_description(
            [(f"{fx.name}.md", f"{fx.name}.md")],
            lambda _src, body=fx.body: body,  # type: ignore[misc]
            _blind_measurer if fx.blind else _fixture_measurer,
            require_claims=fx.require_claims,
        )
        got = (
            len(verdict.problems),
            len(verdict.exemptions),
            len(verdict.advisories),
        )
        want = (fx.problems, fx.exemptions, fx.advisories)
        ok = got == want
        passed += ok
        logs.append(
            {
                "producer_tuple": count_fields(COUNT_FIXTURE_CONTRACT),
                "document": f"{fx.name}.md",
                "path": f"{fx.name}.md",
                "claim_id": "self-description",
                "expected": want,
                "observed": got,
                "fixture_id": fx.name,
                "verdict": "GREEN" if ok else "RED",
                "recovery_argv": ["bin/gb-gatesdoc.py", "--selftest"],
                "exit": 0 if ok else 1,
                "timing_ms": round(
                    (datetime.datetime.now().timestamp() - started) * 1000, 3
                ),
            }
        )
        if not as_json:
            print(
                f"  [{'PASS' if ok else 'FAIL'}] {fx.name:<42} expected {want} got {got}"
            )
            if not ok:
                for detail in (
                    verdict.problems + verdict.exemptions + verdict.advisories
                ):
                    print(f"           {detail}")

    for fx in NORMATIVE_FIXTURES:
        started = datetime.datetime.now().timestamp()
        problems, _records = normative_count_problems(
            normative_fixture_docs(fx), COUNT_FIXTURE_CONTRACT, fx.name
        )
        got = "RED" if problems else "GREEN"
        want = "GREEN" if fx.case == "current" else "RED"
        ok = got == want
        passed += ok
        logs.append(
            {
                "producer_tuple": count_fields(COUNT_FIXTURE_CONTRACT),
                "document": fx.document,
                "path": fx.document,
                "claim_id": fx.case,
                "expected": want,
                "observed": got,
                "fixture_id": fx.name,
                "verdict": got,
                "recovery_argv": COUNT_RECOVERY_ARGV,
                "exit": 0 if got == "GREEN" else 1,
                "timing_ms": round(
                    (datetime.datetime.now().timestamp() - started) * 1000, 3
                ),
            }
        )
        if not as_json:
            print(
                f"  [{'PASS' if ok else 'FAIL'}] {fx.name:<42} expected {want} got {got}"
            )
            if not ok:
                for detail in problems:
                    print(f"           {detail}")

    known_bad = sum(1 for f in FIXTURES if f.name.startswith("bad-")) + sum(
        1 for f in NORMATIVE_FIXTURES if f.case != "current"
    )
    total = len(FIXTURES) + len(NORMATIVE_FIXTURES)
    ok = known_bad >= 3 and passed == total
    if as_json:
        print(
            json.dumps(
                {
                    "schema": "gb-gatesdoc-selftest/1",
                    "producer_tuple": count_fields(COUNT_FIXTURE_CONTRACT),
                    "fixtures": logs,
                    "passed": passed,
                    "total": total,
                    "known_bad": known_bad,
                    "verdict": "GREEN" if ok else "RED",
                    "exit": 0 if ok else 1,
                },
                indent=1,
            )
        )
    elif not ok:
        print(
            f"SELFTEST FAIL - {passed}/{total} fixtures ({known_bad} known-bad)",
            file=sys.stderr,
        )
    else:
        print(f"SELFTEST PASS - {passed}/{total} fixtures ({known_bad} known-bad)")
    return 0 if ok else 1


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
def _read(src: str) -> Optional[str]:
    path = ROOT / src
    if not path.is_file():
        return None
    return path.read_text()


REAL_REPO = "grok_bot_playground"


def _manifest_url_problems() -> List[str]:
    """Every plugin manifest's URL fields must point at the repository this project publishes.

    Measured 2026-09-12: `plugin/.cursor-plugin/plugin.json` had `homepage` and `repository`
    both set to `github.com/JYeswak/grok-bot-gap-kit`, which returns 404 — it does not exist.
    Anyone who loaded the plugin and clicked through landed nowhere, and no gate compared a
    manifest URL against the real remote, so it shipped.

    It checks the URL-BEARING FIELDS, not the file text. The first draft of this rule was a
    substring scan for the dead slug over the whole manifest, and it fired on `"name":
    "grok-bot-gap-kit"` — which is the plugin's own identifier and is not a URL and is not
    broken. A rule that flags a correct line teaches people to delete the rule. Narrowed the
    same hour it was written.

    Offline by construction: it cannot prove a URL resolves, only that no field points at the
    slug that measurably did not, and that a home is named at all.
    """
    out: List[str] = []
    manifests = sorted(ROOT.glob("plugin/.*-plugin/plugin.json"))
    if not manifests:
        # An empty scan set is never a pass — the rule the rest of this file lives by.
        return [
            "no plugin manifest under plugin/.*-plugin/ — the URL check scanned nothing"
        ]
    for mf in manifests:
        rel = mf.relative_to(ROOT)
        try:
            doc = json.loads(mf.read_text())
        except (OSError, ValueError) as e:
            out.append(
                f"{rel} is not readable JSON ({e}) — a manifest an app cannot parse"
            )
            continue
        urls = {
            k: v
            for k, v in doc.items()
            if k in ("homepage", "repository", "bugs", "url")
        }
        if not urls:
            out.append(
                f"{rel} declares no homepage or repository — a manifest with no home"
            )
            continue
        for field, value in sorted(urls.items()):
            text = value if isinstance(value, str) else json.dumps(value)
            if "grok-bot-gap-kit" in text:
                out.append(
                    f"{rel} {field} points at `grok-bot-gap-kit`, which returns 404 — "
                    f"point it at `{REAL_REPO}`"
                )
            elif REAL_REPO not in text:
                out.append(f"{rel} {field} does not name `{REAL_REPO}`: {text[:60]}")
    return out


def main(argv: Sequence[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    as_json = "--json" in argv
    if "--selftest" in argv:
        return selftest(as_json)
    unknown = [
        a for a in argv if a.startswith("-") and a not in ("--json", "--selftest")
    ]
    if unknown:
        print(f"GATESDOC FAIL: unknown option {unknown[0]}", file=sys.stderr)
        return 2

    for required in (DOC, GATE, EXPORTER):
        if not required.is_file():
            print(f"GATESDOC FAIL: {required} absent", file=sys.stderr)
            return 1

    gate = load_gate()
    problems, code_ids, version = normative_problems(DOC.read_text(), gate)
    problems.extend(_manifest_url_problems())
    try:
        contract = gate.count_contract(ROOT)
        contract_fields = count_fields(contract)
        normative_problems_found, normative_records = normative_count_problems(
            {document: _read(document) for document in NORMATIVE_DOCS}, contract
        )
        problems.extend(normative_problems_found)
    except (RuntimeError, ValueError) as exc:
        print(f"GATESDOC FAIL: count producer failed: {exc}", file=sys.stderr)
        return 2
    docs = shipped_root_docs(EXPORTER.read_text())
    missing = [d for d in REQUIRED_DOCS if d not in {src for src, _ in docs}]
    if missing:
        problems.append(
            "exporter no longer publishes %s at the root of the public tree — the "
            "self-description scan would silently shrink" % ", ".join(missing)
        )
    verdict = self_description(
        docs,
        _read,
        lambda metric: contract_fields[metric]
        if metric in ("checks", "fixtures")
        else measure(metric),
    )
    problems.extend(verdict.problems)
    code = 1 if problems else 0

    if as_json:
        envelope = {
            "schema": "gb-gatesdoc/1",
            "tool": "gb",
            "command": "gatesdoc",
            "measured_at": datetime.datetime.now()
            .astimezone()
            .isoformat(timespec="seconds"),
            "gate_version": version,
            "producer_tuple": contract_fields,
            "gate_checks": contract_fields["checks"],
            "gate_fixtures": contract_fields["fixtures"],
            "normative_claims": normative_records,
            "docs_scanned": [{"source": s, "published_as": d} for s, d in docs],
            "measured": verdict.measured,
            "agreed": verdict.agreed,
            "problems": problems,
            "exemptions": verdict.exemptions,
            "advisories": verdict.advisories,
            "verdict": "RED" if problems else "GREEN",
            "exit_code": code,
        }
        print(json.dumps(envelope, indent=1))
        return code

    for note in verdict.exemptions:
        print(f"GATESDOC EXEMPT: {note}")
    for note in verdict.advisories:
        print(f"GATESDOC ADVISORY: {note}")
    if problems:
        for p in problems:
            print(f"GATESDOC FAIL: {p}", file=sys.stderr)
        return code
    producer_text = gate.count_contract_text(contract)
    print(
        f"GATESDOC PASS — {producer_text}, version {version}; "
        f"{verdict.agreed} self-described counts across {len(docs)} shipped docs agree "
        f"with the live runner "
        f"({len(verdict.exemptions)} exempt, {len(verdict.advisories)} advisory)"
    )
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
