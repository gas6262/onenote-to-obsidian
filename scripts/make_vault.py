#!/usr/bin/env python3
"""Create an Obsidian vault, preconfigured, without opening Obsidian first.

A vault is just a folder containing a `.obsidian` directory -- there is no CLI
and no "create vault" URL scheme, so this writes the config directly and then
registers the vault so Obsidian lists it on the next launch.

What it sets up:
  * core plugin selection, with the file explorer left enabled as a fallback
  * the Quire navigator, installed and enabled
  * the wide-pages snippet, enabled
  * "new notes go in the current folder", which is not the default and is
    almost always what you want when notes and drawings live together

Registering the vault edits Obsidian's global config. If Obsidian is running it
holds that file in memory and may overwrite the change on exit, so this asks you
to quit first and says so rather than failing silently.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from platform_paths import (
    install_hint,
    obsidian_config_dir,
    obsidian_installed,
    open_path,
    platform_name,
    recommend_vault_location,
    to_windows_path,
)

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent

CORE_PLUGINS = {
    "file-explorer": True,
    "global-search": True,
    "switcher": True,
    "graph": False,
    "backlink": True,
    "canvas": True,
    "outgoing-link": True,
    "tag-pane": True,
    "properties": True,
    "page-preview": True,
    "daily-notes": True,
    "templates": True,
    "note-composer": True,
    "command-palette": True,
    "editor-status": True,
    "bookmarks": True,
    "outline": True,
    "word-count": True,
    "file-recovery": True,
    "sync": True,
    "bases": True,
}

APP_SETTINGS = {
    # Drawings and attachments land beside the note instead of in a far-off
    # folder -- the single setting that makes "open them side by side" work.
    "newFileLocation": "current",
    "attachmentFolderPath": "./_attachments",
    "alwaysUpdateLinks": True,
    "useMarkdownLinks": False,
    "showInlineTitle": True,
}


def install_obsidian() -> bool:
    if obsidian_installed():
        print("Obsidian is already installed.")
        return True
    if platform_name() == "macos" and shutil.which("brew"):
        print("Installing Obsidian via Homebrew…")
        return subprocess.run(["brew", "install", "--cask", "obsidian"]).returncode == 0
    print("\nObsidian is not installed.\n  " + install_hint() + "\n")
    return False


def obsidian_running() -> bool:
    kind = platform_name()
    try:
        if kind == "macos":
            return subprocess.run(["pgrep", "-x", "Obsidian"],
                                  capture_output=True).returncode == 0
        if kind == "wsl":
            out = subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq Obsidian.exe"],
                                 capture_output=True, text=True, timeout=20, cwd="/mnt/c")
            return "Obsidian.exe" in out.stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return False


def vault_id(path: Path) -> str:
    """Obsidian keys vaults by a 16-hex id; any stable unique value works."""
    import hashlib

    return hashlib.sha256(str(path).encode()).hexdigest()[:16]


def register_vault(path: Path) -> bool:
    cfgdir = obsidian_config_dir()
    if cfgdir is None:
        print("Could not locate Obsidian's config directory; skipping registration.")
        print("  Use 'Open folder as vault' in Obsidian instead.")
        return False
    cfg = cfgdir / "obsidian.json"
    if not cfgdir.exists():
        print(f"No Obsidian config at {cfgdir} — launch Obsidian once, then re-run.")
        return False

    # Obsidian on Windows stores Windows paths. Registering a WSL path from
    # inside the distro produces an entry Obsidian silently cannot open.
    if platform_name() == "wsl":
        recorded = to_windows_path(path)
        if not recorded:
            print("wslpath failed; skipping registration. Use 'Open folder as vault'.")
            return False
    else:
        recorded = str(path)

    data = json.loads(cfg.read_text()) if cfg.exists() else {}
    vaults = data.setdefault("vaults", {})
    for existing in vaults.values():
        if existing.get("path") == recorded:
            print("Vault already registered.")
            return True
    vaults[vault_id(path)] = {"path": recorded, "ts": int(time.time() * 1000)}
    cfg.write_text(json.dumps(data, indent=2) + "\n")
    return True


def scaffold(vault: Path, name: str) -> None:
    cfgdir = vault / ".obsidian"
    (cfgdir / "plugins" / "quire").mkdir(parents=True, exist_ok=True)
    (cfgdir / "snippets").mkdir(parents=True, exist_ok=True)
    (vault / "_attachments").mkdir(exist_ok=True)

    (cfgdir / "core-plugins.json").write_text(json.dumps(CORE_PLUGINS, indent=2) + "\n")
    (cfgdir / "app.json").write_text(json.dumps(APP_SETTINGS, indent=2) + "\n")
    (cfgdir / "community-plugins.json").write_text(json.dumps(["quire"], indent=2) + "\n")
    (cfgdir / "appearance.json").write_text(
        json.dumps({"enabledCssSnippets": ["wide-pages"]}, indent=2) + "\n"
    )

    for f in ("main.js", "manifest.json", "styles.css"):
        src = SKILL / "plugin" / f
        if src.exists():
            shutil.copy2(src, cfgdir / "plugins" / "quire" / f)
        else:
            print(f"  ! plugin/{f} missing — build it with `npm run build` in plugin/")

    snippet = SKILL / "snippets" / "wide-pages.css"
    if snippet.exists():
        shutil.copy2(snippet, cfgdir / "snippets" / "wide-pages.css")

    readme = vault / "Start here.md"
    if not readme.exists():
        readme.write_text(
            f"# {name}\n\n"
            "This vault was set up by the onenote-to-obsidian skill.\n\n"
            "- **Quire** is in the left sidebar: coloured folders, notes that nest\n"
            "  to any depth, drag to reorder.\n"
            "- Reordering also works by command: *Move note up / down*, *Indent*,\n"
            "  *Outdent*. Bind those to hotkeys — they are the only way to reorder\n"
            "  on iPhone, where there is no Apple Pencil.\n"
            "- To sync: Settings → Sync, then turn on **Installed community\n"
            "  plugins** and **Active community plugins** on *every* device.\n"
            "  Sync settings do not themselves sync.\n",
            encoding="utf-8",
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="where to create the vault")
    ap.add_argument("--name", help="display name (default: folder name)")
    ap.add_argument("--no-register", action="store_true")
    ap.add_argument("--open", action="store_true", help="open it in Obsidian when done")
    ap.add_argument("--force", action="store_true",
                    help="proceed despite a WSL-filesystem or missing-Obsidian warning")
    args = ap.parse_args()

    vault = Path(args.path).expanduser().resolve()
    name = args.name or vault.name

    _, warning = recommend_vault_location(vault)
    if warning:
        print("\n  ! " + warning + "\n")
        if not args.force:
            print("    Re-run with --force to use it anyway, or pass a /mnt/c path.")
            return 1

    if not install_obsidian() and not args.force:
        return 1

    existed = (vault / ".obsidian").exists()
    vault.mkdir(parents=True, exist_ok=True)
    scaffold(vault, name)
    print(f"{'Updated' if existed else 'Created'} vault: {vault}")

    if not args.no_register:
        if obsidian_running():
            print("\n  ! Obsidian is running. It caches its vault list in memory and may")
            print("    discard this registration on quit. Either quit Obsidian and re-run")
            print("    with --no-register omitted, or just use 'Open folder as vault'.")
        if register_vault(vault):
            print("Registered with Obsidian.")

    if args.open:
        target = to_windows_path(vault) if platform_name() == "wsl" else str(vault)
        open_path(f"obsidian://open?path={target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
