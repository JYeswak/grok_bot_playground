#!/usr/bin/env python3
"""gb-mine — mine two evidence corpora for capabilities this deployment could adopt.

`gb-gaps.py` says where the Grok Bot ecosystem is thin. It answers that from three maps this
repo already holds, and it joins the result against `mirror-candidates.json` — a file that was
mined BY HAND, once, on 2026-09-10, with `ripwire --for=<domain>` run inside 14 checkouts. A
hand-mined join is a photograph: it cannot notice that franken_ocr gained a table extractor last
week, and nobody will re-run 14 checkouts by hand to find out.

This is that mining, made repeatable and given a second corpus:

  franken-harvest (`fh`)   306 ledger rows + 59219 indexed technical files across 89 sources,
                           already ranked, already carrying commit-pinned file:line evidence.
  ripwire over the mirror  the 222-repo Dicklesworthstone checkout on /Volumes/ZestData. `fh`
                           names WHICH repo is relevant; ripwire is then run inside only those
                           checkouts to rank the symbols a reader would actually imitate.

The two compose on purpose. Running ripwire across 222 repos costs minutes (frankenterm alone:
8.35s measured, warm); running it inside the 3 repos `fh` already ranked costs seconds, and the
repo choice is then evidence rather than a guess. When `fh` names nothing, the fallback is a
name-token match over the mirror's directory listing — weaker, and labelled as such in the
artifact's `repo_selection` field rather than passed off as the same thing.

THE DISTINCTION THIS FILE EXISTS TO PRESERVE. Each source reports OK / EMPTY / UNREACHABLE, not
a count. An unmounted /Volumes/ZestData is UNREACHABLE with a remediation — it is NOT "zero
candidates from the mirror". Measured: with `--mirror /nonexistent` this run still returns 10
candidates from `fh` and refuses to say anything at all about what the mirror holds.

EXIT CODES (this repo's dictionary; see `gb help exit-codes`):

    0   OK           both sources read, and neither offered a candidate — a proven-empty mine
    1   FINDINGS     there are candidates to triage
    2   USAGE        bad invocation (--limit below 1)
    3   ENVIRONMENT  no source could be read at all, OR a source was UNREACHABLE and the rest
                     found nothing — absence was not established, so 0 would be a lie
    130 CANCELLED    SIGINT arrived; no partial artifact is left behind

  gb-mine.py                                    # default queries, write mine/<stamp>.json
  gb-mine.py --query "OCR table extraction" --limit 5
  gb-mine.py --json --dry-run                   # nothing is written
  gb-mine.py --mirror /nonexistent              # proves the UNREACHABLE leg
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import json
import os
import pathlib
import shutil
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import arg, parse  # noqa: E402
from gbtypes import Proc, atomic_write_json, main  # noqa: E402
from gbtypes import run as run_child  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-mine/1"
SUMMARY = (
    "mine franken-harvest and the Dicklesworthstone mirror for adoptable capabilities"
)

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE, EXIT_ENVIRONMENT = 0, 1, 2, 3

DEFAULT_MIRROR = "/Volumes/ZestData/dicklesworthstone-mirror"

# The default queries are this repo's OWN measured gaps, not a wishlist: every one of them is a
# row in MARKET-GAPS.md's domain table where the 322-plugin catalog scores zero. Three, not six,
# because each costs one `fh` call plus its share of the ripwire budget, and a default that takes
# a minute is a default nobody runs.
DEFAULT_QUERIES: Tuple[str, ...] = (
    "OCR extraction of tables and text from scanned PDF documents",
    "semantic search with embeddings and reranking over a corpus",
    "speech transcription and text to speech synthesis",
)

# Bounds. `fh` is a warm local index (measured 2.43-7.82s per call). ripwire parses a whole
# checkout; frankenterm — 187468 symbols scored — took 8.35s warm, so 180s is ~20x the measured
# worst case and only fires on something pathological.
FH_TIMEOUT_S = 90.0
RIPWIRE_TIMEOUT_S = 180.0
# The whole point of letting `fh` choose the repos is that a handful is enough. Three keeps a
# default run under ~40s; raising it trades wall time for breadth with no other effect.
MAX_RIPWIRE_RUNS = 3
MAX_MIRROR_ENTRIES = 4096
EVIDENCE_EXCERPT_CHARS = 400
WHY_CHARS = 200

# Where the two tools live on this machine. `shutil.which` first, so a PATH install wins; the
# literal path is the fallback for a cron/launchd context whose PATH does not include ~/.local/bin.
FH_FALLBACK = "~/.local/bin/fh"
RIPWIRE_FALLBACK = "~/.local/bin/ripwire"


class State(str, enum.Enum):
    """What a source actually did. Three values, because there are three outcomes and collapsing
    the last two into "0 results" is the specific lie this repo was built to stop telling."""

    OK = "OK"
    EMPTY = "EMPTY"
    UNREACHABLE = "UNREACHABLE"


@dataclasses.dataclass(frozen=True)
class Probe:
    """One child invocation, recorded whether it worked or not. A source's status is a CLAIM;
    these are the receipts for it."""

    argv: Tuple[str, ...]
    code: int
    elapsed_s: float
    timed_out: bool
    truncated: bool
    note: str = ""

    @classmethod
    def of(cls, proc: Proc, note: str = "") -> "Probe":
        return cls(
            argv=proc.argv,
            code=proc.code,
            elapsed_s=proc.elapsed_s,
            timed_out=proc.timed_out,
            truncated=proc.truncated,
            note=note,
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "argv": list(self.argv),
            "exit": self.code,
            "elapsed_s": self.elapsed_s,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "note": self.note,
        }


@dataclasses.dataclass(frozen=True)
class Source:
    """One corpus's verdict. `remediation` is mandatory on UNREACHABLE and empty otherwise —
    an unreachable source that does not say how to reach it is a dead end, not a report."""

    name: str
    state: State
    detail: str
    remediation: str
    probes: Tuple[Probe, ...] = ()
    extra: Optional[Dict[str, Any]] = None

    @property
    def read(self) -> bool:
        """Did this source answer at all? EMPTY is an answer; UNREACHABLE is not."""
        return self.state is not State.UNREACHABLE

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "source": self.name,
            "status": self.state.value,
            "detail": self.detail,
            "probes": [p.to_json() for p in self.probes],
        }
        if self.remediation:
            out["remediation"] = self.remediation
        if self.extra:
            out.update(self.extra)
        return out


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One adoptable thing, with the address a reader can open."""

    source: str
    query: str
    repo: str
    row: str
    evidence: str
    why: str
    source_rank: int
    score: Optional[float] = None
    excerpt: str = ""
    rank: int = 0

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "rank": self.rank,
            "source": self.source,
            "query": self.query,
            "repo": self.repo,
            "row": self.row,
            "evidence": self.evidence,
            "why": self.why,
            "source_rank": self.source_rank,
        }
        if self.score is not None:
            out["score"] = self.score
        if self.excerpt:
            out["evidence_excerpt"] = self.excerpt
        return out

    def render(self) -> str:
        return f"  {self.rank:>2}. [{self.source}] {self.evidence}\n      {self.why}"


