import { create } from "zustand";
import { getToken, logout as apiLogout } from "../services/api";

const useStore = create((set, get) => ({
  // ── Auth ───────────────────────────────────────────────────
  // Initialized from localStorage synchronously so a page refresh with an
  // existing token doesn't flash the login screen before the check runs.
  // Note: this only reflects a *local* token at boot time — a Supabase-only
  // session (security-audit P2e) is confirmed asynchronously by App.jsx's
  // verifySession() effect, which calls setUser() once that resolves; until
  // then authChecked stays false so the login screen doesn't flash.
  user: null,
  authChecked: false,   // true once we've resolved whether the stored token is real
  isAuthenticated: !!getToken(),
  setUser: (user) => set({ user, isAuthenticated: !!user, authChecked: true }),
  logout: async () => {
    // security-audit P2e: clears both the local token AND signs out of any
    // active Supabase session (api.js's logout() handles both — see its
    // docstring), so a dual-auth user is fully logged out either way.
    await apiLogout();
    set({ user: null, isAuthenticated: false, authChecked: true });
  },

  // ── File Tree ──────────────────────────────────────────────
  fileTree: [],
  setFileTree: (tree) => set({ fileTree: tree }),

  // ── Open Tabs ──────────────────────────────────────────────
  openTabs: [],
  activeTab: null,
  splitTab: null,

  openFile: (file) => {
    const { openTabs } = get();
    const existing = openTabs.find((t) => t.path === file.path);
    if (existing) { set({ activeTab: file.path }); return; }
    set({ openTabs: [...openTabs, { ...file, modified: false }], activeTab: file.path });
  },
  openFileSplit: (file) => {
    const { openTabs } = get();
    if (!openTabs.find((t) => t.path === file.path)) {
      set({ openTabs: [...openTabs, { ...file, modified: false }] });
    }
    set({ splitTab: file.path });
  },
  closeSplit: () => set({ splitTab: null }),
  closeTab: (filePath) => {
    const { openTabs, activeTab, splitTab } = get();
    const idx = openTabs.findIndex((t) => t.path === filePath);
    const newTabs = openTabs.filter((t) => t.path !== filePath);
    let newActive = activeTab;
    if (activeTab === filePath) newActive = newTabs[Math.max(0, idx - 1)]?.path || null;
    set({ openTabs: newTabs, activeTab: newActive, splitTab: splitTab === filePath ? null : splitTab });
  },
  updateTabContent: (filePath, content) => set((s) => ({
    openTabs: s.openTabs.map((t) => t.path === filePath ? { ...t, content, modified: true } : t),
  })),
  markTabSaved: (filePath) => set((s) => ({
    openTabs: s.openTabs.map((t) => t.path === filePath ? { ...t, modified: false } : t),
  })),

  // ── AI Settings ───────────────────────────────────────────
  providers: {},
  setProviders: (p) => set({ providers: p }),
  selectedProvider: localStorage.getItem("devos_provider") || "ollama",
  selectedModel: localStorage.getItem("devos_model") || "",
  setProvider: (id) => {
    localStorage.setItem("devos_provider", id);
    const model = get().providers[id]?.defaultModel || "";
    localStorage.setItem("devos_model", model);
    set({ selectedProvider: id, selectedModel: model });
  },
  setModel: (model) => { localStorage.setItem("devos_model", model); set({ selectedModel: model }); },

  // ── Workspace Settings ────────────────────────────────────
  workspaceSettings: null,
  setWorkspaceSettings: (s) => set({ workspaceSettings: s }),

  // ── Chat ──────────────────────────────────────────────────
  chatMessages: [],
  chatOpen: false,
  addChatMessage: (msg) => set((s) => ({ chatMessages: [...s.chatMessages, { ...msg, id: Date.now() + Math.random() }] })),
  updateLastAssistantMessage: (text) => set((s) => {
    const msgs = [...s.chatMessages];
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === "assistant") { msgs[i] = { ...msgs[i], content: text }; return { chatMessages: msgs }; }
    }
    return {};
  }),
  clearChat: () => set({ chatMessages: [] }),
  setChatOpen: (v) => set({ chatOpen: v }),

  // ── @mention pinned files ─────────────────────────────────
  mentionedFiles: [],
  addMentionedFile: (f) => set((s) => ({
    mentionedFiles: s.mentionedFiles.find(x => x.path === f.path) ? s.mentionedFiles : [...s.mentionedFiles, f],
  })),
  removeMentionedFile: (path) => set((s) => ({ mentionedFiles: s.mentionedFiles.filter(f => f.path !== path) })),
  clearMentionedFiles: () => set({ mentionedFiles: [] }),

  // ── Terminal context (paste terminal output into chat) ────
  terminalContext: null,
  setTerminalContext: (text) => set({ terminalContext: text }),
  clearTerminalContext: () => set({ terminalContext: null }),

  // ── Scratch pad ────────────────────────────────────────────
  scratchOpen: false,
  setScratchOpen: (v) => set({ scratchOpen: v }),
  scratchContent: localStorage.getItem("devos_scratch") || "# Scratch Pad\n# Quick notes and code snippets — auto-saved\n",
  setScratchContent: (c) => { localStorage.setItem("devos_scratch", c); set({ scratchContent: c }); },

  // ── Terminal ──────────────────────────────────────────────
  terminalOpen: false,
  setTerminalOpen: (v) => set({ terminalOpen: v }),

  // ── Settings ──────────────────────────────────────────────
  settingsOpen: false,
  setSettingsOpen: (v) => set({ settingsOpen: v }),

  // ── CMD+K ─────────────────────────────────────────────────
  cmdkOpen: false,
  cmdkSelection: null,
  setCmdkOpen: (open, selection = null) => set({ cmdkOpen: open, cmdkSelection: selection }),

  // ── Composer (multi-file AI edits) ───────────────────────
  composerOpen: false,
  setComposerOpen: (v) => set({ composerOpen: v }),

  // ── Command Palette ───────────────────────────────────────
  paletteOpen: false,
  setPaletteOpen: (v) => set({ paletteOpen: v }),

  // ── Flow (embedded Python automation) ────────────────────
  flowOpen: false,
  setFlowOpen: (v) => set({ flowOpen: v }),

  // ── MCP (Model Context Protocol connections) ──────────────
  mcpOpen: false,
  setMcpOpen: (v) => set({ mcpOpen: v }),

  // ── Workers (Stage 3 personas — Sessions 8-11) ────────────
  workersOpen: false,
  setWorkersOpen: (v) => set({ workersOpen: v }),

  // ── Research (Deep Research Panel) ────────────────────────
  researchOpen: false,
  setResearchOpen: (v) => set({ researchOpen: v }),

  // ── Workflow Editor ────────────────────────────────────────
  workflowOpen: false,
  setWorkflowOpen: (v) => set({ workflowOpen: v }),

  // ── Git ───────────────────────────────────────────────────
  gitOpen: false,
  setGitOpen: (v) => set({ gitOpen: v }),
  gitStatus: null,
  setGitStatus: (s) => set({ gitStatus: s }),

  // ── Search ────────────────────────────────────────────────
  searchOpen: false,
  setSearchOpen: (v) => set({ searchOpen: v }),
  searchResults: [],
  setSearchResults: (r) => set({ searchResults: r }),

  // ── Problems ──────────────────────────────────────────────
  problems: [],
  problemsOpen: false,
  setProblemsOpen: (v) => set({ problemsOpen: v }),
  setProblems: (p) => set({ problems: p }),

  // ── Agent ─────────────────────────────────────────────────
  agentOpen: false,
  setAgentOpen: (v) => set({ agentOpen: v }),
  agentActions: [],
  agentRunning: false,
  addAgentAction: (a) => set((s) => ({ agentActions: [...s.agentActions, a] })),
  clearAgentActions: () => set({ agentActions: [] }),
  setAgentRunning: (v) => set({ agentRunning: v }),

  // ── Background agents ─────────────────────────────────────
  bgAgents: [],  // [{ id, task, status, startedAt, actions }]
  addBgAgent: (agent) => set((s) => ({ bgAgents: [...s.bgAgents, agent] })),
  updateBgAgent: (id, patch) => set((s) => ({
    bgAgents: s.bgAgents.map(a => a.id === id ? { ...a, ...patch } : a),
  })),
  removeBgAgent: (id) => set((s) => ({ bgAgents: s.bgAgents.filter(a => a.id !== id) })),

  // ── Index ─────────────────────────────────────────────────
  indexStats: null,
  setIndexStats: (s) => set({ indexStats: s }),

  // ── Status ────────────────────────────────────────────────
  statusMessage: "Ready",
  setStatus: (msg) => set({ statusMessage: msg }),

  // ── Project Switcher & HITL ───────────────────────────────
  currentProject: localStorage.getItem("devos_current_project") || "default",
  setCurrentProject: async (projectId) => {
    const { api } = await import("../services/api");
    api.setCurrentProject(projectId);
    set({ currentProject: projectId });
    try {
      const { tree } = await api.getTree();
      set({ fileTree: tree || [] });
    } catch (e) {}
  },
  pendingHitlRequests: [],
  setPendingHitlRequests: (requests) => set({ pendingHitlRequests: requests }),
  addPendingHitlRequest: (req) => set((s) => {
    if (s.pendingHitlRequests.some(r => r.id === req.id)) return {};
    return { pendingHitlRequests: [...s.pendingHitlRequests, req] };
  }),
  removePendingHitlRequest: (id) => set((s) => ({ pendingHitlRequests: s.pendingHitlRequests.filter(r => r.id !== id) })),

  // ── Mobile ────────────────────────────────────────────────
  mobileMenuOpen: false,
  setMobileMenuOpen: (v) => set({ mobileMenuOpen: v }),
  mobileFileTreeOpen: false,
  setMobileFileTreeOpen: (v) => set({ mobileFileTreeOpen: v }),
  mobileTab: 'editor',
  setMobileTab: (v) => set({ mobileTab: v }),

  // ── Theme (day/night mode) ─────────────────────────────────
  theme: localStorage.getItem("devos_theme") || "dark",
  setTheme: (t) => {
    localStorage.setItem("devos_theme", t);
    document.documentElement.setAttribute("data-theme", t);
    set({ theme: t });
  },

  // ── Midnight Obsidian layout active view ───────────────────
  activeView: "automation", // "automation" | "files" | "terminal" | "git" | "workers" | "chat" | "settings"
  setActiveView: (v) => set({ activeView: v }),

  // ── Script opened from Automation / PyRunner Matrix ───────
  activeScriptId: null,
  activeScriptCode: null,
  openScriptInEditor: (script) => {
    // Accepts { id, name, code, language } and opens it as a tab + right dock
    const file = {
      path: script.name || `script:${script.id}`,
      content: script.code || "",
      language: script.language || "python",
    };
    const { openTabs } = get();
    const existing = openTabs.find((t) => t.path === file.path);
    if (!existing) {
      set({ openTabs: [...openTabs, { ...file, modified: false }], activeTab: file.path });
    } else {
      set({ activeTab: file.path });
    }
    set({ activeScriptId: script.id, activeScriptCode: script.code, activeView: "chat" });
  },
}));

export default useStore;
