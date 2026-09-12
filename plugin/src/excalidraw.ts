import { App, TFile, TFolder } from "obsidian";

/**
 * Excalidraw always files new drawings under its own configured folder, so a
 * drawing never lands beside the note you were writing. Its ExcalidrawAutomate
 * scripting API takes a `foldername` that overrides exactly that:
 *
 *   async create(params?: { filename?, foldername?, templatePath?,
 *                           onNewPane?, silent?, frontmatterKeys? }): Promise<string>
 *
 * Reaching the instance means going through `app.plugins`, which is not part of
 * the public Obsidian typings -- so every hop is probed rather than cast, and a
 * missing or changed API degrades to a notice instead of throwing.
 */

const EXCALIDRAW_ID = "obsidian-excalidraw-plugin";

interface ExcalidrawAutomate {
  reset?: () => void;
  create: (params: {
    filename?: string;
    foldername?: string;
    templatePath?: string;
    onNewPane?: boolean;
    silent?: boolean;
  }) => Promise<string>;
}

function hasKey<K extends string>(v: unknown, key: K): v is Record<K, unknown> {
  return typeof v === "object" && v !== null && Reflect.has(v, key);
}

export function getExcalidrawAutomate(app: App): ExcalidrawAutomate | null {
  if (!hasKey(app, "plugins")) return null;
  const plugins = app.plugins;
  if (!hasKey(plugins, "plugins")) return null;
  const registry = plugins.plugins;
  if (!hasKey(registry, EXCALIDRAW_ID)) return null;
  const plugin = registry[EXCALIDRAW_ID];
  if (!hasKey(plugin, "ea")) return null;
  const ea = plugin.ea;
  if (!hasKey(ea, "create") || typeof (ea as { create: unknown }).create !== "function") {
    return null;
  }
  return ea as unknown as ExcalidrawAutomate;
}

export function excalidrawAvailable(app: App): boolean {
  return getExcalidrawAutomate(app) !== null;
}

/** Create a drawing in `folder` rather than Excalidraw's configured folder. */
export async function createDrawingIn(app: App, folder: TFolder): Promise<TFile | null> {
  const ea = getExcalidrawAutomate(app);
  if (!ea) return null;

  ea.reset?.();
  // Omitting `filename` keeps the user's own "Drawing <date>" naming convention.
  const created = await ea.create({ foldername: folder.path, onNewPane: false });
  if (typeof created !== "string") return null;

  const path = created.endsWith(".md") ? created : `${created}.md`;
  const file = app.vault.getAbstractFileByPath(path);
  return file instanceof TFile ? file : null;
}