# ---------------------------------------------------------------------------------------------
# Locating the tools
# ---------------------------------------------------------------------------------------------
def locate(name: str, fallback: str) -> Optional[str]:
    """PATH first, then the known install location. None means the tool is not on this machine,
    which is UNREACHABLE — never an empty corpus."""
    found = shutil.which(name)
    if found:
        return found
    candidate = pathlib.Path(fallback).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def clip(text: str, limit: int) -> str:
    """One line, bounded. Artifacts are read in a terminal and diffed in git; a 4 KB headline
    wrapped across 30 lines destroys both."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    # Both upstreams truncate their own strings with a trailing ellipsis; appending a second one
    # produces "……", which reads as corruption rather than as elision.
    return flat[: limit - 1].rstrip().rstrip("\u2026").rstrip() + "\u2026"


# ---------------------------------------------------------------------------------------------
# Source 1 — franken-harvest
# ---------------------------------------------------------------------------------------------
def fh_search(
    binary: str, query: str, limit: int
) -> Tuple[Proc, Optional[Dict[str, Any]]]:
    """One `fh search --json`, parsed WHATEVER the exit code was.

    Measured 2026-09-11, and the reason this function does not short-circuit on `proc.code`:
    `fh search "zzqqxwv gorplenak frobnitzery"` exits **4** with a complete envelope carrying
    `code: "EMPTY"`, `failure_kind: "SEARCH_QUERY_MATCHED_NOTHING"`, and the hint "no false hit
    was returned". That is a SUCCESSFUL read of the corpus that found nothing — the exact thing
    this producer must not relabel as UNREACHABLE. So the envelope, not the exit code, decides;
    the exit code only matters when there is no envelope to read.
    """
    proc = run_child(
        [binary, "search", "--json", "--limit", str(limit), query],
        timeout_s=FH_TIMEOUT_S,
    )
    try:
        parsed = json.loads(proc.out)
    except (json.JSONDecodeError, ValueError):
        return proc, None
    return proc, parsed if isinstance(parsed, dict) else None


def fh_candidates(rows: Sequence[Dict[str, Any]], query: str) -> List[Candidate]:
    """Project `fh`'s `results[]` onto this repo's candidate shape.

    Observed keys on 2026-09-11 (`fh` schema_version 1.0.0): rank, score, row_id, row_kind,
    headline, mirror_path, ledger_line, source_revision, repository, evidence — and `line_end`,
    which is present on some rows and ABSENT on others, so every read here is a `.get`.
    """
    out: List[Candidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("row_id") or "")
        repo = str(row.get("repository") or "?")
        path = str(row.get("mirror_path") or "")
        line = row.get("ledger_line")
        evidence = path or (f"{repo} row {row_id}" if row_id else repo)
        if path and ":" not in path and isinstance(line, int):
            evidence = f"{path}:{line}"
        headline = clip(str(row.get("headline") or ""), WHY_CHARS)
        kind = str(row.get("row_kind") or "row")
        why = headline or f"{kind} row matching {query!r}"
        raw_score = row.get("score")
        out.append(
            Candidate(
                source="franken-harvest",
                query=query,
                repo=repo,
                row=row_id or path,
                evidence=evidence,
                why=why,
                source_rank=int(row.get("rank") or (len(out) + 1)),
                score=float(raw_score) if isinstance(raw_score, (int, float)) else None,
                excerpt=clip(str(row.get("evidence") or ""), EVIDENCE_EXCERPT_CHARS),
            )
        )
    return out


# `fh` answers "the corpus holds nothing for this query" and "the corpus is unreadable" with the
# SAME `code: "EMPTY"`, the SAME `success: false`, and the SAME exit 4. Only `failure_kind`
# separates them, so only `failure_kind` is read here — the whole point of this producer is that
# those two are not the same answer.
#
# Measured 2026-09-11 across all three retrieval verbs, gibberish query vs
# FRANKEN_HARVEST_LEDGER=/nonexistent, every cell exit 4 / code EMPTY / success false:
#
#   verb       matched nothing                 corpus unreadable
#   search     SEARCH_QUERY_MATCHED_NOTHING    SEARCH_CORPUS_UNREADABLE
#   suggest    SEARCH_QUERY_MATCHED_NOTHING    SEARCH_CORPUS_UNREADABLE
#   why        WHY_QUERY_NO_MATCH              SEARCH_CORPUS_UNREADABLE
#
# `why` does NOT reuse search's spelling, so this is a SET and not a constant: a later edit that
# reaches for `fh why` and inherits a one-string test would label a genuine empty UNREACHABLE.
FH_MATCHED_NOTHING = frozenset({"SEARCH_QUERY_MATCHED_NOTHING", "WHY_QUERY_NO_MATCH"})


def fh_read(envelope: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Did this `fh` call READ the corpus, and what should the probe record say?

    Returns (read, note). `read` false means the source did not answer for this query; it is
    NOT "the corpus is empty", and the caller must never render it as one.
    """
    if envelope is None:
        return False, "no parseable JSON envelope on stdout"
    if envelope.get("success") is True:
        return True, ""
    kind = str(envelope.get("failure_kind") or envelope.get("code") or "unknown")
    error = envelope.get("error")
    message = str(error.get("message") or "") if isinstance(error, dict) else ""
    if kind in FH_MATCHED_NOTHING:
        return True, f"{kind}: {clip(message, 160)}"
    return False, f"{kind}: {clip(message, 160)}"


