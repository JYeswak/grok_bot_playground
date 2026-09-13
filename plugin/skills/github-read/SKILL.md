---
name: github-read
description: Read GitHub repos, issues, and pull requests through github-mcp. Use when asked what is open, what a PR says, or to fetch issue detail. Never comment, merge, push, or close.
---

# GitHub read

Read the tracker. This account has `github-mcp` connected 2026-09-12
with five tools (*client-binary-verified*):

- `github_get_repo`
- `github_list_issues`
- `github_get_issue`
- `github_list_pull_requests`
- `github_get_pull_request`

`user-github` (different server) was `error` / 0 tools the same day —
do not use that row. This is not `pr-verify` (that runs a repro on the
cloud computer). This is the MCP read.

## The rule that matters more than the list

**Five reads, zero writes.** No comment, merge, push, label, or close.
If the owner wants a comment, draft it in chat; sending is a different
act.

## When to use

- Asked what issues/PRs are open, or to quote an issue/PR body.
- Feeding `linear-aging` / `pr-desk` with GitHub data.
- Do **not** use to merge, review-submit, or push. Do not use the
  errored `user-github` server as if it were this one.

## Inputs and access

1. **Owner/repo** plus optional issue/PR number or filter.
2. **These five tools** on this Bot. Missing → `plugin-enable-verify`.
3. Public or already-authorized repos only. No token paste.

## Sequence

1. **Probe** `github_get_repo` or `github_list_issues` once.
2. **Fetch** the objects the question names. Quote titles, numbers,
   states, dates, URLs.
3. Draft any suggested comment in the output as DRAFT, never posted.

## Validation

- Only the five read tools were called.
- Every item has a URL and state.
- No merge/comment/push.

## Output

Dated list or the one object requested, with URLs. PATH `PLUGIN`.
Draft comment clearly labeled DRAFT if asked.

## Boundaries

- Never comment, review, merge, push, close, or label.
- SEND-LOCK: a draft comment is not posted. Owner's explicit yes on
  that exact text would be a different routine; this MCP cannot post
  it today anyway — do not browser-post to "help."
- Never paste `GITHUB_TOKEN`.
