#!/usr/bin/env python3
"""gb-templates — the Bot templates this repo proposes, and the rules that keep them proposable.

WHY A VALIDATOR AND NOT A FOLDER. The measurement that produced these templates is not a style
preference, it is a mechanism. Measured 2026-09-11 on this account (`utilization/`): 10 Bots, 2
routines in existence, **0 routines that have ever produced a run**, and **0 memory shards
carrying content**. Measured the same day across 645 attributed Bots built by other people
(`usecases/`): the median CAPTURED charter is 625 characters, and 94.6% of them carry 3
integrations or fewer. This account's median charter is 1,029 characters.

Those two facts are the same fact. A 1,000-character department charter cannot be put in a
routine prompt, because "be the CRM" is not an instruction that starts and finishes; "sweep for
deals with no contact in 14 days" is. So the cap in here is not tidiness — a charter over the cap
is a charter that will never be scheduled, and this file refuses it while it is still cheap to
fix. Every rule below is in that shape: it fires on the thing that would make the template
un-runnable, and nothing else.

  gb-templates.py list                 # id, job, chars, integrations, cadence
  gb-templates.py show <id>            # the whole template, charter rendered ready to paste
  gb-templates.py validate             # every rule; exit 1 on any violation
  gb-templates.py stats                # our charter lengths against the two MEASURED baselines
  gb-templates.py deploy <id> --apply  # create the Bot AND its routine; dry run without --apply
  gb-templates.py <verb> --json        # the same, machine-readable
  gb-templates.py --selftest           # the rules fire on known-bad templates

EXIT CODES
    0   OK / valid / selftest passed
    1   a template violates a rule, or the selftest failed
    2   bad invocation, or `show` was given an id that does not exist
    4   `deploy --apply` PARTIAL: the Bot was created and its routine could not be CONFIRMED in
        the API. Distinct from 1 because the account did change and the Bot is still there —
        the manifest names it, and rolling back is the operator's call, not this tool's.
    130 cancelled (SIGINT); 143 (SIGTERM) — via `gbtypes.main`
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_text, main as gbmain, read_json_capped, run  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

SCHEMA = "gb-template/1"

# The 15 keys, in the order `show` prints them. A template is a fixed record, not a bag: a
# missing key is a template that a reader will silently skip a section of, and an extra key is a
# field somebody is relying on that nothing validates.
KEYS: Tuple[str, ...] = (
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
ROUTINE_KEYS: Tuple[str, ...] = ("cadence", "when", "prompt", "writes")
ROUTINE_OPT_KEYS: Tuple[str, ...] = ("prompt_provenance",)
PROVENANCE: Tuple[str, ...] = ("author", "curator")

# THE CAP, AND WHY THIS NUMBER. 900 sits above the corpus's captured median (625) and above its
# 90th percentile of 968 only by accident — it is not a percentile. It is the length past which a
# charter stops being a job description and becomes a constitution, which is the observed state
# of all 10 Bots on this account (median 1,029, none scheduled). Raising it would remove the only
# mechanical pressure toward a schedulable Bot that this repo has.
CHARTER_MAX = 900
# Corpus mean is 1.878 real integrations among the 450 Bots that have any; 94.6% carry <= 3.
INTEGRATION_MAX = 3

TIERS = ("A", "B", "C")
# The six categories the corpus actually uses, measured: Personal 178, Productivity 146,
# Marketing 128, Ops 114, Sales 59, Success 20.
CATEGORIES = ("Personal", "Productivity", "Marketing", "Ops", "Sales", "Success")
CADENCES = ("daily", "weekly", "none")

# Integrations that let a Bot act OUTSIDE the chat thread — send, publish, book, buy. The list is
# deliberately short and named: anything on it makes `approval_boundary` mandatory. An
# integration absent from this list is not "safe", it is UNCLASSIFIED, and a template using one
# is refused rather than waved through, because the default for an unknown power is not trust.
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
    # Google Sheets added 2026-09-12. Measured contradiction, not a wishlist entry: six persona
    # Bots across the 12 packs name `sheets-mcp`, and no template could declare it because the
    # name was absent here — so a pack told an operator to use Sheets while the template layer
    # was structurally forbidden from saying so. Docs, Drive and Calendar were already
    # classified; Sheets is the same first-party family with the same power (it writes a
    # document outside the chat), so its absence was an oversight rather than a decision.
    # Still UNCLASSIFIED and still refused: Linear, Reddit and Transcript, each promised by a
    # pack. Those are genuinely new powers and need a decision, not a quiet addition — they are
    # roadmap row `packs-promise-tools-templates-cannot-name`.
    "Google Sheets",
}
READ_ONLY_OK = {
    "Grok"
}  # names an account's own model surface, not an outbound capability

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PLACEHOLDER = re.compile(r"\b(todo|tbd|fixme|lorem|xxx|your bot here)\b", re.I)
# `verify` has to name something a human can go and look at. These are the shapes an observable
# takes in practice: a quoted command or field, a transition, or something countable.
OBSERVABLE = re.compile(
    r"(`|->|\bgoes\b|count|\brises\b|\bfalls\b|\bzero\b|\bunchanged\b)", re.I
)
# ...and these are the shapes a FEELING takes. `verify: "it feels faster"` is the failure this
# rule exists for, and it is the most natural thing in the world to write.
FEELING = re.compile(
    r"\b(feels?|feeling|seems?|vibe|nicer|smoother|more useful|better overall)\b", re.I
)
SEND_POSTURE = re.compile(
    r"(draft only|read only|never sends?|sends? nothing|send nothing|publishes nothing|never publish)",
    re.I,
)
RELAY_CLAUSE = re.compile(r"another bot", re.I)
# The cadence-"none" exemption has to be argued, not asserted. One of these has to appear in
# `why_this_shape` alongside the word "schedul": a reason the job has no recurring trigger.
UNSCHEDULABLE_REASON = re.compile(
    r"(cannot|could only|no recurring input|does not exist until|nothing to wake|on demand)",
    re.I,
)


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------
def load_all(path: pathlib.Path) -> List[Tuple[pathlib.Path, Any]]:
    """Every `*.json` under `path`, ascending by filename. Unparseable files are loaded as None
    and reported by `validate` — dropping them here would turn a broken template into an absent
    one, which is the one outcome a validator must never produce."""
    if not path.is_dir():
        return []
    return [(p, read_json_capped(p)) for p in sorted(path.glob("*.json"))]


def valid_only(path: pathlib.Path) -> List[Dict[str, Any]]:
    """The templates that pass. `list`/`show`/`stats` read through this so they can never render
    a template that `validate` rejects."""
    out = []
    for p, doc in load_all(path):
        if isinstance(doc, dict) and not check_one(p, doc):
            out.append(doc)
    return sorted(out, key=lambda d: (str(d.get("tier")), str(d.get("id"))))


# ---------------------------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------------------------
def check_one(path: pathlib.Path, doc: Any) -> List[str]:
    """Return the problems with one template. Empty means it is proposable."""
    bad: List[str] = []
    name = path.name
    if not isinstance(doc, dict):
        return [f"{name}: not a JSON object (unparseable or the wrong shape)"]

    missing = [k for k in KEYS if k not in doc]
    extra = [k for k in doc if k not in KEYS]
    if missing:
        bad.append(f"{name}: {len(missing)} key(s) missing: {', '.join(missing)}")
    if extra:
        bad.append(f"{name}: {len(extra)} unknown key(s): {', '.join(sorted(extra))}")

    if doc.get("schema") != SCHEMA:
        bad.append(f"{name}: schema is {doc.get('schema')!r}, not {SCHEMA!r}")

    tid = doc.get("id") or ""
    if not isinstance(tid, str) or not KEBAB.match(tid):
        bad.append(f"{name}: id {tid!r} is not kebab-case")
    elif tid != path.stem:
        bad.append(f"{name}: id {tid!r} does not match the filename stem {path.stem!r}")

    if doc.get("tier") not in TIERS:
        bad.append(f"{name}: tier {doc.get('tier')!r} is not one of {TIERS}")
    if doc.get("category") not in CATEGORIES:
        bad.append(
            f"{name}: category {doc.get('category')!r} is not one of {CATEGORIES}"
        )

    for k in ("name", "job", "charter", "memory", "verify", "why_this_shape"):
        v = doc.get(k)
        if not isinstance(v, str) or not v.strip():
            bad.append(f"{name}: {k} is empty")

    job = doc.get("job")
    if isinstance(job, str) and job.strip():
        if len(re.findall(r"[.!?](?:\s|$)", job)) != 1:
            bad.append(f"{name}: job is not one sentence ({job[:48]!r}…)")
        if len(job) > 180:
            bad.append(
                f"{name}: job is {len(job)} chars — a job that needs 180 is two jobs"
            )

    # THE CAP. Two rules, because they fail differently: a wrong count is a stale template
    # somebody hand-edited, and an over-long charter is a Bot that will never be scheduled.
    charter = doc.get("charter")
    chars = doc.get("charter_chars")
    if isinstance(charter, str):
        if (
            not isinstance(chars, int)
            or isinstance(chars, bool)
            or chars != len(charter)
        ):
            bad.append(
                f"{name}: charter_chars is {chars!r} but the charter is {len(charter)} chars"
            )
        if len(charter) > CHARTER_MAX:
            bad.append(
                f"{name}: charter is {len(charter)} chars, over the {CHARTER_MAX} cap — "
                f"this is the length at which a charter stops fitting in a routine prompt"
            )

    ints = doc.get("integrations")
    if not isinstance(ints, list) or any(
        not isinstance(i, str) or not i.strip() for i in ints
    ):
        bad.append(f"{name}: integrations is not a list of names")
        ints = []
    elif len(ints) > INTEGRATION_MAX:
        bad.append(
            f"{name}: {len(ints)} integrations, over the {INTEGRATION_MAX} cap "
            f"(corpus mean is 1.878 among Bots that have any)"
        )
    unknown = [i for i in ints if i not in ACTS_OUTSIDE_CHAT and i not in READ_ONLY_OK]
    if unknown:
        bad.append(
            f"{name}: unclassified integration(s) {unknown} — add them to ACTS_OUTSIDE_CHAT "
            f"or READ_ONLY_OK; an unclassified power cannot be gated"
        )

    bad += _check_routine(name, doc)
    bad += _check_boundary(name, doc, ints)

    verify = doc.get("verify")
    if isinstance(verify, str) and verify.strip():
        if FEELING.search(verify):
            bad.append(
                f"{name}: verify describes a feeling, not an observable "
                f"({FEELING.search(verify).group(0)!r})"
            )
        if not OBSERVABLE.search(verify):
            bad.append(
                f"{name}: verify names nothing observable — it needs a command, a field, a "
                f"transition (`0 -> 1`) or something countable"
            )

    replaces = doc.get("replaces")
    if replaces is not None and not (isinstance(replaces, str) and replaces.strip()):
        bad.append(f"{name}: replaces must be a non-empty string or null")
    if doc.get("tier") == "B" and not replaces:
        bad.append(
            f"{name}: tier B is 'narrows a department Bot' — replaces must name which one"
        )

    for k in (
        "name",
        "job",
        "charter",
        "verify",
        "why_this_shape",
        "approval_boundary",
    ):
        v = doc.get(k)
        if isinstance(v, str) and PLACEHOLDER.search(v):
            bad.append(
                f"{name}: {k} still holds placeholder text "
                f"({PLACEHOLDER.search(v).group(0)!r})"
            )
    return bad


def _check_routine(name: str, doc: Dict[str, Any]) -> List[str]:
    """A routine is either runnable or argued for. There is no third state.

    This is the rule the whole file is built around: 2 routines exist on this account and 0 have
    ever run, so a template that ships without a schedulable prompt reproduces the deficit it was
    written to close. `cadence: none` stays available — some jobs genuinely have no recurring
    trigger — but it costs an argument in `why_this_shape`, not an assertion.
    """
    bad: List[str] = []
    r = doc.get("routine")
    if not isinstance(r, dict):
        return [f"{name}: routine is not an object (use cadence 'none', never null)"]
    missing = [k for k in ROUTINE_KEYS if k not in r]
    extra = [k for k in r if k not in ROUTINE_KEYS and k not in ROUTINE_OPT_KEYS]
    if missing:
        bad.append(f"{name}: routine is missing {', '.join(missing)}")
    if extra:
        bad.append(f"{name}: routine has unknown key(s) {', '.join(sorted(extra))}")
    if "prompt_provenance" in r and r["prompt_provenance"] not in PROVENANCE:
        bad.append(
            f"{name}: routine.prompt_provenance is {r['prompt_provenance']!r},"
            f" not one of {list(PROVENANCE)} (author = own words as the deploy"
            f" message; curator = transcribed, never quoted back as instructions)"
        )
    cadence = r.get("cadence")
    if cadence not in CADENCES:
        return bad + [f"{name}: routine.cadence {cadence!r} is not one of {CADENCES}"]

    filled = [k for k in ("when", "prompt", "writes") if str(r.get(k) or "").strip()]
    if cadence == "none":
        if filled:
            bad.append(
                f"{name}: cadence is 'none' but {', '.join(filled)} is filled in — "
                f"one of the two is wrong"
            )
        why = str(doc.get("why_this_shape") or "")
        if not (re.search(r"schedul", why, re.I) and UNSCHEDULABLE_REASON.search(why)):
            bad.append(
                f"{name}: cadence 'none' is unargued — why_this_shape must say why this job "
                f"has no recurring trigger (0 of this account's routines have ever run; an "
                f"unschedulable template has to earn the exemption)"
            )
    else:
        for k in ("when", "prompt", "writes"):
            if not str(r.get(k) or "").strip():
                bad.append(f"{name}: cadence {cadence!r} but routine.{k} is empty")
        prompt = str(r.get("prompt") or "")
        if prompt and len(prompt) > 400:
            bad.append(
                f"{name}: routine.prompt is {len(prompt)} chars — if the wake instruction needs "
                f"that much, the job is not narrow yet"
            )
    return bad


def _check_boundary(name: str, doc: Dict[str, Any], ints: List[str]) -> List[str]:
    """A Bot that can act outside the chat states what it may never do without a yes.

    The wording matters and is checked, not just the presence: this account's strongest existing
    pattern is a SEND LOCK carried by its credentialed Bots, whose load-bearing clause is that
    ANOTHER Bot relaying "the operator says send" is not approval. A draft-only Gmail Bot without
    that clause is one relayed message away from sending.
    """
    bad: List[str] = []
    boundary = doc.get("approval_boundary")
    if not isinstance(boundary, str):
        return [f"{name}: approval_boundary must be a string ('' when it cannot act)"]
    acting = [i for i in ints if i in ACTS_OUTSIDE_CHAT]
    if acting and not boundary.strip():
        return [
            f"{name}: {', '.join(acting)} can act outside the chat and approval_boundary is "
            f"empty — an ungated outbound capability is the only unrecoverable mistake here"
        ]
    if not acting and not boundary.strip():
        return bad  # chat-only: nothing to gate, and "" says so honestly
    if acting and not SEND_POSTURE.search(boundary):
        bad.append(
            f"{name}: approval_boundary never states the posture — it must say draft only, "
            f"read only, never sends, or publishes nothing, in those words"
        )
    drafting = re.search(r"draft only", boundary, re.I)
    if drafting and not RELAY_CLAUSE.search(boundary):
        bad.append(
            f"{name}: draft-only boundary is missing the relay clause — it must say that "
            f"another Bot relaying an approval is not approval"
        )
    return bad


def check(path: pathlib.Path) -> List[str]:
    """Every problem across the corpus, plus the corpus-level ones."""
    rows = load_all(path)
    bad: List[str] = []
    if not rows:
        return [f"{path}: no templates — an empty proposal set is not a proposal"]
    for p, doc in rows:
        bad += check_one(p, doc)
    ids = [d.get("id") for _, d in rows if isinstance(d, dict)]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        bad.append(f"duplicate id(s): {', '.join(str(d) for d in dupes)}")
    scheduled = [
        d
        for _, d in rows
        if isinstance(d, dict)
        and isinstance(d.get("routine"), dict)
        and d["routine"].get("cadence") in ("daily", "weekly")
    ]
    if len(rows) > 2 and not scheduled:
        bad.append(
            "not one template carries a runnable routine — the set reproduces the deficit it "
            "exists to close (measured: 2 routines on this account, 0 runs ever)"
        )
    return bad


# ---------------------------------------------------------------------------------------------
# The measured baselines. Read, never typed.
# ---------------------------------------------------------------------------------------------
def _newest(d: pathlib.Path) -> Optional[pathlib.Path]:
    rows = sorted(p for p in d.glob("*.json") if p.name[:10].count("-") == 2)
    return rows[-1] if rows else None


def corpus_baseline(root: pathlib.Path) -> Dict[str, Any]:
    """The 645-Bot attributed corpus, measured from the newest `usecases/` snapshot.

    TWO MEDIANS, BOTH REPORTED, AND THE DIFFERENCE MATTERS. 158 of the 645 rows carry
    `prompt_chars: 0` — the Bot is attributed but its text was never captured. Including those
    zeros gives a median of 557 characters; excluding them gives 625. A charter cannot be zero
    characters long, so the honest comparison for OUR lengths is the captured median, and the
    all-rows figure is printed beside it so nobody has to wonder which one they are holding.
    """
    path = _newest(root / "usecases")
    if path is None:
        return {"available": False, "why": "no usecases/ snapshot on this machine"}
    doc = read_json_capped(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return {"available": False, "why": f"{path.name} is not a usecases snapshot"}
    rows = [r for r in doc["rows"] if isinstance(r, dict)]
    allc = [r["prompt_chars"] for r in rows if isinstance(r.get("prompt_chars"), int)]
    cap = [c for c in allc if c > 0]
    slots = [len(r.get("integrations") or []) for r in rows]
    real = [
        len([i for i in (r.get("integrations") or []) if i not in READ_ONLY_OK])
        for r in rows
    ]
    with_real = [n for n in real if n > 0]
    return {
        "available": True,
        "source": str(path.relative_to(root)),
        "captured_at": doc.get("captured_at"),
        "bots": doc.get("bots") or len(rows),
        "charters_captured": len(cap),
        "charters_uncaptured": len(allc) - len(cap),
        "median_chars_captured": round(statistics.median(cap), 1) if cap else None,
        "median_chars_all_rows": round(statistics.median(allc), 1) if allc else None,
        "mean_chars_captured": round(statistics.mean(cap), 1) if cap else None,
        "share_captured_within_cap": round(
            sum(1 for c in cap if c <= CHARTER_MAX) / len(cap), 3
        )
        if cap
        else None,
        "integrations_mean_all_bots": round(statistics.mean(slots), 3)
        if slots
        else None,
        "integrations_mean_with_a_real_one": round(sum(with_real) / len(with_real), 3)
        if with_real
        else None,
        "share_within_integration_cap": round(
            sum(1 for n in slots if n <= INTEGRATION_MAX) / len(slots), 3
        )
        if slots
        else None,
        "approval_language_share": doc.get("approval_share"),
    }


def account_baseline(root: pathlib.Path) -> Dict[str, Any]:
    """This account's own Bots: how long their charters are, and how much they run.

    `fleet-spec.json` is excluded from the public export by name, so on an installed copy this
    answers `available: false` rather than a number. Only the LENGTH of each description is read
    here; no description text and no Bot name leaves this function.
    """
    out: Dict[str, Any] = {
        "available": False,
        "why": "fleet-spec.json is not on this machine",
    }
    spec = read_json_capped(root / "fleet-spec.json")
    if isinstance(spec, dict) and isinstance(spec.get("bots"), list):
        lens = [
            len(b["description"])
            for b in spec["bots"]
            if isinstance(b, dict) and isinstance(b.get("description"), str)
        ]
        if lens:
            out = {
                "available": True,
                "source": "fleet-spec.json",
                "bots": len(lens),
                "median_chars": round(statistics.median(lens), 1),
                "mean_chars": round(statistics.mean(lens), 1),
                "min_chars": min(lens),
                "max_chars": max(lens),
                "share_within_cap": round(
                    sum(1 for c in lens if c <= CHARTER_MAX) / len(lens), 3
                ),
            }
    util = _newest(root / "utilization")
    doc = read_json_capped(util) if util else None
    if isinstance(doc, dict):
        bots = [b for b in (doc.get("bots") or []) if isinstance(b, dict)]
        out["utilization"] = {
            "source": str(util.relative_to(root)),
            "captured_at": doc.get("captured_at"),
            "bot_count": doc.get("bot_count"),
            "routines_total": doc.get("routines_total"),
            "bots_with_a_routine_that_ran": sum(
                1 for b in bots if int(b.get("routines_with_runs") or 0) > 0
            ),
            "memory_shards_with_content": doc.get("memory_shards_with_content"),
            "idle_credentialed": len(doc.get("idle_credentialed") or []),
        }
    return out


def stats(path: pathlib.Path, root: pathlib.Path) -> Dict[str, Any]:
    rows = valid_only(path)
    chars = [int(d["charter_chars"]) for d in rows]
    ours: Dict[str, Any] = {
        "templates": len(rows),
        "median_chars": round(statistics.median(chars), 1) if chars else None,
        "mean_chars": round(statistics.mean(chars), 1) if chars else None,
        "min_chars": min(chars) if chars else None,
        "max_chars": max(chars) if chars else None,
        "cap": CHARTER_MAX,
        "all_within_cap": all(c <= CHARTER_MAX for c in chars) if chars else None,
        "integrations_mean": round(
            statistics.mean([len(d["integrations"]) for d in rows]), 3
        )
        if rows
        else None,
        "with_a_runnable_routine": sum(
            1 for d in rows if d["routine"]["cadence"] in ("daily", "weekly")
        ),
        "with_an_approval_boundary": sum(
            1 for d in rows if d["approval_boundary"].strip()
        ),
        "by_tier": {t: sum(1 for d in rows if d["tier"] == t) for t in TIERS},
        "narrows_a_department": sum(1 for d in rows if d["replaces"]),
    }
    corpus = corpus_baseline(root)
    account = account_baseline(root)
    deltas: Dict[str, Any] = {}
    if ours["median_chars"] is not None:
        if corpus.get("median_chars_captured"):
            deltas["vs_corpus_captured_median"] = round(
                ours["median_chars"] - corpus["median_chars_captured"], 1
            )
        if account.get("median_chars"):
            d = ours["median_chars"] - account["median_chars"]
            deltas["vs_this_account_median"] = round(d, 1)
            deltas["vs_this_account_percent"] = round(
                100.0 * d / account["median_chars"], 1
            )
    return {
        "schema": "gb-template-stats/1",
        "ours": ours,
        "corpus": corpus,
        "account": account,
        "deltas": deltas,
    }


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------
def emit(text: str) -> None:
    """`print`, except that a reader who walked away is not a failed measurement."""
    try:
        print(text)
    except BrokenPipeError:
        try:
            import os

            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass


def warn(text: str) -> None:
    try:
        print(text, file=sys.stderr)
    except BrokenPipeError:
        try:
            import os

            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stderr.fileno())
        except (OSError, ValueError):
            pass


def _few_ids(ids: List[str], prefer: str, n: int = 6) -> str:
    """Short known-id preview. Prefer hello-computer so a typo still names it."""
    ordered: List[str] = []
    if prefer in ids:
        ordered.append(prefer)
    for item in ids:
        if item not in ordered:
            ordered.append(item)
        if len(ordered) >= n:
            break
    extra = len(ids) - len(ordered)
    if not ordered:
        return "(none)"
    text = ", ".join(ordered)
    if extra > 0:
        text += f", ... +{extra} more"
    return text


def _teach_unknown_template(
    tid: str, rows: List[Dict[str, Any]], verb: str
) -> Tuple[str, str, str]:
    """Return (hint, listing, preview) for an id that is not in the catalog."""
    ids = [str(r.get("id")) for r in rows if r.get("id")]
    preview = _few_ids(ids, "hello-computer")
    hint = f"gb templates {verb} hello-computer"
    listing = "gb templates list"
    return hint, listing, preview


def render_list(rows: List[Dict[str, Any]]) -> str:
    out = [
        f"{'tier':<5}{'id':<20}{'chars':>6}  {'cadence':<8}{'integrations':<18}job",
        "-" * 110,
    ]
    for d in rows:
        ints = ", ".join(d["integrations"]) or "—"
        out.append(
            f"{d['tier']:<5}{d['id']:<20}{d['charter_chars']:>6}  "
            f"{d['routine']['cadence']:<8}{ints:<18}{d['job']}"
        )
    sched = sum(1 for d in rows if d["routine"]["cadence"] in ("daily", "weekly"))
    out.append("")
    out.append(
        f"{len(rows)} template(s) · {sched} with a runnable routine · "
        f"{sum(1 for d in rows if d['replaces'])} narrowing a department Bot · "
        f"charter cap {CHARTER_MAX}"
    )
    return "\n".join(out)


def render_show(d: Dict[str, Any]) -> str:
    r = d["routine"]
    out = [
        f"{d['name']}  ({d['id']} · tier {d['tier']} · {d['category']})",
        "",
        f"JOB          {d['job']}",
        f"REPLACES     {d['replaces'] or '— new job, no department Bot narrowed'}",
        f"INTEGRATIONS {', '.join(d['integrations']) or '— none; it uses the Bot own cloud computer, which every Bot already has'}",
    ]
    if r["cadence"] == "none":
        out.append("ROUTINE      none — chat-invoked (see WHY THIS SHAPE)")
    else:
        out += [
            f"ROUTINE      {r['cadence']}, {r['when']}",
            f"  prompt     {r['prompt']}",
            f"  writes     {r['writes']}",
        ]
    out += [
        "",
        f"APPROVAL BOUNDARY",
        f"  {d['approval_boundary'] or 'cannot act outside the chat — nothing to gate'}",
        "",
        "MEMORY",
        f"  {d['memory']}",
        "",
        "VERIFY",
        f"  {d['verify']}",
        "",
        "WHY THIS SHAPE",
        f"  {d['why_this_shape']}",
        "",
        f"CHARTER — paste into the Bot's description field verbatim ({d['charter_chars']} chars, "
        f"cap {CHARTER_MAX})",
        "-" * 94,
    ]
    out.append(d["charter"])
    out.append("-" * 94)
    return "\n".join(out)


def render_stats(doc: Dict[str, Any]) -> str:
    o, c, a, dl = doc["ours"], doc["corpus"], doc["account"], doc["deltas"]
    out = [
        f"OURS        {o['templates']} template(s) · median {o['median_chars']} chars · "
        f"mean {o['mean_chars']} · range {o['min_chars']}–{o['max_chars']} · "
        f"cap {o['cap']} ({'all within' if o['all_within_cap'] else 'OVER'})",
        f"            {o['with_a_runnable_routine']}/{o['templates']} carry a runnable routine · "
        f"{o['with_an_approval_boundary']} carry an approval boundary · "
        f"{o['integrations_mean']} integrations each · tiers {o['by_tier']}",
        "",
    ]
    if c.get("available"):
        out += [
            f"CORPUS      {c['bots']} attributed Bots ({c['source']}, {c['captured_at']})",
            f"            median charter {c['median_chars_captured']} chars over the "
            f"{c['charters_captured']} with captured text "
            f"({c['median_chars_all_rows']} if the {c['charters_uncaptured']} uncaptured rows "
            f"are counted as 0)",
            f"            {c['share_captured_within_cap']:.1%} of captured charters are within "
            f"our {CHARTER_MAX} cap · {c['share_within_integration_cap']:.1%} carry "
            f"<= {INTEGRATION_MAX} integrations",
            f"            {c['integrations_mean_with_a_real_one']} integrations per Bot among "
            f"those with a real one ({c['integrations_mean_all_bots']} across all) · "
            f"approval language in {c['approval_language_share']:.1%}",
        ]
    else:
        out.append(f"CORPUS      unavailable — {c.get('why')}")
    out.append("")
    if a.get("available"):
        out += [
            f"ACCOUNT     {a['bots']} Bots · median charter {a['median_chars']} chars · "
            f"range {a['min_chars']}–{a['max_chars']} · "
            f"{a['share_within_cap']:.0%} within our cap",
        ]
    else:
        out.append(f"ACCOUNT     charter lengths unavailable — {a.get('why')}")
    u = a.get("utilization")
    if u:
        out.append(
            f"            {u['routines_total']} routine(s) exist across {u['bot_count']} Bots, "
            f"{u['bots_with_a_routine_that_ran']} have ever run · "
            f"{u['memory_shards_with_content']} memory shard(s) with content · "
            f"{u['idle_credentialed']} idle credentialed ({u['source']})"
        )
    if dl:
        out += [
            "",
            f"DELTA       our median is {dl.get('vs_corpus_captured_median', '—')} chars vs the "
            f"corpus captured median, and {dl.get('vs_this_account_median', '—')} chars "
            f"({dl.get('vs_this_account_percent', '—')}%) vs this account's",
        ]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------------------------
GOOD = {
    "schema": SCHEMA,
    "id": "fixture-bot",
    "name": "Fixture Bot",
    "tier": "A",
    "category": "Ops",
    "job": "Do one narrow thing once a week and leave a receipt.",
    "charter": "You are the fixture. Your job: one thing, once a week.",
    "charter_chars": 0,  # set immediately below from the charter itself — never typed by hand
    "integrations": [],
    "routine": {
        "cadence": "weekly",
        "when": "Mon 08:00 local",
        "prompt": "Do the thing and post one line.",
        "writes": "one dated line per week in this thread",
    },
    "approval_boundary": "",
    "memory": "The week count.",
    "verify": "`bin/gb-utilization.py` — routines_with_runs goes 0 -> 1.",
    "why_this_shape": "The smallest template that exercises every rule.",
    "replaces": None,
}
GOOD["charter_chars"] = len(GOOD["charter"])


def _fix(**over: Any) -> Dict[str, Any]:
    d = json.loads(json.dumps(GOOD))
    for k, v in over.items():
        d[k] = v
    if "charter" in over and "charter_chars" not in over:
        d["charter_chars"] = len(d["charter"])
    return d


def selftest() -> int:
    """Every rule fires on a template that breaks exactly it, and the real corpus stays clean.

    A validator is only worth its exit code if a known-bad input reaches it. Each leg below
    plants ONE defect on an otherwise-valid fixture, so a passing leg proves that rule and not a
    neighbour; the last two legs are positive controls, because a validator that rejects
    everything also passes every known-bad leg.
    """
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    with tempfile.TemporaryDirectory() as td:
        box = pathlib.Path(td)

        def probe(doc: Dict[str, Any], stem: Optional[str] = None) -> List[str]:
            p = box / f"{stem or doc.get('id') or 'x'}.json"
            atomic_write_text(p, json.dumps(doc, indent=1) + "\n")
            return check_one(p, doc)

        def fires(
            name: str, doc: Dict[str, Any], needle: str, stem: Optional[str] = None
        ) -> None:
            found = probe(doc, stem)
            hit = [p for p in found if needle in p]
            leg(
                name,
                bool(hit),
                hit[0] if hit else f"nothing matched {needle!r}: {found}",
            )

        leg(
            "known-good-validates",
            not probe(GOOD),
            f"the good fixture must be clean, got {probe(GOOD)}",
        )

        # THE TWO LEGS THE ASSIGNMENT NAMES, first.
        long_charter = "You are the long one. " * 60  # 1,320 chars
        fires(
            "charter-over-cap-fires",
            _fix(charter=long_charter),
            f"over the {CHARTER_MAX} cap",
        )
        fires(
            "four-integrations-fires",
            _fix(integrations=["Gmail", "Slack", "Notion", "GitHub"]),
            f"over the {INTEGRATION_MAX} cap",
        )

        fires(
            "charter-chars-mismatch-fires", _fix(charter_chars=11), "but the charter is"
        )
        fires("bad-schema-fires", _fix(schema="gb-template/0"), "not 'gb-template/1'")
        fires(
            "non-kebab-id-fires",
            _fix(id="Fixture_Bot"),
            "not kebab-case",
            stem="Fixture_Bot",
        )
        fires(
            "id-filename-mismatch-fires",
            _fix(id="fixture-bot"),
            "filename stem",
            stem="other",
        )
        fires(
            "missing-key-fires",
            {k: v for k, v in GOOD.items() if k != "memory"},
            "key(s) missing",
        )
        fires("unknown-key-fires", _fix(surprise="hello"), "unknown key(s)")
        fires("bad-tier-fires", _fix(tier="D"), "tier 'D' is not one of")
        fires(
            "bad-category-fires",
            _fix(category="Growth"),
            "category 'Growth' is not one of",
        )
        fires(
            "two-sentence-job-fires",
            _fix(job="Do a thing. Then another thing."),
            "not one sentence",
        )

        # Routine: the deficit these templates exist to close.
        fires(
            "empty-routine-prompt-fires",
            _fix(routine={**GOOD["routine"], "prompt": ""}),
            "routine.prompt is empty",
        )
        fires(
            "routine-null-fires", _fix(routine=None), "use cadence 'none', never null"
        )
        fires(
            "bad-cadence-fires",
            _fix(routine={**GOOD["routine"], "cadence": "hourly"}),
            "is not one of",
        )
        fires(
            "bad-provenance-fires",
            _fix(routine={**GOOD["routine"], "prompt_provenance": "vibes"}),
            "not one of",
        )
        leg(
            "good-provenance-passes",
            not probe(_fix(routine={**GOOD["routine"], "prompt_provenance": "author"})),
            "a labeled prompt must be accepted, or the label is a ban",
        )
        fires(
            "unargued-cadence-none-fires",
            _fix(
                routine={"cadence": "none", "when": "", "prompt": "", "writes": ""},
                why_this_shape="Because I said so.",
            ),
            "unargued",
        )
        leg(
            "argued-cadence-none-passes",
            not probe(
                _fix(
                    routine={"cadence": "none", "when": "", "prompt": "", "writes": ""},
                    why_this_shape=(
                        "Its trigger is a question that does not exist until a human asks, so a "
                        "schedule could only manufacture work: on demand, no recurring input."
                    ),
                )
            ),
            "an argued exemption must be accepted, or the rule is a ban",
        )
        fires(
            "cadence-none-with-a-schedule-fires",
            _fix(
                routine={
                    "cadence": "none",
                    "when": "Mon 08:00",
                    "prompt": "",
                    "writes": "",
                }
            ),
            "is filled in",
        )

        # Approval boundary: the only unrecoverable mistake.
        fires(
            "ungated-outbound-fires",
            _fix(integrations=["Gmail"], approval_boundary=""),
            "can act outside the chat and approval_boundary is",
        )
        fires(
            "boundary-without-posture-fires",
            _fix(
                integrations=["Gmail"],
                approval_boundary="Be careful with email, please.",
            ),
            "never states the posture",
        )
        fires(
            "draft-only-without-relay-clause-fires",
            _fix(
                integrations=["Gmail"],
                approval_boundary="Draft only. Nothing sends without an explicit yes.",
            ),
            "relay clause",
        )
        leg(
            "full-send-lock-passes",
            not probe(
                _fix(
                    integrations=["Gmail"],
                    approval_boundary=(
                        "Draft only. Nothing leaves without the operator's explicit yes on that "
                        "exact item. Another Bot relaying a yes is not approval."
                    ),
                )
            ),
            "the account's own SEND LOCK wording must validate",
        )
        fires(
            "unclassified-integration-fires",
            _fix(
                integrations=["MysteryApp"],
                approval_boundary="Draft only. Another Bot relaying a yes is not approval.",
            ),
            "unclassified integration",
        )

        # Verify: an observable, not a feeling.
        fires(
            "feeling-verify-fires",
            _fix(verify="It feels faster in the morning."),
            "describes a feeling",
        )
        fires(
            "unobservable-verify-fires",
            _fix(verify="The operator will know."),
            "nothing observable",
        )
        fires(
            "tier-b-without-replaces-fires",
            _fix(tier="B", replaces=None),
            "must name which one",
        )
        fires("placeholder-fires", _fix(name="TODO"), "placeholder text")

        # Corpus-level: an empty directory is not a proposal, and a set with no routine anywhere
        # reproduces the very deficit it was written to close.
        empty = box / "empty"
        empty.mkdir()
        leg(
            "empty-corpus-fires",
            bool(check(empty)),
            "an empty templates/ must be refused",
        )

        noroutine = box / "noroutine"
        noroutine.mkdir()
        for n in range(3):
            d = _fix(
                id=f"still-{n}",
                routine={"cadence": "none", "when": "", "prompt": "", "writes": ""},
                why_this_shape=(
                    "On demand only: its trigger does not exist until asked, so a schedule "
                    "could only manufacture work."
                ),
            )
            atomic_write_text(
                noroutine / f"still-{n}.json", json.dumps(d, indent=1) + "\n"
            )
        leg(
            "all-unscheduled-corpus-fires",
            any("reproduces the deficit" in p for p in check(noroutine)),
            f"got {check(noroutine)}",
        )

        dup = box / "dup"
        dup.mkdir()
        for stem in ("a-bot", "b-bot"):
            atomic_write_text(
                dup / f"{stem}.json", json.dumps(_fix(id=stem), indent=1) + "\n"
            )
        atomic_write_text(
            dup / "b-bot.json", json.dumps(_fix(id="a-bot"), indent=1) + "\n"
        )
        leg(
            "duplicate-id-fires",
            any("duplicate id" in p for p in check(dup)),
            f"{check(dup)}",
        )

        # STATS ARITHMETIC, on an inline corpus whose answer is known by hand: charters
        # 100/200/900/0/0 -> captured median 200, all-rows median 100, and the zeros must not be
        # allowed to pull the captured median down. This is the leg that would have caught
        # reporting 557 as "the corpus median charter length".
        fake = box / "fakeroot"
        (fake / "usecases").mkdir(parents=True)
        atomic_write_text(
            fake / "usecases" / "2026-01-01T0000.json",
            json.dumps(
                {
                    "bots": 5,
                    "captured_at": "2026-01-01T00:00:00+00:00",
                    "approval_share": 0.5,
                    "rows": [
                        {"prompt_chars": 100, "integrations": ["Gmail"]},
                        {"prompt_chars": 200, "integrations": ["Gmail", "Slack"]},
                        {"prompt_chars": 900, "integrations": []},
                        {"prompt_chars": 0, "integrations": ["Grok"]},
                        {"prompt_chars": 0, "integrations": ["Grok"]},
                    ],
                }
            ),
        )
        base = corpus_baseline(fake)
        leg(
            "stats-excludes-uncaptured-charters",
            base["median_chars_captured"] == 200.0
            and base["median_chars_all_rows"] == 100.0
            and base["charters_uncaptured"] == 2,
            f"got {base.get('median_chars_captured')}/{base.get('median_chars_all_rows')}/"
            f"{base.get('charters_uncaptured')}",
        )
        leg(
            "stats-separates-real-integrations",
            base["integrations_mean_all_bots"] == 1.0
            and base["integrations_mean_with_a_real_one"] == 1.5,
            f"got {base.get('integrations_mean_all_bots')} / "
            f"{base.get('integrations_mean_with_a_real_one')}",
        )
        leg(
            "stats-reports-absence-not-zero",
            corpus_baseline(box)["available"] is False
            and account_baseline(box)["available"] is False,
            "a missing snapshot must answer unavailable, never 0",
        )

        # POSITIVE CONTROL. Without this, a validator that rejected everything would pass every
        # leg above. This one has to be the repo's real templates.
        real = check(TEMPLATES)
        leg(
            "repo-templates-validate-clean",
            not real,
            "; ".join(real[:3]) if real else "",
        )
        got = stats(TEMPLATES, ROOT)
        leg(
            "repo-stats-computes",
            isinstance(got["ours"]["median_chars"], (int, float))
            and got["ours"]["all_within_cap"] is True,
            f"got {got['ours']}",
        )

        # -----------------------------------------------------------------------------------
        # THE ROUTINE HALF OF A DEPLOY. Every leg below is offline: the transport is a fake
        # with a recorded script, and the clock and the sleep are injected, so the bound is
        # proven without spending the wait and no leg can touch a live account.
        # -----------------------------------------------------------------------------------
        tpl = _fix()
        want: Dict[str, Any] = tpl["routine"]
        spec = deploy_spec(tpl, box)  # `box` has no fleet-spec.json: seed is {} here
        carried = spec["bots"][0].get("routine") or {}
        leg(
            "deploy-spec-carries-the-routine",
            carried.get("prompt") == want["prompt"]
            and carried.get("cadence") == "weekly"
            and carried.get("when") == want["when"],
            f"the spec dropped the routine again: {carried}",
        )
        msg = routine_request(tpl)
        leg(
            "request-carries-the-prompt-verbatim",
            want["prompt"] in msg and "weekly" in msg and want["when"] in msg,
            f"the message lost part of the routine: {msg!r}",
        )
        leg(
            "request-asks-for-exactly-one-routine",
            "ONE routine" in msg and "Do not run it now" in msg,
            "the message did not bound the Bot to one routine, unrun",
        )

        # THE LIVE DEFECT, in a leg. A scheduling-shaped prompt must be passed THROUGH, never
        # fenced as "copy verbatim": fenced, the Bot stored the create instruction as the
        # routine's own prompt and would have re-created a routine every Monday (measured on
        # cap-watch, 2026-09-12, by reading the record back).
        sched_prompt = (
            'Create a weekly routine named "Cap Check", Mondays 08:30 America/Denver, '
            "enabled, whose message is: Report the caps."
        )
        sched_msg = routine_request(_fix(routine={**want, "prompt": sched_prompt}))
        leg(
            "scheduling-shaped-prompt-is-passed-through",
            sched_msg.startswith(sched_prompt)
            and "<<<" not in sched_msg
            and "cadence:" not in sched_msg,
            f"a prompt that already schedules was wrapped again: {sched_msg!r}",
        )
        leg(
            "message-shaped-prompt-is-still-fenced",
            "<<<" in msg and "cadence:" in msg,
            f"a message-shaped prompt lost its fence: {msg!r}",
        )
        # POSITIVE CONTROL on the classifier: the repo's own templates must contain BOTH shapes,
        # or one of the two branches above is never exercised by real data and the split is a
        # story rather than a measurement.
        shapes = {
            bool(
                SCHEDULING_PROMPT.match(
                    str((d["routine"] or {}).get("prompt") or "").strip()
                )
            )
            for d in valid_only(TEMPLATES)
            if d["routine"]["cadence"] in RUNNABLE_CADENCES
        }
        leg(
            "repo-templates-carry-both-prompt-shapes",
            shapes == {True, False},
            f"the corpus exercises only shape(s) {shapes}",
        )

        def faker(
            script: List[Tuple[int, Any]],
        ) -> Tuple[Callable[..., Tuple[int, Any]], List[Dict[str, Any]]]:
            """A transport whose answers are a script, and a log of what it was asked."""
            asked: List[Dict[str, Any]] = []

            def fake(token: str, method: str, body: Any = None) -> Tuple[int, Any]:
                asked.append({"method": method, "body": body})
                return script[min(len(asked) - 1, len(script) - 1)]

            return fake, asked

        one = {
            "automations": [
                {
                    "automationId": "a1",
                    "recordJson": json.dumps(
                        {
                            "name": "Weekly receipt",
                            "triggerDescription": "Every Monday 7:45",
                            "isEnabled": True,
                            "provenance": "user",
                        }
                    ),
                }
            ]
        }
        slept: List[float] = []
        ticks = iter([0.0, 0.0, 6.0, 6.0, 12.0, 12.0, 12.0, 12.0])
        tape, asked = faker([(200, {"automations": []}), (200, {}), (200, one)])
        got = confirm_routine(
            tape,
            "tok",
            "uuid-9",
            wait_s=60.0,
            poll_s=6.0,
            sleep=slept.append,
            clock=lambda: next(ticks),
        )
        leg(
            "confirm-polls-until-the-routine-appears",
            got["verdict"] == "confirmed"
            and got["attempts"] == 3
            and slept == [6.0, 6.0]
            and got["routines"][0]["name"] == "Weekly receipt",
            f"got {got} after sleeps {slept}",
        )
        leg(
            "confirm-addresses-the-bot-by-uuid",
            all(a["method"] == "ListGrokBotAgentAutomations" for a in asked)
            and all(a["body"] == {"agent_id": "uuid-9"} for a in asked),
            f"the poll addressed something else: {asked}",
        )
        leg(
            "confirm-quotes-provenance-without-attributing-it",
            got["routines"][0]["provenance_recorded"] == "user"
            and not any("author" in k for k in got["routines"][0]),
            f"provenance was reshaped into a claim: {got['routines'][0]}",
        )

        # FIRES-ON-KNOWN-BAD 1: a server that always answers 200-and-empty must end ABSENT,
        # bounded. Without this leg the one above would also pass a confirm that never gives up.
        empty_ticks = iter([0.0] + [float(i) for i in range(0, 400, 6)])
        tape2, _ = faker([(200, {"automations": []})])
        never = confirm_routine(
            tape2,
            "tok",
            "u",
            wait_s=30.0,
            poll_s=6.0,
            sleep=lambda _s: None,
            clock=lambda: next(empty_ticks),
        )
        leg(
            "confirm-says-absent-and-stops",
            never["verdict"] == "absent"
            and never["confirmed"] is False
            and never["attempts"] <= 6,
            f"an always-empty server gave {never}",
        )
        # FIRES-ON-KNOWN-BAD 2: the house rule, in code. A read that FAILED saw nothing, and
        # that is not the same fact as a read that succeeded and found nothing.
        bad_ticks = iter([0.0] + [float(i) for i in range(0, 400, 6)])
        tape3, _ = faker([(401, "unauthorized")])
        blind = confirm_routine(
            tape3,
            "tok",
            "u",
            wait_s=18.0,
            poll_s=6.0,
            sleep=lambda _s: None,
            clock=lambda: next(bad_ticks),
        )
        leg(
            "confirm-never-calls-an-unreadable-server-empty",
            blind["verdict"] == "unreadable"
            and blind["successful_reads"] == 0
            and blind["last_http"] == 401,
            f"a failed read was reported as absence: {blind}",
        )
        leg(
            "routine-rows-keeps-an-undescribable-routine",
            len(
                routine_rows(
                    {"automations": [{"automationId": "a2", "recordJson": "{"}]}
                )
            )
            == 1
            and routine_rows({}) == []
            and routine_rows("nonsense") == [],
            "a present-but-unparseable routine was dropped, or a junk answer invented one",
        )

        # THE DRY-RUN GATE. `--apply` is the only thing that may send, and a failed create may
        # never be followed by a message to a Bot that might not exist.
        leg(
            "dry-run-never-sends",
            should_deploy_routine(tpl, False, 0) is False,
            "a dry run would have messaged a live account",
        )
        leg(
            "apply-sends-only-after-a-clean-create",
            should_deploy_routine(tpl, True, 0) is True
            and should_deploy_routine(tpl, True, 1) is False,
            "the send gate ignored the create's exit code",
        )
        leg(
            "unschedulable-template-sends-nothing",
            should_deploy_routine(_fix(routine={**want, "cadence": "none"}), True, 0)
            is False
            and should_deploy_routine(_fix(routine={**want, "prompt": "  "}), True, 0)
            is False,
            "a template with no runnable routine still triggered a message",
        )

        # THE MANIFEST IS THE RECORD. A message may only go to a uuid THIS run created.
        man = {
            "created": [
                {"name": "Fixture Bot", "new_numeric_id": 7, "new_uuid": "uuid-new"}
            ]
        }
        leg(
            "manifest-row-matches-by-name",
            (created_row(man, "Fixture Bot") or {}).get("new_uuid") == "uuid-new"
            and created_row(man, "Someone Else") is None
            and created_row({"created": []}, "Fixture Bot") is None,
            "the manifest lookup matched a Bot this run did not create",
        )
        out_lines = "created Fixture Bot   id=7\n\nmanifest /tmp/rebuild/x.json\nrollback with: …"
        leg(
            "manifest-path-read-off-the-deployers-own-stdout",
            manifest_from_output(out_lines) == pathlib.Path("/tmp/rebuild/x.json")
            and manifest_from_output("created Fixture Bot id=7") is None,
            f"got {manifest_from_output(out_lines)}",
        )

        # PARTIAL IS NOT SUCCESS, and is not a create failure either.
        leg(
            "partial-gets-its-own-exit-code",
            deploy_rc(0, {"verdict": "confirmed"}) == 0
            and deploy_rc(0, {"verdict": "not-attempted"}) == 0
            and deploy_rc(0, {"verdict": "absent"}) == PARTIAL_RC
            and deploy_rc(0, {"verdict": "unreadable"}) == PARTIAL_RC
            and deploy_rc(0, {"verdict": "nothing-created"}) == PARTIAL_RC
            and deploy_rc(3, {"verdict": "confirmed"}) == 3,
            "an unconfirmed routine or a failed create was spelled as success",
        )
        partial = render_routine(
            {
                "verdict": "absent",
                "send_http": 200,
                "successful_reads": 5,
                "waited_s": 30.0,
                "bot_uuid": "uuid-new",
                "manifest": "/tmp/rebuild/x.json",
            },
            tpl,
        )
        body = "\n".join(partial)
        leg(
            "partial-report-keeps-the-bot-and-names-the-step",
            "PARTIAL" in body
            and "the Bot EXISTS" in body
            and "NOT rolled back" in body
            and "gb fleet send" in body
            and "--rollback /tmp/rebuild/x.json" in body,
            f"the partial report did not hand over both options: {body!r}",
        )
        # A run that created NOTHING must not offer a rollback: the manifest it would name
        # deletes nothing, and "NOT rolled back" would imply this run had made a Bot.
        skipped = "\n".join(
            render_routine(
                {
                    "verdict": "nothing-created",
                    "detail": "a name already present is SKIPPED",
                    "manifest": "/tmp/rebuild/x.json",
                },
                tpl,
            )
        )
        leg(
            "a-run-that-created-nothing-offers-no-rollback",
            "the account is unchanged" in skipped
            and "--rollback" not in skipped
            and "NOT rolled back" not in skipped
            and "gb fleet send" in skipped,
            f"a no-op run was reported as if it had created a Bot: {skipped!r}",
        )
        confirmed = "\n".join(
            render_routine(
                {
                    "verdict": "confirmed",
                    "attempts": 2,
                    "waited_s": 6.0,
                    "bot_uuid": "uuid-new",
                    "routines": got["routines"],
                },
                tpl,
            )
        )
        leg(
            "confirmed-report-cites-the-server-read",
            "ROUTINE CONFIRMED" in confirmed
            and "ListGrokBotAgentAutomations" in confirmed
            and "uuid-new" in confirmed
            and "PARTIAL" not in confirmed,
            f"the confirmation did not cite its evidence: {confirmed!r}",
        )

    passed = sum(1 for _, ok, _ in legs if ok)
    for name, ok, detail in legs:
        emit(
            f"  {'ok  ' if ok else 'FAIL'} {name}{('  — ' + detail) if detail and not ok else ''}"
        )
    if passed != len(legs):
        warn(f"SELFTEST FAIL - {passed}/{len(legs)}")
        return 1
    emit(f"SELFTEST PASS - {passed}/{len(legs)}")
    return 0


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------
def deploy_spec(tpl: Dict[str, Any], root: pathlib.Path) -> Dict[str, Any]:
    """Turn ONE proposed template into a one-Bot spec `gb-rebuild-fleet.py` can instantiate.

    WHY AN ADAPTER AND NOT A SECOND DEPLOYER. The Bot create path is already proven live —
    `CreateGrokBotAgentFromTemplate` then immediately `UpdateGrokBotAgent`, with a rollback
    manifest that deletes the Bot a run created (only the Bot: the routine record outlives the
    delete, measured — see the note above `ROUTINE_WAIT_S`). Reimplementing that against the
    RPCs here would be a second write path into a paying customer's account, with its own bugs
    and its own rollback. So this maps the shape, and `deploy_routine` below does the second.

        template.name    -> name          template.charter -> description
        template.job     -> title         avatar           -> inherited from the reviewed roster
        template.routine -> routine       source_uuid      -> None (new Bot, never a rebuild)

    THE ROUTINE TRAVELS WITH THE SPEC, and that is the fix for the defect this function WAS.
    It used to map five fields and drop `routine` on the floor, so the most valuable thing a
    template encodes — the cadence and the prompt that make the Bot run unattended — was the one
    thing a deploy did not carry. 42 of 43 templates here declare a runnable routine; 11 of the
    13 Bots on this account carry none. `gb-rebuild-fleet.py` ignores the extra key (it reads
    name/title/avatar/description), so it is here for the RECORD: the spec file written next to
    the manifest now states the whole intent of the run, including the part delivered by chat.

    The seed is read from the repo's own `fleet-spec.json` rather than hardcoded, so a seed swap
    is made in exactly one place and cannot drift between the roster and a template deploy.
    """
    spec_path = root / "fleet-spec.json"
    seed: Dict[str, Any] = {}
    avatar_shape: Any = None
    avatar_color: Any = None
    if spec_path.is_file():
        reviewed = json.loads(spec_path.read_text())
        seed = reviewed.get("seed") or {}
        bots = reviewed.get("bots") or []
        if bots:
            avatar_shape = bots[0].get("avatar_shape")
            avatar_color = bots[0].get("avatar_color")
    title = str(tpl.get("job") or tpl.get("name") or "")[:60]
    routine = tpl.get("routine") or {}
    return {
        "version": 1,
        "_why": (
            f"one-Bot spec generated by `gb-templates deploy {tpl.get('id')}`; "
            "the deploy mechanism is bin/gb-rebuild-fleet.py, single-sourced"
        ),
        "seed": seed,
        "bots": [
            {
                "name": str(tpl.get("name") or tpl.get("id")),
                "title": title,
                "avatar_shape": avatar_shape,
                "avatar_color": avatar_color,
                "source_uuid": None,
                "description": str(tpl.get("charter") or ""),
                # Carried, not consumed by the rebuilder: the routine is created by chat after
                # the Bot exists. See `routine_request` for the message and why it is a message.
                "routine": {k: routine.get(k) for k in ROUTINE_KEYS},
            }
        ],
    }


# ---------------------------------------------------------------------------------------------
# The routine — the half of a template that used not to deploy
# ---------------------------------------------------------------------------------------------
# MEASURED, not inferred (commit 0d60a49, 2026-09-11). Routines have no create RPC: the shared
# transport refuses everything outside List/Get by contract, and the vendor's own UI says "This
# Bot keeps its routines on the server. Ask it in chat to change, pause, or delete one." So the
# create path is charter + ONE chat message. `Routine Proof` was deployed, asked in chat to
# schedule itself, and `ListGrokBotAgentAutomations` then listed `Weekly receipt`, cron
# `CRON_TZ=America/Denver 45 7 * * 1`, isEnabled true. The Bot's REPLY was never the evidence;
# the API read was, and that ordering is why `confirm_routine` exists below.
#
# The record carries `provenance: "user"`. That string is reported verbatim and NOTHING is
# concluded from it: nothing in this product records who authored a routine, and two claims in
# this repo that provenance revealed authorship — one citing the other — were both falsified.
#
# WHAT A ROLLBACK DOES AND DOES NOT UNDO, measured 2026-09-12 on two deploys of `cap-watch`.
# `DeleteGrokBotAgent` removes the Bot — `ListGrokBotAgents` went 14 -> 13 and the name was
# gone — but `ListGrokBotAgentAutomations` still returned the routine for BOTH deleted uuids
# afterwards, HTTP 200, one automation each. So the roster is restored and the automation
# record is ORPHANED, not deleted. Two consequences, and only the first is measured: a routine
# read is never proof that its Bot still exists, and whether an orphan can still fire is
# UNKNOWN here — nothing was observed either way, so nothing is claimed either way.
ROUTINE_WAIT_S = (
    150.0  # bounded on purpose: an unconfirmed routine must end in a verdict
)
ROUTINE_POLL_S = 6.0
RUNNABLE_CADENCES = ("daily", "weekly")
PARTIAL_RC = 4


# THE PROMPT FIELD HAS TWO SHAPES, measured across the 42 runnable templates here: 21 are the
# MESSAGE the routine should run each time ("Post today's digest: ..."), and 21 are already a
# SCHEDULING INSTRUCTION ("Create a routine named X with cron ... whose message is: ..."). The
# distinction is not cosmetic. Measured live on `cap-watch` 2026-09-12: this function wrapped a
# scheduling-shaped prompt in "copy this VERBATIM" and the Bot obeyed — it stored the CREATE
# INSTRUCTION as the routine's own prompt, so every Monday 08:30 that routine would have woken
# the Bot and told it to create another routine. Confirmed by reading the record back:
# `"prompt":"Create a weekly routine named \"Cap Check\" ..."`. A routine that schedules
# routines is not the job the template was validated for, and no count of routines would have
# caught it — only reading the prompt back did.
SCHEDULING_PROMPT = re.compile(
    r"^\s*(create|make this your)\b[^.]{0,80}\broutine\b", re.I
)


def routine_request(tpl: Dict[str, Any]) -> str:
    """The ONE message that makes a Bot schedule itself, in the shape the prompt calls for.

    A scheduling-shaped prompt is passed THROUGH: it already names the routine, the cron and
    the message, so adding a competing header would hand the Bot two names and two hours to
    reconcile, and fencing it as "copy verbatim" makes the create instruction become the thing
    that runs. A message-shaped prompt is WRAPPED: the cadence and hour come from the template,
    and the prompt is fenced because it has to be copied verbatim — an unfenced multi-sentence
    prompt comes back paraphrased, and a paraphrased prompt is a different job.

    Both shapes carry the same bounds: exactly one routine, do not run it now, change nothing
    else. Those bounds are the only thing this function adds to a template's own words.
    """
    r = tpl.get("routine") or {}
    prompt = str(r.get("prompt") or "").strip()
    bounds = (
        "Create exactly ONE routine. Do not run it now. Change nothing else about yourself, "
        "then reply with a single line saying it is saved."
    )
    if SCHEDULING_PROMPT.match(prompt):
        return "\n".join([prompt, "", bounds])
    name = str(tpl.get("name") or tpl.get("id"))
    return "\n".join(
        [
            f"Create ONE routine for yourself on the server, named {name!r}.",
            "",
            f"cadence:  {r.get('cadence')}",
            f"when:     {r.get('when') or 'the hour your charter implies'}",
            "prompt to run each time, copied VERBATIM from between the markers:",
            f"  <<<{prompt}>>>",
            "",
            bounds,
        ]
    )


def routine_rows(resp: Any) -> List[Dict[str, Any]]:
    """Flatten ONE `ListGrokBotAgentAutomations` answer.

    The interesting fields live in `recordJson`, a JSON STRING inside the row. Unparseable means
    "a routine exists and we could not describe it", so the row is still returned — dropping it
    would turn a present routine into an absent one, which is the one error this must not make.
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(resp, dict):
        return out
    for a in resp.get("automations") or []:
        if not isinstance(a, dict):
            continue
        rec: Dict[str, Any] = {}
        raw = a.get("recordJson")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                rec = parsed
        out.append(
            {
                "name": str(rec.get("name") or a.get("automationId") or "?"),
                "schedule": str(
                    rec.get("triggerDescription") or rec.get("schedule") or "?"
                ),
                "enabled": bool(rec.get("isEnabled")),
                # Verbatim, attributed to nobody — see the note above this section.
                "provenance_recorded": str(rec.get("provenance") or "?"),
            }
        )
    return out


