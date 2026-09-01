/**
 * DevOS Layout Store
 * Manages layout presets, active layout, and workspace-specific layouts.
 * Layout presets define the arrangement of panels — like Photoshop workspaces.
 */
import { create } from "zustand";
import { usePanelStore } from "./panelStore";

const STORAGE_KEY = "devos_layouts";

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
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        customPresets: state.customPresets,
        activePreset: state.activePreset,
      })
    );
  } catch {
    /* ignore */
  }
}

// ── Built-in Presets ────────────────────────────────────────
// Each preset defines a set of panels with their dock positions and sizes.
const BUILTIN_PRESETS = {
  builder: {
    id: "builder",
    name: "Builder",
    description: "Workflow 75% | Chat 25% | IDE hidden",
    builtin: true,
    icon: "Workflow",
    panels: [
      { type: "workflow", state: "docked", dock: "center", size: { width: 0, height: 0 } },
      { type: "chat", state: "docked", dock: "right", size: { width: 380, height: 0 } },
    ],
    dockOrder: { top: [], bottom: [], left: [], right: ["chat-main"], center: ["workflow-main"] },
  },

  developer: {
    id: "developer",
    name: "Developer",
    description: "IDE 70% | Workflow 20% | Terminal 10%",
    builtin: true,
    icon: "Code",
    panels: [
      { type: "ide", state: "docked", dock: "center", size: { width: 0, height: 0 } },
      { type: "workflow", state: "docked", dock: "right", size: { width: 320, height: 0 } },
      { type: "terminal", state: "docked", dock: "bottom", size: { width: 0, height: 240 } },
    ],
    dockOrder: { top: [], bottom: ["terminal-main"], left: [], right: ["workflow-main"], center: ["ide-main"] },
  },

  debug: {
    id: "debug",
    name: "Debug",
    description: "Terminal 50% | IDE 30% | Workflow 20%",
    builtin: true,
    icon: "Bug",
    panels: [
      { type: "terminal", state: "docked", dock: "center", size: { width: 0, height: 0 } },
      { type: "ide", state: "docked", dock: "right", size: { width: 420, height: 0 } },
      { type: "workflow", state: "docked", dock: "left", size: { width: 300, height: 0 } },
    ],
    dockOrder: { top: [], bottom: [], left: ["workflow-main"], right: ["ide-main"], center: ["terminal-main"] },
  },

  operations: {
    id: "operations",
    name: "Operations",
    description: "Metrics, Workers, Deployments, Logs, Alerts",
    builtin: true,
    icon: "Activity",
    panels: [
      { type: "metrics", state: "docked", dock: "center", size: { width: 0, height: 0 } },
      { type: "agents", state: "docked", dock: "right", size: { width: 360, height: 0 } },
      { type: "logs", state: "docked", dock: "bottom", size: { width: 0, height: 200 } },
    ],
    dockOrder: { top: [], bottom: ["logs-main"], left: [], right: ["agents-main"], center: ["metrics-main"] },
  },

  collaboration: {
    id: "collaboration",
    name: "AI Collaboration",
    description: "Chat, Workflow, Memory, Planning",
    builtin: true,
    icon: "Users",
    panels: [
      { type: "chat", state: "docked", dock: "center", size: { width: 0, height: 0 } },
      { type: "workflow", state: "docked", dock: "right", size: { width: 400, height: 0 } },
      { type: "memory", state: "docked", dock: "left", size: { width: 300, height: 0 } },
    ],
    dockOrder: { top: [], bottom: [], left: ["memory-main"], right: ["workflow-main"], center: ["chat-main"] },
  },

  presentation: {
    id: "presentation",
    name: "Presentation",
    description: "Clean, Minimal, Readonly, No sidebars",
    builtin: true,
    icon: "Presentation",
    panels: [
      { type: "workflow", state: "docked", dock: "center", size: { width: 0, height: 0 } },
    ],
    dockOrder: { top: [], bottom: [], left: [], right: [], center: ["workflow-main"] },
    config: { readonly: true, minimal: true, noSidebars: true },
  },
};

