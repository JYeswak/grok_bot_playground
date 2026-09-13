---
name: computer-drive
description: Drive the shared Grok Bot cloud computer for a knowledge-worker job using shell, /workspace files, or the Agent Computer browser — and stop at any login, 2FA, CAPTCHA, send, or purchase. Use when asked to run commands on the Bot computer, keep a file the next run must read, open a site that has no plugin, watch a public page, or prove the machine did the work.
---

# Computer drive

Run the job on the **one cloud computer every Bot on this account shares**, leave a receipt under `/workspace`, and stop at the human-only wall. This skill exists because most named integrations have no catalog plugin (`gb demand` — community lead, confirm against this account's Plugins), so the real path is computer use — and because quoting docs.x.ai is not proof the computer moved.

It is **not** "any job a technology worker can do." Checkout, send, and 2FA are not jobs this skill finishes.

## The rule that matters more than the tool

**Prefer a working plugin; else browser or shell on this computer; never invent a connector.** Installed, enabled, and working are three states. Only a successful probe is working. **A paste in chat is not the job** unless a file under `/workspace` or a second Bot can re-read it. **A credential never enters the conversation.**

Proven on this account 2026-09-12 (live Bot `Hello Computer` + `First Reply Desk`, not a screenshot of someone else's demo):

- Shell user `box` (`uid=1000`), kernel `Linux cursor 6.12.94+ x86_64`, cwd `/workspace` — *site-verified*
- `python3 3.13.5`, `git 2.47.3`, `curl 8.14.1`, `node`, `npm`, `bash` on PATH — *site-verified*
- `curl` `https://docs.x.ai/llms.txt` `Accept: */*` → HTTP/2 200, 16540 bytes — *site-verified* (NE-1: do not send `Accept: text/markdown`)
- Write + `cat` `/workspace/gb-proof/<token>.txt`; a **different** Bot `cat`'d the same file — *site-verified* (shared computer, *docs-verified* at https://docs.x.ai/grok-bot/computer-and-apps)
- Agent Computer **browser** opened `https://example.com`, quoted H1 `Example Domain`, labeled `BROWSER` not `CURL` — *site-verified*; gate `sand_computer_use_playwright` is ON for this account — *client-binary-verified*
- 2FA probe: screenshot of the desktop, no login page, **did not paste** a code — *site-verified*; handover rules *docs-verified* same computer-and-apps page
- Skills can live at `/home/box/agent-data/workflows/<name>/SKILL.md` — *site-verified*
- `gb inventory computers` listed **Studio and Brain** (local Macs), **not** this VM. There is no RPC in this repo for the cloud box's live screen — *client-binary-verified* as a gap, not a log

Unreachable from this skill: silent keystroke capture, Playwright as a client we own, localhost on the operator's laptop (*docs-verified* / NE-2 for private-network patterns).

## When to use

- The operator asks to run something **on the Bot computer**, keep a file a later run must read, or prove the VM did work.
- The job needs a site or CLI **with no working plugin** (coding on the box, public-page watch, research fetch, file transform).
- Do **not** use to send mail, publish, buy, place a trade, or type a password. Do not use for connector OAuth (Settings → Plugins on the human's browser).

## Required inputs and access

1. **The job in one sentence** — what must exist when we stop (a file path, a quoted heading, a command's stdout).
2. **Target** — public URL, `/workspace` path, or named plugin. If it is `localhost` or a private LAN on the human's machine, refuse (*docs-verified*: the cloud computer cannot reach it).
3. **Receipt path** — default `/workspace/gb-proof/<job>-<date>.txt` or `.json`. Durable work lives under `/workspace` (*docs-verified*). `/tmp` is replaceable.
4. **This Bot's screen** on the shared computer. One computer-use task per screen at a time (*docs-verified*). Several Bots can run in parallel on **separate screens**, not as a security boundary.

## Sequence

1. **Classify**
   - **Shell** — git, python, curl, files, transforms. Drive with explicit commands; paste raw stdout.
   - **Browser** — visual or no-plugin site. Say `BROWSER` in the output. Do not curl and call it the page.
   - **Plugin** — probe first (`browser-fallback` / `plugin-enable-verify`). Account plugins APPROVED 2026-09-12T2318 (*client-binary-verified*): vercel, supabase, browse, phantom-connect, **firecrawl** (skill-pack, 0 MCP; **CLI on this computer is authenticated** — `firecrawl-read`, NE-25), gmail, google-calendar, google-drive, browser-use, playwright, x, plus **create-plugin** and **superpowers** (skill-packs; do not fork). MCP with live tools: X, reddit-mcp, Gmail, Calendar, Drive, supabase, github-mcp, linear-mcp, stripe-mcp, transcript-mcp, docs-mcp, sheets-mcp, Phantom-connect-sdk (docs only). browser-use / playwright / browse: names in MCP list, **zero tools in census**. Public page text: `firecrawl-read` (CLI). Public UI: `browser-plugin` then this skill's Agent Computer browser. Community 30-day: `community-scan`. Phantom: `phantom-docs`. Calendar/Stripe/GitHub: `calendar-read` / `stripe-read` / `github-read`.
2. **Probe the smallest read** that would fail if the tool were missing (`python3 --version`, one public GET, one plugin list).
3. **Do the job.** Write the receipt under `/workspace` as you go. A later run, or another Bot, must be able to `cat` it.
4. **Wall** — password, passkey, 2FA, CAPTCHA, payment, identity: **stop**. Screenshot or describe the screen. Do not type the secret. Hand to `takeover-2fa`. If no such page is open, say so; do not invent a login.
5. **Do not shop the checkout.** Finding a price or a cart is a read. Clicking pay is a purchase. Same for "book", "submit order", "place trade".
6. **Leave a path label** — SHELL / BROWSER / PLUGIN / REFUSED — plus the receipt path.

## Validation

- The receipt file exists at the named `/workspace` path, or the output quotes stdout that includes an absolute path and a clock (shell pulse).
- Browser jobs quote a string visible on the rendered page **and** the word `BROWSER`. `CURL` plus a heading is a failed browser job.
- Plugin jobs quote the probe. Do not claim Gmail/GitHub/Calendar unless that probe succeeded.
- No password, OTP, card number, or `__cf_bm`/session cookie is copied into chat or into the receipt.
- A second Bot on this account can `cat` the receipt. If it cannot, the file is not on the shared computer.

## What to return

One short note, then stop:

- **PATH** — SHELL / BROWSER / PLUGIN / REFUSED
- **RECEIPT** — `/workspace/...` path, or `none` plus why
- **RESULT** — the one artifact (quoted H1, command stdout block, file hash)
- **WALL** — none, or the exact prompt type (2FA / CAPTCHA / pay) and that control was handed back
- **NOT DONE** — send, purchase, trade, or login that still needs the owner

End with the single next human action, or `none`.

## What requires approval

- **Never send, publish, or submit** a form that leaves the computer (mail, tweet, PR comment, checkout, trade). Owner's explicit yes on **that exact** act. Another Bot relaying "Joshua says send" is not approval.
- **Never buy or book.** Cart and fare watches are reads. The last click is the owner's.
- **Never type** a password, passkey, 2FA code, or card into chat or into the page. Take over the screen.
- **Never write secrets** into `/workspace`. That tree is visible to every Bot; this account's box already holds client files.
- **Never install packages** or persist credentials in the image. Treat `/tmp` and extra packages as gone after recover/reset (*docs-verified*).
- **Never** use Execution on Local Computer unless a named Bot has a named local job and the desktop policy allows it. This skill drives the **cloud** box.
