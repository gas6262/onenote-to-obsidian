import {
  App,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  TFile,
  TFolder,
  WorkspaceLeaf,
  normalizePath,
  setIcon,
} from "obsidian";
import { createDrawingIn, excalidrawAvailable } from "./excalidraw";
import { K, Node, RANK_STEP, buildTree, folderNoteFor, rankBetween } from "./model";
import { QuireView, VIEW_TYPE_QUIRE } from "./view";

interface QuireSettings {
  hues: Record<string, number>;
  icons: Record<string, string>;
  collapsed: string[];
  newNotesAtTop: boolean;
  debugLog: boolean;
  telemetryUrl: string;
}

const DEFAULTS: QuireSettings = {
  hues: {},
  icons: {},
  collapsed: [],
  newNotesAtTop: true,
  debugLog: false,
  telemetryUrl: "",
};

/** A deliberately small, hand-picked set — enough to label a vault, few enough to scan. */
const ICON_CHOICES = [
  "folder", "notebook-pen", "square-check-big", "target", "calendar-days", "clock",
  "user", "users", "briefcase", "building-2", "graduation-cap", "book-open",
  "banknote", "trending-up", "shopping-cart", "receipt", "home", "key",
  "lightbulb", "feather", "sparkles", "flame", "star", "heart",
  "code", "terminal", "globe", "plane", "car", "map-pin",
  "music", "camera", "utensils", "dumbbell", "heart-pulse", "leaf",
  "wrench", "shield", "trophy", "flask-conical", "palette", "archive",
];

export default class QuirePlugin extends Plugin {
  settings: QuireSettings = { ...DEFAULTS };

  async onload(): Promise<void> {
    await this.loadSettings();

    this.registerView(VIEW_TYPE_QUIRE, (leaf) => new QuireView(leaf, this));

    this.addRibbonIcon("gallery-vertical-end", "Open Quire", () => void this.activate());

    this.addCommand({
      id: "open",
      name: "Open Quire",
      callback: () => void this.activate(),
    });
    this.addCommand({
      id: "reveal",
      name: "Reveal active file in Quire",
      callback: () => {
        void this.activate().then(() => {
          this.views().forEach((v) => v.revealActive());
        });
      },
    });
    this.addCommand({
      id: "new-note",
      name: "New note in current folder",
      callback: () => void this.createNote(this.currentFolder()),
    });
    this.addCommand({
      id: "new-drawing",
      name: "New drawing in current folder",
      checkCallback: (checking) => {
        if (!excalidrawAvailable(this.app)) return false;
        if (!checking) void this.createDrawing(this.currentFolder());
        return true;
      },
    });
    this.addCommand({
      id: "move-up",
      name: "Move note up",
      callback: () => void this.moveActive(-1),
    });
    this.addCommand({
      id: "move-down",
      name: "Move note down",
      callback: () => void this.moveActive(1),
    });
    this.addCommand({
      id: "indent",
      name: "Indent note (make subpage)",
      callback: () => void this.indentActive(),
    });
    this.addCommand({
      id: "outdent",
      name: "Outdent note (promote)",
      callback: () => void this.outdentActive(),
    });

    this.addSettingTab(new QuireSettingTab(this.app, this));

    this.app.workspace.onLayoutReady(() => {
      if (this.app.workspace.getLeavesOfType(VIEW_TYPE_QUIRE).length === 0) void this.activate();
    });
  }

  onUserEnable(): void {
    void this.activate();
  }

  /** Sync may deliver data.json late; never overwrite it with defaults. */
  async onExternalSettingsChange(): Promise<void> {
    const data = await this.loadData();
    if (data) {
      this.settings = { ...DEFAULTS, ...data };
      this.refresh();
    }
  }

  async activate(): Promise<WorkspaceLeaf | null> {
    const { workspace } = this.app;
    const existing = workspace.getLeavesOfType(VIEW_TYPE_QUIRE);
    if (existing.length && existing[0]) {
      await workspace.revealLeaf(existing[0]);
      return existing[0];
    }
    const leaf = workspace.getLeftLeaf(false);
    if (!leaf) return null;
    await leaf.setViewState({ type: VIEW_TYPE_QUIRE, active: true });
    await workspace.revealLeaf(leaf);
    return leaf;
  }

