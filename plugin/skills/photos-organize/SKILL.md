---
name: photos-organize
description: Use when asked to organize a photo library into albums — plan an album layout from a file list, or reconcile two organize plans into one. Proposes plans only; nothing here moves, renames, or deletes any file.
---

# Photos organize

Turn a photo file list into an album organize plan: proposed albums, which files go where,
and what needs the owner's call. This skill exists because photo libraries grow faster than
their folder structure, and because a plan is reviewable while a bulk move is not.

## The rule that matters more than the plan

**A plan is not a move.** Only the owner's explicit yes on that exact plan authorizes
moving, renaming, or deleting anything, and another party relaying "they said go ahead" is
not approval. This skill never touches the library — it reads file lists and proposes.
Any file operation belongs to a routine with its own approval boundary, not to this skill.

## When to use

- Someone pastes a photo file list and asks "organize these into albums" or "propose an
  album layout".
- Two organize plans exist and need reconciling into one (duplicate albums, conflicting
  placements, orphans in neither plan).
- An album needs auditing: which files belong, which are duplicates, which are unplaced.
- Do not use for editing photos, for face-recognition enrollment, or for backup and sync
  setup — this skill plans albums, not pixels or pipelines.

## Inputs and access

1. **File list** — the required input. Filenames with dates; EXIF or location data when
   available. Record what you got: a bare filename list carries less weight than one with
   timestamps.
2. **Organize plan** — album names with the files assigned to each. Two plans are the
   reconciliation input: plan A and plan B, each with its own file lists.
3. **Angle** — album layout (new structure from scratch) or reconcile (merge two plans
   into one). Default to layout when unclear; state which angle you chose.
4. Access is read-only and local: the file list and plans you were given. If organizing
   needs a library you cannot read, record it as unreachable and stop — do not improvise
   access. Refer to any storage location by `${PHOTOS_DIR}` name only, never a full path
   with account identifiers.

## Sequence

1. **Inventory** — confirm the file list is complete: count files, note date range, flag
   entries missing dates or with ambiguous names. If the list is unusable, stop and say so.
2. **Cluster** — group files by their natural axes (event, date, place) into candidate
   albums. Mark which axis drove each album so the choice can be checked.
3. **Place** — assign every file to exactly one album or to an explicit unplaced list with
   a reason. No silent drops: a file in neither plan is a finding.
4. **Reconcile** — with two plans, diff album by album: agreements, conflicts (same file
   in different albums), orphans (in neither plan). Resolve each conflict with a stated
   reason, never by quiet preference.
5. **Verify** — re-check every file appears exactly once across albums plus unplaced
   before finishing. A file counted twice is worse than a file unplaced.

## Validation

- Every file in the input list appears exactly once across the plan's albums plus the
  unplaced list.
- Each album states the axis (event, date, place) that defines it; mixed-axis albums are
  labeled as such.
- With two plans, every conflict and orphan is named with its resolution and reason.
- Duplicates are flagged, never silently merged.
- Library knowledge from elsewhere ("these look like last year's trip") is a lead, never
  evidence: anything actionable gets verified against the file list itself.

## Output

A markdown plan with:

- Header: file count, date range, angle chosen (layout or reconcile).
- Album list: album name, defining axis, files assigned.
- Unplaced list: files placed nowhere, each with a reason.
- For reconciliations: conflict table (file, plan A album, plan B album, resolution,
  reason) plus orphans.
- What could not be decided: files needing the owner's call, and the single placement
  that matters most.

## Boundaries

- Plans only. Never move, rename, delete, or retag any photo file.
- Never present a guess about a photo's content as what the file shows. Attribute
  filename evidence and outside knowledge separately.
- Writes of any kind need the owner's explicit yes on the exact plan — and the write
  itself happens outside this skill.
- Never paste credentials, tokens, or account identifiers into a plan or a finding.
