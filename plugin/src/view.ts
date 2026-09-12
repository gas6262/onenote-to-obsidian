import {
  ItemView,
  Menu,
  Notice,
  TAbstractFile,
  TFile,
  TFolder,
  WorkspaceLeaf,
  setIcon,
} from "obsidian";
import { excalidrawAvailable } from "./excalidraw";
import type QuirePlugin from "./main";
import {
  K,
  Node,
  RANK_STEP,
  buildTree,
  folderNoteFor,
  hueForIndex,
  paint,
  rankBetween,
  readOrder,
} from "./model";

export const VIEW_TYPE_QUIRE = "quire-navigator";

const DEFAULT_ICONS: Array<[RegExp, string]> = [
  [/\b(todo|task|deliverable|deliverables)\b/i, "square-check-big"],
  [/\b(ai4us|ai)\b/i, "feather"],
  [/\b(personal|people|social)\b/i, "user"],
  [/\b(career|job|jobs|interview|interviews)\b/i, "briefcase"],
  [/\b(education|school|learning|wharton|gre|udacity|degree)\b/i, "graduation-cap"],
  [/\b(business|company|companies|llc|corp)\b/i, "building-2"],
  [/\b(finance|income|money|passive|tax|taxes)\b/i, "banknote"],
  [/\b(idea|ideas|brainstorm)\b/i, "lightbulb"],
  [/\b(blog|vlog|coding|code|dev)\b/i, "code"],
  [/\b(travel|trip|trips)\b/i, "plane"],
  [/\b(real estate|renting|rental|home|house|property)\b/i, "home"],
  [/\b(health|fitness|gym|workout)\b/i, "heart-pulse"],
  [/\b(excalidraw|drawing|design)\b/i, "pencil"],
  [/\b(food|recipe|recipes|sensation|kitchen)\b/i, "utensils"],
  [/\b(music|band|song|songs)\b/i, "music"],
  [/\b(external|research|vendor|vendors)\b/i, "globe"],
  [/\b(general|misc|other)\b/i, "shapes"],
  [/\b(log|journal|diary)\b/i, "notebook-pen"],
  [/\b(goal|goals|target)\b/i, "target"],
];

function defaultIconFor(name: string): string {
  for (const [re, icon] of DEFAULT_ICONS) if (re.test(name)) return icon;
  return "folder";
}

export class QuireView extends ItemView {
  navigation = false;
  icon = "gallery-vertical-end";

  private collapsed = new Set<string>();
  private dragged: Node | null = null;
  private renderQueued = false;

  constructor(leaf: WorkspaceLeaf, private plugin: QuirePlugin) {
    super(leaf);
  }

  getViewType(): string {
    return VIEW_TYPE_QUIRE;
  }
  getDisplayText(): string {
    return "Quire";
  }

  async onOpen(): Promise<void> {
    this.contentEl.addClass("quire");
    this.collapsed = new Set(this.plugin.settings.collapsed);

    const refresh = () => this.scheduleRender();
    this.registerEvent(this.app.vault.on("create", refresh));
    this.registerEvent(this.app.vault.on("delete", refresh));
    this.registerEvent(this.app.vault.on("rename", refresh));
    this.registerEvent(this.app.metadataCache.on("changed", refresh));
    this.registerEvent(this.app.workspace.on("css-change", refresh));
    this.registerEvent(
      this.app.workspace.on("file-open", () => this.revealActive()),
    );

    this.render();
  }

  async onClose(): Promise<void> {
    this.contentEl.empty();
  }

  scheduleRender(): void {
    if (this.renderQueued) return;
    this.renderQueued = true;
    window.setTimeout(() => {
      this.renderQueued = false;
      this.render();
    }, 60);
  }

  /* ------------------------------------------------------------ rendering */

  private isDark(): boolean {
    return document.body.hasClass("theme-dark");
  }

