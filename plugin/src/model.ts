import { App, TAbstractFile, TFile, TFolder } from "obsidian";

export const RANK_STEP = 1000;
export const K = {
  order: "nav_order",
  parent: "nav_parent",
  hue: "nav_hue",
  icon: "nav_icon",
} as const;

export type NodeKind = "folder" | "note";

export interface Node {
  kind: NodeKind;
  file: TAbstractFile;
  name: string;
  /** Nesting depth within the sidebar tree, counting folders and notes alike. */
  depth: number;
  /** Depth counted in folders only; notes inherit their folder's value. */
  folderDepth: number;
  order: number | null;
  mtime: number;
  children: Node[];
  /** Folders only: the note that represents the folder, if one exists. */
  folderNote?: TFile;
  colour?: string;
  icon?: string;
  isUtility: boolean;
}

const isUtility = (name: string) => name.startsWith("_");

function frontmatter(app: App, f: TAbstractFile): Record<string, unknown> | undefined {
  return f instanceof TFile ? app.metadataCache.getFileCache(f)?.frontmatter : undefined;
}

/**
 * A file with no nav_order is not "last" -- it is new. Anything created by
 * another plugin (an Excalidraw drawing, a canvas) arrives unranked, and
 * burying it at the bottom of the folder is how you lose track of it. Unranked
 * files sort to the TOP, newest first, and stay there until you drag one --
 * which is the first moment a real rank gets written.
 */
export function readOrder(app: App, f: TAbstractFile): number | null {
  const v = frontmatter(app, f)?.[K.order];
  return typeof v === "number" ? v : null;
}

function mtimeOf(f: TAbstractFile): number {
  return f instanceof TFile ? f.stat.mtime : 0;
}

/** Strip [[ ]] so a parent pointer survives Obsidian's rename refactor. */
export function readParent(app: App, f: TAbstractFile): string | null {
  const v = frontmatter(app, f)?.[K.parent];
  if (typeof v !== "string") return null;
  const m = v.match(/^\[\[(.+?)(?:\|.*)?\]\]$/);
  const target = (m?.[1] ?? v).trim();
  // A parent may be written as a path; we match on basename.
  const base = target.split("/").pop() ?? target;
  return base.length ? base : null;
}

export function folderNoteFor(folder: TFolder): TFile | undefined {
  return folder.children.find(
    (c): c is TFile => c instanceof TFile && c.extension === "md" && c.basename === folder.name,
  );
}

function orderOfFolder(app: App, folder: TFolder): number | null {
  const fn = folderNoteFor(folder);
  return fn ? readOrder(app, fn) : null;
}

interface Sortable {
  order: number | null;
  name: string;
  mtime: number;
}

function bySortKey(a: Sortable, b: Sortable): number {
  const au = a.order === null;
  const bu = b.order === null;
  if (au !== bu) return au ? -1 : 1;            // unranked first
  if (au && bu) return b.mtime - a.mtime;       // newest first among them
  if (a.order !== b.order) return (a.order as number) - (b.order as number);
  return a.name.localeCompare(b.name, undefined, { numeric: true });
}

/**
 * Assemble notes in one folder into a forest using nav_parent pointers.
 * Cycles and dangling parents fall back to the folder root rather than hanging.
 */
function buildNoteForest(app: App, folder: TFolder, depth: number, folderDepth: number): Node[] {
  const folderNote = folderNoteFor(folder);
  const notes = folder.children.filter(
    (c): c is TFile => c instanceof TFile && c !== folderNote,
  );

  const byName = new Map<string, TFile>();
  for (const n of notes) byName.set(n.basename, n);

  const parentOf = new Map<TFile, TFile | null>();
  for (const n of notes) {
    const wanted = n.extension === "md" ? readParent(app, n) : null;
    const p = wanted ? byName.get(wanted) ?? null : null;
    parentOf.set(n, p && p !== n ? p : null);
  }

  // Break cycles: walk up; if we revisit, detach.
  for (const n of notes) {
    const seen = new Set<TFile>([n]);
    let cur = parentOf.get(n) ?? null;
    while (cur) {
      if (seen.has(cur)) {
        parentOf.set(n, null);
        break;
      }
      seen.add(cur);
      cur = parentOf.get(cur) ?? null;
    }
  }

  const nodeOf = new Map<TFile, Node>();
  for (const n of notes) {
    nodeOf.set(n, {
      kind: "note",
      file: n,
      name: n.basename,
      depth: 0,
      folderDepth,
      order: readOrder(app, n),
      mtime: n.stat.mtime,
      children: [],
      isUtility: false,
    });
  }

  const roots: Node[] = [];
  for (const n of notes) {
    const node = nodeOf.get(n)!;
    const p = parentOf.get(n) ?? null;
    if (p) nodeOf.get(p)!.children.push(node);
    else roots.push(node);
  }

  const assignDepth = (list: Node[], d: number) => {
    list.sort(bySortKey);
    for (const node of list) {
      node.depth = d;
      assignDepth(node.children, d + 1);
    }
  };
  assignDepth(roots, depth);
  return roots;
}

