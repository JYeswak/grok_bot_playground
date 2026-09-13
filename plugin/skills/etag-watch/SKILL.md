---
name: etag-watch
description: Fetch vendor pages conditionally with If-None-Match/If-Modified-Since so an unchanged page costs no body bytes. Use when a watch routine re-fetches the same URLs every run, or when asked to diff pages cheaply over time. Read-only fetching; it never posts, submits, or authenticates to a site.
---

# ETag watch

Re-check a set of vendor URLs using conditional requests, so a page that has not changed
returns `304 Not Modified` and costs almost nothing. This skill exists because watch routines
in this account re-download every page in full on every run: the same megabytes, every day,
to discover that nothing moved. The validators the servers already send make that unnecessary.

## The rule that matters more than the diff

**A `304` means unchanged; a failed fetch means unmeasured; those are different answers and
must never be merged.** A timeout, a `403`, a DNS failure, or a page that stopped sending
validators is a finding with its status code, not a quiet "no change". And a validator is only
trustworthy paired with its URL: an ETag stored against the wrong URL silently reports every
future run as unchanged.

## When to use

- A daily or weekly watch routine re-fetches the same vendor, docs, pricing, or changelog
  pages.
- Asked to monitor pages for change over time on a budget, or to explain why a watch job is
  expensive.
- Migrating an existing full-refetch watcher onto conditional requests.
- Do not use for authenticated pages, form submission, scraping behind logins, or any request
  that changes state on the far side. Conditional GET only.

## Inputs and access

1. **URL set** — the required input: the pages to watch, each with a stable id. A URL that
   redirects is recorded with its final location; validators are stored against the final
   URL.
2. **State file** — the per-URL record carried between runs on the Bot's cloud computer:
   `{id, url, etag, last_modified, content_hash, last_status, last_checked, consecutive_errors}`.
   No state file means the first run is a full fetch that establishes one — and says so.
3. **Change rule** — what counts as a change worth reporting: any body difference, or a
   normalized-text difference that ignores timestamps, nonces, and rotating banners. State
   which rule you used.
4. The cloud computer is shared by every Bot on this account, so the state file is visible to
   all of them. Namespace it by watch job and never store credentials in it. No login, no
   cookies, no authenticated fetches.

## Sequence

1. **Load state** — read the state file; report how many URLs have validators and how many
   do not.
2. **Request conditionally** — for each URL send `If-None-Match: <etag>` when an ETag is
   stored, and `If-Modified-Since: <last_modified>` when only a date is stored. Send both
   when both exist.
3. **Handle by status**:
   - `304` — unchanged. Record `last_checked`; no body was transferred. Cost recorded as
     headers only.
   - `200` — changed or no validator honoured. Hash the body under the change rule and
     compare to `content_hash`; a matching hash means the server simply ignores validators,
     and that fact is recorded for the URL.
   - `301/302` — follow once, record the new final URL, and reset validators for it.
   - `4xx/5xx`, timeout, DNS failure — record status and increment `consecutive_errors`.
     Never fall back to declaring the page unchanged.
4. **Diff the changed** — for `200` with a different hash, produce the diff under the change
   rule: what section moved, in a few lines, with the URL.
5. **Write state** — store new `etag`, `last_modified`, `content_hash`, status, and time, per
   URL, atomically. A run that fetched but did not store its validators has saved nothing.
6. **Report cost** — how many `304`s, how many bodies transferred, and the byte total, so the
   saving is observable rather than claimed.

## Validation

- Every URL appears in the run with a status: `304`, `200-unchanged`, `200-changed`, or an
  error with its code.
- Validators are stored against the exact final URL that produced them.
- A URL whose server never returns `304` after two runs is flagged as
  `validators-not-honoured` so it is not silently paid for forever.
- `consecutive_errors` at or above the stated limit escalates as a finding, not a retry loop.
- The report never claims a saving it did not measure: bytes transferred are counted.

## Output

A dated markdown watch report:

- Header: URLs checked, `304` count, changed count, error count, bytes transferred this run.
- **CHANGED** — id, URL, what moved, diff excerpt.
- **ERRORS** — id, URL, status or failure, consecutive error count.
- **NOT HONOURING VALIDATORS** — URLs paying full cost and why.
- The single change most worth a human's attention.

## Boundaries

- GET and HEAD only. Never POST, submit a form, authenticate, or accept cookies to reach
  content.
- Never treat an error, a timeout, or a block as "unchanged".
- Respect the site's robots directives and rate limits; a watch that gets the account blocked
  has cost more than it saved.
- Never store credentials, tokens, or cookies in the state file.
- Any action suggested by a detected change is a proposal for a human; this skill only
  reports.
