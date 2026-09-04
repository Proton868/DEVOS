/**
 * DevOS Spatial Workspace State
 * Single authoritative model for the spatial OS UI.
 * Replaces panel/dock-centric state for the primary experience.
 */
import { create } from "zustand";

const STORAGE_KEY = "devos_spatial";

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function persist(partial) {
  try {
    const prev = load() || {};
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...prev, ...partial }));
  } catch {
    /* ignore */
  }
}

const persisted = load();

export const useSpatialStore = create((set, get) => ({
  // Project / context
  projectId: persisted?.projectId ?? null,

  // Graph focus
  selectedNodeId: null,
  focusedNodeId: null,

  // Ephemeral editor
  editor: {
    open: false,
    file: null,
    line: null,
    language: null,
  },

  // Ghost terminal / runtime
  terminal: {
    open: false,
    executionId: null,
    nodeId: null,
  },

  // Agent / node inspector
  inspector: {
    open: false,
    nodeId: null,
  },

  // Command bar
  commandBar: {
    open: false,
    query: "",
  },

  // Canvas viewport
  viewport: {
    x: persisted?.viewport?.x ?? 0,
    y: persisted?.viewport?.y ?? 0,
    zoom: persisted?.viewport?.zoom ?? 1,
  },

  // Agency HUD visibility
  agencyHudOpen: true,

  // Sidebar mode for cosmic nav
  cosmicSidebarExpanded: false,

  // Actions
  setProjectId: (projectId) => {
    set({ projectId });
    persist({ projectId });
  },

  selectNode: (id) => set({ selectedNodeId: id }),

  focusNode: (id) =>
    set({
      focusedNodeId: id,
      selectedNodeId: id,
    }),

  clearFocus: () =>
    set({
      focusedNodeId: null,
      editor: { open: false, file: null, line: null, language: null },
      inspector: { open: false, nodeId: null },
    }),

  openEditor: ({ file, line = null, language = null, nodeId = null }) =>
    set({
      editor: { open: true, file, line, language },
      focusedNodeId: nodeId ?? get().focusedNodeId,
      selectedNodeId: nodeId ?? get().selectedNodeId,
    }),

  closeEditor: () =>
    set({
      editor: { open: false, file: null, line: null, language: null },
    }),

  openTerminal: ({ executionId = null, nodeId = null } = {}) =>
    set({
      terminal: { open: true, executionId, nodeId },
    }),

  closeTerminal: () =>
    set({
      terminal: { open: false, executionId: null, nodeId: null },
    }),

  toggleTerminal: () => {
    const t = get().terminal;
    if (t.open) get().closeTerminal();
    else get().openTerminal({ nodeId: get().selectedNodeId });
  },

  openInspector: (nodeId) =>
    set({
      inspector: { open: true, nodeId },
      selectedNodeId: nodeId,
    }),

  closeInspector: () =>
    set({
      inspector: { open: false, nodeId: null },
    }),

  openCommandBar: (query = "") =>
    set({
      commandBar: { open: true, query },
    }),

  closeCommandBar: () =>
    set({
      commandBar: { open: false, query: "" },
    }),

  setCommandQuery: (query) =>
    set((s) => ({
      commandBar: { ...s.commandBar, query },
    })),

  setViewport: (viewport) => {
    set({ viewport });
    persist({ viewport });
  },

  toggleAgencyHud: () =>
    set((s) => ({ agencyHudOpen: !s.agencyHudOpen })),

  toggleCosmicSidebar: () =>
    set((s) => ({ cosmicSidebarExpanded: !s.cosmicSidebarExpanded })),

  setCosmicSidebarExpanded: (v) => set({ cosmicSidebarExpanded: !!v }),
}));