const persisted = loadPersisted();

export const useLayoutStore = create((set, get) => ({
  // ── State ───────────────────────────────────────────────
  builtinPresets: BUILTIN_PRESETS,
  customPresets: persisted?.customPresets || {},
  activePreset: persisted?.activePreset || "builder",

  // ── Actions ─────────────────────────────────────────────

  /**
   * Get all presets (builtin + custom).
   */
  getAllPresets: () => ({
    ...get().builtinPresets,
    ...get().customPresets,
  }),

  /**
   * Get a specific preset by ID.
   */
  getPreset: (id) => {
    const { builtinPresets, customPresets } = get();
    return builtinPresets[id] || customPresets[id] || null;
  },

  /**
   * Switch to a preset (apply its layout).
   */
  applyPreset: (presetId) => {
    const preset = get().getPreset(presetId);
    if (!preset) {
      console.warn(`Preset "${presetId}" not found`);
      return;
    }
    const panelStore = usePanelStore.getState();
    // Build panel instances from preset definition
    const panels = preset.panels.map((p, i) => ({
      id: `${p.type}-main`,
      type: p.type,
      state: p.state,
      dock: p.dock,
      dockGroup: null,
      position: { x: 0, y: 0 },
      size: p.size,
      config: p.config || {},
      order: i,
      zIndex: p.state === "floating" ? 100 : 10,
    }));
    panelStore.setLayout(panels, preset.dockOrder, panels[0]?.id);
    set({ activePreset: presetId });
    persist(get());
  },

  /**
   * Save the current layout as a custom preset.
   */
  savePreset: (name, description = "") => {
    const panelStore = usePanelStore.getState();
    const panels = panelStore.panels.map((p) => ({
      type: p.type,
      state: p.state,
      dock: p.dock,
      size: p.size,
      config: p.config,
    }));
    const presetId = `custom-${Date.now()}`;
    const preset = {
      id: presetId,
      name,
      description,
      builtin: false,
      icon: "Layout",
      panels,
      dockOrder: panelStore.dockOrder,
    };
    set((s) => ({
      customPresets: { ...s.customPresets, [presetId]: preset },
    }));
    persist(get());
    return presetId;
  },

  /**
   * Delete a custom preset.
   */
  deletePreset: (presetId) => {
    set((s) => {
      const customPresets = { ...s.customPresets };
      delete customPresets[presetId];
      return { customPresets };
    });
    persist(get());
  },

  /**
   * Rename a custom preset.
   */
  renamePreset: (presetId, newName) => {
    set((s) => {
      const preset = s.customPresets[presetId];
      if (!preset) return s;
      return {
        customPresets: {
          ...s.customPresets,
          [presetId]: { ...preset, name: newName },
        },
      };
    });
    persist(get());
  },

  /**
   * Export a preset as JSON.
   */
  exportPreset: (presetId) => {
    const preset = get().getPreset(presetId);
    if (!preset) return null;
    return JSON.stringify(preset, null, 2);
  },

  /**
   * Import a preset from JSON.
   */
  importPreset: (jsonString) => {
    try {
      const preset = JSON.parse(jsonString);
      if (!preset.name || !preset.panels) {
        throw new Error("Invalid preset format");
      }
      const newId = `imported-${Date.now()}`;
      preset.id = newId;
      preset.builtin = false;
      set((s) => ({
        customPresets: { ...s.customPresets, [newId]: preset },
      }));
      persist(get());
      return newId;
    } catch (e) {
      console.error("Failed to import preset:", e);
      return null;
    }
  },

  /**
   * Get the active preset object.
   */
  getActivePreset: () => get().getPreset(get().activePreset),
}));

export default useLayoutStore;