export function buildTree(app: App, root: TFolder): Node[] {
  const walk = (folder: TFolder, depth: number, folderDepth: number): Node[] => {
    const subfolders = folder.children.filter((c): c is TFolder => c instanceof TFolder);

    const folderNodes: Node[] = subfolders.map((sf) => ({
      kind: "folder" as const,
      file: sf,
      name: sf.name,
      depth,
      folderDepth,
      order: orderOfFolder(app, sf),
      mtime: folderNoteFor(sf)?.stat.mtime ?? 0,
      children: walk(sf, depth + 1, folderDepth + 1),
      folderNote: folderNoteFor(sf),
      isUtility: isUtility(sf.name),
    }));

    // Utility folders (_attachments and friends) always sink to the bottom.
    folderNodes.sort((a, b) => {
      if (a.isUtility !== b.isUtility) return a.isUtility ? 1 : -1;
      return bySortKey(a, b);
    });

    return [...folderNodes, ...buildNoteForest(app, folder, depth, folderDepth)];
  };
  return walk(root, 0, 0);
}

/* ------------------------------------------------------------------ colour */

const NEUTRAL_L = "oklch(0.66 0.012 250)";
const NEUTRAL_D = "oklch(0.58 0.012 250)";

/**
 * Five basic hues on rotation rather than a unique hue per folder. Repetition
 * is fine -- depth is carried by lightness, not hue, so a dark badge always
 * reads as top level and a pale chip always reads as nested even when the two
 * land on the same colour.
 */
export const BASE_HUES = [150, 225, 295, 25, 350];

export function hueForIndex(i: number, _n: number): number {
  return BASE_HUES[i % BASE_HUES.length] as number;
}

/** Top level: dark and saturated, painted as a filled badge with white text. */
export function topColour(hue: number | null, dark: boolean): string {
  if (hue === null) return dark ? NEUTRAL_D : NEUTRAL_L;
  return `oklch(${dark ? 0.55 : 0.5} ${dark ? 0.15 : 0.16} ${hue})`;
}

/**
 * Subfolders fan out across a wide +/-60 degree arc from the parent hue and sit
 * far lighter. The arc is deliberately wide enough to cross into neighbouring
 * families -- siblings being told apart matters more than the wedge staying pure.
 */
export function childColour(
  hue: number | null,
  index: number,
  count: number,
  dark: boolean,
): string {
  if (hue === null) return dark ? NEUTRAL_D : NEUTRAL_L;
  const spread = count > 1 ? index / (count - 1) - 0.5 : 0;
  const h = (hue + spread * 120 + 360) % 360;
  return `oklch(${dark ? 0.74 : 0.78} ${dark ? 0.13 : 0.14} ${h.toFixed(1)})`;
}

/** Paint hue down the tree, stopping the cascade after level 2. */
export function paint(nodes: Node[], dark: boolean, hueOf: (name: string) => number | null): void {
  nodes.forEach((node) => {
    if (node.kind !== "folder") return;
    const hue = node.isUtility ? null : hueOf(node.name);
    node.colour = topColour(hue, dark);

    const kids = node.children.filter((c) => c.kind === "folder");
    kids.forEach((kid, i) => {
      const c = kid.isUtility
        ? topColour(null, dark)
        : childColour(hue, i, kids.length, dark);
      kid.colour = c;
      const cascade = (list: Node[]) =>
        list.forEach((d) => {
          d.colour = c;
          cascade(d.children);
        });
      cascade(kid.children);
    });

    node.children
      .filter((c) => c.kind === "note")
      .forEach((n) => {
        const cascade = (list: Node[]) =>
          list.forEach((d) => {
            d.colour = node.colour;
            cascade(d.children);
          });
        n.colour = node.colour;
        cascade(n.children);
      });
  });
}

/* ------------------------------------------------------------------ ranking */

/** Rank that places a node between two neighbours, or below/above the edge. */
/** Real ranks are always >= 1 -- an unranked neighbour contributes nothing. */
export function realRank(order: number | null): number | null {
  return typeof order === "number" ? order : null;
}

export function rankBetween(before: number | null, after: number | null): number {
  if (before === null && after === null) return RANK_STEP;
  if (before === null) return (after as number) - RANK_STEP;
  if (after === null) return before + RANK_STEP;
  const mid = Math.floor((before + after) / 2);
  return mid === before || mid === after ? NaN : mid; // NaN signals "compact needed"
}
