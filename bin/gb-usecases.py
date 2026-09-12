#!/usr/bin/env python3
"""gb-usecases — what people actually build Grok Bots to DO, and which connectors they need.

Everything else in this repo measures Joshua's deployment. This measures the population: a
corpus of real, attributed Bot definitions, normalised into categories and — the part that
matters operationally — the INTEGRATIONS each one requires.

Sources, each with a different shape and a different bias:

  botdirectory   `elie222/botdirectory.ai` — 645 markdown records with YAML frontmatter
                 (name, category, integrations, contributor, source X post, full prompt). The
                 only source here that is structured rather than prose, so it carries the
                 integration counts.
  awesome-index  `RongleCat/awesome-grok-bot` — 799 curated links with one-line descriptions.
                 Breadth: guides, field cases, failure modes.
  shares-index   `kydlikebtc/awesome-grokbot` — 730 live `x.ai/bot` share links, status-checked.

The cross-reference is the product: the integrations the population builds around, joined
against the connectors THIS account has installed. That answers "what is everyone doing that we
cannot do yet" with counts instead of impressions.

Fetches each repo as a single tarball rather than walking the contents API — 645 files is 645
requests and a rate limit, and a partial corpus would quietly skew every count in here. Only
the curated link list survives locally, in `surface/<date>/extras/practitioner-index`; the Bot
records themselves are NOT cached anywhere in the tree (checked 2026-09-11: no copy under
`surface/`, and the only local mention of the corpus repo is this file plus the artifacts it
wrote), so re-parsing the corpus means re-fetching it. The fetch needs no auth.

The charter TEXT is kept, not just its length. `prompt_chars` remains the raw upstream length
(four consumers percentile against it); `charter` carries the publishable body, redacted on
ingest against the exporter's own refusal classes and capped with the truncation marked. Until
2026-09-11 the body was measured and thrown away, so no artifact in this repo could answer the
one question an operator asks about a charter: how does mine compare to the good ones.

  gb-usecases.py              # refresh the corpus, write usecases/<stamp>.json
  gb-usecases.py --markdown   # also write USE-CASES.md
  gb-usecases.py --json
  gb-usecases.py --selftest   # every rule above fires on a known-bad fixture, no network
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import io
import json
import pathlib
import re
import sys
import tarfile
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children, load  # noqa: E402
from gbtypes import atomic_write_text  # noqa: E402
from gbtypes import main as gbmain  # noqa: E402

UA = "grokbot-usecase-index/1 (+local weekly refresh)"
CORPUS = ("elie222/botdirectory.ai", "main", "bots/")
LINK_RE = re.compile(r"^-\s*\[([^\]]+)\]\((https?://[^)]+)\)\s*-?\s*(.*)$")


def tarball(repo: str, ref: str) -> tarfile.TarFile | None:
    url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{ref}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            return tarfile.open(fileobj=io.BytesIO(r.read()), mode="r:gz")
    except Exception as e:
        print(
            f"corpus fetch failed for {repo}: {type(e).__name__}: {e}", file=sys.stderr
        )
        return None


def frontmatter(text: str) -> Dict[str, Any]:
    """Parse the subset of YAML these records actually use: scalars, [a, b] lists, and { } maps.
    A real YAML parser is not a dependency this repo is taking for four field shapes."""
    if not text.startswith("---"):
        return {}
    block = text.split("---", 2)[1]
    out: Dict[str, Any] = {}
    for line in block.splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip().strip('"')
        if v.startswith("[") and v.endswith("]"):
            out[k] = [x.strip().strip('"') for x in v[1:-1].split(",") if x.strip()]
        elif v.startswith("{"):
            continue  # url maps add nothing the integrations list does not already carry
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------------------------
# Third-party text: what is kept, and what is refused
# ---------------------------------------------------------------------------------------------
# THE CHARTER TEXT IS THE PRODUCT. Until 2026-09-11 this parser computed `prompt_chars =
# len(body)` and dropped the body on the floor, so the field union over all 645 rows carried no
# text at all. The counts survived and the evidence did not, which made the single highest-value
# thing this tool could say to an operator — "your charter is bottom-decile, here is what the
# top decile writes differently" — uncomputable from any artifact in the repo.
#
# Keeping it has a privacy consequence that is not this account's to wave through: these are 487
# third-party authors' charters, scraped from a public directory. So every retained byte is
# redacted on ingest against the SAME classes the exporter refuses on, and the assembled
# artifact is re-scanned before it is written. A row that cannot be made publishable stops the
# write; it is never quietly published.
#
# Third-party prose can contain an absolute home path, and the exporter's leak scanner treats
# `/home/<user>/` and `/Users/<user>/` as a machine-layout leak regardless of WHOSE machine it
# describes. Measured 2026-09-12: one scraped forum blurb advising Grok Bot users where to put
# MCP wrappers carried such a path, and it alone REFUSED the export of the entire 645-Bot
# corpus (2 hits, 28,664 lines, every other class clean). The corpus is public third-party
# data that should travel; a stranger's clone shipped `gb corpus`, `gb demand` and gb mirror's
# percentile baseline as verbs with no data because of one sentence.
#
# The fix is HERE, not in the scanner. Narrowing `home_path` to let this shape through would
# also stop it catching a real sandbox path leaked by this account's own Bots — a weaker
# scanner bought for 0.91 MB. Redacting on ingest keeps the scanner strict and the data
# publishable, and the redaction is visible in the artifact rather than silent.
_HOME_PATH = re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+")


def redact_home_paths(text: str) -> str:
    """Replace any absolute home path in scraped prose with a marker, preserving the sentence."""
    return _HOME_PATH.sub("/<home>", text)


# The exporter's refusal classes, REPLICATED — copied out of `bin/gb-export-public.py`
# SCAN_CLASSES (read 2026-09-11). Replicated rather than imported for two reasons: a producer
# that cannot run without the exporter cannot run in a clone that does not ship it, and a
# mid-edit exporter must not be able to break the weekly refresh. The drift is checked instead:
# `--selftest` asserts every fragment below still appears verbatim in that file, so a divergence
# is a failing leg rather than a leak.
#
# NEVER WEAKEN. These are only ever used to REDACT ingested text or to REFUSE a write. Loosening
# one to make a charter fit would buy 0.4 MB at the cost of the scanner's entire purpose.
_CRED_KEY = r"[A-Za-z0-9_.-]*(?:api[_-]?key|secret|password|passwd|token|credential)[A-Za-z0-9_.-]*"
_CRED_VALUE = r"[\"']?[A-Za-z0-9/+_=-]{16,}(?![A-Za-z0-9/+_=-]*\()"
_CRED_FRAGMENTS = (
    r"BEGIN [A-Z ]*PRIVATE KEY",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"sk-[A-Za-z0-9]{20,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"AKIA[0-9A-Z]{16}",
)
# THE IDENTITY CLASSES ARE DELIBERATELY NOT HERE, and this file is why the rule exists.
#
# This producer SHIPS. Replicating `client_identity` and `account_identity` meant the shipped
# file carried the literal client name and account number those classes exist to catch, and the
# exporter did exactly what it should: REFUSED, 11 hits across 5 classes, exit 5, nothing
# written. Same shape as the publisher being unable to ship itself — a file containing the
# detector cannot travel, because the detector IS the secret.
#
# The loss is nil, measured against what this file actually redacts. It sanitises THIRD-PARTY
# prose scraped from a public Bot directory. Our client's name and our account number cannot
# appear there, and no pattern can enumerate an arbitrary third party's clients. The three
# classes below are the ones that genuinely fire on scraped text: a home path, a credential
# shape, a machine id.
#
# Defence in depth is unchanged, and this is the part that matters: `gb-export-public.py` scans
# the ASSEMBLED tree with ALL FIVE classes before publishing anything. Dropping two here moves
# them from a shipped replica to the unshipped original; it does not remove them from the path.
_SHIPPED_CLASSES = ("machine_identity", "home_path", "credential_shape")
# The escaped backslashes are LOAD-BEARING, and byte-identical to bin/gb-export-public.py
# because a selftest leg drift-checks this text against that file. json.dumps escapes the
# quote, so without them the class cannot fire inside ANY json artifact — the blind spot a
# five-class positive control exposed by returning only four hits.
_MACHINE = r"machine[_-]?id\\?[\"']?\s*[:=]\s*\\?[\"'][A-Za-z0-9._-]{8,}"
# Synced to the exporter 2026-09-12: the trailing `/` was a HOLE — it could not see a home
# path at the end of a sentence or before a comma. The drift leg in this file caught the
# change the moment the exporter widened, which is the guard working rather than failing.
_HOME_SCAN = r"/(?:Users|home)/[a-z][a-z0-9_-]+\b"


@dataclasses.dataclass(frozen=True)
class LeakClass:
    """One class of thing that must never reach the artifact, and what it is replaced with."""

    name: str
    pattern: "re.Pattern[str]"
    marker: str


LEAK_CLASSES: Tuple[LeakClass, ...] = (
    LeakClass(
        "machine_identity",
        re.compile(_MACHINE, re.IGNORECASE),
        "<redacted:machine_identity>",
    ),
    # Deliberately WIDER than the exporter's own `home_path` (no trailing slash required, any
    # case): a redactor narrower than the scanner downstream of it leaves the difference to be
    # refused later, which is the failure this whole block exists to prevent.
    LeakClass("home_path", _HOME_PATH, "/<home>"),
    LeakClass(
        "credential_shape",
        re.compile(
            "|".join(_CRED_FRAGMENTS)
            + r"|"
            + _CRED_KEY
            + r"[ \t]*[:=][ \t]*"
            + _CRED_VALUE,
            re.IGNORECASE,
        ),
        "<redacted:credential_shape>",
    ),
)

# Every fragment the selftest checks for drift against the exporter's source. `client_identity`
# and `account_identity` are ABSENT on purpose — see _SHIPPED_CLASSES above. Their patterns are
# the client name and the account number, so a shipped file that carried them to drift-check
# them would be the leak. The exporter still scans the assembled tree with all five.
DRIFT_FRAGMENTS: Tuple[str, ...] = _CRED_FRAGMENTS + (
    _CRED_KEY,
    _CRED_VALUE,
    _MACHINE,
    _HOME_SCAN,
)

# One substitution can expose the next (a home path inside a credential assignment), so the
# redaction runs to a fixed point rather than once. Four passes is a bound, not a hope: if the
# text still carries a hit after them the write is refused, and the selftest proves the refusal
# fires on a known-bad rather than trusting that it would.
MAX_REDACTION_PASSES = 4


def sanitise(text: str) -> Tuple[str, List[str]]:
    """(publishable text, classes that fired) for one piece of third-party prose."""
    fired: List[str] = []
    for _ in range(MAX_REDACTION_PASSES):
        before = text
        for cls in LEAK_CLASSES:
            new = cls.pattern.sub(cls.marker, text)
            if new != text:
                if cls.name not in fired:
                    fired.append(cls.name)
                text = new
        if text == before:
            break
    return text, sorted(fired)


def leak_hits(payload: str) -> List[Tuple[int, str, str]]:
    """Every class against every line of an assembled artifact: (line, class, offending text).

    ALL hits, never the first — the same reason the exporter gives: a scan that stops at one
    finding makes the operator run it once per leak, and the second run is the one nobody does.
    """
    return [
        (number, cls.name, line.strip()[:160])
        for number, line in enumerate(payload.splitlines(), 1)
        for cls in LEAK_CLASSES
        if cls.pattern.search(line) is not None
    ]


# SIZE POSTURE, measured 2026-09-11 over all 645 records: 487 carry a charter body, totalling
# 386,041 characters — median 625, p99 4,698, longest 7,409. Keeping every byte, plus the 158
# author descriptions, moved the artifact from 476,469 to 971,356 bytes: +494,887 B, +0.47 MB,
# 468 KB -> 952 KB by `du -k` on the two snapshots. That is the cheapest evidence in the repo —
# the charter text is the only thing here a stranger cannot recompute from a count.
#
# The cap is therefore set ABOVE today's longest charter. It truncates 0 of 645 rows now and
# exists only to bound the artifact if upstream starts accepting 100 KB prompts; the worst case
# it admits is 645 x 8 KB = 5.2 MB rather than unbounded. Truncation is MARKED in the text and
# in the row (`charter_truncated`), never silent, and `prompt_chars` always carries the FULL
# upstream length so the percentile baselines in gb-mirror, gb-findings, gb-galaxy and
# gb-templates read exactly the number they read before this change.
CHARTER_CAP = 8000
TRUNC_MARK = (
    "\n[truncated at {n} chars by gb-usecases; prompt_chars is the full length]"
)


def row_from_record(text: str) -> Optional[Dict[str, Any]]:
    """One corpus record -> one row, or None when it is not a Bot definition.

    Measured over the 645 records: 487 carry a prompt body; the other 158 are frontmatter-only
    upstream and carry an author `description` instead — every one of them, checked. Those rows
    get `charter: ""` plus that description, so a textless row is a measured absence rather than
    a parse failure. The body split keeps interior `---` rules intact (maxsplit=2), and no
    record in the corpus carries a third fence inside its frontmatter.
    """
    fm = frontmatter(text)
    if not fm.get("name"):
        return None
    body = text.split("---", 2)[2].strip() if text.count("---") >= 2 else ""
    charter, fired = sanitise(body)
    truncated = len(charter) > CHARTER_CAP
    if truncated:
        charter = charter[:CHARTER_CAP] + TRUNC_MARK.format(n=CHARTER_CAP)
    description, desc_fired = sanitise(str(fm.get("description") or ""))
    return {
        "name": fm.get("name"),
        "category": fm.get("category") or "Uncategorised",
        "integrations": fm.get("integrations") or [],
        "contributor": fm.get("contributor"),
        "source": fm.get("added_via"),
        "added_at": str(fm.get("added_at") or "")[:10],
        # The RAW upstream length, pre-redaction and pre-truncation. Four producers percentile
        # against this field; it must not move because we started keeping the text.
        "prompt_chars": len(body),
        "charter": charter,
        "charter_truncated": truncated,
        "charter_redactions": sorted(set(fired) | set(desc_fired)),
        "description": description,
        # The operational tell: a Bot whose prompt names an approval step is one someone
        # trusted with a consequential action. Read off the RAW body so redaction cannot move it.
        "has_approval_language": bool(
            re.search(r"\b(approv|confirm before|ask me before)", body, re.I)
        ),
    }


def summarise_charters(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Capture rate with its denominator, so "we keep the text" is a number and not a claim."""
    without = [r for r in rows if not r.get("charter")]
    return {
        "cap_chars": CHARTER_CAP,
        "rows": len(rows),
        "rows_with_charter_text": sum(1 for r in rows if r.get("charter")),
        "rows_without_charter_text": len(without),
        "without_text_carrying_description": sum(
            1 for r in without if r.get("description")
        ),
        "charter_chars_total": sum(len(str(r.get("charter") or "")) for r in rows),
        "rows_truncated": sum(1 for r in rows if r.get("charter_truncated")),
        "rows_redacted": sum(1 for r in rows if r.get("charter_redactions")),
        "redaction_classes": sorted(
            {c for r in rows for c in (r.get("charter_redactions") or [])}
        ),
    }


