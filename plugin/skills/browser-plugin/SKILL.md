---
name: browser-plugin
description: Drive a public page through the enabled browser-use, playwright, or browse plugin, then stop at login, 2FA, CAPTCHA, send, or pay. Use when a site needs a real browser and Firecrawl scrape is the wrong tool. Never use the human laptop's Chrome.
---

# Browser plugin

Drive **one public page** with a browser **plugin** this account enabled
2026-09-12 (*client-binary-verified* via `gb inventory plugins`):

| Plugin | Publisher | MCP name seen |
|---|---|---|
| `browser-use` | browser-use | `Browser-use` in `effective_server_names` |
| `playwright` | cursor | `Playwright` in `effective_server_names` |
| `browse` | browserbase | **not** in MCP names the same day |

Same-day `tool_census` returned **zero tools** for all three. Approved is
not working. This skill exists so the Bot probes instead of pretending
Playwright ran.

Prefer `firecrawl-read` when the job is "get the markdown." This skill is
for **seeing / clicking a public UI**, not for shopping checkout.

## The rule that matters more than the plugin brand

**Enumerate this Bot's browser-plugin tools, call one, quote a visible
string, say which plugin.** Empty list → UNVERIFIED. Do not fall through
to cookie-scraping x.com/reddit.com. Agent Computer browser is
`computer-drive`, a different path, labeled `BROWSER` not `PLUGIN`.

## When to use

- Asked to open a public site that needs a real browser (SPA, button,
  rendered heading) and firecrawl scrape is missing or the wrong shape.
- Proving a plugin browser vs the Agent Computer browser.
- Do **not** use for Gmail/Calendar/X/Reddit — those have their own
  plugins. Do not use to send, buy, or pass 2FA.

## Inputs and access

1. **One URL + one observable** (heading, button label, table cell).
2. **This Bot's tools** whose server looks like browser-use, playwright,
   browse, or browserbase. Do not invent names; census had none.
3. **Receipt** under `/workspace/gb-proof/browser-plugin-<date>.txt`.
4. Cloud computer only. Localhost on Studio/Brain is unreachable (NE-2).

## Sequence

1. **Enumerate** matching tools. Quote names. Empty → `plugin-enable-verify`
   (attach plugin to **this** Bot; complete any OAuth).
2. **Prefer firecrawl-read** if the observable is page text and firecrawl
   tools exist.
3. **Drive once.** Navigate, snapshot/screenshot or accessibility tree,
   quote the observable. Label PATH `PLUGIN` and the plugin name.
4. **Wall** — password, 2FA, CAPTCHA, payment, identity: stop. Screenshot.
   Do not type secrets. `takeover-2fa`.
5. Close the session. Do not leave a logged-in shared browser.

## Validation

- Quoted observable came from the plugin call, not from curl.
- Tool names in the output exist on this Bot's list.
- No send, purchase, or typed secret.
- If tools were empty, UNVERIFIED, not a fake PASS.

## Output

PATH (`PLUGIN` / `UNVERIFIED` / `REFUSED`), plugin name, URL, quoted
observable or blocker, receipt path, next human action or `none`.

## Boundaries

- Never send, post, buy, book, or submit a form that leaves the page.
  Owner's explicit yes on that exact act only.
- Never type passwords, OTPs, or cards. Never dump cookies into chat.
- Never claim per-Bot isolation; one cloud computer, shared sessions.
- Never use Execution on Local Computer for this. Not Studio Chrome.
- Never install extra browser packs to "make playwright work."
