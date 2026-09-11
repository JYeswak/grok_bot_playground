---
name: gap-kit-setup
description: Walk through enabling this plugin's skills on a Bot, verify each one actually took, and set any keys the safe way
---

# Set up the gap kit

Run this once per Bot. It checks rather than assumes: every step ends with something you can
see, because "enabled" and "working" are different claims.

## 1. Confirm the skills loaded

Ask the Bot: **"list your skills"**. You should see `experiment-readout`, `doc-extract` and
`deployment-audit`. If a skill is missing, the plugin is installed but not enabled for THIS Bot
— open **Settings → Plugins**, find the plugin, and enable it for the Bot you are configuring.

## 2. Prove the machine works before you trust the answers

Two of these skills compute on the Bot's own Linux computer rather than guessing. Verify that
path is alive:

> Run `python3 -c "import statistics; print(statistics.mean([1,2,3]))"` on your computer and
> paste the output.

If that fails, the Bot cannot compute and the statistics skill will be worthless — fix the
computer (**Settings → Beta → Update Agent Computer**) before continuing.

## 3. Keys, if a skill ever needs one

Nothing here requires a key today. When one does, it goes in **Settings → Plugins → Configure**
as a declared variable, never in a chat message. A key pasted into chat lives on in the
transcript, the local replica, and any share link made from that Bot.

## 4. Give it one real job

The kit is inert until something routes to it. Pick the one that matches the Bot:

- statistics desk: paste a real A/B result and ask for the readout.
- document desk: attach a real invoice and ask for the totals with page references.
- deployment audit: ask what changed in your Grok Bot account this week.

## 5. Check it refused something

Ask the statistics skill: **"just tell me if it's significant"**. A correctly loaded skill
answers with the effect size and interval and declines the bare yes/no. If it just says "yes",
the skill is not actually in context — repeat step 1.
