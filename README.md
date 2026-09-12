# OneNote → Obsidian

Migrates OneNote into Obsidian while keeping page/subpage nesting and manual
page order — the two things every export tool silently drops.

## Quick start

```bash
pip install -r requirements.txt
export ONENOTE_CLIENT_ID=<your Azure app id — see SKILL.md>

# look before you leap; this writes nothing
python3 scripts/migrate.py ~/work-notes --account work --list-notebooks

python3 scripts/migrate.py ~/work-notes --account work \
    --notebook "David@Microsoft" --newest-first
```

`--account work` for a company/school tenant, `personal` for outlook/hotmail.
A work notebook will not show up under `personal`.

## As a Claude Code skill

Copy the whole folder to `~/.claude/skills/onenote-to-obsidian/`, then ask
Claude to migrate your OneNote notes. `SKILL.md` carries the full context —
why the migration breaks, the data model, the sync traps.

## What's inside

- `scripts/` — the migration pipeline; every one dry-runs unless given `--apply`
- `plugin/` — Quire, the OneNote-style navigator (built `main.js` + TypeScript source)
- `snippets/` — a CSS snippet that replaces Obsidian's wide dead margins

Requires Python 3.10+ and, for rebuilding the plugin, Node 18+.