def mine_fh(queries: Sequence[str], limit: int) -> Tuple[Source, List[Candidate]]:
    """Search the harvest once per query. A query that errors does not sink the source: its
    probe is recorded and the rest still run, because a partial read is still a read."""
    binary = locate("fh", FH_FALLBACK)
    if binary is None:
        return (
            Source(
                name="franken-harvest",
                state=State.UNREACHABLE,
                detail="the `fh` executable is not on PATH and not at " + FH_FALLBACK,
                remediation=(
                    "install franken-harvest, or add its install directory to PATH; "
                    "verify with `fh --info`"
                ),
            ),
            [],
        )

    probes: List[Probe] = []
    found: List[Candidate] = []
    failures = 0
    corpus: Dict[str, Any] = {}
    hint = ""
    for query in queries:
        proc, envelope = fh_search(binary, query, limit)
        answered, note = fh_read(envelope)
        envelope = envelope or {}
        if not answered:
            failures += 1
            error = envelope.get("error")
            if isinstance(error, dict) and error.get("hint"):
                hint = clip(str(error["hint"]), 200)
            probes.append(
                Probe.of(proc, note or clip(proc.err, 160) or "non-zero exit")
            )
            continue
        data = envelope.get("data")
        data = data if isinstance(data, dict) else {}
        rows = data.get("results")
        rows = rows if isinstance(rows, list) else []
        probes.append(
            Probe.of(
                proc,
                note
                or (
                    f"code={envelope.get('code')} result_count={data.get('result_count')} "
                    f"hits_found={data.get('hits_found')}"
                ),
            )
        )
        if not corpus and data:
            corpus = {
                "fh_schema_version": envelope.get("schema_version"),
                "ledger_path": data.get("ledger_path"),
                "indexed_rows": data.get("indexed_rows"),
                "indexed_technical_files": data.get("indexed_technical_files"),
                "technical_corpus_loaded": data.get("technical_corpus_loaded"),
                "retrieval_scope": data.get("retrieval_scope"),
                "match_mode": data.get("match_mode"),
            }
        found.extend(fh_candidates(rows, query))

    if failures == len(queries):
        detail = (
            f"all {failures} `fh search` call(s) failed before reading the corpus — see probes "
            f"for each call's failure_kind"
        )
        return (
            Source(
                name="franken-harvest",
                state=State.UNREACHABLE,
                detail=detail,
                remediation=hint
                or (
                    "run `fh doctor` — the ledger, bead index, or doc index is not resolvable "
                    "from this environment"
                ),
                probes=tuple(probes),
            ),
            [],
        )

    state = State.OK if found else State.EMPTY
    detail = (
        f"{len(found)} ranked row(s) across {len(queries)} query/queries"
        if found
        else f"{len(queries)} query/queries read the ledger and matched nothing"
    )
    if failures:
        detail += f" ({failures} query/queries errored and are recorded in probes)"
    return (
        Source(
            name="franken-harvest",
            state=state,
            detail=detail,
            remediation="",
            probes=tuple(probes),
            extra={"binary": binary, "corpus": corpus}
            if corpus
            else {"binary": binary},
        ),
        found,
    )


