#!/usr/bin/env python3
"""gb-mirror — what THIS machine can tell you about YOUR OWN Grok Bot deployment, in 90 seconds.

WHY THIS FILE EXISTS. Every other producer in `bin/` measures the WORLD (645 attributed Bots,
452 tracked repos, the shipped template set, one market) or measures JOSHUA (13 Bots, 2 routines
of which 1 has ever run, one weekly allowance). A stranger who clones this repo gets neither:
the world numbers are about other people and the account numbers are about somebody else's
account. The first question a new operator actually has is "what does my own fleet look like,
and is it shaped like a fleet that works" — and until this file there was no verb that answered
it without a keychain prompt, a network round trip, a deployment audit, or a single artifact on
disk.

So this reads the desktop client's OWN state directory and reports six numbers plus one
sentence. No token. No network. No repo state. No setup. Tier B (`--deep`) then adds the
server-only facts over the READ-ONLY RPC path that `bin/gb-pull-inventory.py` already owns —
reused, never reimplemented, and never on the write side.

WHAT TIER A READS, AND HOW IT IS SHAPED (measured 2026-09-11 on a live install, app 0.47.0).
`~/Library/Application Support/Grok Bot/sand-client-persistence/` holds one file per client
state slice, named for the BASE32 of the slice key with its padding stripped
(`gblib.unb32` decodes it). Three slices carry deployment facts:

    …roster.last-roster              one row per Bot: name, `description` (the charter), title,
                                     createdAt/updatedAt/lastActivityAt/lastViewedAt,
                                     notificationsEnabled, notifyOnUpdatesEnabled, origin,
                                     harness, isHiddenFromSidebar, isGroup, memberIds
    …transcript.replicas.<uuid>      one per Bot the client has opened — a transcript replica,
                                     and, crucially, a UUID
    …sidebar.last-sections           the operator's own grouping of Bot ids into sections

THE TRAP THIS FILE IS BUILT AROUND, and it cost the repo a wrong conclusion today. The roster
blob is a PER-MACHINE CACHE. It lags the server, by minutes or by a sync it never got: a Bot
created through the API on another Mac was live in `ListGrokBotAgents` and visible in the app
while this machine's cached roster omitted it, and a reader concluded the Bot had been deleted
(gb-pull-inventory.py:290-296). Measured here, right now: the roster carries 11 rows while the
transcript replicas carry 20 uuids, NINE of which are in no roster row. So absence from Tier A
is NEVER evidence that a Bot does not exist, this file says that in its own output, and it
reports the recoverable-id delta as a number rather than hiding it.

WHAT TIER A CANNOT SEE IS MEASURED, NOT ASSUMED. Listing blind spots from memory is how a tool
starts lying a release later. `blind_spots()` greps every local blob for the field names that a
server record WOULD carry (`nextRunAt` and `recordJson` for a routine, `usagePercent` for the
allowance, `legacyAgentId` for API addressability, `blockInstructions` for account guardrails)
and reports the count it found. Because an empty result is not evidence of absence, the same
probe carries POSITIVE CONTROLS — `description`, `notificationsEnabled`, `lastActivityAt`, all
roster fields that MUST be found. If a control comes back zero the grep is broken, and the whole
blind-spot list degrades to UNMEASURED instead of being reported as "absent".

THE PERCENTILE IS A CONFORMITY SCORE AND THE OUTPUT SAYS SO. Ranking a fleet against 645
attributed Bots is useful for charter length and integration count. It is ACTIVELY MISLEADING
for approval language, because the measurement runs the other way: the ten most prolific
builders in that corpus use approval language LESS than everybody else (measured live below,
over the rows that actually carry a charter). "Be more like the top builders" is therefore wrong
advice on the safety axis, `caveat()` computes that split from the corpus rather than quoting
it, and no row in this file calls a percentile a safety score.

HISTORY, AND WHY IT IS PART OF THIS FILE RATHER THAN A NEW VERB. Until `--save` existed every
run was a fresh reading with nothing to compare against, so the one artifact an operator most
wants — "I changed X, here is what moved" — was impossible from the verb that already held every
number needed to produce it. `--save` persists the `--json` envelope byte for byte into
`mirror/<ISO>.json`; `--since` reads one back and reports what MOVED per numbered finding.

The direction is the product, not the delta. Each of the six findings carries `better` (`up`,
`down` or `neither`), because +3 on "Bots that can interrupt you" is an improvement and +3 on
"Dormant 7d+" is a regression — a diff that printed signed deltas without that would be a table.
`neither` is a real answer and is used where this file has not earned an opinion: there is no
universally better charter length, and its own CAVEAT measures the corpus using approval
language in the OPPOSITE direction from "more is safer".

`--since` REFUSES rather than producing a nonsense delta when the snapshot is not another
reading of the same fleet. The discriminator is read off what the envelope already carries:
`client.account_slot` (strong: two accounts are two fleets), `platform.support_dir` (weak, and
said to be weak: two Macs with the same username share the path) and `platform.os`. A snapshot
whose schema predates the `metric`/`better` fields is refused by name, checked both on the
`schema` string it CLAIMS and on the rows that are the EVIDENCE. "Nothing moved" is printed as
the real answer it is, never dressed up as a finding.

    gb-mirror.py                     Tier A: six numbers, one sentence, local only
    gb-mirror.py --deep              Tier B: adds routines, memory, usage over read-only RPCs
    gb-mirror.py --json              the same, machine-readable
    gb-mirror.py --out PATH          write the envelope (atomically) as well as printing it
    gb-mirror.py --save              record this reading in mirror/<ISO>.json
    gb-mirror.py --since latest      diff this reading against the newest saved snapshot
    gb-mirror.py --since PATH        diff against one named snapshot
    gb-mirror.py --selftest          the readers, the verdict and the diff fire on fixtures

EXIT CODES
    0   measured (including "measured, and you have zero Bots" — that is an answer, and
        including "measured, and nothing moved since the snapshot")
    1   the selftest failed
    2   bad invocation, including a `--since` snapshot this build must not diff: a missing
        file, a file that is not a reading, another account, another machine's state
        directory, or a schema with no direction in it
    3   this machine cannot answer: unsupported platform, no support directory, no roster
        cached yet, nothing saved yet for `--since latest`, or `--deep` was asked for and
        the session could not be read
    130 cancelled (SIGINT); 143 (SIGTERM) — via `gbtypes.main`

READ-ONLY BY CONSTRUCTION. Tier A opens files. Tier B calls `gb-pull-inventory.py`'s `rpc`,
which refuses any method that is not a `List`/`Get` (gb-pull-inventory.py:124). Nothing here
creates, updates, deletes or messages a Bot.
"""

from __future__ import annotations

import base64
import bisect
import dataclasses
import datetime as dt
import json
import pathlib
import re
import statistics
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import arg, parse  # noqa: E402
from gblib import SUPPORT_DIR_ENV, Platform, dated_children  # noqa: E402
from gblib import platform_support, unb32  # noqa: E402
from gbtypes import atomic_write_json, atomic_write_text  # noqa: E402
from gbtypes import main as gbmain  # noqa: E402
from gbtypes import read_text_capped  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
# `/2` because `numbers[]` rows gained `metric`, `whole`, `unit` and `better` — the machine-
# readable half a before/after diff needs. A `/1` snapshot carries none of them, so `--since`
# refuses it by NAME rather than silently diffing string values whose direction it cannot know.
SCHEMA = "gb-mirror/2"
SUMMARY = (
    "read your own Grok Bot deployment off this machine: six numbers and one sentence"
)

EXIT_OK, EXIT_SELFTEST, EXIT_USAGE, EXIT_ENVIRONMENT = 0, 1, 2, 3

# The slice-key suffixes this file understands. Matched on the DECODED key, never on the base32
# filename, so a client that changes its encoding breaks loudly instead of silently reading zero
# Bots.
PERSIST_DIR = "sand-client-persistence"
KEY_ROSTER = ".roster.last-roster"
KEY_REPLICA = ".transcript.replicas."
KEY_SECTIONS = ".sidebar.last-sections"
KEY_ACCOUNT = ".client-meta.account-slot"

# THE APPROVAL DETECTOR. Single-sourced in spirit from `bin/gb-usecases.py:121`, which is what
# produced the 31.9% corpus figure this file compares against. It is re-declared here rather than
# imported because importing the harvester would drag its network path into a local-only verb —
# and a `selftest` leg reads the literal back out of that file and FAILS on drift, so the two
# cannot diverge unnoticed. Comparing our share against the corpus share with a different
# detector would be the subtlest wrong number in the whole tool.
APPROVAL_RE = re.compile(r"\b(approv|confirm before|ask me before)", re.I)
USECASES_PRODUCER = ROOT / "bin" / "gb-usecases.py"

# THE SCHEDULABLE CEILING. 900 is `CHARTER_MAX` in `bin/gb-templates.py:78` and the number is not
# tidiness: a charter longer than this is a constitution rather than a job, and it cannot be
# pasted into a routine prompt, which is the observed reason a 1,029-median fleet has never run a
# routine. Reused, not re-chosen — two verbs must not hold two opinions about the same ceiling.
CHARTER_MAX = 900

# Dormancy. Seven days because the shortest cadence any shipped template uses is weekly, so a Bot
# with no activity for longer than a week has missed at least one full cycle of the fastest job
# this repo proposes.
DORMANT_DAYS = 7

# WHICH WAY IS BETTER, PER FINDING, because it is not a property of the tool — it is a property
# of the row. +3 on "Bots that can interrupt you" is an improvement and +3 on "Dormant 7d+" is a
# regression, so a diff that prints signed deltas without this is a table rather than a finding.
# `NEITHER` is a real answer and is used twice on purpose: there is no universally better charter
# length (row 3 is where length becomes a defect), and this file's own CAVEAT says the corpus
# measures approval language running the OTHER way, so calling more of it "better" would
# contradict the sentence printed six lines below it.
BETTER_UP, BETTER_DOWN, BETTER_NEITHER = "up", "down", "neither"

# Where a saved reading lives, and the envelope a diff answers with. One file per reading, named
# for the reading's own `measured_at` — not for the wall clock at write time — so the artifact
# and its content cannot disagree about when the fleet was in that shape.
SNAP_DIR = ROOT / "mirror"
DIFF_SCHEMA = "gb-mirror-diff/1"

# A ceiling on a snapshot this process will load. The Tier A envelope for the 13-Bot fleet this
# was measured on is 10,037 bytes; 8 MB is ~800x that and exists so a corrupt or hostile file in
# `mirror/` cannot be read into memory unbounded.
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024

# Severity, ranked, so "did the verdict get worse" is answerable rather than a string compare.
# `verdict()` emits GREEN and RED today; AMBER is carried because a third arm is a one-line
# change and a diff that crashed on it would be the worst possible time to discover the gap.
SEVERITY_RANK: Dict[str, int] = {"GREEN": 0, "AMBER": 1, "RED": 2}

# A ceiling on local state this process will hold. The largest blob measured here is 115 KB and
# the whole directory is ~800 KB; 4 MB per blob is ~35x the worst case and exists so a corrupt or
# hostile state file cannot be read into memory unbounded.
MAX_BLOB_BYTES = 4 * 1024 * 1024

# What Tier A structurally cannot know, and the field name each server record would carry if it
# were cached locally. `marker` is grepped; `via` is the RPC that does answer it.
BLIND_SPOTS: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    (
        "routines and schedules",
        ("nextRunAt", "recordJson"),
        "ListGrokBotAgentAutomations",
    ),
    ("memory shards", ("memoryShard", "shardId"), "ListGrokBotMemoryShards"),
    ("weekly allowance and spend", ("usagePercent",), "GetSandUsageStatus"),
    ("API addressability", ("legacyAgentId",), "ListGrokBotAgents"),
    (
        "account guardrails (auto-review)",
        ("blockInstructions", "autoReviewInstructions"),
        "GetGrokBotUserMcpSettings",
    ),
    ("connectors, MCP servers and plugins", ("isVerified",), "ListUserPluginInstalls"),
    ("skills attached to a Bot", ("skillId",), "ListGrokBotAgentSkills"),
)

# THE POSITIVE CONTROLS, IN TWO TIERS, and the tiering is a defect fix rather than a nicety.
#
# MEASURED 2026-09-11 against a planted fresh-machine state directory (signed in, synced, zero
# Bots): a single flat control list made the FIRST THING A STRANGER SEES read
# "TIER A BLIND SPOTS: UNMEASURED", because `description` and `notificationsEnabled` are ROSTER
# ROW fields and a zero-Bot roster has no rows to carry them. The probe was working perfectly;
# the control was asking a question the input could not answer, and the tool called its own
# healthy answer untrustworthy.
#
# So: the STRUCTURAL control is a field every slice blob carries whatever the account holds, and
# it is what proves the reader decoded a real filename and read real content. The BOT controls
# are stronger — they prove a marker inside a roster ROW would have been found — and they are
# only applicable when there is at least one row to hold them. A control that cannot apply is
# skipped and SAID to be skipped, never silently passed and never counted as a failure.
STRUCTURAL_CONTROLS: Tuple[str, ...] = ("schemaVersion",)
BOT_CONTROLS: Tuple[str, ...] = (
    "description",
    "notificationsEnabled",
    "lastActivityAt",
)


