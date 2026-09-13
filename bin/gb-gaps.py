#!/usr/bin/env python3
"""gb-gaps — the roadmap that cannot go stale, because it re-proves every gap it lists.

WHY THIS EXISTS (measured 2026-09-12). Four wizard duels produced 24 product ideas. An audit
against the 52-verb surface and the 25 gate checks measured the real split: 2 shipped under
their proposed names (`gb mirror`, the fleet percentile inside it), 6 shipped under DIFFERENT
names (`gb delta` became `gb digest`; the routine witness became the gate check
`g25-routine-liveness`; `gb pulse` became `gb corpus freshness`), 8 are partial, and 8 never
shipped. 16 of 24 in some form.

That count is itself a finding about roadmaps. My first draft of this docstring said "one
shipped", because I had searched for the PROPOSED VERB NAMES and found only `mirror`. Searching
for a label instead of the capability understated delivery by seven and would have sent someone
to rebuild `gb delta` beside the `gb digest` that already does it. Nothing in this repo could tell you WHICH, so the only way to answer "what is still
open" was to re-read four documents totalling 140KB and guess against a 52-verb surface. That
is a roadmap, and every roadmap decays the moment someone ships something without updating it.

THE INVERSION THAT FIXES IT. `gb post` proves a CLAIM is still TRUE before publishing it.
This file proves a GAP is still OPEN before listing it. Same machinery, opposite polarity:
each row carries a command plus the signal that appears in that command's output WHILE the gap
exists. Run it, and a gap someone closed yesterday drops off by itself — nobody has to
remember to delete the row. A roadmap that cannot notice it is done is how "we should build X"
survives three months past X shipping.

THREE VERDICTS, and the third is the one that matters:

  OPEN     the proof ran and the open-signal is present. Still real. Rank it.
  CLOSED   the proof ran and the signal is GONE. Someone shipped it. Say so loudly —
           the whole point of an executable roadmap is that closing a row is visible.
  UNKNOWN  the proof could not run, or crashed. NOT the same as closed. A broken probe is
           evidence about the probe, and reporting it as "done" is how a gap list starts
           lying in the flattering direction. Measured the same day in `gb post`: a wrong
           dict key crashed two commands and the verifier read the crash as a refutation.

RANKING IS severity x reach / effort, and reach is the honest divisor. A severity-3 gap that
affects only this account ranks below a severity-2 gap every operator hits, because this repo
already measures one account well and generalises badly — the single largest structural risk
it carries. `reach` is stated per row, never inferred.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gbtypes  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-gaps/1"
EXIT_OPEN_GAPS = 1
EXIT_UNKNOWN = 2

SEV = {1: "nice", 2: "real", 3: "blocking"}
EFFORT = {"S": 1, "M": 3, "L": 8}
REACH = {
    "mine": 1,  # only this account benefits
    "some": 2,  # operators who already use gb
    "every": 3,  # anyone running Grok Bot, including people who never install this
}


@dataclasses.dataclass(frozen=True)
class Gap:
    """One open product gap, and the command that proves it is still open.

    `open_signal` is a REGEX matched against the proof's combined stdout+stderr. It must
    describe the WORLD WITH THE GAP IN IT, never the absence of something — "no README" is
    provable by matching the error a missing file produces, whereas matching nothing at all
    would make every broken command look like a closed gap.
    """

    id: str
    title: str
    why: str
    proof: str
    open_signal: str
    severity: int  # 1-3
    effort: str  # S | M | L
    reach: str  # mine | some | every
    source: str
    fix: str
    # A regex that MUST appear in the proof's output for a CLOSED verdict to be believable.
    # Measured 2026-09-12: the `no-routine-write` row used an open-signal that simply never
    # appeared in its command's output, so a real, unfixed gap reported CLOSED — "someone
    # shipped this" about work nobody had done. A wrong signal fails in the FLATTERING
    # direction, which is the one nobody double-checks. The control is the house rule applied
    # to the ledger itself: an empty result is not evidence of absence unless a positive
    # control proves the probe was looking in the right place.
    control: str = ""

    @property
    def impact(self) -> int:
        """severity x reach. The whole value of closing this row, cost not considered."""
        return self.severity * REACH[self.reach]

    @property
    def rank(self) -> Tuple[int, int, str]:
        """Impact FIRST, cheapness second, id last so the order is total.

        The first version divided impact by effort, and a sev-2 S-effort README fix scored
        4.00 against 3.00 for a blocking gap that every operator hits. That is how a backlog
        fills with easy wins while the structural problem stays open for a quarter: dividing
        by cost lets a trivial row outrank an important one purely by being trivial.

        Effort is a TIE-BREAKER, never a divisor. Among rows of equal impact, prefer the
        cheap one; across unequal impact, impact wins and the expensive row stays on top
        where someone has to look at it.
        """
        return (-self.impact, EFFORT[self.effort], self.id)

    def problems(self) -> List[str]:
        bad: List[str] = []
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,47}", self.id):
            bad.append(f"id {self.id!r} is not a slug")
        if self.severity not in SEV:
            bad.append(f"severity {self.severity} outside 1-3")
        if self.effort not in EFFORT:
            bad.append(f"effort {self.effort!r} not S/M/L")
        if self.reach not in REACH:
            bad.append(f"reach {self.reach!r} not mine/some/every")
        if not self.proof.strip():
            bad.append("no proof command: an unprovable gap is an opinion")
        if not self.fix.strip():
            bad.append("no fix named — a gap with no next action is a complaint")
        for field, pat in (
            ("open_signal", self.open_signal),
            ("control", self.control),
        ):
            if not pat:
                continue
            try:
                re.compile(pat)
            except re.error as e:
                bad.append(f"{field} is not a valid regex: {e}")
        return bad


@dataclasses.dataclass(frozen=True)
class Verdict:
    gap_id: str
    state: str  # OPEN | CLOSED | UNKNOWN
    detail: str


_CRASHED = re.compile(
    r"Traceback \(most recent call last\)"
    r"|^\s*(?:KeyError|IndexError|TypeError|AttributeError|ModuleNotFoundError):",
    re.MULTILINE,
)


def probe(
    g: Gap, timeout_s: int = 180, runner: Optional[Callable[..., Any]] = None
) -> Verdict:
    """Run the proof and decide whether the gap is still there.

    A non-zero exit is NOT failure. Most proofs here are EXPECTED to exit non-zero — `ls` on a
    missing file, a gate carrying RED, a verb refusing without credentials. The exit code is
    recorded; the SIGNAL decides.
    """
    run = runner or subprocess.run
    try:
        p = run(
            g.proof,
            shell=True,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return Verdict(
            g.id, "UNKNOWN", f"proof exceeded {timeout_s}s — probe broken, not closed"
        )
    except OSError as e:
        return Verdict(g.id, "UNKNOWN", f"{type(e).__name__}: {e}")
    out = (getattr(p, "stdout", "") or "") + (getattr(p, "stderr", "") or "")
    code = getattr(p, "returncode", None)
    if _CRASHED.search(out):
        return Verdict(
            g.id,
            "UNKNOWN",
            f"the PROBE crashed (exit {code}) — says nothing about the gap",
        )
    if re.search(g.open_signal, out, re.MULTILINE):
        return Verdict(g.id, "OPEN", f"open-signal present (exit {code})")
    if g.control and not re.search(g.control, out, re.MULTILINE):
        return Verdict(
            g.id,
            "UNKNOWN",
            f"the probe's own positive control {g.control!r} is MISSING (exit {code}) — the "
            "command is looking in the wrong place, so its silence proves nothing",
        )
    if not out.strip():
        return Verdict(
            g.id,
            "UNKNOWN",
            f"proof produced NO output (exit {code}) — cannot read silence as closed",
        )
    return Verdict(
        g.id, "CLOSED", f"open-signal GONE (exit {code}) — someone shipped this"
    )


# ----------------------------------------------------------------------------------------------
# the ledger. Every row was measured on 2026-09-12; `source` names who found it.

GAPS: Tuple[Gap, ...] = (
    Gap(
        id="no-repo-readme",
        title="The source repo has no README.md at all",
        why=(
            "The public MIRROR gets one, generated from packaging/README.public.md, so the "
            "published face is fine. The repo a collaborator or an agent lands in has nothing: "
            "`ls README.md` errors. Anyone with repo access — including every subagent this "
            "project spawns — opens a 3,046-file tree with no statement of what it is."
        ),
        proof="ls README.md 2>&1",
        open_signal=r"No such file|cannot access",
        severity=2,
        effort="S",
        reach="some",
        source="measured directly, 2026-09-12",
        fix="Add a repo-root README.md, or symlink the packaged one so the two cannot drift.",
    ),
    Gap(
        id="make-fixtures-unreachable",
        title="gb-make-fixtures.py is the last producer the CLI cannot reach",
        why=(
            "`gb dogfood audit` is down to ONE gap from 54, and this is it. The detector is "
            "still lethal — unregistering two verbs moved the count 1 -> 3 — so the 1 is real "
            "rather than a softened threshold. It regenerates the gate's 55-fixture corpus and "
            "runs today only as a side effect of something else."
        ),
        proof="./bin/gb dogfood audit 2>&1",
        open_signal=r"gb-make-fixtures\.py",
        severity=1,
        effort="S",
        reach="mine",
        source="gb dogfood audit, mutation-verified 2026-09-12",
        fix="Either register it as a verb or mark it deliberately internal in the exporter's why-not list.",
    ),
    Gap(
        id="one-account-generalisation",
        title="Most measurements describe ONE account and do not generalise",
        why=(
            "13 Bots on one account, plus a public corpus of 645. Account-scoped claims — "
            "`0 of 13 Bots can interrupt you`, `11 of 13 carry zero routines` — are true here "
            "and say nothing about a stranger's fleet. `gb mirror` is the existing answer "
            "(it reads a stranger's own disk, no token, no network) but nothing routes a new "
            "operator to it first, and `gb post` refuses account-scoped claims in a clone "
            "rather than reframing them as 'here is OUR number, run it for YOURS'."
        ),
        proof="./bin/gb post --limit 3 2>&1 | tail -1",
        open_signal=r"refused",
        severity=3,
        effort="M",
        reach="every",
        source="measured in a clean public clone, 2026-09-12",
        fix=(
            "Give every claim a `scope` field (account | corpus | universal). In a clone, an "
            "account-scoped claim should render as a COMPARISON prompt, not a refusal."
        ),
    ),
)


def load_extra(root: pathlib.Path = ROOT) -> Tuple[List[Gap], List[str]]:
    """Rows contributed as JSON under `roadmap/`, merged newest-wins per id.

    NOT `gaps/` — that directory already belongs to `gb demand`, which writes integration
    demand-gap artifacts there. Pointing at it silently read three of its files and reported
    "0 row(s)" three times, which looks like an empty roadmap rather than a wrong address.
    A name collision that produces a plausible empty answer is worse than a crash.

    The built-in tuple above is the spine; research agents drop artifacts here. Same merge rule
    as the findings supply, for the same reason: reading only the newest file would let
    whichever producer ran last silently delete every other producer's rows.
    """
    notes: List[str] = []
    d = root / "roadmap"
    if not d.is_dir():
        return [], notes
    by_id: Dict[str, Gap] = {}
    skipped = 0
    for art in sorted(d.glob("*.json")):
        try:
            doc = json.loads(art.read_text())
        except (ValueError, OSError) as e:
            notes.append(f"{art.name}: unreadable ({type(e).__name__})")
            continue
        kept = 0
        for r in doc.get("rows", []):
            if not isinstance(r, dict):
                skipped += 1
                continue
            try:
                by_id[str(r["id"])] = Gap(
                    id=str(r["id"]),
                    title=str(r["title"]),
                    why=str(r.get("why", "")),
                    proof=str(r.get("proof", "")),
                    open_signal=str(r.get("open_signal", "")),
                    severity=int(r.get("severity", 2)),
                    effort=str(r.get("effort", "M")),
                    reach=str(r.get("reach", "some")),
                    source=str(r.get("source", art.name)),
                    fix=str(r.get("fix", "")),
                    control=str(r.get("control", "")),
                )
                kept += 1
            except (KeyError, TypeError, ValueError):
                skipped += 1
        notes.append(f"{art.name}: {kept} row(s)")
    if skipped:
        notes.append(f"SKIPPED {skipped} malformed row(s)")
    return list(by_id.values()), notes


def collect(root: pathlib.Path = ROOT) -> Tuple[List[Gap], List[str]]:
    extra, notes = load_extra(root)
    by_id: Dict[str, Gap] = {g.id: g for g in GAPS}
    by_id.update(
        {g.id: g for g in extra}
    )  # artifacts win, so a row can be corrected in place
    return list(by_id.values()), notes


def render(rows: Sequence[Tuple[Gap, Verdict]]) -> str:
    out: List[str] = []
    openr = [(g, v) for g, v in rows if v.state == "OPEN"]
    closed = [(g, v) for g, v in rows if v.state == "CLOSED"]
    unknown = [(g, v) for g, v in rows if v.state == "UNKNOWN"]
    openr.sort(key=lambda gv: gv[0].rank)

    out.append(f"  {'IMPACT':>6} {'SEV':<9} {'EFF':<4} {'REACH':<6} {'ID':<30} TITLE")
    out.append("  " + "-" * 108)
    for g, _v in openr:
        out.append(
            f"  {g.impact:>5}  {SEV[g.severity]:<9} {g.effort:<4} {g.reach:<6} {g.id:<30} {g.title[:44]}"
        )
    if closed:
        out.append("")
        out.append(f"  CLOSED since this row was written — {len(closed)}:")
        for g, v in closed:
            out.append(f"    {g.id:<30} {v.detail}")
    if unknown:
        out.append("")
        out.append(f"  UNKNOWN — probe could not decide, NOT closed — {len(unknown)}:")
        for g, v in unknown:
            out.append(f"    {g.id:<30} {v.detail}")
    out.append("")
    out.append(
        f"  {len(openr)} open · {len(closed)} closed · {len(unknown)} unknown "
        f"· ranked by severity x reach, effort breaks ties"
    )
    return "\n".join(out)


def render_one(g: Gap, v: Verdict) -> str:
    return "\n".join(
        [
            f"  {g.id}  [{SEV[g.severity]} · effort {g.effort} · reach {g.reach} · impact {g.impact}]",
            f"  {g.title}",
            "",
            f"  WHY:   {g.why}",
            f"  FIX:   {g.fix}",
            f"  PROVE: {g.proof}",
            f"  STATE: {v.state} — {v.detail}",
            f"  FOUND: {g.source}",
        ]
    )


def body_run(a: argparse.Namespace) -> int:
    gaps, notes = collect()
    malformed = [(g, p) for g in gaps for p in (g.problems(),) if p]
    ok = [g for g in gaps if not g.problems()]
    rows = [(g, probe(g, timeout_s=a.timeout)) for g in ok]

    if a.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "captured_at": dt.datetime.now(dt.timezone.utc)
                    .replace(microsecond=0)
                    .isoformat(),
                    "result": {
                        "open": len([1 for _g, v in rows if v.state == "OPEN"]),
                        "closed": len([1 for _g, v in rows if v.state == "CLOSED"]),
                        "unknown": len([1 for _g, v in rows if v.state == "UNKNOWN"]),
                        "malformed": len(malformed),
                        "rows": [
                            {
                                "id": g.id,
                                "title": g.title,
                                "state": v.state,
                                "detail": v.detail,
                                "impact": g.impact,
                                "severity": g.severity,
                                "effort": g.effort,
                                "reach": g.reach,
                                "fix": g.fix,
                                "proof": g.proof,
                                "source": g.source,
                            }
                            for g, v in sorted(rows, key=lambda gv: gv[0].rank)
                        ],
                        "notes": notes,
                    },
                },
                indent=1,
            )
        )
    else:
        for n in notes:
            print(f"  note: {n}")
        if malformed:
            print(f"\n  MALFORMED — {len(malformed)} row(s) rejected before probing:")
            for g, probs in malformed:
                print(f"    {g.id}: {probs[0]}")
        print()
        print(render(rows))
        if a.detail:
            for g, v in sorted(rows, key=lambda gv: gv[0].rank):
                if v.state == "OPEN":
                    print("\n" + "=" * 110)
                    print(render_one(g, v))

    if any(v.state == "UNKNOWN" for _g, v in rows):
        return EXIT_UNKNOWN
    return EXIT_OPEN_GAPS if any(v.state == "OPEN" for _g, v in rows) else 0


# ----------------------------------------------------------------------------------------------


def _fake(out: str, code: int = 0) -> Callable[..., Any]:
    class P:
        def __init__(self) -> None:
            self.stdout = out
            self.stderr = ""
            self.returncode = code

    def run(*_a: Any, **_k: Any) -> Any:
        return P()

    return run


def _gap(**over: Any) -> Gap:
    base: Dict[str, Any] = dict(
        id="no-repo-readme",
        title="t",
        why="w",
        proof="ls README.md",
        open_signal=r"No such file",
        severity=2,
        effort="S",
        reach="some",
        source="s",
        fix="f",
    )
    base.update(over)
    return Gap(**base)


def selftest() -> int:
    fails: List[str] = []
    n = 0

    def check(ok: bool, label: str) -> None:
        nonlocal n
        n += 1
        if not ok:
            fails.append(label)

    # --- the three verdicts, each proven ---
    check(
        probe(_gap(), runner=_fake("ls: No such file or directory")).state == "OPEN",
        "a present open-signal did not read OPEN",
    )
    check(
        probe(_gap(), runner=_fake("README.md")).state == "CLOSED",
        "a vanished open-signal did not read CLOSED",
    )
    check(
        probe(_gap(), runner=_fake("")).state == "UNKNOWN",
        "empty output was not UNKNOWN",
    )

    # THE POSITIVE CONTROL ON THE PROBE ITSELF. Measured 2026-09-12: the `no-routine-write`
    # row shipped an open-signal that never appeared in its command's output, so a real,
    # unfixed gap reported CLOSED — the ledger said "someone shipped this" about work nobody
    # had done. A wrong signal fails in the FLATTERING direction, which is the one nobody
    # re-checks. With a control, a probe that cannot see its own landmark says UNKNOWN.
    ctl = _gap(open_signal=r"five fields wide", control=r"def deploy_spec")
    v = probe(ctl, runner=_fake("def deploy_spec(tpl):  # now deploys the routine too"))
    check(
        v.state == "CLOSED",
        f"control present + signal gone must be CLOSED, got {v.state}",
    )
    v = probe(ctl, runner=_fake("totally unrelated output from the wrong file"))
    check(v.state == "UNKNOWN", f"control ABSENT must be UNKNOWN, got {v.state}")
    check("wrong place" in v.detail, "the control failure did not name the cause")
    # and a row with no control keeps the old two-state behaviour
    v = probe(_gap(open_signal=r"zzz"), runner=_fake("something else"))
    check(v.state == "CLOSED", "a control-less row should still be able to close")
    check(
        any("valid regex" in x for x in _gap(control="(bad").problems()),
        "an invalid control regex passed validation",
    )

    # a CRASHED probe is never CLOSED — the flattering direction is the dangerous one
    v = probe(
        _gap(), runner=_fake("Traceback (most recent call last):\nKeyError: 'x'\n", 1)
    )
    check(v.state == "UNKNOWN", f"a crashed probe gave {v.state}, not UNKNOWN")
    check("says nothing about the gap" in v.detail, "the crash verdict blamed the gap")

    # a non-zero exit that still shows the signal is OPEN: most proofs here exit non-zero
    check(
        probe(_gap(), runner=_fake("ls: No such file", 2)).state == "OPEN",
        "a non-zero exit masked a present open-signal",
    )

    # --- structure ---
    check(_gap().problems() == [], "a well-formed gap was rejected")
    check(
        any("slug" in p for p in _gap(id="Bad ID").problems()), "a non-slug id passed"
    )
    check(
        any("unprovable" in p for p in _gap(proof=" ").problems()),
        "a proofless gap passed",
    )
    check(
        any("complaint" in p for p in _gap(fix="").problems()), "a fixless gap passed"
    )
    check(
        any("outside 1-3" in p for p in _gap(severity=7).problems()),
        "a bad severity passed",
    )
    check(
        any("not S/M/L" in p for p in _gap(effort="XL").problems()),
        "a bad effort passed",
    )
    check(
        any("not mine/some/every" in p for p in _gap(reach="all").problems()),
        "a bad reach passed",
    )
    check(
        any("valid regex" in p for p in _gap(open_signal="(unclosed").problems()),
        "an invalid regex passed",
    )

    # --- ranking: severity x reach / effort, and reach is load-bearing ---
    mine = _gap(id="a-mine", severity=3, reach="mine", effort="S")
    every = _gap(id="b-every", severity=2, reach="every", effort="S")
    check(
        every.impact > mine.impact,
        "a sev-2 gap every operator hits must outrank a sev-3 gap only we hit",
    )
    check(
        every.rank < mine.rank,
        "sort order disagrees with impact (lower tuple sorts first)",
    )
    cheap = _gap(id="c-cheap", severity=2, reach="some", effort="S")
    dear = _gap(id="d-dear", severity=2, reach="some", effort="L")
    check(
        cheap.rank < dear.rank, "effort did not break a tie between equal-impact rows"
    )

    # REGRESSION, the defect this ranking replaced. Version 1 divided impact by effort, so a
    # trivial README fix (sev 2, reach some, effort S) scored 4.00 and a blocking gap every
    # operator hits (sev 3, reach every, effort L) scored 1.125 — the cheap cosmetic row sat
    # on top of the structural one purely for being cheap. Effort must never do that again.
    trivial = _gap(id="e-readme", severity=2, reach="some", effort="S")
    structural = _gap(id="f-structural", severity=3, reach="every", effort="L")
    check(
        structural.rank < trivial.rank,
        "a trivial cheap fix outranked a blocking gap every operator hits",
    )
    check(
        sorted([trivial, structural], key=lambda g: g.rank)[0].id == "f-structural",
        "the sort disagrees with the pairwise comparison",
    )

    # --- the shipped ledger is itself well-formed ---
    for g in GAPS:
        check(
            g.problems() == [], f"shipped gap {g.id} is malformed: {g.problems()[:1]}"
        )
    check(len({g.id for g in GAPS}) == len(GAPS), "duplicate id in the shipped ledger")

    for f in fails:
        print(f"FAIL: {f}")
    print(f"SELFTEST {'FAIL' if fails else 'PASS'} - {n - len(fails)}/{n} properties")
    return 1 if fails else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gb-gaps.py",
        description="the roadmap that re-proves every gap it lists, and drops the ones you closed",
    )
    p.add_argument("--json", action="store_true", help="machine-readable envelope")
    p.add_argument(
        "--detail", action="store_true", help="full why/fix/proof per open gap"
    )
    p.add_argument(
        "--selftest", action="store_true", help="inline fixtures, no network"
    )
    p.add_argument("--timeout", type=int, default=180, help="per-proof seconds")
    return p


def body() -> int:
    ns = build_parser().parse_args()
    if ns.selftest:
        return selftest()
    return body_run(ns)


if __name__ == "__main__":
    gbtypes.main(body)
