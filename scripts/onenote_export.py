# /// script
# requires-python = ">=3.10"
# dependencies = ["msal", "requests", "beautifulsoup4", "markdownify"]
# ///
"""
Export OneNote notebooks to an Obsidian vault as Markdown.

Auth is OAuth device-code flow: you approve in a browser, this script only ever
holds a scoped access token (Notes.Read). No password passes through it.

Usage:
    export ONENOTE_CLIENT_ID=<your azure app client id>
    uv run .tools/onenote_to_obsidian.py --out .
    uv run .tools/onenote_to_obsidian.py --out . --notebook "Work" --dry-run
"""

from __future__ import annotations

import argparse
import base64
import atexit
import json
import mimetypes
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import msal
import requests
from bs4 import BeautifulSoup, NavigableString
from markdownify import MarkdownConverter

GRAPH = "https://graph.microsoft.com/v1.0"
# Notes.Read.All is work/school only; personal Microsoft accounts must use
# Notes.Read, or the OneNote service rejects the token with error 40001.
SCOPES = ["Notes.Read"]
ATTACHMENT_DIR = "_attachments"

# Placeholder tokens survive markdownify's escaping; swapped for wikilinks after.
IMG_TOKEN = "\x00IMG:{}\x00"
FILE_TOKEN = "\x00FILE:{}\x00"
# Line breaks inside a table cell: a real newline would split the row.
CELL_BR = "\x00BR\x00"

# OneNote note tags -> Markdown prefixes.
TAG_PREFIX = {
    "to-do": "[ ] ",
    "to-do:completed": "[x] ",
    "important": "⭐ ",
    "question": "❓ ",
    "remember-for-later": "\U0001f4cc ",
    "definition": "\U0001f4d6 ",
    "highlight": "",
}


# --------------------------------------------------------------------------- auth
def get_token(client_id: str, authority: str, cache_path: Path) -> str:
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text())
    atexit.register(
        lambda: cache_path.write_text(cache.serialize()) if cache.has_state_changed else None
    )

    app = msal.PublicClientApplication(client_id, authority=authority, token_cache=cache)

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            print(f"Reusing cached sign-in for {accounts[0].get('username')}", file=sys.stderr)
            return result["access_token"]

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise SystemExit(f"Could not start device flow: {flow.get('error_description', flow)}")

    print("\n" + "=" * 62, file=sys.stderr)
    print(flow["message"], file=sys.stderr)
    print("=" * 62 + "\n", file=sys.stderr)

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise SystemExit(f"Sign-in failed: {result.get('error_description', result)}")
    print(f"Signed in as {result.get('id_token_claims', {}).get('preferred_username', '?')}",
          file=sys.stderr)
    return result["access_token"]


