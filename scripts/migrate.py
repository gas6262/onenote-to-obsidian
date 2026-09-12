#!/usr/bin/env python3
"""Run the whole OneNote -> Obsidian migration, end to end.

    python3 scripts/migrate.py ~/work-notes --notebook "David@Microsoft" --work-account

Stages, in order:

  1. vault      create the vault, install Quire, set sane defaults
  2. auth       device-code sign-in to Microsoft (browser, no password here)
  3. export     pull the notebooks, writing nav_order / nav_parent as it goes
  4. chrono     re-sort date-named pages newest-first
  5. tighten    strip the blank lines that make every checklist double-spaced

Each stage is skippable and re-runnable. `--dry-run` stops after listing what
would be exported, which is the safe first move on an unfamiliar account.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from platform_paths import platform_name, recommend_vault_location

HERE = Path(__file__).resolve().parent

# A work or school account lives on a different endpoint from a personal one.
AUTHORITY = {
    "personal": "https://login.microsoftonline.com/consumers",
    "work": "https://login.microsoftonline.com/organizations",
    "either": "https://login.microsoftonline.com/common",
}


def run(cmd: list[str], **kw) -> int:
    print(f"\n$ {' '.join(str(c) for c in cmd)}\n", flush=True)
    return subprocess.run([str(c) for c in cmd], **kw).returncode


def python() -> str:
    return sys.executable or "python3"


def check_deps() -> bool:
    missing = []
    for mod in ("msal", "requests", "bs4", "markdownify"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print("Missing Python packages: " + ", ".join(missing))
        print(f"\n  {python()} -m pip install -r requirements.txt\n")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("vault", help="where the vault lives (created if absent)")
    ap.add_argument("--notebook", action="append", default=[],
                    help="notebook to import; repeatable. Omit for all.")
    ap.add_argument("--client-id", default=os.environ.get("ONENOTE_CLIENT_ID"))
    ap.add_argument("--account", choices=sorted(AUTHORITY), default="either",
                    help="personal (outlook/hotmail), work (school/org), or either")
    ap.add_argument("--newest-first", action="store_true", default=True,
                    help="newest page on top (default)")
    ap.add_argument("--oldest-first", dest="newest_first", action="store_false")
    ap.add_argument("--list-notebooks", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="proceed despite the WSL-filesystem warning")
    ap.add_argument("--skip", action="append", default=[],
                    choices=["vault", "export", "chrono", "tighten"])
    args = ap.parse_args()

    vault = Path(args.vault).expanduser().resolve()

    _, warning = recommend_vault_location(vault)
    if warning:
        print("\n  ! " + warning + "\n")
        print("    Pass a /mnt/c path, or add --force to proceed anyway.")
        if not args.force:
            return 1

    if not args.client_id:
        print(
            "No Microsoft app client id.\n\n"
            "Microsoft does not publish a shared one, so you need a free app\n"
            "registration — about three minutes, once per account:\n\n"
            "  1. https://entra.microsoft.com → App registrations → New registration\n"
            "  2. Supported account types: include personal *and* organizational\n"
            "  3. Authentication → Allow public client flows → Yes\n"
            "     (skipping this is the usual cause of AADSTS7000218)\n"
            "  4. API permissions → Microsoft Graph → Delegated → Notes.Read\n"
            "  5. Copy the Application (client) ID\n\n"
            "Then:  export ONENOTE_CLIENT_ID=<that id>\n",
            file=sys.stderr,
        )
        return 2

    if not check_deps():
        return 2

    if "vault" not in args.skip:
        mk = [python(), HERE / "make_vault.py", vault]
        if args.force:
            mk.append("--force")
        if run(mk) != 0:
            return 1

    export = [
        python(), HERE / "onenote_export.py",
        "--out", vault,
        "--client-id", args.client_id,
        "--authority", AUTHORITY[args.account],
    ]
    for nb in args.notebook:
        export += ["--notebook", nb]
    if args.newest_first:
        export.append("--newest-first")

    if args.list_notebooks:
        return run(export + ["--list-notebooks"])
    if args.dry_run:
        return run(export + ["--dry-run"])

    if "export" not in args.skip:
        if run(export) != 0:
            print("\nExport failed. Nothing downstream has run; fix and re-run.",
                  file=sys.stderr)
            return 1

    # OneNote's stored order is not reliably chronological, so dates in the
    # names win wherever they exist.
    if "chrono" not in args.skip:
        run([python(), HERE / "chrono_order.py", "--vault", vault, "--apply"])

    if "tighten" not in args.skip:
        run([python(), HERE / "tighten_lists.py", "--vault", vault, "--apply"])

    print(f"""
Done. {vault}

Next, in Obsidian:
  1. Open the vault (it is registered, or use 'Open folder as vault')
  2. Settings → Community plugins → enable Quire if it is not already on
  3. To reach other devices: Settings → Sync → turn on
     'Installed community plugins' AND 'Active community plugins'
     — on every device, since sync settings do not sync
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
