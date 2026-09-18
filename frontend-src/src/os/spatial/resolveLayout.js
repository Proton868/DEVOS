/**
 * Constraint-based spatial layout resolver.
 * Adapts presentation from available width/height and content minimums.
 * Device labels are derived diagnostics only — never the decision input.
 */

import { WORKSPACE_REGISTRY, PRIMARY_NAV_ORDER, getWorkspace } from "./registry.js";

/** @typedef {'visible'|'collapsed'|'overlay'|'hidden'|'fullscreen'} SurfaceMode */

/**
 * @typedef {Object} Viewport
 * @property {number} width
 * @property {number} height
 * @property {'portrait'|'landscape'} [orientation]
 */

/**
 * @typedef {Object} SpatialStateInput
 * @property {string} activeId
 * @property {string|null} [fullscreenId]
 * @property {string[]} openIds
 * @property {Record<string, number>} [widthPct]  // share of main split for focus-like cols
 * @property {string[]} [collapsedIds]
 * @property {string|null} [overlayId]
 * @property {string|null} [previousArrangementJson]
 */

/**
 * Measure free content area (after chrome estimates).
 * @param {Viewport} viewport
 * @param {{ railWidth?: number, missionBarHeight?: number }} [chrome]
 */
export function contentArea(viewport, chrome = {}) {
  const w = Math.max(0, Number(viewport?.width) || 0);
  const h = Math.max(0, Number(viewport?.height) || 0);
  const rail = chrome.railWidth ?? 52;
  const bar = chrome.missionBarHeight ?? 44;
  return {
    width: Math.max(0, w - rail),
    height: Math.max(0, h - bar),
    orientation:
      viewport?.orientation ||
      (h >= w ? "portrait" : "landscape"),
  };
}

/**
 * How many primary simultaneous columns can fit without going below mins.
 * @param {number} areaWidth
 * @param {string[]} candidateIds
 */
export function maxSimultaneousPrimaries(areaWidth, candidateIds) {
  const defs = candidateIds
    .map((id) => getWorkspace(id))
    .filter(Boolean)
    .sort((a, b) => b.priority - a.priority);
  let used = 0;
  let count = 0;
  for (const d of defs) {
    const need = d.minWidth;
    if (used + need <= areaWidth) {
      used += need;
      count += 1;
    } else {
      break;
    }
  }
  return Math.max(1, count);
}

/**
 * Resolve presentation modes for all registered workspaces.
 * @param {Viewport} viewport
 * @param {SpatialStateInput} state
 * @param {{ railWidth?: number, missionBarHeight?: number }} [chrome]
 */
