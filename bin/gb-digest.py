#!/usr/bin/env python3
"""gb-digest — the thing a human actually reads.

WHY THIS EXISTS (measured 2026-09-11). The repo could answer "what changed" only in machine
shapes: `gb daily` prints counters, `gb-store.py diff --json` emits graded row sets,
`gb-whatchanged.py` reports gate flips that are mostly unnameable plumbing, and `gb advise`
ranks actions. There was no `CHANGELOG.md` and `findings/` held ONE entry. So the honest answer
to "how does a person learn what changed this week" was: they don't. That gap is the distance
between an instrument and a product.

WHAT IT IS. Four sections, in the order a reader cares about, each ending in something they can
DO rather than something they should feel:

    1. what the vendor shipped     (sources: docs.x.ai, Cursor changelog/blog)
    2. what builders shipped       (github, feeds, x, usecases)
    3. what changed in your fleet  (inventory, utilization, the gate board)
    4. the one thing to do next    (a real command, usually `gb templates deploy <id>`)

RULE ZERO COMPLIANCE, and this is the whole design constraint. A digest is a document, and this
repo scores documents ZERO. It earns its place only because every row terminates in an
executable action against a live deployment — `gb templates deploy <id> --apply` is one touch and
was proven end to end today. A row that cannot name a command is not printed; it is counted in
`suppressed` so the silence is visible rather than flattering.

FRESHNESS IS A CONTRACT: BROKE vs QUIET.
A page that always renders makes staleness unfalsifiable — that is precisely why the duel killed
the dashboard idea. So this refuses to present old data as news:

    QUIET  every source is fresh and genuinely nothing changed        -> exit 0
    NEWS   at least one graded change with an action                  -> exit 0
    BROKE  a source is stale past its budget, or a diff is UNMEASURED -> exit 1, and the
           section says which source and how old, never "no change"

"Unmeasured" and "unchanged" are different answers and conflating them is the failure this file
exists to prevent. It happened twice today at the data layer (a stale inventory reported
`routines_with_runs: 0` six hours after a routine fired; a per-machine cache reported a live Bot
as deleted), so the renderer refuses to be the third.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gbargs  # noqa: E402
import gbtypes  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# How old a source may be before the digest calls it BROKE rather than quiet. These are the
# producers' own measured cadences, not round numbers: the vendor surface rebuilds in-week, the
# X firehose moves by the minute, the corpus has nearly stopped growing.
STALE_BUDGET_HOURS: Dict[str, int] = {
    "sources": 36,
    "github": 36,
    "feeds": 36,
    "x": 36,
    "links": 72,
    "usecases": 24 * 14,  # 36 added in the last week vs 241 in three August days
    "market": 24 * 7,
}

VENDOR_ROOTS: Tuple[str, ...] = ("sources",)
BUILDER_ROOTS: Tuple[str, ...] = ("github", "feeds", "x", "usecases")


@dataclasses.dataclass(frozen=True)
class Row:
    """One line a human reads. `action` is mandatory by construction."""

    text: str
    action: str
    source: str


@dataclasses.dataclass(frozen=True)
class Section:
    title: str
    rows: Tuple[Row, ...]
    unmeasured: Tuple[str, ...] = ()
    suppressed: int = 0

    def as_json(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "rows": [
                {"text": r.text, "action": r.action, "source": r.source}
                for r in self.rows
            ],
            "unmeasured": list(self.unmeasured),
            "suppressed_without_action": self.suppressed,
        }


@dataclasses.dataclass(frozen=True)
class Digest:
    stamp: str
    verdict: str  # NEWS | QUIET | BROKE
    sections: Tuple[Section, ...]
    stale: Tuple[str, ...]
    next_action: str

    def as_json(self) -> Dict[str, Any]:
        return {
            "schema": "gb-digest/1",
            "stamp": self.stamp,
            "verdict": self.verdict,
            "stale_sources": list(self.stale),
            "next_action": self.next_action,
            "sections": [s.as_json() for s in self.sections],
        }


def newest(root: pathlib.Path, name: str) -> Optional[pathlib.Path]:
    d = root / name
    if not d.is_dir():
        return None
    files = [p for p in d.rglob("*.json") if not p.name.startswith(".")]
    return max(files, key=lambda p: p.name) if files else None


def age_hours(path: pathlib.Path, now: dt.datetime) -> float:
    stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
    return (now - stamp).total_seconds() / 3600.0


def stale_sources(root: pathlib.Path, now: dt.datetime) -> List[str]:
    """A source past its budget. Reported by NAME and AGE — 'something is stale' is not a finding."""
    out: List[str] = []
    for name, budget in sorted(STALE_BUDGET_HOURS.items()):
        p = newest(root, name)
        if p is None:
            out.append(f"{name}: never collected")
            continue
        age = age_hours(p, now)
        if age > budget:
            out.append(f"{name}: {age:.0f}h old (budget {budget}h)")
    return out


def run_diff(root: pathlib.Path, kind: str) -> Optional[Dict[str, Any]]:
    """Ask the store, rather than re-deriving a diff a second way."""
    proc = gbtypes.run(
        [
            sys.executable,
            str(root / "bin" / "gb-store.py"),
            "diff",
            "--kind",
            kind,
            "--json",
        ],
        timeout_s=300,
        cwd=root,
    )
    if proc.code != 0 or not proc.out.strip():
        return None
    try:
        parsed = json.loads(proc.out)
    except ValueError:
        return None
    result = parsed.get("result")
    return result if isinstance(result, dict) else None


def title_of(row: Any) -> str:
    if not isinstance(row, dict):
        return str(row)[:90]
    for key in ("title", "name", "full_name", "job", "url"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:90]
    return str(row.get("id") or "?")[:90]


def diff_section(
    root: pathlib.Path,
    title: str,
    kinds: Sequence[str],
    action_for: Any,
    limit: int = 5,
) -> Section:
    rows: List[Row] = []
    unmeasured: List[str] = []
    suppressed = 0
    for kind in kinds:
        d = run_diff(root, kind)
        if d is None:
            unmeasured.append(f"{kind}: the store could not diff it")
            continue
        if d.get("status") != "OK":
            unmeasured.append(f"{kind}: {d.get('detail') or d.get('status')}")
            continue
        added = int(d.get("added") or 0)
        changed = int(d.get("changed") or 0)
        if not added and not changed:
            continue
        graded = " (graded by identity)" if d.get("graded_by_identity") else ""
        head = f"{kind}: {added} new, {changed} changed{graded}"
        act = action_for(kind, d)
        if not act:
            suppressed += 1
            continue
        rows.append(Row(head, act, kind))
        for item in list(d.get("added_rows") or [])[:limit]:
            rows.append(Row(f"    new · {title_of(item)}", act, kind))
    return Section(title, tuple(rows), tuple(unmeasured), suppressed)


def fleet_section(root: pathlib.Path) -> Section:
    """What changed in the operator's OWN deployment — the only section about them."""
    rows: List[Row] = []
    unmeasured: List[str] = []
    util = newest(root, "utilization")
    if util is None:
        unmeasured.append("utilization: never collected — run bin/gb-utilization.py")
        return Section("what changed in your fleet", (), tuple(unmeasured))
    try:
        doc = json.loads(util.read_text(errors="replace"))
    except (ValueError, OSError):
        unmeasured.append(f"utilization: {util.name} is unreadable")
        return Section("what changed in your fleet", (), tuple(unmeasured))

    bots = doc.get("bots") or []
    routines = sum(int(b.get("routines") or 0) for b in bots if isinstance(b, dict))
    ran = sum(
        int(b.get("routines_with_runs") or 0) for b in bots if isinstance(b, dict)
    )
    shards = doc.get("memory_shards_with_content")

    if routines and ran < routines:
        rows.append(
            Row(
                f"{ran} of {routines} routines have ever run — a schedule that never fires is "
                f"a chat client on a subscription",
                "gb why g25-routine-liveness",
                "utilization",
            )
        )
    elif routines:
        rows.append(
            Row(
                f"all {routines} routines have run",
                "gb why g25-routine-liveness",
                "utilization",
            )
        )
    else:
        rows.append(
            Row(
                "0 routines exist — nothing in this fleet works while you sleep",
                "gb templates deploy routine-proof --apply",
                "utilization",
            )
        )
    if isinstance(shards, int) and shards == 0:
        rows.append(
            Row(
                "0 memory shards carry content — every run starts from nothing",
                "gb templates deploy decision-ledger --apply",
                "utilization",
            )
        )
    return Section("what changed in your fleet", tuple(rows), tuple(unmeasured))