  async loadSettings(): Promise<void> {
    const data = await this.loadData();
    this.settings = { ...DEFAULTS, ...(data ?? {}) };
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  views(): QuireView[] {
    return this.app.workspace
      .getLeavesOfType(VIEW_TYPE_QUIRE)
      .map((l) => l.view)
      .filter((v): v is QuireView => v instanceof QuireView);
  }

  refresh(): void {
    this.views().forEach((v) => v.scheduleRender());
  }

  private currentFolder(): TFolder {
    return this.app.workspace.getActiveFile()?.parent ?? this.app.vault.getRoot();
  }

  private tree(): Node[] {
    return buildTree(this.app, this.app.vault.getRoot());
  }

  /* ------------------------------------------------------- frontmatter io */

  private async setKeys(file: TFile, values: Record<string, unknown>): Promise<void> {
    if (file.extension !== "md") return;
    try {
      await this.app.fileManager.processFrontMatter(file, (fm) => {
        for (const [k, v] of Object.entries(values)) {
          if (v === null) delete fm[k];
          else fm[k] = v;
        }
      });
    } catch (e) {
      await this.log(`setKeys failed on ${file.path}: ${String(e)}`);
    }
  }

  /** The writable carrier for a node's own order. Folders use their folder note. */
  private async carrierFor(node: Node): Promise<TFile | null> {
    if (node.kind === "note") return node.file instanceof TFile ? node.file : null;
    const folder = node.file as TFolder;
    return folderNoteFor(folder) ?? (await this.ensureFolderNote(folder));
  }

  /** Created lazily — only when a folder actually needs somewhere to store order. */
  async ensureFolderNote(folder: TFolder): Promise<TFile | null> {
    const existing = folderNoteFor(folder);
    if (existing) return existing;
    const path = normalizePath(`${folder.path}/${folder.name}.md`);
    try {
      const file = await this.app.vault.create(path, `# ${folder.name}\n`);
      this.refresh();
      return file;
    } catch (e) {
      new Notice(`Could not create folder note: ${String(e)}`);
      return null;
    }
  }

  /* ------------------------------------------------------------ reorder */

  private findNode(path: string, list: Node[] = this.tree()): Node | null {
    for (const n of list) {
      if (n.file.path === path) return n;
      const hit = this.findNode(path, n.children);
      if (hit) return hit;
    }
    return null;
  }

  private parentListOf(node: Node, list: Node[] = this.tree()): Node[] {
    if (list.some((n) => n.file.path === node.file.path)) return list;
    for (const n of list) {
      const hit = this.parentListOf(node, n.children);
      if (hit.length) return hit;
    }
    return [];
  }

  /** Assign a rank between two siblings, compacting the group if ranks collide. */
  private async placeBetween(node: Node, before: Node | null, after: Node | null): Promise<void> {
    const carrier = await this.carrierFor(node);
    if (!carrier) return;

    let rank = rankBetween(before?.order ?? null, after?.order ?? null);
    if (Number.isNaN(rank)) {
      const sibs = this.parentListOf(node).filter((n) => n.kind === node.kind);
      let i = 1;
      for (const s of sibs) {
        const c = await this.carrierFor(s);
        if (c) await this.setKeys(c, { [K.order]: i * RANK_STEP });
        i++;
      }
      rank = rankBetween(before ? before.order : null, after ? after.order : null);
      if (Number.isNaN(rank)) rank = (before?.order ?? 0) + RANK_STEP / 2;
    }
    await this.setKeys(carrier, { [K.order]: rank });
    this.refresh();
  }

  async move(node: Node, delta: number): Promise<void> {
    const sibs = this.parentListOf(node).filter((n) => n.kind === node.kind);
    const idx = sibs.findIndex((n) => n.file.path === node.file.path);
    const to = idx + delta;
    if (idx < 0 || to < 0 || to >= sibs.length) return;

    const before = delta < 0 ? (sibs[to - 1] ?? null) : (sibs[to] ?? null);
    const after = delta < 0 ? (sibs[to] ?? null) : (sibs[to + 1] ?? null);
    await this.placeBetween(node, before, after);
  }

  private async moveActive(delta: number): Promise<void> {
    const f = this.app.workspace.getActiveFile();
    const node = f ? this.findNode(f.path) : null;
    if (node) await this.move(node, delta);
  }

  /** Indent = become a subpage of the sibling immediately above. */
  async indent(node: Node): Promise<void> {
    if (node.kind !== "note" || !(node.file instanceof TFile)) return;
    const sibs = this.parentListOf(node).filter((n) => n.kind === "note");
    const idx = sibs.findIndex((n) => n.file.path === node.file.path);
    const above = idx > 0 ? sibs[idx - 1] : null;
    if (!above) {
      new Notice("Nothing above to nest under");
      return;
    }
    const last = above.children.at(-1);
    await this.setKeys(node.file, {
      [K.parent]: `[[${above.name}]]`,
      [K.order]: (last?.order ?? 0) + RANK_STEP,
    });
    this.refresh();
  }

  /** Outdent = become a sibling of the current parent, placed just after it. */
  async outdent(node: Node): Promise<void> {
    if (node.kind !== "note" || !(node.file instanceof TFile)) return;
    const parentPath = this.parentOf(node);
    if (!parentPath) {
      new Notice("Already at the top level");
      return;
    }
    const parent = this.findNode(parentPath);
    if (!parent) return;
    const grandSibs = this.parentListOf(parent).filter((n) => n.kind === "note");
    const pIdx = grandSibs.findIndex((n) => n.file.path === parent.file.path);
    const next = pIdx >= 0 ? (grandSibs[pIdx + 1] ?? null) : null;

    const grandParent = this.parentOf(parent);
    await this.setKeys(node.file, {
      [K.parent]: grandParent ? `[[${this.findNode(grandParent)?.name ?? ""}]]` : null,
      [K.order]: rankBetweenSafe(parent.order, next?.order ?? null),
    });
    this.refresh();
  }

  private parentOf(node: Node, list: Node[] = this.tree()): string | null {
    for (const n of list) {
      if (n.children.some((c) => c.file.path === node.file.path)) return n.file.path;
      const hit = this.parentOf(node, n.children);
      if (hit) return hit;
    }
    return null;
  }

  private async indentActive(): Promise<void> {
    const f = this.app.workspace.getActiveFile();
    const node = f ? this.findNode(f.path) : null;
    if (node) await this.indent(node);
  }

  private async outdentActive(): Promise<void> {
    const f = this.app.workspace.getActiveFile();
    const node = f ? this.findNode(f.path) : null;
    if (node) await this.outdent(node);
  }

  /* ---------------------------------------------------------------- drop */

  async applyDrop(src: Node, target: Node, zone: string): Promise<void> {
    const srcFile = src.file;

    if (zone === "drop-into") {
      if (target.kind === "folder" && srcFile instanceof TFile) {
        const dest = normalizePath(`${(target.file as TFolder).path}/${srcFile.name}`);
        await this.app.fileManager.renameFile(srcFile, dest);
        this.refresh();
        return;
      }
      if (target.kind === "note" && srcFile instanceof TFile) {
        // Nest as a subpage. Move folders first if they differ.
        if (srcFile.parent?.path !== target.file.parent?.path) {
          const dest = normalizePath(`${target.file.parent?.path ?? ""}/${srcFile.name}`);
          await this.app.fileManager.renameFile(srcFile, dest);
        }
        const last = target.children.at(-1);
        await this.setKeys(srcFile, {
          [K.parent]: `[[${target.name}]]`,
          [K.order]: (last?.order ?? 0) + RANK_STEP,
        });
        this.refresh();
        return;
      }
    }

    // Reorder beside the target, adopting the target's parent.
    const sibs = this.parentListOf(target).filter((n) => n.kind === src.kind);
    const idx = sibs.findIndex((n) => n.file.path === target.file.path);
    const before = zone === "drop-above" ? (sibs[idx - 1] ?? null) : target;
    const after = zone === "drop-above" ? target : (sibs[idx + 1] ?? null);

    if (src.kind === "note" && srcFile instanceof TFile) {
      const targetParent = this.parentOf(target);
      const targetParentNode = targetParent ? this.findNode(targetParent) : null;
      if (srcFile.parent?.path !== target.file.parent?.path) {
        const dest = normalizePath(`${target.file.parent?.path ?? ""}/${srcFile.name}`);
        await this.app.fileManager.renameFile(srcFile, dest);
      }
      await this.setKeys(srcFile, {
        [K.parent]:
          targetParentNode && targetParentNode.kind === "note"
            ? `[[${targetParentNode.name}]]`
            : null,
      });
    }
    await this.placeBetween(src, before, after);
  }

  /* -------------------------------------------------------------- create */

  async createNote(folder: TFolder, sibling?: Node, asChild = false): Promise<void> {
    let base = "Untitled";
    let n = 0;
    while (this.app.vault.getAbstractFileByPath(normalizePath(`${folder.path}/${base}.md`))) {
      base = `Untitled ${++n}`;
    }
    const path = normalizePath(`${folder.path}/${base}.md`);
    const file = await this.app.vault.create(path, "");

    // New notes land at the top of their group, per OneNote-style chronology.
    const tree = this.tree();
    const scope = sibling
      ? asChild
        ? sibling.children
        : this.parentListOf(sibling, tree)
      : (this.findNode(folder.path, tree)?.children ?? tree);
    // Only real ranks count -- unranked siblings are new files floating at the
    // top and must not drag the new note's rank into nonsense.
    const ranks = scope
      .filter((x) => x.kind === "note")
      .map((x) => x.order)
      .filter((o): o is number => typeof o === "number");
    const topRank = ranks.length ? Math.min(...ranks) : RANK_STEP * 2;

    const values: Record<string, unknown> = {
      [K.order]: this.settings.newNotesAtTop ? topRank - RANK_STEP : topRank + RANK_STEP,
    };
    if (asChild && sibling) values[K.parent] = `[[${sibling.name}]]`;
    await this.setKeys(file, values);

    this.refresh();
    await this.app.workspace.getLeaf(false).openFile(file);
    this.views().forEach((v) => v.revealActive());
  }

  /** Excalidraw files every drawing under its own folder; this overrides that. */
  async createDrawing(folder: TFolder): Promise<void> {
    try {
      const file = await createDrawingIn(this.app, folder);
      if (!file) {
        new Notice("Could not create a drawing here — is Excalidraw enabled?");
        return;
      }
      this.refresh();
      if (this.app.workspace.getActiveFile()?.path !== file.path) {
        await this.app.workspace.getLeaf(false).openFile(file);
      }
      this.views().forEach((v) => v.revealActive());
    } catch (e) {
      new Notice(`Drawing failed: ${String(e)}`);
      await this.log(`createDrawing failed in ${folder.path}: ${String(e)}`);
    }
  }

  async createFolder(parent: TFolder): Promise<void> {
    let base = "New folder";
    let n = 0;
    while (this.app.vault.getAbstractFileByPath(normalizePath(`${parent.path}/${base}`))) {
      base = `New folder ${++n}`;
    }
    await this.app.vault.createFolder(normalizePath(`${parent.path}/${base}`));
    this.refresh();
  }

  /* ---------------------------------------------------------------- icon */

  pickIcon(node: Node): void {
    new IconModal(this.app, node.name, ICON_CHOICES, async (icon) => {
      if (icon === null) delete this.settings.icons[node.file.path];
      else this.settings.icons[node.file.path] = icon;
      await this.saveSettings();
      this.refresh();
    }).open();
  }

  promptRename(node: Node): void {
    new RenameModal(this.app, node.name, async (next) => {
      const trimmed = next.trim();
      if (!trimmed || trimmed === node.name) return;
      const file = node.file;
      const dir = file.parent?.path ?? "";
      const suffix = file instanceof TFile ? `.${file.extension}` : "";
      const dest = normalizePath(`${dir ? dir + "/" : ""}${trimmed}${suffix}`);
      try {
        // renameFile updates [[wikilinks]] across the vault, which is what keeps
        // nav_parent pointers valid when a parent note is renamed.
        await this.app.fileManager.renameFile(file, dest);
        const icon = this.settings.icons[file.path];
        if (icon) {
          delete this.settings.icons[file.path];
          this.settings.icons[dest] = icon;
        }
        if (node.kind === "folder" && this.settings.hues[node.name] !== undefined) {
          this.settings.hues[trimmed] = this.settings.hues[node.name] as number;
          delete this.settings.hues[node.name];
        }
        await this.saveSettings();
        this.refresh();
      } catch (e) {
        new Notice(`Rename failed: ${String(e)}`);
      }
    }).open();
  }

  /* --------------------------------------------------------------- debug */

  async log(msg: string): Promise<void> {
    if (!this.settings.debugLog) return;
    const line = `- ${new Date().toISOString()} ${msg}\n`;
    const path = normalizePath("_quire/debug.md");
    try {
      const existing = this.app.vault.getAbstractFileByPath(path);
      if (existing instanceof TFile) {
        await this.app.vault.append(existing, line);
      } else {
        if (!this.app.vault.getAbstractFileByPath("_quire")) {
          await this.app.vault.createFolder("_quire");
        }
        await this.app.vault.create(path, `# Quire debug\n\n${line}`);
      }
    } catch {
      /* logging must never break the plugin */
    }
  }
}

function rankBetweenSafe(before: number | null, after: number | null): number {
  const r = rankBetween(before, after);
  return Number.isNaN(r) ? (before ?? 0) + RANK_STEP / 2 : r;
}

/** A grid of real icons — you pick by sight, not by guessing a Lucide name. */
class IconModal extends Modal {
  constructor(
    app: App,
    private label: string,
    private choices: string[],
    private onPick: (icon: string | null) => void,
  ) {
    super(app);
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.addClass("quire-icon-modal");
    contentEl.createEl("h3", { text: `Icon for ${this.label}` });

    const grid = contentEl.createDiv({ cls: "quire-icon-grid" });
    for (const name of this.choices) {
      const cell = grid.createEl("button", { cls: "quire-icon-cell", attr: { "aria-label": name } });
      setIcon(cell, name);
      cell.onclick = () => {
        this.onPick(name);
        this.close();
      };
    }

    const foot = contentEl.createDiv({ cls: "quire-icon-foot" });
    const clear = foot.createEl("button", { text: "Use a coloured square" });
    clear.onclick = () => {
      this.onPick(null);
      this.close();
    };
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

class RenameModal extends Modal {
  constructor(app: App, private initial: string, private onSubmit: (v: string) => void) {
    super(app);
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.createEl("h3", { text: "Rename" });
    const input = contentEl.createEl("input", { type: "text", value: this.initial });
    input.style.width = "100%";
    input.focus();
    input.select();

    const commit = () => {
      this.onSubmit(input.value);
      this.close();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commit();
      if (e.key === "Escape") this.close();
    });

    const foot = contentEl.createDiv({ cls: "quire-icon-foot" });
    const ok = foot.createEl("button", { text: "Rename", cls: "mod-cta" });
    ok.onclick = commit;
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

class QuireSettingTab extends PluginSettingTab {
  constructor(app: App, private plugin: QuirePlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    new Setting(containerEl)
      .setName("New notes at the top")
      .setDesc("Place a new note above its siblings rather than below.")
      .addToggle((t) =>
        t.setValue(this.plugin.settings.newNotesAtTop).onChange(async (v) => {
          this.plugin.settings.newNotesAtTop = v;
          await this.plugin.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName("Write a debug log")
      .setDesc("Append diagnostics to _quire/debug.md, which syncs to your other devices.")
      .addToggle((t) =>
        t.setValue(this.plugin.settings.debugLog).onChange(async (v) => {
          this.plugin.settings.debugLog = v;
          await this.plugin.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName("Telemetry endpoint")
      .setDesc("Optional. Leave empty to send nothing anywhere.")
      .addText((t) =>
        t
          .setPlaceholder("https://…")
          .setValue(this.plugin.settings.telemetryUrl)
          .onChange(async (v) => {
            this.plugin.settings.telemetryUrl = v.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Reset folder colours")
      .setDesc("Clear saved hues so they are reassigned by position.")
      .addButton((b) =>
        b.setButtonText("Reset").onClick(async () => {
          this.plugin.settings.hues = {};
          await this.plugin.saveSettings();
          this.plugin.refresh();
          new Notice("Folder colours reset");
        }),
      );
  }
}