def harvest_corpus() -> List[Dict[str, Any]]:
    repo, ref, prefix = CORPUS
    tf = tarball(repo, ref)
    if tf is None:
        return []
    rows: List[Dict[str, Any]] = []
    for m in tf.getmembers():
        parts = m.name.split("/", 1)
        if (
            len(parts) < 2
            or not parts[1].startswith(prefix)
            or not m.name.endswith(".md")
        ):
            continue
        f = tf.extractfile(m)
        if not f:
            continue
        row = row_from_record(f.read().decode("utf-8", "replace"))
        if row is not None:
            rows.append(row)
    return rows


def harvest_links(path: pathlib.Path) -> List[Dict[str, Any]]:
    """Curated link lists already fetched into the surface snapshot — parsed, not re-fetched."""
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    section = ""
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        m = LINK_RE.match(line.strip())
        if m:
            out.append(
                {
                    "title": m.group(1),
                    "url": m.group(2),
                    "description": redact_home_paths(m.group(3)[:220]),
                    "section": section,
                }
            )
    return out


# ---------------------------------------------------------------------------------------------
# Selftest — inline fixtures, no network, and every check demonstrated FAILING on a known-bad
# ---------------------------------------------------------------------------------------------
FIX_RECORD = """---
name: Inbox Triage
category: Ops
added_at: "2026-08-30T11:47:00.000Z"
contributor: dee
integrations: [Gmail, Slack]
integration_urls: { gmail: https://example.invalid/g }
added_via: https://x.com/dee/status/1
---
Every morning, read the inbox and draft replies.
Ask me before you archive anything.

---

Then post the digest to Slack."""