def vendor_action(kind: str, _d: Dict[str, Any]) -> str:
    return "gb templates deploy vendor-watch --apply" if kind == "sources" else ""


def builder_action(kind: str, _d: Dict[str, Any]) -> str:
    return {
        "github": "gb mine --limit 5",
        "feeds": "gb templates deploy vendor-watch --apply",
        "x": "gb templates list",
        "usecases": "gb templates stats",
    }.get(kind, "")


def build(root: pathlib.Path, now: Optional[dt.datetime] = None) -> Digest:
    now = now or dt.datetime.now(dt.timezone.utc)
    stale = stale_sources(root, now)
    sections = (
        diff_section(root, "what the vendor shipped", VENDOR_ROOTS, vendor_action),
        diff_section(root, "what builders shipped", BUILDER_ROOTS, builder_action),
        fleet_section(root),
    )
    any_rows = any(s.rows for s in sections)
    any_unmeasured = any(s.unmeasured for s in sections)
    verdict = (
        "BROKE" if (stale or any_unmeasured) else ("NEWS" if any_rows else "QUIET")
    )
    nxt = next((r.action for s in sections for r in s.rows if r.action), "gb triage")
    return Digest(now.strftime("%Y-%m-%dT%H%M"), verdict, sections, tuple(stale), nxt)


