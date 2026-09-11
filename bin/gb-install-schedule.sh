#!/usr/bin/env bash
# gb-install-schedule — install, validate, or remove the weekly tick as a launchd agent.
#
# Why a scheduler at all: g3 enforces an 8-day snapshot-freshness ceiling. Before this existed,
# exactly one snapshot had ever been taken, by hand. A staleness gate with nothing behind it does
# not detect decay — it waits to accuse someone. g10-loop-scheduled gates this agent in turn.
#
# The plist follows the house exemplar rather than my first guess at it
# (dicklesworthstone-mirror/storage_ballast_helper/src/daemon/service/launchd.rs:311-333, found via
# `fh suggest "launchd plist"`). Five keys my first version lacked, each for a reason:
#   ProcessType=Background + Nice=19 + LowPriorityIO  a weekly doc fetch must never compete with
#                                                     Joshua's foreground work
#   ThrottleInterval=60                               launchd's default 10s respawn floor is wrong
#                                                     for a weekly job; 60 makes a crash-loop visible
#                                                     instead of hot
#   WorkingDirectory + EnvironmentVariables[PATH]     the first version ran `bash -lc "cd … && …"`,
#                                                     inheriting whatever a login shell happened to
#                                                     export. launchd agents do not get your
#                                                     interactive PATH; depending on it is how a
#                                                     scheduled job works on the machine that
#                                                     installed it and nowhere else.
# Load path is `bootstrap gui/<uid>` / `bootout`, not `load`/`unload` — the same pair the exemplar
# uses. `load` is deprecated and reports success in cases where the job is not actually registered.
#
#   bin/gb-install-schedule.sh --dry-run     # render + plutil -lint + show target paths, write nothing
#   bin/gb-install-schedule.sh --install     # bootout-then-bootstrap, verify registration, record it
#   bin/gb-install-schedule.sh --validate    # force a real run now and prove it landed
#   bin/gb-install-schedule.sh --status
#   bin/gb-install-schedule.sh --uninstall
set -euo pipefail

# THE PLATFORM GATE. Everything below is launchd, which exists only on macOS. Off it, the
# failure used to be `launchctl: command not found` in the middle of a bootstrap — after the
# plist had already been written to a `~/Library/LaunchAgents` directory that does not belong
# on that machine. Exit 3 ENVIRONMENT and write nothing: `gb platform` reports this same fact
# as `scheduler_install: NOT_IMPLEMENTED`, with the same workaround.
if [[ "$(uname -s)" != "Darwin" ]]; then
    cat >&2 <<REFUSAL
gb-install-schedule: scheduler_install is NOT_IMPLEMENTED on $(uname -s).
    This installs a launchd agent, and launchd is macOS-only. The Windows equivalent is Task
    Scheduler (schtasks); the Linux equivalent is a systemd --user timer, or cron. Neither is
    implemented here, and nothing was written.
    fix: schedule bin/gb-weekly.sh yourself, weekly, and keep the 8-day snapshot ceiling g3
         enforces. See: gb platform
REFUSAL
    exit 3
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="ai.zeststream.grokbot.weekly"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG="$ROOT/state/weekly.log"
DOMAIN="gui/$(id -u)"
TARGET="${DOMAIN}/${LABEL}"

plist() {
cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${ROOT}/bin/gb-weekly.sh</string>
    <string>--source=launchd</string>
  </array>
  <key>WorkingDirectory</key><string>${ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:${HOME}/.local/bin</string></dict>
  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>15</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>ProcessType</key><string>Background</string>
  <key>Nice</key><integer>19</integer>
  <key>LowPriorityIO</key><true/>
  <key>ExitTimeOut</key><integer>120</integer>
  <key>StandardOutPath</key><string>${LOG}</string>
  <key>StandardErrorPath</key><string>${LOG}</string>
</dict>
</plist>
PLIST
}

lint_to() { # render to $1 and refuse anything plutil rejects
  plist > "$1"
  plutil -lint "$1" >/dev/null 2>&1 || { echo "plist lint FAILED" >&2; plutil -lint "$1" >&2; return 1; }
}

record() {
  python3 - "$ROOT" "$LABEL" "$PLIST" "$1" <<'PY'
import datetime, json, pathlib, sys
root, label, plist, state = sys.argv[1:5]
d = pathlib.Path(root) / "schedule"; d.mkdir(exist_ok=True)
(d / "agent.json").write_text(json.dumps({
    "installed": state == "installed", "label": label, "plist": plist,
    "schedule": "Mon 09:15 local", "domain_target": f"gui/<uid>/{label}",
    "installed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
    "_why": "Committed so g10 can tell 'the loop is scheduled' from 'someone ran it by hand once'.",
}, indent=1) + "\n")
PY
}

case "${1:---dry-run}" in
  --dry-run|--print)
    tmp="$(mktemp -t gbplist).plist"; lint_to "$tmp"
    echo "plist lint: OK"
    echo "would write:  $PLIST"
    echo "would log to: $LOG"
    echo "would target: $TARGET"
    echo "---"; cat "$tmp"; rm -f "$tmp" ;;

  --install)
    mkdir -p "$(dirname "$PLIST")" "$ROOT/state"
    lint_to "$PLIST"
    launchctl bootout "$TARGET" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    # bootstrap can exit 0 without registering; only `print` proves it.
    launchctl print "$TARGET" >/dev/null 2>&1 || { echo "bootstrap returned 0 but $TARGET is not registered" >&2; exit 1; }
    record installed
    echo "registered $TARGET — Mondays 09:15 local"
    echo "run --validate to prove it actually executes; do not wait until Monday to find out." ;;

  --validate)
    launchctl print "$TARGET" >/dev/null 2>&1 || { echo "not registered — run --install first" >&2; exit 1; }
    before=$(wc -l < "$ROOT/schedule/runs.jsonl" 2>/dev/null || echo 0)
    launchctl kickstart -k "$TARGET"
    for _ in $(seq 1 60); do
      after=$(wc -l < "$ROOT/schedule/runs.jsonl" 2>/dev/null || echo 0)
      [[ "$after" -gt "$before" ]] && { echo "validated: a launchd-triggered run was recorded"; tail -1 "$ROOT/schedule/runs.jsonl"; exit 0; }
      sleep 2
    done
    echo "kickstart fired but no run was recorded within 120s — check $LOG" >&2; exit 1 ;;

  --status)
    launchctl print "$TARGET" 2>/dev/null | grep -iE "^\s+(state|program|last exit code|runs) " || echo "not registered"
    [[ -f "$ROOT/schedule/runs.jsonl" ]] && { echo "last recorded run:"; tail -1 "$ROOT/schedule/runs.jsonl"; } ;;

  --uninstall)
    launchctl bootout "$TARGET" 2>/dev/null || true
    rm -f "$PLIST"; record removed
    echo "removed $TARGET" ;;

  *) echo "usage: $0 [--dry-run|--install|--validate|--status|--uninstall]" >&2; exit 64 ;;
esac