# ---------------------------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Bot:
    """One Bot, as the local cache knows it.

    Frozen because a roster row is a READING, not a working value: something that can be mutated
    after it is parsed is something a later stage can quietly correct, and then the number printed
    is not the number measured.
    """

    uuid: str
    name: str
    charter_chars: int
    approval: bool
    notifications: bool
    notify_on_updates: bool
    hidden: bool
    origin: Optional[str]
    harness: Optional[str]
    last_activity_ms: Optional[int]
    created_ms: Optional[int]

    @property
    def has_charter(self) -> bool:
        return self.charter_chars > 0

    @property
    def over_cap(self) -> bool:
        return self.charter_chars > CHARTER_MAX

    def idle_days(self, now_ms: int) -> Optional[float]:
        stamp = self.last_activity_ms or self.created_ms
        if not stamp:
            return None
        return max(0.0, (now_ms - stamp) / 86_400_000.0)

    def to_json(self, now_ms: int) -> Dict[str, Any]:
        idle = self.idle_days(now_ms)
        return {
            "uuid": self.uuid,
            "name": self.name,
            "charter_chars": self.charter_chars,
            "over_schedulable_cap": self.over_cap,
            "approval_language": self.approval,
            "notifications_enabled": self.notifications,
            "notify_on_updates_enabled": self.notify_on_updates,
            "hidden_from_sidebar": self.hidden,
            "origin": self.origin,
            "harness": self.harness,
            "idle_days": None if idle is None else round(idle, 1),
        }


@dataclasses.dataclass(frozen=True)
class Probe:
    """One blind-spot probe: the fact, the markers grepped, how many blobs matched."""

    fact: str
    markers: Tuple[str, ...]
    hits: int
    via: str

    @property
    def absent(self) -> bool:
        return self.hits == 0

    def to_json(self) -> Dict[str, Any]:
        return {
            "fact": self.fact,
            "markers": list(self.markers),
            "blobs_matching": self.hits,
            "local_state_has_it": not self.absent,
            "served_by": self.via,
        }


@dataclasses.dataclass(frozen=True)
class Baseline:
    """The corpus this fleet is ranked against, read live from `usecases/<newest>.json`.

    `available` is separate from the numbers because "there is no corpus snapshot here" and "the
    corpus median is zero" are different facts, and a fresh clone hits the first one.
    """

    available: bool
    source: Optional[str]
    captured_at: Optional[str]
    bots: int
    charter_samples: Tuple[int, ...]
    charter_median: Optional[float]
    integrations_mean: Optional[float]
    approval_share: Optional[float]
    top_builder_approval: Optional[float]
    other_builder_approval: Optional[float]
    top_builder_bots: int
    detail: str

    def percentile(self, value: float) -> Optional[float]:
        """Where `value` sits in the corpus's charter-length distribution, 0-100.

        `bisect_right` so a fleet whose median EQUALS the corpus median lands at the share of
        builders it is at-or-above, which is the sentence a reader will write down. Returns None
        with no samples — a percentile against nothing is not a small percentile.
        """
        if not self.charter_samples:
            return None
        rank = bisect.bisect_right(self.charter_samples, value)
        return round(100.0 * rank / len(self.charter_samples), 1)


@dataclasses.dataclass(frozen=True)
class Deep:
    """The server-only half. Every field is Optional because "not checked" must never render as
    zero — the distinction the whole repo is built on."""

    reached: bool
    detail: str
    server_bots: Optional[int] = None
    api_addressable: Optional[int] = None
    routines: Optional[int] = None
    routines_enabled: Optional[int] = None
    routines_with_a_run: Optional[int] = None
    memory_shards: Optional[int] = None
    memory_with_content: Optional[int] = None
    usage_percent: Optional[float] = None
    next_reset: Optional[str] = None
    cache_lag: Optional[int] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "reached": self.reached,
            "detail": self.detail,
            "server_bots": self.server_bots,
            "api_addressable": self.api_addressable,
            "routines": self.routines,
            "routines_enabled": self.routines_enabled,
            "routines_with_a_run": self.routines_with_a_run,
            "memory_shards": self.memory_shards,
            "memory_shards_with_content": self.memory_with_content,
            "usage_percent": self.usage_percent,
            "next_reset": self.next_reset,
            "roster_cache_lag_bots": self.cache_lag,
        }


@dataclasses.dataclass(frozen=True)
class Mirror:
    """Everything Tier A measured, and nothing it inferred."""

    support_dir: Optional[pathlib.Path]
    app_version: Optional[str]
    signed_in: Optional[bool]
    account_slot: Optional[str]
    roster_cached_at: Optional[str]
    roster_age_s: Optional[float]
    bots: Tuple[Bot, ...]
    replica_ids: Tuple[str, ...]
    section_ids: Tuple[str, ...]
    blobs_read: int
    blobs_skipped: int
    probes: Tuple[Probe, ...]
    controls_ok: bool
    controls_applied: Tuple[str, ...]
    now_ms: int

    @property
    def recoverable_ids(self) -> Tuple[str, ...]:
        """Bot uuids this machine knows about that the cached ROSTER does not list.

        The measurement that makes the cache-lag warning a number instead of a caveat.
        """
        known = {b.uuid for b in self.bots}
        extra = [i for i in self.replica_ids if i not in known]
        extra += [i for i in self.section_ids if i not in known and i not in extra]
        return tuple(extra)


# ---------------------------------------------------------------------------------------------
# Tier A readers. Pure functions of a directory, so a fixture is a real input and not a mock.
# ---------------------------------------------------------------------------------------------
def read_slices(support: pathlib.Path) -> Tuple[Dict[str, str], int]:
    """(decoded slice key -> blob text, blobs skipped).

    A blob over the cap is SKIPPED and counted, never truncated: a short read presented as a
    whole file is the bug `read_text_capped` raises to prevent, and swallowing that exception
    here would reintroduce it one layer up. The skip count is what lets `blind_spots` downgrade
    its own answer.
    """
    out: Dict[str, str] = {}
    skipped = 0
    d = support / PERSIST_DIR
    if not d.is_dir():
        return out, skipped
    for blob in sorted(d.iterdir()):
        if not blob.name.endswith(".blob") or not blob.is_file():
            continue
        key = unb32(blob.name)
        if not key:
            skipped += 1
            continue
        try:
            text = read_text_capped(blob, max_bytes=MAX_BLOB_BYTES)
        except ValueError:
            skipped += 1
            continue
        if text is None:
            skipped += 1
            continue
        out[key] = text
    return out, skipped


def _slice_value(slices: Dict[str, str], suffix: str) -> Optional[Any]:
    """The `value` of the one slice whose key ends with `suffix`, or None.

    The client wraps every slice as `{"schemaVersion": N, "value": …}`; unwrapping it here is
    what keeps the schema shape in one place instead of at four call sites.
    """
    for key, text in slices.items():
        if key.endswith(suffix):
            try:
                doc = json.loads(text)
            except ValueError:
                return None
            return doc.get("value") if isinstance(doc, dict) else None
    return None


def roster_rows(slices: Dict[str, str]) -> Optional[List[Dict[str, Any]]]:
    """The cached roster's rows, or None for "this machine has never cached a roster".

    None and `[]` are different answers and both happen: a client that has never synced has no
    roster slice at all, and a signed-in account with no Bots has a roster with zero rows. The
    first is an ENVIRONMENT answer; the second is a real, printable measurement.
    """
    value = _slice_value(slices, KEY_ROSTER)
    if not isinstance(value, dict):
        return None
    rows = value.get("rows")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def bots_from_rows(rows: Sequence[Dict[str, Any]]) -> Tuple[Bot, ...]:
    """Roster rows -> Bots. Groups are dropped: `isGroup` rows are a chat of several Bots, not a
    Bot, and counting them would inflate the first number on the board."""
    out: List[Bot] = []
    for r in rows:
        uuid = str(r.get("id") or "")
        if not uuid or r.get("isGroup"):
            continue
        charter = r.get("description") or ""
        out.append(
            Bot(
                uuid=uuid,
                name=str(r.get("name") or "(unnamed)"),
                charter_chars=len(charter),
                approval=bool(APPROVAL_RE.search(charter)),
                notifications=bool(r.get("notificationsEnabled")),
                notify_on_updates=bool(r.get("notifyOnUpdatesEnabled")),
                hidden=bool(r.get("isHiddenFromSidebar")),
                origin=r.get("origin"),
                harness=r.get("harness"),
                last_activity_ms=_ms(r.get("lastActivityAt"))
                or _ms(r.get("updatedAt")),
                created_ms=_ms(r.get("createdAt")),
            )
        )
    return tuple(out)


