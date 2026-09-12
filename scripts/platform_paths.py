#!/usr/bin/env python3
"""Locate Obsidian across macOS, Windows, Linux, and WSL.

WSL is the awkward one, and it is awkward in a specific way: the scripts run in
Linux but **Obsidian runs on Windows**. So three things differ from a normal
Linux run, and all three fail quietly rather than loudly if you ignore them.

  1. Obsidian's config lives at %APPDATA%\\obsidian on the Windows side, not at
     ~/.config/obsidian inside the distro.
  2. That config stores Windows paths (C:\\Users\\...), so a vault registered
     with a Linux path is simply never found.
  3. A vault kept on the WSL filesystem is reached by Windows over the \\\\wsl$
     network share. Obsidian works through it, but file watching is unreliable
     and large vaults crawl -- so the vault belongs on the Windows drive, which
     WSL reaches at /mnt/c at full speed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    if is_wsl():
        return "wsl"
    return "linux"


# --------------------------------------------------------------- WSL helpers

def _windows_env(var: str) -> str | None:
    """Read a Windows environment variable from inside WSL."""
    try:
        out = subprocess.run(
            ["cmd.exe", "/c", f"echo %{var}%"],
            capture_output=True, text=True, timeout=15,
            cwd="/mnt/c",  # cmd.exe warns loudly when cwd is a UNC path
        )
        value = out.stdout.strip()
        return value if value and not value.startswith("%") else None
    except (OSError, subprocess.SubprocessError):
        return None


def to_windows_path(p: Path) -> str | None:
    """/mnt/c/Users/x  ->  C:\\Users\\x"""
    try:
        out = subprocess.run(["wslpath", "-w", str(p)],
                             capture_output=True, text=True, timeout=15)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def to_wsl_path(win: str) -> Path | None:
    try:
        out = subprocess.run(["wslpath", "-u", win],
                             capture_output=True, text=True, timeout=15)
        return Path(out.stdout.strip()) if out.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def windows_home() -> Path | None:
    """The Windows user profile, as a path WSL can write to."""
    profile = _windows_env("USERPROFILE")
    return to_wsl_path(profile) if profile else None


# ------------------------------------------------------------ obsidian paths

def obsidian_config_dir() -> Path | None:
    kind = platform_name()
    if kind == "macos":
        return Path.home() / "Library/Application Support/obsidian"
    if kind == "windows":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "obsidian" if appdata else None
    if kind == "wsl":
        appdata = _windows_env("APPDATA")
        if not appdata:
            return None
        base = to_wsl_path(appdata)
        return base / "obsidian" if base else None
    return Path.home() / ".config/obsidian"


def obsidian_installed() -> bool:
    kind = platform_name()
    if kind == "macos":
        return Path("/Applications/Obsidian.app").exists()
    if kind == "windows":
        return shutil.which("obsidian") is not None or bool(
            list(Path(os.environ.get("LOCALAPPDATA", "")).glob("Obsidian/Obsidian.exe"))
        )
    if kind == "wsl":
        home = windows_home()
        if not home:
            return False
        candidates = [
            home / "AppData/Local/Obsidian/Obsidian.exe",
            home / "AppData/Local/Programs/Obsidian/Obsidian.exe",
            Path("/mnt/c/Program Files/Obsidian/Obsidian.exe"),
        ]
        return any(c.exists() for c in candidates)
    return shutil.which("obsidian") is not None


def install_hint() -> str:
    kind = platform_name()
    if kind == "macos":
        return "brew install --cask obsidian"
    if kind in ("windows", "wsl"):
        return (
            "Install Obsidian on the *Windows* side, not inside WSL:\n"
            "    winget install Obsidian.Obsidian\n"
            "  or download from https://obsidian.md/download"
        )
    return "Download the AppImage or .deb from https://obsidian.md/download"


def open_path(target: str) -> bool:
    """Open a file, folder, or URL with the host GUI."""
    kind = platform_name()
    try:
        if kind == "macos":
            subprocess.run(["open", target], timeout=20)
        elif kind == "windows":
            os.startfile(target)  # type: ignore[attr-defined]
        elif kind == "wsl":
            if target.startswith(("http://", "https://", "obsidian://")):
                subprocess.run(["cmd.exe", "/c", "start", "", target],
                               cwd="/mnt/c", timeout=20)
            else:
                win = to_windows_path(Path(target))
                subprocess.run(["explorer.exe", win or target], timeout=20)
        else:
            subprocess.run(["xdg-open", target], timeout=20)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def recommend_vault_location(requested: Path) -> tuple[Path, str | None]:
    """On WSL, steer the vault onto the Windows drive.

    Returns (path_to_use, warning). The requested path is never silently
    overridden -- the caller decides what to do with the warning.
    """
    if platform_name() != "wsl":
        return requested, None
    if str(requested).startswith("/mnt/"):
        return requested, None
    home = windows_home()
    suggestion = home / requested.name if home else None
    warning = (
        f"{requested} is on the WSL filesystem.\n"
        "  Windows Obsidian would reach it over the \\\\wsl$ network share: file\n"
        "  watching becomes unreliable and a large vault gets slow.\n"
    )
    if suggestion:
        warning += f"  Recommended instead:  {suggestion}"
    return requested, warning
