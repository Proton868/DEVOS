/**
 * Pure IDE tab / split group model for the Spatial DevOS IDE.
 * Does not depend on React — unit-testable.
 */

/**
 * @typedef {{ path: string, language?: string|null, modified?: boolean, title?: string }} IdeTab
 */

/**
 * @param {IdeTab[]} tabs
 * @param {IdeTab} tab
 * @returns {{ tabs: IdeTab[], activePath: string }}
 */
export function openTab(tabs, tab) {
  const list = Array.isArray(tabs) ? [...tabs] : [];
  const path = String(tab?.path || "");
  if (!path) return { tabs: list, activePath: null };
  const idx = list.findIndex((t) => t.path === path);
  if (idx >= 0) {
    const next = [...list];
    next[idx] = { ...next[idx], ...tab, path };
    return { tabs: next, activePath: path };
  }
  return {
    tabs: [...list, { language: null, modified: false, title: path.split("/").pop(), ...tab, path }],
    activePath: path,
  };
}

/**
 * @param {IdeTab[]} tabs
 * @param {string} path
 * @param {{ activePath?: string|null, splitPath?: string|null }} selection
 */
export function closeTab(tabs, path, selection = {}) {
  const list = (tabs || []).filter((t) => t.path !== path);
  let activePath = selection.activePath ?? null;
  let splitPath = selection.splitPath ?? null;
  if (splitPath === path) splitPath = null;
  if (activePath === path) {
    activePath = list.length ? list[Math.max(0, list.length - 1)].path : null;
  }
  return { tabs: list, activePath, splitPath };
}

/**
 * @param {IdeTab[]} tabs
 * @param {string} path
 * @param {Partial<IdeTab>} patch
 */
export function patchTab(tabs, path, patch) {
  return (tabs || []).map((t) => (t.path === path ? { ...t, ...patch } : t));
}

/**
 * Split editor: secondary group shows splitPath; primary shows activePath.
 * @param {string|null} activePath
 * @param {string|null} splitPath
 * @param {string} path
 */
export function openSplit(activePath, splitPath, path) {
  if (!path) return { activePath, splitPath: null };
  if (path === activePath) return { activePath, splitPath: splitPath || null };
  return { activePath: activePath || path, splitPath: path };
}

export function closeSplit(activePath) {
  return { activePath, splitPath: null };
}

/**
 * Language heuristic from path.
 */
export function languageFromPath(path) {
  const p = String(path || "").toLowerCase();
  if (p.endsWith(".py")) return "python";
  if (p.endsWith(".ts") || p.endsWith(".tsx")) return "typescript";
  if (p.endsWith(".js") || p.endsWith(".jsx") || p.endsWith(".mjs")) return "javascript";
  if (p.endsWith(".json")) return "json";
  if (p.endsWith(".css") || p.endsWith(".scss")) return "css";
  if (p.endsWith(".html") || p.endsWith(".htm")) return "html";
  if (p.endsWith(".md")) return "markdown";
  if (p.endsWith(".yml") || p.endsWith(".yaml")) return "yaml";
  if (p.endsWith(".rs")) return "rust";
  if (p.endsWith(".go")) return "go";
  if (p.endsWith(".sh")) return "shell";
  return "plaintext";
}

/**
 * Merge LSP diagnostics keyed by URI/path.
 * @param {Record<string, object[]>} current
 * @param {{ uri?: string, diagnostics?: object[] }} params
 */
export function mergeDiagnostics(current, params) {
  const next = { ...(current || {}) };
  const uri = params?.uri || "";
  const path = uri.replace(/^file:\/\//, "").replace(/^\/+/, "") || uri;
  const diags = Array.isArray(params?.diagnostics) ? params.diagnostics : [];
  if (!path) return next;
  if (!diags.length) {
    delete next[path];
    return next;
  }
  next[path] = diags.map((d) => ({
    severity: d.severity ?? 1,
    message: d.message || "",
    range: d.range || null,
    source: d.source || "lsp",
  }));
  return next;
}

export function flattenDiagnostics(map) {
  const out = [];
  for (const [path, diags] of Object.entries(map || {})) {
    for (const d of diags || []) {
      out.push({ path, ...d });
    }
  }
  return out;
}