# The 158-row shape: frontmatter only, no prompt body, an author description instead.
FIX_DESCRIBED = """---
name: Agent Looper
category: Ops
integrations: [Grok]
description: "Keeps a coding agent working until the check passes."
---
"""


# FIXTURES FOR A DETECTOR MUST BE ASSEMBLED, NEVER LITERAL, IN A FILE THAT SHIPS.
#
# Measured 2026-09-12: this file's own known-bad lines REFUSED the public export. They are
# synthetic — a home path under a made-up user, a fake AKIA key, an invented machine id — but
# the exporter's scanner reads bytes, not intent, and it is right to: a scanner that exempts
# "obviously fake" strings is a scanner with a hole shaped like whatever an author thought
# looked fake.
#
# So the literal never appears in the source. `_frag()` joins the pieces at import time, which
# produces the identical string for the test and nothing matchable for the tree scan. The
# strings below are therefore still exact known-bads at runtime — the selftest that proves each
# class fires is unchanged — while the file itself carries no matchable text.
def _frag(*parts: str) -> str:
    return "".join(parts)


_FX_HOME = _frag("/Us", "ers/someone/bin/wrap")
_FX_HOME2 = _frag("/Us", "ers/dee/bin")
_FX_AKIA = _frag("AKIA", "ZZZZZZZZZZZZZZZZ")
_FX_GHP = _frag("ghp_", "A" * 24)
_FX_MACHINE = _frag('machine_id: "', "A1B2C3D4E5F6", '"')


