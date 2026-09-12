#!/usr/bin/env python3
"""Per-Bot spend ledger from live usage-event reads (read-only).

Measured 2026-09-11: DashboardService/GetSandUsageStatus is account-level
(7 top-level keys, no Bot dimension). The per-Bot split lives one surface
over: DashboardService/GetFilteredUsageEvents returns per-turn display
items carrying conversationId + chargedCents + tokenUsage. For direct 1:1
turns conversationId == the desktop roster Bot uuid (verified live).

Honest boundary (live-proven, same session):
Two exact joins, nothing modeled. (1) Direct turns: conversationId == roster
uuid. (2) Routine runs: sand-subagent-<runId> == the run id in
ListGrokBotAgentAutomations recordJson (verified on a live run 2026-09-11).
recordJson retains only recent runs, and chat-spawned multitask subagents
carry no parent key at all, so older/detached subagent spend lands in
`unmatched` — never distributed by weights. Routine run records carry
status and timestamps but no cost fields, so they join but cannot price.
No caps, no thresholds, no mirrored limits are stored here by
design (g12 rule): this file records measured cents, never policy.
Auth is the desktop client's bearer token, decrypted in memory and never
written anywhere. Only List*/Get* RPCs are callable from this file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import importlib.util
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from gbtypes import atomic_write_text  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "state" / "spend-ledger.json"
SCHEMA = "gb-spend-ledger/1"
DASH = "aiserver.v1.DashboardService"
PAGE_SIZE = 1000


# TRANSPORT IS A LIBRARY (gbrpc.py) since 2026-09-12. This used to dynamically import
# bin/gb-pull-inventory.py by path to borrow access_token/rpc — a library dependency
# wearing a producer's clothes, which made `gb dogfood audit` count this file as one
# more hand-roller and forced the python 3.9.6 sys.modules dance on every borrower.
def _load_pull():
    """The transport, imported normally."""
    import gbrpc

    return gbrpc


def _roster(root: pathlib.Path) -> dict[str, str]:
    """uuid -> Bot name from the newest studio inventory (offline)."""
    files = sorted(glob.glob(str(root / "inventory" / "*.studio.json")))
    if not files:
        raise SystemExit(
            "no inventory/*.studio.json — run bin/gb-pull-inventory.py first"
        )
    doc = json.loads(pathlib.Path(files[-1]).read_text())
    bots = doc.get("bots") or []
    return {b["uuid"]: b["name"] for b in bots if b.get("uuid") and b.get("name")}


def _run_map(pull, token: str, roster: dict[str, str]) -> dict[str, dict]:
    """run-id -> owning Bot, from live automation records (read-only).

    Measured 2026-09-11: a routine run's subagent turns bill under
    conversationId ``sand-subagent-<runId>`` where runId is the run's id in
    ListGrokBotAgentAutomations recordJson. Exact UUID equality, no modeling.
    recordJson retains only recent runs, so older routine spend stays
    unmatched rather than guessed.
    """
    import json as _json

    out: dict[str, dict] = {}
    for uuid, name in roster.items():
        st, resp = pull.rpc(token, "ListGrokBotAgentAutomations", {"agent_id": uuid})
        autos = (
            resp.get("automations") if st == 200 and isinstance(resp, dict) else None
        ) or []
        for a in autos:
            try:
                rec = _json.loads(a.get("recordJson") or "{}")
            except Exception:
                continue
            for run in rec.get("runs") or []:
                if run.get("id"):
                    out[run["id"]] = {
                        "bot": name,
                        "uuid": uuid,
                        "routine": rec.get("name"),
                        "run_status": run.get("status"),
                    }
    return out


def _ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _f2(x) -> float:
    return round(float(x), 4)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pull = _load_pull()
    import gblib

    roster = _roster(ROOT)
    token = pull.access_token(gblib.platform_support().support_dir)

    st_u, usage = pull.rpc(token, "GetSandUsageStatus", {}, service=DASH)
    if st_u != 200 or not isinstance(usage, dict):
        raise SystemExit(
            f"GetSandUsageStatus unreachable (status {st_u}) — no entry written"
        )
    period_start = usage.get("currentPeriodStart")
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    window = {"start": period_start, "start_ms": _ms(period_start), "end_ms": now_ms}

    st_c, cycle = pull.rpc(token, "GetCurrentPeriodUsage", {}, service=DASH)
    st_a, agg = pull.rpc(token, "GetAggregatedUsageEvents", {}, service=DASH)

    events: list[dict] = []
    page = 1
    while True:
        st_e, resp = pull.rpc(
            token,
            "GetFilteredUsageEvents",
            {
                "startDate": window["start_ms"],
                "endDate": window["end_ms"],
                "page": page,
                "pageSize": PAGE_SIZE,
            },
            service=DASH,
        )
        if st_e != 200 or not isinstance(resp, dict):
            raise SystemExit(
                f"GetFilteredUsageEvents failed (status {st_e}) — no entry written"
            )
        batch = resp.get("usageEventsDisplay") or []
        events.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1

    runmap = _run_map(pull, token, roster)
    per_bot: dict[str, dict] = {}
    unmatched_models: dict[str, int] = {}
    unmatched_cents = 0.0
    unmatched_ids: list[str] = []

    def _row(uuid: str, name: str) -> dict:
        return per_bot.setdefault(
            uuid,
            {
                "name": name,
                "uuid": uuid,
                "events": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "charged_cents": 0.0,
                "routine_events": 0,
                "routine_cents": 0.0,
            },
        )

    for e in events:
        tu = e.get("tokenUsage") or {}
        cents = float(e.get("chargedCents") or 0)
        cid = str(e.get("conversationId") or "")
        run = (
            runmap.get(cid[len("sand-subagent-") :])
            if cid.startswith("sand-subagent-")
            else None
        )
        if cid in roster or run is not None:
            row = _row(run["uuid"] if run else cid, run["bot"] if run else roster[cid])
            row["events"] += 1
            row["input_tokens"] += int(tu.get("inputTokens") or 0)
            row["output_tokens"] += int(tu.get("outputTokens") or 0)
            row["cache_read_tokens"] += int(tu.get("cacheReadTokens") or 0)
            row["charged_cents"] += cents
            if run is not None:
                row["routine_events"] += 1
                row["routine_cents"] += cents
        else:
            unmatched_cents += cents
            unmatched_models[e.get("model") or "?"] = (
                unmatched_models.get(e.get("model") or "?", 0) + 1
            )
            if len(unmatched_ids) < 50 and cid not in unmatched_ids:
                unmatched_ids.append(cid)
    for row in per_bot.values():
        row["charged_cents"] = _f2(row["charged_cents"])
        row["routine_cents"] = _f2(row["routine_cents"])
    matched_cents = _f2(sum(r["charged_cents"] for r in per_bot.values()))
    total_cents = _f2(matched_cents + unmatched_cents)

    od = usage.get("onDemandSettings") or {}
    entry = {
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "window": window,
        "window_event_count": len(events),
        "account": {
            "source": "DashboardService/GetSandUsageStatus",
            "usage_percent": usage.get("usagePercent"),
            "plan_label": usage.get("grokPlanLabel"),
            "next_reset": usage.get("nextResetTimestampUtc"),
            "on_demand_enabled": od.get("enabled"),
            "on_demand_eligible": od.get("eligible"),
        },
        "billing_cycle": (
            {
                "source": "DashboardService/GetCurrentPeriodUsage",
                "plan_usage": (
                    cycle.get("planUsage") if isinstance(cycle, dict) else None
                ),
            }
            if st_c == 200
            else {
                "source": "DashboardService/GetCurrentPeriodUsage",
                "unreachable": True,
            }
        ),
        "by_model": (
            {
                "source": "DashboardService/GetAggregatedUsageEvents",
                "aggregations": agg.get("aggregations"),
            }
            if st_a == 200 and isinstance(agg, dict)
            else {
                "source": "DashboardService/GetAggregatedUsageEvents",
                "unreachable": True,
            }
        ),
        "per_bot": sorted(per_bot.values(), key=lambda r: -r["charged_cents"]),
        "unmatched": {
            "events": len(events) - sum(r["events"] for r in per_bot.values()),
            "charged_cents": _f2(unmatched_cents),
            "by_model": unmatched_models,
            "conversation_ids_truncated_at_50": unmatched_ids,
            "why": "remaining sand-subagent-* turns predate the visible run history "
            "(recordJson retains only recent runs) or were spawned from chat multitask "
            "fan-outs, which carry no parent key; automation_id filter returns empty 200; "
            "ListGrokBotAgentSessions returns empty. Never distributed by weights.",
        },
        "coverage": {
            "matched_cents": matched_cents,
            "total_cents": total_cents,
            "matched_fraction": round(matched_cents / total_cents, 4)
            if total_cents
            else None,
        },
        "method": "exact joins only: conversationId == roster uuid (direct turns), "
        "sand-subagent-<runId> == automation run id (routine turns); sums in cents from live reads; no modeled splits, no mirrored caps",
    }

    out = pathlib.Path(args.out)
    doc = {"schema": SCHEMA, "entries": []}
    if out.is_file():
        try:
            doc = json.loads(out.read_text())
        except Exception:
            raise SystemExit(
                f"{out} unreadable — refusing to append to a corrupt ledger"
            )
    doc.setdefault("entries", []).append(entry)
    # Hand-rolled tmp+replace is atomic in ORDERING but skips the fsync the helper does, so
    # a crash between write and replace can still leave a zero-length ledger. g22 flagged it
    # and g22 was right; this is a money ledger and it appends.
    atomic_write_text(out, json.dumps(doc, indent=1) + "\n")

    n_bots = len(per_bot)
    line = (
        f"spend-ledger entry {entry['captured_at']}: {len(events)} window events, "
        f"{n_bots} Bots, {matched_cents}c matched / {total_cents}c total "
        f"({entry['coverage']['matched_fraction']})"
    )
    if args.json:
        print(json.dumps({"entry": entry, "artifact": str(out)}, indent=1))
    else:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
