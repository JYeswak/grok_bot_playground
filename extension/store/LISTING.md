# Chrome Web Store listing copy

## Name

Page to Grok Bot

## Summary

Turn the active page into a paste-ready Grok Bot charter, first message, and schedule prompt.

## Category

Productivity

## Detailed description

Page to Grok Bot reads the current page after you click the toolbar button, context menu item, or **Read this tab** in the side panel. It extracts a lightweight page fingerprint — URL, headings, page type hints, selected text, and same-site links — then compiles that into a `gb-template/1` charter you can copy or download.

The extension is intentionally narrow:

- it does **not** sign in to Grok
- it does **not** create the Bot for you
- it does **not** install a routine
- it does **not** use cookies or a backend

Use it when you want a quick starting point for a product watch, changelog watch, hiring watch, or one-off research bot from the page you are already reading.

## Privacy tab answers

### Single purpose

Compile the active page into a paste-ready Grok Bot charter and related copy.

### Remote code

No.

### Permissions justification

- `activeTab`: reads the page you explicitly invoked the extension on.
- `scripting`: runs the extractor on that active page.
- `sidePanel`: renders the local compiler UI.
- `contextMenus`: adds the page shortcut.

### Data use

- Personally identifiable information: No
- Health information: No
- Financial and payment information: No
- Authentication information: No
- Personal communications: No
- Location: No
- Web history: No
- User activity: No
- Website content: **Yes, only the active tab after a user gesture**

### Data handling checkboxes

- Sold to third parties: No
- Used for advertising: No
- Used for creditworthiness or lending: No
- Shared with third parties: No
- Processed off device: No

## Test instructions

1. Open Chrome and load the unpacked extension from the `extension/` directory.
2. Visit <https://stripe.com/pricing>.
3. Click the extension action or right-click the page and choose **Make a Grok Bot from this page**.
4. Confirm the side panel opens and shows a page fingerprint for Stripe pricing.
5. Confirm the mission chips render, the preview shows a `gb-template/1` object, and the charter meter stays at or under `900 / 900`.
6. Click **Copy charter**, **Copy first message**, **Copy schedule prompt**, and **Download gb-template/1** to confirm each action works without requiring an account.

## Assets to prepare

- 128x128 icon
- 1280x800 screenshots
- 440x280 small tile

Do not invent screenshots. Capture the real side panel running on a safe public page such as `stripe.com/pricing`.

## First publish recommendation

Publish first as **Unlisted** or to **Trusted Testers**, not Public.