  render(): void {
    const el = this.contentEl;
    el.empty();

    const bar = el.createDiv({ cls: "quire-toolbar" });
    const newBtn = bar.createEl("button", { cls: "quire-tool", attr: { "aria-label": "New note" } });
    setIcon(newBtn, "file-plus");
    newBtn.onclick = () => void this.plugin.createNote(this.activeFolder());

    const newFolderBtn = bar.createEl("button", {
      cls: "quire-tool",
      attr: { "aria-label": "New folder" },
    });
    setIcon(newFolderBtn, "folder-plus");
    newFolderBtn.onclick = () => void this.plugin.createFolder(this.activeFolder());

    if (excalidrawAvailable(this.app)) {
      const drawBtn = bar.createEl("button", {
        cls: "quire-tool",
        attr: { "aria-label": "New drawing here" },
      });
      setIcon(drawBtn, "pencil-line");
      drawBtn.onclick = () => void this.plugin.createDrawing(this.activeFolder());
    }

    const collapseBtn = bar.createEl("button", {
      cls: "quire-tool",
      attr: { "aria-label": "Collapse all" },
    });
    setIcon(collapseBtn, "chevrons-down-up");
    collapseBtn.onclick = () => {
      const all = new Set<string>();
      const walk = (list: Node[]) =>
        list.forEach((n) => {
          if (n.children.length) all.add(n.file.path);
          walk(n.children);
        });
      walk(this.tree());
      this.collapsed = all;
      this.persistCollapsed();
      this.render();
    };

    const list = el.createDiv({ cls: "quire-list" });
    const tree = this.tree();
    const tops = tree.filter((n) => n.kind === "folder" && !n.isUtility);
    paint(tree, this.isDark(), (name) => {
      const stored = this.plugin.settings.hues[name];
      if (typeof stored === "number") return stored;
      const i = tops.findIndex((t) => t.name === name);
      return i >= 0 ? hueForIndex(i, tops.length) : null;
    });

    for (const node of tree) this.renderNode(node, list);
    this.markActive();
  }

  scrollToActive(): void {
    const active = this.app.workspace.getActiveFile();
    if (!active) return;
    const row = this.contentEl.find(`.quire-row[data-path="${CSS.escape(active.path)}"]`);
    row?.scrollIntoView({ block: "center", behavior: "auto" });
  }

  private tree(): Node[] {
    return buildTree(this.app, this.app.vault.getRoot());
  }

  private renderNode(node: Node, parent: HTMLElement): void {
    const row = parent.createDiv({ cls: `quire-row quire-${node.kind}` });
    row.dataset.path = node.file.path;
    row.style.paddingLeft = `${4 + node.depth * 15}px`;
    if (node.isUtility) row.addClass("is-utility");
    if (node.folderDepth === 0 && node.kind === "folder") row.addClass("is-top");

    const hasKids = node.children.length > 0;
    const isCollapsed = this.collapsed.has(node.file.path);

    const chev = row.createSpan({ cls: "quire-chev" });
    if (hasKids) {
      setIcon(chev, isCollapsed ? "chevron-right" : "chevron-down");
      chev.onclick = (e) => {
        e.stopPropagation();
        if (isCollapsed) this.collapsed.delete(node.file.path);
        else this.collapsed.add(node.file.path);
        this.persistCollapsed();
        this.render();
      };
    }

    const isTopFolder = node.kind === "folder" && node.folderDepth === 0 && !node.isUtility;
    if (isTopFolder) {
      row.addClass("is-badge");
      row.style.background = node.colour ?? "";
    }

    const mark = row.createSpan({ cls: "quire-mark" });
    if (node.kind === "folder" && !node.isUtility) {
      const icon = this.plugin.settings.icons[node.file.path];
      if (icon) {
        setIcon(mark, icon);
        mark.addClass("is-icon");
      } else if (node.folderDepth === 0) {
        setIcon(mark, defaultIconFor(node.name));
        mark.addClass("is-icon");
      } else {
        mark.addClass("is-square");
      }
      mark.style.color = isTopFolder ? "#fff" : (node.colour ?? "");
      if (mark.hasClass("is-square")) {
        mark.style.background = isTopFolder ? "#fff" : (node.colour ?? "");
      }
      mark.onclick = (e) => {
        e.stopPropagation();
        this.plugin.pickIcon(node);
      };
    } else if (node.kind === "folder") {
      mark.addClass("is-square");
      mark.style.background = node.colour ?? "";
    }

    if (node.colour && node.depth > 0 && !isTopFolder) {
      const tick = row.createSpan({ cls: "quire-tick" });
      tick.style.background = node.colour;
      tick.style.left = `${node.depth * 15 - 4}px`;
    }

    row.createSpan({ cls: "quire-name", text: node.name });

    // Open: a folder with a folder note opens it; otherwise toggle.
    row.onclick = () => {
      if (node.kind === "note") {
        void this.app.workspace.getLeaf(false).openFile(node.file as TFile);
      } else if (node.folderNote) {
        void this.app.workspace.getLeaf(false).openFile(node.folderNote);
      } else if (hasKids) {
        chev.click();
      }
    };

    this.registerDomEvent(row, "contextmenu", (e) => this.showMenu(e, node));
    this.attachDrag(row, node);

    if (hasKids && !isCollapsed) {
      for (const child of node.children) this.renderNode(child, parent);
    }
  }

