#!/usr/bin/env python3
"""gb-post — turn measured findings into posts that VERIFY THEMSELVES before they ship.

WHY THIS EXISTS (measured 2026-09-12). This repo can measure a Grok Bot deployment and the
ecosystem around it — 33 verbs, 22 producer selftests, 55 gate fixtures. It could not teach
anyone anything. There was no publishing surface at all: `ls bin/ | grep -iE 'post|publish|
social|teach'` returned nothing, and `findings/` was a directory with one entry. So the honest
answer to "how does anyone else benefit from this work" was: they don't.

THE ONE PROPERTY THAT MATTERS. A post here cannot ship a number that this CLI does not
reproduce. Not "should not" — cannot: `verify` RUNS the command attached to every claim and
asserts the claim's own number appears in the output. A claim whose command does not reproduce
it is REFUSED, and refusal is the default state of every draft.

That rule is not a style preference, it is the scar tissue of one evening. SIX numbers were
falsified by re-measurement in a single session, every one of them mine, and every one of them
had felt solid enough to say out loud:

  claimed                                    measured                        how it died
  "no API to create a Bot"                   CreateGrokBotAgentFromTemplate  a live create
  "0 routines have ever run"                 1 of 2 had run                  stale by 6 hours
  "the Bot is not in the roster — phantom"   it was there                    per-machine cache
  "ListGrokBotAgents caps at 10"             returned 13                     no paging field
  "249 of 294 integrations unserved"         241 of 294                      wrong matcher
  "97 uses from 86 authors"                  160 from 155                    no shipped producer

Five of the six were a DERIVED read beaten by the source: a cache, an artifact, an inline
script, a stale file. The sixth was a matcher nobody had tested. A post is the most expensive
place to be wrong, because a reader cannot re-measure for you — so the producer re-measures
first, and a post that cannot survive that is not a post.

WHAT A POST IS HERE. Four required parts, and the shape is enforced, not suggested:

  1. A CLAIM with its DENOMINATOR. "241 of 294" is a claim; "most integrations" is a mood.
  2. The COMMAND that reproduces it, which a reader can run on their own deployment.
  3. What the reader DOES differently. A finding that changes no behaviour is trivia.
  4. The CAVEAT, when the number has one. Stating the limit is what makes the rest credible.

RANKING IS BY TEACHING VALUE, NEVER BY ENGAGEMENT. Measured twice tonight: ranking by
citations made one account talking to itself (`alltoolsverse`, five self-cites) look like a
market, and an 866k-impression row turned out to be a different @mattyp post about moving to
San Francisco. Engagement ranks the loudest; distinct authors rank the real. This file ranks
`surprise * actionability`, and breaks ties toward the claim with the cheaper command, because
a finding a reader can check in three seconds teaches more than one that needs a token.

NOVELTY IS A LEDGER, NOT A MEMORY. `post-ledger.json` records every claim id ever published
with the date and the number as published. A claim whose number has CHANGED is not a repeat —
it is a follow-up, and those are the best posts, so the ledger distinguishes `published` from
`superseded` rather than collapsing both into "seen".
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
from gblib import load  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
LEDGER = ROOT / "post-ledger.json"
OUT_DIR = ROOT / "posts"
SCHEMA = "gb-post/1"

EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3

# A claim must state a denominator. "241 of 294" passes; "most" does not. The pattern is
# deliberately narrow: a bare number with no basis is the shape every falsified claim above
# took, so the gate is "two numbers, or a number and an explicit total", not "contains a digit".
# A number, optionally decimal, optionally comma-grouped. Written once because getting it
# wrong in one of three alternatives is exactly how the first version passed its own tests
# and then rejected 26 well-formed claims.
_N = r"\d[\d,]*(?:\.\d+)?"
_DENOM = re.compile(
    # "241 of 294", "13/13", "11 of the 13 Bots", "8 out of all 11".
    #
    # THE ARTICLE IS THE POINT. The first version of this pattern was
    # `\d+\s*(?:of|/|out of)\s*\d+` and it rejected 26 of 44 real claims — every one of them
    # correctly stating its denominator as "11 of THE 13 Bots". The determiner between the two
    # numbers is how people actually write, and a gate that only accepts the shape its author
    # happened to type is a gate that measures its author, not the claims.
    rf"\b{_N}\s*(?:of|/|out of)\s*(?:the|its|all|those|these)?\s*{_N}\b"
    # NOTE the absence of `\b` after `%`: `%` is a non-word character, so `%\b` demands a word
    # character immediately after it and therefore never matches "35% of ...". The selftest
    # caught that one on the first run, which is the only reason it is not in a post right now.
    rf"|\b{_N}%.*\bof\b"  # 3.18% of its 100% weekly allowance
    rf"|\b{_N}\s*(?:vs|versus)\s*{_N}\b",  # 563 vs 663
    re.IGNORECASE,
)
# Mood words that stand in for a measurement. Their presence is not fatal on its own; their
# presence INSTEAD of a denominator is.
_MOOD = (
    "most",
    "many",
    "several",
    "a lot",
    "huge",
    "massive",
    "dramatically",
    "significantly",
    "everyone",
    "nobody",
    "always",
    "never",
)


# Unambiguous evidence that the INSTRUMENT failed, not that the claim is false. Kept narrow on
# purpose: a broad "error" match would swallow real output, and `gb triage` legitimately prints
# the word ERROR while carrying a correct verdict.
_CRASHED = re.compile(
    r"Traceback \(most recent call last\)"
    r"|^\s*(?:KeyError|IndexError|TypeError|AttributeError|ValueError|"
    r"ModuleNotFoundError|ImportError|JSONDecodeError|FileNotFoundError):",
    re.MULTILINE,
)
MAX_CLAIM_CHARS = 240  # one post's claim line; longer is a thread, not a claim


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _today() -> str:
    return dt.date.today().isoformat()


# ----------------------------------------------------------------------------------------------
# the claim


@dataclasses.dataclass(frozen=True)
class Claim:
    """One publishable finding, and everything required to prove it in public.

    `number` is carried SEPARATELY from `claim` on purpose. Verification asserts that this
    exact string appears in the command's own output; if the number lived only inside the prose
    we would be regex-guessing which digits mattered, and a verifier that guesses is a verifier
    that passes the wrong thing.
    """

    id: str
    claim: str
    number: str
    reproduce: Optional[str]
    teaches: str
    surprise: int  # 0-3, how counterintuitive
    actionability: int  # 0-3, how directly it changes what someone does
    caveat: Optional[str] = None
    source: Optional[str] = None

    @property
    def teaching_value(self) -> int:
        return self.surprise * self.actionability

    def structural_problems(self) -> List[str]:
        """Everything wrong with this claim that needs no network to see.

        Checked BEFORE the command runs, because a claim that is malformed cannot be rescued
        by a passing command — and running commands is the expensive half.
        """
        bad: List[str] = []
        if not self.id or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", self.id):
            bad.append(f"id {self.id!r} is not a stable lowercase slug")
        if not self.claim.strip():
            bad.append("claim is empty")
        if len(self.claim) > MAX_CLAIM_CHARS:
            bad.append(
                f"claim is {len(self.claim)} chars, over the {MAX_CLAIM_CHARS} limit"
            )
        if not self.number.strip():
            bad.append("number is empty — a claim with no number is an opinion")
        if self.number.strip() and self.number not in self.claim:
            bad.append(
                f"number {self.number!r} does not appear in its own claim, so the two can drift"
            )
        if not _DENOM.search(self.claim):
            moods = [m for m in _MOOD if m in self.claim.lower()]
            bad.append(
                "claim states no denominator"
                + (f" and leans on {moods[0]!r} instead" if moods else "")
            )
        if not self.teaches.strip():
            bad.append(
                "teaches nothing — a finding that changes no behaviour is trivia"
            )
        if self.reproduce is None:
            bad.append("no reproduce command: unverifiable, therefore unpublishable")
        elif not self.reproduce.strip():
            bad.append("reproduce command is blank")
        for field, lo, hi in (("surprise", 0, 3), ("actionability", 0, 3)):
            v = getattr(self, field)
            if not isinstance(v, int) or not lo <= v <= hi:
                bad.append(f"{field}={v!r} is outside {lo}-{hi}")
        return bad


@dataclasses.dataclass(frozen=True)
class Verdict:
    """What happened when a claim was put to its own command."""

    claim_id: str
    state: str  # VERIFIED | REFUSED | UNRUNNABLE
    detail: str
    exit_code: Optional[int] = None

    @property
    def publishable(self) -> bool:
        return self.state == "VERIFIED"


# ----------------------------------------------------------------------------------------------
# verification — the whole point


def verify_claim(
    c: Claim, timeout_s: int = 300, runner: Optional[Callable[..., Any]] = None
) -> Verdict:
    """Run the claim's own command and REQUIRE its number in the output.

    Three outcomes, kept distinct because they demand different responses:

      VERIFIED  — the command ran and its output contains the number. Publishable.
      REFUSED   — the command ran and the number is ABSENT. This is the interesting one: it
                  means the claim is stale or was never true. Do not soften it, do not
                  re-word the prose to match the new output without looking; the delta is
                  itself a finding, which is how five of tonight's six falsifications surfaced.
      UNRUNNABLE— the command could not execute (missing file, timeout, non-zero with no
                  output). NOT the same as refused: a broken instrument proves nothing either
                  way, and reporting it as a refusal would be the same "empty result equals
                  absence" error the house rules forbid.

    A non-zero exit is NOT automatically fatal. `gb triage` exits 1 to CARRY a RED verdict, and
    `gb dogfood audit` exits 1 when gaps exist — both are working correctly and both print the
    numbers we quote. So the test is the OUTPUT, with the exit code recorded alongside it.
    """
    if c.reproduce is None or not c.reproduce.strip():
        return Verdict(c.id, "UNRUNNABLE", "no command attached")
    run = runner or subprocess.run
    try:
        proc = run(
            c.reproduce,
            shell=True,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return Verdict(c.id, "UNRUNNABLE", f"command exceeded {timeout_s}s")
    except OSError as e:
        return Verdict(c.id, "UNRUNNABLE", f"{type(e).__name__}: {e}")

    out = (getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")
    code = getattr(proc, "returncode", None)
    if not out.strip():
        return Verdict(
            c.id, "UNRUNNABLE", f"command produced NO output (exit {code})", code
        )
    # A CRASHED COMMAND IS NOT A REFUTATION. Measured 2026-09-12: two of the first eight real
    # claims came back REFUSED because my reproduce command indexed `['result']['routines']`
    # on an envelope that carries `routines` at the top level. The command raised, printed a
    # traceback, and the traceback is non-empty output — so the verifier read "number absent"
    # and reported the CLAIM as stale. Both claims were true.
    #
    # That is the same error this whole file exists to prevent, pointed backwards: an empty or
    # broken result treated as evidence about the world. A traceback is unambiguous evidence
    # about the INSTRUMENT, so it gets its own verdict and never impugns the claim.
    if _CRASHED.search(out):
        first = next(
            (ln.strip() for ln in reversed(out.strip().splitlines()) if ln.strip()), ""
        )
        return Verdict(
            c.id,
            "UNRUNNABLE",
            f"the COMMAND crashed, which says nothing about the claim: {first[:90]}",
            code,
        )
    # Match on the digits, tolerating thousands separators on either side: a producer may print
    # "2,583" where the claim says "2583", and treating that as a refusal would be a false
    # falsification — the same error class, pointed the other way.
    needle = c.number.strip()
    hay = out.replace(",", "")
    if needle in out or needle.replace(",", "") in hay:
        return Verdict(
            c.id, "VERIFIED", f"number present in output (exit {code})", code
        )
    return Verdict(
        c.id,
        "REFUSED",
        f"number {needle!r} ABSENT from the output of its own command (exit {code}) — "
        "the claim is stale or was never true; re-measure before rewording",
        code,
    )


# ----------------------------------------------------------------------------------------------
# the ledger


def read_ledger(path: pathlib.Path = LEDGER) -> Dict[str, Any]:
    doc = load(path) or {}
    if not isinstance(doc, dict):
        return {"schema": "gb-post-ledger/1", "entries": []}
    doc.setdefault("schema", "gb-post-ledger/1")
    doc.setdefault("entries", [])
    return doc


def ledger_state(c: Claim, doc: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """NEW, SUPERSEDED, or PUBLISHED — and the number we published last time.

    SUPERSEDED is a first-class state, not a synonym for seen. A claim we published at 241 that
    now measures 238 is the most valuable post available: it is a measured change in the
    ecosystem, with our own prior number as the baseline. Collapsing it into "already posted"
    would throw away the only asset this repo accumulates.
    """
    prior = [
        e for e in doc.get("entries", []) if isinstance(e, dict) and e.get("id") == c.id
    ]
    if not prior:
        return "NEW", None
    last = prior[-1]
    was = str(last.get("number", ""))
    if was and was != c.number:
        return "SUPERSEDED", was
    return "PUBLISHED", was


def record(claims: Sequence[Claim], path: pathlib.Path = LEDGER) -> int:
    doc = read_ledger(path)
    today = _today()
    for c in claims:
        doc["entries"].append(
            {"id": c.id, "number": c.number, "published_at": today, "claim": c.claim}
        )
    gbtypes.atomic_write_text(path, json.dumps(doc, indent=1) + "\n")
    return len(claims)


# ----------------------------------------------------------------------------------------------
# rendering


def render_post(c: Claim, state: str, was: Optional[str]) -> str:
    """One post: the claim, the command, what to do, the caveat. In that order, always.

    The command goes ABOVE the lesson deliberately. A reader who runs it first arrives at the
    lesson having already measured their own deployment, which is the only way a number about
    someone else's fleet becomes actionable about theirs.
    """
    lines: List[str] = []
    if state == "SUPERSEDED" and was:
        lines.append(
            f"Correction to something I posted: it was {was}. It is now {c.number}."
        )
        lines.append("")
    lines.append(c.claim)
    lines.append("")
    lines.append("Run it on your own deployment:")
    lines.append(f"  {c.reproduce}")
    lines.append("")
    lines.append(f"What to do with that: {c.teaches}")
    if c.caveat:
        lines.append("")
        lines.append(f"Caveat: {c.caveat}")
    return "\n".join(lines)


def render_table(rows: List[Tuple[Claim, Verdict, str, Optional[str]]]) -> str:
    out: List[str] = []
    out.append(f"  {'STATE':<11} {'LEDGER':<11} {'TV':>3}  {'ID':<26} CLAIM")
    out.append("  " + "-" * 104)
    for c, v, state, _was in rows:
        out.append(
            f"  {v.state:<11} {state:<11} {c.teaching_value:>3}  {c.id:<26} {c.claim[:52]}"
        )
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------
# claim sources


def _coerce(row: Dict[str, Any]) -> Optional[Claim]:
    """A findings row -> a Claim, or None if it lacks the required fields.

    Returning None rather than raising is deliberate: an upstream producer growing a new row
    kind should not take the publisher down, and the count of skipped rows is reported so the
    silence is visible.
    """
    try:
        return Claim(
            id=str(row["id"]),
            claim=str(row.get("claim", "")),
            number=str(row.get("number", "")),
            reproduce=row.get("reproduce"),
            teaches=str(row.get("teaches", "")),
            surprise=int(row.get("surprise", 0)),
            actionability=int(row.get("actionability", 0)),
            caveat=row.get("caveat"),
            source=row.get("source"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def load_claims(root: pathlib.Path = ROOT) -> Tuple[List[Claim], List[str]]:
    """MERGE every findings artifact, newest-wins per claim id. Reports what it skipped.

    Not "read the newest file". Findings ACCUMULATE from several producers — one mines the
    verbs, another mines the doc gaps, a third will mine something not written yet — and they
    land as separate artifacts. Reading only the newest file means whichever producer ran last
    silently deletes every other producer's supply, and the loss is invisible because the
    output still looks like a full list.

    Newest-wins PER ID rather than per file, so a re-measured claim supersedes its own older
    row without taking unrelated claims down with it. That is the same lesson as the roster
    dedupe earlier tonight, where sorting by label instead of freshness let a stale artifact
    mask a routine that had demonstrably run.
    """
    notes: List[str] = []
    arts = (
        sorted((root / "findings").glob("*.json"))
        if (root / "findings").is_dir()
        else []
    )
    if not arts:
        return [], ["no findings/*.json artifact — run `gb findings` first"]
    by_id: Dict[str, Claim] = {}
    skipped = 0
    for art in arts:  # oldest first, so later files overwrite earlier ones per id
        doc = load(art) or {}
        rows = doc.get("rows") or doc.get("result", {}).get("rows") or []
        kept = 0
        for r in rows:
            if not isinstance(r, dict):
                skipped += 1
                continue
            c = _coerce(r)
            if c is None:
                skipped += 1
                continue
            by_id[c.id] = c
            kept += 1
        notes.append(f"{art.name}: {kept} claim(s)")
    notes.append(f"merged {len(by_id)} distinct claim(s) from {len(arts)} artifact(s)")
    if skipped:
        notes.append(f"SKIPPED {skipped} row(s) that lacked the required claim fields")
    return list(by_id.values()), notes


def rank(claims: Sequence[Claim]) -> List[Claim]:
    """Teaching value first; then the cheaper command; then id, so the order is total.

    "Cheaper" is approximated by command length, which is crude but honest about being crude:
    the real cost signal would be a measured runtime, and we do not have one until `verify`
    has run. Ties broken by id keep the output stable across runs, which matters because an
    unstable sort makes a diff of yesterday's drafts unreadable.
    """
    return sorted(
        claims,
        key=lambda c: (-c.teaching_value, len(c.reproduce or "z" * 999), c.id),
    )


# ----------------------------------------------------------------------------------------------
# actions


def cmd_draft(a: argparse.Namespace) -> int:
    claims, notes = load_claims()
    if not claims:
        for n in notes:
            print(f"gb post: {n}", file=sys.stderr)
        return EXIT_ENVIRONMENT

    doc = read_ledger()
    structural: List[Tuple[Claim, List[str]]] = []
    ok: List[Claim] = []
    for c in claims:
        probs = c.structural_problems()
        (structural.append((c, probs)) if probs else ok.append(c))

    rows: List[Tuple[Claim, Verdict, str, Optional[str]]] = []
    for c in rank(ok):
        state, was = ledger_state(c, doc)
        if state == "PUBLISHED" and not a.include_published:
            continue
        v = (
            verify_claim(c, timeout_s=a.timeout)
            if not a.no_verify
            else Verdict(c.id, "UNRUNNABLE", "verification skipped by --no-verify")
        )
        rows.append((c, v, state, was))
        if a.limit and len([r for r in rows if r[1].publishable]) >= a.limit:
            break

    publishable = [r for r in rows if r[1].publishable]

    if a.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "captured_at": _now(),
                    "result": {
                        "claims_read": len(claims),
                        "structurally_rejected": len(structural),
                        "verified": len(publishable),
                        "refused": len([r for r in rows if r[1].state == "REFUSED"]),
                        "unrunnable": len(
                            [r for r in rows if r[1].state == "UNRUNNABLE"]
                        ),
                        "posts": [
                            {
                                "id": c.id,
                                "state": v.state,
                                "ledger": st,
                                "teaching_value": c.teaching_value,
                                "text": render_post(c, st, was),
                            }
                            for c, v, st, was in publishable
                        ],
                        "rejections": [
                            {"id": c.id, "problems": p} for c, p in structural
                        ]
                        + [
                            {"id": c.id, "problems": [v.detail]}
                            for c, v, _s, _w in rows
                            if not v.publishable
                        ],
                        "notes": notes,
                    },
                },
                indent=1,
            )
        )
        return 0 if publishable else EXIT_REFUSED

    for n in notes:
        print(f"  note: {n}")
    if structural:
        print(
            f"\n  STRUCTURALLY REJECTED — {len(structural)} claim(s), before any command ran:"
        )
        for c, probs in structural:
            print(f"    {c.id}")
            for p in probs:
                print(f"      - {p}")
    print()
    print(render_table(rows))
    for c, v, st, was in rows:
        if not v.publishable:
            continue
        print("\n" + "=" * 106)
        print(render_post(c, st, was))
    bad = [r for r in rows if r[1].state == "REFUSED"]
    if bad:
        print("\n" + "=" * 106)
        print(
            "  REFUSED — the command did not reproduce the number. Re-measure, do not reword:"
        )
        for c, v, _s, _w in bad:
            print(f"    {c.id}: {v.detail}")
    print(
        f"\n  {len(publishable)} publishable · {len(bad)} refused · "
        f"{len([r for r in rows if r[1].state == 'UNRUNNABLE'])} unrunnable · "
        f"{len(structural)} malformed"
    )
    if a.out and publishable:
        OUT_DIR.mkdir(exist_ok=True)
        path = OUT_DIR / f"{_now().replace(':', '')}.md"
        body = "\n\n---\n\n".join(
            render_post(c, st, was) for c, _v, st, was in publishable
        )
        gbtypes.atomic_write_text(path, body + "\n")
        print(f"  wrote {path.relative_to(ROOT)}")
    return 0 if publishable else EXIT_REFUSED


def cmd_verify(a: argparse.Namespace) -> int:
    """Re-run every claim in the ledger. The drift report is the product."""
    claims, notes = load_claims()
    if not claims:
        for n in notes:
            print(f"gb post: {n}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    doc = read_ledger()
    published = {e.get("id") for e in doc.get("entries", []) if isinstance(e, dict)}
    subject = [c for c in claims if c.id in published] if published else claims
    results = [(c, verify_claim(c, timeout_s=a.timeout)) for c in subject]
    drift = [(c, v) for c, v in results if v.state == "REFUSED"]
    if a.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "captured_at": _now(),
                    "result": {
                        "checked": len(results),
                        "verified": len([1 for _c, v in results if v.publishable]),
                        "drifted": len(drift),
                        "rows": [
                            {"id": c.id, "state": v.state, "detail": v.detail}
                            for c, v in results
                        ],
                    },
                },
                indent=1,
            )
        )
        return EXIT_REFUSED if drift else 0
    for c, v in results:
        print(f"  {v.state:<11} {c.id:<28} {v.detail[:60]}")
    print(f"\n  {len(results)} checked · {len(drift)} DRIFTED")
    if drift:
        print("  a drifted claim is a follow-up post, not an error to hide")
    return EXIT_REFUSED if drift else 0


def cmd_ledger(a: argparse.Namespace) -> int:
    doc = read_ledger()
    entries = [e for e in doc.get("entries", []) if isinstance(e, dict)]
    if a.json:
        print(json.dumps({"schema": SCHEMA, "result": {"entries": entries}}, indent=1))
        return 0
    if not entries:
        print("  nothing published yet")
        return 0
    for e in entries:
        print(
            f"  {e.get('published_at','?'):<12} {e.get('id',''):<28} {e.get('number','')}"
        )
    print(
        f"\n  {len(entries)} published · {len({e.get('id') for e in entries})} distinct claim(s)"
    )
    return 0


def cmd_record(a: argparse.Namespace) -> int:
    """Record ONLY claims that verify right now. Publishing is the gate's payload."""
    claims, _notes = load_claims()
    want = set(a.ids or [])
    if not want:
        print("gb post record: name at least one claim id", file=sys.stderr)
        return EXIT_USAGE
    chosen = [c for c in claims if c.id in want]
    missing = want - {c.id for c in chosen}
    if missing:
        print(
            f"gb post record: unknown claim id(s): {sorted(missing)}", file=sys.stderr
        )
        return EXIT_USAGE
    bad = [(c, c.structural_problems()) for c in chosen]
    malformed = [(c, p) for c, p in bad if p]
    if malformed:
        for c, p in malformed:
            print(f"gb post record: {c.id} is malformed: {p[0]}", file=sys.stderr)
        return EXIT_REFUSED
    verdicts = [(c, verify_claim(c, timeout_s=a.timeout)) for c in chosen]
    refused = [(c, v) for c, v in verdicts if not v.publishable]
    if refused:
        for c, v in refused:
            print(f"gb post record: {c.id} REFUSED — {v.detail}", file=sys.stderr)
        return EXIT_REFUSED
    n = record([c for c, _v in verdicts])
    print(f"  recorded {n} claim(s) in {LEDGER.name}")
    return 0


