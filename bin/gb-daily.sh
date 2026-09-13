#!/usr/bin/env bash
# gb-daily.sh — the daily tick: collect → ingest → compact → diff.
#
# WHY DAILY AND NOT WEEKLY (measured 2026-09-11, do not re-derive):
#
#   The weekly tick was built when this repo watched ONE source. Then the fleet was measured:
#
#     docs.x.ai      183/183 pages carry lastmod inside 7d — ONE build stamp, rebuilt in-week
#     GitHub         270 repos pushed in a single day out of 452 tracked
#     X              posts arriving minute-by-minute; a search run twice 13 min apart moved
#     cursor.com     changelog + blog, both live RSS
#
#   A weekly poll against a surface that rebuilds daily reports yesterday's world and calls it
#   current. The allowance window is still weekly (resets Fridays ~20:17 MDT) — that governs
#   SPEND, not OBSERVATION. This script observes; `gb-weekly.sh` still owns the tick that spends.
#
# WHAT IT DOES NOT DO: it does not change the Grok Bot deployment. It collects, stores and
# diffs. The product change is a human decision, taken from the diff.
#
# CONTRACT
#   exit 0  a diff was produced (or there is honestly nothing new)
#   exit 1  a producer refused or a check found something the operator must see
#   exit 2  usage
#   exit 3  unmeasured — the store could not be read; never reported as "nothing new"

set -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/bin"
PY="${PYTHON:-python3}"
STAMP="$(date -u +%Y-%m-%dT%H%M)"
LOG_DIR="$ROOT/state"
LOG="$LOG_DIR/daily.log"
FETCH=1
AS_JSON=0
FULL_ROWS=0
KINDS=()

usage() {
  cat <<'USAGE'
gb-daily.sh — the daily tick: collect → ingest → compact → diff
  --no-fetch      skip the network producers; ingest and diff what is already on disk
  --kind ROOT     only diff this producer root (repeatable; default: all with 2+ snapshots)
  --json          machine-readable envelope on stdout
  --full          include every added/changed row (default caps each rows array at 20;
                  counts are always full — a capped array never hides a number)
  -h, --help      this

Producers run in sequence and a failure NEVER aborts the tick: a refusing source is recorded
and the remaining producers still run. A tick that collects 5 of 6 sources is worth more than
a tick that dies on the first 403.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-fetch) FETCH=0; shift ;;
    --full) FULL_ROWS=1; shift ;;
    --kind) KINDS+=("$2"); shift 2 ;;
    --json) AS_JSON=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'gb-daily: unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$LOG_DIR"
printf '\n=== daily tick %s ===\n' "$STAMP" >>"$LOG"

# --- collect ---------------------------------------------------------------------------------
# Each producer is independent. We record its exit code rather than trusting a pipeline: a
# producer that refuses a source (403, rate limit) exits non-zero BY DESIGN and its partial
# artifact is still worth ingesting.
declare -a RESULTS=()
run_producer() {
  local name="$1" script="$2"
  if [ ! -x "$BIN/$script" ] && [ ! -f "$BIN/$script" ]; then
    RESULTS+=("$name:absent:-")
    return
  fi
  local start rc
  start=$(date +%s)
  "$PY" "$BIN/$script" collect >>"$LOG" 2>&1
  rc=$?
  RESULTS+=("$name:$rc:$(( $(date +%s) - start ))s")
  printf '  %-10s rc=%s\n' "$name" "$rc" >>"$LOG"
}

if [ "$FETCH" = 1 ]; then
  run_producer sources gb-sources.py
  run_producer github  gb-github.py
  run_producer feeds   gb-feeds.py
  run_producer grokbotdev gb-grokbotdev.py
  run_producer x       gb-x.py
  run_producer links   gb-links.py
else
  RESULTS+=("collect:skipped:--no-fetch")
fi

# --- ingest + compact ------------------------------------------------------------------------
# Ingest is idempotent by snapshot id, so re-running the tick cannot inflate the store.
INGEST_JSON="$("$PY" "$BIN/gb-store.py" ingest --json 2>>"$LOG")"
INGEST_RC=$?
COMPACT_JSON="$("$PY" "$BIN/gb-store.py" compact --json 2>>"$LOG")"
COMPACT_RC=$?

if [ "$INGEST_RC" != 0 ] || [ "$COMPACT_RC" != 0 ]; then
  printf 'gb-daily: the store could not be updated — UNMEASURED, not "nothing new"\n' >&2
  exit 3
