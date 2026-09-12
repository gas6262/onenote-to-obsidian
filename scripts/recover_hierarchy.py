#!/usr/bin/env python3
"""Recover OneNote page hierarchy (level + order) into Obsidian frontmatter.

The original export fetched `order` but never wrote it, and never requested
`level` at all -- so subpage nesting was lost. Both come back from Graph when
`pagelevel=true` is passed, and every exported note still carries its
`onenote_id`, so matching is exact rather than heuristic.

Writes two frontmatter keys:
    nav_order   sparse rank among siblings (step 1000)
    nav_parent  "[[Parent note]]" wikilink -- Obsidian's rename refactor
                maintains these for free, so nesting survives renames.

Default is a dry run. Pass --apply to write.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

import msal
import requests

GRAPH = "https://graph.microsoft.com/v1.0"
CLIENT_ID = "e3afdb41-c463-4f29-9114-8d1b8dacb230"
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["Notes.Read"]

RANK_STEP = 1000
FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


# --------------------------------------------------------------------- auth
def token(cache_path: Path) -> str:
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text())
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)
    accounts = app.get_accounts()
    if not accounts:
        raise SystemExit("No cached sign-in. Re-run onenote_to_obsidian.py to authenticate.")
    res = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not res or "access_token" not in res:
        raise SystemExit(f"Token refresh failed: {(res or {}).get('error_description', '?')}")
    if cache.has_state_changed:
        cache_path.write_text(cache.serialize())
    return res["access_token"]


# -------------------------------------------------------------------- vault
def scan_vault(vault: Path) -> dict[str, Path]:
    """Map onenote_id -> note path."""
    index: dict[str, Path] = {}
    for md in vault.rglob("*.md"):
        if any(part.startswith(".") for part in md.relative_to(vault).parts):
            continue
        head = md.read_text(encoding="utf-8", errors="replace")[:2000]
        m = FM_RE.match(head)
        if not m:
            continue
        for line in m.group(1).splitlines():
            if line.startswith("onenote_id:"):
                raw = line.split(":", 1)[1].strip()
                index[json.loads(raw) if raw.startswith('"') else raw] = md
                break
            if line.startswith("quire_src:"):
                raw = line.split(":", 1)[1].strip().strip('"')
                if raw.startswith("DO-NOT-EDIT|"):
                    raw = raw[len("DO-NOT-EDIT|"):]
                try:
                    blob = json.loads(base64.b64decode(raw).decode("utf-8"))
                except Exception:
                    break
                if blob.get("i"):
                    index[blob["i"]] = md
                break
    return index


def set_frontmatter(path: Path, updates: dict[str, str]) -> None:
    """Insert/replace keys in an existing frontmatter block, preserving order."""
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        raise ValueError(f"{path} has no frontmatter block")
    lines = m.group(1).splitlines()
    kept = [ln for ln in lines if ln.split(":", 1)[0].strip() not in updates]
    kept += [f"{k}: {v}" for k, v in updates.items()]
    path.write_text("---\n" + "\n".join(kept) + "\n---\n" + text[m.end():], encoding="utf-8")


# -------------------------------------------------------------------- graph
def get(url: str, tok: str, attempts: int = 5) -> list[dict]:
    """Graph throttles and occasionally 504s on OneNote reads; back off and retry."""
    out: list[dict] = []
    while url:
        body = None
        for attempt in range(attempts):
            r = requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=90)
            if r.status_code in (429, 500, 502, 503, 504):
                wait = int(r.headers.get("Retry-After", 0)) or min(2 ** attempt, 30)
                print(f"    {r.status_code}; retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            body = r.json()
            break
        if body is None:
            raise requests.HTTPError(f"gave up after {attempts} attempts: {url}")
        out.extend(body.get("value", []))
        url = body.get("@odata.nextLink")
    return out


def pages_for(section_id: str, tok: str) -> list[dict]:
    # pagelevel=true is what makes Graph populate `level` and `order`.
    return get(
        f"{GRAPH}/me/onenote/sections/{section_id}/pages"
        "?$top=100&$select=id,title,level,order&$orderby=order&pagelevel=true",
        tok,
    )


def reverse_groups(pages: list[dict]) -> list[dict]:
    """Flip order newest-first while keeping each subtree attached to its parent.

    A page and its descendants move as one block, and the blocks themselves are
    reversed -- so 2026 rises above 2025, and the dates inside 2026 flip too.
    """
    def split(items: list[dict], level: int) -> list[list[dict]]:
        blocks: list[list[dict]] = []
        for pg in items:
            if (pg.get("level") or 0) == level or not blocks:
                blocks.append([pg])
            else:
                blocks[-1].append(pg)
        return blocks

    def walk(items: list[dict], level: int) -> list[dict]:
        out: list[dict] = []
        for block in reversed(split(items, level)):
            head, rest = block[0], block[1:]
            out.append(head)
            if rest:
                out.extend(walk(rest, level + 1))
        return out

    return walk(pages, 0)


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=".", help="vault root")
    ap.add_argument("--section", action="append",
                    help="section display name; repeatable. Omit for all sections.")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--except", dest="excluded", action="append", default=[],
                    help="section display name to skip; repeatable")
    ap.add_argument("--reverse", action="store_true",
                    help="newest first: flip sibling order within each group")
    args = ap.parse_args()

    vault = Path(args.vault)
    tok = token(Path(__file__).resolve().parent / ".token-cache.json")

    print("Indexing vault by onenote_id...", file=sys.stderr)
    index = scan_vault(vault)
    print(f"  {len(index)} notes carry an onenote_id\n", file=sys.stderr)

    sections = get(f"{GRAPH}/me/onenote/sections?$select=id,displayName&$top=100", tok)
    if args.section:
        wanted = {s.lower() for s in args.section}
        sections = [s for s in sections if s["displayName"].lower() in wanted]
        if not sections:
            raise SystemExit(f"No section matched {args.section}")
    if args.excluded:
        skip = {s.lower() for s in args.excluded}
        sections = [s for s in sections if s["displayName"].lower() not in skip]

    planned: list[tuple[Path, dict[str, str], str]] = []
    failed: list[str] = []
    missing = 0

    for sec in sections:
        try:
            pages = pages_for(sec["id"], tok)
        except Exception as e:
            print(f"!!! {sec['displayName']}: {e}", file=sys.stderr)
            failed.append(sec["displayName"])
            continue
        if not pages:
            continue
        if args.reverse:
            pages = reverse_groups(pages)
        print(f"=== {sec['displayName']}  ({len(pages)} pages)")

        # ancestors[d] = wikilink target of the most recent page at depth d
        ancestors: dict[int, str] = {}
        rank: dict[int, int] = {}

        for pg in pages:
            level = pg.get("level") or 0
            path = index.get(pg["id"])
            title = pg.get("title") or "(untitled)"

            if path is None:
                print(f"  {'  ' * level}- {title}   [no matching note]")
                missing += 1
                continue

            rank[level] = rank.get(level, 0) + RANK_STEP
            # a deeper level restarts numbering beneath its new parent
            for deeper in [d for d in rank if d > level]:
                rank[deeper] = 0

            updates = {"nav_order": str(rank[level])}
            parent = ancestors.get(level - 1) if level > 0 else None
            if parent:
                updates["nav_parent"] = json.dumps(f"[[{parent}]]")

            ancestors[level] = path.stem
            for deeper in [d for d in ancestors if d > level]:
                ancestors.pop(deeper, None)

            shown = f"{'  ' * level}{path.stem}"
            detail = f"nav_order={rank[level]}"
            if parent:
                detail += f", nav_parent=[[{parent}]]"
            print(f"  {shown:<52} {detail}")
            planned.append((path, updates, sec["displayName"]))

    print(f"\n{len(planned)} notes to update; {missing} Graph pages had no local note.")
    if failed:
        print(f"{len(failed)} section(s) failed: {', '.join(failed)}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0

    for path, updates, _ in planned:
        set_frontmatter(path, updates)
    print(f"Wrote {len(planned)} notes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