# ---------------------------------------------------------------------------------------------
# Source 2 — ripwire over the mirror
# ---------------------------------------------------------------------------------------------
def mirror_repos(mirror: pathlib.Path) -> List[str]:
    """The checkout names, bounded. Only directories: MANIFEST.json and CORPUS-INDEX.md sit at
    the same level and are not repos."""
    names: List[str] = []
    try:
        with os.scandir(mirror) as entries:
            for entry in entries:
                if len(names) >= MAX_MIRROR_ENTRIES:
                    break
                if entry.is_dir() and not entry.name.startswith("."):
                    names.append(entry.name)
    except OSError:
        return []
    return sorted(names)


def pick_repos(
    present: Sequence[str],
    harvest: Sequence[Candidate],
    queries: Sequence[str],
) -> Tuple[List[Tuple[str, str]], str]:
    """Choose which checkouts are worth a ripwire run, and SAY WHICH WAY THEY WERE CHOSEN.

    Preferred: the repos `fh` already ranked, in its rank order, each paired with the query that
    surfaced it. That makes the choice evidence. Fallback, only when the harvest named nothing
    reachable: a name-token match over the mirror listing — genuinely weaker, so the artifact
    records `repo_selection` and the caller can discount accordingly.

    Ties are broken by the order the QUERIES were given, then by name: every query produces a
    rank-1 row, so without that the first checkout scanned would be decided by the alphabet.
    """
    available = set(present)
    order = {q: i for i, q in enumerate(queries)}
    chosen: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    ranked = sorted(
        harvest, key=lambda c: (c.source_rank, order.get(c.query, len(order)), c.repo)
    )
    for cand in ranked:
        if cand.repo in seen or cand.repo not in available:
            continue
        seen.add(cand.repo)
        chosen.append((cand.repo, cand.query))
        if len(chosen) >= MAX_RIPWIRE_RUNS:
            break
    if chosen:
        return chosen, "franken-harvest rank order"

    scored: List[Tuple[int, int, str, str]] = []
    for index, query in enumerate(queries):
        flat = "".join(c if c.isalnum() else " " for c in query.lower())
        tokens = [t for t in flat.split() if len(t) >= 4]
        for name in present:
            hits = sum(1 for t in tokens if t in name.lower())
            if hits:
                scored.append((-hits, index, name, query))
    for _, _, name, query in sorted(scored):
        if name in seen:
            continue
        seen.add(name)
        chosen.append((name, query))
        if len(chosen) >= MAX_RIPWIRE_RUNS:
            break
    if chosen:
        return chosen, "fallback: query-token match against mirror directory names"
    return [], "no repo could be selected"


