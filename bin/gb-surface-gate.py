#!/usr/bin/env python3
"""gb-surface-gate — decide whether this week's Grok Bot measurement is trustworthy,
and whether the deployment still agrees with the vendor surface.

The gate this repo exists to run. It blocks one edge: closing a weekly tick.
A tick may not be recorded GREEN unless every check below passes at a fresh
snapshot. Its verdict text and its exit code always agree (0 GREEN, 1 RED,
2 ERROR) — the `docs-staleness-gate.sh` defect (prints RED, exits 0) is the
known failure this contract forbids.

Three verdict states, deliberately distinct:
  GREEN  measured, and everything agrees
  RED    measured, and something disagrees — a finding to close
  ERROR  NOT measured (fetcher broke, snapshot absent, canary collapsed).
         "Never checked" must never read like "passed".

Usage:
  gb-surface-gate.py [--root DIR] [--now YYYY-MM-DD] [--json] [--disable CHECK]
  gb-surface-gate.py --selftest [--json]
  gb-surface-gate.py --capabilities
  gb-surface-gate.py --counts [--json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children, load  # noqa: E402

VERSION = "1.11.0"
MAX_SNAPSHOT_AGE_DAYS = 8
MIN_PAGE_BYTES = 500
MIN_LLMS_TOTAL_PAGES = 100
MIN_GROK_BOT_PAGES = 15
STALE_BOT_DAYS = 21
MAX_GROKBOTDEV_AGE_DAYS = 8  # weekly tick; missing daily collect is a quiet producer

GREEN, RED, ERROR = "GREEN", "RED", "ERROR"
EXIT = {GREEN: 0, RED: 1, ERROR: 2}
# A Bot younger than this is exempt from g19: a fleet is built before it is used.
FLEET_GRACE_DAYS = 14
# The archive must be newer than this. Tied to the weekly tick, not to taste.
MAX_ARCHIVE_AGE_DAYS = 8
# The mined-bundle surface g27 grades. The miner records each shipped bundle under
# schema/<ver>/ and the review pointer in schema/reviewed.json; this gate never
# talks to the vendor, it only reads bundles already on disk.
SCHEMA_DIRNAME = "schema"
SCHEMA_REVIEWED_FILENAME = "reviewed.json"
SCHEMA_REGISTRY_FILENAME = "registry.json"

CHECKS = [
    "g1-surface-fetch-integrity",
    "g2-surface-canary",
    "g3-snapshot-freshness",
    "g4-client-update-applied",
    "g5-bot-charter-present",
    "g6-capability-delta-reviewed",
    "g7-desktop-parity",
    "g8-desktop-inventory",
    "g9-routine-health",
    "g10-loop-scheduled",
    "g11-tunable-delta-reviewed",
    "g12-ondemand-spend-bounded",
    "g13-practitioner-index-reviewed",
    "g14-coverage-ratchet",
    "g15-market-delta-reviewed",
    "g16-doc-delta-reviewed",
    "g17-discovery-triaged",
    "g18-mcp-surface-healthy",
    "g19-fleet-earning-its-keep",
    "g20-context-archived",
    "g21-usecase-corpus-reviewed",
    "g22-durable-io",
    "g23-types-ratcheted",
    "g24-cli-contract",
    "g25-routine-liveness",
    "g26-jobs-proof-calls",
    "g27-surface-drift",
    "g28-grokbotdev-fresh",
    "g29-dogfood-dispositions",
    "g30-score-ordering",
]

MAX_RUN_GAP_DAYS = 9  # one day of slack past g3's 8-day snapshot ceiling

MAX_INVENTORY_AGE_DAYS = 30
FAILED_RUN_STATES = {"failed", "error", "fail"}


def result(check: str, verdict: str, detail: str, **extra) -> dict:
    return {"check": check, "verdict": verdict, "detail": detail, **extra}


def g1_surface_fetch_integrity(snap: pathlib.Path | None) -> dict:
    c = "g1-surface-fetch-integrity"
    if snap is None:
        return result(c, ERROR, "no snapshot directory under surface/")
    man = load(snap / "manifest.json")
    if not man:
        return result(c, ERROR, f"{snap.name}/manifest.json missing or unparseable")
    if man.get("index_status") != 200:
        return result(
            c,
            ERROR,
            f"llms.txt index status={man.get('index_status')} err={man.get('index_error')}",
        )
    pages = man.get("pages") or []
    if not pages:
        return result(
            c,
            ERROR,
            "zero pages in manifest — an empty scan set is an ERROR, never a pass",
        )
    bad = []
    for p in pages:
        why = None
        if p.get("status") != 200:
            why = f"status={p.get('status')}"
        elif p.get("bytes", 0) < MIN_PAGE_BYTES:
            why = f"bytes={p.get('bytes')}<{MIN_PAGE_BYTES}"
        elif not p.get("marker_ok"):
            why = "missing 'Grok Bot' marker"
        else:
            f = snap / "pages" / f"{p['slug']}.md"
            if not f.is_file():
                why = "page file absent on disk"
            elif hashlib.sha256(f.read_bytes()).hexdigest() != p.get("sha256"):
                why = "sha256 mismatch between manifest and file"
        if why:
            bad.append(f"{p.get('slug')}: {why}")
    # The extra Tier-1 sources are graded too: a watch list that is named but not fetched is the
    # gate-wiring failure this repo refuses to ship.
    for x in man.get("extras") or []:
        if x.get("status") != 200:
            bad.append(
                f"{x.get('label')}: status={x.get('status')} {x.get('error','')}".strip()
            )
        elif x.get("bytes", 0) < x.get("min_bytes", 1):
            bad.append(
                f"{x.get('label')}: {x.get('bytes')}B < floor {x.get('min_bytes')}"
            )
    if bad:
        return result(
            c, RED, f"{len(bad)} source(s) failed integrity", failures=bad[:10]
        )
    return result(
        c,
        GREEN,
        f"{len(pages)} pages + {len(man.get('extras') or [])} extra sources "
        f"fetched, hashed, and verified",
    )


def g2_surface_canary(snap: pathlib.Path | None) -> dict:
    c = "g2-surface-canary"
    man = load(snap / "manifest.json") if snap else None
    if not man:
        return result(c, ERROR, "no manifest to read the canary from")
    total, gb = man.get("llms_total_pages", 0), man.get("grok_bot_pages_listed", 0)
    if total < MIN_LLMS_TOTAL_PAGES or gb < MIN_GROK_BOT_PAGES:
        return result(
            c,
            ERROR,
            f"index collapsed: {gb} grok-bot pages (floor {MIN_GROK_BOT_PAGES}), "
            f"{total} total (floor {MIN_LLMS_TOTAL_PAGES}) — the fetcher or the doc site changed; confirm by hand",
        )
    return result(c, GREEN, f"index healthy: {gb} grok-bot pages of {total} total")


def g3_snapshot_freshness(snap: pathlib.Path | None, now: dt.date) -> dict:
    c = "g3-snapshot-freshness"
    if snap is None:
        return result(c, ERROR, "no snapshot to age")
    try:
        taken = dt.date.fromisoformat(snap.name[:10])
    except ValueError:
        return result(c, ERROR, f"snapshot dir {snap.name!r} is not an ISO date")
    age = (now - taken).days
    if age > MAX_SNAPSHOT_AGE_DAYS:
        return result(
            c,
            RED,
            f"newest snapshot is {age}d old (ceiling {MAX_SNAPSHOT_AGE_DAYS}d) — the weekly loop stopped",
        )
    return result(c, GREEN, f"snapshot {snap.name} is {age}d old")


def g4_client_update_applied(audit: dict | None) -> dict:
    c = "g4-client-update-applied"
    if not audit:
        return result(c, ERROR, "no deployment audit to read")
    app = audit.get("app") or {}
    if not app.get("present"):
        return result(c, ERROR, f"Grok Bot app not found at {app.get('path')}")
    installed = app.get("version")
    marker = audit.get("update_marker") or {}
    target = marker.get("targetVersion")
    phase = marker.get("phase")
    if target and target != installed:
        return result(
            c,
            RED,
            f"update {installed} -> {target} is {phase}, not applied; the running client is behind the shipped one",
            installed=installed,
            target=target,
            phase=phase,
        )
    backend = (audit.get("account") or {}).get("app_version_seen_by_backend")
    if backend and installed and backend != installed:
        return result(
            c,
            RED,
            f"backend evaluated gates for {backend}, installed client is {installed}",
        )
    return result(c, GREEN, f"client {installed} is the version the vendor last staged")


def g5_bot_charter_present(audit: dict | None, root: pathlib.Path) -> dict:
    """Every Bot carries a standing description.

    Source precedence matters: for durable-identity Bots the SERVER roster is authoritative and
    the desktop's local blob is a cache. Measured 2026-09-11 — right after the pre-durable Bots
    were deleted, the local blob held one stray "New Bot" while the server held the real ten.
    So this grades `server_bots` from the newest inventory when present, and says which it used.
    """
    c = "g5-bot-charter-present"
    src, bots = "local audit", (audit or {}).get("bots") or []
    have = inventories(root)
    for _label, (_f, doc) in sorted(have.items()):
        if doc.get("server_bots"):
            src, bots = "server roster", doc["server_bots"]
            break
    if not audit and not bots:
        return result(c, ERROR, "no deployment audit to read")
    if not bots:
        return result(
            c,
            ERROR,
            "roster empty — cannot distinguish 'no bots' from 'roster not read'",
        )
    naked = [b["name"] for b in bots if not b.get("description_chars")]
    if naked:
        return result(
            c,
            RED,
            f"{len(naked)} of {len(bots)} Bot(s) ({src}) hold credentials with no "
            f"standing description: {', '.join(naked)}",
            bots=naked,
            source=src,
        )
    return result(
        c, GREEN, f"all {len(bots)} Bots carry a standing description ({src})"
    )


def reviewed_baseline(
    root: pathlib.Path, audits: list[pathlib.Path]
) -> pathlib.Path | None:
    """The audit a findings file says it reviewed — the baseline the next delta is measured from.

    Deliberately NOT "the previous audit on disk": re-running the audit would then silently wash
    away an unreviewed vendor change. A delta stays open until a findings file names the audit it
    was reviewed against (`reviewed-audit: <stamp>.json`).
    """
    named = set()
    fdir = root / "findings"
    if fdir.is_dir():
        for f in fdir.glob("*.md"):
            named.update(
                re.findall(
                    r"reviewed-audit:\s*(\S+\.json)", f.read_text(errors="replace")
                )
            )
    hits = [a for a in audits if a.name in named]
    return hits[-1] if hits else (audits[0] if audits else None)


def g6_capability_delta_reviewed(
    audit: dict | None,
    baseline: pathlib.Path | None,
    audit_path: pathlib.Path | None,
    root: pathlib.Path,
    snap: pathlib.Path | None,
) -> dict:
    c = "g6-capability-delta-reviewed"
    if not audit:
        return result(c, ERROR, "no deployment audit to read")
    audit_name = audit_path.name if audit_path else "<unknown>"
    baseline_name = baseline.name if baseline else "<none>"
    prev = load(baseline) if (baseline and baseline != audit_path) else None
    cur_g, prev_g = (audit.get("gates") or {}), ((prev or {}).get("gates") or {})
    cur_names = set(cur_g.get("enabled_names") or [])
    if not cur_names:
        return result(
            c,
            ERROR,
            "zero feature-gate names recovered — decoding broke; absence is not proof",
        )
    if not prev:
        return result(
            c,
            GREEN,
            f"no reviewed baseline before {audit_name} ({len(cur_names)} capabilities); nothing to diff yet",
        )
    # Exact when both audits carry hashed ids; name-based otherwise, which also moves when our
    # decode coverage changes. `basis` says which one produced this verdict.
    cur_ids, prev_ids = (
        set(cur_g.get("enabled_ids") or []),
        set(prev_g.get("enabled_ids") or []),
    )
    if cur_ids and prev_ids:
        names: dict = {}
        basis = "ids"
        gained = sorted(names.get(i, i) for i in cur_ids - prev_ids)
        lost = sorted(names.get(i, i) for i in prev_ids - cur_ids)
    else:
        # A name-only diff is NOT an entitlement signal. Measured 2026-09-11: the 0.24.0 -> 0.47.0
        # upgrade produced 35 "gained" and 38 "lost" names while the real change was +2 enabled
        # gates — every "lost" gate was still TRUE, its literal had just left the bundle. Refuse to
        # render that as a vendor movement; ERROR resolves itself once both audits carry ids.
        return result(
            c,
            ERROR,
            f"cannot compute an exact delta: {baseline_name} predates enabled_ids capture. "
            f"Name-basis diffs move with decode coverage, not entitlement. Re-run the audit so the "
            f"next tick compares ids.",
        )
    if not gained and not lost:
        return result(
            c,
            GREEN,
            f"capability set unchanged ({len(cur_names)} enabled, basis={basis})",
        )
    # Determinism-exempt: display-only fallback. Reached only when snap is None;
    # the stamp names the findings file inside RED detail text and never feeds a
    # verdict comparison, so the wall clock cannot move a verdict here.
    stamp = snap.name[:10] if snap else dt.date.today().isoformat()
    return result(
        c,
        RED,
        f"vendor moved ({len(gained)} gained, {len(lost)} lost, basis={basis}) since {baseline_name}; "
        f"record it in findings/{stamp}.md with a `reviewed-audit: {audit_name}` line",
        gained=gained[:25],
        lost=lost[:25],
    )


def g11_tunable_delta_reviewed(
    audit: dict | None,
    baseline: pathlib.Path | None,
    audit_path: pathlib.Path | None,
    root: pathlib.Path,
    snap: pathlib.Path | None,
) -> dict:
    """No parameter this account runs on changed without somebody reading the new value.

    g6 watches whether a capability is ON. This watches what it is SET TO — the surface that
    carries `grok_bot_conversation_size_limits` (soft 256MB / hard 10GB), `grok_bot_loop_detection`
    (mode=on), the memory-pressure thresholds that decide when a Bot's process is culled, and the
    model-routing tables. xAI can retune any of them server-side with no client update and no UI,
    so an unread change here is exactly as silent as an unread entitlement change — and more
    likely, because experiments move constantly.

    Compares the per-entry value DIGEST, not the value: the full payload is ~400KB of vendor
    experiment data per tick. A digest cannot be read, but it cannot be wrong either, and
    `gb-capabilities.py --tunables` prints the live values whenever the digest says to look.
    """
    c = "g11-tunable-delta-reviewed"
    if not audit:
        return result(c, ERROR, "no deployment audit to read")
    cur_t = audit.get("tunables") or {}
    cur_d = cur_t.get("digests") or {}
    if not cur_d:
        return result(
            c,
            ERROR,
            "no tunable digests in the audit — the parameter surface is unmeasured",
        )
    audit_name = audit_path.name if audit_path else "<unknown>"
    baseline_name = baseline.name if baseline else "<none>"
    prev = load(baseline) if (baseline and baseline != audit_path) else None
    prev_d = ((prev or {}).get("tunables") or {}).get("digests") or {}
    if not prev:
        return result(
            c,
            GREEN,
            f"no reviewed baseline before {audit_name} "
            f"({len(cur_d)} tunables, {cur_t.get('named', 0)} named); nothing to diff yet",
        )
    if not prev_d:
        # Same refusal shape as g6's: a baseline that predates the measurement is unmeasured, not
        # unchanged, and rounding that up would report a clean board for a surface nobody watched.
        return result(
            c,
            ERROR,
            f"cannot compute a tunable delta: {baseline_name} predates digest "
            f"capture. Re-run the audit so the next tick has both sides.",
        )
    names = cur_t.get("names") or {}
    changed = sorted(
        names.get(k, f"<unnamed {k}>")
        for k in set(cur_d) & set(prev_d)
        if cur_d[k] != prev_d[k]
    )
    added = sorted(names.get(k, f"<unnamed {k}>") for k in set(cur_d) - set(prev_d))
    removed = sorted(
        ((prev or {}).get("tunables") or {}).get("names", {}).get(k, f"<unnamed {k}>")
        for k in set(prev_d) - set(cur_d)
    )
    if not (changed or added or removed):
        return result(
            c,
            GREEN,
            f"{len(cur_d)} tunables unchanged ({cur_t.get('named', 0)} named, "
            f"{cur_t.get('distinct_param_keys', 0)} distinct params)",
        )
    # Determinism-exempt: display-only fallback. Reached only when snap is None;
    # the stamp names the findings file inside RED detail text and never feeds a
    # verdict comparison, so the wall clock cannot move a verdict here.
    stamp = snap.name[:10] if snap else dt.date.today().isoformat()
    return result(
        c,
        RED,
        f"parameters moved ({len(changed)} retuned, {len(added)} added, {len(removed)} removed) since "
        f"{baseline_name}; read them with `bin/gb-capabilities.py --tunables` and record it in "
        f"findings/{stamp}.md with a `reviewed-audit: {audit_name}` line",
        retuned=changed[:20],
        added=added[:20],
        removed=removed[:20],
    )


def g12_ondemand_spend_bounded(root: pathlib.Path, now: dt.date) -> dict:
    """This period is not on pace to bill past the included allowance.

    REWRITTEN 2026-09-11, because the previous premise was false. It read: "the account can
    bill without limit and nobody has written down that they meant it", and it went RED unless
    a hand-maintained `spend-decision.json` existed. Two measurements killed that:

      1. The operator sets a spend cap INSIDE the app, to whatever they choose, and can change
         it at any time. "Can bill without limit" was simply not true.
      2. The vendor's usage object publishes `period_start`, `next_reset`, `usage_percent`,
         `has_available_usage`, `plan_label`, `on_demand_enabled`, `on_demand_eligible` and a
         dashboard URL — and NO cap, and NO dollar figure. `usage_percent` is a fraction of the
         included allowance, not money.

    So the old check asked the operator to hand-maintain a local mirror of a mutable in-app
    setting that this tool cannot read back. That file is stale the moment they change the cap
    in the app, and a gate cannot verify it — it can only verify that somebody typed something.
    Gating a shared tool on one person's billing preference is the error; a fresh installer
    would have gotten a RED board about a file they had never heard of.

    What IS measurable from published data is the question that actually matters: at the
    current pace, will this period cross the included allowance at all? Crossing it is the
    precondition for any on-demand charge, whatever the cap is set to. Below the allowance the
    cap is irrelevant; above it, the cap is the operator's business and the vendor enforces it.

    GREEN when on-demand is off (it cannot bill), or when the projection stays inside the
    allowance. RED only when on-demand is ON and the period is genuinely on pace to exceed
    100%. The projection is the same shape `gb-monitor.py` uses, and both read the same fields,
    so the gate and the monitor cannot disagree about the pace.
    """
    c = "g12-ondemand-spend-bounded"
    have = inventories(root)
    usage = (
        next((doc.get("usage") for _f, doc in have.values() if doc.get("usage")), None)
        if have
        else None
    )
    if not usage:
        return result(
            c,
            ERROR,
            "no usage recorded in any inventory — spend position is unmeasured; "
            "run bin/gb-pull-inventory.py --device <label>",
        )
    pct = usage.get("usage_percent")
    if usage.get("on_demand_enabled") is not True:
        return result(
            c,
            GREEN,
            f"on-demand spending off; {pct:.1f}% of the included allowance used"
            if isinstance(pct, (int, float))
            else "on-demand spending off",
        )
    if not isinstance(pct, (int, float)):
        return result(
            c,
            ERROR,
            "on-demand is enabled but usage_percent is absent — the pace cannot be computed, "
            "which is not the same as being inside the allowance",
        )

    # Project to period end. A raw percentage read late in a window reports a number that can
    # no longer move; read early it hides a burn that will obviously overshoot.
    start, reset = usage.get("period_start"), usage.get("next_reset")
    elapsed = None
    if isinstance(start, str) and isinstance(reset, str):
        try:
            t0 = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
            t1 = dt.datetime.fromisoformat(reset.replace("Z", "+00:00"))
            span = (t1 - t0).total_seconds()
            if span > 0:
                # The gate's clock is the --now date (FIXTURE_NOW under --selftest),
                # never the wall clock: a fixture that is RED at generation must
                # still be RED at check time. Midnight UTC, like every sibling check.
                now_utc = dt.datetime(
                    now.year, now.month, now.day, tzinfo=dt.timezone.utc
                )
                elapsed = max(0.0, min(1.0, (now_utc - t0).total_seconds() / span))
        except ValueError:
            elapsed = None

    # Below a tenth of the window, dividing by the elapsed fraction amplifies rounding into a
    # forecast. Fall back to the raw figure, which under-alerts rather than inventing a number.
    projected = pct / elapsed if (elapsed is not None and elapsed >= 0.10) else pct
    basis = "projected" if (elapsed is not None and elapsed >= 0.10) else "raw"
    window = (
        f", {elapsed * 100:.1f}% of the window elapsed" if elapsed is not None else ""
    )
    if projected > 100.0:
        return result(
            c,
            RED,
            f"on pace to exceed the included allowance: {projected:.1f}% {basis} "
            f"({pct:.1f}% used{window}), and on-demand is ENABLED so the overage bills. "
            f"Adjust the cap or turn on-demand off at "
            f"{usage.get('dashboard_url', 'the spending dashboard')}",
            projected_percent=round(projected, 1),
            basis=basis,
        )
    return result(
        c,
        GREEN,
        f"on-demand enabled and inside the allowance: {projected:.1f}% {basis} "
        f"({pct:.1f}% used{window}); the in-app cap governs anything past 100%",
        projected_percent=round(projected, 1),
        basis=basis,
    )


def g13_practitioner_index_reviewed(
    root: pathlib.Path, snap: pathlib.Path | None
) -> dict:
    """The community index moved and somebody read it.

    The brief for this repo was state of the art EVERY WEEK — vendor docs plus what practitioners
    are actually doing. For four ticks the loop watched only the vendor, and the practitioner half
    happened once, by hand, at the start. awesome-grok-bot is a 799-entry curated index that moves
    daily and is the one community source whose CHANGE is a usable signal.

    The rule is deliberately weak about content and strict about attention: a changed hash means
    somebody reads the diff and writes what is worth adopting (or that nothing is), then records
    the hash they read. It never asserts the community is right — `practitioner-reviewed.json`
    records that a human looked, not that a claim was verified. Everything actionable found there
    still has to be measured against this account before it is believed.
    """
    c = "g13-practitioner-index-reviewed"
    man = load(snap / "manifest.json") if snap else None
    if not man:
        return result(c, ERROR, "no snapshot manifest — the surface was never fetched")
    row = next(
        (
            e
            for e in (man.get("extras") or [])
            if e.get("label") == "practitioner-index"
        ),
        None,
    )
    if not row:
        return result(
            c,
            ERROR,
            "snapshot carries no practitioner-index row — the source is not being fetched",
        )
    if row.get("status") != 200 or row.get("error"):
        return result(
            c,
            ERROR,
            f"practitioner-index fetch failed ({row.get('status')}) "
            f"{row.get('error', '')} — unread is not unchanged",
        )
    cur = row.get("sha256") or ""
    rec = load(root / "practitioner-reviewed.json") or {}
    seen = rec.get("sha256")
    if seen == cur:
        return result(
            c,
            GREEN,
            f"practitioner index unchanged since review {rec.get('reviewed_at', '?')} "
            f"({cur[:12]})",
        )
    if not seen:
        return result(
            c,
            RED,
            f"practitioner index never reviewed ({cur[:12]}, {row.get('bytes')} bytes) — "
            f"read it and record the hash in practitioner-reviewed.json",
        )
    return result(
        c,
        RED,
        f"practitioner index moved {str(seen)[:12]} -> {cur[:12]} since "
        f"{rec.get('reviewed_at', '?')}; read the diff and record what is worth adopting",
        previous=str(seen)[:12],
        current=cur[:12],
    )


def g14_coverage_ratchet(root: pathlib.Path) -> dict:
    """The number of facets this repo actually measures may go up, never quietly down.

    Everything else here judges the deployment. This judges the INSTRUMENT: `bin/gb-coverage.py`
    walks the artifacts on disk and counts the facets that carry a real value, and this refuses a
    tick where that count fell below the recorded floor. A read that silently starts returning
    null — an API shape change, a renamed field, a probe that quietly 404s — otherwise looks
    exactly like a clean board, because every check that depended on it goes quiet at the same
    time. Measured today: nine facets moved from "hand record" to "machine read" in one session,
    and nothing in the repo would have noticed them moving back.

    The floor lives in `coverage-floor.json` at the repo root (tracked — a gate input in `state/`
    is a gate with no evidence) and only ever rises, by an explicit commit that says why.
    """
    c = "g14-coverage-ratchet"
    # Import by path: the module name has a hyphen, so importlib.util is the only way in.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gbcov", pathlib.Path(__file__).resolve().parent / "gb-coverage.py"
    )
    if spec is None or spec.loader is None:
        return result(
            c,
            ERROR,
            "bin/gb-coverage.py is missing — the instrument cannot measure itself",
        )
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        rs = mod.rows(root)
    except Exception as e:
        return result(
            c, ERROR, f"gb-coverage.py failed to run ({type(e).__name__}: {e})"
        )
    covered = sum(1 for r in rs if r["verdict"] in (mod.MEASURED, mod.EMPTY))
    lost = [r["facet"] for r in rs if r["verdict"] == mod.UNMEASURED]
    floor_doc = load(root / "coverage-floor.json") or {}
    floor = floor_doc.get("covered")
    if floor is None:
        return result(
            c,
            ERROR,
            f"no coverage floor recorded — write coverage-floor.json "
            f"(currently {covered}/{len(rs)} facets measured)",
        )
    if covered < floor:
        return result(
            c,
            RED,
            f"coverage fell {floor} -> {covered} of {len(rs)} facets; "
            f"unmeasured now: {', '.join(lost) or '(none named)'}",
            lost=lost,
            floor=floor,
        )
    if covered > floor:
        return result(
            c,
            GREEN,
            f"coverage {covered}/{len(rs)} is ABOVE the floor of {floor} — "
            f"raise the floor in coverage-floor.json in this commit",
        )
    return result(
        c,
        GREEN,
        f"coverage holds at {covered}/{len(rs)} facets ({len(mod.BLOCKED_FACETS)} blocked)",
    )


def g15_market_delta_reviewed(root: pathlib.Path, snap: pathlib.Path | None) -> dict:
    """Nothing installable changed under us without somebody reading what changed.

    Three catalogs move on the vendor's schedule, not ours: the 322-entry plugin catalog, the
    71-listing Bot marketplace, and the pinned `gitRef` of the plugins THIS account already has
    enabled. The last one is the dangerous column — a moved ref is new third-party code running
    inside a Bot that was approved once, and no other check in this repo looks at it.

    Reviewed means a human read `bin/gb-whatchanged.py` and recorded the market snapshot they
    read in `market-reviewed.json`. As with g13, the record asserts attention, not approval.
    """
    c = "g15-market-delta-reviewed"
    rows = dated_children(root / "market", ".json")
    if not rows:
        return result(c, ERROR, "no market snapshot — run bin/gb-market-snapshot.py")
    cur_path = rows[-1]
    cur = load(cur_path)
    if not cur or not ((cur.get("plugins") or {}).get("rows")):
        return result(
            c, ERROR, f"{cur_path.name} carries no plugin catalog — the read failed"
        )
    rec = load(root / "market-reviewed.json") or {}
    if rec.get("snapshot") == cur_path.name:
        return result(
            c,
            GREEN,
            f"market reviewed at {cur_path.name} "
            f"({(cur.get('plugins') or {}).get('total')} plugins, "
            f"{(cur.get('bot_marketplace') or {}).get('total')} listings)",
        )
    if len(rows) == 1:
        return result(
            c, GREEN, f"first market snapshot ({cur_path.name}) — nothing to diff yet"
        )
    prev = load(rows[-2]) or {}

    def refs(d):
        return {
            r["name"]: r.get("git_ref")
            for r in ((d.get("plugins") or {}).get("rows") or [])
        }

    def mine(d):
        return {r["name"]: r.get("catalog_git_ref") for r in (d.get("installed") or [])}

    cur_r, prev_r = refs(cur), refs(prev)
    added = sorted(set(cur_r) - set(prev_r))
    removed = sorted(set(prev_r) - set(cur_r))
    moved = sorted(k for k in set(cur_r) & set(prev_r) if cur_r[k] != prev_r[k])
    cur_m, prev_m = mine(cur), mine(prev)
    mine_moved = sorted(k for k in set(cur_m) & set(prev_m) if cur_m[k] != prev_m[k])
    if not (added or removed or moved or mine_moved):
        return result(
            c,
            GREEN,
            f"catalog unchanged since {rows[-2].name} "
            f"({(cur.get('plugins') or {}).get('total')} plugins)",
        )
    head = (
        f"{len(mine_moved)} INSTALLED plugin(s) shipped new code ({', '.join(mine_moved)}); "
        if mine_moved
        else ""
    )
    return result(
        c,
        RED,
        f"{head}catalog moved since {rows[-2].name}: +{len(added)} / -{len(removed)} / "
        f"{len(moved)} updated. Read `bin/gb-whatchanged.py` and record {cur_path.name} in "
        f"market-reviewed.json",
        installed_updated=mine_moved,
        added=added[:15],
        removed=removed[:15],
        updated=moved[:15],
    )


def g16_doc_delta_reviewed(root: pathlib.Path) -> dict:
    """A vendor doc page changed and somebody read the change.

    `g1` proves the pages were fetched intact and `g2` proves the index did not collapse — but for
    five ticks nothing compared this week's page hashes to last week's. The repo stored a sha256
    per page and never once asked whether it moved, which is the difference between an archive
    and a watch.

    Covers both official trees and the practitioner index, page by page. Reviewed means the
    snapshot name is recorded in `surface-reviewed.json`; `bin/gb-whatchanged.py` names the exact
    pages that moved.
    """
    c = "g16-doc-delta-reviewed"
    snaps = dated_children(root / "surface")
    if not snaps:
        return result(c, ERROR, "no snapshot at all")
    cur_dir = snaps[-1]
    cur = load(cur_dir / "manifest.json")
    if not cur or not cur.get("pages"):
        return result(c, ERROR, f"{cur_dir.name} carries no pages — the fetch failed")
    rec = load(root / "surface-reviewed.json") or {}
    if rec.get("snapshot") == cur_dir.name:
        return result(c, GREEN, f"docs reviewed at {cur_dir.name}")
    if len(snaps) == 1:
        return result(
            c, GREEN, f"first snapshot ({cur_dir.name}) — nothing to diff yet"
        )
    prev = load(snaps[-2] / "manifest.json") or {}

    def view(m):
        return {p["slug"]: p.get("sha256") for p in (m.get("pages") or [])} | {
            e["label"]: e.get("sha256") for e in (m.get("extras") or [])
        }

    a, b = view(prev), view(cur)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    if not (added or removed or changed):
        return result(c, GREEN, f"{len(b)} sources byte-identical to {snaps[-2].name}")
    return result(
        c,
        RED,
        f"docs moved since {snaps[-2].name}: {len(changed)} changed, {len(added)} added, "
        f"{len(removed)} removed ({', '.join((changed + added + removed)[:8])}). Read "
        f"`bin/gb-whatchanged.py` and record {cur_dir.name} in surface-reviewed.json",
        changed=changed[:20],
        added=added[:20],
        removed=removed[:20],
    )


def g17_discovery_triaged(root: pathlib.Path) -> dict:
    """Every notable repo the search found that we had never heard of has been looked at.

    `sources.json` and `g13` watch what we already know. Neither can find what appeared this week
    under a name nobody has told us. Measured on the first run of `bin/gb-discover.py`: 98
    candidates, 95 of them new, 26 at or above 50 stars — including a 2497-star open-source
    alternative, a 730-entry second community index, and a source-level reconstruction of the
    client this repo reverse-engineers. A fixed watch list finds none of that.

    Triaged means each notable NEW repo is either adopted into `sources.json` or dismissed WITH A
    REASON in `discovery-seen.json`. Dismissal is a first-class answer — most of the top of that
    list is legitimately irrelevant — but an unexamined one is not.

    A failed search is an ERROR, never "nothing new": the positive-control rule. A discovery run
    that returns zero because the query broke reads exactly like a quiet week.
    """
    c = "g17-discovery-triaged"
    rows = dated_children(root / "discovery", ".json")
    if not rows:
        return result(
            c, ERROR, "no discovery run — bin/gb-discover.py has never been run"
        )
    doc = load(rows[-1]) or {}
    if doc.get("errors"):
        qs = ", ".join(e.get("query", "?") for e in doc["errors"])
        return result(
            c,
            ERROR,
            f"{len(doc['errors'])} discovery quer(y/ies) FAILED ({qs}) — a broken "
            f"search is not an empty result",
        )
    if not doc.get("candidates_total"):
        return result(
            c,
            ERROR,
            "discovery returned zero candidates across every query — that is a "
            "broken search, not a quiet week",
        )
    seen_doc = load(root / "discovery-seen.json") or {}
    adopted = {r["repo"] for r in (load(root / "sources.json") or {}).get("repos", [])}
    triaged = (
        set(seen_doc.get("repos") or [])
        | set((seen_doc.get("dismissed") or {}))
        | adopted
    )
    untriaged = [
        r["repo"]
        for r in (doc.get("new") or [])
        if r.get("stars", 0) >= doc.get("notable_stars", 50)
        and r["repo"] not in triaged
    ]
    if untriaged:
        return result(
            c,
            RED,
            f"{len(untriaged)} notable new repo(s) never triaged: "
            f"{', '.join(untriaged[:8])}"
            + (f" (+{len(untriaged) - 8} more)" if len(untriaged) > 8 else "")
            + " — adopt into sources.json or dismiss with a reason in discovery-seen.json",
            untriaged=untriaged[:20],
        )
    return result(
        c,
        GREEN,
        f"{doc.get('candidates_total')} candidate(s) scanned, "
        f"{len(doc.get('new') or [])} new, all notable ones triaged "
        f"({len(adopted)} watched, {len(seen_doc.get('dismissed') or {})} dismissed)",
    )


def g18_mcp_surface_healthy(root: pathlib.Path) -> dict:
    """Every MCP surface a Bot can reach still answers, with no fewer tools than last week.

    MCP is where a capability disappears silently: a server moves, a token expires, a tool is
    renamed upstream, and the Bot just stops being able to do something it did last week. The app
    reports none of that. Two failures are distinguished on purpose —

      unreachable   the endpoint did not answer at all: a finding
      auth-required 401/403, i.e. the server IS there and wants a credential this repo does not
                    hold: recorded, never counted as broken (the NE-8 distinction, applied to MCP)

    A shrinking tool list is the quieter finding and the one nothing else would ever surface, so
    it is RED on its own.
    """
    c = "g18-mcp-surface-healthy"
    rows = dated_children(root / "mcp", ".json")
    if not rows:
        return result(
            c, ERROR, "no MCP validation run — bin/gb-mcp-validate.py has never run"
        )
    cur = load(rows[-1]) or {}
    servers = cur.get("servers") or []
    if not servers:
        return result(
            c, ERROR, f"{rows[-1].name} lists no servers — the validator read nothing"
        )
    rejected = [s["name"] for s in servers if s.get("state") == "credential-rejected"]
    if rejected:
        return result(
            c,
            RED,
            f"{len(rejected)} MCP server(s) REJECTED a credential we hold "
            f"({', '.join(rejected)}) — the stored secret is stale or the server "
            f"runs a different one. Every consumer resolving it gets 401.",
            rejected=rejected,
        )
    failed = [s["name"] for s in servers if s.get("ok") is False]
    if failed:
        detail = "; ".join(
            f"{s['name']}: {s.get('error', '?')[:60]}"
            for s in servers
            if s.get("ok") is False
        )
        return result(
            c, RED, f"{len(failed)} MCP server(s) unreachable — {detail}", failed=failed
        )
    shrunk = []
    if len(rows) > 1:
        prev = {s["name"]: s for s in ((load(rows[-2]) or {}).get("servers") or [])}
        for s in servers:
            was = (prev.get(s["name"]) or {}).get("tool_count")
            now_n = s.get("tool_count")
            if was is not None and now_n is not None and now_n < was:
                shrunk.append(f"{s['name']} {was}->{now_n}")
    if shrunk:
        return result(
            c,
            RED,
            f"MCP tool set SHRANK: {', '.join(shrunk)} — a capability went away "
            f"without anything else noticing",
            shrunk=shrunk,
        )
    healthy = [s for s in servers if s.get("ok")]
    authed = [s for s in servers if s.get("state") == "auth-required"]
    return result(
        c,
        GREEN,
        f"{len(healthy)} MCP server(s) healthy "
        f"({sum(s.get('tool_count', 0) for s in healthy)} tools), "
        f"{len(authed)} behind auth, "
        f"{len(servers) - len(healthy) - len(authed)} not probeable",
    )


def g19_fleet_earning_its_keep(root: pathlib.Path) -> dict:
    """A Bot that can act on your behalf is either working or retired — never just sitting there.

    Every other check in this repo grades the INSTRUMENT: what the vendor changed, what the
    account may do, what the parameters are. None of them ask whether the fleet does anything.
    Measured the day this was written: ten Bots, two routines, zero memory shards with content,
    and 2.7% of the weekly allowance consumed.

    The gated shape is not "you are underusing your subscription" — that is nagging and this
    board does not nag. It is narrower and it is a risk: a Bot with a standing description and
    shared-computer access, with NO routine and NO conversation, is a credential nobody is
    watching. Delete it or give it a job; both are one action, and both clear this.

    Grace: a Bot younger than `FLEET_GRACE_DAYS` is exempt, because a fleet is built before it is
    used and a check that fires on the day you create a teammate teaches you to create fewer.
    """
    c = "g19-fleet-earning-its-keep"
    rows = dated_children(root / "utilization", ".json")
    if not rows:
        return result(
            c, ERROR, "no utilization run — bin/gb-utilization.py has never run"
        )
    doc = load(rows[-1]) or {}
    bots = doc.get("bots") or []
    if not bots:
        return result(
            c, ERROR, f"{rows[-1].name} lists no Bots — the measurement read nothing"
        )
    # Only Bots that have existed long enough to have been used are judged.
    stale = [
        b["name"]
        for b in bots
        if b.get("idle_credentialed")
        and (
            b.get("last_active_days") is None
            or b["last_active_days"] > FLEET_GRACE_DAYS
        )
    ]
    orph = doc.get("orphaned_entries") or 0
    note = (
        f" · {orph} orphaned transcript entries from deleted Bots remain on disk"
        if orph
        else ""
    )
    if stale:
        return result(
            c,
            RED,
            f"{len(stale)} Bot(s) hold shared-computer credentials with no routine "
            f"and no conversation for >{FLEET_GRACE_DAYS}d: {', '.join(stale)} — "
            f"give each a job or delete it{note}",
            idle=stale,
        )
    idle_now = doc.get("idle_credentialed") or []
    return result(
        c,
        GREEN,
        f"{len(bots)} Bot(s), {doc.get('routines_total', 0)} routine(s), "
        f"{doc.get('live_entries', 0)} live transcript entries; "
        f"{len(idle_now)} idle but inside the {FLEET_GRACE_DAYS}d grace{note}",
    )


def g20_context_archived(root: pathlib.Path, now: dt.date) -> dict:
    """The fleet's conversation history is archived before the client drops it.

    Two measured loss paths, and the continuous one is the dangerous one:

      deletion  a rebuilt or deleted Bot orphans its replica (1135 entries, 2026-09-11)
      rotation  the desktop keeps ~200 entries per Bot and silently drops the oldest past that.
                Four replicas sat at EXACTLY 200 — a cap, not a coincidence. This one loses
                history on a perfectly healthy fleet, forever, with no event to notice.

    Memory cannot be written back (`PutGrokBotMemoryShard` → "the agent's memory is owned by its
    Temporal turns; the box cannot write it"), so an archive is not one copy of two — past the
    cap it is the ONLY copy. A Bot at the cap with a stale archive is actively losing history
    right now, which is why that case is RED on its own regardless of the calendar.
    """
    c = "g20-context-archived"
    rows = dated_children(root / "archive", ".json")
    if not rows:
        return result(c, ERROR, "no context archive — run bin/gb-context-archive.py")
    man = load(rows[-1]) or {}
    if not man.get("rows"):
        return result(
            c, ERROR, f"{rows[-1].name} lists no replicas — the archive read nothing"
        )
    try:
        age = (now - dt.date.fromisoformat(rows[-1].name[:10])).days
    except ValueError:
        return result(
            c, ERROR, f"archive manifest {rows[-1].name} has no readable date"
        )
    at_cap = man.get("at_cap") or []
    if at_cap and age > MAX_ARCHIVE_AGE_DAYS:
        return result(
            c,
            RED,
            f"{len(at_cap)} replica(s) are AT the {man.get('client_cap')}-entry "
            f"client cap and the archive is {age}d old — every new turn is "
            f"dropping an old one that exists nowhere else",
            at_cap=at_cap,
            age=age,
        )
    if age > MAX_ARCHIVE_AGE_DAYS:
        return result(
            c,
            RED,
            f"context archive is {age}d old (ceiling {MAX_ARCHIVE_AGE_DAYS}d) — "
            f"run bin/gb-context-archive.py",
        )
    return result(
        c,
        GREEN,
        f"{man.get('replicas')} replica(s) / {man.get('entries_total')} entries "
        f"archived {age}d ago ({len(at_cap)} at the client cap, "
        f"{man.get('orphaned_entries', 0)} from deleted Bots, recoverable via "
        f"bin/gb-handover.py)",
    )


def g21_usecase_corpus_reviewed(root: pathlib.Path) -> dict:
    """What the population builds moved, and somebody read the diff.

    The rest of this board watches what the vendor ships and what this account holds. This
    watches what everyone ELSE is doing with the product — 645 attributed Bot definitions with
    structured integrations, refreshed weekly. A new integration appearing across a dozen Bots is
    the earliest honest signal that a capability is worth having, and it arrives long before the
    vendor documents it.

    Reviewed means `usecases-reviewed.json` names the current corpus snapshot. An empty corpus is
    an ERROR, never "the ecosystem went quiet" — the same positive-control rule discovery uses.
    """
    c = "g21-usecase-corpus-reviewed"
    rows = dated_children(root / "usecases", ".json")
    if not rows:
        return result(c, ERROR, "no use-case corpus — run bin/gb-usecases.py")
    cur = load(rows[-1]) or {}
    if not cur.get("bots"):
        return result(
            c,
            ERROR,
            f"{rows[-1].name} holds zero Bot definitions — a failed fetch is "
            f"not an empty ecosystem",
        )
    rec = load(root / "usecases-reviewed.json") or {}
    if rec.get("snapshot") == rows[-1].name:
        gap = [g for g in (cur.get("integration_gap") or []) if not g.get("installed")]
        return result(
            c,
            GREEN,
            f"corpus reviewed at {rows[-1].name}: {cur['bots']} Bot "
            f"definitions, {len(cur.get('integrations') or [])} integrations, "
            f"{len(gap)} of the top 25 not installed here",
        )
    if len(rows) == 1:
        return result(
            c,
            GREEN,
            f"first corpus snapshot ({cur['bots']} Bots) — nothing to diff yet",
        )
    prev = load(rows[-2]) or {}
    pn, cn = (
        {b["name"] for b in (prev.get("rows") or [])},
        {b["name"] for b in (cur.get("rows") or [])},
    )
    pi = {k for k, _ in (prev.get("integrations") or [])}
    ci = {k for k, _ in (cur.get("integrations") or [])}
    new_bots, new_integ = sorted(cn - pn), sorted(ci - pi)
    if not (new_bots or new_integ):
        return result(
            c, GREEN, f"corpus unchanged since {rows[-2].name} ({cur['bots']} Bots)"
        )
    return result(
        c,
        RED,
        f"{len(new_bots)} new Bot definition(s) and {len(new_integ)} new integration(s) since "
        f"{rows[-2].name}"
        + (f" — new integrations: {', '.join(new_integ[:8])}" if new_integ else "")
        + f". Read `USE-CASES.md` and record {rows[-1].name} in usecases-reviewed.json",
        new_integrations=new_integ[:20],
        new_bots=new_bots[:20],
    )


def _scan_calls(
    tree: object, filename: str, raw_writes: list[str], unbounded: list[str]
) -> None:
    """Classify every call in one parsed producer into the two durability smells g22 names.

    Split out of `g22_durable_io` so the RULES are readable as a list of four `elif`s rather
    than buried three levels inside a file loop. Appends into the caller's lists: the caller
    owns the accumulation, this owns the classification.
    """
    import ast as _ast

    for node in _ast.walk(tree):  # type: ignore[arg-type]
        if not isinstance(node, _ast.Call):
            continue
        func = node.func
        if isinstance(func, _ast.Attribute):
            name = func.attr
            base = getattr(func.value, "id", "")
        else:
            name = getattr(func, "id", "")
            base = ""
        line = f"{filename}:{node.lineno}"
        timed = any(kw.arg == "timeout" for kw in node.keywords)
        if name in ("write_text", "write_bytes"):
            raw_writes.append(line)
        elif name == "dump" and base == "json":
            raw_writes.append(line)
        elif (
            base == "subprocess"
            and name in ("run", "check_output", "check_call")
            and not timed
        ):
            unbounded.append(line)
        elif (
            isinstance(func, _ast.Attribute)
            and name in ("communicate", "wait")
            and not timed
        ):
            unbounded.append(line)


CANONICAL_VERBS = {
    "doctor",
    "health",
    "repair",  # the mandatory triad
    "validate",
    "audit",
    "why",  # the subsidiary triad (this repo writes artifacts)
    "capabilities",
    "robot-docs",
    "quickstart",
    "examples",
    "info",
    "help",
    "completion",
    "triage",  # the mega-command
    # `setup` is canonical, not a convenience. Measured 2026-09-11: a fresh public clone had no
    # artifacts, so `gb triage` printed 22 ERROR rows and exited 1 — one state (nothing measured
    # yet) reported 22 times, indistinguishable from a broken tool, with no route out. `setup` is
    # that route, and `triage`/`health`/`work`/`doctor` name it by hand when they detect an
    # unconfigured root. Deleting it restores the wall, which is exactly the class of regression
    # this check exists to catch.
    "setup",
    # `platform` is canonical for the same reason `setup` is, one wave later. Measured
    # 2026-09-11: four files hardcoded `~/Library/Application Support/Grok Bot`, two shelled out
    # to `security find-generic-password`, one drove `launchctl`, and
    # `grep -rl 'sys.platform\|platform.system' bin/` returned ZERO — so a Windows or Linux
    # installer got the wave-4 check wall back through a new door, with no sentence anywhere in
    # the tool saying "this operating system is not verified". `gb platform` is that sentence,
    # and `route_unconfigured`, `gb setup`'s platform row, `gb-deployment-audit` and
    # `gb-pull-inventory` all name it in their remediations. Deleting it leaves four dead ends.
    "platform",
}


def g24_cli_contract(root: pathlib.Path) -> dict:
    """`bin/gb` still exposes the canonical operator surface, and every verb is wired.

    Measured 2026-09-11: this repo had 22 producers in `bin/` and no CLI. Each script answered
    `--help`, so each was fine in isolation; nothing could answer "what is wrong right now",
    because nothing spoke for the whole system. `gb` is that surface, and this check is what
    stops it decaying back into a directory.

    Static, over the AST — the same posture as g22, so it costs one parse rather than a
    subprocess per fixture. It asserts three things:

      (1) every canonical verb is still registered (a deletion is a contract break, not a
          refactor);
      (2) every registered command has a handler — adding to COMMANDS without adding to
          HANDLERS is a KeyError the moment an agent types the verb, and nothing else catches
          it until then;
      (3) the exit-code dictionary is still populated, because an undocumented exit code is
          indistinguishable from a made-up one on the receiving end.

    The RUNTIME half of this contract (stdout parses, exit codes are right, typos get
    corrected) is proven by execution in `bin/gbtypes-selftest.py` t8, not here — a gate that
    shells out 53 times to prove it would cost more than it is worth.
    """
    c = "g24-cli-contract"
    import ast as _ast

    cli = (
        (root / "bin" / "gb")
        if (root / "bin" / "gb").is_file()
        else (pathlib.Path(__file__).resolve().parent / "gb")
    )
    if not cli.is_file():
        return result(
            c, ERROR, "bin/gb is absent — the producer family has no entry point"
        )
    if not os.access(cli, os.X_OK):
        return result(
            c, RED, f"{cli.name} is not executable — an agent cannot invoke it"
        )
    try:
        tree = _ast.parse(cli.read_text(errors="replace"))
    except SyntaxError as exc:
        return result(
            c, ERROR, f"bin/gb does not parse ({exc.msg}) — cannot audit the contract"
        )

    def keys_of(name: str) -> set:
        for node in tree.body:
            targets = (
                node.targets
                if isinstance(node, _ast.Assign)
                else ([node.target] if isinstance(node, _ast.AnnAssign) else [])
            )
            if isinstance(node, (_ast.Assign, _ast.AnnAssign)) and any(
                getattr(t, "id", "") == name for t in targets
            ):
                val = node.value
                if isinstance(val, _ast.Dict):
                    return {k.value for k in val.keys if isinstance(k, _ast.Constant)}
        return set()

    commands, handlers, codes = (
        keys_of("COMMANDS"),
        keys_of("HANDLERS"),
        keys_of("EXIT_CODES"),
    )
    problems = []
    missing = sorted(CANONICAL_VERBS - commands)
    if missing:
        problems.append(f"{len(missing)} canonical verb(s) gone: {', '.join(missing)}")
    unwired = sorted(commands - handlers)
    if unwired:
        problems.append(
            f"{len(unwired)} command(s) registered with no handler "
            f"(KeyError on first use): {', '.join(unwired)}"
        )
    if len(codes) < 5:
        problems.append(
            f"the exit-code dictionary has {len(codes)} entries — an undocumented "
            f"exit code cannot be branched on"
        )
    if problems:
        return result(
            c,
            RED,
            "; ".join(problems) + " — see `gb capabilities --json`",
            missing_verbs=missing,
            unwired=unwired,
        )
    return result(
        c,
        GREEN,
        f"bin/gb exposes {len(commands)} verbs including the full canonical "
        f"surface; all wired; {len(codes)} exit codes documented",
    )


def g22_durable_io(root: pathlib.Path) -> dict:
    """Every producer writes artifacts atomically and waits on children with a deadline.

    Measured 2026-09-11: 43 artifact writes in `bin/`, zero atomic. `pathlib.write_text` truncates
    the file and then writes it, so a tick killed in that window (Ctrl-C, sleep, the scheduler's
    SIGTERM) leaves a half-written JSON. Every reader here funnels through `load()`, which maps
    unparseable to None, which every gate reads as "not measured" — so an interrupted run does not
    merely fail, it ERASES a measurement and reports the erasure as an absence. That is the exact
    confusion this board exists to prevent, arriving through the back door.

    The same argument applies to a child process with no deadline: a hung fetch hangs the tick
    forever, and the run that never finishes writes nothing and reports nothing.

    This is a STATIC check over the source text parsed as an AST — it reads the tree as data and
    never imports or executes a producer, so it is safe in a fixture sweep. Two rules:

      (1) no `X.write_text/​write_bytes(...)` and no `json.dump(..., fh)` in `bin/` — the atomic
          primitive in `gbtypes.py` is the only sanctioned writer;
      (2) no BLOCKING wait on a child without a deadline: `subprocess.run/check_output/check_call`
          and `.communicate()/.wait()` must all carry `timeout=`.

    Rule 2 deliberately does NOT flag `subprocess.Popen` itself. `Popen` does not block — it
    spawns and returns, and it has no `timeout` parameter to pass. The hang lives in whatever
    waits afterwards, so that is what the rule checks. Flagging `Popen` would have been a rule
    the correct code could not satisfy, which teaches people to add exemptions instead of
    deadlines. (Caught 2026-09-11 by this check firing on `gbtypes.run`, the one function in the
    tree that already enforces a deadline properly.)

    The two spine files — `gbtypes.py` and `gbtypes-selftest.py` — are exempt from BOTH static
    rules. `gbtypes.py` IS the implementation; the selftest's known-bad legs must perform a naive
    write and an unbounded wait in order to prove those hazards are real. They are not unchecked:
    they are checked by EXECUTION instead, in `bin/gbtypes-selftest.py` (6 proofs, each with a
    known-bad leg that must fire). Two named files, not a pattern anyone can opt into.
    """
    c = "g22-durable-io"
    import ast as _ast

    spine = {"gbtypes.py", "gbtypes-selftest.py"}
    # This check grades THIS REPO'S SOURCE, not the artifact root it is pointed at — the write
    # path is a property of the producers, not of a deployment snapshot. A fixture may still
    # plant its own `bin/` to exercise the RED leg; when it does, that wins. Without the
    # override the check would be unfixtureable, and an unfixtureable check is unproven.
    bin_dir = (
        root / "bin"
        if (root / "bin").is_dir()
        else pathlib.Path(__file__).resolve().parent
    )
    sources = sorted(p for p in bin_dir.glob("*.py"))
    if not sources:
        return result(
            c,
            ERROR,
            f"{bin_dir} holds no python producers — an empty scan set is never a pass",
        )

    raw_writes: list[str] = []
    unbounded: list[str] = []
    for path in sources:
        if path.name in spine:
            continue
        try:
            tree = _ast.parse(path.read_text(errors="replace"))
        except SyntaxError as exc:
            return result(
                c, ERROR, f"{path.name} does not parse ({exc.msg}) — cannot audit it"
            )
        _scan_calls(tree, path.name, raw_writes, unbounded)

    problems = []
    if raw_writes:
        problems.append(
            f"{len(raw_writes)} non-atomic write(s): {', '.join(raw_writes[:6])}"
            + (" ..." if len(raw_writes) > 6 else "")
        )
    if unbounded:
        problems.append(
            f"{len(unbounded)} child process(es) with no timeout: {', '.join(unbounded[:6])}"
            + (" ..." if len(unbounded) > 6 else "")
        )
    if problems:
        return result(
            c,
            RED,
            "; ".join(problems) + " — route writes through gbtypes.atomic_write_* "
            "and give every subprocess a deadline",
            raw_writes=raw_writes[:40],
            unbounded=unbounded[:40],
        )
    return result(
        c,
        GREEN,
        f"{len(sources)} producer(s): every artifact write is atomic and every "
        f"child process carries a deadline",
    )


def g23_types_ratcheted(root: pathlib.Path) -> dict:
    """The typed surface is verified by two checkers, and it never shrinks.

    These types are not documentation — they are the contract the Rust port will be transliterated
    from, and the safety properties (`Verdict` cannot be a bare string, arguments arrive frozen,
    a deadline cannot be omitted) hold only while something checks them. An annotation nobody
    verifies is a comment that looks like a guarantee.

    Two checkers because they disagree: measured 2026-09-11, only mypy caught an `__exit__`
    returning `bool`, and only pyright named the missing `__dataclass_fields__` on an unbounded
    TypeVar. Agreement across two lineages is the evidence; one checker is one opinion.

    A checker that did not run is ERROR, never GREEN — `mypy: not installed` means the property
    is UNMEASURED, and this board's founding rule is that unmeasured never rounds up to clean.
    A floor that got smaller is RED: the ratchet only turns one way.
    """
    c = "g23-types-ratcheted"
    rows = dated_children(root / "typecheck", ".json")
    if not rows:
        return result(c, ERROR, "no typecheck artifact — run bin/gb-typecheck.py")
    cur = load(rows[-1]) or {}
    runs = cur.get("runs") or []
    if not runs:
        return result(
            c,
            ERROR,
            f"{rows[-1].name} records no checker run — an empty result set is never a pass",
        )
    unran = [r.get("tool", "?") for r in runs if not r.get("ran")]
    if unran:
        return result(
            c,
            ERROR,
            f"checker(s) did not run: {', '.join(unran)} — the typed surface is "
            f"UNMEASURED, which is not the same as clean",
        )
    missing = cur.get("missing_from_disk") or []
    if missing:
        return result(
            c,
            RED,
            f"{len(missing)} file(s) on the type floor no longer exist: "
            f"{', '.join(missing[:5])} — a file cannot leave the ratchet by being deleted",
        )
    failing = [
        (r.get("tool"), int(r.get("error_count") or 0))
        for r in runs
        if int(r.get("error_count") or 0)
    ]
    if failing:
        worst = max(failing, key=lambda kv: kv[1])
        sample = next(
            (r.get("errors") or [""] for r in runs if r.get("tool") == worst[0]), [""]
        )[0]
        return result(
            c,
            RED,
            f"{worst[0]} reports {worst[1]} error(s) on the type floor "
            f"({', '.join(f'{t}={n}' for t, n in failing)}): {sample[:120]}",
            failing=dict(failing),
        )
    if len(rows) > 1:
        prev = load(rows[-2]) or {}
        prev_size, cur_size = (
            int(prev.get("floor_size") or 0),
            len(cur.get("checked") or []),
        )
        if cur_size < prev_size:
            return result(
                c,
                RED,
                f"the type floor shrank from {prev_size} to {cur_size} file(s) — "
                f"the ratchet only turns one way",
            )

    # "mypy 1.11.0 (compiled: yes)" -> "mypy 1.11.0": the second token is the version; the tail
    # is a build note, and `.split()[-1]` rendered it as "mypy yes)".
    def _ver(row: dict) -> str:
        parts = str(row.get("version") or "").split()
        return (
            f"{row.get('tool')} {parts[1]}" if len(parts) > 1 else str(row.get("tool"))
        )

    tools = ", ".join(_ver(r) for r in runs)
    return result(
        c,
        GREEN,
        f"{len(cur.get('checked') or [])} file(s) clean under {len(runs)} "
        f"independent checker(s) ({tools})",
    )


def g7_desktop_parity(root: pathlib.Path, audit: dict | None, now: dt.date) -> dict:
    """Every registered desktop runs the same client, and was measured recently.

    Auto-review rules and Execution-on-Local-Computer are stored on the desktop that set them
    and synced only to that desktop's computer (docs.x.ai/grok-bot/security). A second Mac is a
    second deployment, so a desktop nobody measured is an unknown, not a pass.
    """
    c = "g7-desktop-parity"
    reg = load(root / "desktops.json")
    if not reg or not reg.get("desktops"):
        return result(
            c,
            ERROR,
            "desktops.json missing or empty — cannot know which desktops exist",
        )
    if not audit:
        return result(c, ERROR, "no host-of-record audit to compare desktops against")
    hor_label = reg.get("host_of_record")
    by_label = {d["label"]: d for d in reg["desktops"]}
    hor = by_label.get(hor_label)
    if not hor:
        return result(
            c, ERROR, f"host_of_record {hor_label!r} is not in the desktops list"
        )
    if audit.get("host") != hor.get("hostname"):
        return result(
            c,
            ERROR,
            f"host-of-record audit was captured on {audit.get('host')!r}, "
            f"but desktops.json says {hor_label} is {hor.get('hostname')!r}",
        )
    want = (audit.get("app") or {}).get("version")
    problems, seen = [], []
    for label, d in by_label.items():
        if label == hor_label:
            continue
        files = [
            f
            for f in dated_children(root / "deployment" / "desktops", ".json")
            if f.name.endswith(f".{label}.json")
        ]
        if not files:
            problems.append(f"{label}: never audited (bin/gb-audit-remote.sh {label})")
            continue
        newest = files[-1]
        age = (now - dt.date.fromisoformat(newest.name[:10])).days
        if age > MAX_SNAPSHOT_AGE_DAYS:
            problems.append(
                f"{label}: last audit {age}d old (ceiling {MAX_SNAPSHOT_AGE_DAYS}d)"
            )
            continue
        got = ((load(newest) or {}).get("app") or {}).get("version")
        seen.append(f"{label}={got}")
        if got != want:
            problems.append(f"{label}: client {got}, host of record {want}")
    if problems:
        return result(c, RED, "; ".join(problems), host_of_record=want)
    return result(
        c,
        GREEN,
        f"{len(by_label)} desktops on {want}"
        + (f" ({', '.join(seen)})" if seen else ""),
    )


def inventories(root: pathlib.Path) -> dict[str, tuple[pathlib.Path, dict]]:
    """Newest recorded hand-inventory per device label."""
    out: dict[str, tuple[pathlib.Path, dict]] = {}
    d = root / "inventory"
    if not d.is_dir():
        return out
    for f in sorted(
        c for c in d.iterdir() if c.suffix == ".json" and c.name[:10].count("-") == 2
    ):
        doc = load(f)
        if not doc:
            continue
        label = doc.get("device") or f.name.split(".")[-2]
        out[label] = (f, doc)  # sorted ascending, so the last write wins
    return out


def g8_desktop_inventory(root: pathlib.Path, now: dt.date) -> dict:
    """Every registered desktop has a fresh record of the ONE thing no API will answer.

    That is now a single field. Execution on Local Computer is set per desktop and nothing serves
    it back — probed across all six declared services on 2026-09-11. Everything else this check
    used to demand by hand is machine-read by `gb-pull-inventory.py`: routines, skills, MCP
    servers, plugins (user-level), registered computers, and the Auto-review rules themselves.

    The Auto-review rules were the expensive mistake. They are ACCOUNT-level and the server
    returns all five verbatim, so this check spent two days telling Joshua that Studio's rules
    were "not checked" and asking him to re-enter rules that already applied there (NE-9). It now
    verifies them from the API and only reports a desktop as unmeasured for `local_exec_policy`.
    """
    c = "g8-desktop-inventory"
    reg = load(root / "desktops.json")
    if not reg or not reg.get("desktops"):
        return result(
            c,
            ERROR,
            "desktops.json missing or empty — cannot know which desktops to record",
        )
    have = inventories(root)
    if not have:
        return result(
            c,
            ERROR,
            "no inventory recorded at all — run bin/gb-record-inventory.py --device <label> --template",
        )
    problems = []
    for d in reg["desktops"]:
        label = d["label"]
        if label not in have:
            problems.append(f"{label}: never recorded")
            continue
        f, doc = have[label]
        age = (now - dt.date.fromisoformat(f.name[:10])).days
        if age > MAX_INVENTORY_AGE_DAYS:
            problems.append(
                f"{label}: record is {age}d old (ceiling {MAX_INVENTORY_AGE_DAYS}d)"
            )
            continue
        if doc.get("local_exec_policy") is None:
            problems.append(f"{label}: local_exec_policy not recorded")
        else:
            # The hand record ages on its OWN date, not on the date of the pull that carried it.
            pr = doc.get("policy_recorded_at")
            if not pr:
                problems.append(
                    f"{label}: policy recorded with no date — cannot tell if it is current"
                )
            else:
                try:
                    if (now - dt.date.fromisoformat(pr)).days > MAX_INVENTORY_AGE_DAYS:
                        problems.append(
                            f"{label}: policy last confirmed {pr}, over "
                            f"{MAX_INVENTORY_AGE_DAYS}d ago — re-confirm in Settings"
                        )
                except ValueError:
                    problems.append(
                        f"{label}: policy_recorded_at {pr!r} is not an ISO date"
                    )
        # Account-level and server-authoritative. A per-device null here is a reading failure, not
        # a missing setting — hence the wording, and why it no longer names a device.
        if doc.get("auto_review_rules") is None:
            problems.append(
                "auto_review_rules unreadable from the API — unmeasured, not absent"
            )
        if doc.get("bots") is None:
            problems.append(f"{label}: bots/routines not recorded")
    if problems:
        return result(c, RED, "; ".join(problems))
    return result(
        c,
        GREEN,
        f"{len(reg['desktops'])} desktop(s) recorded within {MAX_INVENTORY_AGE_DAYS}d",
    )


def g25_routine_liveness(root: pathlib.Path, now: dt.date) -> dict:
    """A routine that is enabled and past due, with ZERO runs, is a dead scheduler.

    WHY THIS EXISTS, measured 2026-09-11. `g9-routine-health` reported GREEN on this account
    while 0 of 2 routines had ever fired. It is not a bug in g9 — g9 asks whether a routine is
    FAILING or silently paused, and branches on `runs is None` (not read) and on "enabled and
    every recent run failed". A routine whose `recent_runs` is the EMPTY LIST matches neither:
    it has never failed, because it has never run. The most important question about an
    always-on agent — did the schedule ever actually fire — had no check at all.

    Liveness, not health. The distinction earns its keep: a Bot can be perfectly healthy and
    completely inert, and only one of those two words was being measured.

    GREEN   every enabled routine has either run, or is not yet due
    RED     an enabled routine is past its own `next_run_at_ms` and still has zero runs
    ERROR   nothing has been read, so liveness is UNMEASURED — never reported as fine
    """
    c = "g25-routine-liveness"
    have = inventories(root)
    if not have:
        return result(c, ERROR, "no inventory to read routine liveness from (see g8)")

    now_ms = int(
        dt.datetime(now.year, now.month, now.day, tzinfo=dt.timezone.utc).timestamp()
        * 1000
    )
    dead: list[str] = []
    waiting = 0
    alive = 0
    seen: set[tuple] = set()

    # Routines are ACCOUNT-level: every desktop's artifact carries the same set, so they must be
    # deduped or a two-Mac fleet double-counts. But dedupe order is load-bearing and the obvious
    # `sorted(have.items())` is WRONG — it takes whichever LABEL sorts first ("brain" < "studio"),
    # not whichever READ is freshest. Measured here: Brain's older artifact said `recent_runs: []`
    # for `CFS morning check-in` while Studio's fresher one said `['ok']`, and this check reported
    # "0 routine(s) have run" about a routine that had demonstrably run. Newest artifact wins.
    def _freshness(item: tuple) -> str:
        _label, (path, _doc) = item
        return path.name

    for _label, (_f, doc) in sorted(have.items(), key=_freshness, reverse=True):
        for b in doc.get("bots") or []:
            for r in b.get("routines") or []:
                ident = (b.get("name"), r.get("name"))
                if ident in seen:
                    continue
                seen.add(ident)
                if not r.get("enabled"):
                    continue
                runs = r.get("recent_runs")
                if runs is None:
                    continue  # "not read" is g9's finding, not liveness
                if runs:
                    alive += 1
                    continue
                due = r.get("next_run_at_ms")
                if isinstance(due, (int, float)) and due < now_ms:
                    dead.append(
                        f"{b.get('name')}/{r.get('name')}: enabled, due, zero runs"
                    )
                else:
                    waiting += 1
    if dead:
        return result(
            c,
            RED,
            f"{len(dead)} routine(s) enabled and past due with no run ever: "
            + "; ".join(sorted(dead)[:4]),
        )
    if not seen:
        return result(c, ERROR, "no routines recorded at all — liveness is UNMEASURED")
    return result(
        c,
        GREEN,
        f"{alive} routine(s) have run, {waiting} enabled and not yet due, none dead",
    )


def g26_jobs_proof_calls(root: pathlib.Path) -> dict:
    """A jobs `done` receipt without a live Bot ask is a name-match lie.

    Measured 2026-09-12: `live_agent=1000` was a roster substring (`Vendor Watch`
    vs `vendor-watch`). `gb jobs prove` now spends `gb bot ask --expect`; this
    check grades the receipt. The gate does not talk to the vendor — it reads
    `jobs/walks.jsonl` already on disk.

    GREEN  every latest `done` row carries proof_ok true
    RED    a `done` row has no proof_ok (name-match / paste-only)
    ERROR  walks.jsonl absent — done-claims are UNMEASURED
    """
    c = "g26-jobs-proof-calls"
    live_root = pathlib.Path(__file__).resolve().parents[1]
    walks = root / "jobs" / "walks.jsonl"
    if not walks.is_file():
        if root.resolve() == live_root.resolve():
            return result(
                c,
                ERROR,
                "jobs/walks.jsonl absent — done-claims are UNMEASURED",
            )
        return result(
            c,
            GREEN,
            "fixture has no jobs receipts — no done claimed",
        )
    latest: dict[str, dict] = {}
    try:
        for line in walks.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict) and row.get("id"):
                latest[str(row["id"])] = row
    except (OSError, json.JSONDecodeError) as exc:
        return result(c, ERROR, f"jobs/walks.jsonl unreadable: {exc}")
    if not latest:
        return result(c, ERROR, "jobs/walks.jsonl empty — done-claims are UNMEASURED")
    bad = []
    for jid, row in sorted(latest.items()):
        if row.get("verdict") != "done":
            continue
        proven = row.get("proof")
        proof: dict = proven if isinstance(proven, dict) else {}
        if row.get("proof_ok") is not True or proof.get("rc") != 0:
            bad.append(f"{jid}: done without expect-match")
    if bad:
        return result(
            c,
            RED,
            f"{len(bad)} done receipt(s) without a Bot ask match: "
            + "; ".join(bad[:4]),
        )
    return result(
        c,
        GREEN,
        f"{sum(1 for r in latest.values() if r.get('verdict')=='done')} done "
        f"receipt(s) carry expect-match; {len(latest)} job(s) recorded",
    )


def _schema_method_sig(method: dict) -> tuple:
    """What counts as "the same method": input, output, kind, and requiredness.

    AMENDED 2026-09-12 (operator): a field flipping optional to required — or listed
    fields added/removed on the input shape — is a breaking change and must move the
    verdict on its own. Method records carry the input shape as `fields`, a list of
    `{name, req}` (req True means the caller must send it). Bundles mined before the
    amendment carry no `fields` at all; two records that both lack it compare exactly
    as they did before, so the amendment only adds a comparison, never redefines one.
    """
    raw = method.get("fields")
    if raw is None:
        shaped: tuple | None = None
    elif isinstance(raw, dict):
        shaped = tuple(sorted((name, bool(req)) for name, req in raw.items()))
    elif isinstance(raw, list):
        items: list[tuple] = []
        for field in raw:
            if isinstance(field, dict):
                items.append((field.get("name"), bool(field.get("req", False))))
            else:
                items.append((field, None))
        shaped = tuple(sorted(items, key=repr))
    else:
        shaped = (repr(raw),)
    return (method.get("input"), method.get("output"), method.get("kind"), shaped)


def _schema_methods(bundle: pathlib.Path) -> dict | None:
    """name -> signature for one mined bundle. None when the bundle is unreadable."""
    try:
        services = json.loads((bundle / "services.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(services, dict):
        return None
    out: dict = {}
    for records in services.values():
        if not isinstance(records, list):
            return None
        for method in records:
            if not isinstance(method, dict) or not isinstance(method.get("name"), str):
                return None
            out[method["name"]] = _schema_method_sig(method)
    return out


def _schema_counts(bundle: pathlib.Path) -> tuple | None:
    """(methods, services, messages, enums) for a GREEN detail line. None if unreadable."""
    try:
        services = json.loads((bundle / "services.json").read_text())
        messages = json.loads((bundle / "messages.json").read_text())
        enums = json.loads((bundle / "enums.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not all(isinstance(doc, dict) for doc in (services, messages, enums)):
        return None
    n_methods = 0
    for records in services.values():
        if not isinstance(records, list):
            return None
        n_methods += len(records)
    return (n_methods, len(services), len(messages), len(enums))


def _describe_sig_change(old: tuple, new: tuple) -> list:
    """input/output/kind moves, one fragment each."""
    bits = []
    for label, before, after in (
        ("input", old[0], new[0]),
        ("output", old[1], new[1]),
        ("kind", old[2], new[2]),
    ):
        if before != after:
            bits.append(f"{label} {before} -> {after}")
    return bits


def _describe_field_changes(old_shaped: tuple | None, new_shaped: tuple | None) -> list:
    """Listed-field adds/removes and requiredness flips, one fragment each."""
    bits = []
    before_fields = dict(old_shaped or [])
    after_fields = dict(new_shaped or [])
    for name in sorted(set(before_fields) | set(after_fields)):
        if name not in before_fields:
            state = "required" if after_fields[name] else "optional"
            bits.append(f"+field {name} ({state})")
        elif name not in after_fields:
            bits.append(f"-field {name}")
        elif before_fields[name] != after_fields[name]:
            was = "required" if before_fields[name] else "optional"
            now = "required" if after_fields[name] else "optional"
            bits.append(f"field {name} {was} -> {now}")
    return bits


def _describe_method_change(old: tuple, new: tuple) -> str:
    """One RED line for a changed method: which of input/output/kind/requiredness moved."""
    bits = _describe_sig_change(old, new) + _describe_field_changes(old[3], new[3])
    return "; ".join(bits) if bits else "identical"


def _schema_records(root: pathlib.Path) -> tuple[list, dict, dict] | dict:
    """The three records g27 grades, or the early verdict when they do not read.

    Success is (versions, reviewed, registry). Anything else is a ready-made
    result() dict — ERROR when drift is UNMEASURED, GREEN for the nothing-mined
    fixture leg — so the caller returns it untouched and keeps only the
    GREEN/RED drift assembly. Every detail string below moved verbatim here;
    the fixtures assert verdict text.
    """
    c = "g27-surface-drift"
    live_root = pathlib.Path(__file__).resolve().parents[1]
    schema_dir = root / SCHEMA_DIRNAME
    versions = (
        sorted(p.name for p in schema_dir.iterdir() if p.is_dir())
        if schema_dir.is_dir()
        else []
    )
    reviewed_path = schema_dir / SCHEMA_REVIEWED_FILENAME
    registry_path = schema_dir / SCHEMA_REGISTRY_FILENAME
    if not versions or not reviewed_path.is_file() or not registry_path.is_file():
        if root.resolve() == live_root.resolve():
            missing = sorted(
                name
                for name, present in (
                    ("bundles", bool(versions)),
                    (SCHEMA_REVIEWED_FILENAME, reviewed_path.is_file()),
                    (SCHEMA_REGISTRY_FILENAME, registry_path.is_file()),
                )
                if not present
            )
            return result(
                c,
                ERROR,
                "schema drift UNMEASURED — absent: " + ", ".join(missing),
            )
        return result(
            c, GREEN, "fixture has no schema bundles — nothing mined to grade"
        )
    try:
        reviewed = json.loads(reviewed_path.read_text())
        registry = json.loads(registry_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return result(c, ERROR, f"schema registry/reviewed unreadable: {exc}")
    if not isinstance(reviewed, dict) or not isinstance(reviewed.get("version"), str):
        return result(c, ERROR, "schema/reviewed.json unparseable: no version string")
    if (
        not isinstance(registry, dict)
        or not isinstance(registry.get("methods"), dict)
        or not isinstance(registry.get("messages"), dict)
    ):
        return result(
            c,
            ERROR,
            "schema/registry.json unparseable: need methods/messages maps",
        )
    return (versions, reviewed, registry)


def g27_surface_drift(root: pathlib.Path) -> dict:
    """The shipped client declares a different RPC surface than the one somebody reviewed.

    WHY THIS EXISTS, measured 2026-09-12. The vendor ships a new client, the bundle's
    proto surface moves, and nothing in the gate reads it — capability ids (g6) and
    tunable digests (g11) both move past a method rename without firing. The miner
    records each shipped bundle under schema/<ver>/ (sha256 in the manifest, never
    vendor bytes in the repo); this check grades the MINED drift only, never live
    reachability. The gate does not talk to the vendor — it reads bundles on disk.

    GREEN   the newest mined bundle is the reviewed one (with counts), or the newer
            bundle moves nothing method-shaped — messages/enums alone do not gate
    RED     the newest mined bundle is unreviewed AND a method was added, removed,
            or changed (same name, different input/output/kind/requiredness)
    ERROR   registry or reviewed missing/unparseable, or a named bundle unreadable —
            drift is UNMEASURED, never reported as "no drift"
    """
    c = "g27-surface-drift"
    records = _schema_records(root)
    if isinstance(records, dict):
        return records
    versions, reviewed, registry = records
    schema_dir = root / SCHEMA_DIRNAME
    newest = versions[-1]
    reviewed_ver = reviewed["version"]
    if newest == reviewed_ver:
        counts = _schema_counts(schema_dir / newest)
        if counts is None:
            return result(
                c, ERROR, f"schema/{newest} bundle unreadable — drift UNMEASURED"
            )
        n_methods, n_services, n_messages, n_enums = counts
        return result(
            c,
            GREEN,
            f"surface {newest} reviewed: {n_methods} methods across "
            f"{n_services} services, {n_messages} messages, {n_enums} enums",
        )
    old = _schema_methods(schema_dir / reviewed_ver)
    new = _schema_methods(schema_dir / newest)
    if old is None or new is None:
        return result(
            c,
            ERROR,
            f"schema bundle unreadable ({reviewed_ver} -> {newest}) — drift UNMEASURED",
        )
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(n for n in set(old) & set(new) if old[n] != new[n])
    if added or removed or changed:
        lines = (
            [f"+{n}" for n in added]
            + [f"-{n}" for n in removed]
            + [f"~{n} ({_describe_method_change(old[n], new[n])})" for n in changed]
        )
        return result(
            c,
            RED,
            f"surface drift {reviewed_ver} -> {newest}: " + "; ".join(lines[:4]),
        )
    return result(
        c,
        GREEN,
        f"surface {newest} unreviewed but method-identical to {reviewed_ver}",
    )


def g28_grokbotdev_fresh(root: pathlib.Path, now: dt.date) -> dict:
    """grokbot.dev lean feed+status was collected recently, without using MCP.

    Community-claim producer (NE-25: mcp.grokbot.dev is NXDOMAIN). g13 still
    judges RongleCat/awesome-grok-bot. This judges the JSON artifact only:
    missing/unparseable is ERROR; stale or mcp.used is RED. Never fetches.
    """
    c = "g28-grokbotdev-fresh"
    rows = dated_children(root / "grokbotdev", ".json")
    if not rows:
        return result(
            c,
            ERROR,
            "no grokbotdev artifact — bin/gb-grokbotdev.py has never run",
        )
    doc = load(rows[-1]) or {}
    if doc.get("schema") != "gb-grokbotdev/1":
        return result(
            c,
            ERROR,
            f"{rows[-1].name} missing schema gb-grokbotdev/1 — unmeasured, not empty",
        )
    items = doc.get("rows")
    if not isinstance(items, list):
        return result(
            c,
            ERROR,
            f"{rows[-1].name} has no rows list — empty scan is ERROR",
        )
    if doc.get("count") and not items:
        return result(
            c,
            ERROR,
            f"{rows[-1].name} count={doc.get('count')} but rows empty",
        )
    mcp = doc.get("mcp") if isinstance(doc.get("mcp"), dict) else {}
    if mcp.get("used") is True:
        return result(
            c,
            RED,
            f"{rows[-1].name} mcp.used=true — NE-25 forbids fetching mcp.grokbot.dev",
        )
    try:
        age = (now - dt.date.fromisoformat(rows[-1].name[:10])).days
    except ValueError:
        return result(c, ERROR, f"{rows[-1].name} has no readable date")
    if age > MAX_GROKBOTDEV_AGE_DAYS:
        return result(
            c,
            RED,
            f"grokbotdev artifact is {age}d old (ceiling {MAX_GROKBOTDEV_AGE_DAYS}d) — "
            f"run bin/gb-grokbotdev.py collect",
        )
    return result(
        c,
        GREEN,
        f"{len(items)} grokbot.dev rows captured {age}d ago "
        f"(mcp_used=false, epistemic={doc.get('epistemic', '?')})",
    )


def g9_routine_health(root: pathlib.Path, now: dt.date) -> dict:
    """No routine is silently failing, of unknown run state, or paused without a decision.

    A deliberate pause is a decision, not a defect — `pauses-acknowledged.json` records who
    decided, why, and the date it must be re-made. An acknowledged pause is GREEN until its
    `review_by`; after that it is RED again, because an exception that never expires is how a
    monitor goes quiet forever.
    """
    c = "g9-routine-health"
    have = inventories(root)
    if not have:
        return result(c, ERROR, "no inventory to read routine health from (see g8)")
    ackdoc = load(root / "pauses-acknowledged.json") or {}
    acks = {(e.get("bot"), e.get("routine")): e for e in ackdoc.get("acknowledged", [])}
    reviewed = {
        (e.get("bot"), e.get("routine")): e
        for e in ackdoc.get("self_created_reviewed", [])
    }
    # Routines are account-level, not per-device: the same set comes back on every desktop's
    # record. Dedupe by (bot, routine) or a two-Mac fleet double-counts every finding.
    bad, seen, acked = [], set(), 0
    for label, (_f, doc) in sorted(have.items()):
        for b in doc.get("bots") or []:
            for r in b.get("routines") or []:
                ident = (b.get("name"), r.get("name"))
                if ident in seen:
                    continue
                seen.add(ident)
                runs = r.get("recent_runs")
                name = f"{b.get('name')}/{r.get('name')}"
                if runs is None:
                    bad.append(f"{name}: run history not read")
                elif (
                    r.get("enabled")
                    and runs
                    and all(str(x).lower() in FAILED_RUN_STATES for x in runs)
                ):
                    bad.append(
                        f"{name}: every recorded run failed and it is still enabled"
                    )
                elif (
                    r.get("enabled") and r.get("provenance") == "untrusted" and not runs
                ):
                    rev = reviewed.get(ident)
                    if not rev:
                        # DOES NOT CLAIM AUTHORSHIP, corrected 2026-09-12. This message used to
                        # read "created by the Bot itself (provenance=untrusted)". That was an
                        # INFERENCE from two routines on a freshly rebuilt Bot, and a live test
                        # killed it: a Bot asked in chat to schedule itself produced
                        # provenance="user". So `untrusted` does NOT mean Bot-authored, and
                        # nothing recorded anywhere says who wrote a routine.
                        #
                        # The check still earns its place on what IS measured — enabled, zero
                        # runs, never reviewed, and a provenance nobody can attribute is a
                        # scheduler no human has signed off. The wording had already propagated
                        # the false claim into gb-findings.py, which cited THIS LINE as its
                        # authority, which is how one stale message becomes two public claims.
                        bad.append(
                            f"{name}: provenance=untrusted (authorship is NOT recorded), "
                            f"enabled, never run and never reviewed — confirm or delete it"
                        )
                    else:
                        try:
                            if now > dt.date.fromisoformat(rev.get("review_by", "")):
                                bad.append(
                                    f"{name}: unattributed routine accepted {rev.get('acked_at')} but "
                                    f"review_by {rev.get('review_by')} has passed — re-confirm"
                                )
                            else:
                                acked += 1
                        except ValueError:
                            bad.append(
                                f"{name}: self-created review has an unreadable review_by"
                            )
                elif r.get("enabled") is False:
                    ack = acks.get(ident)
                    if not ack:
                        bad.append(
                            f"{name}: paused with no recorded decision — resume it, delete it, "
                            f"or acknowledge it in pauses-acknowledged.json"
                        )
                    else:
                        try:
                            due = dt.date.fromisoformat(ack.get("review_by", ""))
                        except ValueError:
                            bad.append(
                                f"{name}: pause acknowledged with an unreadable review_by"
                            )
                            continue
                        if now > due:
                            bad.append(
                                f"{name}: pause acknowledged {ack.get('acked_at')} but review_by "
                                f"{ack.get('review_by')} has passed — re-decide or delete"
                            )
                        else:
                            acked += 1
    total = len(seen)
    if bad:
        return result(
            c,
            RED,
            f"{len(bad)} of {total} routine(s): " + "; ".join(bad[:6]),
            routines=total,
            findings=bad,
        )
    return result(
        c,
        GREEN,
        f"{total} routine(s) recorded, none failing or silently paused"
        + (
            f" ({acked} accepted by recorded decision, review by "
            f"{min(e['review_by'] for e in list(acks.values()) + list(reviewed.values()) if e.get('review_by'))})"
            if acked
            else ""
        ),
    )


def scheduler_cadence(fired: list[str], now: dt.date) -> list[str]:
    """Has the SCHEDULER itself run recently enough — independent of how often a human has.

    The old rule asked whether one of the last three runs came from launchd, and went RED on
    2026-09-11 because an agent ran the tick four times by hand while iterating on it. The
    scheduler was registered and had fired that morning. Manual runs are noise in this question,
    and a check a person breaks by testing the loop teaches people to stop testing the loop.
    """
    if not fired:
        return [
            "no run has ever come from the scheduler — registered but never fired, "
            "or only ever run by hand"
        ]
    newest = max(fired)
    try:
        age = (now - dt.date.fromisoformat(newest[:10])).days
    except ValueError:
        return [f"scheduler run timestamp {newest!r} is unreadable"]
    if age > MAX_RUN_GAP_DAYS:
        return [
            f"the scheduler last fired {newest[:10]}, {age}d ago "
            f"(ceiling {MAX_RUN_GAP_DAYS}d) — registered but not firing"
        ]
    return []


def g10_loop_scheduled(root: pathlib.Path, now: dt.date) -> dict:
    """Something other than a human actually runs this loop.

    Measured 2026-09-11: g3 enforced an 8-day freshness ceiling while exactly one snapshot existed,
    taken by hand, with no launchd agent and no cron entry on either machine. A staleness gate with
    no scheduler behind it does not detect decay — it waits to accuse someone. This check is the
    scheduler's own gate.
    """
    c = "g10-loop-scheduled"
    runs_path = root / "schedule" / "runs.jsonl"
    if not runs_path.is_file() or not runs_path.read_text().strip():
        return result(
            c,
            ERROR,
            "schedule/runs.jsonl absent or empty — the loop has never recorded a run",
        )
    runs = []
    for line in runs_path.read_text().splitlines():
        try:
            runs.append(json.loads(line))
        except Exception:
            continue
    if not runs:
        return result(c, ERROR, "schedule/runs.jsonl has no parseable run records")
    agent = load(root / "schedule" / "agent.json")
    problems = []
    if not agent or not agent.get("installed"):
        problems.append(
            "no scheduler registered (bin/gb-install-schedule.sh --install)"
        )
    newest = max(r.get("at", "") for r in runs)
    try:
        age = (now - dt.date.fromisoformat(newest[:10])).days
        if age > MAX_RUN_GAP_DAYS:
            problems.append(
                f"last run {newest[:10]} is {age}d ago (ceiling {MAX_RUN_GAP_DAYS}d)"
            )
    except ValueError:
        problems.append(f"newest run timestamp {newest!r} is unreadable")
    # Judge the SCHEDULER's cadence, not its share of recent rows. The old rule ("one of the last
    # 3 runs came from launchd") went RED on 2026-09-11 because an agent ran the tick four times
    # by hand while iterating — the scheduler was registered and had fired that morning. Manual
    # runs are noise in this question, and a check that a human can break by testing the loop
    # teaches people to stop testing the loop.
    fired = [r.get("at", "") for r in runs if r.get("source") == "launchd"]
    problems += scheduler_cadence(fired, now)
    if problems:
        return result(c, RED, "; ".join(problems), runs=len(runs))
    return result(
        c,
        GREEN,
        f"{len(runs)} recorded run(s), newest {newest[:10]}; scheduler last "
        f"fired {max(fired)[:10]} ({agent.get('schedule')})",
    )


def g29_dogfood_dispositions(root: pathlib.Path, now: dt.date) -> dict:
    """Every recorded dogfood gap carries exactly one structured disposition.

    A blanket `--accept` over gaps nobody disposed per gap is refused; a recorded
    reject stays RED until its gap closes; a defer past review_by is RED again.
    Reads the baseline artifact only — the CLI ratchet enforces the same rule at
    write time through the shared judge in `bin/gb-dogfood.py`, so the two can
    never disagree about a defer. Fixture-clocked: expiry reads the gate `--now`
    date, never the wall clock."""
    c = "g29-dogfood-dispositions"
    import importlib.util

    doc = load(root / "dogfood-baseline.json")
    if not doc:
        return result(
            c, ERROR, "no dogfood-baseline.json — run bin/gb-dogfood.py audit"
        )
    if doc.get("schema") not in ("gb-dogfood-baseline/1", "gb-dogfood-baseline/2"):
        return result(
            c, ERROR, f"dogfood-baseline schema {doc.get('schema')!r} unreadable"
        )
    gaps = sorted((doc.get("gaps") or {}).keys())
    decisions = [d for d in (doc.get("decisions") or []) if isinstance(d, dict)]
    decided = {str(d.get("gap")) for d in decisions if d.get("gap")}
    blanket = sorted(
        {
            str(k)
            for a in (doc.get("acceptances") or [])
            if isinstance(a, dict)
            for k in (a.get("keys") or [])
            if str(k) not in decided
        }
    )
    spec = importlib.util.spec_from_file_location(
        "gbdogfood", pathlib.Path(__file__).resolve().parent / "gb-dogfood.py"
    )
    if spec is None or spec.loader is None:
        return result(
            c, ERROR, "bin/gb-dogfood.py is missing — cannot judge dispositions"
        )
    mod = importlib.util.module_from_spec(spec)
    try:
        # dataclass string annotations resolve through sys.modules at class
        # creation: without this registration the exec dies with
        # AttributeError: 'NoneType' object has no attribute '__dict__'.
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    except Exception as e:
        return result(c, ERROR, f"bin/gb-dogfood.py failed to load ({e})")
    life = mod.evaluate_dispositions(decisions, gaps, now)
    problems = []
    if blanket:
        problems.append(
            f"{len(blanket)} gap(s) under blanket free-form acceptance: "
            + ", ".join(blanket[:6])
            + (" ..." if len(blanket) > 6 else "")
        )
    for key in ("undisposed", "rejected_open", "expired", "invalid"):
        rows = life.get(key) or []
        if rows:
            problems.append(f"{key} {len(rows)}: " + ", ".join(rows[:6]))
    if problems:
        return result(
            c,
            RED,
            "; ".join(problems)
            + " — dispose each gap: --dispose KEY=adopt|reject|defer",
            undisposed=life.get("undisposed"),
            rejected_open=life.get("rejected_open"),
            expired=life.get("expired"),
            invalid=life.get("invalid"),
            blanket=blanket,
        )
    return result(
        c,
        GREEN,
        f"{len(gaps)} recorded gap(s), each with one valid disposition "
        f"({len([d for d in decisions if d.get('disposition') == 'adopt'])} adopt, "
        f"{len([d for d in decisions if d.get('disposition') == 'reject'])} reject, "
        f"{len([d for d in decisions if d.get('disposition') == 'defer'])} defer)",
    )


def g30_score_ordering(root: pathlib.Path, now: dt.date) -> dict:
    """Recorded dogfood points recompute from the disclosed weights, in order.

    Points are ordinal (bead .7): the table supervises ORDER, never distances.
    This check re-derives every verifiable recorded score from the current
    KIND/SIGNAL weights and requires the recorded rank to agree with the
    deterministic re-rank. A swapped or hand-edited score breaks pairs and goes
    RED; rows predating the kind+signals breakdown are unverifiable (ERROR when
    nothing is verifiable — re-record the baseline). Shares the dogfood loader
    with g29. `now` is accepted for check-signature uniformity and unused: the
    ladder has no clock in it."""
    c = "g30-score-ordering"
    import importlib.util

    _ = now
    doc = load(root / "dogfood-baseline.json")
    if not doc:
        return result(
            c, ERROR, "no dogfood-baseline.json — run bin/gb-dogfood.py audit"
        )
    rows = doc.get("gaps") or {}
    verifiable = {
        k: v
        for k, v in rows.items()
        if isinstance(v, dict) and "kind" in v and "signals" in v
    }
    if not verifiable:
        return result(
            c,
            ERROR,
            "no recorded gap carries kind+signals — re-record the baseline; "
            "ordering is UNMEASURED on pre-breakdown rows",
        )
    spec = importlib.util.spec_from_file_location(
        "gbdogfood", pathlib.Path(__file__).resolve().parent / "gb-dogfood.py"
    )
    if spec is None or spec.loader is None:
        return result(c, ERROR, "bin/gb-dogfood.py is missing — cannot recompute")
    mod = importlib.util.module_from_spec(spec)
    try:
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    except Exception as e:
        return result(c, ERROR, f"bin/gb-dogfood.py failed to load ({e})")

    def recompute(v: dict) -> int:
        sig = v.get("signals") or {}
        return int(mod.KIND_WEIGHT.get(v.get("kind"), 0)) + sum(
            int(mod.SIGNAL_WEIGHT.get(k, 0)) * int(sig.get(k) or 0)
            for k in ("importers", "callers", "docs", "tick", "public")
        )

    mist = sorted(k for k, v in verifiable.items() if recompute(v) != v.get("score"))
    keys = sorted(verifiable)
    inversions = sorted(
        f"{a}>{b}"
        for i, a in enumerate(keys)
        for b in keys[i + 1 :]
        if ((verifiable[a].get("score") or 0) > (verifiable[b].get("score") or 0))
        != (recompute(verifiable[a]) > recompute(verifiable[b]))
    )
    problems = []
    if mist:
        problems.append(f"scores do not recompute: {', '.join(mist[:6])}")
    if inversions:
        problems.append(
            f"recorded rank inverts recomputation: {', '.join(inversions[:6])}"
        )
    if problems:
        return result(
            c,
            RED,
            "; ".join(problems) + " — points are ordinal; tampering is not measurement",
            mist=mist,
            inversions=inversions,
        )
    return result(
        c,
        GREEN,
        f"{len(verifiable)} recorded score(s) recompute and rank clean "
        f"({len(rows) - len(verifiable)} pre-breakdown rows skipped)",
    )


def advisories(audit: dict | None) -> list[str]:
    """Non-gating rows. Real work, but not grounds to block a tick."""
    if not audit:
        return []
    out = []
    bots = audit.get("bots") or []
    stale = [b for b in bots if (b.get("idle_days") or 0) > STALE_BOT_DAYS]
    if stale:
        out.append(
            "idle >21d: "
            + ", ".join(f"{b['name']}({b['idle_days']}d)" for b in stale[:8])
        )
    # REMOVED 2026-09-11 (NE-6): this advisory read `notificationsEnabled` from the desktop roster
    # blob and reported "15/15 Bots have notifications off" for days. Joshua confirmed every Bot's
    # Notifications switch is ON, on a cache 39 seconds old, on the machine he was sitting at. The
    # field is not a readable mirror of that switch, and the server roster does not carry it at
    # all — so there is NO trustworthy source and the honest move is to say nothing rather than
    # nag with a false finding. Restore only if a real read appears.
    unread = [b for b in bots if (b.get("unread_count") or 0) > 0]
    if unread:
        out.append(
            "unread backlog: "
            + ", ".join(f"{b['name']}={b['unread_count']}" for b in unread[:8])
        )
    g = audit.get("gates") or {}
    if g.get("undecoded_enabled"):
        out.append(
            f"{g['undecoded_enabled']} enabled capabilities could not be named "
            f"({g.get('decoded')} of {g.get('total')} names recovered from app.asar)"
        )
    return out


def run(root: pathlib.Path, now: dt.date, disabled: set[str]) -> dict:
    surfaces = dated_children(root / "surface")
    snap = surfaces[-1] if surfaces else None
    audits = dated_children(root / "deployment", ".json")
    audit_path = audits[-1] if audits else None
    audit = load(audit_path) if audit_path else None
    baseline = reviewed_baseline(root, audits)

    results = [
        g1_surface_fetch_integrity(snap),
        g2_surface_canary(snap),
        g3_snapshot_freshness(snap, now),
        g4_client_update_applied(audit),
        g5_bot_charter_present(audit, root),
        g6_capability_delta_reviewed(audit, baseline, audit_path, root, snap),
        g7_desktop_parity(root, audit, now),
        g8_desktop_inventory(root, now),
        g9_routine_health(root, now),
        g10_loop_scheduled(root, now),
        g11_tunable_delta_reviewed(audit, baseline, audit_path, root, snap),
        g12_ondemand_spend_bounded(root, now),
        g13_practitioner_index_reviewed(root, snap),
        g14_coverage_ratchet(root),
        g15_market_delta_reviewed(root, snap),
        g16_doc_delta_reviewed(root),
        g17_discovery_triaged(root),
        g18_mcp_surface_healthy(root),
        g19_fleet_earning_its_keep(root),
        g20_context_archived(root, now),
        g21_usecase_corpus_reviewed(root),
        g22_durable_io(root),
        g23_types_ratcheted(root),
        g24_cli_contract(root),
        g25_routine_liveness(root, now),
        g26_jobs_proof_calls(root),
        g27_surface_drift(root),
        g28_grokbotdev_fresh(root, now),
        g29_dogfood_dispositions(root, now),
        g30_score_ordering(root, now),
    ]
    results = [r for r in results if r["check"] not in disabled]
    verdict = GREEN
    if any(r["verdict"] == ERROR for r in results):
        verdict = ERROR
    elif any(r["verdict"] == RED for r in results):
        verdict = RED
    if not results:
        verdict = ERROR  # every check disabled is not a pass
    return {
        "gate": "gb-surface-gate",
        "version": VERSION,
        "root": str(root),
        "reviewed_baseline": baseline.name if baseline else None,
        "disabled": sorted(disabled),
        "snapshot": snap.name if snap else None,
        "deployment": audit_path.name if audit_path else None,
        "verdict": verdict,
        "exit_code": EXIT[verdict],
        "checks": results,
        "advisories": advisories(audit),
    }


def verdict_line(out: dict) -> str:
    """Render normal and early-ERROR envelopes without assuming artifact ids."""
    context = []
    if out.get("snapshot") is not None:
        context.append("snapshot %s" % out["snapshot"])
    if out.get("deployment") is not None:
        context.append("deployment %s" % out["deployment"])
    suffix = " (%s)" % ", ".join(context) if context else ""
    return "VERDICT %s%s" % (out.get("verdict", ERROR), suffix)


# --------------------------------------------------------------------------- selftest

FIXTURE_EXPECT = {
    "known-good": GREEN,
    "known-good-delta-reviewed": GREEN,
    "known-good-pause-acked": GREEN,
    "known-good-selfmade-reviewed": GREEN,
    "bad-empty-surface": ERROR,
    "bad-canary-collapse": ERROR,
    "bad-truncated-page": RED,
    "bad-stale-snapshot": RED,
    "bad-update-staged": RED,
    "bad-empty-charter": RED,
    "bad-server-bot-uncharted": RED,
    "bad-capability-delta-unreviewed": RED,
    "bad-baseline-without-ids": ERROR,
    "bad-desktop-version-drift": RED,
    "bad-desktop-audit-missing": RED,
    "bad-schedule-manual-only": RED,
    "bad-schedule-unregistered": RED,
    "bad-schedule-stale": RED,
    "bad-pause-ack-expired": RED,
    "bad-inventory-stale": RED,
    "bad-no-local-exec-policy": RED,
    "bad-policy-stale": RED,
    "bad-routine-failing": RED,
    "bad-routine-self-created": RED,
    "bad-no-inventory": ERROR,
    "bad-tunable-delta-unreviewed": RED,
    "bad-tunable-baseline-blind": ERROR,
    "bad-tunables-missing": ERROR,
    "bad-ondemand-unbounded": RED,
    "bad-usage-unmeasured": ERROR,
    "bad-practitioner-index-moved": RED,
    "bad-practitioner-never-reviewed": RED,
    "bad-coverage-regressed": RED,
    "bad-coverage-floor-missing": ERROR,
    "bad-installed-plugin-moved": RED,
    "bad-market-unreviewed": RED,
    "bad-doc-changed-unreviewed": RED,
    "bad-scheduler-gone-quiet": RED,
    "bad-discovery-untriaged": RED,
    "bad-discovery-query-failed": ERROR,
    "bad-discovery-empty": ERROR,
    "bad-mcp-unreachable": RED,
    "bad-mcp-tools-shrank": RED,
    # RESTORED 2026-09-12: an edit adding the g25 row silently DISPLACED this one, and the
    # suite still reported 54/54 because the total is driven by the table's own length — the
    # count cannot notice an entry that left. A fixture expectation that vanishes takes its
    # check's proof with it, which is the same class of defect g25 had in the first place.
    "bad-mcp-credential-rejected": RED,
    # g25-routine-liveness had NO fixture: disabling the check still produced 54/54, which
    # means nothing proved it. This corpus is enabled + past due + zero runs.
    "bad-routine-never-fired": RED,
    "bad-idle-credentialed-bot": RED,
    "bad-jobs-done-without-proof": RED,
    "known-good-new-bot-in-grace": GREEN,
    "bad-archive-stale": RED,
    "bad-archive-stale-at-cap": RED,
    "bad-dogfood-blanket-accept": RED,
    "bad-dogfood-defer-expired": RED,
    "bad-cli-contract-unwired": RED,
    "bad-usecases-unreviewed": RED,
    "bad-durable-io-raw-write": RED,
    "bad-dogfood-undisposed": RED,
    "bad-dogfood-score-tampered": RED,
    "bad-dogfood-reject-open": RED,
    "bad-dogfood-invalid-disposition": RED,
    "bad-types-regressed": RED,
    "bad-types-unmeasured": ERROR,
    "bad-gate-error-envelope": ERROR,
    "bad-usecases-empty": ERROR,
    "bad-surface-phantom": RED,
    "bad-grokbotdev-missing": ERROR,
    "bad-grokbotdev-stale": RED,
}
FIXTURE_NOW = dt.date(2026, 9, 11)


def _selftest_result(root: pathlib.Path, disabled: set[str]) -> tuple[list[dict], bool]:
    fx = root / "fixtures"
    rows: list = []
    ok = True
    if not fx.is_dir() or not any(fx.iterdir()):
        return rows, False
    for name, expected in FIXTURE_EXPECT.items():
        d = fx / name
        if not d.is_dir():
            rows.append(
                {"fixture": name, "expected": expected, "got": "MISSING", "pass": False}
            )
            ok = False
            continue
        marker = load(d / "gate-error-envelope.json")
        renderer_ok = True
        if isinstance(marker, dict):
            got = marker
            try:
                renderer_ok = verdict_line(got) == "VERDICT ERROR"
            except (KeyError, TypeError, ValueError):
                renderer_ok = False
        else:
            got = run(d, FIXTURE_NOW, disabled)
        # exit code must agree with the verdict text, always
        agree = got["exit_code"] == EXIT[got["verdict"]]
        passed = got["verdict"] == expected and agree and renderer_ok
        rows.append(
            {
                "fixture": name,
                "expected": expected,
                "got": got["verdict"],
                "exit": got["exit_code"],
                "exit_agrees": agree,
                "pass": passed,
                "reasons": [
                    f"{row['check']}={row['verdict']}"
                    for row in got["checks"]
                    if row["verdict"] != GREEN
                ]
                + (["renderer=ERROR"] if not renderer_ok else []),
            }
        )
        ok &= passed
    return rows, ok


def count_contract(root: pathlib.Path) -> dict:
    """Return the producer-backed tuple used by every normative gate-count claim.

    Mutation coverage is measured, not inferred from the number of registered checks: a
    check is covered only when disabling it makes the otherwise-green selftest fail.
    """
    _rows, baseline_ok = _selftest_result(root, set())
    if not baseline_ok:
        raise RuntimeError(
            "baseline selftest is not green; mutation coverage is unmeasurable"
        )
    split = {verdict: 0 for verdict in (GREEN, RED, ERROR)}
    for verdict in FIXTURE_EXPECT.values():
        split[verdict] += 1
    total = len(FIXTURE_EXPECT)
    if sum(split.values()) != total:
        raise RuntimeError("fixture verdict split does not equal fixture total")
    covered = sum(not _selftest_result(root, {check})[1] for check in CHECKS)
    if covered > len(CHECKS):
        raise RuntimeError("mutation-covered count exceeds enabled checks")
    return {
        "checks": len(CHECKS),
        "fixtures": total,
        "verdict_split": split,
        "mutation_covered": covered,
    }


def count_contract_text(counts: dict) -> str:
    split = counts["verdict_split"]
    return (
        f"checks={counts['checks']} fixtures={counts['fixtures']} "
        f"GREEN={split[GREEN]} RED={split[RED]} ERROR={split[ERROR]} "
        f"mutation-covered={counts['mutation_covered']}"
    )


def selftest(root: pathlib.Path, disabled: set[str], as_json: bool) -> int:
    fx = root / "fixtures"
    if not fx.is_dir() or not any(fx.iterdir()):
        print(
            "SELFTEST ERROR: fixtures/ absent — a gate with no known-bad is not a gate",
            file=sys.stderr,
        )
        return 2
    rows, ok = _selftest_result(root, disabled)
    payload = {
        "selftest": "gb-surface-gate",
        "version": VERSION,
        "disabled": sorted(disabled),
        "fixtures": rows,
        "pass": ok,
    }
    if as_json:
        print(json.dumps(payload, indent=1))
    else:
        for r in rows:
            mark = "PASS" if r["pass"] else "FAIL"
            print(
                f"  [{mark}] {r['fixture']:<34} expected {r['expected']:<5} got {r.get('got')}"
                + (
                    f"  ({', '.join(r.get('reasons') or [])})"
                    if r.get("reasons")
                    else ""
                )
            )
        print(
            f"SELFTEST {'PASS' if ok else 'FAIL'} — {sum(1 for r in rows if r['pass'])}/{len(rows)} fixtures"
        )
    return 0 if ok else 1


def capabilities() -> dict:
    return {
        "name": "gb-surface-gate",
        "version": VERSION,
        "purpose": "block a weekly Grok Bot tick whose measurement is untrustworthy or whose deployment disagrees with the vendor surface",
        "checks": CHECKS,
        "verdicts": {"GREEN": 0, "RED": 1, "ERROR": 2},
        "inputs": [
            "surface/<date>/manifest.json",
            "surface/<date>/pages/*.md",
            "deployment/<stamp>.json",
            "deployment/desktops/<stamp>.<label>.json",
            "desktops.json",
            "inventory/<stamp>.<label>.json",
            "pauses-acknowledged.json",
            "schedule/agent.json",
            "schedule/runs.jsonl",
            "findings/<date>.md",
            "jobs/walks.jsonl",
            "grokbotdev/<stamp>.json",
        ],
        "thresholds": {
            "max_snapshot_age_days": MAX_SNAPSHOT_AGE_DAYS,
            "max_inventory_age_days": MAX_INVENTORY_AGE_DAYS,
            "min_page_bytes": MIN_PAGE_BYTES,
            "min_llms_total_pages": MIN_LLMS_TOTAL_PAGES,
            "min_grok_bot_pages": MIN_GROK_BOT_PAGES,
            "stale_bot_days": STALE_BOT_DAYS,
            "max_grokbotdev_age_days": MAX_GROKBOTDEV_AGE_DAYS,
        },
        "selftest": (
            f"gb-surface-gate.py --selftest  ({len(FIXTURE_EXPECT)} fixtures; "
            "mutation: --selftest --disable <check> must FAIL)"
        ),
        "counts": "gb-surface-gate.py --counts  (one normative count tuple)",
    }


def main() -> int:
    root_default = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(root_default))
    ap.add_argument("--now", default=None, help="YYYY-MM-DD; defaults to today (UTC)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--capabilities", action="store_true")
    ap.add_argument("--counts", action="store_true")
    ap.add_argument(
        "--disable",
        action="append",
        default=[],
        choices=CHECKS,
        help="mutation testing only: drop a check and prove the selftest fails",
    )
    args = ap.parse_args()

    if args.capabilities:
        print(json.dumps(capabilities(), indent=1))
        return 0
    if args.counts:
        try:
            counts = count_contract(root_default)
        except RuntimeError as exc:
            print(f"COUNT CONTRACT ERROR: {exc}", file=sys.stderr)
            return 2
        print(
            json.dumps(counts, indent=1) if args.json else count_contract_text(counts)
        )
        return 0
    disabled = set(args.disable)
    root = pathlib.Path(args.root).resolve()
    if args.selftest:
        return selftest(root_default, disabled, args.json)

    # Legitimate wall-clock entry point: a live run defaults --now to today
    # (UTC); --selftest never reaches this line (FIXTURE_NOW is pinned). Every
    # verdict-affecting read below uses this `now`, never the wall clock.
    now = (
        dt.date.fromisoformat(args.now)
        if args.now
        else dt.datetime.now(dt.timezone.utc).date()
    )
    out = run(root, now, disabled)
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        for r in out["checks"]:
            print(f"  {r['verdict']:<5} {r['check']:<30} {r['detail']}")
        for a in out["advisories"]:
            print(f"  NOTE  {a}")
        print(verdict_line(out))
    return out["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
