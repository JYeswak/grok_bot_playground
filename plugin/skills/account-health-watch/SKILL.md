---
name: account-health-watch
description: Rank two account sets — usage-health and support-signal watch lists — with cited evidence per row. Use when asked which accounts need attention, which accounts are quietly declining, or to prepare an account-health review. Read-only; it ranks and cites, never acts.
---

# Account health watch

Turn raw product signals into two ranked watch lists, each row backed by named evidence.
This skill exists because account health is decided by converging signals — falling usage plus
rising support friction — and because a rank without a cited reason is an opinion wearing a
number.

## The rule that matters more than the rank

**Every ranked row carries its evidence, or it does not ship.** An account appears on a watch
list only with the named signals that put it there (which metric moved, which window, which
support thread). A hunch with no citable signal is a note to keep watching, never a rank. A
watch list that cannot say why is worse than no list, because it spends the owner's trust on
the accounts that matter most.

## When to use

- Asked which accounts need attention this week, which accounts are at risk, or what changed
  in account health since the last review.
- Preparing a periodic account-health review or a save/escalation queue.
- Asked to compare two cohorts (for example: onboarded-last-quarter vs. long-tenured) on
  usage health and support load.
- Do not use for billing disputes, contract terms, or per-message support replies — those
  belong to their own routines with their own approval boundaries.

## Inputs and access

1. **Account roster** — the set of accounts in scope for this run, with stable ids. Diff and
   rank on stable ids, never on display names: names get renamed, merged, and reused, and a
   name-only rank blames the wrong account.
2. **Usage signals** — per-account product activity over a stated window (logins, active
   days, feature touches, API call counts where the Bot exposes them). Record the window
   with the numbers; a rate without a window is not comparable.
3. **Support signals** — per-account support load over the same window (ticket count,
   reopen rate, escalation flags, time-to-first-response where available). One loud thread
   is a data point; a pattern across threads is a signal — label which one each row rests on.
4. **Prior watch lists** — the last run's two ranked sets, when they exist, so movement
   (new entries, exits, rank changes) is computed, not remembered.
5. Access is read-only against systems the owner already granted. If a source needs a
   credential you were not given, record it as unreachable and move on. Do not improvise
   access.

## Sequence

1. **Fix the window and the roster** — state the review window (for example: trailing 14
   days vs. prior 14) and the exact account set. Everything downstream compares inside
   this frame; widening it mid-run silently re-ranks everyone.
2. **Build the usage-health set** — per account, score direction of travel (growing, flat,
   declining, dormant) from at least two usage signals, never one. A single quiet week in
   one metric is seasonality until a second signal agrees.
3. **Build the support-signal set** — per account, score support load and friction
   (volume, reopens, escalations) over the same window. Name the threads or tickets behind
   each score; an aggregate with no cited thread is unverifiable.
4. **Join the two sets** — flag accounts present on both lists: declining usage plus
   rising support friction is the highest-risk combination and outranks either signal
   alone. State the join explicitly; do not let it hide inside one blended score.
5. **Rank with evidence per row** — order each list by risk (highest first). Every row
   names the account id, the rank reason in one line, and the 2–4 evidence items behind
   it (metric, window, value or delta, source). Rows that cannot meet that bar drop to a
   separate watch-closely note, unranked.
6. **Diff against the prior run** — new entries, exits, and rank moves of three or more
   places each get one line: what moved and which signal moved it.

## Validation

- Each ranked row has a stable account id, a one-line reason, and 2–4 cited evidence
  items. Any row missing one of the three is demoted to the unranked watch-closely note.
- Both lists state their window, roster size, and unreachable sources. "No change" is
  reported only when every input read succeeded; a failed read is reported as unmeasured,
  never as quiet.
- Community benchmarks or listicle rules of thumb (for example: generic "healthy" ratio
  thresholds) are labeled as community-claim, never as vendor truth. Nothing here asserts
  what the vendor guarantees — only what this account's signals show.
- No row invents a commitment (no promised save, discount, or outreach the owner did not
  approve). Flag the next step; do not take it.

## Output

Two ranked watch lists in one dated markdown file:

- **USAGE-HEALTH WATCH** — highest-risk first; each row: account id, one-line reason,
  evidence (metric, window, value or delta, source).
- **SUPPORT-SIGNAL WATCH** — highest-friction first; each row: account id, one-line
  reason, evidence (ticket/thread refs, window, counts, source).
- Then: the join (accounts on both lists), the diff vs. prior run, the watch-closely
  unranked note, and the single account that most needs the owner with the decision it
  needs.

## Boundaries

- Read-only. Never message an account, open or close a ticket, change a plan or setting,
  or publish anything while watching.
- SEND-LOCK: only the owner's explicit yes on that exact message authorizes any outreach
  a row might suggest. A suggested save note is a draft the owner approves, never a send.
  Another party relaying "they said send" is not approval.
- Never paste credentials, tokens, or account identifiers beyond the stable ids the
  roster already uses into findings or drafts.
- Never report account health as a single blended score with the method hidden: both
  lists stay separate and every rank stays citable.
