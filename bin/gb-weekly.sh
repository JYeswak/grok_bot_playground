#!/usr/bin/env bash
# gb-weekly — the whole weekly tick: measure the vendor surface, measure this
# deployment, then let the gate decide. Prints one line you paste into the bead
# comment or the commit body. It writes no report and no successor document.
#
#   bin/gb-weekly.sh              # snapshot + audit + gate
#   bin/gb-weekly.sh --gate-only  # re-judge the existing snapshot, fetch nothing
#   bin/gb-weekly.sh --json
#
# Exit code is the gate's: 0 GREEN, 1 RED, 2 ERROR. A RED tick is a successful
# tick that found work; an ERROR tick means we did not measure and must re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE_ONLY=0
JSON=""
SOURCE="manual"
for arg in "$@"; do
  case "$arg" in
    --gate-only) GATE_ONLY=1 ;;
    --json) JSON="--json" ;;
    --source=*) SOURCE="${arg#--source=}" ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 64 ;;
  esac
done

LOG="$ROOT/state/last-run.log"
mkdir -p "$ROOT/state"
: > "$LOG"

# The launchd log is append-only and nothing else trims it. A weekly job left for a year is a
# quiet disk leak, and a 50 MB log is also a log nobody reads.
WEEKLY_LOG="$ROOT/state/weekly.log"
if [[ -f "$WEEKLY_LOG" ]] && [[ $(wc -c < "$WEEKLY_LOG") -gt 1048576 ]]; then
  tail -c 262144 "$WEEKLY_LOG" > "$WEEKLY_LOG.trim" && mv "$WEEKLY_LOG.trim" "$WEEKLY_LOG"
  echo "[log trimmed to last 256KB at $(date -u +%FT%TZ)]" >> "$WEEKLY_LOG"
fi

if [[ "$GATE_ONLY" -eq 0 ]]; then
  # Producer truth before filtered output: capture each producer's own exit code.
  python3 "$ROOT/bin/gb-surface-snapshot.py" >>"$LOG" 2>&1
  snap_rc=$?
  echo "snapshot_rc=$snap_rc" >>"$LOG"
  if [[ $snap_rc -ne 0 ]]; then
    echo "ERROR snapshot producer failed (rc=$snap_rc); see $LOG" >&2
    tail -5 "$LOG" >&2
    exit 2
  fi

  # The catalog is a network read and its whole value is the diff, so it runs every tick beside
  # the doc snapshot. A failure here is not fatal to the tick — g15 reports an unreadable catalog
  # as ERROR rather than letting the run die before the gate ever speaks.
  # Discovery finds what no watch list holds. Non-fatal like the market read: g17 reports a
  # failed search as ERROR rather than letting the tick die before the gate speaks.
  # Archive BEFORE anything else touches the fleet: past the ~200-entry client cap this is the
  # only copy of those turns, and every new turn drops one.
  python3 "$ROOT/bin/gb-context-archive.py" >>"$LOG" 2>&1
  echo "archive_rc=$?" >>"$LOG"

  python3 "$ROOT/bin/gb-utilization.py" >>"$LOG" 2>&1
  echo "utilization_rc=$?" >>"$LOG"

  python3 "$ROOT/bin/gb-mcp-validate.py" >>"$LOG" 2>&1
  echo "mcp_rc=$?" >>"$LOG"

  python3 "$ROOT/bin/gb-usecases.py" --markdown >>"$LOG" 2>&1
  echo "usecases_rc=$?" >>"$LOG"

  # gaps depends on BOTH the market and usecases snapshots above, so it runs after them.
  python3 "$ROOT/bin/gb-gaps.py" --markdown >>"$LOG" 2>&1
  echo "gaps_rc=$?" >>"$LOG"

  python3 "$ROOT/bin/gb-discover.py" >>"$LOG" 2>&1
  echo "discover_rc=$?" >>"$LOG"

  python3 "$ROOT/bin/gb-market-snapshot.py" >>"$LOG" 2>&1
  echo "market_rc=$?" >>"$LOG"

  python3 "$ROOT/bin/gb-deployment-audit.py" >>"$LOG" 2>&1
  audit_rc=$?
  echo "audit_rc=$audit_rc" >>"$LOG"
  if [[ $audit_rc -ne 0 ]]; then
    echo "ERROR deployment audit producer failed (rc=$audit_rc); see $LOG" >&2
    tail -5 "$LOG" >&2
    exit 2
  fi
  sed -n '1,4p' "$LOG"
fi

# A fixture the repo cannot hand to a fresh clone is not evidence. Measured 2026-09-11: both g12
# and g13 read their inputs from gitignored `state/`, and `.gitignore`'s `state/` matches at any
# depth, so the FIXTURE copies were untracked too — the suite passed here and would have failed
# on a clean checkout. Proving it from a clone is the only check that catches that class.
if [[ "$GATE_ONLY" -eq 0 ]] && command -v git >/dev/null && [[ -d "$ROOT/.git" ]]; then
  CLONE="$(mktemp -d)"
  if git clone -q "$ROOT" "$CLONE" 2>>"$LOG"; then
    clone_out="$(cd "$CLONE" && python3 bin/gb-surface-gate.py --selftest 2>&1 | tail -1)"
    echo "clone_selftest: $clone_out" >>"$LOG"
    case "$clone_out" in
      *"SELFTEST PASS"*) : ;;
      *) echo "ERROR the committed tree fails its own selftest in a clean clone: $clone_out" >&2 ;;
    esac
  fi
  rm -rf "$CLONE"
fi

set +e
python3 "$ROOT/bin/gbtypes-selftest.py" >>"$LOG" 2>&1; spine_rc=$?
if [[ $spine_rc -ne 0 ]]; then echo "ERROR typed-spine proofs failed (rc=$spine_rc); see $LOG" >&2; tail -5 "$LOG" >&2; fi
python3 "$ROOT/bin/gb-typecheck.py" >>"$LOG" 2>&1; types_rc=$?
if [[ $types_rc -ne 0 ]]; then echo "ERROR typecheck floor not clean (rc=$types_rc); see $LOG" >&2; fi
python3 "$ROOT/bin/gb-surface-gate.py" $JSON
gate_rc=$?
set -e

case "$gate_rc" in
  0) verdict=GREEN ;;
  1) verdict=RED ;;
  *) verdict=ERROR ;;
esac
# Every run is recorded, including its trigger. A loop that only ever runs because a human typed
# the command is not a scheduled loop, and g10 says so.
mkdir -p "$ROOT/schedule"
printf '{"at":"%s","verdict":"%s","exit":%s,"source":"%s","host":"%s"}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$verdict" "$gate_rc" "$SOURCE" "$(hostname)" \
  >> "$ROOT/schedule/runs.jsonl"

# The gates say whether a change was reviewed; this says what the change WAS. Printed every tick
# so the answer to "what moved this week" is in the run output, not in someone's memory.
python3 "$ROOT/bin/gb-whatchanged.py" || true

echo "gb-tick $(date -u +%Y-%m-%d) grokbot: ${verdict} — paste this line into the bead comment or commit body with the one product change it produced (or ZERO and why)."
exit "$gate_rc"
