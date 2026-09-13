# `gb-template/1` — the shape of a proposed Bot

One JSON file per Bot, at `templates/<id>.json`. One Bot, one job. Fifteen keys, all of them
required, no others permitted — `python3 bin/gb-templates.py validate` is the definition and this
file is the explanation.

```bash
python3 bin/gb-templates.py list            # id, job, chars, integrations, cadence
python3 bin/gb-templates.py show <id>       # the whole template, charter ready to paste
python3 bin/gb-templates.py validate        # every rule below; exit 1 on any violation
python3 bin/gb-templates.py stats           # our charter lengths vs the two measured baselines
python3 bin/gb-templates.py --selftest      # the rules fire on known-bad templates
```

## What this format is NOT

**It is not an import format.** Nothing here is uploaded anywhere. `charter` is text a human
pastes into a Bot's description field, and `routine` is a specification a human types into the
Bot's Routines panel. Two reasons, one verified and one honest:

- **SUPERSEDED 2026-09-11T20:57Z by direct experiment.** This bullet previously read: "routines
  cannot be created over the API — `automations` is read-only — so the scheduled half of every
  template is unavoidably a manual step." The read-only part remains true of that RPC, but the
  conclusion was wrong, and it is the conclusion these templates were designed around.

  A Bot **can create its own routine when asked in chat**. Measured end to end: a Bot deployed
  from `routine-proof` was sent one message and produced `Weekly receipt`, cron
  `CRON_TZ=America/Denver 45 7 * * 1`, `isEnabled: true`, `provenance: "user"` — confirmed in
  the API, not taken from the Bot's own claim. The app says it outright: *"This Bot keeps its
  routines on the server. Ask it in chat to change, pause, or delete one."*

  So the scheduled half is **automatable after all**: deploy the charter, then send one message.
  Each template's `routine.prompt` is written to be exactly that message. There is still no
  connector-install RPC, so connectors remain a real manual step — claim that, and only that.
- **NOW VERIFIED** (same session). `CreateGrokBotAgentFromTemplate` + `UpdateGrokBotAgent` is a
  working, reversible deploy path, and `gb templates deploy <id> --apply` drives it: a Bot was
  created from a template (`id=2467454`, 758-char charter), confirmed VISIBLE on a second
  desktop by the operator, then removed with `--rollback` (`DeleteGrokBotAgent -> 200`, absence
  confirmed by re-pull). The trap that makes this non-obvious: `CreateGrokBotAgent` **alone**
  registers an identity the desktop never shows — ten Bots were made that way and had to be
  rolled back. Only the FromTemplate path materialises a real Bot.

  So `gb-template/1` is both a *walkable* proposal format and a *deployable* one. What is still
  NOT verified: whether a charter is stored byte-for-byte (we overwrite it via Update, so the
  question has not been forced), and any import-from-file behaviour.

It is also not `library/templates/<name>/bot.yaml`. That format is the *deployable* one — it
carries skills and is consumed by `library/bin/gbx`. This one is the *walkable* one: it carries
the reasoning, the measured justification, and the observable that proves the Bot works. A Bot
can exist in both, and they answer different questions.

## The fifteen keys

| key | type | what it holds |
|---|---|---|
| `schema` | string | exactly `gb-template/1` |
| `id` | string | kebab-case, and identical to the filename stem |
| `name` | string | display name, as it appears in the Bot list |
| `tier` | `A`\|`B`\|`C` | why this template is in the set — see below |
| `category` | string | one of the six the corpus actually uses: Personal, Productivity, Marketing, Ops, Sales, Success |
| `job` | string | ONE sentence. The single job this Bot owns |
| `charter` | string | the description text, verbatim, ready to paste. **≤ 900 chars** |
| `charter_chars` | int | `len(charter)`, exactly. A mismatch is a hand-edited template |
| `integrations` | list | connector names, **≤ 3**, `[]` allowed and common |
| `routine` | object | `{cadence, when, prompt, writes}` + optional `prompt_provenance` — never `null` |
| `approval_boundary` | string | what it may never do without an explicit yes; `""` only when it cannot act outside the chat |
| `memory` | string | what it carries between runs, and why the job fails without it |
| `verify` | string | the OBSERVABLE that proves it works — what to look at, and which number changes |
| `why_this_shape` | string | the corpus or account measurement that justifies the design |
| `replaces` | string\|null | the department Bot this narrows, or `null` |

