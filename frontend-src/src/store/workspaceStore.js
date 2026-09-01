/**
 * DevOS Workspace Store
 * Manages multiple workspaces, each with its own layout, theme, and settings.
 */
import { create } from "zustand";

const STORAGE_KEY = "devos_workspaces";

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
        workspaces: state.workspaces,
        activeWorkspaceId: state.activeWorkspaceId,
      })
    );
  } catch {
    /* ignore */
  }
}

const persisted = loadPersisted();

const defaultWorkspaces = {
  default: {
    id: "default",
    name: "Default",
    icon: "LayoutGrid",
    createdAt: Date.now(),
    settings: {
      preset: "builder",
      theme: "carbon",
      density: "comfortable",
    },
  },
};

export const useWorkspaceStore = create((set, get) => ({
  // ── State ───────────────────────────────────────────────
  workspaces: persisted?.workspaces || defaultWorkspaces,
  activeWorkspaceId: persisted?.activeWorkspaceId || "default",
  sidebarState: "compact", // 'expanded' | 'compact' | 'hidden'

  // ── Actions ─────────────────────────────────────────────

  /**
   * Create a new workspace.
   */
  createWorkspace: (name, settings = {}) => {
    const id = `ws-${Date.now()}`;
    const workspace = {
      id,
      name,
      icon: settings.icon || "LayoutGrid",
      createdAt: Date.now(),
      settings: {
        preset: settings.preset || "builder",
        theme: settings.theme || "carbon",
        density: settings.density || "comfortable",
        ...settings,
      },
    };
    set((s) => ({
      workspaces: { ...s.workspaces, [id]: workspace },
      activeWorkspaceId: id,
    }));
    persist(get());
    return id;
  },

  /**
   * Delete a workspace.
   */
  deleteWorkspace: (id) => {
    if (id === "default") return; // can't delete default
    set((s) => {
      const workspaces = { ...s.workspaces };
      delete workspaces[id];
      const activeWorkspaceId =
        s.activeWorkspaceId === id ? "default" : s.activeWorkspaceId;
      return { workspaces, activeWorkspaceId };
    });
    persist(get());
  },

  /**
   * Switch to a workspace.
   */
  switchWorkspace: (id) => {
    if (!get().workspaces[id]) return;
    set({ activeWorkspaceId: id });
    persist(get());
    // Apply the workspace settings
    const ws = get().workspaces[id];
    if (ws.settings.theme) {
      // Defer to avoid circular import; themeStore reads this
      import("./themeStore").then(({ useThemeStore }) => {
        useThemeStore.getState().setTheme(ws.settings.theme);
      });
    }
  },

  /**
   * Rename a workspace.
   */
  renameWorkspace: (id, name) => {
    set((s) => {
      const ws = s.workspaces[id];
      if (!ws) return s;
      return {
        workspaces: { ...s.workspaces, [id]: { ...ws, name } },
      };
    });
    persist(get());
  },

  /**
   * Update workspace settings.
   */
  updateWorkspaceSettings: (id, settings) => {
    set((s) => {
      const ws = s.workspaces[id];
      if (!ws) return s;
      return {
        workspaces: {
          ...s.workspaces,
          [id]: { ...ws, settings: { ...ws.settings, ...settings } },
        },
      };
    });
    persist(get());
  },

  /**
   * Get the active workspace.
   */
  getActiveWorkspace: () => get().workspaces[get().activeWorkspaceId],

  /**
   * Set sidebar state (expanded/compact/hidden).
   */
  setSidebarState: (state) => {
    set({ sidebarState: state });
  },

  /**
   * Toggle sidebar through states: expanded → compact → hidden → expanded.
   */
  toggleSidebarState: () => {
    const { sidebarState } = get();
    const next = sidebarState === "expanded" ? "compact" : sidebarState === "compact" ? "hidden" : "expanded";
    set({ sidebarState: next });
  },

  /**
   * Export all workspaces as JSON.
   */
  exportWorkspaces: () => {
    return JSON.stringify({
      workspaces: get().workspaces,
      activeWorkspaceId: get().activeWorkspaceId,
    }, null, 2);
  },

  /**
   * Import workspaces from JSON.
   */
  importWorkspaces: (jsonString) => {
    try {
      const data = JSON.parse(jsonString);
      if (!data.workspaces) throw new Error("Invalid format");
      set({
        workspaces: { ...defaultWorkspaces, ...data.workspaces },
        activeWorkspaceId: data.activeWorkspaceId || "default",
      });
      persist(get());
      return true;
    } catch (e) {
      console.error("Failed to import workspaces:", e);
      return false;
    }
  },
}));

export default useWorkspaceStore;
