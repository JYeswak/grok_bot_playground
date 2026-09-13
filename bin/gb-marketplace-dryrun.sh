#!/usr/bin/env bash
# gb-marketplace-dryrun — run the MARKETPLACE'S OWN CI against our plugin, before any PR exists.
#
# Our validator (bin/gb-plugin-validate.py) encodes the rules as we READ them. This runs the
# rules as the vendor ENFORCES them: their catalog validator and their index generator, from a
# fresh clone of xai-org/plugin-marketplace, against a catalog entry that points at plugin/.
# The gap between those two is exactly the class of mistake that gets a PR sent back — measured
# 2026-09-11, when our manifest sat at `.grok-plugin/plugin.json` because that is what the
# marketplace repo uses for its OWN catalog, while the plugin reference documents
# `plugin.json` (Agent Plugins) or `.cursor-plugin/plugin.json` (Cursor Plugins).
#
# Everything happens in a temp clone. Nothing is pushed, and the vendor repo is never modified
# in place.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN="$ROOT/plugin"
NAME="$(python3 -c "import json,sys;print(json.load(open('$PLUGIN/.cursor-plugin/plugin.json'))['name'])")"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "=== clone the marketplace (shallow, throwaway) ==="
git clone -q --depth=1 https://github.com/xai-org/plugin-marketplace "$WORK/pm"
cd "$WORK/pm"

echo "=== baseline: their catalog must pass before we add anything ==="
python3 scripts/validate-catalog.py

cp -R "$PLUGIN" "external_plugins/$NAME"
python3 - "$NAME" <<'PY'
import json, pathlib, sys
name = sys.argv[1]
cat = pathlib.Path(".grok-plugin/marketplace.json")
doc = json.loads(cat.read_text())
man = json.loads(pathlib.Path(f"external_plugins/{name}/.cursor-plugin/plugin.json").read_text())
doc["plugins"].append({
    "name": name,
    "description": man["description"],
    "category": "productivity",
    # Local source for the dry run. A real submission pins a 40-char sha on a public repo;
    # local proves the manifest and components without needing that repo to exist yet.
    "source": {"source": "local", "path": f"./external_plugins/{name}"},
    "homepage": man["homepage"],
    "keywords": man.get("keywords", []),
})
cat.write_text(json.dumps(doc, indent=2) + "\n")
PY

echo "=== with our entry: exactly what their CI runs ==="
python3 scripts/validate-catalog.py
python3 scripts/generate-plugin-index.py
python3 scripts/generate-plugin-index.py --check

echo "=== what THEIR indexer discovered in our plugin ==="
python3 - "$NAME" <<'PY'
import json, pathlib, sys
idx = json.loads(pathlib.Path(".grok-plugin/plugin-index.json").read_text())
me = (idx.get("plugins") or {}).get(sys.argv[1]) or {}
comp = me.get("components") or {}
if not comp:
    raise SystemExit("FAIL: the vendor indexer found no components in our plugin")
for kind, items in sorted(comp.items()):
    print(f"  {kind}: {', '.join(i['name'] for i in items)}")
PY

echo "DRY RUN PASS — the vendor's own CI accepts this plugin"