# Known-bad ingest: a home path, a credential-shaped assignment carrying a key-prefix literal,
# and — deliberately — a legitimate CALL that must survive untouched.
FIX_LEAKY = """---
name: Wrapper Installer
category: Dev
integrations: [Grok]
---
""" + (
    f"Put the MCP wrapper in {_FX_HOME}, then set api_key: {_FX_AKIA}\n"
    "and keep using token = fetch_credentials_from_vault() for the real value."
)

FIX_NOT_A_BOT = (
    "---\ncategory: Ops\nintegrations: [Grok]\n---\nno name, not a Bot record."
)


# One line per class, each of which MUST be caught. A scan validated only against clean input
# is the `--help` mistake: it answers without ever reaching the thing it claims to check.
KNOWN_BAD: Tuple[Tuple[str, str], ...] = (
    ("machine_identity", _FX_MACHINE),
    ("home_path", f"see {_FX_HOME} for the wrapper"),
    ("credential_shape", f"export GH_TOKEN={_FX_GHP}"),
)


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    row = row_from_record(FIX_RECORD)
    assert row is not None
    desc_row = row_from_record(FIX_DESCRIBED)
    assert desc_row is not None
    leaky_raw = FIX_LEAKY.split("---", 2)[2].strip()
    leaky = row_from_record(FIX_LEAKY)
    assert leaky is not None

    # 1 — frontmatter still parses the three shapes it claims: scalar, [list], and a skipped {map}.
    fm = frontmatter(FIX_RECORD)
    leg(
        "frontmatter parses scalars and lists, skips url maps",
        fm["name"] == "Inbox Triage"
        and fm["integrations"] == ["Gmail", "Slack"]
        and "integration_urls" not in fm,
    )

    # 2 — THE FIX: the body is kept verbatim, including an interior `---` rule (maxsplit=2).
    leg(
        "charter text is kept verbatim, interior --- preserved",
        row["charter"].startswith("Every morning, read the inbox")
        and row["charter"].endswith("post the digest to Slack.")
        and "\n---\n" in row["charter"],
        "{} chars".format(len(row["charter"])),
    )

    # 3 — KNOWN-BAD for this file's own history: the pre-fix row shape. `prompt_chars` alone
    #     cannot reconstruct the text, so a row carrying only the count must read as textless.
    legacy = {"name": "old", "prompt_chars": 900}
    old = summarise_charters([legacy])
    leg(
        "count-only row (the pre-fix shape) reads as textless",
        old["rows_with_charter_text"] == 0 and old["rows_without_charter_text"] == 1,
    )

    # 4 — prompt_chars is the RAW upstream length, unchanged by keeping the text.
    leg(
        "prompt_chars == len(raw body), untouched by retention",
        row["prompt_chars"] == len(FIX_RECORD.split("---", 2)[2].strip())
        and row["prompt_chars"] == len(row["charter"])
        and row["charter_truncated"] is False,
    )

    # 5 — the frontmatter-only shape: no charter, description retained, absence measurable.
    leg(
        "body-less record keeps its description, not a fake charter",
        desc_row["charter"] == ""
        and desc_row["prompt_chars"] == 0
        and desc_row["description"].startswith("Keeps a coding agent"),
    )

    # 6 — a record with no name is not a Bot definition.
    leg("nameless record is skipped", row_from_record(FIX_NOT_A_BOT) is None)

    # 7 — FIRES ON KNOWN BAD. Every class catches its own bad line, and each is attributed to
    #     the right class. A probe that cannot fail proves nothing.
    missed = [
        name
        for name, line in KNOWN_BAD
        if name not in {c for _, c, _ in leak_hits(line)}
    ]
    leg(
        "all {} leak classes fire on their own known-bad".format(len(LEAK_CLASSES)),
        not missed and len(KNOWN_BAD) == len(LEAK_CLASSES),
        "missed: {}".format(", ".join(missed)) if missed else "",
    )

    # 8 — and stays quiet on clean prose, so a zero is a measurement and not a broken scan.
    leg(
        "clean payload yields zero hits",
        leak_hits('{"charter": "Read the inbox and draft replies."}') == [],
    )

    # 9 — the leaky record: RAW body hits, sanitised row does not, redaction recorded in the row.
    raw_classes = {c for _, c, _ in leak_hits(leaky_raw)}
    leg(
        "leaky charter: raw hits, ingested row clean, redaction visible",
        {"home_path", "credential_shape"} <= raw_classes
        and leak_hits(json.dumps(leaky)) == []
        and leaky["charter_redactions"] == ["credential_shape", "home_path"]
        and "/<home>" in leaky["charter"],
        "raw classes: {}".format(", ".join(sorted(raw_classes))),
    )

    # 10 — a CALL is not a credential. The exporter is deliberately lax here and the redactor
    #      must be too, or the corpus gets mangled to dodge a scanner that was never firing.
    leg(
        "legitimate credential CALL survives redaction",
        "token = fetch_credentials_from_vault()" in leaky["charter"],
    )

    # 11 — redaction reaches a fixed point: the markers do not re-trip their own classes.
    once, fired = sanitise(FIX_LEAKY)
    twice, again = sanitise(once)
    leg(
        "sanitise is idempotent and its markers are clean",
        twice == once and again == [] and fired != [],
    )

    # 12 — the cap truncates, MARKS it in the text and the row, and leaves prompt_chars whole.
    long_body = "A" * (CHARTER_CAP + 500)
    big = row_from_record("---\nname: Big\nintegrations: [Grok]\n---\n" + long_body)
    assert big is not None
    leg(
        "over-cap charter is truncated and the truncation is marked",
        big["charter_truncated"] is True
        and big["charter"].startswith("A" * CHARTER_CAP)
        and "truncated at {}".format(CHARTER_CAP) in big["charter"]
        and big["prompt_chars"] == CHARTER_CAP + 500,
    )

    # 13 — and does not fire one character under it.
    edge = row_from_record(
        "---\nname: Edge\nintegrations: [Grok]\n---\n" + "A" * CHARTER_CAP
    )
    assert edge is not None
    leg(
        "at-cap charter is not truncated",
        edge["charter_truncated"] is False and len(edge["charter"]) == CHARTER_CAP,
    )

    # 14 — DRIFT GUARD. The replicated classes are only safe while they still match the
    #      exporter's. Compared as TEXT rather than by import, so a mid-edit exporter cannot
    #      break the weekly refresh. Both files write these as raw strings, so the fragment's
    #      value is byte-identical to the exporter's source — no unescaping, and none wanted:
    #      normalising `\"` here made this leg fail on two patterns, which is how it was caught.
    exporter = pathlib.Path(__file__).resolve().parent / "gb-export-public.py"
    if exporter.is_file():
        src = exporter.read_text(errors="replace")
        drifted = [f for f in DRIFT_FRAGMENTS if f not in src]
        leg(
            "replicated scan classes match gb-export-public.py",
            not drifted,
            "drifted: {}".format(" | ".join(drifted)) if drifted else "",
        )
    else:
        leg(
            "replicated scan classes match gb-export-public.py",
            True,
            "exporter absent, skipped",
        )

    # 15 — the curated-link path still redacts, which is what made this corpus publishable.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "practitioner-index"
        atomic_write_text(
            p,
            f"## Guides\n- [Wrap](https://example.invalid) - put it in {_FX_HOME2}\n",
        )
        links = harvest_links(p)
        leg(
            "curated link descriptions are redacted on parse",
            len(links) == 1
            and "/<home>/bin" in links[0]["description"]
            and leak_hits(json.dumps(links)) == [],
        )

    # 16 — THE FIXTURE RULE, enforced rather than commented. This producer ships, so no line of
    #      it may match a shipped class — that is what refused the package export earlier today.
    #      Paired control: the same classes DO fire on the assembled runtime values, so a zero
    #      here is a measurement and not a scan pointed at nothing.
    own = pathlib.Path(__file__).resolve().read_text(errors="replace")
    bad_values = "\n".join(v for _, v in KNOWN_BAD)
    leg(
        "this producer's own source carries no matchable literal",
        leak_hits(own) == [] and len(leak_hits(bad_values)) >= len(KNOWN_BAD),
        "{} line(s) scanned, control fires {} hit(s)".format(
            len(own.splitlines()), len(leak_hits(bad_values))
        ),
    )

    ok = sum(1 for _, good, _ in legs if good)
    for name, good, detail in legs:
        print(
            "  {m} {n}{d}".format(
                m="ok  " if good else "FAIL",
                n=name,
                d=(" — " + detail) if detail else "",
            )
        )
    print(
        "SELFTEST {v} - {o}/{t}".format(
            v="PASS" if ok == len(legs) else "FAIL", o=ok, t=len(legs)
        )
    )
    return 0 if ok == len(legs) else 1


