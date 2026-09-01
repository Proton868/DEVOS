/**
 * DevOS Panel Store
 * Manages all panel instances: positions, sizes, states, and layout structure.
 * This is the core of the dockable panel system.
 *
 * Panel Instance Schema:
 * {
 *   id: string,                    // Unique instance ID
 *   type: string,                  // Panel type (from registry)
 *   state: 'docked'|'floating'|'pinned'|'hidden'|'fullscreen',
 *   dock: 'top'|'bottom'|'left'|'right'|'center'|null,
 *   dockGroup: string|null,        // Tab group within a dock
 *   position: { x: number, y: number },  // For floating panels
 *   size: { width: number, height: number },
 *   config: {},                    // Panel-specific configuration
 *   zIndex: number,                // For floating panel stacking
 *   order: number,                 // Order within dock
 * }
 */
import { create } from "zustand";
import { panelRegistry } from "../components/panels/panelRegistry";
import { PANEL_STATES, DOCK_POSITIONS, LAYOUT, Z_INDEX } from "../theme/tokens";

const STORAGE_KEY = "devos_panels";

// ── Persistence ────────────────────────────────────────────
function loadPersisted() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function persist(state) {
  try {
    // Only persist panel instances and active layout, not transient drag state
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        panels: state.panels,
        activePanelId: state.activePanelId,
        dockOrder: state.dockOrder,
      })
    );
  } catch {
    /* ignore quota errors */
  }
}

let panelIdCounter = 0;
function genId(type) {
  panelIdCounter += 1;
  return `${type}-${Date.now()}-${panelIdCounter}`;
}

// ── Default Layout ─────────────────────────────────────────
function createDefaultPanels() {
  const workflow = {
    id: "workflow-main",
    type: "workflow",
    state: PANEL_STATES.DOCKED,
    dock: DOCK_POSITIONS.CENTER,
    dockGroup: null,
    position: { x: 0, y: 0 },
    size: { width: 0, height: 0 },
    config: {},
    zIndex: Z_INDEX.docked,
    order: 0,
  };
  const chat = {
    id: "chat-main",
    type: "chat",
    state: PANEL_STATES.DOCKED,
    dock: DOCK_POSITIONS.RIGHT,
    dockGroup: null,
    position: { x: 0, y: 0 },
    size: { width: 380, height: 0 },
    config: { mode: "docked" },
    zIndex: Z_INDEX.docked,
    order: 0,
  };
  return [workflow, chat];
}

const persisted = loadPersisted();

