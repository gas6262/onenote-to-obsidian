#!/usr/bin/env python3
"""Remove blank lines between list items, so checklists render tight.

The OneNote conversion left a blank line between most bullets. In Markdown that
turns a "tight" list into a "loose" one: every item gets wrapped in its own
paragraph and picks up vertical margin, which is the extra spacing you see.

A blank line is only removed when the non-blank lines on both sides are list
items -- so blank lines separating a list from surrounding prose, and blank
lines inside fenced code blocks, are left alone.

Default is a dry run; pass --apply to write.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FM_RE = re.compile(r"\A(---\r?\n.*?\r?\n---\r?\n)", re.S)
LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")


def tighten(text: str) -> tuple[str, int]:
    m = FM_RE.match(text)
    head, body = (m.group(1), text[m.end():]) if m else ("", text)

    lines = body.split("\n")
    keep = [True] * len(lines)
    fence = False

    for i, ln in enumerate(lines):
        stripped = ln.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = not fence
            continue
        if fence or ln.strip():
            continue
        prev = next((lines[j] for j in range(i - 1, -1, -1) if lines[j].strip()), None)
        nxt = next((lines[j] for j in range(i + 1, len(lines)) if lines[j].strip()), None)
        if prev and nxt and LIST_ITEM.match(prev) and LIST_ITEM.match(nxt):
            keep[i] = False

    removed = keep.count(False)
    return head + "\n".join(l for l, k in zip(lines, keep) if k), removed


def notes(vault: Path, sub: str | None):
    root = vault / sub if sub else vault
    for md in sorted(root.rglob("*.md")):
        if any(p.startswith(".") for p in md.relative_to(vault).parts):
            continue
        yield md


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=".", help="vault root")
    ap.add_argument("--path", help="limit to a subfolder")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    vault = Path(args.vault)
    changed = 0
    total = 0
    sample: tuple[Path, str, str] | None = None

    for md in notes(vault, args.path):
        original = md.read_text(encoding="utf-8")
        fixed, removed = tighten(original)
        if not removed:
            continue
        changed += 1
        total += removed
        if sample is None:
            sample = (md, original, fixed)
        if args.apply:
            md.write_text(fixed, encoding="utf-8")

    if sample and not args.apply:
        path, before, after = sample
        print(f"Example — {path.relative_to(vault)}\n")
        b = [l for l in before.split("\n") if not l.startswith("quire_src")][:14]
        a = [l for l in after.split("\n") if not l.startswith("quire_src")][:14]
        w = max((len(x) for x in b), default=0) + 2
        print(f"  {'BEFORE'.ljust(w)}AFTER")
        for i in range(max(len(b), len(a))):
            print(f"  {(b[i] if i < len(b) else '').ljust(w)}{a[i] if i < len(a) else ''}")
        print()

    verb = "Tightened" if args.apply else "Would tighten"
    print(f"{verb} {changed} notes, removing {total} blank lines.", file=sys.stderr)
    if not args.apply:
        print("Dry run. Re-run with --apply to write.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