fi

# --- diff ------------------------------------------------------------------------------------
# The point of the whole tick. A root with one snapshot reports INSUFFICIENT rather than zero:
# "we have not measured twice yet" and "nothing changed" are different answers.
if [ "${#KINDS[@]}" -eq 0 ]; then
  KINDS=(sources github feeds grokbotdev x links usecases market)
fi

DIFFS="["
SEP=""
for k in "${KINDS[@]}"; do
  d="$("$PY" "$BIN/gb-store.py" diff --kind "$k" --json 2>>"$LOG")"
  [ -z "$d" ] && continue
  DIFFS="$DIFFS$SEP$(printf '%s' "$d" | "$PY" -c 'import json,sys
try:
    print(json.dumps(json.load(sys.stdin)["result"]))
except Exception:
    print("null")')"
  SEP=","
done
export GB_STAMP="$STAMP" GB_RESULTS="${RESULTS[*]}" GB_DIFFS="$DIFFS"
export GB_INGEST="$INGEST_JSON" GB_COMPACT="$COMPACT_JSON" GB_ASJSON="$AS_JSON" GB_FULL="$FULL_ROWS"
export GB_CAP=20
"$PY" - <<'REPORT'
import json, os, sys

stamp = os.environ["GB_STAMP"]
as_json = os.environ.get("GB_ASJSON") == "1"


def load(name, default):
    try:
        return json.loads(os.environ.get(name) or "")
    except Exception:
        return default


ingest = load("GB_INGEST", {}).get("result", {})
compact = load("GB_COMPACT", {}).get("result", {})
diffs = [d for d in load("GB_DIFFS", []) if isinstance(d, dict)]
full = os.environ.get("GB_FULL") == "1"
cap = int(os.environ.get("GB_CAP") or 20)
truncated = False
if not full:
    for d in diffs:
        for key in ("added_rows", "changed_rows"):
            rows = d.get(key)
            if isinstance(rows, list) and len(rows) > cap:
                d[key] = rows[:cap]
                truncated = True
producers = []
for chunk in (os.environ.get("GB_RESULTS") or "").split():
    parts = chunk.split(":")
    if len(parts) == 3:
        producers.append({"producer": parts[0], "rc": parts[1], "took": parts[2]})

changed = [d for d in diffs if d.get("status") == "OK" and (d.get("added") or d.get("removed"))]
insufficient = [d for d in diffs if d.get("status") == "INSUFFICIENT"]
refused = [p for p in producers if p["rc"] not in ("0", "skipped", "absent")]

payload = {
    "schema": "gb-daily/1",
    "diffs": diffs,
    "rows_truncated": truncated,
    "summary": {
        "roots_changed": len(changed),
        "roots_unmeasured": len(insufficient),
        "producers_refused": len(refused),
        "rows_added": sum(int(d.get("added") or 0) for d in changed),
    },
}

if as_json:
    print(json.dumps(payload, indent=1))
else:
    print(f"daily tick {stamp}")
    for p in producers:
        mark = "ok " if p["rc"] == "0" else "-- " if p["rc"] in ("skipped", "absent") else "RC" + p["rc"]
        print(f"  {mark:<5} {p['producer']:<9} {p['took']}")
    print(
        f"  store   {ingest.get('files_ingested', '?')} new file(s), "
        f"{ingest.get('unique_rows', '?')} unique rows, "
        f"dedup {ingest.get('dedup_ratio', '?')}"
    )
    if compact:
        print(f"  compact {compact.get('reclaimed_pct', '?')}% reclaimed, verified={compact.get('verified')}")
    print()
    if changed:
        for d in changed:
            print(f"  NEW  {d['kind']:<9} +{d.get('added', 0)} -{d.get('removed', 0)}")
            for row in (d.get("added_rows") or [])[:5]:
                title = row.get("title") or row.get("name") or row.get("id") or "?"
                print(f"         {str(title)[:92]}")
    else:
        print("  no change in any root with two snapshots")
    if insufficient:
        names = ", ".join(d["kind"] for d in insufficient)
        print(f"  UNMEASURED (need a second snapshot): {names}")
    if refused:
        for p in refused:
            print(f"  REFUSED {p['producer']} rc={p['rc']} — see state/daily.log")

sys.exit(1 if refused else 0)
REPORT
