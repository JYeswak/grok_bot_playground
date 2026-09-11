#!/usr/bin/env bash
# gb-audit-remote — run the deployment audit on another registered desktop over ssh
# and bring the JSON home to deployment/desktops/.
#
#   bin/gb-audit-remote.sh brain
#
# Why this exists: Auto-review rules and Execution-on-Local-Computer live on the desktop that
# set them (https://docs.x.ai/grok-bot/security), so a second Mac is a second deployment, not a
# mirror. `desktops.json` is the registry; `g7-desktop-parity` fails when a registered desktop
# has no recent audit or runs a different client version than the host of record.
#
# Read-only on the remote: it copies one python file to a temp path, runs it, prints JSON, and
# deletes the copy. It never writes to the remote's Grok Bot state.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${1:-}"
[[ -n "$LABEL" ]] || { echo "usage: gb-audit-remote.sh <label from desktops.json>" >&2; exit 64; }

ALIAS=$(python3 - "$ROOT/desktops.json" "$LABEL" <<'PY'
import json, sys
reg = json.load(open(sys.argv[1]))
for d in reg["desktops"]:
    if d["label"] == sys.argv[2]:
        print(d.get("ssh") or "")
        sys.exit(0)
sys.exit(3)
PY
) || { echo "label '$LABEL' is not in desktops.json" >&2; exit 65; }
[[ -n "$ALIAS" ]] || { echo "'$LABEL' has no ssh alias — audit it locally with bin/gb-deployment-audit.py" >&2; exit 65; }

REMOTE_TMP="/tmp/gb-deployment-audit.$$.py"
STAMP="$(date -u +%Y-%m-%dT%H%M)"
OUT="$ROOT/deployment/desktops/${STAMP}.${LABEL}.json"
mkdir -p "$(dirname "$OUT")"

scp -q -o BatchMode=yes -o ConnectTimeout=10 "$ROOT/bin/gb-deployment-audit.py" "$ALIAS:$REMOTE_TMP"
# --no-decode: mining app.asar for gate names costs ~1.5s and 50 MB of reads; the host of record
# already recovers the name map for this client version, and parity is a version question.
ssh -o BatchMode=yes -o ConnectTimeout=10 "$ALIAS" \
  "python3 '$REMOTE_TMP' --no-decode --out /dev/stdout; rc=\$?; rm -f '$REMOTE_TMP'; exit \$rc" \
  > "$OUT.raw"
rc=$?
echo "remote_audit_rc=$rc"
if [[ $rc -ne 0 ]]; then
  echo "ERROR remote audit failed on '$ALIAS' (rc=$rc)" >&2
  head -5 "$OUT.raw" >&2
  exit 2
fi

# The audit prints a human summary line after the JSON it wrote to stdout; keep only the JSON.
python3 - "$OUT.raw" "$OUT" <<'PY'
import json, sys, pathlib
raw = pathlib.Path(sys.argv[1]).read_text()
start = raw.index("{")
depth = 0
for i, ch in enumerate(raw[start:], start):
    depth += (ch == "{") - (ch == "}")
    if depth == 0:
        break
obj = json.loads(raw[start:i + 1])
pathlib.Path(sys.argv[2]).write_text(json.dumps(obj, indent=1) + "\n")
print(f"{sys.argv[2]}: {obj['host']} app {obj['app']['version']}, {len(obj['bots'])} bots")
PY
rm -f "$OUT.raw"
