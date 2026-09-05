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
    set((s) => {
      const layout = {
        ...s.layout,
        focusCollapsed: false,
        activeWorkspace: "ide",
        filesDrawerOpen: s.layout?.filesDrawerOpen !== false,
      };
      try { localStorage.setItem("devos_sp_layout", JSON.stringify(layout)); } catch (_) {}
      return {
        editor: { open: true, file, scriptId, language },
        layout,
      };
    }),
  closeEditor: () => set({ editor: { open: false, file: null, scriptId: null, language: null } }),

  webIntel: { open: false, crawlId: null },
  openWebIntel: ({ crawlId = null } = {}) =>
    set({ webIntel: { open: true, crawlId: crawlId || null } }),
  closeWebIntel: () => set({ webIntel: { open: false, crawlId: null } }),

  // ── Workspace Preview (verified artifact presentation) ───
  preview: {
    open: false,
    minimized: false,
    projectId: null,
    path: "index.html",
    error: null,
    title: "Preview",
  },
  openPreview: ({ projectId = null, path = "index.html", title = "Preview" } = {}) =>
    set((s) => {
      const layout = { ...s.layout, activeWorkspace: "preview" };
      try { localStorage.setItem("devos_sp_layout", JSON.stringify(layout)); } catch (_) {}
      return {
        preview: {
          open: true,
          minimized: false,
          projectId: projectId || null,
          path: path || "index.html",
          error: null,
          title: title || "Preview",
        },
        layout,
      };
    }),
  closePreview: () =>
    set((s) => ({
      preview: { ...s.preview, open: false, minimized: false, error: null },
    })),
  minimizePreview: () =>
    set((s) => ({ preview: { ...s.preview, minimized: true } })),
  restorePreview: () =>
    set((s) => ({ preview: { ...s.preview, minimized: false, open: true } })),
  setPreviewPath: (path) =>
    set((s) => ({ preview: { ...s.preview, path: path || "index.html", error: null } })),
  setPreviewError: (error) =>
    set((s) => ({ preview: { ...s.preview, error: error || null } })),

  // ── Copilot ──────────────────────────────────────────────
  nuhaMode: "chat", // chat | plan | action
  setNuhaMode: (mode) => set({ nuhaMode: mode || "chat" }),
  activePlanId: null,
  setActivePlanId: (id) => set({ activePlanId: id || null }),
  orchestrationStatus: null,
  setOrchestrationStatus: (s) => set({ orchestrationStatus: s }),
  // Live mission snapshot for canvas glow + Agency Dashboard (not a second shell)
  orchestrationMission: null, // { planId, status, goal, nodes[], edges[], updatedAt }
  setOrchestrationMission: (mission) => set({ orchestrationMission: mission || null }),
  applyOrchestrationPlan: (plan) => {
    if (!plan) return set({ orchestrationMission: null });
    const nodes = plan.nodes || (plan.steps || []).map((s) => ({
      id: s.id,
      description: s.description,
      persona_id: s.persona_id,
      status: s.status || "pending",
      capabilities: s.required_capabilities || s.capabilities || [],
    }));
    set({
      activePlanId: plan.id || plan.plan_id || null,
      orchestrationStatus: plan.status || null,
      orchestrationMission: {
        planId: plan.id || plan.plan_id,
        status: plan.status,
        goal: plan.goal,
        nodes,
        edges: plan.edges || [],
        personas: plan.personas || [],
        risk_level: plan.risk_level,
        requires_hitl: plan.requires_hitl,
        pivot_reached: plan.pivot_reached || plan.saga?.pivot_reached,
        pivot_action: plan.pivot_action || plan.saga?.pivot_action,
        pivot_step_id: plan.pivot_step_id || plan.saga?.pivot_step_id,
        saga: plan.saga || null,
        updatedAt: Date.now(),
      },
    });
  },
  activePersonaId: "nuha",
  setActivePersona: (id) => set({ activePersonaId: (id || "nuha").toLowerCase() }),
  personaProfileOpen: null, // persona id or null
  openPersonaProfile: (id) => set({ personaProfileOpen: id || "nuha", overlay: "persona-profile" }),
  closePersonaProfile: () => set({ personaProfileOpen: null, overlay: null }),
  copilot: { open: false, nodeId: null, seed: null, personaId: "nuha" },
  openCopilot: (nodeId = null, seed = null, personaId = null) =>
    set((s) => {
      const layout = { ...s.layout, focusCollapsed: false, activeWorkspace: "chat" };
      try { localStorage.setItem("devos_sp_layout", JSON.stringify(layout)); } catch (_) {}
      return {
        copilot: {
          open: true,
          nodeId,
          seed,
          personaId: (personaId || s.activePersonaId || "nuha").toLowerCase(),
        },
        layout,
      };
    }),
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
  setOmniOpen: (v) => set({ omniOpen: !!v }),
  overlay: null, // null | 'files' | 'git' | 'search' | 'memory' | 'mcp' | 'research' | 'settings' | 'history' | 'system' | 'composer'
  setOverlay: (overlay) => set({ overlay }),

  // ── Workspace layout (spatial window behavior — not a second shell) ──
  layout: {
    fleetCollapsed: true,
    focusCollapsed: false,
    focusWidthPct: 62,
    filesDrawerOpen: true,
    activeWorkspace: "canvas", // canvas | ide | chat | fleet | preview
  },
  setLayout: (patch) =>
    set((s) => {
      const layout = { ...s.layout, ...patch };
      try {
        localStorage.setItem("devos_sp_layout", JSON.stringify(layout));
      } catch (_) { /* ignore */ }
      return { layout };
    }),
  hydrateLayout: () => {
    try {
      const raw = localStorage.getItem("devos_sp_layout");
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") {
        set((s) => ({ layout: { ...s.layout, ...parsed } }));
      }
    } catch (_) { /* ignore */ }
  },
  setFleetCollapsed: (v) =>
    set((s) => {
      const layout = { ...s.layout, fleetCollapsed: !!v };
      try { localStorage.setItem("devos_sp_layout", JSON.stringify(layout)); } catch (_) {}
      return { layout };
    }),
  setFocusCollapsed: (v) =>
    set((s) => {
      const layout = { ...s.layout, focusCollapsed: !!v };
      try { localStorage.setItem("devos_sp_layout", JSON.stringify(layout)); } catch (_) {}
      return { layout };
    }),
  setFocusWidthPct: (pct) =>
    set((s) => {
      const n = Math.min(85, Math.max(28, Number(pct) || 62));
      const layout = { ...s.layout, focusWidthPct: n };
      try { localStorage.setItem("devos_sp_layout", JSON.stringify(layout)); } catch (_) {}
      return { layout };
    }),
  setFilesDrawerOpen: (v) =>
    set((s) => {
      const layout = { ...s.layout, filesDrawerOpen: !!v };
      try { localStorage.setItem("devos_sp_layout", JSON.stringify(layout)); } catch (_) {}
      return { layout };
    }),
  setActiveWorkspace: (name) =>
    set((s) => {
      const layout = { ...s.layout, activeWorkspace: name || "canvas" };
      try { localStorage.setItem("devos_sp_layout", JSON.stringify(layout)); } catch (_) {}
      return { layout };
    }),

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