def ripwire_for(
    binary: str, repo_dir: pathlib.Path, query: str
) -> Tuple[Proc, Optional[Dict[str, Any]]]:
    """One `ripwire <dir> --for=<task> --json`.

    `--for` is used rather than `--exemplar` for one measured reason: on 2026-09-11 `ripwire
    . --exemplar=... --json` exited 1 with "--json is not yet supported for --exemplar". The
    task lens is the one that emits a machine-readable envelope, so it is the one used here.
    """
    proc = run_child(
        [binary, str(repo_dir), f"--for={query}", "--json"],
        timeout_s=RIPWIRE_TIMEOUT_S,
        cwd=repo_dir,
    )
    if proc.code != 0:
        return proc, None
    try:
        parsed = json.loads(proc.out)
    except (json.JSONDecodeError, ValueError):
        return proc, None
    return proc, parsed if isinstance(parsed, dict) else None


def ripwire_candidates(
    envelope: Dict[str, Any], repo: str, query: str, limit: int
) -> List[Candidate]:
    """Project ripwire's `sigs[]` onto this repo's candidate shape.

    Observed keys on 2026-09-11: root carries at, task, route, confidence, margin_pct, kept,
    scored, corpus, bundle, lens, doc_mentions, tail{total,shown,files}, sigs[]. A `sigs` row
    carries l (line), n (name), p (path), sig, r (rank), cx, ccx, in (callers), churn, amp — and
    OPTIONALLY id and `tested`, so both are read defensively.
    """
    sigs = envelope.get("sigs")
    sigs = sigs if isinstance(sigs, list) else []
    revision = str(envelope.get("at") or "")
    # ripwire grades its own answer. A `low` confidence or `weak=true` bundle means the ranking
    # is a shrug, and a candidate that does not carry that grade invites a reader to trust it.
    grade = f"ripwire confidence={envelope.get('confidence')} weak={bool(envelope.get('weak'))}"
    out: List[Candidate] = []
    for row in sigs:
        if not isinstance(row, dict):
            continue
        path = str(row.get("p") or "")
        line = row.get("l")
        if not path or not isinstance(line, int):
            continue
        callers = row.get("in")
        facts = []
        if isinstance(callers, int) and callers:
            facts.append(f"{callers} callers")
        if row.get("tested") is True:
            facts.append("covered by a test")
        churn = row.get("churn")
        if isinstance(churn, int) and churn:
            facts.append(f"churn {churn}")
        suffix = f" \u2014 {', '.join(facts)}" if facts else ""
        out.append(
            Candidate(
                source="ripwire-mirror",
                query=query,
                repo=repo,
                row=str(row.get("id") or row.get("n") or path),
                evidence=f"{repo}/{path}:{line}",
                why=clip(str(row.get("sig") or row.get("n") or "") + suffix, WHY_CHARS),
                source_rank=int(row.get("r") or (len(out) + 1)),
                excerpt=f"revision {revision} \u00b7 {grade}" if revision else grade,
            )
        )
        if len(out) >= limit:
            break
    return out


