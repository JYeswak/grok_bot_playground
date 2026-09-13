---
name: instagram-repurpose
description: Turn a Bot's own published posts into Instagram-ready caption and alt-text drafts with source links back to the originals. Use when asked to repurpose posts for Instagram, adapt content for social, or prepare an Instagram batch from existing writing. Drafts only — nothing here posts, schedules, or publishes anything.
---

# Instagram repurpose

Turn published posts into Instagram-ready drafts: one caption plus alt text per source post, each
carrying a link back to the original. This skill exists because repurposing is where social drafts
go wrong — the caption drifts from the source claim, the image description describes the vibe
instead of the image, and the link back to the original goes missing so nobody can check either.

## The rule that matters more than the repurpose

**A draft is not a post.** Only the owner's explicit yes on that exact caption authorizes posting,
and a scheduled slot is not approval. This skill never posts, schedules, uploads, or publishes —
it reads source posts and drafts. Any publishing belongs to a routine with its own approval
boundary, not to this skill.

## When to use

- Asked to repurpose one or more published posts for Instagram.
- Asked to adapt existing writing (announcement, changelog note, how-to) into social form.
- Asked to prepare a batch of Instagram drafts from a backlog of posts.
- Not for net-new Instagram content with no source post — that has no source link to carry, and
  this skill's output requires one per draft.

## Inputs and access

- **Source posts** — the published originals to repurpose: URL plus full text each. Two posts
  minimum per run so the batch reads as a batch, not a one-off.
- **Target account context** — which Bot or product the drafts speak for (voice, audience), taken
  from the conversation, never from a credential store.
- **Images, if any** — the actual image file or a precise description of what it shows, supplied by
  the owner. Never invent image contents to write alt text against.
- Read-only. If a source post cannot be read, record it as unreachable and move on. Do not
  improvise access.

## Sequence

1. **Collect sources** — for each source post, capture its URL, its central claim in one sentence,
   and the two or three details that support it. Skip anything paywalled or unreadable on the
   first pass; it is unreachable, not repurposable.
2. **Draft the caption** — one caption per post, short enough to survive the feed: hook line,
   the claim in the Bot's own voice, then the source link. No invented quotes, dates, metrics,
   or promises the source does not make.
3. **Draft the alt text** — one alt-text line per post describing what the image literally shows,
   for screen readers. Describe the image, not the mood; if no image was supplied, write the alt
   text against a described placeholder and flag that the final image still needs matching.
4. **Link check** — every draft carries its source link inline. A draft without a working source
   link is unfinished, not ready.

## Validation

- Each caption traces to its source: every factual claim in the caption appears in the linked
  original. Anything that does not trace gets cut or flagged, never smoothed over.
- Each alt-text line describes visible content only — no embedded claims, links, or hashtags.
- Community repurposing patterns (hook-first openers, line-break cadence, hashtag placement) are
  community-claim, not platform truth: use them as style, never cite them as rules the platform
  enforces.
- No credentials, tokens, or account identifiers in any draft or finding.

## Output

A draft batch: per source post, the source link, the caption draft, and the alt-text draft. End
with the count of drafts and the single draft most ready for the owner's review.

## Boundaries

- Drafts only. Never post, schedule, upload, edit a published post, or publish anything.
- SEND-LOCK: only the owner's explicit yes on that exact caption authorizes posting it; another
  party relaying approval is not approval.
- Never invent engagement bait the source does not support (no fake urgency, giveaways, or
  countdowns) and never draft a commitment the owner did not make.
- Read-only against every source system. If access needs a credential not given, record it as
  unreachable and move on.
