# Page to Grok Bot is a feature, not a rewrite

This extension adds one new entry point to the existing playground philosophy: take a page the user is already reading and turn it into paste-ready bot text. It does not replace `gb`, the templates shelf, or the existing deployment boundary.

## Why this is a feature

- It emits a `gb-template/1` object in the same house style as the checked-in templates.
- It helps a human start from the current page without changing any existing `gb` CLI surfaces.
- It keeps the same safety boundary: no credentials, no browser takeover of Grok, no invented connector RPCs.

## Boundary table

| Step | Who does it |
| --- | --- |
| Extension fingerprints the current page and emits a charter | Extension |
| Human pastes the charter into Grok Bot | Human |
| `gb templates deploy` / `gb x` creates a Bot when a supported live session exists | `gb` tooling |
| Connector installation or enablement | Human in product settings |
| Routine creation | Human asks the Bot in chat to schedule itself |

## Non-goals

- No connector RPC layer
- No cookie use
- No login automation
- No promise that a downloaded template means a Bot or routine now exists

The extension is intentionally the text compiler at the edge of the workflow, not a rewrite of the workflow itself.
