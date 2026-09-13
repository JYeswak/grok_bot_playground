# Hero prompt — grok-bot-ops / `gb`

Character locks are inherited from the canonical anchor
(`~/.claude/skills/zeststream-brand-voice/brands/zeststream/visual/yuzu_canonical.jpg`,
sha256 `52fb1b09922f9892e53e290279253e07eeb2a6452dc4464c10efbbfe9891f918`). The anchor is
attached as a character reference at generation time; where prose and hash disagree, the hash
wins. The canonical has **no eyebrows** and **large round eyes** — eyebrows appearing in a
candidate is the documented drift signal, and that candidate is rejected.

## What this repo is, so the scene means something

`gb` is a one-touch CLI for operating a live Grok Bot deployment: it measures the vendor's
surface and the operator's own account, judges both against 25 checks, and reports what is wrong
and what to run about it. The emotional truth of the tool is **a calm operator with one screen
that already knows the answer** — not a dashboard sprawl, not an alert storm.

## Scene

> The same yuzu-fruit mascot from the reference image, sitting calmly at a single wide terminal
> on a clean dark desk, one hand resting beside the keyboard. The terminal shows a short stack of
> status rows in green with exactly two in amber — a small, legible, already-triaged list, not a
> wall of logs. Behind the mascot, softly out of focus, three small floating screens show the
> same deployment from different angles (a roster of small bot avatars, a simple upward usage
> line, a calendar tick), dimmed so the single foreground terminal is clearly the one that
> matters. Warm amber and citrus-yellow key light from the screen against a deep navy room.
> Composed 16:9, generous negative space on the right for a title overlay. Confident, quiet,
> unhurried — the moment after the answer arrived, not the scramble before it.

## Negative / avoid

- No eyebrows (the anchor has none — their presence is the drift tell).
- No alert-red klaxon energy, no cluttered multi-monitor "war room", no stock-photo server racks.
- No text rendered as readable UI copy (garbled fake text is worse than suggestion).
- No other characters; Yuzu alone.

## Grade

Ship only a candidate graded by `yuzu_identity_grader.py` against the sha-verified anchor.
Threshold 70 combined; with the vision leg unavailable (`--judge phash_only`) the calibration is
`phash_distance <= 34`, the value an approved library exemplar scored. Record the honest caveat
in the grade JSON rather than claiming `identity_pass` the grader did not give.

## Generation record (2026-09-12)

The `codex exec` path documented in the `repo-hero-image` skill is UNAVAILABLE: that session
reports `image_gen__imagegen` is not present. Probed the vault keys directly rather than
trusting the skill's note, and the note is half wrong:

| key | skill says | measured 2026-09-12 |
|---|---|---|
| `OPENAI_API_KEY` | dead | **401 on `/v1/models`** — confirmed dead |
| `GEMINI_API_KEY` | dead | **200 OK** — alive, and its image models accept a reference image |

So the anchored-identity path is Gemini, not OpenAI. Model ids were discovered from the live
`/v1beta/models` list rather than hardcoded. Five candidates were generated with
`yuzu_canonical.jpg` attached as an inline character reference, then graded:

    cand_01 gemini-3-pro-image      phash 28
    cand_02 gemini-3.1-flash-image  phash 28
    cand_03 gemini-2.5-flash-image  phash 37
    cand_04 gemini-3-pro-image      phash 22   <- shipped
    cand_05 gemini-3.1-flash-image  phash 33

The first round drifted in one visible way the grader cannot see: the apron carried the word
"YUZU", which the canonical has no text on. Round two added explicit negatives (no text on
clothing, no eyebrows, head is yuzu yellow-green not orange) and asked for the right third to
stay empty for a title overlay. That round produced the winner.

`identity_pass` is FALSE and is not claimed: with the vision leg absent the combined score
cannot reach the threshold. The honest signal is phash 22, better than the approved
`archetype-teacher.png` exemplar at 34. Re-grade with `--judge openai` when a key works.