def _ms(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def replica_ids(slices: Dict[str, str]) -> Tuple[str, ...]:
    """Every Bot uuid this client holds a transcript replica for.

    The cache-lag detector. A replica exists because the operator OPENED that Bot, so a uuid here
    with no roster row is proof the roster is stale rather than proof the Bot is gone.
    """
    ids = []
    for key in sorted(slices):
        at = key.find(KEY_REPLICA)
        if at >= 0:
            tail = key[at + len(KEY_REPLICA) :]
            if tail:
                ids.append(tail)
    return tuple(ids)


def section_ids(slices: Dict[str, str]) -> Tuple[str, ...]:
    """Bot uuids the operator has filed into sidebar sections — a second independent id source."""
    value = _slice_value(slices, KEY_SECTIONS)
    if not isinstance(value, dict):
        return ()
    out: List[str] = []
    for sec in value.get("sections") or []:
        if isinstance(sec, dict):
            for i in sec.get("agentIds") or []:
                if isinstance(i, str) and i not in out:
                    out.append(i)
    return tuple(out)


def blind_spots(
    slices: Dict[str, str], skipped: int, bots_cached: int
) -> Tuple[Tuple[Probe, ...], bool, Tuple[str, ...]]:
    """(one probe per server-only fact, whether the controls held, what the controls were).

    THE DISCIPLINE, stated as code: an empty grep is only evidence of absence when the same grep
    finds something it MUST find. `ok` is False when an APPLICABLE control missed, or when any
    blob was skipped — an unread blob could be the one holding the marker, so a skip makes the
    whole answer unmeasured rather than negative.

    `bots_cached` decides which controls apply. With zero Bots the roster has no rows, so the
    row-level controls cannot fire and are reported as inapplicable rather than failed. The
    returned tuple of control descriptions is what lets the board say which question it actually
    asked, instead of asserting "3 positive controls held" whatever happened.
    """
    texts = list(slices.values())

    def hits(markers: Sequence[str]) -> int:
        return sum(1 for t in texts if any(m in t for m in markers))

    ok = skipped == 0 and bool(texts)
    applied: List[str] = []
    for control in STRUCTURAL_CONTROLS:
        found = hits((control,))
        applied.append(f"{control}={found}")
        if found == 0:
            ok = False
    for control in BOT_CONTROLS:
        if bots_cached == 0:
            applied.append(f"{control}=n/a (no Bot row to carry it)")
            continue
        found = hits((control,))
        applied.append(f"{control}={found}")
        if found == 0:
            ok = False
    probes = tuple(
        Probe(fact=fact, markers=markers, hits=hits(markers), via=via)
        for fact, markers, via in BLIND_SPOTS
    )
    return probes, ok, tuple(applied)


def app_facts(support: pathlib.Path) -> Tuple[Optional[str], Optional[bool]]:
    """(app version, signed-in) from the client's own status files.

    TWO FILES, TWO FIELDS, and they are not the same answer. `sand-session-marker.json` is
    rewritten while the app runs and carries `appVersion` but NOT `signedIn`;
    `desktop-status.json` is written at start and carries both. Measured 2026-09-11: taking both
    fields off the first file that had EITHER reported `signed_in: null` on a machine that was
    plainly signed in — an unmeasured value manufactured by a short-circuit. Each field is
    therefore resolved independently, version preferring the running file and sign-in taking the
    first file that states it at all.
    """
    version: Optional[str] = None
    signed_in: Optional[bool] = None
    for name in ("sand-session-marker.json", "desktop-status.json"):
        doc = _read_json(support / name)
        if not isinstance(doc, dict):
            continue
        if version is None and doc.get("appVersion"):
            version = str(doc["appVersion"])
        if signed_in is None and isinstance(doc.get("signedIn"), bool):
            signed_in = doc["signedIn"]
    return version, signed_in


def _read_json(path: pathlib.Path) -> Optional[Any]:
    try:
        text = read_text_capped(path, max_bytes=MAX_BLOB_BYTES)
    except ValueError:
        return None
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def roster_stamp(
    support: pathlib.Path, now_ms: int
) -> Tuple[Optional[str], Optional[float]]:
    """(when the roster blob was last written, how many seconds ago).

    From the FILESYSTEM, because the blob carries no timestamp of its own. This is the number that
    turns "the cache may be stale" into "the cache is 13 minutes old", which is the difference
    between a caveat a reader ignores and a fact a reader acts on.
    """
    d = support / PERSIST_DIR
    if not d.is_dir():
        return None, None
    for blob in sorted(d.iterdir()):
        if blob.name.endswith(".blob") and unb32(blob.name).endswith(KEY_ROSTER):
            try:
                mtime = blob.stat().st_mtime
            except OSError:
                return None, None
            when = dt.datetime.fromtimestamp(mtime, dt.timezone.utc)
            return when.strftime("%Y-%m-%dT%H:%M:%SZ"), max(
                0.0, now_ms / 1000.0 - mtime
            )
    return None, None


def account_slot(slices: Dict[str, str]) -> Optional[str]:
    """Which signed-in account this state belongs to. Printed because a mirror of the WRONG
    account is the one failure mode a reader cannot detect from the numbers."""
    value = _slice_value(slices, KEY_ACCOUNT)
    return value if isinstance(value, str) else None


def take_mirror(
    support: pathlib.Path, now_ms: Optional[int] = None
) -> Optional[Mirror]:
    """Read the whole local picture, or None when no roster has ever been cached here."""
    now = (
        now_ms
        if now_ms is not None
        else int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    )
    slices, skipped = read_slices(support)
    rows = roster_rows(slices)
    if rows is None:
        return None
    version, signed_in = app_facts(support)
    stamp, age = roster_stamp(support, now)
    bots = bots_from_rows(rows)
    probes, controls_ok, controls = blind_spots(slices, skipped, len(bots))
    return Mirror(
        support_dir=support,
        app_version=version,
        signed_in=signed_in,
        account_slot=account_slot(slices),
        roster_cached_at=stamp,
        roster_age_s=age,
        bots=bots,
        replica_ids=replica_ids(slices),
        section_ids=section_ids(slices),
        blobs_read=len(slices),
        blobs_skipped=skipped,
        probes=probes,
        controls_ok=controls_ok,
        controls_applied=controls,
        now_ms=now,
    )


# ---------------------------------------------------------------------------------------------
# The corpus this fleet is ranked against
# ---------------------------------------------------------------------------------------------
def corpus_baseline(root: pathlib.Path = ROOT) -> Baseline:
    """The newest `usecases/` snapshot, reduced to the four comparisons this file makes.

    EVERY NUMBER IS COMPUTED HERE, from `rows`, rather than read from the snapshot's own summary
    fields or pasted from a session note. The denominator matters and it is the thing most easily
    got wrong: the median charter is taken over the rows that ACTUALLY CARRY A CHARTER (487 of 645
    today, median 625), because including the 158 zeros drags it to 557 and that number describes
    the harvester's coverage, not anybody's charters.
    """
    rows = dated_children(root / "usecases", ".json")
    if not rows:
        return Baseline(
            False,
            None,
            None,
            0,
            (),
            None,
            None,
            None,
            None,
            None,
            0,
            "no usecases/ snapshot in this clone — run bin/gb-usecases.py to fetch the corpus",
        )
    newest = rows[-1]
    doc = _read_json(newest)
    corpus = (doc or {}).get("rows") if isinstance(doc, dict) else None
    if not isinstance(corpus, list) or not corpus:
        return Baseline(
            False,
            str(newest.relative_to(root)),
            None,
            0,
            (),
            None,
            None,
            None,
            None,
            None,
            0,
            f"{newest.name} carries no rows — the snapshot is unusable, not empty",
        )
    charters = sorted(
        int(r["prompt_chars"])
        for r in corpus
        if isinstance(r, dict)
        and isinstance(r.get("prompt_chars"), int)
        and r["prompt_chars"] > 0
    )
    with_text = [
        r
        for r in corpus
        if isinstance(r, dict)
        and isinstance(r.get("prompt_chars"), int)
        and r["prompt_chars"] > 0
    ]
    top, rest = _builder_split(with_text)
    return Baseline(
        available=True,
        source=str(newest.relative_to(root)),
        captured_at=(doc or {}).get("captured_at"),
        bots=len(corpus),
        charter_samples=tuple(charters),
        charter_median=statistics.median(charters) if charters else None,
        integrations_mean=round(
            sum(len(r.get("integrations") or []) for r in corpus if isinstance(r, dict))
            / len(corpus),
            2,
        ),
        approval_share=round(
            sum(
                1
                for r in corpus
                if isinstance(r, dict) and r.get("has_approval_language")
            )
            / len(corpus),
            3,
        ),
        top_builder_approval=_share(top),
        other_builder_approval=_share(rest),
        top_builder_bots=len(top),
        detail=f"{len(corpus)} attributed Bots, {len(charters)} carrying a charter",
    )


def _builder_split(
    with_text: Sequence[Dict[str, Any]], top_n: int = 10
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(rows by the `top_n` most prolific contributors, everyone else).

    Over the rows that CARRY A CHARTER, because that is the population whose charters are being
    compared — splitting the full 645 would put a builder's uncaptured rows on one side of a
    comparison about charter content. Ties at the boundary are resolved by name so the split is
    deterministic across runs and machines.
    """
    counts: Dict[str, int] = {}
    for r in with_text:
        who = r.get("contributor")
        if isinstance(who, str) and who:
            counts[who] = counts.get(who, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    names = {name for name, _ in ranked}
    return (
        [r for r in with_text if r.get("contributor") in names],
        [r for r in with_text if r.get("contributor") not in names],
    )


def _share(rows: Sequence[Dict[str, Any]]) -> Optional[float]:
    if not rows:
        return None
    return round(sum(1 for r in rows if r.get("has_approval_language")) / len(rows), 3)


def caveat(base: Baseline) -> Optional[str]:
    """The sentence that stops a percentile from being read as a safety score.

    Emitted only when the corpus actually SHOWS the inversion. If a future snapshot has the top
    builders using approval language MORE than everyone else, this returns the honest opposite
    rather than repeating today's finding — a caveat that cannot be falsified is advertising.
    """
    top, rest = base.top_builder_approval, base.other_builder_approval
    if top is None or rest is None:
        return None
    if top < rest:
        return (
            f"the {base.top_builder_bots} charters from the corpus's 10 most prolific builders "
            f"name an approval step {top:.0%} of the time against {rest:.0%} for everyone else — "
            f"the most productive builders are the LESS cautious half, so a percentile against "
            f"this cohort is a CONFORMITY score, never a safety score"
        )
    return (
        f"measured in {base.source}: the top-10 builders name an approval step {top:.0%} of the "
        f"time against {rest:.0%} for everyone else — the inversion this tool has warned about "
        f"since 2026-09-11 is NOT present in this snapshot; a percentile is still conformity, "
        f"not safety"
    )


# ---------------------------------------------------------------------------------------------
# The six numbers, and the one sentence
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Number:
    """One row of the board: what it is, the measurement, the comparison if there is one, and —
    since the history half exists — the machine-readable metric behind the sentence.

    `value` is prose for a human and is NOT diffable: "8 of 13 over 900 chars" changes when the
    fleet grows even though the count did not. `metric` is the one quantity the row is ABOUT,
    `whole` is the denominator when there is one, and `better` says which way is an improvement.
    Without `better` a diff can print `+3` but cannot say whether the operator should be pleased,
    and a table of signed deltas is not a finding.
    """

    key: str
    label: str
    value: str
    against: str = ""
    metric: Optional[float] = None
    whole: Optional[int] = None
    unit: str = ""
    better: str = BETTER_NEITHER

    def to_json(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "against": self.against,
            "metric": self.metric,
            "whole": self.whole,
            "unit": self.unit,
            "better": self.better,
        }


def numbers(m: Mirror, base: Baseline) -> Tuple[Number, ...]:
    """Six numbers. Always six, always in this order, always including the boring ones.

    A board that hides a row when its value is zero is a board a reader cannot use to conclude
    anything: "no Bot can reach you" and "that was not checked" would print identically.
    """
    n = len(m.bots)
    charters = sorted(b.charter_chars for b in m.bots if b.has_charter)
    med = statistics.median(charters) if charters else None
    pctile = base.percentile(med) if med is not None else None
    over = sum(1 for b in m.bots if b.over_cap)
    blank = sum(1 for b in m.bots if not b.has_charter)
    appr = sum(1 for b in m.bots if b.approval)
    reach = sum(1 for b in m.bots if b.notifications)
    idle = [b.idle_days(m.now_ms) for b in m.bots]
    dormant = sum(1 for d in idle if d is not None and d >= DORMANT_DAYS)
    undated = sum(1 for d in idle if d is None)
    extra = len(m.recoverable_ids)
    corpus_over_cap = (
        100.0
        * sum(1 for c in base.charter_samples if c > CHARTER_MAX)
        / len(base.charter_samples)
        if base.charter_samples
        else None
    )

    return (
        Number(
            "bots",
            "Bots",
            f"{n} in this machine's cache",
            # PRECISELY what was measured, and nothing more. A uuid the roster does not list is
            # EITHER a Bot this cache has not synced yet OR a Bot that was deleted and left its
            # transcript behind. Tier A cannot tell those apart, so it must not call the number
            # "more Bots" — it is a reason to run `--deep` before counting.
            f"+{extra} uuid(s) here that the roster does not list — unsynced Bots or deleted "
            f"leftovers; Tier A cannot tell which, `--deep` can"
            if extra
            else "no uuid outside the roster: this cache looks self-consistent",
            # NEITHER: a bigger fleet is not a better fleet and a smaller one is not a worse one.
            # Rows 3-6 are where the SHAPE of the fleet is judged.
            metric=float(n),
            unit="Bots",
            better=BETTER_NEITHER,
        ),
        Number(
            "charter_median",
            "Charter median",
            f"{int(med):,} chars" if med is not None else "no charter to measure",
            (
                f"corpus {int(base.charter_median):,} — {pctile:.0f}th percentile of "
                f"{len(base.charter_samples)} builders"
                if med is not None
                and base.available
                and base.charter_median
                and pctile is not None
                else base.detail
            ),
            # NEITHER, and this is the honest answer rather than a missing one: shorter is only
            # better while a charter is over the ceiling (row 3 measures exactly that), and a
            # 40-character charter is worse than a 700-character one, not better.
            metric=None if med is None else float(med),
            unit="chars",
            better=BETTER_NEITHER,
        ),
        Number(
            "unschedulable",
            "Unschedulable",
            f"{over} of {n} over {CHARTER_MAX} chars",
            # The corpus share is the comparison that makes the count mean something: "8 of 11"
            # is a number, "8 of 11 against 15% of the corpus" is a finding.
            (f"{blank} with no charter at all" if n else "nothing deployed to schedule")
            + (
                f"; {corpus_over_cap:.0f}% of the corpus is over the cap"
                if corpus_over_cap is not None
                else ""
            ),
            metric=float(over),
            whole=n,
            better=BETTER_DOWN,
        ),
        Number(
            "approval",
            "Approval language",
            f"{appr} of {n} charters ({_pc(appr, n)})",
            f"corpus {base.approval_share:.0%} — see CAVEAT"
            if base.approval_share is not None
            else base.detail,
            # NEITHER, on this file's own evidence: `caveat()` measures the ten most prolific
            # builders in the corpus using approval language LESS than everybody else, so a diff
            # that called +1 here "BETTER" would contradict the CAVEAT printed under the board.
            metric=float(appr),
            whole=n,
            better=BETTER_NEITHER,
        ),
        Number(
            "reachable",
            "Can interrupt you",
            f"{reach} of {n} have notifications on",
            f"{sum(1 for b in m.bots if b.notify_on_updates)} of {n} will notify on updates only",
            # UP: `verdict()` calls zero reachable Bots RED, because an approval boundary you
            # cannot be told about is decorative. More is better here and nowhere else.
            metric=float(reach),
            whole=n,
            better=BETTER_UP,
        ),
        Number(
            "dormant",
            f"Dormant {DORMANT_DAYS}d+",
            f"{dormant} of {n}",
            "no Bot to judge"
            if n == 0
            else (
                f"{undated} with no timestamp to judge"
                if undated
                else "every Bot carries a timestamp"
            ),
            metric=float(dormant),
            whole=n,
            better=BETTER_DOWN,
        ),
    )


def _pc(part: int, whole: int) -> str:
    return "n/a" if whole == 0 else f"{100.0 * part / whole:.0f}%"


def verdict(m: Mirror, base: Baseline) -> Tuple[str, str]:
    """(severity, ONE sentence).

    Ranked, and the ranking is the product. A reader who gets six numbers and six opinions has to
    do the triage himself; the whole promise of this verb is that the most consequential fact about
    his fleet is the sentence he reads first. Severity is the board's own vocabulary — RED is
    "measured, and the answer is bad" — so a tick can grep it.
    """
    n = len(m.bots)
    if n == 0:
        return (
            "GREEN",
            "This machine is signed in and has zero Bots cached: nothing is deployed yet, which "
            "is the cleanest possible starting point — `gb templates list` is the next command.",
        )
    blank = [b for b in m.bots if not b.has_charter]
    over = [b for b in m.bots if b.over_cap]
    dormant = [
        b
        for b in m.bots
        if (b.idle_days(m.now_ms) or 0.0) >= DORMANT_DAYS and b.last_activity_ms
    ]
    reach = sum(1 for b in m.bots if b.notifications)
    extra = len(m.recoverable_ids)

    if blank:
        return (
            "RED",
            f"{len(blank)} of your {n} Bots carry no charter at all "
            f"({', '.join(b.name for b in blank[:3])}{'…' if len(blank) > 3 else ''}): a Bot with "
            f"no description does whatever the last message said and nothing on a schedule, so "
            f"these are chat windows rather than deployed Bots.",
        )
    if len(over) * 2 > n:
        med = statistics.median([b.charter_chars for b in m.bots if b.has_charter])
        return (
            "RED",
            f"{len(over)} of your {n} charters are over {CHARTER_MAX} characters (median "
            f"{int(med):,}): that is a department constitution, not a job, and it cannot be pasted "
            f"into a routine prompt — which is the mechanical reason a fleet this size can still "
            f"have never run a scheduled job.",
        )
    if len(dormant) * 2 > n:
        return (
            "RED",
            f"{len(dormant)} of your {n} Bots have done nothing for {DORMANT_DAYS}+ days: they "
            f"are costing you attention in the sidebar and producing nothing, so either give "
            f"each one a routine or delete it.",
        )
    if reach == 0:
        return (
            "RED",
            f"none of your {n} Bots has notifications enabled: whatever they decide, you will only "
            f"find out by opening the app, which makes every approval boundary in those charters "
            f"decorative.",
        )
    if extra:
        return (
            "GREEN",
            f"Your {n} cached Bots look schedulable, and this machine also knows {extra} uuid(s) "
            f"the cached roster does not list — run `--deep` before concluding anything about how "
            f"many Bots you have.",
        )
    return (
        "GREEN",
        f"Your {n} Bots are charter-complete, inside the {CHARTER_MAX}-character ceiling that "
        f"keeps a charter schedulable, and at least one can reach you — the shape this repo is "
        f"trying to produce.",
    )


# ---------------------------------------------------------------------------------------------
# Tier B: the server-only half, over the puller's READ-ONLY transport
# ---------------------------------------------------------------------------------------------
def _load_puller() -> Any:
    """The puller module, loaded from its file because its name has a dash in it.

    `sys.modules[name] = mod` BEFORE `exec_module` is not optional on the 3.9 floor: a module that
    defines a `@dataclass` resolves its own annotations through `sys.modules` during execution and
    dies inside `dataclasses` without it. Registering unconditionally costs nothing and removes a
    failure that only appears when the imported file grows a dataclass.
    """
    import importlib.util

    path = pathlib.Path(__file__).resolve().parent / "gb-pull-inventory.py"
    spec = importlib.util.spec_from_file_location("gb_pull_inventory", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gb_pull_inventory"] = mod
    spec.loader.exec_module(mod)
    return mod


def deep_read(support: pathlib.Path, bots: Sequence[Bot]) -> Deep:
    """The routines, memory and allowance, read with the desktop client's own session.

    Reuses `gb-pull-inventory.py` wholesale — `access_token`, `rpc`, `durable_index`,
    `account_surface`, `usage_status`, `routine_rows`. That file refuses any method outside
    `List`/`Get` by contract, so this path cannot write even by mistake; reimplementing the
    transport here would have been a second place for that contract to be forgotten.

    Every failure answers `reached=False` with the reason. A deep read that could not happen must
    not leave zeros on the board.
    """
    try:
        mod = _load_puller()
    except Exception as exc:  # pragma: no cover - exercised only by a broken checkout
        return Deep(
            False, f"could not load gb-pull-inventory.py: {type(exc).__name__}: {exc}"
        )
    try:
        token = mod.access_token(support)
    except SystemExit as exc:
        return Deep(False, f"session unavailable: {exc}")
    except Exception as exc:
        return Deep(False, f"session unavailable: {type(exc).__name__}: {exc}")

    try:
        durable = mod.durable_index(token)
        account = mod.account_surface(token)
        usage = mod.usage_status(token)
        rows = mod.routine_rows(
            token, [{"id": b.uuid, "name": b.name} for b in bots], durable
        )
    except Exception as exc:
        return Deep(
            False, f"read failed after authenticating: {type(exc).__name__}: {exc}"
        )

    routines = [r for row in rows for r in (row.get("routines") or [])]
    shards = account.get("memory_shards")
    server = len(durable)
    return Deep(
        reached=True,
        detail=f"{len(rows)} Bots interrogated over read-only RPCs",
        server_bots=server,
        api_addressable=sum(1 for r in rows if r.get("api_addressable")),
        routines=len(routines),
        routines_enabled=sum(1 for r in routines if r.get("enabled")),
        routines_with_a_run=sum(1 for r in routines if r.get("recent_runs")),
        memory_shards=None if shards is None else len(shards),
        memory_with_content=None
        if shards is None
        else sum(1 for s in shards if s.get("has_content")),
        usage_percent=(usage or {}).get("usage_percent"),
        next_reset=(usage or {}).get("next_reset"),
        # The cache-lag number, from the two sides that disagree. Positive: the server knows more
        # Bots than this machine has cached. Reported even when zero, because "they agree right
        # now" is the fact that makes the caveat actionable rather than permanent.
        cache_lag=server - len(bots) if server else None,
    )


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------
def envelope(
    m: Mirror, base: Baseline, deep: Optional[Deep], plat: Platform
) -> Dict[str, Any]:
    sev, line = verdict(m, base)
    return {
        "schema": SCHEMA,
        "measured_at": dt.datetime.fromtimestamp(
            m.now_ms / 1000.0, dt.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tier": "B" if deep and deep.reached else "A",
        "platform": {
            "os": plat.os,
            "status": plat.status,
            "support_dir": str(m.support_dir) if m.support_dir else None,
        },
        "client": {
            "app_version": m.app_version,
            "signed_in": m.signed_in,
            "account_slot": m.account_slot,
            "roster_cached_at": m.roster_cached_at,
            "roster_age_seconds": None
            if m.roster_age_s is None
            else round(m.roster_age_s),
            "blobs_read": m.blobs_read,
            "blobs_skipped": m.blobs_skipped,
        },
        "verdict": {"severity": sev, "sentence": line},
        "numbers": [x.to_json() for x in numbers(m, base)],
        "baseline": {
            "available": base.available,
            "source": base.source,
            "captured_at": base.captured_at,
            "bots": base.bots,
            "charter_median": base.charter_median,
            "charters_measured": len(base.charter_samples),
            "integrations_mean": base.integrations_mean,
            "approval_share": base.approval_share,
            "top_builder_approval": base.top_builder_approval,
            "other_builder_approval": base.other_builder_approval,
            "detail": base.detail,
        },
        "caveat": caveat(base),
        "cache_warning": (
            "the roster blob is a PER-MACHINE CACHE: a Bot created elsewhere is absent from it "
            "until this desktop syncs, so absence here is never evidence a Bot does not exist"
        ),
        "tier_a_blind_spots": {
            "controls_held": m.controls_ok,
            "controls_applied": list(m.controls_applied),
            "probes": [p.to_json() for p in m.probes],
            "note": (
                "each fact is grepped for in every local blob; the counts are measured. "
                "controls_held=false means the probe itself is untrustworthy and every zero "
                "above is UNMEASURED rather than absent"
            ),
        },
        "recoverable_uuids_not_in_roster": list(m.recoverable_ids),
        "bots": [b.to_json(m.now_ms) for b in m.bots],
        "deep": deep.to_json() if deep else None,
    }


def render(m: Mirror, base: Baseline, deep: Optional[Deep], plat: Platform) -> str:
    sev, line = verdict(m, base)
    age = (
        f" · roster cached {_age(m.roster_age_s)} ago"
        if m.roster_age_s is not None
        else " · roster never timestamped"
    )
    out = [
        "gb-mirror — your Grok Bot deployment, read off THIS machine",
        f"  Grok Bot {m.app_version or '(version unread)'} · {plat.os} ({plat.status})"
        f"{age} · {m.blobs_read} local state blobs",
        "",
    ]
    for i, num in enumerate(numbers(m, base), 1):
        out.append(f"  {i}  {num.label:<20} {num.value}")
        if num.against:
            out.append(f"     {'':<20} {num.against}")
    out += ["", f"  VERDICT [{sev}]  {line}", ""]

    answered = " — Tier B below answered them" if deep and deep.reached else ""
    if m.controls_ok:
        out.append(
            "  TIER A CANNOT SEE (measured: each field a server record would carry, grepped "
            f"across all {m.blobs_read} blobs){answered}"
        )
        out.append(f"    positive controls held: {', '.join(m.controls_applied)}")
        for p in m.probes:
            mark = "absent" if p.absent else f"{p.hits} blob(s)"
            out.append(f"    {p.fact:<36}{mark:<13}served by {p.via}")
    else:
        out.append(
            "  TIER A BLIND SPOTS: UNMEASURED — an applicable positive control missed "
            f"({', '.join(m.controls_applied) or 'none ran'}) or a blob was skipped "
            f"({m.blobs_skipped}), so this run cannot tell absent from unread"
        )
    if not (deep and deep.reached):
        out.append(
            "    -> `gb-mirror.py --deep` reads these over the read-only RPC path."
        )

    if deep is not None:
        out += ["", "  TIER B (read-only RPCs)"]
        if not deep.reached:
            out.append(f"    UNREACHED — {deep.detail}")
        else:
            lag = deep.cache_lag
            ran = deep.routines_with_a_run
            out += [
                f"    server knows          {_opt(deep.server_bots)} Bots"
                + (
                    f" — {lag:+d} vs this machine's cache"
                    if lag is not None
                    else " (no durable record to compare)"
                ),
                f"    API-addressable       {_opt(deep.api_addressable)} of {len(m.bots)}",
                f"    routines              {_opt(deep.routines)} total · "
                f"{_opt(deep.routines_enabled)} enabled · "
                f"{_opt(ran)} ha{'s' if ran == 1 else 've'} ever produced a run",
                f"    memory shards         {_opt(deep.memory_with_content)} of "
                f"{_opt(deep.memory_shards)} carry any content",
                f"    weekly allowance      {_pct1(deep.usage_percent)} used"
                f" · resets {(deep.next_reset or '(unread)')[:19]}",
            ]

    cav = caveat(base)
    if cav:
        out += ["", f"  CAVEAT  {cav}"]
    out += [
        "",
        "  CACHE   the roster is a per-machine cache. A Bot created on another device or through",
        "          the API is missing here until this desktop syncs — absence is never proof of",
        f"          deletion. Here, right now: {len(m.bots)} row(s) cached against "
        f"{len(m.replica_ids)} transcript uuid(s) on disk.",
    ]
    return "\n".join(out)


def _opt(value: Any) -> str:
    return "not checked" if value is None else f"{value}"


def _pct1(value: Any) -> str:
    """A percentage to one decimal. The server answers 2.939325; printing that verbatim tells a
    reader the tool has no idea which digits it earned."""
    if value is None:
        return "not checked"
    if isinstance(value, (int, float)):
        return f"{value:.1f}%"
    return f"{value}"


def _age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds / 60)}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


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


# ---------------------------------------------------------------------------------------------
# History — save a reading, then diff a later reading against it
# ---------------------------------------------------------------------------------------------
# WHY THIS HALF EXISTS. Every run before this one was a fresh snapshot with nothing to compare
# against, so the one artifact an operator most wants — "I changed X, here is what moved" — was
# impossible from the verb that already holds every number it needs. `--save` persists the
# envelope `--json` already prints, byte for byte; `--since` reads one back and reports what
# MOVED, per numbered finding, with the direction that makes a delta mean something.
#
# WHAT THIS HALF REFUSES TO DO, and each refusal is a wrong answer that would otherwise print:
#   * diff a snapshot from another ACCOUNT — a delta between two fleets is arithmetic, not a
#     finding, and nothing in the numbers would reveal it
#   * diff a snapshot from another MACHINE's state directory — same objection
#   * diff a snapshot whose schema predates the `metric`/`better` fields, because judging
#     direction from the prose `value` is guesswork
#   * dress up "nothing moved" as a finding. It is a real answer and it is printed as one.
@dataclasses.dataclass(frozen=True)
class Refusal:
    """An exit code and the sentence the operator gets instead of a nonsense diff."""

    code: int
    text: str


def snapshot_body(doc: Dict[str, Any]) -> str:
    """The bytes a snapshot file holds: EXACTLY what `--json` prints, plus its newline.

    Identical on purpose. A saved reading that differed from the printed one — reordered keys, a
    different indent, a wrapper object announcing itself — would be a second shape to maintain and
    a second thing for a consumer to get wrong, and `--save` promises to persist what the verb
    already computes.
    """
    return json.dumps(doc, indent=1) + "\n"


_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})Z$")


def snapshot_name(measured_at: str) -> str:
    """`2026-09-12T03:36:00Z` -> `2026-09-12T033600.json`.

    SECONDS ARE IN THE NAME, unlike the `YYYY-MM-DDTHHMM` artifacts elsewhere in this repo, and
    the reason is the workflow this verb exists for: save, change one thing, save again. Two saves
    inside one minute is the NORMAL case for a before/after, and a minute-resolution name would
    have silently overwritten the "before" — destroying the exact artifact the operator was
    trying to mint. Still date-prefixed, so `gblib.dated_children` sorts and filters it.
    """
    mt = _STAMP_RE.match(measured_at or "")
    if mt is None:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%S") + ".json"
    return f"{mt.group(1)}T{mt.group(2)}{mt.group(3)}{mt.group(4)}.json"


def save_snapshot(
    doc: Dict[str, Any], directory: pathlib.Path = SNAP_DIR
) -> pathlib.Path:
    """Write this reading to `<directory>/<ISO>.json` atomically and return the path."""
    path = directory / snapshot_name(str(doc.get("measured_at") or ""))
    atomic_write_text(path, snapshot_body(doc))
    return path


def snapshots(directory: pathlib.Path = SNAP_DIR) -> List[pathlib.Path]:
    """Every saved reading, oldest first. `dated_children` is the shared rule for "this is an
    artifact of a tick" — a hand-dropped `notes.json` in here is not a snapshot."""
    return dated_children(directory, ".json")


def resolve_since(
    spec: str, directory: pathlib.Path = SNAP_DIR
) -> Tuple[Optional[pathlib.Path], Optional[Refusal]]:
    """(the snapshot to diff against, or the refusal that names what to do instead).

    "No snapshot yet" is ENVIRONMENT (3), not usage: the operator asked a well-formed question
    that this machine has no data to answer. A path that does not exist IS usage (2).
    """
    if spec == "latest":
        rows = snapshots(directory)
        if not rows:
            return None, Refusal(
                EXIT_ENVIRONMENT,
                f"gb-mirror: nothing saved yet, so there is no 'before' to compare against "
                f"({directory} holds no snapshot).\n"
                f"    That is not a failure — this fleet has simply never been recorded.\n"
                f"    fix: `gb mirror --save` now, change something, then re-run "
                f"`gb mirror --since latest`",
            )
        return rows[-1], None
    path = pathlib.Path(spec)
    if not path.is_file():
        return None, Refusal(
            EXIT_USAGE,
            f"gb-mirror: --since {spec}: no such snapshot file.\n"
            f"    fix: `--since latest`, or the path `gb mirror --save` printed",
        )
    return path, None


def load_snapshot(
    path: pathlib.Path,
) -> Tuple[Optional[Dict[str, Any]], Optional[Refusal]]:
    """Parse a saved reading, or refuse with the reason.

    The schema is checked TWICE and deliberately: once on the `schema` string, which is a CLAIM,
    and once on the rows, which are the EVIDENCE. A file that says `gb-mirror/2` while its
    `numbers[]` carry no `better` field would otherwise be diffed with no direction at all, and
    every move would print without a judgement — the exact defect the direction work exists to
    remove.
    """
    try:
        raw = read_text_capped(path, max_bytes=MAX_SNAPSHOT_BYTES)
    except ValueError:
        return None, Refusal(
            EXIT_USAGE,
            f"gb-mirror: {path} is larger than {MAX_SNAPSHOT_BYTES // (1024 * 1024)} MB, which "
            f"no reading this verb produces ever is — refusing to load it.",
        )
    if raw is None:
        return None, Refusal(EXIT_USAGE, f"gb-mirror: {path} could not be read.")
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return None, Refusal(
            EXIT_USAGE, f"gb-mirror: {path} is not JSON, so it is not a saved reading."
        )
    if not isinstance(doc, dict) or not str(doc.get("schema", "")).startswith(
        "gb-mirror/"
    ):
        return None, Refusal(
            EXIT_USAGE,
            f"gb-mirror: {path} is not a gb-mirror reading (schema "
            f"{doc.get('schema') if isinstance(doc, dict) else type(doc).__name__!r}).\n"
            f"    fix: point --since at a file written by `gb mirror --save`",
        )
    rows = doc.get("numbers")
    rows_ok = isinstance(rows, list) and all(
        isinstance(r, dict) and "better" in r and "metric" in r for r in rows
    )
    if doc.get("schema") != SCHEMA or not rows_ok:
        return None, Refusal(
            EXIT_USAGE,
            f"gb-mirror: {path} is schema {doc.get('schema')!r} and this build writes {SCHEMA!r} "
            f"— its findings carry no direction, so a diff could print a delta but not say "
            f"whether it is an improvement.\n"
            f"    fix: `gb mirror --save` to start a baseline this build can diff",
        )
    return doc, None


# THE DISCRIMINATOR, and it is read off what the payload ALREADY carries rather than a new field
# minted for the diff:
#
#   client.account_slot     `grok|user_01M0…` — the signed-in account. THE STRONG ONE: two
#                           different accounts are two different fleets, and no number on the
#                           board would reveal the swap.
#   platform.support_dir    the state directory this reading came out of. A WEAK machine
#                           discriminator and stated as such: two Macs with the same username
#                           produce the same path, so this catches a relocated or exported state
#                           directory, not every second machine.
#   platform.os             macos vs win32 — a different client build entirely.
#
# What no discriminator here can catch: two machines signed into the SAME account with the same
# username. That case is also the one where a diff is least wrong (same fleet, two caches), and
# the footer says both readings are of a per-machine cache anyway.
IDENTITY_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("account", "signed-in account"),
    ("os", "client platform"),
    ("state_dir", "state directory"),
)


def identity(doc: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """The three fields a diff must agree on, pulled from the envelope as it already stands."""
    client = doc.get("client") if isinstance(doc.get("client"), dict) else {}
    plat = doc.get("platform") if isinstance(doc.get("platform"), dict) else {}
    assert isinstance(client, dict) and isinstance(plat, dict)

    def _s(value: Any) -> Optional[str]:
        return value if isinstance(value, str) and value else None

    return {
        "account": _s(client.get("account_slot")),
        "os": _s(plat.get("os")),
        "state_dir": _s(plat.get("support_dir")),
    }


def identity_check(
    old: Dict[str, Any], new: Dict[str, Any], path: pathlib.Path
) -> Tuple[Optional[Refusal], Tuple[str, ...]]:
    """(refusal when the two readings are not of the same fleet, notes to print when they are).

    A MISSING value counts as a disagreement when the other side has one: "this snapshot does not
    say which account it came from" is not evidence that it came from yours. When BOTH sides are
    silent the diff proceeds and says so in a NOTE — refusing there would make the feature dead on
    any machine whose client does not cache the account slot, and an unverifiable diff that
    announces itself is more useful than no diff at all.
    """
    a, b = identity(old), identity(new)
    notes: List[str] = []
    for key, label in IDENTITY_FIELDS:
        if a[key] == b[key]:
            if a[key] is None:
                notes.append(
                    f"neither reading records its {label}, so this diff ASSUMES they are the "
                    f"same fleet — it could not verify it"
                )
            continue
        return (
            Refusal(
                EXIT_USAGE,
                f"gb-mirror: refusing to diff — {path} and this reading do not share the same "
                f"{label}.\n"
                f"    snapshot: {a[key] or '(not recorded)'}\n"
                f"    this run: {b[key] or '(not recorded)'}\n"
                f"    A delta between two different fleets is arithmetic, not a finding, and "
                f"nothing on the board would have revealed the swap.\n"
                f"    fix: `gb mirror --save` on this machine to start its own baseline",
            ),
            (),
        )
    return None, tuple(notes)


@dataclasses.dataclass(frozen=True)
class Move:
    """One numbered finding that reads differently than it did, and which way that is."""

    index: int
    key: str
    label: str
    old_value: str
    new_value: str
    old_metric: Optional[float]
    new_metric: Optional[float]
    old_whole: Optional[int]
    new_whole: Optional[int]
    unit: str
    better: str

    @property
    def delta(self) -> Optional[float]:
        if self.old_metric is None or self.new_metric is None:
            return None
        return self.new_metric - self.old_metric

    @property
    def judgement(self) -> str:
        """BETTER / WORSE / MOVED. `MOVED` is used whenever a direction cannot be EARNED: no
        metric on one side, a flat metric, or a row where neither direction is an improvement."""
        d = self.delta
        if d is None or d == 0:
            return "MOVED"
        if self.better == BETTER_UP:
            return "BETTER" if d > 0 else "WORSE"
        if self.better == BETTER_DOWN:
            return "BETTER" if d < 0 else "WORSE"
        return "MOVED"

    @property
    def detail(self) -> str:
        """The sentence under the row: the signed delta, the judgement, and WHY that is the
        judgement — because "WORSE" with no stated rule is an opinion."""
        d = self.delta
        if d is None:
            return (
                "no comparable number on both sides — the prose changed, the measurement was "
                "not taken twice"
            )
        sign = f"{d:+,.0f}" if float(d).is_integer() else f"{d:+,.1f}"
        unit = f" {self.unit}" if self.unit else ""
        rule = {
            BETTER_UP: "higher is better on this row",
            BETTER_DOWN: "lower is better on this row",
        }.get(self.better, "neither direction is better on this row")
        if d == 0 and self.old_whole != self.new_whole:
            return (
                f"count unchanged, fleet size moved "
                f"({self.old_whole} -> {self.new_whole}): the share changed, the number did not"
            )
        return f"{sign}{unit}  {self.judgement} — {rule}"

    def to_json(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "key": self.key,
            "label": self.label,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "old_metric": self.old_metric,
            "new_metric": self.new_metric,
            "old_whole": self.old_whole,
            "new_whole": self.new_whole,
            "unit": self.unit,
            "better": self.better,
            "delta": self.delta,
            "judgement": self.judgement,
            "detail": self.detail,
        }


@dataclasses.dataclass(frozen=True)
class VerdictMove:
    """The one ranked sentence, then and now."""

    old_severity: str
    new_severity: str
    old_sentence: str
    new_sentence: str

    @property
    def severity_moved(self) -> bool:
        return self.old_severity != self.new_severity

    @property
    def moved(self) -> bool:
        return self.severity_moved or self.old_sentence != self.new_sentence

    @property
    def judgement(self) -> str:
        """BETTER / WORSE when the severity is ranked, MOVED when only the sentence changed and
        UNCHANGED when nothing did. An unranked severity is not guessed at."""
        if not self.severity_moved:
            return "MOVED" if self.moved else "UNCHANGED"
        old = SEVERITY_RANK.get(self.old_severity)
        new = SEVERITY_RANK.get(self.new_severity)
        if old is None or new is None:
            return "MOVED"
        return "BETTER" if new < old else "WORSE"

    def to_json(self) -> Dict[str, Any]:
        return {
            "old_severity": self.old_severity,
            "new_severity": self.new_severity,
            "old_sentence": self.old_sentence,
            "new_sentence": self.new_sentence,
            "judgement": self.judgement,
            "moved": self.moved,
        }


@dataclasses.dataclass(frozen=True)
class Diff:
    """What moved between two readings of the same fleet."""

    snapshot: pathlib.Path
    old_measured_at: str
    new_measured_at: str
    elapsed_s: Optional[float]
    moves: Tuple[Move, ...]
    unchanged: Tuple[str, ...]
    added: Tuple[str, ...]
    dropped: Tuple[str, ...]
    verdict: VerdictMove
    notes: Tuple[str, ...]

    @property
    def nothing_moved(self) -> bool:
        return not (self.moves or self.added or self.dropped or self.verdict.moved)

    @property
    def worse(self) -> Tuple[Move, ...]:
        return tuple(m for m in self.moves if m.judgement == "WORSE")

    @property
    def better(self) -> Tuple[Move, ...]:
        return tuple(m for m in self.moves if m.judgement == "BETTER")


def _rows(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = doc.get("numbers")
    if not isinstance(rows, list):
        return {}
    return {
        str(r.get("key")): r
        for r in rows
        if isinstance(r, dict) and isinstance(r.get("key"), str)
    }


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def _int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) else None


def _stamp_s(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    try:
        return (
            dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=dt.timezone.utc)
            .timestamp()
        )
    except ValueError:
        return None


def _verdict_of(doc: Dict[str, Any]) -> Tuple[str, str]:
    v = doc.get("verdict")
    if not isinstance(v, dict):
        return "", ""
    return str(v.get("severity") or ""), str(v.get("sentence") or "")


def diff_readings(
    old: Dict[str, Any],
    new: Dict[str, Any],
    snapshot: pathlib.Path,
    notes: Sequence[str] = (),
) -> Diff:
    """Compare two readings, finding by finding, in the board's own order.

    A row counts as MOVED when its prose, its metric or its denominator changed — all three,
    because "3 of 13" becoming "3 of 14" is a real change that a metric-only comparison would
    call unchanged and a prose-only comparison could not direct.
    """
    before, after = _rows(old), _rows(new)
    moves: List[Move] = []
    unchanged: List[str] = []
    for i, (key, row) in enumerate(after.items(), 1):
        prev = before.get(key)
        if prev is None:
            continue
        old_v, new_v = str(prev.get("value") or ""), str(row.get("value") or "")
        om, nm = _num(prev.get("metric")), _num(row.get("metric"))
        ow, nw = _int(prev.get("whole")), _int(row.get("whole"))
        if old_v == new_v and om == nm and ow == nw:
            unchanged.append(str(row.get("label") or key))
            continue
        moves.append(
            Move(
                index=i,
                key=key,
                label=str(row.get("label") or key),
                old_value=old_v,
                new_value=new_v,
                old_metric=om,
                new_metric=nm,
                old_whole=ow,
                new_whole=nw,
                unit=str(row.get("unit") or ""),
                # The direction is read from the CURRENT reading, not the snapshot: this build
                # owns what "better" means, and a stale opinion baked into an old file must not
                # steer today's judgement.
                better=str(row.get("better") or BETTER_NEITHER),
            )
        )
    old_at, new_at = (
        str(old.get("measured_at") or ""),
        str(new.get("measured_at") or ""),
    )
    a, b = _stamp_s(old_at), _stamp_s(new_at)
    osev, osen = _verdict_of(old)
    nsev, nsen = _verdict_of(new)
    return Diff(
        snapshot=snapshot,
        old_measured_at=old_at,
        new_measured_at=new_at,
        elapsed_s=None if a is None or b is None else b - a,
        moves=tuple(moves),
        unchanged=tuple(unchanged),
        added=tuple(k for k in after if k not in before),
        dropped=tuple(k for k in before if k not in after),
        verdict=VerdictMove(osev, nsev, osen, nsen),
        notes=tuple(notes),
    )


def render_diff(d: Diff) -> str:
    total = len(d.moves) + len(d.unchanged)
    out = [
        "gb-mirror --since — what MOVED on your fleet",
        f"  before  {d.old_measured_at or '(undated)'}  ·  {d.snapshot}",
        f"  after   {d.new_measured_at or '(undated)'}"
        + (f"  ·  {_age(d.elapsed_s)} later" if d.elapsed_s is not None else ""),
        "",
    ]
    for note in d.notes:
        out.append(f"  NOTE    {note}")
    if d.notes:
        out.append("")

    if d.nothing_moved:
        out += [
            f"  NOTHING MOVED — all {total} findings read exactly as they did, and the verdict "
            f"is unchanged.",
            "  That is a real answer, not a failed measurement: this fleet is in the same shape.",
        ]
    else:
        for mv in d.moves:
            out.append(f"  {mv.index}  {mv.label:<20} {mv.old_value}")
            out.append(f"     {'':<20} ->  {mv.new_value}")
            out.append(f"     {'':<20} {mv.detail}")
        if d.moves:
            out.append("")
        better, worse = len(d.better), len(d.worse)
        out.append(
            f"  {len(d.moves)} of {total} findings moved — {better} better, {worse} worse, "
            f"{len(d.moves) - better - worse} with no better-or-worse direction"
        )
        if d.unchanged:
            out.append(f"  unchanged: {', '.join(d.unchanged)}")
        for key in d.added:
            out.append(
                f"  NEW FINDING  {key} — the snapshot has no such row to compare against"
            )
        for key in d.dropped:
            out.append(
                f"  GONE         {key} — the snapshot had this row and this build does not"
            )

    v = d.verdict
    out += [
        "",
        f"  VERDICT  {v.old_severity or '?'} -> {v.new_severity or '?'}  [{v.judgement}]",
    ]
    if v.moved:
        out += [f"    before  {v.old_sentence}", f"    after   {v.new_sentence}"]
    out += [
        "",
        "  CACHE   both readings are of THIS machine's cache, so a move here can be a sync that",
        "          finally landed rather than a change on the server. Anything surprising is a",
        "          reason to run `--deep` before concluding you changed it.",
    ]
    return "\n".join(out)


def diff_envelope(d: Diff) -> Dict[str, Any]:
    return {
        "schema": DIFF_SCHEMA,
        "snapshot": str(d.snapshot),
        "before": d.old_measured_at,
        "after": d.new_measured_at,
        "elapsed_seconds": None if d.elapsed_s is None else round(d.elapsed_s),
        "nothing_moved": d.nothing_moved,
        "moved": [m.to_json() for m in d.moves],
        "unchanged": list(d.unchanged),
        "added": list(d.added),
        "dropped": list(d.dropped),
        "counts": {
            "moved": len(d.moves),
            "better": len(d.better),
            "worse": len(d.worse),
            "undirected": len(d.moves) - len(d.better) - len(d.worse),
            "unchanged": len(d.unchanged),
        },
        "verdict": d.verdict.to_json(),
        "notes": list(d.notes),
        "cache_warning": (
            "both readings are of this machine's per-machine cache: a move can be a late sync "
            "rather than a change on the server"
        ),
    }


def diff_against(
    spec: str, doc: Dict[str, Any], directory: pathlib.Path = SNAP_DIR
) -> Tuple[Optional[Diff], Optional[Refusal]]:
    """The whole `--since` path as one function, so the selftest exercises exactly what `body`
    runs rather than a re-assembled imitation of it."""
    path, refusal = resolve_since(spec, directory)
    if refusal is not None or path is None:
        return None, refusal
    old, refusal = load_snapshot(path)
    if refusal is not None or old is None:
        return None, refusal
    refusal, notes = identity_check(old, doc, path)
    if refusal is not None:
        return None, refusal
    return diff_readings(old, doc, path, notes), None


# ---------------------------------------------------------------------------------------------
# Selftest — inline fixtures, and a fires-on-known-bad leg for every rule that matters
# ---------------------------------------------------------------------------------------------
def _blob_name(key: str) -> str:
    """The client's own filename for a slice key: base32, lowercased, padding stripped.

    The INVERSE of `gblib.unb32`. Writing fixtures through it is what makes the fixture a real
    input: a test that planted plain-named files would pass while the decoder was broken.
    """
    return base64.b32encode(key.encode()).decode().rstrip("=").lower() + ".blob"


def _plant(box: pathlib.Path, key: str, value: Any, schema: int = 1) -> None:
    d = box / PERSIST_DIR
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        d / _blob_name(key), json.dumps({"schemaVersion": schema, "value": value})
    )


ACCOUNT = "sand.client.slice.account.grok%7Cuser_TEST"


def _row(**kw: Any) -> Dict[str, Any]:
    """A roster row with every field the reader touches, so a fixture defect is the one under
    test rather than a missing key."""
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Fixture",
        "description": "You are a fixture. Ask me before you do anything consequential.",
        "createdAt": 1_000_000_000_000,
        "updatedAt": 1_700_000_000_000,
        "lastActivityAt": 1_700_000_000_000,
        "notificationsEnabled": True,
        "notifyOnUpdatesEnabled": True,
        "isHiddenFromSidebar": False,
        "isGroup": False,
        "origin": "user",
        "harness": "temporal",
    }
    row.update(kw)
    return row


