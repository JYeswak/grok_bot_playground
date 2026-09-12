# CLAUDE.md

Read [AGENTS.md](AGENTS.md). It is the whole contract and this file is deliberately not a second
copy of it: two files stating the same rules drift, and the one you happen to open wins.

The three things worth knowing before you open it:

- `gb` measures and proposes. It cannot create a Bot, sign in, or send as the user.
- Route from `gb capabilities --json`. Never hardcode a verb list, never read `bin/`.
- Exit `3` is the tool naming a missing prerequisite, not a crash. Read the sentence.