  private markActive(): void {
    const active = this.app.workspace.getActiveFile();
    this.contentEl.findAll(".quire-row").forEach((r) => {
      r.toggleClass("is-active", !!active && r.dataset.path === active.path);
    });
  }

  /**
   * Open whatever the active file is sitting inside, then scroll to it.
   *
   * Without this, opening a file in a collapsed folder -- an Excalidraw drawing
   * dropped into its own configured folder, say -- looks exactly like the file
   * not existing. Ancestors include note parents, not just folders, since a
   * subpage can be hidden by its parent note being collapsed.
   */
  revealActive(scroll = true): void {
    const active = this.app.workspace.getActiveFile();
    if (!active) {
      this.markActive();
      return;
    }

    const trail: string[] = [];
    const find = (list: Node[], path: string[]): boolean => {
      for (const n of list) {
        if (n.file.path === active.path) {
          trail.push(...path);
          return true;
        }
        if (find(n.children, [...path, n.file.path])) return true;
      }
      return false;
    };
    if (!find(this.tree(), [])) return;

    const opened = trail.filter((p) => this.collapsed.delete(p)).length;
    if (opened > 0) {
      this.persistCollapsed();
      this.render();
    } else {
      this.markActive();
    }

    if (!scroll) return;
    const row = this.contentEl.find(`.quire-row[data-path="${CSS.escape(active.path)}"]`);
    row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  private activeFolder(): TFolder {
    const f = this.app.workspace.getActiveFile();
    const parent = f?.parent;
    return parent ?? this.app.vault.getRoot();
  }

  private persistCollapsed(): void {
    this.plugin.settings.collapsed = [...this.collapsed];
    void this.plugin.saveSettings();
  }

  /* ---------------------------------------------------------------- drag */

  /**
   * HTML5 drag works from a mouse, a trackpad or an Apple Pencil -- including
   * on iPad. Only a bare finger produces no drag events, and that case falls
   * back to the move/indent commands.
   */
  private attachDrag(row: HTMLElement, node: Node): void {
    row.draggable = true;

    this.registerDomEvent(row, "dragstart", (e) => {
      this.dragged = node;
      row.addClass("is-dragging");
      e.dataTransfer?.setData("text/plain", node.file.path);
      if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
    });

    this.registerDomEvent(row, "dragend", () => {
      this.dragged = null;
      this.contentEl.findAll(".quire-row").forEach((r) => {
        r.removeClasses(["is-dragging", "drop-above", "drop-below", "drop-into"]);
      });
    });

    this.registerDomEvent(row, "dragover", (e) => {
      if (!this.dragged || this.dragged === node) return;
      e.preventDefault();
      row.removeClasses(["drop-above", "drop-below", "drop-into"]);
      row.addClass(this.dropZone(e, row, node));
    });

    this.registerDomEvent(row, "dragleave", () => {
      row.removeClasses(["drop-above", "drop-below", "drop-into"]);
    });

    this.registerDomEvent(row, "drop", (e) => {
      e.preventDefault();
      const zone = this.dropZone(e, row, node);
      const src = this.dragged;
      row.removeClasses(["drop-above", "drop-below", "drop-into"]);
      if (src && src !== node) void this.plugin.applyDrop(src, node, zone);
    });
  }

  private dropZone(
    e: DragEvent,
    row: HTMLElement,
    target: Node,
  ): "drop-above" | "drop-below" | "drop-into" {
    const rect = row.getBoundingClientRect();
    const y = (e.clientY - rect.top) / rect.height;
    const canNest = target.kind === "folder" || target.file instanceof TFile;
    if (canNest && y > 0.3 && y < 0.7) return "drop-into";
    return y < 0.5 ? "drop-above" : "drop-below";
  }

  /* ---------------------------------------------------------------- menu */

  private showMenu(e: MouseEvent, node: Node): void {
    const menu = new Menu();
    const file = node.file;

    menu.addItem((i) =>
      i.setTitle("New note here").setIcon("file-plus").onClick(() => {
        const folder = file instanceof TFolder ? file : (file.parent ?? this.app.vault.getRoot());
        void this.plugin.createNote(folder, node.kind === "note" ? node : undefined);
      }),
    );

    if (excalidrawAvailable(this.app)) {
      menu.addItem((i) =>
        i.setTitle("New drawing here").setIcon("pencil-line").onClick(() => {
          const folder = file instanceof TFolder ? file : (file.parent ?? this.app.vault.getRoot());
          void this.plugin.createDrawing(folder);
        }),
      );
    }

    if (node.kind === "note") {
      menu.addItem((i) =>
        i.setTitle("New subnote").setIcon("corner-down-right").onClick(() => {
          const folder = file.parent ?? this.app.vault.getRoot();
          void this.plugin.createNote(folder, node, true);
        }),
      );
      menu.addSeparator();
      menu.addItem((i) =>
        i.setTitle("Indent").setIcon("indent").onClick(() => void this.plugin.indent(node)),
      );
      menu.addItem((i) =>
        i.setTitle("Outdent").setIcon("outdent").onClick(() => void this.plugin.outdent(node)),
      );
    }

    menu.addItem((i) =>
      i.setTitle("Rename…").setIcon("pencil").onClick(() => this.plugin.promptRename(node)),
    );

    if (node.kind === "folder") {
      menu.addItem((i) =>
        i.setTitle("Change icon…").setIcon("smile").onClick(() => this.plugin.pickIcon(node)),
      );
      if (!node.folderNote) {
        menu.addItem((i) =>
          i.setTitle("Add folder note").setIcon("file-text").onClick(() => {
            void this.plugin.ensureFolderNote(file as TFolder);
          }),
        );
      }
    }

    menu.addSeparator();
    menu.addItem((i) =>
      i.setTitle("Move up").setIcon("arrow-up").onClick(() => void this.plugin.move(node, -1)),
    );
    menu.addItem((i) =>
      i.setTitle("Move down").setIcon("arrow-down").onClick(() => void this.plugin.move(node, 1)),
    );

    menu.addSeparator();
    this.app.workspace.trigger("file-menu", menu, file, VIEW_TYPE_QUIRE, this.leaf);
    menu.showAtMouseEvent(e);
  }

  /* ------------------------------------------------------------ siblings */

  /** Siblings of a node in render order — the basis for every reorder. */
  siblingsOf(node: Node): Node[] {
    const find = (list: Node[], parent: Node[] | null): Node[] | null => {
      if (list.includes(node)) return parent === null ? list : list;
      for (const n of list) {
        const hit = find(n.children, n.children);
        if (hit) return hit;
      }
      return null;
    };
    return find(this.tree(), null) ?? [];
  }

  currentTree(): Node[] {
    return this.tree();
  }

  notice(msg: string): void {
    new Notice(msg);
  }

  orderOf(f: TAbstractFile): number | null {
    return readOrder(this.app, f);
  }

  folderNote(f: TFolder): TFile | undefined {
    return folderNoteFor(f);
  }

  ranks() {
    return { RANK_STEP, rankBetween, K };
  }
}
