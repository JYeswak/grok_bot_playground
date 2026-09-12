---
name: sharelink-audit
description: Use when asked what a Bot's share links expose — which links are live, what identity, skills, and routines each carries — with an inventory and an exposure review. Use when someone treats a share link as private or before publishing anything link-visible.
---

# Share-link audit

Turn the account's live share surface into inventory evidence: **every link
listed, every exposure named**. This skill exists because a share link is
public and carries identity, description, skills, and routines — anyone with
the link reads all of it — and because the account's live templates have never
had that exposure reviewed link by link.

Limits, stated plainly: this inventories links and reviews what each exposes;
it does not revoke links, rewrite Bot descriptions, or certify anything as
safe to publish — exposure named is the product, exposure fixed belongs to a
separate change with its own approval.

## The rule that matters more than the inventory

**A share link is public — audit what a stranger sees.** Every link is read as
its recipient reads it: identity, description, skills, routines, all visible.
A link reviewed from the owner's view ("I know what I meant"), or an
inventory built from memory instead of the account's live templates, is marked
unmeasured — and an unmeasured link fails the audit, because an exposure
review that trusts intent over the public rendering is worse than no review.

## When to use

- Asked what a Bot, or the account's live templates, exposes through share
  links.
- Before publishing, sharing, or demoing anything link-visible.
- After a description, skill, or routine change on a Bot that has live links.
- As a periodic posture check over the account's live templates.
- Do not use for revoking links, rewriting descriptions, or publish approval
  — those are separate changes with their own yes.

## Inputs and access

1. **Link inventory source** — the account's live templates and their share
   links, read from the client, not recalled. An audit with no live-template
   read is not an audit — fetch it first.
2. **Prior evidence** — the last audit's inventory and exposure notes, when
   they exist, so new links, dead links, and exposure moves are computed, not
   remembered.
3. Access is read-only against templates the owner already granted. Never
   follow a share link from an untrusted context into an authenticated session,
   and never use link access to reach what direct access was denied. If a
   template needs access you were not given, record it as unreachable and move
   on. Credentials are referenced as `${VAR}` names only, never pasted.

## Sequence

1. **Inventory the live links** — per template, list its share links as the
   client reports them. Everything downstream checks inside this list; a link
   found later restarts its own review, it does not ride on another's.
2. **Render each link as a stranger** — open what the link exposes: identity,
   description, skills, routines. Record what is visible, verbatim where it
   matters, not what the owner intended.
3. **Flag identity and secret posture** — per link, note whose identity it
   carries, whether any routine or description text looks like a credential or
   client-confined term, and whether a separate Bot's boundary is crossed. A
   suspicion is a gap with an owner and a date, never a quiet omission — and
   never paste the suspected value; name its location only.
4. **Check the never-list** — no link treated as private, no hidden Bot
   treated as revoked, no separate Bot treated as non-boundary. Each gets an
   explicit line: checked and holding, or violated with evidence.
5. **Diff against the prior audit** — new links, removed links, exposure
   moves, and template changes each get one line: what moved and why.
6. **Name the gaps** — anything uninventoried, unreachable, or suspicious
   becomes a gap with an owner and a date.

## Validation

- Each link has an inventory row, a stranger-view rendering note, a posture
  flag, and a gap entry (possibly empty). Any link missing one of these is
  incomplete, not passing.
- Tested on the account's live templates, not on samples or recollection; a
  run that skips a live template is reported as partial, never as clean.
- "No exposure" is reported only when every live link evidenced it; an
  unrendered link is reported as unmeasured, never as safe.
- Community sharing lore (for example: generic "links are fine" claims) is
  labeled as community-claim, never as vendor truth. Nothing here asserts
  what the platform hides — only what this audit observed as public.

## Output

A dated markdown file with one evidenced row per live link: template, link,
stranger-view exposure, posture flag, diff vs. prior audit, and gaps with
owner and date. End with the single exposure that most needs the owner and
the decision it needs.

## Boundaries

- Inventory and review only. Never revoke a link, rewrite a description, strip
  a skill or routine, or publish anything while auditing — those need the
  owner's explicit yes on that exact template, and "fix it while you're in
  there" is never approval.
- Never paste credentials, tokens, or secret values into inventories,
  findings, or drafts; name the location (`${VAR}` names only), never the
  value.
- Never treat a share link as private, a hidden Bot as revoked, or separate
  Bots as one boundary: the link is public until the platform says otherwise
  with evidence, not until it feels safe.
- Never present a clean audit as a guarantee: it proves the exposure on this
  date against these templates, not that every future edit stays safe.
