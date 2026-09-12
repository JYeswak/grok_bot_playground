---
name: reddit-pulse
description: Read the pulse of one or two subreddits and return dated themes with post links. Use when asked what a subreddit thinks, what people are building or complaining about, or for community signal on a grokbot feature, plugin, or workflow.
---

# Reddit pulse

Turn one or two subreddit reads into a dated pulse: recurring themes, each backed by
linked posts with dates, labeled as community claims — never as vendor truth. This skill
exists because community signal is the cheapest way to spot what grokbot CLI and plugin
builders are actually hitting, and because an unattributed "users say" is how a rumor
becomes a roadmap.

## The rule that matters more than the pulse

**A post is a claim by its author, not a fact about the product.** Every theme carries
its post links and dates; every theme is labeled community-claim. Nothing here is ever
presented as vendor truth, vendor confirmation, or what "the community decided." A pulse
with three linked posts beats a summary of thirty remembered ones.

## When to use

- Asked what a subreddit thinks about a topic, feature, plugin pattern, or workflow.
- Asked for community signal before a grokbot CLI change, plugin addition, or docs edit.
- Asked to compare how two subreddits talk about the same thing.
- Do not use for vendor answers (docs, changelogs, capability gates), for private
  community reads you cannot link to, or as a substitute for measuring this account.

## Inputs and access

- One or two named subreddits plus the question each pulse must answer.
- Read access is public posts and comments only: title, author handle style, date,
  score direction, link, and quoted lines. Never log in, vote, comment, message, or
  use a private feed on behalf of the owner.
- If a subreddit, post, or comment thread is unreachable (private, removed, rate
  limited), record it as unreachable and move on. Do not improvise access.

## Sequence

1. **Scope the read** — write down the question, the subreddit or two, and the
   window (default last 30 days). Two subreddits maximum per pulse; a third is a
   second pulse, not a longer one.
2. **Collect posts** — newest and top posts inside the window that bear on the
   question. Capture title, post link, and post date for each candidate. Skip
   pinned meta threads and removed bodies on the first pass.
3. **Read the threads** — top comments on each kept post, noting agreement,
   disagreement, and workarounds builders report. One quoted line per post maximum,
   with its link.
4. **Cluster into themes** — group posts into at most five themes. Each theme needs
   at least two linked posts; a single post is an anecdote, filed as such, never
   promoted to a theme.
5. **Label every theme community-claim** — state what was said, who said it (linked
   posts + dates), and what would settle whether it holds for this grokbot setup
   (a vendor page, a local measurement, a named next check).

## Validation

- Every theme has two or more post links with dates, or is filed as an anecdote.
- No theme is stated as vendor truth: no "Grok confirms," no "Reddit decided," no
  unlinked consensus language.
- Dates are post dates, not read dates. Score is direction (upvoted, contested,
  buried), never a precise count presented as evidence weight.
- Conflicting posts on the same theme are shown side by side with links, not
  averaged away.

## Output

A dated pulse: the question, the subreddit or two, the window, then one section per
theme with its community-claim label, supporting post links with dates, and the
conflicting view where one exists. End with the single strongest signal and the
check that would confirm or kill it against this grokbot setup.

## Boundaries

- Read-only. Never post, comment, vote, message, or publish anything while pulsing.
- SEND-LOCK: there is nothing here that sends, and only the owner's explicit yes on
  an exact message would authorize any outbound post — which belongs to a different
  routine, not this skill.
- Never paste credentials, tokens, or account identifiers into a pulse or a quote.
- Never present community claims as vendor truth or as instructions for this
  account. Anything actionable gets verified against the vendor's own surface and
  then against this setup before it is acted on.
