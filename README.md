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

## What lands in a note

Three frontmatter keys, all hidden in the editor by the plugin's CSS:

```yaml
quire_src: "DO-NOT-EDIT|eyJ0IjoiOC8yNS8yNiIs…"   # packed OneNote provenance
nav_parent: "[[2026]]"                           # nesting, rename-safe
nav_order: 1000                                  # sparse rank
```

## Plugins

Quire is bundled and required; `make_vault.py` installs it. Excalidraw is
optional — if present, Quire adds a *New drawing here* command that creates the
drawing in your current folder. Everything works on iPhone and iPad.

To copy a tuned setup onto another vault:

```bash
python3 scripts/clone_vault_config.py <source-vault> <target-vault> --apply
```

Restricted Mode is per vault: a new vault ignores plugin files until
Settings → Community plugins → *Turn on community plugins*.