def selftest() -> int:
    """Each reader and each verdict arm fires on an inline fixture built for it alone.

    Structure, because a selftest that only proves the happy path proves nothing: every leg that
    asserts a POSITIVE also has a known-bad sibling that must produce the opposite answer. The
    two that matter most are the base32 decoder (a fixture with real client filenames, so a
    broken decoder reads zero Bots instead of passing) and the positive-control gate (a state
    directory whose controls are missing must report UNMEASURED, never "absent").
    """
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    now = 1_700_100_000_000

    with tempfile.TemporaryDirectory() as td:
        top = pathlib.Path(td)

        # ---- 1. A three-Bot deployment with real client filenames -------------------------
        good = top / "good"
        _plant(
            good,
            ACCOUNT + ".roster.last-roster",
            {
                "rows": [
                    _row(id="aaa", name="Front Desk", description="a" * 400),
                    _row(
                        id="bbb",
                        name="Ops",
                        description="b" * 1200 + " approve",
                        notificationsEnabled=False,
                    ),
                    _row(id="ccc", name="Stale", lastActivityAt=1_600_000_000_000),
                    _row(id="grp", name="A group", isGroup=True),
                ]
            },
            schema=4,
        )
        _plant(good, ACCOUNT + ".transcript.replicas.aaa", {"entries": []})
        _plant(good, ACCOUNT + ".transcript.replicas.zzz", {"entries": []})
        _plant(
            good,
            ACCOUNT + ".sidebar.last-sections",
            {"sections": [{"id": "s1", "name": "All", "agentIds": ["aaa", "qqq"]}]},
        )
        _plant(good, "sand.client.slice.client-meta.account-slot", "grok|user_TEST")
        # THE REAL SHAPE, split the way the client splits it: the running marker carries the
        # version and no sign-in flag, and the start-up status file carries both. A fixture that
        # put `signedIn` in the marker would have passed while the production reader returned
        # null on a signed-in machine — which is exactly what it did before this leg existed.
        atomic_write_text(
            good / "sand-session-marker.json",
            json.dumps({"appVersion": "9.9.9", "pid": 1, "aliveAtMs": now}),
        )
        atomic_write_text(
            good / "desktop-status.json",
            json.dumps({"appVersion": "9.9.8", "signedIn": True}),
        )
        m = take_mirror(good, now_ms=now)

        leg(
            "base32-filenames-decode",
            m is not None and len(m.bots) == 3,
            f"expected 3 Bots from client-named blobs, got "
            f"{None if m is None else len(m.bots)}",
        )
        assert m is not None
        leg(
            "groups-are-not-bots",
            all(b.name != "A group" for b in m.bots),
            "an isGroup row must not be counted as a Bot",
        )
        leg(
            "app-version-prefers-running-marker",
            m.app_version == "9.9.9",
            f"got {m.app_version!r}",
        )
        leg(
            "signed-in-read-from-the-file-that-states-it",
            m.signed_in is True,
            "signedIn lives in desktop-status.json only; resolving it off the marker must not "
            f"manufacture a null: got {m.signed_in!r}",
        )
        leg(
            "account-slot-read",
            m.account_slot == "grok|user_TEST",
            f"got {m.account_slot!r}",
        )
        leg(
            "cache-lag-ids-recovered",
            set(m.recoverable_ids) == {"zzz", "qqq"},
            f"replica+sidebar uuids missing from the roster: got {m.recoverable_ids}",
        )
        leg(
            "charter-and-cap-measured",
            sorted(b.charter_chars for b in m.bots) == [63, 400, 1208]
            and sum(1 for b in m.bots if b.over_cap) == 1,
            f"got {sorted(b.charter_chars for b in m.bots)}",
        )
        leg(
            "approval-detected-per-bot",
            sum(1 for b in m.bots if b.approval) == 2,
            f"got {[(b.name, b.approval) for b in m.bots]}",
        )
        leg(
            "dormancy-uses-last-activity",
            sum(1 for b in m.bots if (b.idle_days(now) or 0) >= DORMANT_DAYS) == 1,
            f"got {[(b.name, b.idle_days(now)) for b in m.bots]}",
        )
        leg(
            "roster-stamp-measured",
            m.roster_age_s is not None,
            "no mtime read for the roster",
        )

        # ---- 2. KNOWN BAD: a decoder that does not decode ---------------------------------
        broken = top / "broken-names"
        (broken / PERSIST_DIR).mkdir(parents=True)
        atomic_write_text(
            broken / PERSIST_DIR / "not-base32-at-all.blob",
            json.dumps({"schemaVersion": 4, "value": {"rows": [_row()]}}),
        )
        bm = take_mirror(broken, now_ms=now)
        leg(
            "undecodable-blob-is-not-a-roster",
            bm is None,
            "a directory of unreadable names must answer 'no roster cached', not zero Bots",
        )

        # ---- 3. Absent vs empty -----------------------------------------------------------
        nothing = top / "never-ran"
        nothing.mkdir()
        leg(
            "no-support-state-answers-none",
            take_mirror(nothing, now_ms=now) is None,
            "an empty support dir must be None (ENVIRONMENT), never a zero-Bot fleet",
        )
        zero = top / "zero-bots"
        _plant(zero, ACCOUNT + ".roster.last-roster", {"rows": []}, schema=4)
        zm = take_mirror(zero, now_ms=now)
        leg(
            "zero-bots-is-a-measurement",
            zm is not None and len(zm.bots) == 0,
            "a synced account with no Bots must measure as zero, not as unmeasured",
        )

        # ---- 4. The positive-control gate: every way it can hold and every way it can fail --
        leg(
            "controls-hold-on-real-shape",
            m.controls_ok,
            "controls must hold on a real roster",
        )
        leg(
            "controls-are-reported-not-asserted",
            len(m.controls_applied) == len(STRUCTURAL_CONTROLS) + len(BOT_CONTROLS)
            and all("=" in c for c in m.controls_applied),
            f"the board must name what it checked: got {m.controls_applied}",
        )
        # THE FRESH-MACHINE CASE, which is the one a stranger sees first. Zero Bots means the
        # roster has no rows, so the row-level controls CANNOT fire. Counting that as a failed
        # control made a perfectly healthy read print "TIER A BLIND SPOTS: UNMEASURED" as the
        # first thing a new operator ever saw — measured against a planted state directory on
        # 2026-09-11, and this leg is why the controls are tiered.
        assert zm is not None
        leg(
            "zero-bots-skips-row-controls-instead-of-failing",
            zm.controls_ok is True
            and any("n/a" in c for c in zm.controls_applied)
            and all(p.absent for p in zm.probes),
            f"an empty fleet must still be MEASURED: got ok={zm.controls_ok} "
            f"{zm.controls_applied}",
        )
        # KNOWN BAD: the client renames its roster fields. Rows exist, so the row controls DO
        # apply, and they miss — which is exactly the drift that would otherwise make every
        # charter read as 0 characters and every notification read as off, silently.
        drift = top / "schema-drift"
        _plant(
            drift,
            ACCOUNT + ".roster.last-roster",
            {"rows": [{"id": "a", "name": "Renamed", "charter": "moved field"}]},
            schema=5,
        )
        dm = take_mirror(drift, now_ms=now)
        leg(
            "roster-schema-drift-forces-unmeasured",
            dm is not None and dm.controls_ok is False and len(dm.bots) == 1,
            "a roster whose row fields have been renamed must report UNMEASURED, not a fleet of "
            f"0-character charters: got {None if dm is None else dm.controls_ok}",
        )
        # KNOWN BAD: a blob this process could not read at all. The marker it does not contain
        # might be in the file that was skipped, so absence is not knowable.
        unread = top / "unreadable"
        _plant(unread, ACCOUNT + ".roster.last-roster", {"rows": [_row()]}, schema=4)
        atomic_write_text(unread / PERSIST_DIR / "this-is-not-base32.blob", "{}")
        um = take_mirror(unread, now_ms=now)
        leg(
            "skipped-blob-forces-unmeasured",
            um is not None and um.blobs_skipped == 1 and um.controls_ok is False,
            f"an unread blob must downgrade the answer: got "
            f"{None if um is None else (um.blobs_skipped, um.controls_ok)}",
        )
        skipped = top / "skipped"
        _plant(skipped, ACCOUNT + ".roster.last-roster", {"rows": [_row()]}, schema=4)
        atomic_write_text(
            skipped / PERSIST_DIR / _blob_name(ACCOUNT + ".transcript.replicas.big"),
            "x" * 64,
        )
        sm = take_mirror(skipped, now_ms=now)
        leg(
            "probes-report-server-facts-absent",
            sm is not None
            and all(p.absent for p in sm.probes)
            and sm.controls_ok is True,
            "a real roster carries none of the server-only markers, and controls must still hold",
        )

        # ---- 5. The verdict ranks, and each arm is reachable -----------------------------
        def sentence(rows: List[Dict[str, Any]], base: Baseline) -> Tuple[str, str]:
            box = top / f"v{len(legs)}"
            _plant(box, ACCOUNT + ".roster.last-roster", {"rows": rows}, schema=4)
            mm = take_mirror(box, now_ms=now)
            assert mm is not None
            return verdict(mm, base)

        empty_base = Baseline(
            False, None, None, 0, (), None, None, None, None, None, 0, "no corpus"
        )
        blank_first = sentence(
            [_row(id="a", description=""), _row(id="b", description="x" * 2000)],
            empty_base,
        )
        leg(
            "verdict-blank-charter-wins",
            blank_first[1].startswith("1 of your 2 Bots carry no charter"),
            f"an undirected Bot must outrank an over-cap one: got {blank_first[1][:80]}",
        )
        # MAJORITY over the cap, because that is the rule: one fat charter in a healthy fleet is
        # not the headline, and a fixture with 1-of-2 would have asserted a threshold this
        # verdict does not implement.
        cap_first = sentence(
            [
                _row(id="a", description="x" * 1500),
                _row(id="b", description="y" * 1500),
                _row(id="c", description="z" * 40),
            ],
            empty_base,
        )
        leg(
            "verdict-over-cap-fires",
            "over 900 characters" in cap_first[1] and cap_first[0] == "RED",
            f"got {cap_first}",
        )
        minority_cap = sentence(
            [_row(id="a", description="x" * 1500), _row(id="b", description="y" * 40)],
            empty_base,
        )
        leg(
            "verdict-minority-over-cap-is-not-a-headline",
            "over 900 characters" not in minority_cap[1],
            f"one fat charter in a two-Bot fleet must not read as RED: got {minority_cap}",
        )
        leg(
            "verdict-unreachable-fires",
            "notifications enabled"
            in sentence([_row(id="a", notificationsEnabled=False)], empty_base)[1],
            "a fleet no Bot can interrupt must be the headline when nothing worse is true",
        )
        leg(
            "verdict-zero-bots-is-green",
            sentence([], empty_base)[0] == "GREEN",
            "an empty deployment is a clean start, not a failure",
        )
        leg(
            "verdict-healthy-is-green",
            sentence([_row(id="a"), _row(id="b")], empty_base)[0] == "GREEN",
            f"got {sentence([_row(id='a')], empty_base)}",
        )
        leg(
            "verdict-dormant-fires",
            f"{DORMANT_DAYS}+ days"
            in sentence(
                [
                    _row(id="a", lastActivityAt=1_600_000_000_000),
                    _row(id="b", lastActivityAt=1_600_000_000_000),
                    _row(id="c"),
                ],
                empty_base,
            )[1],
            "a mostly-idle fleet must say so",
        )

        # ---- 6. The baseline arithmetic, on a corpus whose answer is known by hand -------
        fake = top / "fakeroot"
        (fake / "usecases").mkdir(parents=True)
        atomic_write_text(
            fake / "usecases" / "2026-01-01T0000.json",
            json.dumps(
                {
                    "captured_at": "2026-01-01T00:00:00+00:00",
                    "rows": [
                        {
                            "prompt_chars": 100,
                            "integrations": ["Gmail"],
                            "contributor": "prolific",
                            "has_approval_language": False,
                        },
                        {
                            "prompt_chars": 300,
                            "integrations": ["Gmail", "Slack"],
                            "contributor": "prolific",
                            "has_approval_language": False,
                        },
                        {
                            "prompt_chars": 900,
                            "integrations": [],
                            "contributor": "careful",
                            "has_approval_language": True,
                        },
                        {
                            "prompt_chars": 0,
                            "integrations": ["Grok"],
                            "contributor": "careful",
                            "has_approval_language": True,
                        },
                    ],
                }
            ),
        )
        base = corpus_baseline(fake)
        leg(
            "baseline-median-excludes-uncaptured",
            base.charter_median == 300 and len(base.charter_samples) == 3,
            f"median over charters that exist: got {base.charter_median} "
            f"over {len(base.charter_samples)}",
        )
        leg(
            "baseline-integrations-over-all-rows",
            base.integrations_mean == 1.0,
            f"got {base.integrations_mean}",
        )
        leg(
            "baseline-approval-over-all-rows",
            base.approval_share == 0.5,
            f"got {base.approval_share}",
        )
        leg(
            "baseline-percentile-is-ecdf",
            base.percentile(100) == 33.3
            and base.percentile(900) == 100.0
            and base.percentile(50) == 0.0,
            f"got {base.percentile(100)}/{base.percentile(900)}/{base.percentile(50)}",
        )
        leg(
            "baseline-absent-is-not-zero",
            corpus_baseline(top / "no-such-root").available is False
            and corpus_baseline(top / "no-such-root").charter_median is None,
            "a clone with no corpus must answer unavailable, never a median of 0",
        )
        # THE COHORT SPLIT needs more than ten contributors to exist at all. The four-row corpus
        # above has two, so both land in the top ten and "everyone else" is EMPTY — which is why
        # `caveat` returns None there, and why asserting the inversion on that fixture was
        # asserting against a population that does not exist. This one has twelve: ten prolific
        # builders with two charters each (2 of 20 cautious = 10%) and two occasional builders
        # with one each (2 of 2 cautious = 100%), so the measured inversion is known by hand.
        cohort = top / "cohortroot"
        (cohort / "usecases").mkdir(parents=True)
        prolific = [
            {
                "prompt_chars": 500,
                "integrations": [],
                "contributor": f"c{i:02d}",
                "has_approval_language": i < 2,
            }
            for i in range(10)
            for _ in range(2)
        ]
        occasional = [
            {
                "prompt_chars": 800,
                "integrations": [],
                "contributor": who,
                "has_approval_language": True,
            }
            for who in ("d1", "d2")
        ]
        atomic_write_text(
            cohort / "usecases" / "2026-01-02T0000.json",
            json.dumps({"captured_at": "x", "rows": prolific + occasional}),
        )
        split = corpus_baseline(cohort)
        leg(
            "baseline-cohort-split-is-measured",
            split.top_builder_bots == 20
            and split.top_builder_approval == 0.2
            and split.other_builder_approval == 1.0,
            f"got n={split.top_builder_bots} top={split.top_builder_approval} "
            f"rest={split.other_builder_approval}",
        )
        leg(
            "caveat-follows-the-measurement",
            "CONFORMITY" in (caveat(split) or ""),
            f"top={split.top_builder_approval} rest={split.other_builder_approval}: "
            f"got {caveat(split)!r}",
        )
        leg(
            "caveat-empty-cohort-is-silent",
            caveat(base) is None,
            "a corpus with fewer than eleven contributors has no 'everyone else' to compare "
            f"against, so it must make no cohort claim: got {caveat(base)!r}",
        )
        flipped = dataclasses.replace(
            split, top_builder_approval=0.9, other_builder_approval=0.1
        )
        leg(
            "caveat-refuses-to-repeat-a-stale-finding",
            "NOT present in this snapshot" in (caveat(flipped) or ""),
            "a corpus without the inversion must not be told it has one",
        )
        leg(
            "caveat-silent-without-a-corpus",
            caveat(empty_base) is None,
            "no corpus means no cohort claim at all",
        )

        # ---- 7. The board always has six rows, and renders on every shape ---------------
        leg(
            "board-is-six-rows",
            len(numbers(m, base)) == 6,
            f"got {len(numbers(m, base))}",
        )
        leg(
            "board-renders-with-zero-bots",
            bool(render(zm, base, None, platform_support("darwin"))) if zm else False,
            "a zero-Bot fleet must still print a board",
        )
        leg(
            "board-renders-without-a-corpus",
            "no usecases/"
            in render(
                m, corpus_baseline(top / "nope"), None, platform_support("darwin")
            ),
            "a clone with no corpus must say so on the board, not print a bare number",
        )

        # ---- 8. Cross-platform honesty, both branches -----------------------------------
        win = platform_support("win32", {"APPDATA": "C:/Users/x/AppData/Roaming"})
        leg(
            "windows-is-unverified-not-broken",
            win.status == "UNVERIFIED" and win.support_dir is not None,
            f"got {win.status} / {win.support_dir}",
        )
        ios = platform_support("ios", {})
        leg(
            "ios-is-unsupported-with-a-reason",
            ios.status == "UNSUPPORTED" and ios.support_dir is None and bool(ios.why),
            f"got {ios.status} / {ios.support_dir}",
        )
        leg(
            "unsupported-platform-refuses-before-reading",
            plat_gate(ios) is not None
            and plat_gate(platform_support("darwin")) is None,
            "a platform with no client must be refused with text, not a traceback",
        )

        # ---- 9. The approval detector cannot silently diverge from the corpus producer --
        src = read_text_capped(USECASES_PRODUCER) or ""
        leg(
            "approval-detector-matches-gb-usecases",
            APPROVAL_RE.pattern in src,
            f"{USECASES_PRODUCER.name} no longer contains {APPROVAL_RE.pattern!r} — the corpus "
            f"share and our share would be measured with different detectors",
        )

        # ---- 10. Positive control on the REAL repo corpus -------------------------------
        real = corpus_baseline(ROOT)
        leg(
            "repo-corpus-reads",
            real.available
            and real.charter_median is not None
            and len(real.charter_samples) > 100,
            f"got available={real.available} median={real.charter_median} "
            f"n={len(real.charter_samples)}",
        )

        # ---- 11. HISTORY: save, then diff — and the direction that makes a delta a finding --
        #
        # The fixture is a real STATE DIRECTORY read twice, not two hand-written envelopes: the
        # operator's actual workflow is "save, change the fleet, run again", and a fixture that
        # forged the second envelope would pass while `numbers()` stopped carrying a metric.
        # The one fleet change is chosen so the SAME SIGN lands on both sides of the judgement:
        # unschedulable -1 (better) and reachable -2 (worse). A diff that printed raw deltas
        # would label those two identically, which is the defect these legs exist to catch.
        state = top / "history-state"
        _plant(state, "sand.client.slice.client-meta.account-slot", "grok|user_TEST")
        atomic_write_text(
            state / "desktop-status.json",
            json.dumps({"appVersion": "9.9.8", "signedIn": True}),
        )

        def _fleet(rows: List[Dict[str, Any]]) -> None:
            _plant(state, ACCOUNT + ".roster.last-roster", {"rows": rows}, schema=4)

        plat_d = platform_support("darwin")
        _fleet(
            [
                _row(id="aaa", name="A", description="a" * 400),
                _row(id="bbb", name="B", description="b" * 1200),
                _row(
                    id="ccc",
                    name="C",
                    description="c" * 300,
                    notificationsEnabled=False,
                ),
            ]
        )
        m_before = take_mirror(state, now_ms=now)
        assert m_before is not None
        before_doc = envelope(m_before, base, None, plat_d)

        hist = top / "mirror"
        snap = save_snapshot(before_doc, hist)
        raw = read_text_capped(snap) or ""
        leg(
            "save-persists-the-json-payload-byte-for-byte",
            raw == json.dumps(before_doc, indent=1) + "\n"
            and json.loads(raw) == before_doc,
            "a saved snapshot must be exactly what --json prints, not a second shape",
        )
        leg(
            "save-is-named-for-the-readings-own-stamp",
            snap.name == "2023-11-16T020000.json",
            f"got {snap.name} for measured_at {before_doc['measured_at']}",
        )
        # KNOWN BAD this guards: a minute-resolution name. Save, change one thing, save again is
        # the whole workflow, and a clobbered "before" destroys the artifact being minted.
        same_minute = envelope(
            dataclasses.replace(m_before, now_ms=now + 1000), base, None, plat_d
        )
        snap2 = save_snapshot(same_minute, hist)
        leg(
            "two-saves-in-one-minute-do-not-clobber",
            snap2 != snap and snap.is_file() and len(snapshots(hist)) == 2,
            f"{snap.name} vs {snap2.name}: {[p.name for p in snapshots(hist)]}",
        )
        leg(
            "since-latest-takes-the-newest-snapshot",
            resolve_since("latest", hist)[0] == snap2,
            f"got {resolve_since('latest', hist)[0]}",
        )
        _, no_snaps = diff_against("latest", before_doc, top / "no-mirror-dir")
        leg(
            "no-snapshot-yet-refuses-as-environment-and-names-save",
            no_snaps is not None
            and no_snaps.code == EXIT_ENVIRONMENT
            and "--save" in no_snaps.text,
            f"got {no_snaps}",
        )

        unchanged_diff, err = diff_against("latest", same_minute, hist)
        leg(
            "unchanged-fleet-says-nothing-moved",
            err is None
            and unchanged_diff is not None
            and unchanged_diff.nothing_moved
            and "NOTHING MOVED" in render_diff(unchanged_diff)
            and "WORSE" not in render_diff(unchanged_diff),
            f"err={err} diff={None if unchanged_diff is None else unchanged_diff.moves}",
        )

        # The fleet changes: the long charter is trimmed under the ceiling (better), every Bot
        # loses notifications (worse), one goes dormant (worse), the median moves (neither).
        later = now + 86_400_000
        _fleet(
            [
                _row(
                    id="aaa",
                    name="A",
                    description="a" * 400,
                    notificationsEnabled=False,
                ),
                _row(
                    id="bbb",
                    name="B",
                    description="b" * 500,
                    notificationsEnabled=False,
                ),
                _row(
                    id="ccc",
                    name="C",
                    description="c" * 900,
                    notificationsEnabled=False,
                    lastActivityAt=later - 10 * 86_400_000,
                ),
            ]
        )
        m_after = take_mirror(state, now_ms=later)
        assert m_after is not None
        after_doc = envelope(m_after, base, None, plat_d)
        moved, err = diff_against(str(snap), after_doc, hist)
        assert moved is not None, f"the moved diff must compute: {err}"
        judged = {mv.key: mv.judgement for mv in moved.moves}
        text = render_diff(moved)

        leg(
            "worsening-is-labelled-worse",
            judged.get("dormant") == "WORSE"
            and judged.get("reachable") == "WORSE"
            and "WORSE" in text,
            f"got {judged}",
        )
        leg(
            "improvement-is-labelled-better",
            judged.get("unschedulable") == "BETTER" and "BETTER" in text,
            f"got {judged}",
        )
        leg(
            "direction-is-per-finding-not-a-raw-delta",
            # -1 on row 3 and -2 on row 5: same sign, opposite verdicts. A signed-delta table
            # cannot tell these apart, and this is the leg that says so.
            judged.get("unschedulable") == "BETTER"
            and judged.get("reachable") == "WORSE"
            and all(
                (mv.delta or 0) < 0
                for mv in moved.moves
                if mv.key in ("unschedulable", "reachable")
            ),
            f"got {[(mv.key, mv.delta, mv.judgement) for mv in moved.moves]}",
        )
        leg(
            "undirected-finding-is-moved-not-judged",
            judged.get("charter_median") == "MOVED"
            and "neither direction is better"
            in next(mv.detail for mv in moved.moves if mv.key == "charter_median"),
            f"got {judged}: charter length has no better direction, and saying one would be "
            f"a claim this file has not earned",
        )
        leg(
            "unchanged-rows-are-listed-not-dropped",
            set(moved.unchanged) == {"Bots", "Approval language"}
            and len(moved.moves) + len(moved.unchanged) == 6,
            f"moved={[mv.key for mv in moved.moves]} unchanged={moved.unchanged}",
        )
        leg(
            "verdict-severity-worsening-is-labelled",
            moved.verdict.old_severity == "GREEN"
            and moved.verdict.new_severity == "RED"
            and moved.verdict.judgement == "WORSE",
            f"got {moved.verdict.to_json()}",
        )
        env = diff_envelope(moved)
        leg(
            "diff-envelope-counts-match-the-board",
            env["counts"]
            == {
                "moved": 4,
                "better": 1,
                "worse": 2,
                "undirected": 1,
                "unchanged": 2,
            }
            and env["schema"] == DIFF_SCHEMA
            and env["nothing_moved"] is False,
            f"got {env['counts']}",
        )
        leg(
            "elapsed-is-measured-between-the-two-readings",
            # `_age` is the board's own formatter, reused rather than re-chosen: a day reads as
            # "24.0h" there and must read the same here.
            moved.elapsed_s == 86_400.0 and "24.0h later" in text,
            f"got {moved.elapsed_s} / {text.splitlines()[2] if text else ''}",
        )

        # ---- 12. Every row carries a direction, and the encoder is control-tested --------
        rows = numbers(m_after, base)
        leg(
            "every-finding-encodes-a-direction",
            all(r.better in (BETTER_UP, BETTER_DOWN, BETTER_NEITHER) for r in rows)
            and {r.key for r in rows if r.better == BETTER_DOWN}
            == {"unschedulable", "dormant"}
            and {r.key for r in rows if r.better == BETTER_UP} == {"reachable"},
            f"got {[(r.key, r.better) for r in rows]}",
        )
        leg(
            "every-count-row-carries-a-metric",
            all(r.metric is not None for r in rows),
            f"a row with no metric is a row a diff cannot direct: "
            f"{[(r.key, r.metric) for r in rows]}",
        )

        # ---- 13. The refusals, each on a fixture built to trip exactly one of them -------
        foreign = top / "foreign"
        fa: Dict[str, Any] = json.loads(json.dumps(before_doc))
        fa["client"]["account_slot"] = "grok|user_SOMEBODY_ELSE"
        save_snapshot(fa, foreign)
        _, ref_acct = diff_against("latest", after_doc, foreign)
        leg(
            "foreign-account-snapshot-is-refused",
            ref_acct is not None
            and ref_acct.code == EXIT_USAGE
            and "signed-in account" in ref_acct.text
            and "user_SOMEBODY_ELSE" in ref_acct.text,
            f"got {ref_acct}",
        )
        machine = top / "other-machine"
        fm: Dict[str, Any] = json.loads(json.dumps(before_doc))
        fm["platform"]["support_dir"] = (
            "/Us" "ers/someone-else/Library/Application Support/x"
        )
        save_snapshot(fm, machine)
        _, ref_mach = diff_against("latest", after_doc, machine)
        leg(
            "foreign-machine-snapshot-is-refused",
            ref_mach is not None
            and ref_mach.code == EXIT_USAGE
            and "state directory" in ref_mach.text,
            f"got {ref_mach}",
        )
        # BOTH sides silent about the account: diff, but SAY it is unverified. Refusing here
        # would make the whole feature dead on a client that does not cache the account slot.
        anon_dir = top / "anon"
        anon_before: Dict[str, Any] = json.loads(json.dumps(before_doc))
        anon_after: Dict[str, Any] = json.loads(json.dumps(after_doc))
        anon_before["client"]["account_slot"] = None
        anon_after["client"]["account_slot"] = None
        save_snapshot(anon_before, anon_dir)
        anon_diff, ref_anon = diff_against("latest", anon_after, anon_dir)
        leg(
            "unrecorded-account-diffs-but-declares-it-unverified",
            ref_anon is None
            and anon_diff is not None
            and any("could not verify" in n for n in anon_diff.notes)
            and "NOTE" in render_diff(anon_diff),
            f"ref={ref_anon} notes={None if anon_diff is None else anon_diff.notes}",
        )
        old_schema = top / "schema-1"
        s1: Dict[str, Any] = json.loads(json.dumps(before_doc))
        s1["schema"] = "gb-mirror/1"
        for r in s1["numbers"]:
            r.pop("better", None)
            r.pop("metric", None)
        save_snapshot(s1, old_schema)
        _, ref_old = diff_against("latest", after_doc, old_schema)
        leg(
            "snapshot-predating-the-direction-fields-is-refused-by-name",
            ref_old is not None
            and ref_old.code == EXIT_USAGE
            and "gb-mirror/1" in ref_old.text
            and "--save" in ref_old.text,
            f"got {ref_old}",
        )
        # The schema string is a CLAIM; the rows are the EVIDENCE. A forged header must not buy
        # a diff with no direction in it.
        liar = top / "liar"
        lie: Dict[str, Any] = json.loads(json.dumps(before_doc))
        for r in lie["numbers"]:
            r.pop("better", None)
        save_snapshot(lie, liar)
        _, ref_lie = diff_against("latest", after_doc, liar)
        leg(
            "current-schema-string-without-direction-fields-is-still-refused",
            ref_lie is not None and ref_lie.code == EXIT_USAGE,
            "a file claiming the current schema while carrying no `better` field must not be "
            f"diffed: got {ref_lie}",
        )
        junk = top / "junk"
        junk.mkdir()
        atomic_write_text(junk / "2026-01-01T000000.json", json.dumps({"hello": 1}))
        _, ref_junk = diff_against("latest", after_doc, junk)
        leg(
            "a-file-that-is-not-a-mirror-reading-is-refused",
            ref_junk is not None and "not a gb-mirror reading" in ref_junk.text,
            f"got {ref_junk}",
        )
        atomic_write_text(junk / "2026-01-02T000000.json", "{not json at all")
        _, ref_bad = diff_against("latest", after_doc, junk)
        leg(
            "unparseable-snapshot-is-refused-not-crashed",
            ref_bad is not None and "not JSON" in ref_bad.text,
            f"got {ref_bad}",
        )
        _, ref_missing = diff_against(str(top / "nope.json"), after_doc, hist)
        leg(
            "missing-since-path-is-refused-by-resolve-not-by-the-loader",
            # The MESSAGE is asserted, not just the code: without it, a build that let a missing
            # path fall through to `load_snapshot` still produced EXIT_USAGE and this leg passed
            # for the wrong reason (measured — the mutation was silent until this line existed).
            ref_missing is not None
            and ref_missing.code == EXIT_USAGE
            and "no such snapshot file" in ref_missing.text,
            f"got {ref_missing}",
        )

        # ---- 14. A count that did not move while the fleet size did ---------------------
        grew_dir = top / "grew"
        save_snapshot(before_doc, grew_dir)
        grew: Dict[str, Any] = json.loads(json.dumps(before_doc))
        for r in grew["numbers"]:
            if r["key"] == "approval":
                r["whole"] = 4
                r["value"] = "0 of 4 charters (0%)"
        grew_diff, _ = diff_against("latest", grew, grew_dir)
        assert grew_diff is not None
        leg(
            "flat-count-against-a-changed-fleet-size-is-said-plainly",
            any(
                mv.key == "approval"
                and mv.judgement == "MOVED"
                and "fleet size moved" in mv.detail
                for mv in grew_diff.moves
            ),
            f"got {[(mv.key, mv.detail) for mv in grew_diff.moves]}",
        )

    passed = sum(1 for _, ok, _ in legs if ok)
    for name, ok, detail in legs:
        emit(
            f"  {'ok  ' if ok else 'FAIL'} {name}{('  — ' + detail) if detail and not ok else ''}"
        )
    if passed != len(legs):
        warn(f"SELFTEST FAIL - {passed}/{len(legs)}")
        return EXIT_SELFTEST
    emit(f"SELFTEST PASS - {passed}/{len(legs)}")
    return EXIT_OK


