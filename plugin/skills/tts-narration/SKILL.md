---
name: tts-narration
description: Turn scripts into narrated audio with a recorded voice profile. Use when asked to narrate a script, voice a brief or readout, or produce audio for two scripts with the same voice.
---

# TTS narration

Turn a script into narrated audio read in a recorded voice profile, with the chain from script
to audio to voice unbroken and inspectable. This skill exists because script-to-audio is a
recurring community pattern (a small cluster of Bots wraps speech synthesis with no shared
plugin) and because audio whose voice and text cannot be traced back is indistinguishable
from a stranger reading unknown words. The local text-to-speech capability this assumes is
mirror-backed (community-claim): a machine on the owner's side that renders text to audio
in a previously recorded voice — never a vendor endpoint, and no specific tool or API is
assumed beyond "given text plus a voice profile, it returns audio".

## The rule that matters more than the audio

**Audio without a chain is a rumor.** Every delivered file traces to the exact script text
it renders, the named voice profile, and the render date — and any line that was skipped,
reworded for pronunciation, or re-rendered is labeled as such, never silently patched.

## When to use

- Asked to narrate a script, voice a written brief, or produce listenable audio from text
  the owner provides.
- Asked to narrate two scripts (compare takes, or voice each separately) with each
  script's chain kept distinct.
- Asked what a narration actually renders versus what the script says on the page.
- Do not use for transcribing audio back to text, for cloning a voice never recorded
  with consent, or for publishing audio — this skill renders local audio files only.

## Inputs and access

- **Script(s)** — one script for a single narration, two scripts for two takes. Freeze
  the text first: version, word count, and any pronunciation notes (names, acronyms,
  units) stated up front.
- **Voice profile** — a previously recorded profile, named, with its recording date.
  Never record, clone, or invent a voice inside this skill; the profile must already
  exist and be owned or consented.
- **Access** — the local synthesis capability plus whatever the Bot can already read.
  If rendering needs a credential, a paid endpoint, or a profile you were not given,
  record it as unreachable and move on. Do not improvise access. Name any
  configuration only as `${VAR}`-style variable names, never values.

## Sequence — what to do, in this order

1. **Freeze the script** — final text, word count, pronunciation notes. With two
   scripts, keep the blocks and everything downstream separate.
2. **Name the voice** — profile name, recording date, and what it consents to. No
   profile, no render: that is a finding, not a gap to fill with a default voice.
3. **Render the audio** — script plus named profile through the local capability.
   Record method (which profile, which script version, render date) and duration.
4. **Validate the render** — check the validation section below before delivering a
   single file. A render that fails validation gets re-rendered or reported, never
   quietly shipped.
5. **Mark the gaps** — skipped lines, pronunciation substitutions, and re-rendered
   spans, each labeled. End with the single most consequential check (does the
   audio say what the script says) and what it would take to verify further.

## Validation

- Audio present for the claimed script; missing or truncated renders fail, never
  silently narrow, the delivery.
- Spot-check the render against the frozen text — no dropped paragraphs, no
  reworded sentences passed off as verbatim, no invented closings.
- Voice identity stated: profile name and recording date. A fallback voice never
  delivers with the confidence of the named one.
- Two-script renders keep chains separate: no span from script A delivered under
  script B, no shared claim about "both sound fine" without a check on each side.
- Anything skipped, substituted, or re-rendered is labeled with the reason, never
  smoothed over.

## Output

Per script: a source block (script version, word count, voice profile name and
recording date, render date), the audio file with duration, and a gaps list. With
two scripts, deliver two chains plus a short comparison that checks each side
separately. End with the single most consequential check and the decision or
follow-up it needs — not a wall of audio.

## Boundaries

- Render-only plus notes. Never record or clone a voice, publish audio, or spend
  on synthesis or enrichment while narrating.
- Approval boundary: this skill renders audio files for the owner to use.
  Publishing audio, attributing it publicly, or voicing words for someone who did
  not consent belongs to a routine with its own approval — nothing here sends or
  publishes.
- Never present community speech-synthesis lore (which engines sound best, which
  voices "always work") as vendor truth. State what rendered today, for this
  script, with this profile.
- Never paste credentials, tokens, or account identifiers into a script, note, or
  finding. If rendering needs access you lack, record it as unreachable and move
  on.
