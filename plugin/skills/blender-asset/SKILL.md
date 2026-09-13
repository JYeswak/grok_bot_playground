---
name: blender-asset
description: Brief Blender 3D asset tasks into render-or-convert plans with file provenance. Use when asked to render a scene, convert a model, or plan a Blender task with named inputs and outputs. Briefs only — never runs Blender or writes files itself.
---

# Blender asset

Turn a 3D task into a briefed render-or-convert plan where every file carries its
provenance. This skill exists because 3D work fails on unnamed inputs: a corpus measure
finds 5 of 645 Bots building on Blender with no dedicated plugin, so the practical move
is a brief over named files — source path, format, version — not new render machinery.

Use when asked to render a scene, convert a model between formats, or plan a Blender
task with explicit inputs and outputs. When-to-use is deliberately narrow: named files
in, briefed plan out, nothing executed.

## The rule that matters more than the brief

**Unnamed file, unplanned task.** Every input names its path, format, and where it came
from; every output names its path, format, and what produced it. A file whose provenance
you cannot state is a lead, not an input — label it as such or drop it. Community
render settings and converter lore are community-claim: never present forum advice as
vendor truth; if the installed Blender build does not support it, the brief does not
use it.

## Inputs and access

1. **Asset task** — render or convert, the source file (path, format, origin), and the
   wanted output (path, format, quality bar). At most two tasks per brief. If any of
   these is missing, ask for it before briefing; briefing on a vague task manufactures
   a vague render.
2. **Readable files only** — the scene or model files the owner already has, plus the
   installed Blender build's actual capabilities. A file you cannot read is recorded as
   unreachable, never improvised around.

## What to do, in this order

Follow this sequence; each step feeds the next.

1. **Provenance** — record every input file: path, format, origin, last-modified if
   known. Skip temp and cache noise on the first pass; it is a count of its own, not
   inputs to silently brief.
2. **Brief** — per task: operation (render vs. convert), exact source-to-output
   mapping, settings with their source (build-supported vs. community-claim), and the
   verification check (what the output must look like to count as done).
3. **Validation** — before writing the brief, check every task against its inputs:
   each source file readable with stated provenance, each output path writable and
   named, each setting supported by the installed build or labeled community-claim.
   Anything that fails validation becomes an UNBRIEFED note or is cut — never
   promoted into the brief.

## Output

A brief with one section per task: inputs with provenance, operation and settings,
output paths, and the verification check. End with the unbriefed list (tasks wanted
but not plannable) and the single task that most needs the owner's eyes. Two task
shapes the skill was built for: a render brief (scene in, image or frames out, sample
count and resolution stated) and a convert brief (model in one format, same model out
another, fidelity check stated).

## Boundaries

- Approval boundary: read-only plus briefs. This skill never runs Blender, converts a
  file, writes or overwrites anything, and never installs builds or add-ons. There is
  nothing here that sends — no draft-send step exists, so no send can be authorized
  from this skill. Any execution belongs to a separate routine with its own approval.
- Never paste credentials, tokens, or account identifiers into a brief or a finding.
- Never report file contents beyond what the reader can already open. If a file needs
  access you cannot get, record it as unreachable and move on.
