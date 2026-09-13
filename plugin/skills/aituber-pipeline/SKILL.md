---
name: aituber-pipeline
description: Plan avatar-video pipeline runs as ordered steps with asset lists. Use when asked to plan an AITuber-style video run, stage avatar assets, or order pipeline steps from script to publish. Plans only — nothing here renders, posts, or publishes anything.
---

# AITuber pipeline

Turn a video idea into an ordered pipeline plan where every step carries its asset
list. This skill exists because avatar-video work fails on missing assets mid-run: a
corpus measure finds 5 of 645 Bots building on AITuber with no dedicated plugin, so the
practical move is a plan over named assets — script, voice, avatar, background — not new
render machinery.

Use when asked to plan an avatar-video run, stage the assets a run needs, or order
pipeline steps from script to publish. When-to-use is deliberately narrow: idea plus
assets in, ordered step plan out, nothing rendered or posted.

## The rule that matters more than the plan

**No asset, no step.** Every pipeline step names the exact assets it consumes and
produces; a step whose inputs are not staged is unscheduled, not assumed. Community
pipeline templates and "viral format" advice are community-claim: never present them as
vendor or product truth. If an asset does not exist in a readable location, the step
that needs it waits — the plan says so explicitly.

## Inputs and access

1. **Run request** — the video idea (topic, length band, voice or avatar choice) and
   which assets already exist (script draft, voice clip, avatar model, background).
   At most two runs per plan. If the idea names no assets at all, stage the asset
   list first and ask — planning steps on zero assets manufactures a zero run.
2. **Readable assets only** — the files and accounts the owner already has. A source
   you cannot read is recorded as unreachable, never improvised around.

## What to do, in this order

Follow this sequence; each step feeds the next.

1. **Assets** — inventory what exists: name, location, format, readiness (ready vs.
   needs-work). Skip unrelated media-library noise on the first pass; it is a count
   of its own, not run assets to silently claim.
2. **Steps** — order the run: script, voice, avatar assembly, background and edit,
   review, publish-slot. Each step names its input assets, its output asset, and
   which earlier step it waits on.
3. **Validation** — before writing the plan, check every step against its assets:
   each input staged or explicitly marked missing, each output named, each ordering
   dependency satisfiable. Anything that fails validation becomes an UNSTAGED note or
   is cut — never promoted into the run.

## Output

A run plan with one section per step: step name, input assets, output asset, and what
unblocks it. End with the unstaged list (assets wanted but missing) and the single
step that most needs the owner's eyes. Two run shapes the skill was built for: a
script-to-voice run (script in, voiced audio out, timing check stated) and a full
assembly run (all assets staged, ordered to a publish-ready slot with review named).

## Boundaries

- Drafts and plans only. Never render, voice-synthesize, post, publish, or schedule
  any video; SEND-LOCK applies — only the owner's explicit yes on that exact video
  authorizes a publish, never a bulk "post them all". Any publish belongs to a
  separate step the owner approves video by video.
- Never paste credentials, tokens, or account identifiers into a plan or a finding;
  `${VAR}` names only.
- Never present community pipeline advice as vendor truth. If a capability is not in
  the tools the owner actually has, record it as unverified and move on.
