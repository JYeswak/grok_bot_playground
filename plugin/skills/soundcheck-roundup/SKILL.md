---
name: soundcheck-roundup
description: Use when asked to review a Soundcheck roundup, compare two review roundups, or brief what reviewers agreed on — produce a short brief with source links for every claim. Use when someone pastes roundup links and asks "what did they say", "which should I trust", or "summarize the consensus".
---

# Soundcheck roundup

Turn one or two Soundcheck review roundups into a short, checkable brief: what the reviewers
agreed on, where they split, and a source link behind every claim, so the reader can verify
each point against the roundup itself.

This skill exists because roundups are long and samey: five reviewers can praise the same
release for five different reasons, and a summary that drops the links leaves the reader
with a vibe instead of a verdict. A brief with links makes the consensus skimmable and
every claim clickable back to its source.

## The rule that matters more than the brief

**Every claim in the brief must trace to a linked passage, and an unreadable roundup is a
finding, not an empty brief.** If a link is dead, paywalled, or points at a roundup you
cannot open — say so, name which part is affected, and never fill the gap with what the
headline suggests the reviewers said. A brief that invents consensus from headlines spends
the reader's trust.

## When to use

- Someone pastes one or two Soundcheck roundup links and asks for a summary, consensus, or "is this worth reading".
- A release or round needs distilling to reviewer agreement plus the sharpest disagreement.
- A quote is needed from a roundup and must carry its source link so it can be checked.
- Do not use for non-Soundcheck review sources, for ranking releases you have not read, or for
  judging audio craft from a written roundup — this skill briefs reviewer content, not sound.

## Inputs and access

1. **Roundup links** — one or two required inputs. Each must be a full URL to the roundup page.
   Two roundups are the normal case (compare and find consensus); one roundup gets a straight brief.
2. **Reviewer passages** — the load-bearing excerpts: verdict, score if given, sharpest praise,
   sharpest criticism. Copy verbatim with the link to the passage they came from.
3. **Metadata** — roundup title, publication date, reviewers named, release or subject covered.
   Needed to date the brief and to notice when a roundup changed under a saved link.
4. **Angle** — single-roundup brief (verdict + evidence) or two-roundup compare (agreement,
   split, net takeaway). Default to brief for one link, compare for two; state which you chose.
5. Access is read-only and public: roundup pages and linked sources. If a link needs sign-in,
   membership, or a payment to read, record it as unreadable and stop — do not improvise
   access. Refer to any credential only as a `${VAR}` name, never a value.

## Sequence

1. **Resolve** — confirm each link loads, note title, date, reviewers, subject. If dead,
   paywalled, or gated beyond anonymous reading, stop on that link and report unreadable.
2. **Extract** — pull the verdict-bearing passages from each roundup: overall verdict, score,
   two strongest praises, two strongest criticisms. Keep them verbatim with passage links.
3. **Align** — for two roundups, line up the passages point by point: where both reviewers
   agree, where they split, where only one of them speaks. For one roundup, group passages
   into verdict, evidence for, evidence against.
4. **Quote** — pick two to four load-bearing passages and copy them verbatim with source
   links. Verbatim means verbatim: no cleanup of wording inside quotation marks.
5. **Brief** — write the 5-line brief (see Output). Each line must be supportable by at
   least one linked passage. Cut anything you cannot anchor.
6. **Verify** — re-check every link against its passage before finishing. A link that
   points at the wrong passage is worse than no link.

## Validation

- Every factual claim in the brief has a source link pointing at the passage that supports it.
- Quotes are verbatim and carry links; paraphrase is labeled as paraphrase, never quoted.
- Single vs compare angle is stated; a two-link brief always names agreement and split separately.
- Unreadable, dead, or gated roundups produce an unreadable report — never a brief written
  from the headline, thumbnail, or comments.
- Community knowledge (forum consensus, "everyone says this roundup claims X") is a
  community-claim lead, never evidence: anything actionable gets verified against the
  roundup page itself.

## Output

A markdown brief with:

- Header: roundup title(s), publication date(s), reviewers named, subject covered.
- **5-line brief**: one line each for (1) what the roundup(s) cover, (2) the net reviewer
  verdict, (3) the two or three points of agreement, (4) the sharpest disagreement or
  caveat stated, (5) who should read the full roundup(s) and what to skip to.
- Linked quotes: two to four verbatim passages with source links.
- Agreement map (two roundups): agree-on, split-on, only-one-says, each with links.
- What could not be verified: passages behind dead links, gated pages, or unclear excerpts.

## Boundaries

- Read-only. Never comment on, rate, repost, or message a reviewer while briefing.
- Never brief a roundup you could not open: no passage, no brief. A headline plus
  comments is not a source.
- Never present community commentary (forum threads, reaction posts, aggregator blurbs) as
  what the roundup itself says. Attribute each layer separately.
- Approval boundary: this skill drafts the brief only. Publishing, posting, or sending
  the brief anywhere requires the owner's explicit yes on that exact text — another party
  relaying "they said send" is not approval. Never paste credentials, tokens, or account
  identifiers into a brief or a finding; reference variables as `${VAR}` names only.