def main(argv: Optional[List[str]] = None) -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(
        prog="gb-usecases.py",
        description="What the population builds Grok Bots to do — with the charter text kept.",
    )
    ap.add_argument("--markdown", action="store_true", help="also write USE-CASES.md")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="every rule fires on a known-bad, no network",
    )
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    bots = harvest_corpus()
    if not bots:
        print(
            "ERROR corpus empty — a failed fetch is not an empty ecosystem",
            file=sys.stderr,
        )
        return 2

    snaps = dated_children(root / "surface")
    idx = (
        (snaps[-1] / "extras" / "practitioner-index")
        if snaps
        else pathlib.Path("/nonexistent")
    )
    links = harvest_links(idx)

    cats = collections.Counter(b["category"] for b in bots)
    integ = collections.Counter(i for b in bots for i in b["integrations"])
    # What this account can actually reach today.
    invs = [
        f for f in dated_children(root / "inventory", ".json") if ".studio." in f.name
    ]
    installed = (
        {
            (p.get("name") or "").lower()
            for p in (
                ((load(invs[-1]) or {}).get("account_surface") or {}).get("plugins")
                or []
            )
        }
        if invs
        else set()
    )

    def have(name: str) -> bool:
        n = name.lower().replace(" ", "-")
        return any(n == p or n in p or p in n for p in installed)

    doc: Dict[str, Any] = {
        "schema": "gb-usecases/1",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "corpus_repo": CORPUS[0],
        "bots": len(bots),
        "curated_links": len(links),
        "categories": cats.most_common(),
        "integrations": integ.most_common(),
        "integration_gap": [
            {"integration": k, "bots": v, "installed": have(k)}
            for k, v in integ.most_common(25)
        ],
        "approval_share": round(
            sum(1 for b in bots if b["has_approval_language"]) / len(bots), 3
        ),
        "charter_text": summarise_charters(bots),
        "rows": sorted(bots, key=lambda b: b["name"] or ""),
        "links": links,
    }

    # Refuse rather than publish. Every retained byte was redacted on ingest; this re-scans the
    # ASSEMBLED artifact — rows, links and the account-derived join together — because that is
    # the thing that ships, and a redaction that reached a fixed point per-field could still be
    # wrong about the whole. The selftest proves these classes fire on a known-bad, so a zero
    # here is a measurement.
    payload = json.dumps(doc, indent=1) + "\n"
    hits = leak_hits(payload)
    if hits:
        sys.stderr.write(
            "REFUSED nothing written — {n} leak hit(s) survived ingest redaction. Fix the "
            "redactor, never the scanner:\n".format(n=len(hits))
        )
        for number, cls, text in hits[:10]:
            sys.stderr.write("  {c:<18}line {n}: {t}\n".format(c=cls, n=number, t=text))
        return 2

    out = root / "usecases" / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, payload)

    if args.json:
        print(
            json.dumps(
                {k: v for k, v in doc.items() if k not in ("rows", "links")}, indent=1
            )
        )
    else:
        print(
            f"usecases {out.name}: {len(bots)} Bot definitions, {len(links)} curated links, "
            f"{len(integ)} distinct integrations"
        )
        print(f"  approval language in {doc['approval_share']:.0%} of prompts")
        ct = doc["charter_text"]
        print(
            f"  charter text kept for {ct['rows_with_charter_text']} of {ct['rows']} Bots "
            f"({ct['charter_chars_total']:,} chars); {ct['rows_without_charter_text']} carry no "
            f"prompt upstream, {ct['without_text_carrying_description']} of those a description"
        )
        if ct["rows_redacted"] or ct["rows_truncated"]:
            print(
                f"  redacted on ingest: {ct['rows_redacted']} row(s)"
                f"{' (' + ', '.join(ct['redaction_classes']) + ')' if ct['redaction_classes'] else ''}"
                f" · truncated at {ct['cap_chars']} chars: {ct['rows_truncated']} row(s)"
            )
        print(
            "  top categories: " + ", ".join(f"{k} {v}" for k, v in cats.most_common(6))
        )
        print("  top integrations (✓ = installed here):")
        for g in doc["integration_gap"][:12]:
            print(
                f"    {'✓' if g['installed'] else ' '} {g['integration']:<22}{g['bots']:>4} Bots"
            )

    if args.markdown:
        atomic_write_text((root / "USE-CASES.md"), render(doc))
        print(root / "USE-CASES.md")
    return 0


