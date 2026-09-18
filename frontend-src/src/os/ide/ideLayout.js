/**
 * IDE-internal spatial layout — constraint based, shared state for all viewports.
 */

export const IDE_ACTIVITIES = {
  explorer: { id: "explorer", label: "Explorer", minWidth: 180 },
  search: { id: "search", label: "Search", minWidth: 200 },
  scm: { id: "scm", label: "Source Control", minWidth: 200 },
  problems: { id: "problems", label: "Problems", minWidth: 200 },
  run: { id: "run", label: "Run", minWidth: 200 },
};

export const IDE_BOTTOM = {
  terminal: { id: "terminal", label: "Terminal", minHeight: 120 },
  problems: { id: "problems", label: "Problems", minHeight: 100 },
  output: { id: "output", label: "Output", minHeight: 100 },
};

/** Default IDE layout persisted in osStore */
export const DEFAULT_IDE_LAYOUT = {
  activity: "explorer", // explorer | search | scm | problems | run | null
  sidebarOpen: true,
  sidebarWidth: 240,
  bottom: null, // terminal | problems | output | null
  bottomHeight: 180,
  bottomOpen: false,
};

/**
 * @param {{ width: number, height: number }} area - IDE surface area
 * @param {typeof DEFAULT_IDE_LAYOUT} layout
 */
export function resolveIdeLayout(area, layout) {
  const w = Math.max(0, Number(area?.width) || 0);
  const h = Math.max(0, Number(area?.height) || 0);
  const sideMin = 180;
  const editorMin = 320;
  const bottomMin = 100;

  const canSideBySide = w >= sideMin + editorMin;
  const canBottomSplit = h >= 280 + bottomMin;

  const sidebarOpen = !!layout.sidebarOpen && !!layout.activity;
  const bottomOpen = !!layout.bottomOpen && !!layout.bottom;

  let sidebarMode = "hidden";
  if (sidebarOpen) {
    sidebarMode = canSideBySide ? "docked" : "sheet";
  }

  let bottomMode = "hidden";
  if (bottomOpen) {
    bottomMode = canBottomSplit ? "docked" : "sheet";
  }

  const sidebarWidth = Math.min(
    Math.max(sideMin, Number(layout.sidebarWidth) || 240),
    Math.max(sideMin, w - editorMin)
  );
  const bottomHeight = Math.min(
    Math.max(bottomMin, Number(layout.bottomHeight) || 180),
    Math.max(bottomMin, Math.floor(h * 0.45))
  );

  return {
    area: { width: w, height: h },
    activity: layout.activity,
    bottom: layout.bottom,
    sidebarMode, // docked | sheet | hidden
    bottomMode,
    sidebarWidth,
    bottomHeight,
    editorPrimary: true,
    showActivityBar: w >= 360 || sidebarOpen,
  };
}
