---
name: doc-extract
description: Use when given a PDF, scan, screenshot or photo of a document and asked for the numbers, tables, dates, parties or line items in it. Use before anyone retypes a document by hand.
---

# Document extraction

Structured output with provenance, or an honest blank.

## Output

One record per requested field: value or `null`, page, label, path (text-layer or OCR). Tables keep row order and original headers.


## What to read, in this order


1. **Text layer** — `pdftotext -layout` or `pypdf`. If it returns real text, use it and say so.
2. **Rendered page** — only when the text layer is missing or garbled. Render the page and read
   it visually. Mark every value from this path as OCR-derived.

The two paths do not deserve equal confidence and the reader must be able to tell them apart.

## Every value carries where it came from

    total_due: 417.92   (p2, "Balance Due", text-layer)
    invoice_date: null  (p1 date field illegible — OCR, needs a human)

A value without a page reference cannot be checked, and an unchecked extraction is a rumour.

## Never invent

If a field is unreadable, emit `null` with the page and the reason. Do not infer a total by
adding the line items unless asked — and if you do, label it `computed`, not `extracted`.

## Tables

Preserve row order and column headers as they appear. If a column header is ambiguous, keep the
original text rather than normalising it to what you think it means; note the ambiguity beside
the table.

## Validation


Totals that should add up: add them. Dates that should be ordered: check the order. Report any
internal inconsistency you find as a finding, not a correction — the document says what it says.

## Boundaries

- Read-only over the source. Never edit, re-file, forward, or act on the document — hand over the extraction and stop.
- An extraction is not an approval: a value that would move money, access, or identity ships flagged as extracted, never as authorized.
- If neither path yields a field, leave the null and stop. Do not reach for another source to fill the gap — a filled gap is an invention with a citation.