def plat_gate(plat: Platform) -> Optional[str]:
    """The refusal this machine gets instead of a traceback, or None when it can be read.

    Deliberately NOT `gblib.platform_refusal`: that helper's message names the capability a
    producer needed, and Tier A needs no capability at all — only a directory. What it needs is
    the honest sentence for a platform that has none.
    """
    if plat.support_dir is None:
        return (
            f"gb-mirror: {plat.os} ({plat.status}) has no Grok Bot desktop client, so this "
            f"machine holds no local deployment to mirror.\n"
            f"    {plat.why}.\n"
            f"    fix: {plat.remediation}"
        )
    return None


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class MirrorArgs:
    """Read your own Grok Bot deployment off this machine: six numbers and one sentence."""

    deep: bool = arg(
        help="also read routines, memory and the weekly allowance over READ-ONLY RPCs "
        "(needs a signed-in macOS desktop; prompts the keychain once)"
    )
    json: bool = arg(help="machine-readable envelope on stdout")
    out: Optional[str] = arg(
        metavar="PATH", help="write the envelope to PATH as well (atomic)"
    )
    save: bool = arg(
        help=f"record this reading in {SNAP_DIR.name}/<ISO>.json so a later run can diff it"
    )
    since: Optional[str] = arg(
        metavar="PATH|latest",
        help="diff this reading against a saved snapshot and print what MOVED",
    )
    selftest: bool = arg(help="run the inline-fixture selftest and exit")


