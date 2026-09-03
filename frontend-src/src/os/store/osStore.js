/**
 * DevOS Spatial OS — the ONE authoritative workspace state model.
 * Describes WHAT the user is focusing on, not which arbitrary panel is open.
 *
 * Replaces the legacy panelStore/layoutStore/workspaceStore UI booleans.
 * Auth/project/file-tree stay in useStore (shared with LoginScreen etc.).
 */
import { create } from "zustand";

export const NODE_STATES = {
  IDLE: "IDLE",
  QUEUED: "QUEUED",
  THINKING: "THINKING",
  EXECUTING: "EXECUTING",
  RUNNING: "RUNNING",
  WAITING: "WAITING",
  SUCCESS: "SUCCESS",
  FAILED: "FAILED",
  BLOCKED: "BLOCKED",
  ERROR: "ERROR",
};

const useOsStore = create((set, get) => ({
  // ── Project ──────────────────────────────────────────────
  projectId: null,

  // ── Graph (real data, loaded from /api/scripts + /api/scripts/chains) ──
  nodes: [],        // [{ id, kind: 'trigger'|'script'|'runtime'|'output', title, scriptId, webhookToken, state, lastRun }]
  edges: [],        // [{ id, from, to, live }]
  graphLoading: false,
  setGraph: (nodes, edges) => set({ nodes, edges }),
  setGraphLoading: (v) => set({ graphLoading: v }),
  updateNode: (id, patch) =>
    set((s) => ({ nodes: s.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n)) })),
  setNodeState: (scriptId, state, lastRun) =>
    set((s) => ({
      nodes: s.nodes.map((n) =>
        n.scriptId === scriptId ? { ...n, state, lastRun: lastRun ?? n.lastRun } : n
      ),
    })),

  // ── Selection / focus ────────────────────────────────────
  selectedNode: null,
  focusedNode: null,
  selectNode: (id) => set({ selectedNode: id }),
  focusNode: (id) => set({ focusedNode: id, inspector: { open: true, nodeId: id } }),

  // ── Editor (DevOS IDE focus surface) ─────────────────────
  editor: { open: false, file: null, scriptId: null, language: null },
  openEditor: ({ file = null, scriptId = null, language = null } = {}) =>
    set({ editor: { open: true, file, scriptId, language } }),
  closeEditor: () => set({ editor: { open: false, file: null, scriptId: null, language: null } }),

  // ── Copilot ──────────────────────────────────────────────
  copilot: { open: false, nodeId: null, seed: null },
  openCopilot: (nodeId = null, seed = null) =>
    set({ copilot: { open: true, nodeId, seed } }),
  closeCopilot: () => set((s) => ({ copilot: { ...s.copilot, open: false, seed: null } })),

  // ── Inspector ────────────────────────────────────────────
  inspector: { open: false, nodeId: null },
  openInspector: (nodeId) => set({ inspector: { open: true, nodeId } }),
  closeInspector: () => set({ inspector: { open: false, nodeId: null } }),

  // ── Terminal (Ghost Terminal) — contextual + explicit toggle ──
  terminal: { open: false, pinned: false, executionId: null, nodeId: null },
  openTerminal: (nodeId = null, executionId = null) =>
    set((s) => ({
      terminal: {
        open: true,
        pinned: s.terminal.pinned,
        nodeId: nodeId ?? s.terminal.nodeId,
        executionId,
      },
    })),
  closeTerminal: () =>
    set((s) => ({
      terminal: { open: false, pinned: false, executionId: null, nodeId: null },
    })),
  toggleTerminal: () =>
    set((s) => {
      if (s.terminal.open) {
        return { terminal: { open: false, pinned: false, executionId: null, nodeId: null } };
      }
      return {
        terminal: {
          open: true,
          pinned: true,
          nodeId: s.selectedNode || s.terminal.nodeId,
          executionId: s.terminal.executionId,
        },
      };
    }),
  setTerminalPinned: (pinned) =>
    set((s) => ({ terminal: { ...s.terminal, pinned: !!pinned, open: pinned ? true : s.terminal.open } })),

  // ── Command bar ──────────────────────────────────────────
  commandBar: { open: false, query: "" },
  setCommandBar: (open, query = "") => set({ commandBar: { open, query } }),

  // ── Viewport ─────────────────────────────────────────────
  viewport: { x: 80, y: 60, zoom: 1 },
  setViewport: (v) => set({ viewport: v }),

  // ── Sidebar / overlays / HUD ─────────────────────────────
  // railCollapsed: hide the entire left rail (icon strip). Omni still independent.
  railCollapsed: false,
  toggleRail: () => set((s) => ({ railCollapsed: !s.railCollapsed })),
  setRailCollapsed: (v) => set({ railCollapsed: !!v }),
  omniOpen: true,
  toggleOmni: () => set((s) => ({ omniOpen: !s.omniOpen })),
  overlay: null, // null | 'files' | 'git' | 'search' | 'memory' | 'mcp' | 'research' | 'settings' | 'history' | 'system' | 'composer'
  setOverlay: (overlay) => set({ overlay }),
  dashboardOpen: true,
  setDashboardOpen: (v) => set({ dashboardOpen: v }),

  // ── Chat / Copilot presentation: docked (focus column) or floating ──
  chatMode: "docked", // 'docked' | 'floating'
  setChatMode: (mode) => set({ chatMode: mode === "floating" ? "floating" : "docked" }),
  toggleChatMode: () =>
    set((s) => ({ chatMode: s.chatMode === "floating" ? "docked" : "floating" })),

  // ── Live agents (real data from /api/workers + agent tasks) ──
  workers: [],
  setWorkers: (w) => set({ workers: w }),
  agentTasks: [],
  setAgentTasks: (t) => set({ agentTasks: t }),

  // ── Convenience: focused/selected node object ────────────
  getNode: (id) => get().nodes.find((n) => n.id === id) || null,
}));

export default useOsStore;
