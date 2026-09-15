#!/usr/bin/env python3
"""Copy one vault's plugin setup onto another, minus the parts that don't travel.

A straight `cp -R` of `.obsidian` looks right and quietly drags personal state
into the new vault. Three things need handling rather than copying:

  * Recent Files stores a list of paths from the source vault. The preferences
    are worth keeping; the history is not.
  * Terminal can hold absolute paths to launcher scripts inside the source
    vault. Those are rewritten, and referenced scripts under .tools/ copied.
  * Quire keys collapsed folders, hues and icons to folders that exist in the
    source vault and generally do not exist in the target.

Workspace layout, bookmarks and graph state are skipped for the same reason:
they describe files the target vault does not have.

    python3 clone_vault_config.py ~/source/notes ~/Documents/notes/work

Default is a dry run; pass --apply to write.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Settings that describe the source vault's *content* rather than preferences.
SKIP_CONFIG = {"workspace.json", "workspace-mobile.json", "bookmarks.json", "graph.json"}

# Plugins whose data.json is preferences only, so it copies verbatim.
PORTABLE = {
    "obsidian-excalidraw-plugin",
    "editing-toolbar",
    "omnisearch",
    "obsidian-minimal-settings",
}

# Plugins whose settings are rewritten or reset. Anything not listed anywhere
# gets its code copied and its settings left behind, which is the safe default.
RESET_HISTORY = {"recent-files-obsidian": ["recentFiles"]}
REWRITE_PATHS = {"terminal"}
SETTINGS_ARE_VAULT_SPECIFIC = {"quire"}


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def copy_plugin(name: str, src: Path, dst: Path, src_vault: Path, dst_vault: Path,
                apply: bool) -> list[str]:
    notes: list[str] = []
    s, d = src / "plugins" / name, dst / "plugins" / name
    if not s.exists():
        return [f"{name}: not present in source, skipped"]

    if apply:
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(s, d, ignore=shutil.ignore_patterns("data.json"))
    notes.append(f"{name}: code")

    data_src = s / "data.json"
    if not data_src.exists():
        return notes

    if name in SETTINGS_ARE_VAULT_SPECIFIC:
        notes.append(f"{name}: settings NOT copied (keyed to source vault's folders)")
        return notes

    data = load(data_src)

    if name in RESET_HISTORY:
        for key in RESET_HISTORY[name]:
            dropped = len(data.get(key, []))
            data[key] = []
            notes.append(f"{name}: settings, {dropped} remembered path(s) dropped")
    elif name in REWRITE_PATHS:
        raw = json.dumps(data)
        if str(src_vault) in raw:
            data = json.loads(raw.replace(str(src_vault), str(dst_vault)))
            notes.append(f"{name}: settings, absolute paths repointed")
            # Bring across any launcher scripts those paths referred to.
            tools = src_vault / ".tools"
            if tools.exists() and apply:
                (dst_vault / ".tools").mkdir(exist_ok=True)
                for f in tools.glob("*.sh"):
                    shutil.copy2(f, dst_vault / ".tools" / f.name)
                    notes.append(f"{name}: carried .tools/{f.name}")
        else:
            notes.append(f"{name}: settings")
    elif name in PORTABLE:
        notes.append(f"{name}: settings")
    else:
        notes.append(f"{name}: settings (unreviewed plugin — check it after)")

    if apply:
        (d / "data.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="vault to copy the setup FROM")
    ap.add_argument("target", help="vault to copy it TO")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    src_vault = Path(args.source).expanduser().resolve()
    dst_vault = Path(args.target).expanduser().resolve()
    src, dst = src_vault / ".obsidian", dst_vault / ".obsidian"

    for label, p in (("source", src), ("target", dst_vault)):
        if not p.exists():
            print(f"No {label} at {p}", file=sys.stderr)
            return 1
    if not dst.exists() and args.apply:
        dst.mkdir(parents=True)

    plugins = sorted(p.name for p in (src / "plugins").glob("*") if p.is_dir())
    print(f"{src_vault}\n  -> {dst_vault}\n")

    for name in plugins:
        for line in copy_plugin(name, src, dst, src_vault, dst_vault, args.apply):
            print(f"  {line}")

    print()
    for f in ("core-plugins.json", "appearance.json", "hotkeys.json", "app.json"):
        s = src / f
        if not s.exists():
            continue
        if f == "app.json" and (dst / f).exists():
            print(f"  {f}: target's own kept (it may hold better defaults)")
            continue
        if args.apply:
            shutil.copy2(s, dst / f)
        print(f"  {f}: copied")

    snippets = src / "snippets"
    if snippets.exists():
        if args.apply:
            (dst / "snippets").mkdir(exist_ok=True)
            for f in snippets.glob("*.css"):
                shutil.copy2(f, dst / "snippets" / f.name)
        print(f"  snippets: {len(list(snippets.glob('*.css')))} copied")

    for f in sorted(SKIP_CONFIG):
        if (src / f).exists():
            print(f"  {f}: skipped (describes the source vault's files)")

    if args.apply:
        (dst / "community-plugins.json").write_text(
            json.dumps(plugins, indent=2) + "\n", encoding="utf-8")
        print(f"\n  community-plugins.json: {len(plugins)} enabled")
        print("\nDone. In Obsidian: reload (Cmd+R), then Settings → Community plugins")
        print("→ 'Turn on community plugins' if the vault is still in Restricted mode.")
    else:
        print("\nDry run. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