def body() -> int:
    args = parse(MirrorArgs, description=SUMMARY)
    if args.selftest:
        return selftest()

    plat = platform_support()
    refusal = plat_gate(plat)
    if refusal:
        warn(refusal)
        return EXIT_ENVIRONMENT
    support = plat.support_dir
    assert support is not None  # plat_gate guarantees it

    if not support.is_dir():
        # `plat.remediation` is gblib's own sentence for this exact case, and on an UNVERIFIED
        # platform it is the RIGHT one ("find the userData directory and export GB_SUPPORT_DIR")
        # where "launch the app" is not — the operator may already have it installed at a path
        # nobody here has ever seen. Deferring to it rather than restating a macOS-shaped fix is
        # the difference between a Windows user having a route out and having a dead end.
        fix = (
            plat.remediation
            or "launch the Grok Bot desktop app once and sign in, then re-run"
        )
        # Printed only when the remediation has not already said it. Two lines telling the
        # operator to export the same variable reads like the tool does not know what it advised.
        alt = (
            ""
            if SUPPORT_DIR_ENV in fix
            else f"\n    or:  export {SUPPORT_DIR_ENV}=<path> if the client keeps it elsewhere"
        )
        warn(
            f"gb-mirror: no Grok Bot state at {support}.\n"
            f"    {plat.why}.\n"
            f"    fix: {fix}{alt}"
        )
        return EXIT_ENVIRONMENT

    m = take_mirror(support)
    if m is None:
        warn(
            f"gb-mirror: {support} exists but no roster has ever been cached here.\n"
            f"    That is 'this client has not synced', which is NOT the same as 'you have no "
            f"Bots' — reporting zero would be a fact this tool invented.\n"
            f"    fix: open the Grok Bot app, let the sidebar load, then re-run"
        )
        return EXIT_ENVIRONMENT

    base = corpus_baseline()
    deep = deep_read(support, m.bots) if args.deep else None
    doc = envelope(m, base, deep, plat)

    if args.out:
        atomic_write_json(pathlib.Path(args.out), doc)

    # `--since` REPLACES the board with the delta, because the two answer different questions and
    # printing both buries the one that was asked for. The board is one command away.
    if args.since:
        d, no = diff_against(args.since, doc)
        if no is not None or d is None:
            warn(no.text if no else "gb-mirror: --since could not be answered")
            return no.code if no else EXIT_ENVIRONMENT
        emit(json.dumps(diff_envelope(d), indent=1) if args.json else render_diff(d))
    else:
        emit(json.dumps(doc, indent=1) if args.json else render(m, base, deep, plat))

    # Saved LAST, and after the diff, so `--save --since latest` compares against the previous
    # reading rather than against the one it is about to write.
    if args.save:
        path = save_snapshot(doc)
        note = (
            f"  SAVED   {path} — `gb mirror --since latest` will diff the next reading "
            f"against it"
        )
        # stdout stays a pure envelope under `--json`; a save note inside it would break every
        # consumer that pipes this verb into `jq`.
        if args.json:
            warn(note.strip())
        else:
            emit("\n" + note)

    if args.deep and deep is not None and not deep.reached:
        warn(f"gb-mirror: --deep could not read the account — {deep.detail}")
        return EXIT_ENVIRONMENT
    return EXIT_OK


if __name__ == "__main__":
    gbmain(body)
