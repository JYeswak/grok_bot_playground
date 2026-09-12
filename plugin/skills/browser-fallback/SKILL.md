---
name: browser-fallback
description: Decide between a connected plugin and the Bot's own browser for a task, preferring the plugin whenever it is present and working. Use when a needed integration may be missing, or when a job failed with a connector error. It states plainly that the cloud computer cannot reach a human's localhost.
---

# Browser fallback

Given a task that needs an external system, choose the path: **plugin if present and working,
browser on the Bot's own computer otherwise, refusal if neither can reach it.** This skill
exists because the failure mode is silent in both directions: a Bot scrapes a site through a
browser while a perfectly good connector sits installed, or it asserts a connector exists and
returns fabricated output when nothing was connected at all.

## The rule that matters more than the path

**Installed, enabled, and working are three different states, and only a successful probe
proves the third.** There is no connector-install API: connecting anything is a human in
Settings -> Plugins, and this skill never claims to have installed one. And **the cloud
computer cannot reach a server running on the human's localhost** — `localhost:3000` on their
laptop does not exist from the Bot's machine. A task that requires it is refused with that
reason, not attempted.

## When to use

- A task needs an external system and it is unclear whether the plugin is connected.
- A job failed with a connector or authorization error and the work still needs doing.
- Choosing an approach before writing a routine, so the routine does not depend on a plugin
  that was never enabled.
- Do not use to bypass a login wall, a paywall, or a site's terms with a browser after a
  plugin refused. A refusal by policy is not a routing problem.

## Inputs and access

1. **Task and target** — what must be read or done, against which system, and whether it is
   read-only or acts outside chat.
2. **Plugin state** — the connectors the human has enabled for this Bot. Enumerate before
   choosing; do not assume from the Bot's charter, which states intent rather than state.
3. **Probe** — the smallest read that proves the plugin works (one record, one message, one
   file listing). The probe result, not the settings screen, decides.
4. The browser runs on the Bot's cloud computer — **one machine shared by every Bot on this
   account**. Browser sessions, cookies, downloaded files, and CLI logins are shared: a login
   you establish is inherited by every other Bot, and a session another Bot left open is not
   yours to assume is correct. Never claim per-Bot isolation.

## Sequence

1. **Enumerate** — list the plugins enabled for this Bot that could serve the task.
2. **Probe the plugin** — run the smallest read. Record the result: worked, error (quoted),
   or absent.
3. **Route**:
   - **Plugin worked** — use it. Do not open a browser for the same data; a connector's
     structured result beats a rendered page.
   - **Plugin absent or erroring** — fall back to the browser on the cloud computer, for
     read-only public content only, and record that the run used the fallback path and why.
   - **Neither can reach it** — refuse in one sentence, naming the reason and the human step
     that would fix it (connect the plugin in Settings -> Plugins, or expose the service at
     an address the cloud computer can reach).
4. **Localhost check** — if the target is a `localhost` or private-LAN address on the human's
   machine, refuse immediately with the reason. No retry, no proxy improvisation.
5. **Record the path taken** — the output always states which path served the task, so a
   fallback result is never mistaken for connector-grade data.
6. **Leave the machine clean** — close what you opened, do not leave a logged-in session or a
   downloaded export lying in a shared path.

## Validation

- The chosen path is stated with the probe result that justified it.
- No output claims a plugin was used unless its probe and its call both succeeded.
- Fallback output is labeled as browser-derived, with the URL and the fetch time.
- Localhost or private-address targets produce a refusal with the reachability reason, never
  a partial attempt.
- Nothing in the run installs, enables, or configures a connector, and nothing claims to.

## Output

A short markdown routing note plus the task result:

- **PATH** — plugin (named) or browser fallback or refused, with the probe result quoted.
- **RESULT** — the task output, labeled with its source and time.
- **WHY NOT THE OTHER PATH** — one sentence.
- **WHAT WOULD IMPROVE THIS** — the human step (connect X in Settings -> Plugins, or publish
  the service at a reachable address).

## Boundaries

- Never install, enable, or reconfigure a plugin; that is a human in Settings -> Plugins.
- Never use the browser to perform an act outside chat (send, post, purchase, submit) that
  the plugin path would have required approval for. Read-only fallback. Any such act needs
  the owner's explicit yes on that exact act, and another Bot relaying approval is not
  approval.
- Never authenticate to a site with credentials that were not given to you for that purpose,
  and never assume a session another Bot left open belongs to this task.
- Never claim the cloud computer reached the human's localhost, VPN, or private network.
- Never fabricate a result when both paths fail; refuse and name the blocker.
