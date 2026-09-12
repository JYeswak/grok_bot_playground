---
name: llm-ocr
description: Run quality-assessed OCR passes over scans, screenshots, or photos of documents and return a scored transcript per document. Use when a document has no usable text layer, when a prior extraction looks garbled, or before structured field extraction needs a trustworthy transcript to work from.
---

# LLM-aided OCR

A scored transcript per document, with a quality gate that decides pass or escalate — or an
honest escalation when the page cannot be read.

## Division of labor with doc-extract

**doc-extract** answers "what values are in this document": it prefers the PDF text layer, falls
back to reading a rendered page, and returns structured fields with provenance. **This skill**
answers "what does this page actually say, and how confident are we": it runs one or more
vision OCR passes over image-only input and scores every document. Use this skill first to
produce a quality-scored transcript; hand that transcript to doc-extract for field, table, and
total logic. This skill never defines field schemas, never checks totals, and never invents a
value doc-extract would own.

## The rule that matters more than the transcript

**A low score is a finding, not a failure to hide.** A page that scores below the gate must be
reported as below-gate with its score and reason, never polished into fluent text that reads
better than the source. A fluent guess with the hesitation edited out spends the reader's trust
the same way a silent misread does.

## When to use

- A PDF, scan, screenshot, or photo with no usable text layer needs transcribing.
- A prior extraction came back garbled and needs a second, scored pass.
- Structured extraction (doc-extract or equivalent) needs a trustworthy transcript first.
- Do not use when a text layer already yields real text — say so and hand off to doc-extract.

## Inputs and access

1. **Document images** — one or more pages as files or rendered page images. Record the source
   of every page (file name, page number).
2. **Reading goal** — verbatim transcript, or transcript-plus-layout (tables, reading order).
   Default is verbatim; layout only on request.
3. Read-only. If a page needs re-rendering at higher resolution, request it — never modify,
   overwrite, or re-file the source document.

## Sequence

1. **Render** — render each page at a readable resolution (150 dpi minimum; 300 dpi for small
   print). Note skew, rotation, or heavy noise before reading; correct orientation first.
2. **First pass** — transcribe each page visually, top to bottom, preserving reading order.
   Mark every illegible span inline as `[illegible]` rather than guessing.
3. **Second pass** — re-read the flagged spans and low-confidence lines only. A span still
   unreadable after two passes stays `[illegible: reason]` (blur, glare, truncation, handwriting).
4. **Score** — assign each document a quality score from the validation rubric below and apply
   the gate: pass goes to output, below-gate goes to escalation with the score attached.

## Validation (quality gate per document)

Score each document 0–100 from three observable signals:

- **Legibility share** — fraction of words transcribed without an `[illegible]` flag.
- **Layout integrity** — reading order, line breaks, and table cell boundaries preserved.
- **Character confidence** — digits, names, and punctuation re-checked on the second pass.

Gate: **80 and above passes**; **60–79 passes with warnings** (list every flagged span);
**below 60 escalates** — return what was readable, the score, and exactly what would settle it
(higher-resolution scan, cleaner photo, missing page). Community OCR heuristics are
community-claim pattern knowledge, never vendor truth — never cite them as guarantees.

## Output

Per document: the transcript (or transcript-plus-layout), the 0–100 quality score with its
three sub-signals, the list of `[illegible]` spans with page references, and the gate decision
(pass, pass-with-warnings, escalate). End with which document most needs a better source.

## Boundaries

- Read-only plus transcripts. Never send, publish, file, retype-into-a-system, or spend
  anything — SEND-LOCK applies: only the owner's explicit yes on an exact message authorizes
  any send, and nothing in this skill sends.
- Never invent text for an illegible span, never infer a total by arithmetic, and never
  normalise an ambiguous header — emit the flag and move on.
- Never paste credentials, tokens, or account identifiers into a transcript or a finding.
- If a page needs a source you cannot read, record it as unreachable and move on. Do not
  improvise access.