def confirm_routine(
    rpc: Callable[..., Tuple[int, Any]],
    token: str,
    bot_uuid: str,
    *,
    wait_s: float = ROUTINE_WAIT_S,
    poll_s: float = ROUTINE_POLL_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Dict[str, Any]:
    """Poll the SERVER for the routine, addressed by the Bot's UUID, until it appears or the
    bounded wait expires. Three verdicts, and the difference between two of them is the point:

      confirmed   the API listed at least one automation for this UUID
      absent      every read SUCCEEDED and listed nothing — this is evidence of absence, and
                  only because the same call is known to return rows for a Bot that has one
      unreadable  no read succeeded, so absence was never OBSERVED. Never reported as absent:
                  an empty answer from a call that failed is not a measurement of anything.

    Addressed by UUID because the numeric id is REJECTED by this RPC with `agent id must be the
    agent's UUID`, and by the uuid the CREATE returned rather than one a roster cache carries —
    that cache is per-machine and has already been read as "the Bot was deleted" once.
    `sleep`/`clock` are injected so the selftest proves the bound without spending the wait.
    """
    started = clock()
    attempts = 0
    ok_reads = 0
    last: Any = None
    rows: List[Dict[str, Any]] = []
    while True:
        attempts += 1
        status, resp = rpc(token, "ListGrokBotAgentAutomations", {"agent_id": bot_uuid})
        last = status
        if status == 200:
            ok_reads += 1
            rows = routine_rows(resp)
            if rows:
                break
        if clock() - started + poll_s > wait_s:
            break
        sleep(poll_s)
    return {
        "verdict": "confirmed" if rows else ("absent" if ok_reads else "unreadable"),
        "confirmed": bool(rows),
        "attempts": attempts,
        "successful_reads": ok_reads,
        "waited_s": round(clock() - started, 1),
        "last_http": last,
        "routines": rows,
    }


def should_deploy_routine(tpl: Dict[str, Any], apply: bool, child_rc: int) -> bool:
    """The gate on the second write, and the reason a dry run can never send anything.

    `apply` is the only thing that can make this true — the flag is not a formatting choice, it
    is the whole difference between a plan and a message delivered into a paying account. A
    failed create can never be followed by a message, because there may be no Bot to message.
    """
    r = tpl.get("routine") or {}
    return (
        bool(apply)
        and child_rc == 0
        and str(r.get("cadence") or "none") in RUNNABLE_CADENCES
        and bool(str(r.get("prompt") or "").strip())
    )


def manifest_from_output(out: str) -> Optional[pathlib.Path]:
    """The rollback manifest `gb-rebuild-fleet.py` just wrote, read off its own stdout.

    It prints `manifest <path>`; reading that keeps ONE writer of the manifest instead of a
    second guess here at where manifests go and what this run's is called.
    """
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("manifest "):
            p = s[len("manifest ") :].strip()
            if p:
                return pathlib.Path(p)
    return None


def created_row(man: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    """The manifest row for the Bot THIS run created, or None.

    None is a real answer, not a failure: `--apply` SKIPS a name already present, and a Bot this
    run did not create must not be messaged by it — the manifest would then describe less than
    the run did, and a rollback built from it would be a lie in the dangerous direction.
    """
    for row in man.get("created") or []:
        if isinstance(row, dict) and row.get("name") == name and row.get("new_uuid"):
            return row
    return None


def deploy_rc(child_rc: int, step: Dict[str, Any]) -> int:
    """0 only when there is nothing unfinished. A created Bot with an unconfirmed routine is
    PARTIAL, which is neither success nor a create failure and must not be spelled as either."""
    if child_rc != 0:
        return child_rc
    return (
        0 if str(step.get("verdict")) in ("not-attempted", "confirmed") else PARTIAL_RC
    )


def _handover() -> Any:
    """`gb-handover.py`, imported by path because its filename carries a hyphen.

    THE WRITE PATH IS NOT DUPLICATED HERE. `gbrpc.rpc` refuses anything outside List/Get by
    contract — it refused `SendGrokBotUserMessage` once and that refusal is correct and stays —
    and `gb-handover.py` already owns the POST that delivers one message to one Bot, which is
    also how `gb fleet send` reaches it. A third POST in this file would be a third place for a
    write bug to live. The `sys.modules[...] = mod` line before `exec_module` is load-bearing on
    python 3.9.6: without it any module defining a dataclass dies inside `dataclasses._is_type`.
    """
    import importlib.util

    path = pathlib.Path(__file__).resolve().parent / "gb-handover.py"
    spec = importlib.util.spec_from_file_location("gb_handover", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gb_handover"] = mod
    spec.loader.exec_module(mod)
    return mod


def deploy_routine(
    tpl: Dict[str, Any], man_path: Optional[pathlib.Path]
) -> Dict[str, Any]:
    """Second half of a deploy: ask the NEW Bot to schedule itself, then read the server back.

    Order is deliberate at every step. The manifest is consulted FIRST, so the message only ever
    goes to a uuid this run created and the rollback file keeps describing exactly what happened.
    The verdict comes from `ListGrokBotAgentAutomations`, never from the Bot's reply — a Bot that
    says "saved" and scheduled nothing is the exact failure this function exists to catch. What
    it learned is written back into the manifest, so the record of the run includes the half that
    was delivered by chat.
    """
    name = str(tpl.get("name") or tpl.get("id"))
    text = routine_request(tpl)
    step: Dict[str, Any] = {
        "bot": name,
        "message_chars": len(text),
        "requested": False,
        "verdict": "not-attempted",
        "confirmed": False,
        "manifest": str(man_path) if man_path else None,
    }
    if man_path is None or not man_path.is_file():
        step["verdict"] = "no-manifest"
        step["detail"] = (
            "the create step printed no manifest path, so nothing here could name what the run "
            "created — refusing to send a message a rollback could not describe"
        )
        return step
    man = json.loads(man_path.read_text())
    row = created_row(man, name)
    if row is None:
        step["verdict"] = "nothing-created"
        step["detail"] = (
            f"this run created no Bot named {name!r} — a name already present is SKIPPED by the "
            "rebuilder, so there is nothing this run may schedule and nothing it may roll back"
        )
        return step
    uuid = str(row["new_uuid"])
    step["bot_uuid"] = uuid
    step["bot_numeric_id"] = row.get("new_numeric_id")
    try:
        import gbrpc

        support = gbrpc.SUPPORT
        if support is None:
            step["verdict"] = "no-client"
            step["detail"] = (
                "no desktop client state on this machine, so no credential to send with"
            )
            return step
        token = gbrpc.access_token(support)
    except Exception as e:  # noqa: BLE001 - a missing credential is a VERDICT, not a crash
        step["verdict"] = "no-client"
        step["detail"] = f"no client session ({type(e).__name__}: {str(e)[:120]})"
        return step

    status, resp = _handover().send(token, uuid, text)
    step["requested"] = True
    step["send_http"] = int(status)
    step["send_body"] = str(resp)[:200]
    if int(status) != 200:
        step["verdict"] = "send-failed"
        step["detail"] = f"SendGrokBotUserMessage -> {status}; no routine was requested"
    else:
        step.update(confirm_routine(gbrpc.rpc, token, uuid))

    # The manifest is the record of the run, so the half delivered by chat belongs in it. Extra
    # keys are inert for `--rollback`, which reads `created` and nothing else.
    man["routine_step"] = {k: v for k, v in step.items() if k != "manifest"}
    atomic_write_text(man_path, json.dumps(man, indent=1) + "\n")
    return step


def render_routine(step: Dict[str, Any], tpl: Dict[str, Any]) -> List[str]:
    """What the operator is told after the second half ran.

    A PARTIAL is spelled out as a STATE, and which state depends on whether THIS run created
    the Bot. If it did, the Bot exists because of this command, so the report says so, names
    the one manual step and hands over the rollback — it does not quietly undo a create the
    operator asked for, and it does not call an unconfirmed routine a success. If it did not
    (a name already present is skipped), there is nothing to roll back and offering a rollback
    would point the operator at a manifest that deletes nothing.
    """
    r = tpl.get("routine") or {}
    name = str(tpl.get("name") or tpl.get("id"))
    out: List[str] = [""]
    if step.get("verdict") == "confirmed":
        out.append(
            f"ROUTINE CONFIRMED by reading the server — {step.get('attempts')} read(s) of "
            f"ListGrokBotAgentAutomations for {step.get('bot_uuid')} over "
            f"{step.get('waited_s')}s:"
        )
        for got in step.get("routines") or []:
            out.append(
                f"  {got.get('name')}  |  {got.get('schedule')}  |  "
                f"enabled={got.get('enabled')}  |  provenance recorded as "
                f"{got.get('provenance_recorded')!r}"
            )
        out.append(
            "  provenance is quoted verbatim and attributes authorship to nobody: nothing in "
            "this product records who wrote a routine."
        )
        return out

    created_here = bool(step.get("bot_uuid"))
    if created_here:
        out.append(
            f"PARTIAL — the Bot EXISTS and its routine is NOT confirmed ({step.get('verdict')})."
        )
    else:
        out.append(
            f"PARTIAL — this run created no Bot and requested no routine "
            f"({step.get('verdict')}); the account is unchanged."
        )
    if step.get("verdict") == "absent":
        out.append(
            f"  the message was accepted (HTTP {step.get('send_http')}) and "
            f"{step.get('successful_reads')} successful read(s) over {step.get('waited_s')}s "
            "listed no automation for this Bot"
        )
    elif step.get("verdict") == "unreadable":
        out.append(
            "  no read of ListGrokBotAgentAutomations succeeded "
            f"(last HTTP {step.get('last_http')}), so absence was never OBSERVED — this is "
            "UNKNOWN, not empty"
        )
    if step.get("detail"):
        out.append(f"  {step['detail']}")
    if created_here:
        out.append(
            "  the Bot was NOT rolled back: destroying a Bot the operator asked for because a "
            "schedule was slow is the worse error."
        )
    out.append("  THE ONE STEP, in the UI or from here:")
    out.append(
        f"    gb fleet send --bot {name!r} --yes --text "
        f"{str(r.get('prompt') or '')[:120]!r}"
    )
    out.append(f"  then confirm it took: gb fleet routines --bot {name!r}")
    if created_here and step.get("manifest"):
        out.append(
            f"  or undo the Bot entirely: bin/gb-rebuild-fleet.py --rollback {step['manifest']}"
        )
    return out


def cmd_deploy(
    tid: str, path: pathlib.Path, root: pathlib.Path, apply: bool, as_json: bool
) -> int:
    """Deploy one template: the Bot AND its routine. DRY RUN unless `--apply` — an account
    mutation is never a default, and that now covers two writes rather than one."""
    rows = valid_only(path)
    match = next((r for r in rows if r.get("id") == tid), None)
    if match is None:
        hint, listing, preview = _teach_unknown_template(tid, rows, "deploy")
        warn(f"gb-templates: no template {tid!r}. Known ids: {preview}")
        warn(f"    try:  {hint}")
        if apply:
            warn(f"    plan first:  {hint}  (drop --apply; nothing applied)")
        warn(f"    list: {listing}")
        if as_json:
            emit(
                json.dumps(
                    {
                        "schema": "gb-template-deploy/2",
                        "tool": "gb",
                        "command": "templates",
                        "status": "USAGE",
                        "error": f"no template {tid!r}",
                        "applied": False,
                        "hint": hint,
                        "plan": hint,
                        "did_you_mean": listing,
                    },
                    indent=1,
                )
            )
        return 2
    spec = deploy_spec(match, root)
    bot = spec["bots"][0]
    if not spec.get("seed", {}).get("share_id"):
        warn(
            "gb-templates: fleet-spec.json declares no seed share_id, and "
            "CreateGrokBotAgent WITHOUT a template makes a Bot the desktop never shows "
            "(10 were created that way and had to be rolled back). Refusing."
        )
        return 1

    out_dir = root / "state"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_file = out_dir / f"deploy-{tid}.json"
    atomic_write_text(spec_file, json.dumps(spec, indent=1) + "\n")

    argv = [
        sys.executable,
        str(pathlib.Path(__file__).resolve().parent / "gb-rebuild-fleet.py"),
        "--spec",
        str(spec_file),
        "--apply" if apply else "--plan",
    ]
    if apply:
        argv += ["--only", bot["name"]]
    proc = run(argv, timeout_s=600, cwd=root)

    routine = match.get("routine") or {}
    runnable = str(routine.get("cadence") or "none") in RUNNABLE_CADENCES
    man_path = manifest_from_output(proc.out) if apply else None
    step: Dict[str, Any] = {"verdict": "not-attempted", "confirmed": False}
    if should_deploy_routine(match, apply, proc.code):
        step = deploy_routine(match, man_path)
    rc = deploy_rc(proc.code, step)

    if as_json:
        emit(
            json.dumps(
                {
                    "schema": "gb-template-deploy/2",
                    "tool": "gb",
                    "command": "templates",
                    "template": tid,
                    "bot_name": bot["name"],
                    "charter_chars": len(bot["description"]),
                    "seed": spec["seed"].get("share_id"),
                    "applied": apply,
                    "plan": f"gb templates deploy {tid}",
                    "next_command": (
                        None if apply else f"gb templates deploy {tid} --apply"
                    ),
                    "spec": str(spec_file),
                    "manifest": str(man_path) if man_path else None,
                    "rc": proc.code,
                    "exit_code": rc,
                    "output": proc.out.strip().splitlines(),
                    # Superseded key `routine_is_manual`: the routine is no longer a manual
                    # step, so a field whose name asserts that it is would be a false claim in
                    # the machine-readable envelope, where nobody would re-read it.
                    "routine": {
                        "cadence": routine.get("cadence"),
                        "when": routine.get("when"),
                        "prompt_chars": len(str(routine.get("prompt") or "")),
                        "runnable": runnable,
                        "message": routine_request(match) if runnable else None,
                        "step": step,
                    },
                    "partial": rc == PARTIAL_RC,
                },
                indent=1,
            )
        )
        return rc

    emit(proc.out.rstrip())
    if not apply:
        emit("")
        emit(f"plan:  gb templates deploy {tid}")
        emit(f"apply: gb templates deploy {tid} --apply")
        if runnable:
            emit("")
            emit(
                "DRY RUN — nothing created, nothing sent. With --apply this run would:"
            )
            emit(
                f"  1. create {bot['name']!r} from seed {spec['seed'].get('share_id')} "
                f"({len(bot['description'])}-char charter)"
            )
            emit("  2. send that Bot ONE message, verbatim:")
            for line in routine_request(match).splitlines():
                emit(f"     | {line}")
            emit(
                f"  3. poll ListGrokBotAgentAutomations for the NEW uuid for up to "
                f"{int(ROUTINE_WAIT_S)}s and report CONFIRMED or PARTIAL"
            )
    elif apply and runnable:
        for line in render_routine(step, match):
            emit(line)
    elif apply and proc.code == 0:
        emit("")
        emit(
            f"no routine deployed: this template declares cadence "
            f"{str(routine.get('cadence') or 'none')!r}, so there is nothing to schedule."
        )
    return rc


DEID_PATTERNS = [
    ("phone-number shape", r"\b\d{3}[-. ]\d{3}[-. ]\d{4}\b"),
    ("local absolute path", r"/Users/[\w.-]+"),
    ("dollar amount", r"\$[\d,]+"),
    ("email address", r"[\w.+-]+@[\w-]+\.[\w.]+"),
]


def deid_screen(text: str) -> List[str]:
    """Pattern screen, not a verdict: hits name a human review, silence never proves clean."""
    hits = []
    for label, pat in DEID_PATTERNS:
        m = re.findall(pat, text)
        if m:
            hits.append(f"{label} x{len(m)} (e.g. {m[0][:40]!r})")
    return hits


def resolve_source(name_or_uuid: str, root: pathlib.Path) -> str:
    """Bot name -> uuid via the newest deployment roster; uuids pass through."""
    s = name_or_uuid.strip()
    if len(s) == 36 and s.count("-") == 4:
        return s
    files = sorted((root / "deployment").glob("*.json"))
    for f in reversed(files):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        for b in d.get("bots") or []:
            if str(b.get("name") or "").lower() == s.lower() and b.get("uuid"):
                return str(b["uuid"])
        # some audits key by id instead of uuid
        for b in d.get("bots") or []:
            if str(b.get("name") or "").lower() == s.lower() and b.get("id"):
                return str(b["id"])
    return s


def cmd_publish(
    tid: str,
    path: pathlib.Path,
    root: pathlib.Path,
    apply: bool,
    screened: bool,
    source: str,
    as_json: bool,
    force: bool = False,
) -> int:
    """Publish one template as a public share link. DRY RUN unless `--apply` — and
    `--apply` additionally requires `--screened`, the operator's attestation that a
    human reviewed the charter for client names, deals, phones, and inboxes. A pattern
    screen assists that review; it never replaces it."""
    import urllib.request

    rows = valid_only(path)
    match = next((r for r in rows if r.get("id") == tid), None)
    if match is None:
        hint, listing, preview = _teach_unknown_template(tid, rows, "publish")
        warn(f"gb-templates: no template {tid!r}. Known ids: {preview}")
        warn(f"    try:  {hint}")
        warn(f"    list: {listing}")
        if as_json:
            emit(
                json.dumps(
                    {
                        "schema": "gb-template-publish/1",
                        "tool": "gb",
                        "command": "templates",
                        "status": "USAGE",
                        "error": f"no template {tid!r}",
                        "hint": hint,
                        "did_you_mean": listing,
                    },
                    indent=1,
                )
            )
        return 2
    charter = str(match.get("charter") or "")
    hits = deid_screen(f"{match.get('name', '')}\n{match.get('title', '')}\n{charter}")
    for h in hits:
        warn(f"gb-templates publish de-id flag (review, not verdict): {h}")
    if apply and not screened:
        warn(
            "gb-templates: publishing is public. Re-run with --screened once a human "
            "has reviewed the charter. The pattern screen above assists, never attests."
        )
        return 2
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import gbrpc as _rpc
        from gblib import support_dir

        support = support_dir()
        if support is None:
            warn("gb-templates: no client state dir on this machine")
            return 3
        token = _rpc.access_token(support)
        host = _rpc.HOST
    except Exception as e:
        warn(f"gb-templates: no client session ({type(e).__name__})")
        return 3

    def call(method: str, body: Dict[str, Any]) -> Any:
        req = urllib.request.Request(
            f"https://{host}/aiserver.v1.GrokBotService/{method}",
            data=json.dumps(body).encode(),
            headers={
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
                "user-agent": "gb-templates-publish/1",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")

    if apply:
        # One template per source Bot (measured 2026-09-12: Creates from a Bot that
        # already owns a template mint invisible drafts under the LIVE shareId).
        # Refuse rather than pollute; --force is the explicit override.
        try:
            existing = call(
                "GetGrokBotTemplateForSourceAgent", {"source_agent_id": source}
            )
        except Exception:
            existing = {}
        owned = (existing.get("template") or {}) if isinstance(existing, dict) else {}
        owned_id = owned.get("shareId") or owned.get("share_id")
        if owned_id:
            if not force:
                warn(
                    f"gb-templates: source already owns template "
                    f"{owned_id} — publishing here would mint drafts under a "
                    f"live shareId. Use --source pointing at a Bot with no template, or --force."
                )
                return 2
            warn(
                "gb-templates: --force given; drafts will land under the existing shareId"
            )

    # Blob shape is byte-exact per vendor-watch v3 (measured 2026-09-12): top-level
    # profile/memory/skills/routines/plugins, camelCase profile keys, default dumps
    # separators. The empties are required keys, not omissions.
    profile = {
        "name": match.get("name"),
        "description": charter,
        "avatarColor": match.get("avatar_color", "cyan"),
        "avatarShape": match.get("avatar_shape", "hex"),
        "title": match.get("title") or match.get("name"),
    }
    blob = json.dumps(
        {
            "profile": profile,
            "memory": [],
            "skills": [],
            "routines": [],
            "plugins": [],
        }
    ).encode()
    plan = {
        "schema": "gb-template-publish/1",
        "template": tid,
        "profile_fields": sorted(profile.keys()),
        "blob_bytes": len(blob),
        "source": source,
        "applied": apply,
    }
    if not apply:
        if as_json:
            emit(json.dumps(plan, indent=1))
        else:
            emit(
                f"would publish {tid} ({len(blob)}-byte profile blob, source {source}); "
                "re-run with --apply --screened to do it"
            )
        return 0

    # ONE writer, not two. A `--force` guard added earlier the same day introduced a SECOND
    # nested `call()` here, byte-different only in header formatting, which mypy caught as a
    # redefinition. Python would have silently taken the later one, so the guard above and the
    # create below would have been issuing requests through two helpers that could drift.
    # The definition above (used by the pre-flight GetGrokBotTemplateForSourceAgent) is the one
    # that survives; this is deliberately a local WRITER because `gbrpc.rpc` refuses anything
    # outside List/Get by contract, and that refusal is a feature worth not routing around.

    try:
        created = call(
            "CreateGrokBotTemplate",
            {
                "name": profile["name"],
                "source_agent_id": source,
                "title": profile["title"],
                "description": charter,
                "avatar_shape": profile["avatarShape"],
                "avatar_color": profile["avatarColor"],
                "blob_content_type": "application/json",
                "blob_byte_size": len(blob),
            },
        )
        put_url = created.get("blobPutUrl") or created.get("blob_put_url") or ""
        share_id = created.get("shareId") or created.get("share_id") or ""
        if not put_url or not share_id:
            warn(
                f"gb-templates: create answered without blob URL/share id: {json.dumps(created)[:200]}"
            )
            return 1
        put = urllib.request.Request(
            put_url,
            data=blob,
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(put, timeout=60) as r:
            etag = r.headers.get("ETag", "")
            if r.status not in (200, 201):
                warn(f"gb-templates: blob PUT HTTP {r.status}")
                return 1
        # Versions mint per Create: activate ONLY the version just PUT, never an older one.
        version = created.get("version") or created.get("activeVersion") or 1
        activated = call(
            "ActivateGrokBotTemplateVersion", {"share_id": share_id, "version": version}
        )
        public = call("GetPublicGrokBotTemplate", {"share_id": share_id})
        out = {
            **plan,
            "share_id": share_id,
            "version": version,
            "etag": etag,
            "published": public.get("published"),
            "share_url": f"x.ai/bot/{share_id}",
        }
        if as_json:
            emit(json.dumps(out, indent=1))
        else:
            emit(
                f"published {tid} -> x.ai/bot/{share_id} (v{version}, {len(blob)} bytes, sha TBD by reader)"
            )
        return 0
    except Exception as e:
        warn(f"gb-templates: publish failed ({type(e).__name__}): {str(e)[:200]}")
        return 1


# ---------------------------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gb-templates.py",
        description="the Bot templates this repo proposes, and the rules that keep them runnable",
    )
    ap.add_argument(
        "verb",
        nargs="?",
        default="list",
        choices=("list", "show", "validate", "stats", "deploy", "publish"),
    )
    ap.add_argument(
        "id", nargs="?", help="template id, for `show`, `deploy` and `publish`"
    )
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--path", default=str(TEMPLATES), help="templates directory")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="deploy/publish: act for real (dry run without it)",
    )
    ap.add_argument(
        "--screened",
        action="store_true",
        help="publish: a human reviewed the charter for private data",
    )
    ap.add_argument(
        "--source",
        default="Grok",
        help="publish: source Bot name or uuid (default: Grok, neutral)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="publish: allow minting drafts under a source that already owns a template",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    path = pathlib.Path(args.path)

    if args.verb == "deploy":
        if not args.id:
            hint = "gb templates deploy hello-computer"
            listing = "gb templates list"
            if args.apply:
                warn(
                    "gb-templates: `deploy --apply` needs a template id (nothing applied)"
                )
                warn(f"    plan first:  {hint}")
                warn("    (drop --apply)")
                warn(f"    list: {listing}")
            else:
                warn("gb-templates: `deploy` needs a template id")
                warn(f"    try:  {hint}")
                warn(f"    list: {listing}")
            if args.json:
                emit(
                    json.dumps(
                        {
                            "schema": "gb-template-deploy/2",
                            "tool": "gb",
                            "command": "templates",
                            "status": "USAGE",
                            "error": "deploy needs a template id",
                            "applied": False,
                            "hint": hint,
                            "plan": hint,
                            "did_you_mean": listing,
                        },
                        indent=1,
                    )
                )
            return 2
        return cmd_deploy(
            args.id,
            path,
            pathlib.Path(__file__).resolve().parents[1],
            bool(args.apply),
            bool(args.json),
        )
    if args.verb == "publish":
        if not args.id:
            hint = "gb templates publish hello-computer"
            listing = "gb templates list"
            warn("gb-templates: `publish` needs a template id")
            warn(f"    try:  {hint}")
            warn(f"    list: {listing}")
            return 2
        return cmd_publish(
            args.id,
            path,
            pathlib.Path(__file__).resolve().parents[1],
            bool(args.apply),
            bool(args.screened),
            resolve_source(args.source, pathlib.Path(__file__).resolve().parents[1]),
            bool(args.json),
            bool(args.force),
        )

    if args.verb == "validate":
        problems = check(path)
        if args.json:
            emit(
                json.dumps(
                    {
                        "schema": "gb-template-validate/1",
                        "path": str(path),
                        "templates": len(list(path.glob("*.json")))
                        if path.is_dir()
                        else 0,
                        "valid": not problems,
                        "problems": problems,
                    },
                    indent=1,
                )
            )
        elif problems:
            emit(f"INVALID — {len(problems)} problem(s) in {path}:")
            for p in problems:
                emit(f"  - {p}")
        else:
            rows = valid_only(path)
            sched = sum(
                1 for d in rows if d["routine"]["cadence"] in ("daily", "weekly")
            )
            emit(
                f"valid: {len(rows)} template(s) in {path} — every charter within "
                f"{CHARTER_MAX} chars, {sched} with a runnable routine, "
                f"{sum(1 for d in rows if d['approval_boundary'].strip())} with an approval "
                f"boundary"
            )
        return 1 if problems else 0

    if args.verb == "stats":
        doc = stats(path, ROOT)
        emit(json.dumps(doc, indent=1) if args.json else render_stats(doc))
        return 0

    rows = valid_only(path)
    if not rows:
        # An empty table is indistinguishable from "the templates are all broken" and from
        # "you are pointed at the wrong directory". Say which, on stderr, so stdout stays
        # parseable either way.
        found = len(list(path.glob("*.json"))) if path.is_dir() else 0
        warn(
            f"no valid templates in {path} "
            f"({'not a directory' if not path.is_dir() else f'{found} json file(s), none valid'})"
            f" — run `gb-templates.py validate --path {path}`"
        )
    if args.verb == "show":
        if not args.id:
            hint = "gb templates show hello-computer"
            listing = "gb templates list"
            warn("show needs a template id")
            warn(f"    try:  {hint}")
            warn(f"    list: {listing}")
            if args.json:
                emit(
                    json.dumps(
                        {
                            "schema": "gb-template-show/1",
                            "tool": "gb",
                            "command": "templates",
                            "status": "USAGE",
                            "error": "show needs a template id",
                            "hint": hint,
                            "did_you_mean": listing,
                        },
                        indent=1,
                    )
                )
            return 2
        hit = [d for d in rows if d["id"] == args.id]
        if not hit:
            hint, listing, preview = _teach_unknown_template(args.id, rows, "show")
            warn(f"no template {args.id!r}. Known ids: {preview}")
            warn(f"    try:  {hint}")
            warn(f"    list: {listing}")
            if args.json:
                emit(
                    json.dumps(
                        {
                            "schema": "gb-template-show/1",
                            "tool": "gb",
                            "command": "templates",
                            "status": "USAGE",
                            "error": f"no template {args.id!r}",
                            "hint": hint,
                            "did_you_mean": listing,
                        },
                        indent=1,
                    )
                )
            return 2
        emit(
            json.dumps(
                {
                    "schema": "gb-template-show/1",
                    "tool": "gb",
                    "command": "templates",
                    "template": hit[0],
                },
                indent=1,
            )
            if args.json
            else render_show(hit[0])
        )
        return 0

    if args.json:
        emit(
            json.dumps(
                {
                    "schema": "gb-template-list/1",
                    "count": len(rows),
                    "templates": [
                        {
                            "id": d["id"],
                            "name": d["name"],
                            "tier": d["tier"],
                            "category": d["category"],
                            "job": d["job"],
                            "charter_chars": d["charter_chars"],
                            "integrations": d["integrations"],
                            "cadence": d["routine"]["cadence"],
                            "when": d["routine"]["when"],
                            "replaces": d["replaces"],
                        }
                        for d in rows
                    ],
                },
                indent=1,
            )
        )
    else:
        emit(render_list(rows))
    return 0


if __name__ == "__main__":
    gbmain(main)
