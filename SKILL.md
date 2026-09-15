---
name: onenote-to-obsidian
description: Migrate OneNote notebooks into an Obsidian vault, preserving page/subpage nesting and manual page order that a plain export loses. Use when someone wants to move off OneNote, has already imported OneNote notes that came out flat or alphabetical, needs a OneNote-style navigator in Obsidian, or asks to set up a new Obsidian vault with coloured folders and manual note ordering. Covers installing Obsidian, Microsoft sign-in, export, cleanup, the Quire navigator plugin, and syncing to iPhone/iPad.
---

# OneNote → Obsidian

Moves a OneNote account into Obsidian and keeps the structure that makes OneNote
usable: pages with subpages, and pages in the order you dragged them into.

## The thing that goes wrong

Every OneNote export tool loses two fields, and losing them is why imported
vaults feel broken.

OneNote does not store a tree. A section is **a flat list of pages plus an
indent integer**: each page carries an `order` and a `level`. Both are exposed by
Microsoft Graph — but only if you pass an undocumented query parameter:

```
GET /me/onenote/sections/{id}/pages
    ?$select=id,title,level,order&$orderby=order&pagelevel=true
                                                 ^^^^^^^^^^^^^^
```

**Without `pagelevel=true`, Graph returns `level` and `order` as `null`** with no
error. Tools that omit it write files whose names then re-sort alphabetically,
and every subpage becomes a sibling. Obsidian's own official importer fetches
both and then discards `order` too.

Symptom to recognise: a folder of dated notes sorted `1-18-23`, `10-12-23`,
`10-5-25`, `11-13`, interleaved with empty year stubs that were parent pages.

`scripts/onenote_export.py` passes it and writes the hierarchy on the way out.
For a vault imported by something else, `scripts/recover_hierarchy.py` rebuilds
it afterwards by matching on the OneNote id left in each note's frontmatter.

## Platforms

macOS, Windows, Linux, and WSL. **If you are on WSL, read that section below
before running anything** — the vault location matters and the failure mode is
silent.

## Run it

```bash
pip install -r requirements.txt
export ONENOTE_CLIENT_ID=<see "Client id" below>

# Always look first — this touches nothing.
python3 scripts/migrate.py ~/work-notes --account work --list-notebooks

python3 scripts/migrate.py ~/work-notes \
    --account work --notebook "David@Microsoft" --newest-first
```

`--account` picks the Microsoft endpoint, and getting it wrong is the most
common failure: `personal` for outlook/hotmail/live, `work` for a school or
company tenant, `either` when unsure. A work notebook will not appear at all
under `personal`.

Stages run in order and are individually skippable with `--skip`:
`vault` → `export` → `chrono` → `tighten`.

## Windows and WSL

Obsidian runs on **Windows**; the scripts run inside **WSL**. That split is the
source of every Windows-specific failure here, and each one fails quietly.

**Put the vault on the Windows drive.** A vault inside the WSL filesystem is
reached by Obsidian over the `\\wsl$` network share, where file watching is
unreliable and a large vault crawls. WSL reaches the Windows drive at `/mnt/c`
at full speed, so that is the side to use:

```bash
# good — Windows-native, fast for Obsidian, reachable from WSL
python3 scripts/migrate.py /mnt/c/Users/<you>/work-notes --account work

# refused unless you pass --force
python3 scripts/migrate.py ~/work-notes --account work
```

**Install Obsidian on the Windows side, not in the distro:**

```powershell
winget install Obsidian.Obsidian
```

`scripts/platform_paths.py` handles the rest and is worth reading before
debugging anything platform-shaped:

| Concern | On WSL |
|---|---|
| Obsidian's config | `%APPDATA%\obsidian`, found via `cmd.exe /c echo %APPDATA%` then `wslpath -u` |
| Registering a vault | `obsidian.json` stores **Windows** paths — a Linux path registers fine and then silently never opens. `wslpath -w` converts. |
| Opening a file or URL | `explorer.exe` / `cmd.exe /c start`, never `xdg-open` |
| Is Obsidian running | `tasklist.exe`, not `pgrep` |

Two smaller traps: `cmd.exe` warns loudly when its working directory is a UNC
path, so those calls set `cwd="/mnt/c"`; and WSL detection reads
`WSL_DISTRO_NAME` or `/proc/version`, since `sys.platform` is just `linux`.

Device-code sign-in works unchanged — it prints a URL and a code for you to open
in the Windows browser, so nothing needs to launch from inside the distro.

## Client id

Microsoft publishes no shared client id, so each account needs a free app
registration — about three minutes, once:

1. <https://entra.microsoft.com> → **App registrations** → **New registration**
2. Supported account types: **include both personal and organizational**
3. **Authentication → Allow public client flows → Yes**
   (skipping this is the usual cause of `AADSTS7000218`)
4. **API permissions → Microsoft Graph → Delegated → `Notes.Read`**
5. Copy the **Application (client) ID**

Sign-in is device-code flow: a browser opens, no password passes through these
scripts, and the read-scoped token is cached in the vault's `.tools/`. Treat
that cache as a secret; delete it to sign out.

Some corporate tenants require admin consent for Graph. `Notes.Read` on your own
notebooks usually does not, but if sign-in returns a consent error, that is what
happened — it needs an IT admin, not a code change.

## What each script does

| Script | Purpose |
|---|---|
| `make_vault.py` | Create a vault, install Quire, enable the snippet, set new-files-in-current-folder |
| `onenote_export.py` | Export notebooks, writing `nav_order` / `nav_parent` and packing provenance |
| `recover_hierarchy.py` | Repair a vault imported by *another* tool, matching on OneNote id |
| `chrono_order.py` | Re-sort date-named pages newest-first |
| `tighten_lists.py` | Remove the blank lines that make every imported checklist double-spaced |
| `compact_frontmatter.py` | Fold six legacy provenance keys into one `quire_src` line |
| `slim_provenance.py` | Trim that blob to title + URL + id |

Every script defaults to a **dry run** and needs `--apply` to write. Keep it that
way; these edit hundreds of files at once. Back up before `--apply`:

```bash
rsync -a --include='*/' --include='*.md' --exclude='*' vault/ /tmp/backup/
```

## Plugins

**Quire is the only one this skill requires, and it ships in `plugin/`.** It is
not in the community store, so it is installed by copying three files into
`<vault>/.obsidian/plugins/quire/` — `make_vault.py` does that automatically.

| Plugin | Needed? | Why |
|---|---|---|
| **Quire** (bundled) | **Required** | The navigator itself: coloured folders, notes nesting to any depth, drag ordering, the CSS that hides the frontmatter |
| **Excalidraw** | Optional | Quire detects it and adds *New drawing here*, which creates the drawing in your current folder instead of Excalidraw's configured one. Absent, the button and command simply do not appear. |
| Omnisearch | Optional | Full-text search. Worth having — OneNote users expect search to find body text. |
| Recent Files | Optional | A recents list in the sidebar |
| Editing Toolbar | Optional | Formatting toolbar, closer to OneNote's ribbon |
| Minimal Theme Settings | Optional | Only useful with the Minimal theme installed |

Everything above is `isDesktopOnly: false`, so it all works on iPhone and iPad.

### Copying a setup between vaults

To make a second vault match one you have already tuned:

```bash
python3 scripts/clone_vault_config.py ~/source/notes ~/Documents/notes/work --apply
```

It copies plugin code and portable settings, and deliberately does **not** copy
three things that would drag personal state across: Recent Files' remembered
paths, Terminal's absolute paths to launcher scripts inside the source vault
(those are rewritten, and referenced `.tools/*.sh` carried over), and Quire's own
settings, which key collapsed folders and colours to folders the target vault
does not have. Workspace layout, bookmarks and graph state are skipped for the
same reason. Run it without `--apply` first to see the plan.

### After any install

Restricted Mode is **per vault**. A newly created vault ignores plugin files
entirely until Settings → Community plugins → *Turn on community plugins*. Files
present but plugins missing from the UI is almost always this.

## Data model

**Exactly three keys per note, and all three are invisible in the editor.**

```yaml
---
quire_src: "DO-NOT-EDIT|eyJ0IjoiOC8yNS8yNiIsInMiOiJodHRwczovL29uZWRy…"
nav_parent: "[[2026]]"     # wikilink on purpose — see below
nav_order: 1000            # sparse rank; one drag rewrites one file
---
```

That is a real note from a migrated vault, not an illustration. Writing a
**fourth** key is what makes Obsidian render its Properties table, which is the
one thing that makes this look heavy — so do not add one without also adding it
to the CSS below.

### Why you do not see any of it

`plugin/styles.css` hides these from the Properties panel entirely, and collapses
the panel when a note has nothing else in it:

```css
.metadata-property[data-property-key^="quire_"],
.metadata-property[data-property-key^="nav_"]     { display: none !important; }
```

Matching by **prefix** is deliberate: an earlier version listed the three keys
explicitly, the exporter later gained a `quire_section` key, and that one key --
unlisted, therefore unhidden -- brought the whole Properties table back on a
freshly migrated vault. Prefix matching means a new `nav_*` or `quire_*` key is
covered the moment it exists.

