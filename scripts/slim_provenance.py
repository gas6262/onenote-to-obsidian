#!/usr/bin/env python3
"""Trim the packed provenance blob down to what is actually load-bearing.

The first pass kept all six OneNote fields. Three of them earn their place:

    t  original title   -- filenames were mangled at import ("9/3/26" -> "9-3-26")
    s  source URL       -- the click-through back to the original page
    i  onenote_id       -- the key that makes hierarchy re-sync exact

Three do not:

    c  created   ) Obsidian already tracks file times, and these were only ever
    u  updated   ) a copy of them
    p  path      -- restates the folder the note already sits in

The value also gains a DO-NOT-EDIT prefix, so the warning is visible in source
mode where accidental deletion actually happens.

Default is a dry run; pass --apply to write.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
PACKED = "quire_src"
GUARD = "DO-NOT-EDIT|"
KEEP = ("t", "s", "i")


def decode(raw: str) -> dict[str, str] | None:
    token = raw.strip().strip('"')
    if token.startswith(GUARD):
        token = token[len(GUARD):]
    try:
        return json.loads(base64.b64decode(token.encode("ascii")).decode("utf-8"))
    except Exception:
        return None


def encode(values: dict[str, str]) -> str:
    blob = json.dumps(values, separators=(",", ":"), ensure_ascii=False)
    return GUARD + base64.b64encode(blob.encode("utf-8")).decode("ascii")


def slim(path: Path) -> tuple[int, int] | None:
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        return None
    lines = m.group(1).splitlines()
    out: list[str] = []
    before = after = 0

    for ln in lines:
        if not ln.startswith(f"{PACKED}:"):
            out.append(ln)
            continue
        raw = ln.split(":", 1)[1]
        values = decode(raw)
        if values is None:
            out.append(ln)
            continue
        before = len(raw.strip())
        kept = {k: v for k, v in values.items() if k in KEEP and v}
        token = encode(kept)
        after = len(token) + 2
        out.append(f'{PACKED}: "{token}"')

    if not before:
        return None
    path.write_text("---\n" + "\n".join(out) + "\n---\n" + text[m.end():], encoding="utf-8")
    return before, after


def notes(root: Path):
    for md in sorted(root.rglob("*.md")):
        if any(p.startswith(".") for p in md.relative_to(root).parts):
            continue
        yield md


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=".", help="vault root")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    root = Path(args.vault)
    targets = [p for p in notes(root) if PACKED in p.read_text(errors="replace")[:2000]]

    if not args.apply:
        sample = targets[0] if targets else None
        print(f"Would slim {len(targets)} notes.")
        if sample:
            text = sample.read_text()
            m = FM_RE.match(text)
            raw = next(
                (ln.split(":", 1)[1] for ln in (m.group(1).splitlines() if m else [])
                 if ln.startswith(f"{PACKED}:")),
                "",
            )
            values = decode(raw) or {}
            kept = {k: v for k, v in values.items() if k in KEEP and v}
            print(f"  {sample.relative_to(root)}")
            print(f"    now:  {len(raw.strip())} chars  keys={sorted(values)}")
            print(f"    then: {len(encode(kept)) + 2} chars  keys={sorted(kept)}")
        print("\nDry run. Re-run with --apply to write.")
        return 0

    total_before = total_after = 0
    changed = 0
    for p in targets:
        r = slim(p)
        if r:
            total_before += r[0]
            total_after += r[1]
            changed += 1

    saved = total_before - total_after
    print(
        f"Slimmed {changed} notes: {total_before:,} -> {total_after:,} chars "
        f"({saved:,} removed, {saved / max(total_before, 1):.0%})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