export const usePanelStore = create((set, get) => ({
  // ── State ───────────────────────────────────────────────
  panels: persisted?.panels || createDefaultPanels(),
  activePanelId: persisted?.activePanelId || "workflow-main",
  dockOrder: persisted?.dockOrder || {
    top: [],
    bottom: [],
    left: [],
    right: ["chat-main"],
    center: ["workflow-main"],
  },
  // Transient (not persisted)
  draggingPanelId: null,
  dragOverDock: null,
  focusedPanelId: null,
  panelNotifications: {}, // { [panelId]: number }

  // ── Panel Lifecycle ────────────────────────────────────
  /**
   * Open a panel of the given type. If singleton and exists, focus it.
   */
  openPanel: (type, options = {}) => {
    const def = panelRegistry.get(type);
    if (!def) {
      console.warn(`Panel type "${type}" not registered`);
      return null;
    }
    // Check for existing singleton
    if (def.singleton) {
      const existing = get().panels.find((p) => p.type === type);
      if (existing) {
        get().showPanel(existing.id);
        return existing.id;
      }
    }
    const id = options.id || genId(type);
    const panel = {
      id,
      type,
      state: options.state || PANEL_STATES.DOCKED,
      dock: options.dock || DOCK_POSITIONS.CENTER,
      dockGroup: options.dockGroup || null,
      position: options.position || { x: 100, y: 100 },
      size: options.size || def.defaultSize || { width: 420, height: 300 },
      config: { ...def.defaultConfig, ...options.config },
      zIndex: options.state === PANEL_STATES.FLOATING ? Z_INDEX.floating : Z_INDEX.docked,
      order: get().dockOrder[options.dock || DOCK_POSITIONS.CENTER]?.length || 0,
    };
    set((s) => ({
      panels: [...s.panels, panel],
      activePanelId: id,
      dockOrder: {
        ...s.dockOrder,
        [panel.dock]: [...(s.dockOrder[panel.dock] || []), id],
      },
    }));
    persist(get());
    return id;
  },

  /**
   * Close (remove) a panel instance.
   */
  closePanel: (id) => {
    set((s) => {
      const panel = s.panels.find((p) => p.id === id);
      if (!panel) return s;
      const panels = s.panels.filter((p) => p.id !== id);
      const dockOrder = { ...s.dockOrder };
      if (panel.dock) {
        dockOrder[panel.dock] = (dockOrder[panel.dock] || []).filter((pid) => pid !== id);
      }
      let activePanelId = s.activePanelId;
      if (activePanelId === id) {
        activePanelId = panels[0]?.id || null;
      }
      return { panels, dockOrder, activePanelId };
    });
    persist(get());
  },

  /**
   * Show a hidden panel.
   */
  showPanel: (id) => {
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === id && p.state === PANEL_STATES.HIDDEN
          ? { ...p, state: p.dock ? PANEL_STATES.DOCKED : PANEL_STATES.FLOATING }
          : p
      ),
      activePanelId: id,
      focusedPanelId: id,
    }));
    persist(get());
  },

  /**
   * Hide a panel (keep instance, just hide).
   */
  hidePanel: (id) => {
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === id ? { ...p, state: PANEL_STATES.HIDDEN } : p
      ),
    }));
    persist(get());
  },

  /**
   * Toggle panel visibility.
   */
  togglePanel: (type) => {
    const existing = get().panels.find((p) => p.type === type);
    if (existing) {
      if (existing.state === PANEL_STATES.HIDDEN) {
        get().showPanel(existing.id);
      } else {
        get().hidePanel(existing.id);
      }
    } else {
      get().openPanel(type);
    }
  },

  // ── Panel State Transitions ─────────────────────────────
  /**
   * Dock a panel to a specific position.
   */
  dockPanel: (id, dockPosition) => {
    set((s) => {
      const panel = s.panels.find((p) => p.id === id);
      if (!panel) return s;
      const dockOrder = { ...s.dockOrder };
      // Remove from old dock
      if (panel.dock && dockOrder[panel.dock]) {
        dockOrder[panel.dock] = dockOrder[panel.dock].filter((pid) => pid !== id);
      }
      // Add to new dock
      dockOrder[dockPosition] = [...(dockOrder[dockPosition] || []), id];
      return {
        panels: s.panels.map((p) =>
          p.id === id
            ? {
                ...p,
                state: PANEL_STATES.DOCKED,
                dock: dockPosition,
                zIndex: Z_INDEX.docked,
                order: (dockOrder[dockPosition] || []).length - 1,
              }
            : p
        ),
        dockOrder,
        activePanelId: id,
        focusedPanelId: id,
      };
    });
    persist(get());
  },

  /**
   * Float a panel (detach from dock).
   */
  floatPanel: (id, position) => {
    set((s) => {
      const panel = s.panels.find((p) => p.id === id);
      if (!panel) return s;
      const dockOrder = { ...s.dockOrder };
      if (panel.dock && dockOrder[panel.dock]) {
        dockOrder[panel.dock] = dockOrder[panel.dock].filter((pid) => pid !== id);
      }
      return {
        panels: s.panels.map((p) =>
          p.id === id
            ? {
                ...p,
                state: PANEL_STATES.FLOATING,
                dock: null,
                position: position || p.position,
                zIndex: Z_INDEX.floating,
              }
            : p
        ),
        dockOrder,
        activePanelId: id,
        focusedPanelId: id,
      };
    });
    persist(get());
  },

  /**
   * Pin a panel (always on top).
   */
  pinPanel: (id) => {
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === id ? { ...p, state: PANEL_STATES.PINNED, zIndex: Z_INDEX.floating + 10 } : p
      ),
    }));
    persist(get());
  },

  /**
   * Make panel fullscreen.
   */
  fullscreenPanel: (id) => {
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === id
          ? { ...p, state: PANEL_STATES.FULLSCREEN, zIndex: Z_INDEX.floating + 50 }
          : p
      ),
      activePanelId: id,
      focusedPanelId: id,
    }));
    persist(get());
  },

  /**
   * Restore panel from fullscreen/pinned to docked/floating.
   */
  restorePanel: (id) => {
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === id
          ? {
              ...p,
              state: p.dock ? PANEL_STATES.DOCKED : PANEL_STATES.FLOATING,
              zIndex: p.dock ? Z_INDEX.docked : Z_INDEX.floating,
            }
          : p
      ),
    }));
    persist(get());
  },

  // ── Panel Position/Size ──────────────────────────────────
  /**
   * Move a floating panel.
   */
  movePanel: (id, position) => {
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === id ? { ...p, position } : p
      ),
    }));
    // Throttle persistence
    clearTimeout(moveTimeout);
    moveTimeout = setTimeout(() => persist(get()), 500);
  },

  /**
   * Resize a panel.
   */
  resizePanel: (id, size) => {
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === id ? { ...p, size } : p
      ),
    }));
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => persist(get()), 500);
  },

  /**
   * Bring floating panel to front.
   */
  focusPanel: (id) => {
    set((s) => {
      const maxZ = Math.max(...s.panels.map((p) => p.zIndex || 0), Z_INDEX.floating);
      return {
        panels: s.panels.map((p) =>
          p.id === id && p.state === PANEL_STATES.FLOATING
            ? { ...p, zIndex: maxZ + 1 }
            : p
        ),
        activePanelId: id,
        focusedPanelId: id,
      };
    });
  },

  /**
   * Update panel config.
   */
  updatePanelConfig: (id, config) => {
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === id ? { ...p, config: { ...p.config, ...config } } : p
      ),
    }));
    persist(get());
  },

  // ── Dock Group (Tabs) ───────────────────────────────────
  /**
   * Add a panel to a tab group within a dock.
   */
  addToDockGroup: (id, dockPosition, groupId) => {
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === id
          ? { ...p, dock: dockPosition, dockGroup: groupId, state: PANEL_STATES.DOCKED }
          : p
      ),
    }));
    persist(get());
  },

  // ── Drag State ───────────────────────────────────────────
  setDragging: (id) => set({ draggingPanelId: id }),
  setDragOver: (dock) => set({ dragOverDock: dock }),
  clearDrag: () => set({ draggingPanelId: null, dragOverDock: null }),

  // ── Notifications ────────────────────────────────────────
  setPanelNotification: (id, count) =>
    set((s) => ({ panelNotifications: { ...s.panelNotifications, [id]: count } })),
  clearPanelNotification: (id) =>
    set((s) => {
      const notifications = { ...s.panelNotifications };
      delete notifications[id];
      return { panelNotifications: notifications };
    }),

  // ── Layout Operations ────────────────────────────────────
  /**
   * Reset to default layout.
   */
  resetLayout: () => {
    const panels = createDefaultPanels();
    set({
      panels,
      activePanelId: "workflow-main",
      dockOrder: {
        top: [],
        bottom: [],
        left: [],
        right: ["chat-main"],
        center: ["workflow-main"],
      },
    });
    persist(get());
  },

  /**
   * Replace all panels (used by layout presets).
   */
  setLayout: (panels, dockOrder, activePanelId) => {
    set({ panels, dockOrder, activePanelId: activePanelId || panels[0]?.id });
    persist(get());
  },

  // ── Selectors ────────────────────────────────────────────
  getVisiblePanels: () => {
    const { panels } = get();
    return panels.filter((p) => p.state !== PANEL_STATES.HIDDEN);
  },
  getFloatingPanels: () => {
    const { panels } = get();
    return panels.filter((p) => p.state === PANEL_STATES.FLOATING || p.state === PANEL_STATES.PINNED);
  },
  getDockedPanels: (dockPosition) => {
    const { panels, dockOrder } = get();
    const ids = dockOrder[dockPosition] || [];
    return ids
      .map((id) => panels.find((p) => p.id === id))
      .filter(Boolean)
      .filter((p) => p.state === PANEL_STATES.DOCKED);
  },
  getFullscreenPanel: () => {
    const { panels } = get();
    return panels.find((p) => p.state === PANEL_STATES.FULLSCREEN);
  },
}));

let moveTimeout, resizeTimeout;

export default usePanelStore;
