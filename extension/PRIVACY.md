# Privacy Policy — Page to Grok Bot

Page to Grok Bot has one purpose: compile the current page into a paste-ready Grok Bot charter.

## What it reads

- The extension reads the active tab only after a user gesture: opening the side panel from the toolbar or context menu, or clicking **Read this tab** in the side panel.
- It extracts page text and metadata such as the URL, headings, description, same-site links, page type hints, and any text the user has selected on that page.

## What it does not read or send

- No network requests are made by the extension.
- No cookies are read.
- No browsing history is collected.
- No credentials, tokens, or account data are requested.
- No data is shared with third parties.

## Permissions and why they are used

- `activeTab`: read the current page only after the user invokes the extension.
- `scripting`: inject the page-fingerprint extractor into the current page.
- `sidePanel`: show the compiler UI in Chrome's side panel.
- `contextMenus`: add the **Make a Grok Bot from this page** shortcut.

## Data handling

- Data stays in the browser context unless the user explicitly copies or downloads the generated output.
- The extension does not maintain a backend service and does not transmit extracted page data anywhere.

## Contact

Questions or requests should be filed as GitHub issues on this repository:
<https://github.com/JYeswak/grok_bot_playground/issues>