# ----------------------------------------------------------------------------------------------
# selftest


def _fake_run(stdout: str, code: int = 0) -> Callable[..., Any]:
    class P:
        def __init__(self) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = code

    def run(*_a: Any, **_k: Any) -> Any:
        return P()

    return run


def _claim(**over: Any) -> Claim:
    base: Dict[str, Any] = dict(
        id="unserved-connectors",
        claim="241 of 294 integrations builders name are served by no installable connector.",
        number="241",
        reproduce="./bin/gb demand rank",
        teaches="check your own gaps before shopping the marketplace",
        surprise=2,
        actionability=3,
    )
    base.update(over)
    return Claim(**base)


def selftest() -> int:
    fails: List[str] = []
    n = 0

    def check(ok: bool, label: str) -> None:
        nonlocal n
        n += 1
        if not ok:
            fails.append(label)

    # --- structure: a well-formed claim passes, and each rule fires on its own known-bad ---
    check(_claim().structural_problems() == [], "a well-formed claim was rejected")

    check(
        any(
            "denominator" in p
            for p in _claim(
                claim="Most integrations are unserved.", number="241"
            ).structural_problems()
        ),
        "a claim with no denominator was accepted",
    )
    check(
        any(
            "no reproduce command" in p
            for p in _claim(reproduce=None).structural_problems()
        ),
        "a claim with no command was accepted",
    )
    check(
        any(
            "does not appear in its own claim" in p
            for p in _claim(number="999").structural_problems()
        ),
        "a number absent from its own claim was accepted",
    )
    check(
        any("teaches nothing" in p for p in _claim(teaches="").structural_problems()),
        "a claim that teaches nothing was accepted",
    )
    check(
        any(
            "stable lowercase slug" in p
            for p in _claim(id="Bad ID!").structural_problems()
        ),
        "a non-slug id was accepted",
    )
    check(
        any(
            "over the" in p
            for p in _claim(claim="241 of 294. " + "x" * 300).structural_problems()
        ),
        "an over-length claim was accepted",
    )
    check(
        any("outside 0-3" in p for p in _claim(surprise=9).structural_problems()),
        "an out-of-range score was accepted",
    )
    check(
        any("an opinion" in p for p in _claim(number="").structural_problems()),
        "a claim with no number was accepted",
    )

    # --- the denominator matcher: the three accepted shapes, and a rejected one ---
    # Every shape below is taken VERBATIM from a real claim this gate once wrongly rejected,
    # or from one it must keep rejecting. Synthetic examples are what let the first version
    # pass while refusing 26 of 44 real rows.
    for good in (
        "241 of 294 things",
        "13/13 verbs",
        "563 vs 663 chars",
        "35% of all of them",
        "11 of the 13 Bots on this account carry ZERO routines",  # the article case
        "195 of the 645 published Bots name Grok",
        "consumed 3.18% of its 100% weekly allowance",  # decimals + two percents
        "10 of the 16 registered MCP servers",
        "8 out of all 11 charters",
    ):
        check(bool(_DENOM.search(good)), f"denominator matcher missed {good!r}")
    check(
        not _DENOM.search("lots of things happened"),
        "denominator matcher fired on prose",
    )

    # --- verification: the three outcomes, each proven ---
    v = verify_claim(_claim(), runner=_fake_run("gap 241 of 294\n"))
    check(
        v.state == "VERIFIED" and v.publishable, f"a reproducing command gave {v.state}"
    )

    v = verify_claim(_claim(), runner=_fake_run("gap 238 of 294\n"))
    check(
        v.state == "REFUSED", f"a NON-reproducing command gave {v.state}, not REFUSED"
    )
    check("ABSENT" in v.detail, "the refusal did not say the number was absent")

    v = verify_claim(_claim(), runner=_fake_run("", code=0))
    check(v.state == "UNRUNNABLE", f"empty output gave {v.state}, not UNRUNNABLE")

    # A CRASH IS NOT A REFUTATION. This exact pair of fixtures is what two real claims looked
    # like when a wrong dict key in my reproduce command got them reported as stale.
    crash = (
        'Traceback (most recent call last):\n  File "<string>", line 1\n'
        "KeyError: 'result'\n"
    )
    v = verify_claim(_claim(), runner=_fake_run(crash, code=1))
    check(v.state == "UNRUNNABLE", f"a traceback gave {v.state}, not UNRUNNABLE")
    check(
        "says nothing about the claim" in v.detail, "the crash verdict blamed the claim"
    )
    # and the bare exception line, without the Traceback header, which is what `python3 -c`
    # emits through some shells
    v = verify_claim(_claim(), runner=_fake_run("KeyError: 'result'\n", code=1))
    check(v.state == "UNRUNNABLE", "a bare KeyError line was treated as a refusal")
    # the guard must NOT swallow real output: `gb triage` prints ERROR while carrying a verdict
    v = verify_claim(
        _claim(), runner=_fake_run("gate ERROR - gap 241 of 294\n", code=2)
    )
    check(
        v.state == "VERIFIED",
        "the crash guard swallowed legitimate output containing ERROR",
    )

    v = verify_claim(_claim(reproduce=None))
    check(
        v.state == "UNRUNNABLE" and not v.publishable,
        "a command-less claim was not UNRUNNABLE",
    )

    # a non-zero exit that still prints the number must PASS: `gb triage` exits 1 to carry RED
    v = verify_claim(_claim(), runner=_fake_run("gap 241 of 294\n", code=1))
    check(v.state == "VERIFIED", "a RED-carrying exit 1 was treated as failure")

    # thousands separators must not manufacture a false refusal — the inverse error class
    v = verify_claim(
        _claim(number="2583", claim="2583 of 2583 files shipped."),
        runner=_fake_run("export: OK · 2,583 files"),
    )
    check(v.state == "VERIFIED", "a comma-formatted number was falsely REFUSED")

    # --- the ledger: NEW vs SUPERSEDED vs PUBLISHED are three states, not two ---
    doc: Dict[str, Any] = {"entries": []}
    check(ledger_state(_claim(), doc)[0] == "NEW", "an unpublished claim was not NEW")
    doc = {"entries": [{"id": "unserved-connectors", "number": "241"}]}
    check(
        ledger_state(_claim(), doc)[0] == "PUBLISHED",
        "a republished identical claim was not PUBLISHED",
    )
    st, was = ledger_state(
        _claim(number="238", claim="238 of 294 integrations are unserved."), doc
    )
    check(
        st == "SUPERSEDED" and was == "241",
        f"a CHANGED number gave {st}/{was}, not SUPERSEDED/241",
    )

    # --- ranking: teaching value first, cheaper command breaks the tie, order is total ---
    a1 = _claim(id="a-high", surprise=3, actionability=3)
    b1 = _claim(id="b-low", surprise=1, actionability=1)
    check(
        [c.id for c in rank([b1, a1])] == ["a-high", "b-low"],
        "ranking ignored teaching value",
    )
    short = _claim(id="c-short", reproduce="./bin/gb x")
    long = _claim(
        id="d-long", reproduce="./bin/gb x --json | python3 -c 'lots and lots of code'"
    )
    check(
        [c.id for c in rank([long, short])] == ["c-short", "d-long"],
        "tie did not prefer the cheaper command",
    )
    check(rank([a1, b1]) == rank([b1, a1]), "ranking is not stable across input order")

    # --- rendering: the four required parts are present, in order ---
    text = render_post(_claim(caveat="one account, not a population"), "NEW", None)
    check("241 of 294" in text, "the rendered post lost its claim")
    check("./bin/gb demand rank" in text, "the rendered post lost its command")
    check("What to do with that:" in text, "the rendered post lost its lesson")
    check("Caveat:" in text, "the rendered post lost its caveat")
    check(
        text.index("./bin/gb demand rank") < text.index("What to do with that:"),
        "the command must come BEFORE the lesson so a reader measures first",
    )
    sup = render_post(
        _claim(number="238", claim="238 of 294 are unserved."), "SUPERSEDED", "241"
    )
    check(
        sup.startswith("Correction"),
        "a superseded claim did not lead with the correction",
    )

    # --- coercion: a malformed upstream row is skipped, not fatal ---
    check(
        _coerce({"id": "x-y-z", "claim": "1 of 2", "number": "1", "teaches": "t"})
        is not None,
        "a valid row failed to coerce",
    )
    check(_coerce({"no": "id"}) is None, "a row with no id coerced anyway")
    check(
        _coerce({"id": "a-b", "surprise": "not-an-int"}) is None,
        "a bad score coerced anyway",
    )

    for f in fails:
        print(f"FAIL: {f}")
    print(f"SELFTEST {'FAIL' if fails else 'PASS'} - {n - len(fails)}/{n} properties")
    return 1 if fails else 0


