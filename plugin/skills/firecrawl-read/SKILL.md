---
name: firecrawl-read
description: Fetch a public page as markdown through the Firecrawl plugin. Use when asked to scrape, map, or search the public web after the firecrawl plugin is enabled. Never interact, log in, or paste an API key.
---

# Firecrawl read

Turn one public URL or search into dated markdown on the **shared cloud
computer**. This account's **firecrawl** plugin is APPROVED (2026-09-12,
*client-binary-verified*) but it is a **skill-pack with 0 MCP servers**
(marketplace row + `gb inventory mcp` `tool_census` still empty at
23:29Z). The working path is the **CLI on `/home/box`**, authenticated
the same day: Joshua completed `firecrawl login` with an agent on that
computer; `Login successful (team: Personal)`; scrape of
`https://example.com` returned markdown (*site-verified*, operator
report — do not re-run to "confirm").

Do **not** vendor firecrawl's AGPL skill pack (`interact` / login). Do
not `npm i -g firecrawl-cli` again. Do not paste `FIRECRAWL_API_KEY`.

## The rule that matters more than the scrape

**Probe `firecrawl --status` on this computer, then scrape once, write
the receipt.** MCP tools are optional; they were absent when CLI already
worked. An empty MCP list is not UNVERIFIED anymore. Unauthenticated
CLI is. Credits and concurrency come from **this** `--status` line, not
from a remembered 1,209.

If MCP `firecrawl_*` tools appear later, prefer scrape/search/map over
CLI. Never call `interact` / `agent`.

## When to use

- Asked to read, scrape, map, or search a **public** page that `curl` cannot
  render.
- Choosing a fetch path: firecrawl (structured) beats browser-plugin beats
  Agent Computer browser beats curl (`browser-fallback`).
- Do **not** use to log in, click, fill forms, crawl behind auth, or monitor
  a site on a schedule (that is a routine with its own boundary).

## Inputs and access

1. **URL or query** — public. `localhost` / private LAN → refuse (NE-2).
2. **CLI** — `firecrawl` on PATH on the cloud computer. `--status` must
   say authenticated. If it does not, stop: HUMAN `firecrawl login
   --browser` on this screen (`takeover-2fa`). This skill does not log in.
3. **Receipt** — `/workspace/gb-proof/firecrawl-<date>.md`.
4. MCP tools, if any, are a bonus. Census 2026-09-12T23:29Z: none.

## Sequence

1. **`firecrawl --status`.** Quote authenticated vs not, and the
   concurrency line. Do not quote secrets. Unauthenticated → REFUSED.
2. **One read** — `firecrawl scrape <url>` (or search/map). Not crawl,
   not interact.
3. **Write the receipt** under `/workspace`. Quote URL, time, a short
   excerpt.
4. **Wall** — login, paywall, CAPTCHA: stop. Never `interact`.
5. PATH = `SHELL` (CLI) or `PLUGIN` (if an MCP scrape tool actually ran).

## Validation

- `--status` was authenticated in this run, or the run stopped.
- Output names `firecrawl scrape` (or the MCP tool) and the URL.
- Receipt exists under `/workspace`.
- No `interact`, no new CLI install, no API key in chat or the receipt.
- Plugin APPROVED is *client-binary-verified*. This conversation's
  scrape is *site-verified*. MCP tool names from GitHub stay
  *community-claim* until listed live.

## Output

PATH, command or tool, URL/query, dated excerpt, receipt path, `--status`
concurrency, next human click or `none`.

## Boundaries

- Read-only public web. Never `interact`, never fill a form, never pay.
- Never run `firecrawl login` or paste keys. Auth already happened on
  this computer; a dead session is a human re-login, not a chat secret.
- SEND-LOCK: a scrape authorizes no post, email, or issue comment.
- Never treat account-level APPROVED or an empty MCP list as failure
  now that CLI auth is the measured path.
