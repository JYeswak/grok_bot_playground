---
name: phantom-docs
description: Search Phantom developer docs through the enabled phantom-connect plugin. Use when asked how Phantom Connect or the Phantom SDK works. Never sign, send, or submit wallet transactions.
---

# Phantom docs

Answer a Phantom **developer-docs** question from the plugin this account
enabled 2026-09-12 (`phantom-connect`, publisher phantom,
`PLUGIN_STATUS_APPROVED` — *client-binary-verified*). The only tools
`tool_census` returned for `user-Phantom-connect-sdk` the same day
(*client-binary-verified*):

- `query_docs_filesystem_phantom_developer`
- `search_phantom_developer`
- `submit_feedback`

That is a **docs index**, not a wallet. `Phantom-mcp` appears in
`effective_server_names` with **no census row** — treat those tools as
unknown until listed. Do not invent `signTransaction`.

## The rule that matters more than Connect

**Docs in, docs out. No chain write.** A question about balances, seed
phrases, or "just sign this" is a wall, not a docs lookup.

## When to use

- Asked how Phantom Connect / Phantom developer APIs work.
- Asked to look up a Phantom SDK method, error, or integration note.
- Do **not** use to connect a wallet, sign, send SOL/tokens, or paste a
  seed. Do not use `submit_feedback` unless the owner asked to send
  feedback to Phantom.

## Inputs and access

1. **The docs question** — one sentence.
2. **Tools on this Bot** — the three above, if present. If missing:
   `plugin-enable-verify`.
3. No wallet secret, no seed, no private key, ever.

## Sequence

1. **Enumerate** Phantom tools. Quote them.
2. **Search** with `search_phantom_developer` or
   `query_docs_filesystem_phantom_developer`. Quote the passage + source
   path. Label community/vendor as Phantom's own docs, not xAI's.
3. If a `Phantom-mcp` wallet-shaped tool appears: **list it, do not call
   anything that signs, sends, or approves.** Ask the owner.
4. Never call `submit_feedback` unless the owner named that exact
   feedback text.

## Validation

- Every claim cites a docs passage from the tool result.
- No signing, sending, or seed material in the output.
- `submit_feedback` either was not called or the owner authored the text.

## Output

The answer with quoted docs passages, tool names used, and `NOT DONE` if
the real ask was a wallet action.

## Boundaries

- Never sign, send, approve, or broadcast a transaction.
- Never paste seed phrases, private keys, or wallet passwords.
- SEND-LOCK: docs search authorizes no tweet, email, or support ticket.
- Never treat `phantom-connect` APPROVED as "this Bot can move funds."
