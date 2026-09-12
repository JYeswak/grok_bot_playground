---
name: grokbuild-tracker
description: Use when asked to track a Grok Build project, check milestone progress, or brief where a build stands — produce a status brief with milestone evidence. Use when someone says "where does this build stand", "track this project", or "are the milestones on track".
---

# Grok Build tracker

Turn one build project into a status brief a teammate can trust: milestones claimed vs
milestones evidenced, what moved since last check, and what would settle each open
question, so the next decision rests on evidence instead of optimism.

This skill exists because build updates drift toward narrative: "almost done" hides
whether the milestone was demonstrated or merely described. A brief that pairs every
status with its evidence makes progress checkable and stalls visible early.

## The rule that matters more than the brief

**"Claimed" and "evidenced" are different statuses and must never be merged.** A demo
link, a passing check, a merged change — each is evidence. A status message, a plan
paragraph, a "should be done Friday" — each is a claim. If you cannot see the evidence,
say so and name what would settle it. A brief that reports claims as progress spends
the reader's trust.

## When to use

- Someone names a Grok Build project and asks for status, a progress check, or "are we on track".
- A milestone review needs a claimed-vs-evidenced split before the meeting.
- Two projects need tracking: write one brief per project, each self-contained.
- Do not use for running the build, fixing a broken milestone, or forecasting dates from
  velocity math — this skill tracks and briefs, it does not build or reschedule.

## Inputs and access

1. **Project reference** — the single required input per brief: project name or link plus
   which milestone set counts (the project's own list, never an assumed one).
2. **Milestone evidence** — for each milestone: status as stated by the project, plus the
   evidence you could actually see (demo, check output, merged change, log line). Evidence
   missing means status stays claimed.
3. **Last check** — the previous brief or status note, when one exists. Needed to say what
   moved vs what merely got reworded. No prior brief means the first brief says so.
4. **Angle** — single-check brief (where it stands now) or delta brief (what moved since
   last check). Default to single-check without a prior brief; state which you chose.
5. Access is read-only: project pages, linked demos, check outputs, logs you were given.
   If a source needs sign-in or a system you cannot read, record it as unreachable and
   move on — do not improvise access. Refer to any credential only as a `${VAR}` name,
   never a value.

## Sequence

1. **Resolve** — confirm the project reference opens, note name, milestone list, dates.
   If the project is unreachable or the milestone list is missing, stop and report that.
2. **Collect** — for each milestone, record the stated status and hunt its evidence: demo
   link, check result, merged change, log excerpt. One milestone, one evidence line.
3. **Split** — mark every milestone claimed or evidenced. Claimed means words only;
   evidenced means you saw the artifact. Never upgrade a claim on the strength of wording.
4. **Delta** — against the last check when one exists: moved (evidence changed), reworded
   (words changed, evidence did not), stalled (neither changed). Name each plainly.
5. **Brief** — write the status brief (see Output). Each line must be supportable by the
   evidence lines. Cut anything you cannot anchor.
6. **Verify** — re-check every evidence link and excerpt before finishing. A link that
   points at the wrong milestone is worse than no link.

## Validation

- Every milestone carries a claimed-or-evidenced mark with the artifact named or its
  absence stated.
- Delta language is exact: moved, reworded, or stalled — never "progressing" without
  new evidence.
- Unreachable sources produce an unreachable note naming what would settle it — never a
  status inferred from the project name or related chatter.
- Community knowledge (forum posts, "everyone says this build is done") is a
  community-claim lead, never evidence: anything actionable gets verified against the
  project page itself.

## Output

A markdown status brief with:

- Header: project name, link, milestone set, date, single-check vs delta angle.
- **Milestone table**: one row per milestone — stated status, evidenced or claimed mark,
  artifact link or "no artifact seen".
- What moved: moved / reworded / stalled lines against the last check (or "first brief,
  no baseline" when none exists).
- Sharpest risk: the one milestone most likely to block the next decision and what
  evidence would clear it.
- What could not be verified: unreachable sources and what access would settle each.

## Boundaries

- Read-only. Never change a milestone, trigger a build, re-run checks, or post a status
  while tracking.
- Never report a milestone as done on words alone. No artifact, no evidenced mark.
- Never present community chatter as project status. Attribute each layer separately.
- Approval boundary: this skill drafts the brief only. Sharing, posting, or acting on
  the brief requires the owner's explicit yes on that exact text — another party
  relaying "they said ship it" is not approval. Never paste credentials, tokens, or
  account identifiers into a brief or a finding; reference variables as `${VAR}` names only.