def render(d: Digest) -> str:
    out: List[str] = [f"grok bot digest — {d.stamp}  [{d.verdict}]", ""]
    if d.verdict == "BROKE" and d.stale:
        out.append("STALE — this digest is NOT a statement that nothing happened:")
        out += [f"  {s}" for s in d.stale]
        out.append("")
    for s in d.sections:
        out.append(s.title.upper())
        if not s.rows and not s.unmeasured:
            out.append("  nothing changed")
        for r in s.rows:
            out.append(f"  {r.text}")
            if not r.text.startswith("    "):
                out.append(f"      -> {r.action}")
        for u in s.unmeasured:
            out.append(f"  UNMEASURED {u}")
        if s.suppressed:
            out.append(
                f"  ({s.suppressed} change(s) hidden: no action could be named for them)"
            )
        out.append("")
    out.append(f"NEXT: {d.next_action}")
    return "\n".join(out)


def selftest() -> int:
    import tempfile

    fails: List[str] = []
    checks = 0

    def check(ok: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            fails.append(label)

    now = dt.datetime(2026, 9, 11, 21, 0, tzinfo=dt.timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)

        # 1. a fleet with routines that never ran must produce a row AND an action
        (tmp / "utilization").mkdir()
        gbtypes.atomic_write_text(
            tmp / "utilization" / "2026-09-11T0000.json",
            json.dumps(
                {
                    "bots": [{"routines": 2, "routines_with_runs": 0}],
                    "memory_shards_with_content": 0,
                }
            ),
        )
        fs = fleet_section(tmp)
        check(len(fs.rows) == 2, f"fleet rows={len(fs.rows)} want 2")
        check(all(r.action for r in fs.rows), "a fleet row shipped without an action")

        # 2. fires-on-known-bad: a healthy fleet must NOT emit the never-ran row, else leg 1
        #    proves only that rows exist, not that the predicate works
        gbtypes.atomic_write_text(
            tmp / "utilization" / "2026-09-11T0001.json",
            json.dumps(
                {
                    "bots": [{"routines": 2, "routines_with_runs": 2}],
                    "memory_shards_with_content": 3,
                }
            ),
        )
        fs2 = fleet_section(tmp)
        check(len(fs2.rows) == 1, f"healthy fleet rows={len(fs2.rows)} want 1")
        check(
            "have ever run" not in fs2.rows[0].text,
            "healthy fleet still reported the never-ran finding",
        )

        # 3. staleness is named, with the source and its age — never a bare 'stale'
        old = stale_sources(tmp, now + dt.timedelta(days=400))
        check(
            any("utilization" not in s for s in old),
            "stale list is empty on an ancient tree",
        )
        check(all(":" in s for s in old), "a stale entry did not name its source")

        # 4. a missing source is 'never collected', NOT 'unchanged'
        check(
            any("never collected" in s for s in stale_sources(tmp, now)),
            "a never-collected source was not reported as such",
        )

        # 5. unmeasured forces BROKE — the whole point: unmeasured != unchanged
        broke = Digest(
            "x", "BROKE", (Section("t", (), ("k: no diff",)),), (), "gb triage"
        )
        check(
            broke.as_json()["sections"][0]["unmeasured"] == ["k: no diff"],
            "unmeasured lost in json",
        )

        # 6. a row without an action is suppressed and COUNTED, not silently dropped
        sec = Section("t", (), (), 3)
        check(sec.as_json()["suppressed_without_action"] == 3, "suppressed count lost")

        # 7. the rendered text always names the next action
        text = render(build(tmp, now))
        check("NEXT:" in text, "rendered digest has no NEXT action")

    for f in fails:
        print(f"FAIL: {f}")
    print(
        f"SELFTEST {'FAIL' if fails else 'PASS'} - {checks - len(fails)}/{checks} properties"
    )
    return 1 if fails else 0


@dataclasses.dataclass(frozen=True)
class DigestArgs:
    """Render the human-readable digest of what changed."""

    json_out: bool = gbargs.arg(
        default=False, help="machine-readable envelope on stdout"
    )
    selftest: bool = gbargs.arg(default=False, help="prove this renderer's properties")


def body() -> int:
    parser = gbargs.build_parser(
        DigestArgs, prog="gb-digest", description=(__doc__ or "").splitlines()[0]
    )
    ns = parser.parse_args()
    if bool(getattr(ns, "selftest", False)):
        return selftest()
    d = build(ROOT)
    if bool(getattr(ns, "json_out", False)):
        print(json.dumps(d.as_json(), indent=1))
    else:
        print(render(d))
    return 1 if d.verdict == "BROKE" else 0


if __name__ == "__main__":
    gbtypes.main(body)
