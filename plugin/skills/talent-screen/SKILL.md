---
name: talent-screen
description: Screen a candidate set against a scored rubric with cited evidence per criterion. Use when asked to screen resumes, shortlist candidates, or compare applicants for a role. Read-only; it scores and ranks, never contacts, rejects, or advances anyone.
---

# Talent screen

Turn a set of resumes into a scored screen where every score cites the resume line
behind it. This skill exists because screening fails silently: criteria applied from
memory instead of the stated rubric, a score with no quoted evidence, a shortlist that
hides its method — and a rank without a citable reason is an opinion wearing a number,
spending the hiring manager's trust on people's livelihoods. Doctrine source: the
vendor Talent Scout use-case (resume intake to scored screen with ranked shortlist);
nothing here asserts vendor guarantees, only what these resumes show.

## The rule that matters more than the screen

**A score carries its quoted evidence, or it does not ship.** Every criterion score
cites the resume lines that earned it (which bullet, which number, which years). A
candidate appears on the shortlist only with scored criteria behind the rank. A screen
that cannot say why is worse than no screen, because it launders instinct into process
on decisions that change lives.

## When to use

- Asked to screen resumes, score applicants against a rubric, or shortlist
  candidates for a role.
- Preparing a hiring-manager readout: ranked candidates with per-criterion scores
  and the evidence behind each.
- Asked to compare two candidate sets (for example: inbound vs. sourced) on the
  same rubric.
- Do not use for contacting, interviewing, rejecting, or advancing anyone — those
  are hiring acts with their own approval boundary, never part of this skill.

## Inputs and access

1. **Candidate set** — the resumes in scope, each with a stable id (filename or
   record id). Score on ids, never on names alone: names collide, and a name-only
   rank blames the wrong person.
2. **Role rubric** — the scored criteria in force (for example: directly relevant
   years, shipped artifacts, domain match, communication signal), each with its
   weight and what counts as evidence. Name every criterion before scoring; a
   criterion applied but never stated is a silent verdict.
3. **Role context** — the role description, level, and must-haves vs. nice-to-haves
   as the hiring manager stated them. Manager wording is the bar; outside
   assumptions never raise or lower it.
4. **Prior screen** — the last run's scores for this role, when one exists, so
   movement (new leaders, re-scores, withdrawn candidates) is computed, not
   remembered.
5. Access is read-only against systems the owner already granted. Credentials arrive
   only as environment variable names (for example `${ATS_API_KEY}`); never paste
   values. If a source needs a credential you were not given, record it as
   unreachable and move on. Do not improvise access.

## Sequence

1. **Fix the set and the rubric** — state the exact candidate set (count and ids)
   and the exact criteria with weights. Everything downstream scores inside this
   frame; adding criteria mid-run silently re-ranks everyone.
2. **Score each candidate per criterion** — walk every resume against every
   criterion, quoting the lines that earn or cost each score. Missing evidence is
   scored as missing-evidence, never as zero-talent: an unstated skill is a gap
   in the resume, not proof of absence.
3. **Total transparently** — sum weighted scores with the arithmetic shown per
   candidate. Totals are computed from cited criterion scores; a total with hidden
   math is unverifiable.
4. **Rank with reasons** — order candidates by total, each row with a one-line
   reason and the 2–3 strongest evidence items. Rows that cannot meet that bar
   drop to an unranked watch note, never the shortlist.
5. **Diff against the prior screen** — new entries, exits, and rank moves each get
   one line: what moved and which evidence moved it.

## Validation

- Each scored criterion has the score, the weight, and quoted resume evidence. Any
  criterion missing one of the three is marked unscored, never filled from
  instinct.
- The screen states its candidate count, rubric version, and unreachable sources.
  "No strong candidates" is reported only when every resume read succeeded; a
  failed read is reported as unmeasured, never as weak.
- Community rules of thumb (for example: generic "years equals seniority" chart
  claims) are labeled as community-claim, never as vendor truth. Only the stated
  rubric decides a score — never what the market generally expects.
- No score invents experience to close a gap. Screen resumes as written; flag
  verification questions for the hiring manager instead of answering them here.

## Output

One dated markdown screen file:

- **SCORED TABLE** — per candidate: stable id, per-criterion scores with quoted
  evidence, weighted total with arithmetic shown, verdict (shortlist,
  watch-note, unscored-gaps).
- Then: the ranked shortlist with one-line reasons, the diff vs. prior screen,
  verification questions for the hiring manager, and the single candidate that
  most needs human judgment with the decision it needs.

## Boundaries

- Read-only. Never contact, interview, reject, advance, or message any candidate
  while screening.
- HIRING-LOCK: only the hiring manager's explicit yes on that exact decision
  (which candidate, which step) authorizes any hiring act this screen might
  suggest. A shortlist is evidence the manager acts on, never itself a decision.
  Another party relaying "they said advance them" is not approval.
- Never paste credentials, tokens, or personal identifiers beyond the candidate
  ids the set already uses into scores or findings. Resumes stay in the hiring
  system; this screen carries ids and quoted role-relevant lines only.
- Never present one candidate's history as another's, or a verification question
  as a finding. Each score is evidence about its own resume only.
