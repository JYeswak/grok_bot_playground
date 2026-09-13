---
name: local-plugin-surfaces
description: Name which plugin list the operator is looking at and why a folder is missing from Grok Bot Marketplace. Use when gb-receipt or any scaffolded plugin does not show in Settings → Plugins, after Create Plugin, after copying ~/.cursor/plugins/local, or after restarting Cursor or Grok Bot.
---

# Local plugin surfaces

**Grok Bot Marketplace will never list a folder on disk.** That is the
product. Mixing three runtimes is why `gb-receipt` looked “missing”
after it was scaffolded (*site-verified* 2026-09-12).

Companion skill: `grok-bot-plugin-build` (how to author and *list* a
plugin so Marketplace *can* show it).

## The rule that matters more than the folder

**Cursor local ≠ Grok Bot Plugins ≠ Grok Build CLI ≠ marketplace catalog.**
Copying a directory onto Brain, reloading Cursor, or restarting Grok Bot
does not call `ListUserPluginInstalls`.

## When to use

- Settings → Plugins search does not find a plugin we just scaffolded.
- Create Plugin wrote `/workspace/plugins/<name>` and someone copied it
  to `~/.cursor/plugins/local/` on a Mac.
- Studio vs Brain disagree about what is installed.
- Do not use to open the xAI catalog PR (Joshua only). Do not use to
  invent a “load from folder” control — Grok Bot.app has none.

## Inputs and access

1. **Which app** — Grok Bot.app vs Cursor.app vs the Bot cloud computer.
2. **Which tab** — Marketplace vs **Yours**.
3. **Which machine** — `hostname`: Studio, Brain, or `/home/box`.
4. Proof commands: `gb inventory plugins`, `gb plugins show NAME`,
   `ls ~/.cursor/plugins/local`.

## Sequence

1. **Name the list (do this first)**

   | List | Command / click | Shows gb-receipt? |
   |---|---|---|
   | Grok Bot → Settings → Plugins → **Marketplace** | `gb inventory plugins` | **Never** until xAI/Cursor lists it and you **Add** |
   | Grok Bot → Settings → Plugins → **Yours** | enable per Bot | Installed catalog plugins + **private skills**, not Cursor folders |
   | Cursor IDE local | `~/.cursor/plugins/local/<name>` **on that Mac** | Cursor only. Reload Cursor. |
   | Cloud computer | `/workspace/plugins/<name>` and `/home/box/.cursor/plugins/local/<name>` | Files for the VM. Invisible to both Macs. |
   | Grok Build CLI | `~/.grok/plugins/` | **Different product** from Grok Bot.app |

   *docs-verified:* Marketplace vs Yours —
   https://docs.x.ai/grok-bot/settings-and-notifications
   Connect/Add flow —
   https://docs.x.ai/grok-bot/computer-and-apps
   https://cursor.com/help/grok-bot/connect-plugins

   *client-binary-verified* 2026-09-12 Brain `Grok Bot.app` `app.asar`
   (34405120 bytes): string `Yours` present; `plugins/local`,
   `~/.cursor/plugins`, `Install from`, `from a folder`, `Load plugin`
   **all absent** (−1). There is no folder picker.

2. **How Grok Bot *does* add a plugin** (*docs-verified* computer-and-apps)

   1. Grok Bot.app (not Cursor) → **Settings → Plugins**.
   2. **Marketplace** → browse → **Add** → browser auth if asked.
   3. Confirm under **Yours**. Enable tools/skills **on this Bot**.
   4. In chat: `@` connector, `/` skill.
   5. **Add MCP Server** (HTTPS JSON) is the other in-app add path.
      `localhost` is unreachable from the cloud computer (NE-2).

   Installs are **account-wide**. Enable is **per Bot**. No install RPC
   (NE-22 class: skill attach 404). `gb inventory plugins` is the
   marketplace list (13 names on 2026-09-12; no `gb-receipt`).

3. **Private skill (not a plugin tile)**

   Ask the Bot to save a SKILL.md, then **Yours** → enable on that Bot,
   then `/name` in chat. Chat-save often **does not attach** (NE-21).
   The Yours toggle is HUMAN. Pasting SKILL.md into a turn makes the
   Bot regurgitate it (NE-20) — send JOB only.

4. **Persist a sample in this repo (Studio)**

   Land under `plugins/<name>/` with **one** manifest
   `.cursor-plugin/plugin.json` (NE-16: two manifests → NOT SUBMITTABLE).
   `python3 bin/gb-plugin-validate.py --path plugins/<name>`
   Studio: `plugins/install-local.sh --apply` → Cursor local **only**.
   Brain has **no** grokbot clone: `scp -r plugins/<name>
   brain:~/.cursor/plugins/local/<name>`. Reload **Cursor**, not Grok Bot.

5. **gb-receipt (worked example, 2026-09-12)**

   Hello Computer + Create Plugin wrote the VM paths. Copy to Brain
   Cursor local. Repo copy: `plugins/gb-receipt`. Marketplace search
   still empty. That is correct.

## Validation

- Marketplace omit of a local name is **GREEN**, not a bug.
- Cursor local: `test -f ~/.cursor/plugins/local/<name>/.cursor-plugin/plugin.json`
  on the Mac where Cursor should see it. Studio ≠ Brain.
- Do not claim Grok Bot shows it. Do not claim a restart registered it.

## Output

List named, app named, machine named (`hostname`), path, next click:
Marketplace **Add** / Yours enable / Cursor reload / `none`.

## Boundaries

- Never tell the operator to look in Marketplace for a disk folder.
- Never treat Cursor local as Grok Bot.
- Never treat Grok Build `~/.grok/plugins/` as Grok Bot.app.
- Never paste secrets. Never open the catalog PR from an agent.
- SEND-LOCK: copying a plugin authorizes no send.