export function resolveLayout(viewport, state, chrome) {
  const area = contentArea(viewport, chrome);
  const openSet = new Set(
    (state.openIds || []).filter((id) => WORKSPACE_REGISTRY[id])
  );
  // Flow is ambient primary when nothing else claims exclusivity
  if (!openSet.has("flow")) openSet.add("flow");

  const collapsed = new Set(state.collapsedIds || []);
  const activeId =
    (state.activeId && WORKSPACE_REGISTRY[state.activeId]
      ? state.activeId
      : null) ||
    PRIMARY_NAV_ORDER.find((id) => openSet.has(id)) ||
    "flow";

  const fullscreenId =
    state.fullscreenId && WORKSPACE_REGISTRY[state.fullscreenId]
      ? state.fullscreenId
      : null;

  /** @type {Record<string, { mode: SurfaceMode, widthPx?: number, reason?: string }>} */
  const surfaces = {};

  // Fullscreen claims the content area exclusively
  if (fullscreenId) {
    for (const id of Object.keys(WORKSPACE_REGISTRY)) {
      if (id === fullscreenId) {
        surfaces[id] = {
          mode: "fullscreen",
          widthPx: area.width,
          heightPx: area.height,
          reason: "user_fullscreen",
        };
      } else if (openSet.has(id) && WORKSPACE_REGISTRY[id].supportsDock) {
        surfaces[id] = { mode: "collapsed", reason: "fullscreen_peer" };
      } else if (openSet.has(id) && WORKSPACE_REGISTRY[id].supportsOverlay) {
        surfaces[id] = { mode: "hidden", reason: "fullscreen_peer" };
      } else {
        surfaces[id] = { mode: "hidden", reason: "fullscreen_other" };
      }
    }
    return finalize(area, activeId, fullscreenId, surfaces, state, openSet);
  }

  // Candidates wanting simultaneous primary layout
  const primaryOpen = PRIMARY_NAV_ORDER.filter(
    (id) => openSet.has(id) && !collapsed.has(id)
  );
  // Prefer active first in packing
  const ordered = [
    activeId,
    ...primaryOpen.filter((id) => id !== activeId),
  ].filter((id, i, arr) => arr.indexOf(id) === i && openSet.has(id));

  const capacity = maxSimultaneousPrimaries(area.width, ordered);
  const stackMode = capacity <= 1 || area.width < minPrimaryPair(ordered);

  if (stackMode) {
    // One full-screen active dimension; others open → collapsed nav targets
    for (const id of Object.keys(WORKSPACE_REGISTRY)) {
      const def = WORKSPACE_REGISTRY[id];
      if (!openSet.has(id) && id !== "flow") {
        surfaces[id] = { mode: "hidden", reason: "not_open" };
        continue;
      }
      if (id === activeId) {
        surfaces[id] = {
          mode: "fullscreen",
          widthPx: area.width,
          heightPx: area.height,
          reason: "stack_active",
        };
      } else if (openSet.has(id) && def.supportsMobileNav) {
        surfaces[id] = { mode: "collapsed", reason: "stack_inactive" };
      } else if (openSet.has(id) && def.supportsOverlay) {
        surfaces[id] = { mode: "overlay", reason: "stack_secondary" };
      } else if (id === "flow" && activeId !== "flow") {
        surfaces[id] = { mode: "hidden", reason: "stack_background" };
      } else {
        surfaces[id] = { mode: "hidden", reason: "stack_default" };
      }
    }
    // Overlay utility if explicitly requested
    if (state.overlayId && WORKSPACE_REGISTRY[state.overlayId]) {
      surfaces[state.overlayId] = {
        mode: "overlay",
        reason: "user_overlay",
      };
    }
    return finalize(area, activeId, activeId, surfaces, state, openSet);
  }

  // Multi-dimension: pack by priority until width exhausted
  let remaining = area.width;
  const visiblePrimaries = [];
  for (const id of ordered) {
    const def = getWorkspace(id);
    if (!def) continue;
    if (collapsed.has(id)) {
      surfaces[id] = { mode: "collapsed", reason: "user_collapsed" };
      continue;
    }
    if (remaining >= def.minWidth) {
      visiblePrimaries.push(id);
      remaining -= def.minWidth;
    } else if (def.supportsDock) {
      surfaces[id] = { mode: "collapsed", reason: "insufficient_width" };
    } else {
      surfaces[id] = { mode: "hidden", reason: "insufficient_width" };
    }
  }

  // Distribute extra pixels by preferred width * flexibility
  const extra = Math.max(0, area.width - visiblePrimaries.reduce((s, id) => s + getWorkspace(id).minWidth, 0));
  const weights = visiblePrimaries.map((id) => {
    const d = getWorkspace(id);
    return Math.max(0.1, d.preferredWidth * (0.35 + d.flexibility));
  });
  const weightSum = weights.reduce((a, b) => a + b, 0) || 1;
  visiblePrimaries.forEach((id, i) => {
    const def = getWorkspace(id);
    const share = extra * (weights[i] / weightSum);
    surfaces[id] = {
      mode: "visible",
      widthPx: Math.floor(def.minWidth + share),
      heightPx: area.height,
      reason: "split",
    };
  });

  for (const id of Object.keys(WORKSPACE_REGISTRY)) {
    if (surfaces[id]) continue;
    const def = WORKSPACE_REGISTRY[id];
    if (!openSet.has(id)) {
      surfaces[id] = { mode: "hidden", reason: "not_open" };
    } else if (collapsed.has(id)) {
      surfaces[id] = { mode: "collapsed", reason: "user_collapsed" };
    } else if (def.kind !== "primary" && def.supportsOverlay) {
      surfaces[id] = { mode: "overlay", reason: "secondary_open" };
    } else if (def.supportsDock) {
      surfaces[id] = { mode: "collapsed", reason: "demoted" };
    } else {
      surfaces[id] = { mode: "hidden", reason: "default" };
    }
  }

  if (state.overlayId && WORKSPACE_REGISTRY[state.overlayId]) {
    surfaces[state.overlayId] = { mode: "overlay", reason: "user_overlay" };
  }

  // Height: if content height below min for a visible surface, force stack for that surface
  for (const id of visiblePrimaries) {
    const def = getWorkspace(id);
    if (area.height < def.minHeight && def.supportsFullscreen) {
      // Insufficient height → active fullscreen stack
      return resolveLayout(viewport, { ...state, fullscreenId: null }, chrome);
    }
  }

  return finalize(area, activeId, null, surfaces, state, openSet);
}

