#!/usr/bin/env bash
# Build a Chrome Web Store zip with manifest.json at the zip root.
# Also runs the compiler self-test and asks Chrome to pack a .crx when
# google-chrome is on PATH (manifest-accepted check).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DIST="$ROOT/dist"
STAGE="$DIST/stage"
ZIP="$DIST/page-to-grok-bot.zip"

python3 "$ROOT/icons/write_icons.py"

python3 -c "import json,sys; json.load(open('$ROOT/manifest.json')); print('manifest.json ok')"

node --check "$ROOT/background.js"
node --check "$ROOT/compiler.js"
node --check "$ROOT/extract.js"
node --check "$ROOT/missions.js"
node --check "$ROOT/sidepanel.js"
node "$ROOT/selftest.mjs"

rm -rf "$DIST"
mkdir -p "$STAGE"
for f in manifest.json background.js compiler.js extract.js missions.js \
         sidepanel.html sidepanel.css sidepanel.js README.md PRIVACY.md; do
  cp "$ROOT/$f" "$STAGE/$f"
done
mkdir -p "$STAGE/icons" "$STAGE/store"
cp "$ROOT/icons/"icon*.png "$STAGE/icons/"
cp "$ROOT/store/LISTING.md" "$STAGE/store/LISTING.md"

# manifest.json MUST sit at the zip root. A nested extension/ folder fails upload.
(cd "$STAGE" && zip -qr "$ZIP" .)
echo "store zip: $ZIP"
python3 - <<PY
import zipfile
zf = zipfile.ZipFile("$ZIP")
names = zf.namelist()
assert "manifest.json" in names, names
assert not any(n.startswith("extension/") for n in names), names
print("zip root has manifest.json;", len(names), "files")
PY

if command -v google-chrome >/dev/null 2>&1 || command -v google-chrome-stable >/dev/null 2>&1; then
  CHROME="$(command -v google-chrome-stable || command -v google-chrome)"
  "$CHROME" --no-sandbox --disable-gpu --pack-extension="$STAGE" --no-message-box >/dev/null 2>&1 || true
  if [[ -f "$DIST/stage.crx" ]]; then
    mv "$DIST/stage.crx" "$DIST/page-to-grok-bot.crx"
    rm -f "$DIST/stage.pem"
    echo "chrome packed a .crx — manifest accepted"
  else
    echo "chrome pack did not emit a .crx (non-fatal; zip is still the upload)"
  fi
fi
