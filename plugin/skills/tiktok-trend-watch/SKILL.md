---
name: tiktok-trend-watch
description: Scan TikTok for emerging trends relevant to a Bot's niche and produce a dated brief with creator links, dates, and reuse ideas. Use when asked what is trending on TikTok, which sounds or formats to ride, or whether a trend is still fresh enough to join. Read-only — drafts ideas, never posts or publishes anything.
---

# TikTok trend watch

Turn a niche question into a dated trend brief: which sounds, formats, and hooks are rising,
who started them, when, and which one is worth the Bot owner's next post. This skill exists
because several Bots in the corpus build on TikTok with no plugin behind them — the trend
scan is the reusable part, and it works the same whether the owner posts by hand or through
a posting routine later.

## The rule that matters more than the brief

**A trend sighting is a community claim, never vendor truth.** TikTok publishes no public
trend API for this skill to read, so every item below comes from public pages, creator
captions, and third-party trackers — each of which can be stale, gamed, or wrong. Label
every finding with where it was seen and when, and never present listicle knowledge ("top
10 sounds this week") as measured platform data. A brief that says "three creators, linked,
this week" is useful; one that says "TikTok confirms" is fiction.

## When to use

- The owner asks what is trending on TikTok in their niche, which sound or format to use,
  or whether a specific trend is still fresh.
- A weekly or pre-post routine needs a dated snapshot of the niche before drafting content.
- Do not use for posting, scheduling, commenting, or account actions — those belong to a
  routine with its own approval boundary, not to this skill.

## Inputs and access

1. **Niche + audience** — the topic lane (one sentence) and who the owner posts for. No
   niche, no scan: "all of TikTok" is not answerable.
2. **Trend scans** — at least two independent sightings per claimed trend: creator link,
   creator handle, post date, and what was observed (sound, format, hook, caption angle).
   One video is an anecdote; two dated sightings make it a candidate.
3. **Reference window** — how far back counts as fresh (default: last 14 days). Anything
   older is context, not a trend.
4. Access is public pages only. If a video, profile, or tracker needs a login the skill
   was not given, record it as unreachable and move on. Do not improvise access.

## Sequence

Do these in order; each step feeds the next:

1. **Fix the niche query** — restate the lane in one line and name what is out of scope.
   A scan that drifts lanes reports noise as movement.
2. **Collect sightings** — gather candidate trends, two dated creator links minimum each.
   Record handle, post date, and the observed element (sound name, format beat, hook line).
3. **Date-check freshness** — newest sighting inside the reference window keeps the trend
   alive; all sightings older than the window demotes it to context. Say which rule each
   trend passed or failed.
4. **Cross-check the claim** — for anything actionable, verify against a second source: a
   different creator, a tracker page, or the sound page itself. Anything that cannot be
   second-sourced stays labeled single-sighting, never promoted.
5. **Rank for reuse** — order surviving trends by fit to the owner's niche and effort to
   join (sound reuse, format copy, original twist). One ranked list, not twenty links.

## Validation

Before writing the brief, check every item:

- Each trend has two creator links with handles and post dates, or is marked
  single-sighting and excluded from the top pick.
- Every date falls inside — or is explicitly flagged outside — the reference window.
- Every source is labeled: creator page, sound page, tracker, or community listicle.
  No unlabeled "TikTok says" anywhere.
- No invented metrics: no view counts, rankings, or growth claims except numbers copied
  verbatim from a linked page, quoted with the page named.

## Output

A dated markdown brief:

- One section per surviving trend: what it is, the two (or more) creator links with
  handles and post dates, the source label, and the reuse idea for this owner's niche.
- A context shelf: older or single-sighting items, clearly marked, not ranked.
- End with the single trend most worth joining and the first post idea for it — not a
  list of twenty.

## Boundaries

- Read-only plus ideas. Never post, schedule, comment, like, follow, or publish anything.
- SEND-LOCK: only the owner's explicit yes on that exact post authorizes publishing, and
  another party relaying "they said post" is not approval. Posting belongs to a routine
  with its own boundary, never to this skill.
- Never paste credentials, tokens, session cookies, or account identifiers into a brief
  or a finding.
- Never present community or tracker claims as vendor-confirmed data; keep the
  community-claim label on every sighting.
- If a page needs access you were not given, record it as unreachable and move on. Do
  not improvise access.