function minPrimaryPair(ids) {
  const mins = ids
    .map((id) => getWorkspace(id)?.minWidth || 0)
    .filter((n) => n > 0)
    .sort((a, b) => a - b);
  if (mins.length < 2) return mins[0] || 320;
  return mins[0] + mins[1];
}

function finalize(area, activeId, fullscreenId, surfaces, state, openSet) {
  const visible = Object.entries(surfaces)
    .filter(([, v]) => v.mode === "visible" || v.mode === "fullscreen")
    .map(([id]) => id);
  const collapsedIds = Object.entries(surfaces)
    .filter(([, v]) => v.mode === "collapsed")
    .map(([id]) => id);

  // Focus column pct for legacy shell: share of area for non-flow visible primaries
  const side = visible.filter((id) => id !== "flow");
  let focusWidthPct = state.widthPct?.focus ?? 62;
  if (side.length && surfaces[side[0]]?.widthPx && area.width > 0) {
    focusWidthPct = Math.round(
      (100 * side.reduce((s, id) => s + (surfaces[id].widthPx || 0), 0)) /
        area.width
    );
    focusWidthPct = Math.min(85, Math.max(28, focusWidthPct));
  }

  const presentation =
    fullscreenId || (visible.length <= 1 && area.width < 720)
      ? "stack"
      : visible.length >= 2
        ? "split"
        : "stack";

  return {
    area,
    activeId,
    fullscreenId,
    presentation, // 'split' | 'stack'
    surfaces,
    visibleIds: visible,
    collapsedIds,
    openIds: [...openSet],
    navOrder: PRIMARY_NAV_ORDER.filter(
      (id) => openSet.has(id) || id === activeId
    ),
    focusWidthPct,
    focusCollapsed: side.length === 0 && collapsedIds.includes("ide"),
    showDimensionNav: presentation === "stack",
    orientation: area.orientation,
  };
}

/**
 * Derive openIds from legacy osStore surface flags.
 */
export function openIdsFromLegacyFlags(flags) {
  const ids = ["flow"];
  if (flags?.copilotOpen) ids.push("chat");
  if (flags?.editorOpen) ids.push("ide");
  if (flags?.previewOpen) ids.push("preview");
  if (flags?.terminalOpen) ids.push("terminal");
  if (flags?.inspectorOpen) ids.push("inspector");
  if (flags?.filesOpen) ids.push("files");
  return [...new Set(ids)];
}

/**
 * Map activeWorkspace string → dimension id
 */
export function normalizeActiveId(name) {
  const map = {
    canvas: "flow",
    flow: "flow",
    ide: "ide",
    chat: "chat",
    preview: "preview",
    fleet: "flow",
    terminal: "terminal",
    files: "files",
  };
  return map[name] || name || "flow";
}
