#!/usr/bin/env python3
"""Fold the six OneNote provenance keys into one opaque `quire_src` value.

The export wrote title/created/updated/source/onenote_id/onenote_path to every
note -- six lines of machinery above every page. None of it is useful to read,
but all of it is worth keeping: `onenote_id` is what makes hierarchy recovery
exact, and `source` is the way back to the original page.

So it is packed, not discarded: base64 of a compact JSON object.

    quire_src: "eyJ0IjoiOS8zLzI2IiwiaSI6IjAtYWU2Nzh..."

Keys inside the blob:  t title · c created · u updated
                       s source · i onenote_id · p onenote_path

Round-trips losslessly. Use --decode to read one back, --expand to restore.
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
FIELDS = {
    "title": "t",
    "created": "c",
    "updated": "u",
    "source": "s",
    "onenote_id": "i",
    "onenote_path": "p",
}


def parse_scalar(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip('"')
    return raw


def pack(values: dict[str, str]) -> str:
    blob = json.dumps(values, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(blob.encode("utf-8")).decode("ascii")


GUARD = "DO-NOT-EDIT|"


def unpack(token: str) -> dict[str, str]:
    if token.startswith(GUARD):
        token = token[len(GUARD):]
    return json.loads(base64.b64decode(token.encode("ascii")).decode("utf-8"))


def split_note(path: Path) -> tuple[list[str], str] | None:
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        return None
    return m.group(1).splitlines(), text[m.end():]


def write_note(path: Path, lines: list[str], body: str) -> None:
    path.write_text("---\n" + "\n".join(lines) + "\n---\n" + body, encoding="utf-8")


def compact(path: Path) -> bool:
    parsed = split_note(path)
    if not parsed:
        return False
    lines, body = parsed
    if any(ln.startswith(f"{PACKED}:") for ln in lines):
        return False

    found: dict[str, str] = {}
    kept: list[str] = []
    for ln in lines:
        key = ln.split(":", 1)[0].strip()
        if key in FIELDS and ":" in ln:
            found[FIELDS[key]] = parse_scalar(ln.split(":", 1)[1])
        else:
            kept.append(ln)
    if not found:
        return False

    kept.append(f'{PACKED}: "{pack(found)}"')
    write_note(path, kept, body)
    return True


def expand(path: Path) -> bool:
    parsed = split_note(path)
    if not parsed:
        return False
    lines, body = parsed
    token = next((ln.split(":", 1)[1] for ln in lines if ln.startswith(f"{PACKED}:")), None)
    if token is None:
        return False

    values = unpack(parse_scalar(token))
    inverse = {v: k for k, v in FIELDS.items()}
    restored = [
        f"{inverse[k]}: {json.dumps(v, ensure_ascii=False)}"
        for k, v in values.items()
        if k in inverse
    ]
    kept = [ln for ln in lines if not ln.startswith(f"{PACKED}:")]
    write_note(path, restored + kept, body)
    return True


def notes(vault: Path):
    for md in sorted(vault.rglob("*.md")):
        if any(p.startswith(".") for p in md.relative_to(vault).parts):
            continue
        yield md


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=".", help="vault root")
    ap.add_argument("--path", help="limit to a subfolder, e.g. 'Personal/Log'")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--expand", action="store_true", help="restore the original keys")
    ap.add_argument("--decode", help="print the decoded blob for one note and exit")
    args = ap.parse_args()

    vault = Path(args.vault)

    if args.decode:
        parsed = split_note(Path(args.decode))
        if not parsed:
            raise SystemExit("no frontmatter")
        token = next((ln.split(":", 1)[1] for ln in parsed[0] if ln.startswith(f"{PACKED}:")), None)
        if token is None:
            raise SystemExit(f"no {PACKED} key")
        print(json.dumps(unpack(parse_scalar(token)), indent=2, ensure_ascii=False))
        return 0

    root = vault / args.path if args.path else vault
    action = expand if args.expand else compact
    verb = "expand" if args.expand else "compact"

    targets = [p for p in notes(root)]
    if not args.apply:
        sample = targets[0] if targets else None
        print(f"Would {verb} up to {len(targets)} notes under {root}.")
        if sample:
            parsed = split_note(sample)
            if parsed:
                print(f"\nBefore — {sample.relative_to(vault)}:")
                for ln in parsed[0]:
                    print(f"  {ln[:88]}")
        print("\nDry run. Re-run with --apply to write.")
        return 0

    changed = sum(1 for p in targets if action(p))
    print(f"{verb.capitalize()}ed {changed} of {len(targets)} notes.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
