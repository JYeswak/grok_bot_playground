## What this changes

-

## How a newcomer notices

- [ ] README / QUICKSTART / GALAXY front door
- [ ] A pasteable template
- [ ] A persona pack
- [ ] The CLI itself
- [ ] Docs only

## Checks

- [ ] Did not edit between `<!-- gb:derived:begin -->` and `<!-- gb:derived:end -->`
- [ ] New templates pass `python3 bin/gb-templates.py validate`
- [ ] No new digit+noun counts (`N verbs`, `N templates`) in root `*.md` unless they match live `gb capabilities`
- [ ] `gb walk bots --paste <id>` still emits the charter and nothing else