# ----------------------------------------------------------------------------------------------

HANDLERS: Dict[str, Callable[[argparse.Namespace], int]] = {
    "draft": cmd_draft,
    "verify": cmd_verify,
    "ledger": cmd_ledger,
    "record": cmd_record,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gb-post.py",
        description="posts that verify their own numbers before they ship",
    )
    p.add_argument(
        "action", nargs="?", default="draft", choices=sorted(HANDLERS) + ["selftest"]
    )
    p.add_argument("--json", action="store_true", help="machine-readable envelope")
    p.add_argument(
        "--selftest", action="store_true", help="inline fixtures, no network"
    )
    p.add_argument(
        "--limit", type=int, default=0, help="stop after N publishable posts"
    )
    p.add_argument("--timeout", type=int, default=300, help="per-command seconds")
    p.add_argument(
        "--no-verify", action="store_true", help="skip verification (drafts only)"
    )
    p.add_argument(
        "--include-published", action="store_true", help="do not hide ledger repeats"
    )
    p.add_argument("--out", action="store_true", help="also write posts/<ISO>.md")
    p.add_argument("--ids", nargs="*", help="claim ids, for `record`")
    return p


def body() -> int:
    ns = build_parser().parse_args()
    if ns.selftest or ns.action == "selftest":
        return selftest()
    return HANDLERS[ns.action](ns)


if __name__ == "__main__":
    gbtypes.main(body)