# --------------------------------------------------------------------------- graph
class Graph:
    def __init__(self, token_provider):
        # A long export outlives a one-hour access token, so hold the provider
        # and re-acquire (silently, from the refresh token) when one expires.
        self.s = requests.Session()
        self._token_provider = token_provider
        self._set_token(token_provider())

    def _set_token(self, token: str) -> None:
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        r = None
        refreshed = False
        for attempt in range(6):
            try:
                # (connect, read). Several graph.microsoft.com IPs blackhole TCP;
                # a short connect timeout fails over to the next address in
                # seconds instead of stalling for the full read timeout.
                r = self.s.request(method, url, timeout=(5, 90), **kw)
            except requests.exceptions.RequestException as e:
                # The pool would hand back the same dead IP on every retry, so
                # drop it and force a fresh DNS resolution + connection.
                self.s.close()
                wait = 2 ** attempt
                print(f"  connection error ({type(e).__name__}), retrying in {wait}s",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            if r.status_code == 401 and not refreshed:
                print("  access token expired, refreshing", file=sys.stderr)
                self._set_token(self._token_provider())
                refreshed = True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                print(f"  throttled ({r.status_code}), retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        if r is None:
            raise RuntimeError(f"could not reach {url} after 6 attempts")
        r.raise_for_status()
        return r

    def paged(self, url: str) -> list[dict]:
        """Follow @odata.nextLink and return all items."""
        items: list[dict] = []
        while url:
            data = self._request("GET", url).json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink", "")
        return items

    def content(self, url: str) -> bytes:
        return self._request("GET", url).content


# --------------------------------------------------------------------------- naming
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, fallback: str = "Untitled") -> str:
    name = _ILLEGAL.sub("-", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    # Obsidian/Markdown choke on these inside wikilinks.
    name = name.replace("[", "(").replace("]", ")").replace("#", "-").replace("^", "-")
    if len(name) > 120:
        name = name[:120].rstrip(" .")
    return name or fallback


def unique_path(base: Path, stem: str, suffix: str) -> Path:
    candidate = base / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = base / f"{stem} ({n}){suffix}"
        n += 1
    return candidate


# --------------------------------------------------------------------------- convert
class OneNoteConverter(MarkdownConverter):
    """markdownify with OneNote's absolutely-positioned div soup flattened."""

    def convert_div(self, el, text, parent_tags=None):
        return text if text.strip() else ""

    def convert_span(self, el, text, parent_tags=None):
        return text


def download_resource(graph: Graph, url: str, out_dir: Path, hint: str, mime: str) -> str | None:
    """Fetch a OneNote resource, write it under out_dir, return its filename."""
    try:
        blob = graph.content(url)
    except Exception as e:  # a dead resource shouldn't kill the whole export
        print(f"  ! could not fetch resource: {e}", file=sys.stderr)
        return None

    stem = safe_name(Path(hint).stem or "attachment", "attachment")
    ext = Path(hint).suffix
    if not ext and mime:
        ext = mimetypes.guess_extension(mime.split(";")[0].strip()) or ""
    if not ext:
        ext = ".bin"

    out_dir.mkdir(parents=True, exist_ok=True)
    path = unique_path(out_dir, stem, ext)
    path.write_bytes(blob)
    return path.name


def page_to_markdown(graph: Graph, html: str, attach_dir: Path, download: bool,
                     stem_hint: str = "image") -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Drop the head: markdownify's `strip` removes tags but keeps their text,
    # which would leak <title> in as a stray line above the body.
    for dead in soup.find_all(["head", "title", "meta", "style", "script"]):
        dead.decompose()

    # OneNote's note tags live on spans/paragraphs as data-tag attributes.
    for el in soup.select("[data-tag]"):
        tag = el.get("data-tag", "").split(";")[0]
        prefix = TAG_PREFIX.get(tag)
        if not prefix:
            continue
        # Checkboxes need a list marker to become Obsidian tasks, unless the
        # element already sits in an <li> that markdownify will prefix itself.
        if tag.startswith("to-do") and not el.find_parent("li"):
            prefix = "- " + prefix
        el.insert(0, prefix)

    # OneNote uses single-column tables as layout boxes, not data. Rendered as
    # Markdown they bury an entire page inside one cell and flatten any list
    # they contain, so unwrap them and let their blocks convert normally.
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if rows and all(len(r.find_all(["td", "th"])) <= 1 for r in rows):
            for tag in table.find_all(["td", "th", "tr", "tbody", "thead"]):
                tag.unwrap()
            table.unwrap()

    # Content pasted into OneNote (code especially) encodes its line breaks as
    # U+FFFC object-replacement characters inside <span>s rather than as markup.
    # In a table cell that has to become a <br>; elsewhere a newline suffices,
    # and either way the character itself must not survive into the Markdown.
    for node in soup.find_all(string=True):
        if "\ufffc" in node:
            sep = CELL_BR if node.find_parent(["td", "th"]) else "\n"
            node.replace_with(node.replace("\ufffc", sep))

    # OneNote tables rarely use <th>; without one markdownify emits an empty
    # header and demotes the real first row into the body.
    for table in soup.find_all("table"):
        if table.find("th"):
            continue
        first = table.find("tr")
        if not first:
            continue
        for cell in first.find_all("td", recursive=False) or first.find_all("td"):
            cell.name = "th"

    # OneNote puts each line of a cell in its own <p>. markdownify turns those
    # into real newlines, which tear the row apart, so join them with a token
    # that becomes a <br> once the table syntax is already built.
    for cell in soup.find_all(["td", "th"]):
        blocks = cell.find_all(["p", "div"], recursive=False)
        for block in blocks[:-1]:
            block.insert_after(CELL_BR)
        for block in blocks:
            block.unwrap()
        for br in cell.find_all("br"):
            br.replace_with(CELL_BR)
        if not cell.get_text().replace(CELL_BR, "").strip():
            cell.clear()  # a cell holding only breaks is an empty cell

    # OneNote pads tables with blank cells; as Markdown they are pure noise.
    def _blank(cell) -> bool:
        return not cell.get_text().replace(CELL_BR, "").strip()

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        while rows and all(_blank(c) for c in rows[-1].find_all(["td", "th"])):
            rows.pop().decompose()
        if not rows:
            continue
        grid = [r.find_all(["td", "th"]) for r in rows]
        for idx in reversed(range(max(len(cells) for cells in grid))):
            if all(idx >= len(cells) or _blank(cells[idx]) for cells in grid):
                for cells in grid:
                    if idx < len(cells):
                        cells[idx].decompose()

        # Decomposing cells strands the whitespace that separated them, which
        # markdownify would emit as stray newlines splitting the table apart.
        for row in rows:
            for node in list(row.children):
                if isinstance(node, NavigableString) and not node.strip():
                    node.extract()

    # Images -> local files.
    for img in soup.find_all("img"):
        src = img.get("data-fullres-src") or img.get("src")
        mime = img.get("data-fullres-src-type") or img.get("data-src-type") or ""
        if not src:
            img.decompose()
            continue
        if not download or not src.startswith("http"):
            img.replace_with(img.get("alt") or "")
            continue
        name = download_resource(graph, src, attach_dir, stem_hint, mime)
        img.replace_with(IMG_TOKEN.format(name) if name else (img.get("alt") or ""))

    # Embedded file attachments.
    for obj in soup.find_all("object"):
        src, hint = obj.get("data"), obj.get("data-attachment") or "attachment"
        if not download or not src or not src.startswith("http"):
            obj.replace_with(f"(attachment: {hint})")
            continue
        name = download_resource(graph, src, attach_dir, hint, obj.get("type") or "")
        obj.replace_with(FILE_TOKEN.format(name) if name else f"(attachment: {hint})")

    md = OneNoteConverter(heading_style="ATX", bullets="-").convert_soup(soup)

    # Swap placeholders for Obsidian wikilinks, now that escaping is done.
    md = re.sub(r"\x00IMG:(.*?)\x00", lambda m: f"![[{m.group(1)}]]", md)
    md = re.sub(r"\x00FILE:(.*?)\x00", lambda m: f"[[{m.group(1)}]]", md)
    md = md.replace(CELL_BR, "<br>")
    md = "\n".join(
        re.sub(r"(?:\s*<br>)+\s*\|", " |", re.sub(r"\|\s*(?:<br>\s*)+", "| ", ln))
        if ln.lstrip().startswith("|") else ln
        for ln in md.split("\n")
    )

    # OneNote emits runs of <br>; blank out whitespace-only lines so the
    # collapse below can fold them, but leave real hard breaks alone.
    md = re.sub(r"(?m)^[ \t]+$", "", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


def reverse_page_groups(pages: list[dict]) -> list[dict]:
    """Newest first, with each page's subtree travelling as one block."""
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
            out.append(block[0])
            if block[1:]:
                out.extend(walk(block[1:], level + 1))
        return out

    return walk(pages, 0)


def frontmatter(page: dict, _trail: list[str], nav_order: int,
                nav_parent: str | None) -> str:
    """Two working keys, plus one packed line of provenance.

    nav_order / nav_parent are what the Quire plugin reads. nav_parent is a
    wikilink on purpose: Obsidian's rename refactor maintains those, so nesting
    survives a page being renamed. Everything else -- the original title, the
    OneNote id, the URL back to the page -- is base64-packed into a single
    quire_src value, because it is worth keeping and never worth reading.
    """
    def esc(v: str) -> str:
        return json.dumps(v, ensure_ascii=False)

    provenance = {
        "t": page.get("title") or "Untitled",
        "s": (page.get("links") or {}).get("oneNoteWebUrl", {}).get("href") or "",
        "i": page.get("id", ""),
    }
    blob = json.dumps({k: v for k, v in provenance.items() if v},
                      separators=(",", ":"), ensure_ascii=False)
    packed = "DO-NOT-EDIT|" + base64.b64encode(blob.encode("utf-8")).decode("ascii")

    lines = ["---", f"nav_order: {nav_order}"]
    if nav_parent:
        lines.append(f"nav_parent: {esc(f'[[{nav_parent}]]')}")
    lines.append(f"quire_src: {esc(packed)}")
    lines.append("---\n")
    return "\n".join(lines)


# --------------------------------------------------------------------------- walk
def export_section(graph: Graph, section: dict, out_dir: Path, trail: list[str],
                   attach_dir: Path, args) -> int:
    # `pagelevel=true` is undocumented and mandatory: without it Graph returns
    # level and order as null, which is how the first migration silently lost
    # every subpage relationship and every manual ordering.
    pages = graph.paged(
        f"{GRAPH}/me/onenote/sections/{section['id']}/pages"
        "?$top=100&$select=id,title,createdDateTime,lastModifiedDateTime,level,order,links"
        "&$orderby=order&pagelevel=true"
    )
    if not pages:
        return 0
    pages.sort(key=lambda p: p.get("order") or 0)
    if getattr(args, "newest_first", False):
        pages = reverse_page_groups(pages)

    print(f"  {'/'.join(trail)} - {len(pages)} page(s)", file=sys.stderr)
    if args.dry_run:
        return len(pages)

    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    ancestors: dict[int, str] = {}   # level -> stem of the most recent page there
    rank: dict[int, int] = {}
    for page in pages:
        level = page.get("level") or 0
        title = safe_name(page.get("title"), "Untitled")
        try:
            html = graph.content(
                f"{GRAPH}/me/onenote/pages/{page['id']}/content?includeIDs=true"
            ).decode("utf-8", "replace")
            body = page_to_markdown(graph, html, attach_dir,
                                    not args.no_attachments, stem_hint=title)
        except Exception as e:
            print(f"  ! skipped {title!r}: {e}", file=sys.stderr)
            continue

        path = unique_path(out_dir, title, ".md")

        rank[level] = rank.get(level, 0) + 1000
        for deeper in [d for d in rank if d > level]:
            rank[deeper] = 0
        parent = ancestors.get(level - 1) if level > 0 else None
        ancestors[level] = path.stem
        for deeper in [d for d in ancestors if d > level]:
            ancestors.pop(deeper, None)

        header = (frontmatter(page, trail, rank[level], parent)
                  if not args.no_frontmatter else "")
        path.write_text(f"{header}\n# {page.get('title') or 'Untitled'}\n\n{body}", encoding="utf-8")
        count += 1
    return count


def export_group(graph: Graph, kind: str, gid: str, out_dir: Path, trail: list[str],
                 attach_dir: Path, args) -> int:
    """Recurse a notebook or section group: its sections, then its subgroups."""
    total = 0
    for sec in graph.paged(f"{GRAPH}/me/onenote/{kind}/{gid}/sections?$select=id,displayName"):
        name = safe_name(sec.get("displayName"), "Section")
        total += export_section(graph, sec, out_dir / name, trail + [name], attach_dir, args)

    for grp in graph.paged(f"{GRAPH}/me/onenote/{kind}/{gid}/sectionGroups?$select=id,displayName"):
        name = safe_name(grp.get("displayName"), "Group")
        total += export_group(graph, "sectionGroups", grp["id"], out_dir / name,
                              trail + [name], attach_dir, args)
    return total


def main() -> int:
    p = argparse.ArgumentParser(description="Export OneNote to an Obsidian vault.")
    p.add_argument("--out", default=".", help="vault root (default: cwd)")
    p.add_argument("--notebook", action="append", default=[],
                   help="only this notebook (repeatable)")
    p.add_argument("--client-id", default=os.environ.get("ONENOTE_CLIENT_ID"))
    p.add_argument("--authority",
                   default=os.environ.get("ONENOTE_AUTHORITY",
                                          "https://login.microsoftonline.com/common"))
    p.add_argument("--dry-run", action="store_true", help="list what would be exported")
    p.add_argument("--newest-first", action="store_true",
                   help="reverse page order so the newest sits on top")
    p.add_argument("--list-notebooks", action="store_true",
                   help="print the notebooks this account can see, then exit")
    p.add_argument("--no-attachments", action="store_true")
    p.add_argument("--no-frontmatter", action="store_true")
    args = p.parse_args()

    if not args.client_id:
        print("Missing client id. Set ONENOTE_CLIENT_ID or pass --client-id.\n"
              "See .tools/README.md for the 3-minute app registration.", file=sys.stderr)
        return 2

    vault = Path(args.out).resolve()
    cache_path = vault / ".tools" / ".token-cache.json"
    graph = Graph(lambda: get_token(args.client_id, args.authority, cache_path))
    attach_dir = vault / ATTACHMENT_DIR

    notebooks = graph.paged(
        f"{GRAPH}/me/onenote/notebooks?$select=id,displayName,isShared")
    if args.list_notebooks:
        for nb in notebooks:
            tag = "shared" if nb.get("isShared") else "own"
            print(f"  [{tag:>6}]  {nb.get('displayName')}")
        return 0
    if args.notebook:
        wanted = {n.lower() for n in args.notebook}
        notebooks = [n for n in notebooks if (n.get("displayName") or "").lower() in wanted]
    if not notebooks:
        print("No matching notebooks found.", file=sys.stderr)
        return 1

    print(f"\n{len(notebooks)} notebook(s) -> {vault}\n", file=sys.stderr)
    total = 0
    for nb in notebooks:
        name = safe_name(nb.get("displayName"), "Notebook")
        print(f"{name}", file=sys.stderr)
        total += export_group(graph, "notebooks", nb["id"], vault / name,
                              [name], attach_dir, args)

    verb = "would export" if args.dry_run else "exported"
    print(f"\nDone - {verb} {total} page(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
