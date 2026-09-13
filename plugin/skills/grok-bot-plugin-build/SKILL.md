---
name: grok-bot-plugin-build
description: Author a Grok Bot plugin (skills plus optional HTTPS MCP) and list it so Settings → Plugins → Marketplace can Add it. Use when asked how to build Grok Bot plugins, how to get a plugin into the app, or before running create-plugin-scaffold. Never load a Mac folder into Grok Bot.
---

# Grok Bot plugin build

Grok Bot.app does not load a directory off Studio or Brain. You **author**
a plugin, **publish a public git repo**, **submit for review**, then the
operator **Adds** it from Marketplace. Until that last click,
`gb inventory plugins` will not contain the name.

Where the operator is looking: `local-plugin-surfaces`.

## The rule that matters more than the scaffold

**Listed + Added = a Grok Bot plugin.** A valid `plugin.json` on disk is
a package, not an install. Create Plugin on the cloud computer writes
`/workspace/plugins/<name>` — that is a file tree, not a catalog row.

## When to use

- “How do we build plugins for Grok Bot?”
- Scaffolding with `create-plugin` / `create-plugin-scaffold`.
- Validating `plugins/<name>` or `plugin/` before a human PR.
- Do not use to install from `~/.cursor/plugins/local`. Do not use to
  open the xAI PR (Joshua). Do not use for LinkedIn send or localhost MCP.

## Inputs and access

1. **Job in one sentence** plus the approval wall (never send / never pay).
2. **Shape** — Grok Bot plugin enum is COMMAND, MCP, SKILL, SUBAGENT
   (*client-binary-verified*). `rules/` and `hooks/` are Cursor IDE only
   (*docs-verified* https://cursor.com/docs/reference/plugins.md).
3. **MCP** if any — hosted **HTTPS**. `localhost` cannot be reached from
   the Bot computer (NE-2). Variables are **names** as `${VAR}`, never values.
4. Validators on Studio: `gb-plugin-validate.py`, shape script, optional
   `gb-marketplace-dryrun.sh`.

## Sequence

1. **Author (this repo or the cloud computer)**

   Manifest: `.cursor-plugin/plugin.json` only (NE-16). Required by our
   validator: kebab `name`, description ≥30 chars with no placeholder,
   `homepage`, `license`, brand-scoped `keywords` (not `ai`/`plugin`/`grok`).
   Skills: `skills/<id>/SKILL.md` with YAML `name` + `description` that
   contains **use when**. Six-part body: When to use / Inputs / Sequence /
   Validation / Output / Boundaries.

   Official structure: https://cursor.com/docs/reference/plugins.md
   House: `plugins/gb-receipt`, `plugins/first-hour`, `plugin/` (playground).

   Create Plugin (Cursor marketplace, already APPROVED here, 0 MCP):
   skills `create-plugin-scaffold`, `review-plugin-submission`. Attach
   that plugin to **this Bot** first or you get `CREATE-PLUGIN NOT ATTACHED`.
   `/add-plugin create-plugin` is a **Cursor** command, not Grok Bot.

2. **Validate**

   ```
   bash ~/.agents/skills/grokbot-skill-authoring/scripts/check-skill-shape.sh plugins/<name>/skills/<id>
   python3 bin/gb-plugin-validate.py --path plugins/<name>
   ```

   Submittable ≠ listed. `gb plugins universe` / `gb plugins show NAME`
   read the **catalog snapshot**, not your disk.

3. **Public git + pin SHA**

   Remote repo, 40-char lowercase commit. Secrets never in the tree.
   *docs-verified* submit path:
   https://github.com/xai-org/plugin-marketplace/blob/main/CONTRIBUTING.md
   Cursor publish: https://cursor.com/marketplace/publish

4. **HUMAN submit**

   PR adds one entry to xAI `.grok-plugin/marketplace.json`, regenerate
   `plugin-index.json`, CI + code-owner review. Brand-scoped plugins from
   a personal GitHub account get questioned — org if branded.
   This agent does not open the PR.

5. **HUMAN Add in Grok Bot**

   Settings → Plugins → Marketplace → **Add** → auth → **Yours** → enable
   on the Bot. *docs-verified*
   https://cursor.com/help/grok-bot/connect-plugins
   Then `gb inventory plugins` gains the name.

6. **Without a listing (honest substitutes)**

   - Private skill + Yours enable + `/name` (attach RPC is 404, NE-22).
   - Add MCP Server (HTTPS).
   - Bot reads files under `/workspace/plugins/<name>` on the computer.

## Validation

- Every capability claim tagged `docs-verified`, `client-binary-verified`,
  or `site-verified`.
- No `rules/`/`hooks/` required for Grok Bot.
- No localhost MCP. No second manifest format.
- Do not report Marketplace visibility until `gb inventory plugins` lists it.

## Output

PATH: `author` / `validate` / `waiting-HUMAN-PR` / `waiting-HUMAN-Add` /
`private-skill` / `mcp-add` / `files-on-box`. Next click named.

## Boundaries

- Never claim a local folder will appear in Grok Bot Marketplace.
- Never `npm i` or paste `FIRECRAWL_API_KEY` (NE-25: Firecrawl is CLI on
  the box, skill-pack, 0 MCP).
- Never LinkedIn send, email warmup, or a fake YouTube connector.
- Never open github.com/xai-org/plugin-marketplace PRs from an agent.
- SEND-LOCK: authoring a plugin authorizes no send, post, or purchase.