def mine_ripwire(
    mirror: pathlib.Path,
    queries: Sequence[str],
    harvest: Sequence[Candidate],
    limit: int,
) -> Tuple[Source, List[Candidate]]:
    """Rank symbols inside the checkouts worth opening. Unreachable for two distinct reasons —
    no binary, or no mounted mirror — and each says how to fix ITS OWN cause."""
    binary = locate("ripwire", RIPWIRE_FALLBACK)
    if binary is None:
        return (
            Source(
                name="ripwire-mirror",
                state=State.UNREACHABLE,
                detail="the `ripwire` executable is not on PATH and not at "
                + RIPWIRE_FALLBACK,
                remediation="install ripwire, or add its install directory to PATH",
                extra={"mirror": str(mirror)},
            ),
            [],
        )
    if not mirror.is_dir():
        return (
            Source(
                name="ripwire-mirror",
                state=State.UNREACHABLE,
                detail=(
                    f"{mirror} is not a readable directory — the mirror was NOT read, so this "
                    f"run makes no claim about what it holds"
                ),
                remediation=(
                    f"mount the volume that carries {mirror} (the default lives on "
                    f"/Volumes/ZestData), or pass --mirror at a present checkout root"
                ),
                extra={"mirror": str(mirror), "binary": binary},
            ),
            [],
        )

    present = mirror_repos(mirror)
    if not present:
        return (
            Source(
                name="ripwire-mirror",
                state=State.UNREACHABLE,
                detail=f"{mirror} exists but lists no repository directories",
                remediation=(
                    f"check {mirror} is the mirror ROOT (it should contain one directory per "
                    f"repository) and that this user can read it"
                ),
                extra={"mirror": str(mirror), "binary": binary},
            ),
            [],
        )

    chosen, selection = pick_repos(present, harvest, queries)
    extra: Dict[str, Any] = {
        "mirror": str(mirror),
        "binary": binary,
        "repos_available": len(present),
        "repo_selection": selection,
        "repos_scanned": [{"repo": r, "query": q} for r, q in chosen],
        "run_cap": MAX_RIPWIRE_RUNS,
    }
    if not chosen:
        return (
            Source(
                name="ripwire-mirror",
                state=State.EMPTY,
                detail=(
                    f"{len(present)} checkouts are present, but neither the harvest ranking nor "
                    f"a name-token match selected one to scan"
                ),
                remediation="",
                extra=extra,
            ),
            [],
        )

    probes: List[Probe] = []
    found: List[Candidate] = []
    failures = 0
    for repo, query in chosen:
        proc, envelope = ripwire_for(binary, mirror / repo, query)
        if envelope is None:
            failures += 1
            probes.append(
                Probe.of(
                    proc,
                    f"{repo}: "
                    + (
                        "timed out"
                        if proc.timed_out
                        else clip(proc.err, 160) or "no parseable JSON"
                    ),
                )
            )
            continue
        rows = ripwire_candidates(envelope, repo, query, limit)
        probes.append(
            Probe.of(
                proc,
                f"{repo}: scored={envelope.get('scored')} corpus={envelope.get('corpus')} "
                f"confidence={envelope.get('confidence')} weak={bool(envelope.get('weak'))} "
                f"symbols={len(rows)}",
            )
        )
        found.extend(rows)

    if failures == len(chosen):
        return (
            Source(
                name="ripwire-mirror",
                state=State.UNREACHABLE,
                detail=f"all {failures} ripwire run(s) failed inside {mirror}",
                remediation=(
                    "run `ripwire <one-checkout> --for=test --json` by hand — the checkouts are "
                    "present but ripwire could not parse them from this environment"
                ),
                probes=tuple(probes),
                extra=extra,
            ),
            [],
        )

    state = State.OK if found else State.EMPTY
    detail = (
        f"{len(found)} ranked symbol(s) across {len(chosen)} checkout(s) of {len(present)}"
        if found
        else f"{len(chosen)} checkout(s) parsed and ranked nothing for these queries"
    )
    if failures:
        detail += f" ({failures} run(s) errored and are recorded in probes)"
    return (
        Source(
            name="ripwire-mirror",
            state=state,
            detail=detail,
            remediation="",
            probes=tuple(probes),
            extra=extra,
        ),
        found,
    )


