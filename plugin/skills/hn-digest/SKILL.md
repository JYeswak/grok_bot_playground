---
name: hn-digest
description: Use when asked for a Hacker News digest — the day's notable stories with links and dates, distilled to what matters and why. Summarizes public front-page and thread content only; never posts, votes, or comments on anyone's behalf.
---

# Hacker News digest

Turn the day's Hacker News into a short, checkable digest: notable stories with links and
dates, each distilled to its point. This skill exists because the front page moves faster
than anyone reads, and because a digest with links lets the reader verify every item
against the thread itself.

## The rule that matters more than the digest

**Every item must carry its link and date, and an unreachable thread is a finding, not a
gap to fill.** If a story page is gone, a thread failed to load, or comments are thin on
the key claim — say so, name which item is affected, and never fill the gap with what the
headline suggests the thread says. A digest that invents discussion from a title spends
the reader's trust.

## When to use

- Someone asks for a daily Hacker News digest, "what's notable today", or a roundup on a
  topic (launches, papers, outages, hiring threads).
- A story needs distilling: what it claims, what the top comments add or dispute.
- A link is needed to a story or thread and must carry its date so it can be checked.
- Do not use for posting, commenting, voting, or flagging — this skill reads public
  pages only. Do not use for non-Hacker-News tech press; community-pattern claims from
  aggregators are leads (labeled community-claim), never evidence.

## Inputs and access

1. **Scope** — the required input: front page today, or a topic filter (e.g. launches,
   papers, outages). Default to front page when unclear; state which scope you chose.
2. **Story links with dates** — each candidate item carries its discussion-thread link
   and its submission date. Two digests are the comparison input: digest A and digest B,
   each with its own links.
3. **Angle** — daily digest (breadth: what mattered today) or topic roundup (depth: what
   the threads say on one subject). Default to daily when unclear; state which you chose.
4. Access is read-only and public: story pages and comment threads. If a thread needs
   sign-in or is deleted beyond public view, record it as unreachable and move on — do
   not improvise access.

## Sequence

1. **Collect** — gather candidate stories for the scope with links and dates. Note rank
   or score at collection time; it changes under you.
2. **Filter** — keep what is notable for the reader (new information, strong evidence,
   real discussion); cut hype, dupes, and dead threads. State the cut rule you applied.
3. **Read** — open each kept thread far enough to capture the top comments' verdict:
   what they confirm, dispute, or add. Mark threads you could not read as findings.
4. **Distill** — write each item as claim plus thread verdict, each anchored to its link
   and date. Cut anything you cannot anchor.
5. **Verify** — re-check every link resolves to the thread you describe before finishing.
   A link pointing at the wrong story is worse than no link.

## Validation

- Every item carries a discussion-thread link and a submission date.
- Each item's verdict reflects the thread's top comments, not the headline alone.
- Unreachable or deleted threads produce findings — never summaries written from the
  headline.
- Aggregator or community knowledge ("everyone says this launched yesterday") is labeled
  community-claim and verified against the thread itself before use.
- With two digests, every overlap and divergence is named, not silently merged.

## Output

A markdown digest with:

- Header: scope, digest date, angle chosen (daily or topic).
- Items: story title, link, submission date, one-line claim, one-line thread verdict.
- Cut list: stories considered and cut, each with the reason.
- What could not be verified: threads unreachable, thin, or disputed on the key claim.

## Boundaries

- Read-only. Never post, comment, vote, flag, or message any user while digesting.
- Never present headline inference as thread content. Attribute the story, the comments,
  and outside context separately.
- Never paste credentials, tokens, or account identifiers into a digest or a finding.
