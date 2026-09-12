# Page to Grok Bot extension

Page to Grok Bot turns the current page into paste-ready Grok Bot text. It stays inside the playground boundary: no credentials, no app driving, no hidden RPCs.

## Load unpacked in Chrome

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Choose the repository's `extension/` directory.
5. Open a public page, click the extension action, or use the page context menu item.

## Validate and pack

From the repository root:

```sh
python3 extension/icons/write_icons.py
node extension/selftest.mjs
bash extension/pack.sh
```

`pack.sh` validates the manifest, checks the JavaScript files, runs the self-test, writes icons, and builds a Chrome Web Store zip with `manifest.json` at the zip root.

## Honest boundary

The extension:

- reads the active tab only after a user gesture
- compiles a `gb-template/1` object locally
- lets you copy a charter, first message, or schedule prompt

The extension does **not**:

- sign in to Grok
- create the Bot
- enable connectors
- install or run a routine

## Permissions

| Permission | Why it exists |
| --- | --- |
| `activeTab` | Read the invoked page only after a user gesture |
| `scripting` | Inject the page fingerprint extractor |
| `sidePanel` | Show the local compiler UI |
| `contextMenus` | Add **Make a Grok Bot from this page** |

## P0-P3 phasing

- **P0**: capture the current page, compile a local charter, copy/download it
- **P1**: tighten extraction heuristics and polish preview quality
- **P2**: improve mission presets and first-message guidance
- **P3**: prepare Chrome Web Store listing and release flow

See `docs/CHROME_WEB_STORE.md` and `extension/PRIVACY.md` for release and privacy details.