# ---------------------------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------------------------
def interleave(groups: Sequence[Sequence[Candidate]], limit: int) -> List[Candidate]:
    """Round-robin the sources by their OWN rank, then number the result.

    The two sources score on incommensurable scales — `fh` emits a BM25-ish float around 0.03,
    ripwire emits a PageRank position — so a merged sort on "score" would be arithmetic on units
    that do not share a zero. Round-robin makes no such claim: it says only that each source's
    best row outranks each source's second-best, which is the strongest true statement available.
    """
    ordered = [sorted(g, key=lambda c: c.source_rank) for g in groups]
    out: List[Candidate] = []
    depth = 0
    while len(out) < limit and any(depth < len(g) for g in ordered):
        for group in ordered:
            if depth < len(group) and len(out) < limit:
                out.append(group[depth])
        depth += 1
    return [dataclasses.replace(c, rank=i + 1) for i, c in enumerate(out)]


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class MineArgs:
    """Mine franken-harvest and the Dicklesworthstone mirror for adoptable capabilities."""

    query: Optional[List[str]] = arg(
        metavar="STR",
        help="what to mine for; repeatable (default: this repo's own zero-plugin domain gaps)",
    )
    limit: int = arg(
        default=10, metavar="N", help="maximum candidates to emit (default 10)"
    )
    mirror: str = arg(
        default=DEFAULT_MIRROR,
        metavar="PATH",
        help=f"Dicklesworthstone mirror root (default {DEFAULT_MIRROR})",
    )
    dry_run: bool = arg(help="mine and report, but write no artifact")
    json: bool = arg(help="machine-readable envelope on stdout")


def render(
    payload: Dict[str, Any], candidates: Sequence[Candidate], sources: Sequence[Source]
) -> None:
    """Human output. stdout is the data; the diagnostics went to stderr already."""
    print(
        f"gb-mine \u2014 {payload['queries_run']} query/queries, {len(candidates)} candidate(s)"
    )
    print()
    for src in sources:
        print(f"  {src.state.value:<12} {src.name}: {src.detail}")
        if src.remediation:
            print(f"               remediation: {src.remediation}")
    if candidates:
        print()
        print("candidates (round-robin across sources, by each source's own rank):")
        for cand in candidates:
            print(cand.render())


def body() -> int:
    a = parse(MineArgs, description=SUMMARY)
    if a.limit < 1:
        print("gb-mine: --limit must be at least 1", file=sys.stderr)
        return EXIT_USAGE

    queries: Tuple[str, ...] = (
        tuple(q for q in (a.query or []) if q.strip()) or DEFAULT_QUERIES
    )
    mirror = pathlib.Path(a.mirror).expanduser()

    harvest_src, harvest = mine_fh(queries, a.limit)
    ripwire_src, mirror_rows = mine_ripwire(mirror, queries, harvest, a.limit)
    sources = [harvest_src, ripwire_src]
    candidates = interleave([harvest, mirror_rows], a.limit)

    unreachable = [s.name for s in sources if not s.read]
    if not any(s.read for s in sources):
        code, status = EXIT_ENVIRONMENT, "ENVIRONMENT"
    elif candidates:
        code, status = EXIT_FINDINGS, "FINDINGS"
    elif unreachable:
        # Absence was not established: one corpus never answered. Reporting OK here would be the
        # exact substitution — EMPTY for UNREACHABLE — this producer exists to refuse.
        code, status = EXIT_ENVIRONMENT, "ENVIRONMENT"
    else:
        code, status = EXIT_OK, "OK"

    stamp = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}"
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "mined_at": stamp,
        "status": status,
        "exit": code,
        "mirror": str(mirror),
        "queries": list(queries),
        "queries_run": len(queries),
        "limit": a.limit,
        "method": (
            "fh search --json per query for ledger/technical/doc rows; ripwire <repo> "
            "--for=<query> --json inside the checkouts fh ranked highest"
        ),
        "ranking": "round-robin across sources by each source's own rank; scores are not comparable",
        "sources": [s.to_json() for s in sources],
        "candidates_total": len(harvest) + len(mirror_rows),
        "candidates": [c.to_json() for c in candidates],
        "unreachable_sources": unreachable,
    }

    # The path goes into the payload BEFORE the write: an artifact that does not name itself
    # cannot be cited, and `"artifact": null` inside a file on disk is a self-evident lie.
    artifact: Optional[pathlib.Path] = (
        None if a.dry_run else ROOT / "mine" / f"{stamp}.json"
    )
    payload["artifact"] = str(artifact.relative_to(ROOT)) if artifact else None
    if artifact is not None:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(artifact, payload)

    if a.json:
        print(json.dumps(payload, indent=1))
    else:
        render(payload, candidates, sources)

    for src in sources:
        if not src.read:
            print(
                f"gb-mine: {src.name} UNREACHABLE \u2014 {src.detail}\n"
                f"         remediation: {src.remediation}",
                file=sys.stderr,
            )
    if artifact is not None and not a.json:
        print(f"\nwrote {artifact.relative_to(ROOT)}", file=sys.stderr)
    return code


if __name__ == "__main__":
    main(body)