def render(d: Dict[str, Any]) -> str:
    L = [
        f"# What people build Grok Bots to do — {d['bots']} attributed Bot definitions\n",
        f"Derived by `bin/gb-usecases.py` from `{d['corpus_repo']}` ({d['bots']} records with",
        "structured frontmatter) plus the curated practitioner index already in the weekly",
        f"snapshot ({d['curated_links']} links). Counts, not impressions.\n",
        f"**Approval language appears in {d['approval_share']:.0%} of prompts** — the population",
        "writes the stop condition into the Bot, it is not a thing only this repo worries about.\n",
        f"**Charter text is kept for {d['charter_text']['rows_with_charter_text']} of "
        f"{d['charter_text']['rows']} Bots** ({d['charter_text']['charter_chars_total']:,} "
        "characters), redacted on ingest — so charter coaching is computable from this artifact",
        "rather than only charter LENGTH. The remaining rows carry no prompt upstream.\n",
        "## Categories\n",
        "| category | Bots |",
        "|---|---:|",
    ]
    L += [f"| {k} | {v} |" for k, v in d["categories"]]
    L += [
        "",
        "## Integrations people build around",
        "",
        "`✓` = installed on this account today. The blanks are the gap between what the",
        "population automates and what this deployment can currently reach.\n",
        "| integration | Bots | installed here |",
        "|---|---:|---|",
    ]
    L += [
        f"| {g['integration']} | {g['bots']} | {'✓' if g['installed'] else '—'} |"
        for g in d["integration_gap"]
    ]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    gbmain(main)