In source mode the frontmatter is not hidden but shrunk -- 8px at 28% opacity,
brightening on hover -- so it stays visible enough to avoid deleting by accident.
The `DO-NOT-EDIT|` prefix inside the value says so out loud.

### What is inside quire_src

Base64 of a compact JSON object, three fields, all worth keeping and none worth
reading:

```json
{"t": "8/25/26", "s": "https://onedrive.live.com/…", "i": "0-ae678ac2…"}
```

`t` is the original OneNote title, which matters because filenames were mangled
at import -- a page called `9/3/26` becomes `9-3-26.md`, and one called
`Things That Seem to Make Me Smarter/More productive` loses its slash entirely.
`i` is the page id, the key that makes a re-sync exact rather than fuzzy. `s` is
the link back to the live page. Decode one with:

```bash
python3 scripts/compact_frontmatter.py --decode path/to/note.md
```

Three decisions worth not undoing:

**Order lives in frontmatter, not a shared settings file.** Bartender and
Flexplorer keep `folderPath → [names]` in `data.json`. Obsidian Sync merges JSON
by overlaying top-level keys and never emits a conflict file for `data.json`, so
two devices reordering the same folder silently lose one side's work — reported
in the wild, reproduced with only one device running at a time. Per-note
frontmatter makes a reorder touch one file, so both sides merge.

**`nav_parent` is a wikilink so Obsidian's rename refactor maintains it.**
Rename a parent and children follow. Every path-keyed plugin in this category
silently orphans notes on rename; OneNote page titles *are* filenames, and people
rename constantly.

**Sparse ranks in steps of 1000.** Inserting between two notes writes the
midpoint — one file, not the whole folder. Compaction only happens when a gap
runs out.

**Ordering comes from OneNote, and is only overridden where dates clearly rule.**
The exporter writes `nav_order` straight from OneNote's own `order` field, so a
section you arranged by hand arrives arranged. `chrono_order.py` then re-sorts
*only* groups that dates plainly govern — a group hanging under a year parent, or
one where at least half the members are dated. An interview pipeline or a list of
companies keeps the order OneNote gave it. `--all-groups` forces the old
everything-everywhere behaviour, and is almost never what you want.

**Folders** are real directories at any depth. Folder order lives in the folder
note's frontmatter, created lazily on first drag, so unordered folders never
grow stray files. **Notes** nest to any depth via `nav_parent` — the pointer is
just a chain, so three levels costs the same as one. Cycles are detected on load.

## The Quire plugin

`plugin/` holds source and a built `main.js`. Rebuild with `npm install && npm
run build` (esbuild + TypeScript; edit the vault path in `esbuild.config.mjs`).

Colour uses **five base hues on rotation**, not one per folder. Repetition is
safe because **depth is carried by lightness**: top level is a dark saturated
badge with white text, subfolders are pale chips fanned across ±60°. Level three
inherits its level-two ancestor. Computed in OKLCH so hues look equally bright —
in HSL yellow glares and blue sinks. Folders starting with `_` are grey, unranked
and sorted last.

Other behaviour worth knowing: a file with **no** `nav_order` sorts to the *top*,
newest first, because an unranked file is new rather than last — that is how a
drawing created by another plugin stays findable. Opening any file reveals it,
expanding collapsed ancestors. Excalidraw drawings can be created in the current
folder via Quire, overriding Excalidraw's fixed folder setting.

## Mobile

`isDesktopOnly: false`. Drag works on iPad **with an Apple Pencil, mouse or
trackpad** — WebKit fires drag events for those but not for a bare finger, so on
iPhone reordering is the *Move up / Move down / Indent / Outdent* commands. Bind
them; they are the OneNote `Ctrl+Alt+[` / `]` gestures.

To reach a phone, in **Settings → Sync** turn on **Installed community plugins**
*and* **Active community plugins** — both ship off, and **sync settings do not
themselves sync**, so set them on every device. Then force-quit Obsidian on iOS;
community plugins do not hot-reload there. There is no ribbon on iPhone — the
icon moves into the ☰ menu.

Never run two sync services on one vault.

## Gotchas

- Frontmatter is markdown-only. `.canvas` files and attachments cannot carry
  `nav_order`; they sort by recency instead.
- Duplicate page names are legal in OneNote and impossible in one Obsidian
  folder. The exporter appends ` (2)`; the real title stays in `quire_src`.
- Graph 504s and throttles on OneNote reads. The scripts retry with backoff and
  isolate per-section failures — one bad section must not abandon the rest.
- Pages deleted from OneNote after a prior export have no `order` to recover.
  They get trailing ranks rather than being left unordered at the bottom.
- Don't edit `.obsidian/*.json` or another plugin's `data.json` while Obsidian is
  running; it caches them and will overwrite you.
