#!/usr/bin/env python3
"""Order notes by the dates in their names, newest first.

OneNote's stored `order` turned out not to be chronological in every section --
Daily Deliverables came back as 2024, 2025, 2026 ascending -- so this stops
trusting it and reads the dates directly.

Rules, deliberately conservative:

  * A date is only recognised at the START of a name. "8-22-23 vegas" is dated;
    "Lock was broken today 8-28 at Broadway" is not. Fishing for dates anywhere
    in arbitrary text produces confident nonsense.
  * A name that is exactly a year ("2026") is a year parent. Year parents sort
    newest-first among their siblings.
  * Under a year parent, only month and day matter -- the parent already fixes
    the year. That quietly absorbs typos like "3-23-35" filed under 2025.
  * Undated notes keep their existing relative order and sit ABOVE the dated
    stream, because things like "Goals" and "Weekly Checklist" are reference
    pages, not entries. Pass --undated-last to flip that.
  * A group is only re-sorted when dates actually govern it -- it hangs under a
    year parent, or at least half its members are dated. Everything else keeps
    the order OneNote gave it, which is often deliberate. --all-groups overrides.

Also re-parents a dated note sitting loose at the top of a folder when a year
parent matching its year exists alongside it.

Default is a dry run; pass --apply to write.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
RANK_STEP = 1000

YEAR_ONLY = re.compile(r"^(19|20)\d{2}$")
# M-D-YY, M-D-YYYY, M/D/YY, M.D.YY -- anchored at the start of the name
LEADING_DATE = re.compile(
    r"^(?P<m>\d{1,2})[-/._](?P<d>\d{1,2})(?:[-/._](?P<y>\d{2}|\d{4}))?(?![\d])"
)


def parse_date(name: str) -> tuple[int | None, int | None, int | None] | None:
    """Return (year, month, day); month/day are None for a bare year."""
    s = name.strip()
    if YEAR_ONLY.match(s):
        return int(s), None, None
    m = LEADING_DATE.match(s)
    if not m:
        return None
    mo, dy = int(m.group("m")), int(m.group("d"))
    if not (1 <= mo <= 12 and 1 <= dy <= 31):
        return None
    yr = m.group("y")
    if yr is None:
        return None, mo, dy
    y = int(yr)
    if y < 100:
        y += 2000
    return y, mo, dy


def read_fm(path: Path) -> tuple[list[str], str] | None:
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        return None
    return m.group(1).splitlines(), text[m.end():]


def write_keys(path: Path, updates: dict[str, str]) -> None:
    parsed = read_fm(path)
    if not parsed:
        return
    lines, body = parsed
    kept = [ln for ln in lines if ln.split(":", 1)[0].strip() not in updates]
    kept += [f"{k}: {v}" for k, v in updates.items()]
    path.write_text("---\n" + "\n".join(kept) + "\n---\n" + body, encoding="utf-8")


class Note:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.stem
        parsed = read_fm(path)
        lines = parsed[0] if parsed else []
        self.order = next(
            (int(m.group(1)) for ln in lines if (m := re.match(r"nav_order:\s*(-?\d+)", ln))),
            10**9,
        )
        self.parent = next(
            (m.group(1) for ln in lines
             if (m := re.match(r'nav_parent:\s*"\[\[(.+?)\]\]"', ln))),
            None,
        )
        self.date = parse_date(self.name)

    @property
    def is_year(self) -> bool:
        return bool(self.date and self.date[1] is None)


def date_dominated(members: list["Note"], parent_year: int | None) -> bool:
    """Should this group be re-sorted by date at all?

    OneNote's own order is meaningful in plenty of sections -- an interview
    pipeline, a list of companies -- and blindly date-sorting the whole vault
    destroys it. So a group is only touched when dates clearly govern it:
    it hangs under a year parent, or at least half its members are dated.
    """
    if parent_year is not None:
        return True
    dated = sum(1 for n in members if n.date)
    return dated >= 2 and dated * 2 >= len(members)


def sort_group(notes: list[Note], parent_year: int | None, undated_last: bool) -> list[Note]:
    """Newest first. Undated notes hold their relative order, as a block."""
    dated = [n for n in notes if n.date]
    undated = [n for n in notes if not n.date]

    def key(n: Note):
        y, mo, dy = n.date  # type: ignore[misc]
        # Under a year parent the yy suffix is unreliable; month/day decide.
        year = parent_year if parent_year is not None else (y if y is not None else 0)
        return (year, mo or 0, dy or 0)

    dated.sort(key=key, reverse=True)
    undated.sort(key=lambda n: n.order)
    return dated + undated if undated_last else undated + dated


def process_folder(folder: Path, undated_last: bool, all_groups: bool,
                   plan: list[tuple[Path, dict[str, str]]]) -> list[str]:
    notes = [Note(p) for p in folder.glob("*.md")]
    if not notes:
        return []
    by_name = {n.name: n for n in notes}
    log: list[str] = []

    # Re-parent a loose dated note under a matching year parent.
    years = {n.date[0]: n for n in notes if n.is_year}
    for n in notes:
        if n.parent or n.is_year or not n.date:
            continue
        y = n.date[0]
        if y in years:
            n.parent = years[y].name
            plan.append((n.path, {"nav_parent": f'"[[{years[y].name}]]"'}))
            log.append(f"    re-parent {n.name}  ->  [[{years[y].name}]]")

    groups: dict[str | None, list[Note]] = {}
    for n in notes:
        groups.setdefault(n.parent if n.parent in by_name else None, []).append(n)

    for parent_name, members in groups.items():
        parent = by_name.get(parent_name) if parent_name else None
        parent_year = parent.date[0] if parent and parent.is_year else None
        if not all_groups and not date_dominated(members, parent_year):
            continue  # leave OneNote's ordering alone
        ordered = sort_group(members, parent_year, undated_last)
        for i, n in enumerate(ordered, start=1):
            rank = i * RANK_STEP
            if rank != n.order:
                plan.append((n.path, {"nav_order": str(rank)}))
        if len(ordered) > 1:
            where = f"[[{parent_name}]]" if parent_name else "(top level)"
            log.append(f"    {where}: " + " · ".join(n.name for n in ordered[:6])
                       + (" …" if len(ordered) > 6 else ""))
    return log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=".", help="vault root")
    ap.add_argument("--path", action="append", help="limit to a folder; repeatable")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undated-last", action="store_true")
    ap.add_argument("--all-groups", action="store_true",
                    help="re-sort every group, even ones OneNote ordered deliberately")
    args = ap.parse_args()

    vault = Path(args.vault)
    if args.path:
        folders = [vault / p for p in args.path]
    else:
        folders = [
            d for d in vault.rglob("*")
            if d.is_dir() and not any(x.startswith(".") for x in d.relative_to(vault).parts)
        ]

    plan: list[tuple[Path, dict[str, str]]] = []
    for folder in sorted(folders):
        log = process_folder(folder, args.undated_last, args.all_groups, plan)
        if log:
            print(f"=== {folder.relative_to(vault)}")
            for line in log:
                print(line)

    print(f"\n{len(plan)} frontmatter writes planned.")
    if not args.apply:
        print("Dry run. Re-run with --apply to write.")
        return 0

    merged: dict[Path, dict[str, str]] = {}
    for path, updates in plan:
        merged.setdefault(path, {}).update(updates)
    for path, updates in merged.items():
        write_keys(path, updates)
    print(f"Wrote {len(merged)} notes.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
