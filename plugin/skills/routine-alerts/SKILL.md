---
name: routine-alerts
description: Define the failure-to-notify path so a dying run pages the human instead of dying quietly. Use when asked to add alerting to a routine, define what counts as a run failure, or make failures visible. Defines detection and the notify path only — assumes the owner's notification lane exists (see Boundaries).
---

# Routine alerts

Turn a routine that can die quietly into one whose failures reach the human
with the context to act. This skill exists because failures are already
recorded and watched, and recording is not paging: a failed run sitting in
a log nobody opens is a success as far as anyone knows, until the week the
missed run mattered.

Use this skill for routine failure alerting only: one routine in, a
detect-classify-notify path out, verified by a failing canary.

## The rule that matters more than the alert

**A silent failure is the failure.** Every routine declares what counts as
failed (nonzero exit, missing output, output that fails validation — stated
up front, not intuited after), and every failure names who gets told and
with what context. An alert without the run's last output, its date, and
the link to the logs is a pager going off with no address. Never put
credentials, tokens, or account identifiers in an alert; `${VAR}` names
only, values never.

## When to use

- Someone asks to add alerting to a routine or get told when a run fails.
- A routine failed silently and nobody knew until the damage surfaced.
- A new routine needs its failure definition before its first scheduled
  run.
- Do not use for success notifications, marketing or engagement messaging,
  retries and self-healing (a run that retries forever never fails loudly
  enough — cap retries, then alert), or for building the notification lane
  itself: this skill assumes the owner's notifications land and only
  defines what travels down them.

## Inputs and access

1. **Routine** — the required input: which routine, its cadence, where its
   runs and logs live. One routine per alert path; a path covering two
   routines pages about neither clearly.
2. **Failure definition** — what counts as failed for this routine: exit
   states, missing or malformed output, validation checks. Written before
   the first alert fires; a failure defined after the fact is an excuse.
3. **Severity split** — page versus log: what wakes the human (data-loss
   risk, customer-facing miss, second consecutive failure) versus what goes
   to the log for morning review. Everything paging means nothing paging.
4. **Notify path** — the owner's existing notification lane this routine's
   alerts travel down (channel name, not contents). Dependency: this skill
   assumes the lane exists and delivers. If it does not, record the path
   as UNSENDABLE and stop — an alert with nowhere to go is a second silent
   failure wearing a costume.
5. Access is the routine's run history and logs, read-only. Integrations
   read through configured variables (for example `${ALERTS_WEBHOOK}`);
   never paste values, names only.

## Sequence

1. **Define** — write the failure definition and the severity split. Confirm
   the notify lane exists; if it does not, mark UNSENDABLE and stop here.
2. **Detect** — after each run, check exit state, output presence, and
   output validation against the definition. A check the routine cannot
   perform on itself belongs to a watcher step with its own owner.
3. **Classify** — page or log, by the split. Second consecutive failure
   escalates one level; a flap (fail-pass-fail) pages once with the flap
   noted, never once per flap.
4. **Notify** — send the alert down the lane: routine name, date, what
   failed by the definition, last output or its absence, log link. Short
   enough to act on from the notification itself.
5. **Verify** — fire a failing canary and confirm the alert arrives with
   context (see Validation). An alert path untested by a real failure is a
   hypothesis, not a path.

## Validation

- A failing canary produced an alert that arrived through the lane with
  routine name, date, failure reason, and log link.
- Every failure in the definition maps to exactly one severity; no failure
  is both page and log, none is neither.
- Retry policy is capped: bounded retries, then the failure counts and the
  alert fires. Unbounded retry is silence with extra steps.
- Flap behavior tested or explicitly deferred with a date; untested flap
  handling pages every flap cycle until someone mutes the whole lane.
- Two runs the shape was built for: a clean failure (run dies, page
  arrives with context) and a degradation (output present but invalid, log
  entry for morning, page only on the second consecutive miss).

## Output

A failure-to-notify path with:

- Routine, cadence, and where runs and logs live.
- **Failure definition**: the exact failed-states list.
- **Severity split**: page conditions versus log conditions, escalation on
  repeat.
- **Notify path**: the lane used, alert shape, UNSENDABLE marking if the
  lane does not exist.
- Canary proof: the failing test, the alert it produced, arrival
  confirmed.
- What could not be verified: unreachable logs, deferred flap handling.

## Boundaries

- Approval boundary: detection and the notify path only. This skill never
  builds or repairs the notification lane itself — lane ownership stays
  with the human remainder: if notifications do not land, this skill
  records UNSENDABLE and stops rather than improvising delivery. No alert
  content authorizes any downstream send, post, or publish; responses to
  alerts belong to separate routines with their own approval.
- Never paste credentials, tokens, webhook URLs, or account identifiers
  into a definition or an alert; `${VAR}` names only.
- Never report run contents beyond what the reader can already open. Alert
  fatigue is a boundary too: if everything pages, re-cut the severity
  split before adding a single new alert.