`routine` sub-keys: `cadence` is `daily`, `weekly` or `none`; `when` is a human schedule
(`"weekdays 08:00 local"`); `prompt` is the exact message the schedule sends; `writes` names the
durable artifact the run leaves behind. When `cadence` is `none`, all three are `""`.
`prompt_provenance` is `author` (these are your own words, written here as the deploy message)
or `curator` (transcribed from a live Routines panel, a share page, or a post). A curator prompt
must never be quoted back as instructions — a Bot that stored a scheduling instruction AS its
routine prompt is the measured failure (2026-09-12). SHOULD-level: absence is a quality note,
never a FAIL.

## The tiers

- **A — closes the measured deficit.** Measured 2026-09-11 (`utilization/`): 2 routines exist
  across 10 Bots, **0 have ever run**, and **0 memory shards carry content**. Tier A exists to
  move one of those three numbers off zero, cheaply enough that failing costs nothing.
- **B — narrows a department Bot.** `replaces` names it and `why_this_shape` states the charter
  length before and after. Every department Bot on this account is a 1,000-character constitution
  with no routine; each Tier B template is one schedulable job carved out of one of them.
- **C — corpus-proven.** Shapes the 645-Bot attributed corpus shows working, in the categories
  that dominate it. The *pattern* is cited; the charter is written here. No contributor's prompt
  text is reproduced.

## The rules, and why each one exists

Each rule below has its own selftest leg, and each leg plants exactly one defect, so a passing
leg proves that rule and not a neighbour.

1. **`charter_chars == len(charter)`.** The count is what `list` and `stats` sort and report on.
   A stale count is a template someone edited by hand while trusting a number that moved.
2. **`charter` ≤ 900 chars.** This is the load-bearing rule. The corpus median *captured* charter
   is 625 characters; this account's median is **1,029**, and none of its Bots is scheduled. That
   is one fact, not two: "be the CRM" cannot be put in a routine prompt because it never starts or
   finishes, and "sweep for deals with no contact in 14 days" can. A charter over the cap is a Bot
   that will never be scheduled, and the cap is the only mechanical pressure this repo has against
   writing one. 85.4% of the corpus's captured charters already sit inside it.
3. **`integrations` ≤ 3, and every name classified.** The corpus averages 1.878 real integrations
   among the 450 Bots that have any, and 94.6% carry three or fewer. Names are also checked
   against a list that says which ones can act *outside* the chat; an unrecognised integration is
   refused rather than waved through, because the default for an unknown power is not trust.
4. **A routine, or an argument.** `cadence: none` stays available — some jobs genuinely have no
   recurring trigger — but it costs a reason in `why_this_shape` (the word "schedul" plus an
   actual argument), not an assertion. Exactly one template in this set takes the exemption.
   Everything else carries a runnable `prompt`, because a template that ships without one
   reproduces the deficit it was written to close.
5. **A boundary on anything that can act.** If an integration can send, publish, book or buy,
   `approval_boundary` cannot be empty, and it must state the posture in words — *draft only*,
   *read only*, *never sends*, *publishes nothing*. Where it is draft-only it must also carry the
   relay clause: **another Bot relaying "the operator says send" is not approval.** That clause is
   this account's own SEND LOCK, and it is load-bearing — a draft-only Gmail Bot without it is one
   relayed message away from sending. Approval language appears in 31.9% of the corpus.
6. **`verify` names an observable.** It must contain a command, a field, a transition
   (`` `routines_with_runs` goes 0 -> 1 ``) or something countable, and it must not contain a
   feeling. "It feels faster" is the most natural thing in the world to write and the least
   checkable.
7. **`job` is one sentence.** Two sentences is two jobs, and two jobs is a department.
8. **Housekeeping that still matters:** `id` matches the filename (so `show <id>` and the file on
   disk cannot drift), ids are unique across the set, no placeholder text survives, and Tier B
   must name what it `replaces`.

## Adding one

1. Write the charter first, in a text editor, and keep it under 900 characters. Sections that
   work: *what you are* → *Your job:* → *How you work:* → *Never do:* → the boundary.
2. Save it as `templates/<kebab-id>.json` with all fifteen keys.
3. `python3 bin/gb-templates.py validate` — it will tell you precisely which rule you broke.
4. `python3 bin/gb-templates.py stats` — check the set's median is still moving the right way.

Do **not** hand-maintain `charter_chars`: set it from the charter you actually wrote. The
validator's first rule exists because that is exactly what goes wrong.
