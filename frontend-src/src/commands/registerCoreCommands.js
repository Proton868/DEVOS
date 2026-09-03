/**
 * Registers the canonical set of IDE commands required by the DEVOS IDE
 * completion standard (Requirement 20).
 *
    * Call once from App after the store is available.
 * Commands read live state via useStore.getState() so they stay current.
 *
 * One registry only — do not duplicate catalogs elsewhere.
 */
import { registerCommand } from "./registry";
import useStore from "../store/useStore";
import { api } from "../services/api";

let _registered = false;

export function registerCoreCommands() {
  if (_registered) return;
  _registered = true;

  const s = () => useStore.getState();

  // ── File / Editor ────────────────────────────────────────────────────
  registerCommand({
    id: "file.open",
    label: "Open File…",
    category: "File",
    shortcut: "Ctrl+P",
    keywords: ["quick open", "goto file"],
    run: () => s().setPaletteOpen(true),
  });

  registerCommand({
    id: "file.save",
    label: "Save",
    category: "File",
    shortcut: "Ctrl+S",
    keywords: ["write"],
    when: () => !!s().activeTab,
    run: async () => {
      const { activeTab, openTabs, markTabSaved, setStatus } = s();
      const tab = openTabs?.[activeTab];
      if (!tab) return;
      try {
        await api.writeFile(activeTab, tab.content);
        markTabSaved?.(activeTab);
        setStatus?.("Saved");
      } catch (e) {
        setStatus?.("Save failed: " + e.message);
      }
    },
  });

  registerCommand({
    id: "file.saveAll",
    label: "Save All",
    category: "File",
    shortcut: "Ctrl+K S",
    keywords: ["write all"],
    run: async () => {
      const { openTabs, markTabSaved, setStatus } = s();
      const dirty = Object.entries(openTabs || {}).filter(([, t]) => t?.dirty);
      for (const [path, tab] of dirty) {
        try {
          await api.writeFile(path, tab.content);
          markTabSaved?.(path);
        } catch (e) {
          setStatus?.(`Save failed (${path}): ${e.message}`);
          return;
        }
      }
      setStatus?.(`Saved ${dirty.length} file(s)`);
    },
  });

  registerCommand({
    id: "file.close",
    label: "Close Editor",
    category: "File",
    shortcut: "Ctrl+W",
    when: () => !!s().activeTab,
    run: () => {
      const { activeTab, closeTab, setStatus } = s();
      if (!activeTab) return;
      const result = closeTab?.(activeTab);
      if (result?.needsConfirm) {
        if (window.confirm(`"${result.name}" has unsaved changes. Close anyway?`)) {
          closeTab?.(activeTab, { force: true });
        }
      }
    },
  });

  registerCommand({
    id: "file.closeOthers",
    label: "Close Others",
    category: "File",
    when: () => (s().openTabs || []).length > 1,
    run: () => {
      const { activeTab, closeOtherTabs } = s();
      if (!activeTab) return;
      const result = closeOtherTabs?.(activeTab);
      if (result?.needsConfirm) {
        if (window.confirm(`${result.count} file(s) have unsaved changes. Close anyway?`)) {
          closeOtherTabs?.(activeTab, { force: true });
        }
      }
    },
  });

  registerCommand({
    id: "file.closeAll",
    label: "Close All Editors",
    category: "File",
    run: () => {
      const { closeAllTabs } = s();
      const result = closeAllTabs?.();
      if (result?.needsConfirm) {
        if (window.confirm(`${result.count} file(s) have unsaved changes. Close anyway?`)) {
          closeAllTabs?.({ force: true });
        }
      }
    },
  });

  registerCommand({
    id: "file.reopenClosed",
    label: "Reopen Closed Editor",
    category: "File",
    shortcut: "Ctrl+Shift+T",
    keywords: ["reopen", "undo close"],
    when: () => (s().recentlyClosed || []).length > 0,
    run: () => {
      const path = s().reopenLastClosed?.();
      if (path) s().setStatus?.(`Reopened ${path}`);
    },
  });

  registerCommand({
    id: "file.new",
    label: "New File",
    category: "File",
    shortcut: "Ctrl+N",
    keywords: ["create file"],
    run: async () => {
      const path = prompt("New file path (relative to project root):");
      if (!path) return;
      try {
        await api.createFile(path, "");
        s().openFile?.(path);
        s().refreshTree?.();
      } catch (e) {
        s().setStatus?.("Create failed: " + e.message);
      }
    },
  });

  registerCommand({
    id: "file.newFolder",
    label: "New Folder",
    category: "File",
    keywords: ["create directory", "mkdir"],
    run: async () => {
      const path = prompt("New folder path (relative to project root):");
      if (!path) return;
      try {
        await api.createDir?.(path);
        s().refreshTree?.();
      } catch (e) {
        s().setStatus?.("Create folder failed: " + e.message);
      }
    },
  });

  registerCommand({
    id: "file.rename",
    label: "Rename…",
    category: "File",
    when: () => !!s().activeTab,
    run: async () => {
      const { activeTab } = s();
      if (!activeTab) return;
      const next = prompt("Rename to:", activeTab);
      if (!next || next === activeTab) return;
      try {
        await api.rename?.(activeTab, next);
        s().closeTab?.(activeTab);
        s().openFile?.(next);
        s().refreshTree?.();
      } catch (e) {
        s().setStatus?.("Rename failed: " + e.message);
      }
    },
  });

  registerCommand({
    id: "file.delete",
    label: "Delete File…",
    category: "File",
    when: () => !!s().activeTab,
    run: async () => {
      const { activeTab } = s();
      if (!activeTab) return;
      if (!window.confirm(`Delete ${activeTab}?`)) return;
      try {
        await api.deleteFile?.(activeTab);
        s().closeTab?.(activeTab);
        s().refreshTree?.();
      } catch (e) {
        s().setStatus?.("Delete failed: " + e.message);
      }
    },
  });

  // ── Edit / Search ────────────────────────────────────────────────────
  registerCommand({
    id: "edit.find",
    label: "Find",
    category: "Edit",
    shortcut: "Ctrl+F",
    run: () => {
      // Monaco handles find when focused; open workspace search as fallback
      s().setSearchOpen?.(true);
    },
  });

  registerCommand({
    id: "edit.replace",
    label: "Replace",
    category: "Edit",
    shortcut: "Ctrl+H",
    run: () => s().setSearchOpen?.(true),
  });

  registerCommand({
    id: "search.workspace",
    label: "Search Workspace",
    category: "Search",
    shortcut: "Ctrl+Shift+F",
    keywords: ["find in files", "grep"],
    run: () => s().setSearchOpen?.(true),
  });

  registerCommand({
    id: "edit.format",
    label: "Format Document",
    category: "Edit",
    shortcut: "Shift+Alt+F",
    keywords: ["prettier", "beautify"],
    when: () => !!s().activeTab,
    run: () => {
      // Trigger Monaco format action if editor is focused
      window.dispatchEvent(new CustomEvent("devos:format-document"));
    },
  });

  // ── Navigation / LSP ─────────────────────────────────────────────────
  registerCommand({
    id: "nav.goToDefinition",
    label: "Go to Definition",
    category: "Navigation",
    shortcut: "F12",
    keywords: ["definition", "lsp"],
    run: () => window.dispatchEvent(new CustomEvent("devos:goto-definition")),
  });

  registerCommand({
    id: "nav.findReferences",
    label: "Find References",
    category: "Navigation",
    shortcut: "Shift+F12",
    keywords: ["references", "lsp"],
    run: () => window.dispatchEvent(new CustomEvent("devos:find-references")),
  });

  // ── View / Panels ────────────────────────────────────────────────────
  registerCommand({
    id: "view.terminal",
    label: "Toggle Terminal",
    category: "View",
    shortcut: "Ctrl+`",
    keywords: ["console", "shell"],
    run: () => {
      const { terminalOpen, setTerminalOpen } = s();
      setTerminalOpen?.(!terminalOpen);
    },
  });

  registerCommand({
    id: "view.problems",
    label: "Show Problems",
    category: "View",
    shortcut: "Ctrl+Shift+M",
    keywords: ["diagnostics", "errors"],
    run: () => s().setProblemsOpen?.(true),
  });

  registerCommand({
    id: "view.git",
    label: "Show Git",
    category: "View",
    keywords: ["source control", "scm"],
    run: () => s().setGitOpen?.(true),
  });

  registerCommand({
    id: "view.agent",
    label: "Show Agent Panel",
    category: "View",
    keywords: ["hai", "coding agent"],
    run: () => s().setAgentOpen?.(true),
  });

  registerCommand({
    id: "view.search",
    label: "Show Search",
    category: "View",
    run: () => s().setSearchOpen?.(true),
  });

  registerCommand({
    id: "view.settings",
    label: "Open Settings",
    category: "View",
    shortcut: "Ctrl+,",
    run: () => s().setSettingsOpen?.(true),
  });

  registerCommand({
    id: "view.commandPalette",
    label: "Command Palette",
    category: "View",
    shortcut: "Ctrl+Shift+P",
    run: () => s().setPaletteOpen?.(true),
  });

  // ── Terminal ─────────────────────────────────────────────────────────
  registerCommand({
    id: "terminal.new",
    label: "New Terminal",
    category: "Terminal",
    keywords: ["create terminal"],
    run: () => {
      s().setTerminalOpen?.(true);
      window.dispatchEvent(new CustomEvent("devos:terminal-new"));
    },
  });

  // ── Testing ──────────────────────────────────────────────────────────
  registerCommand({
    id: "test.runFile",
    label: "Run File Tests",
    category: "Test",
    keywords: ["pytest", "jest"],
    when: () => !!s().activeTab,
    run: async () => {
      const path = s().activeTab;
      if (!path) return;
      s().setStatus?.("Running tests for " + path);
      try {
        await api.runTests?.({ path });
      } catch (e) {
        s().setStatus?.("Test run failed: " + e.message);
      }
    },
  });

  registerCommand({
    id: "test.runRelated",
    label: "Run Related Tests",
    category: "Test",
    keywords: ["select_related_tests"],
    when: () => !!s().activeTab,
    run: async () => {
      const path = s().activeTab;
      if (!path) return;
      s().setStatus?.("Running related tests…");
      try {
        await api.runRelatedTests?.({ path });
      } catch (e) {
        s().setStatus?.("Related tests failed: " + e.message);
      }
    },
  });

  registerCommand({
    id: "test.runProject",
    label: "Run Project Tests",
    category: "Test",
    run: async () => {
      s().setStatus?.("Running project tests…");
      try {
        await api.runTests?.({ project: true });
      } catch (e) {
        s().setStatus?.("Project tests failed: " + e.message);
      }
    },
  });

  // ── Build ────────────────────────────────────────────────────────────
  registerCommand({
    id: "build.run",
    label: "Run Build",
    category: "Build",
    keywords: ["compile", "make"],
    run: async () => {
      s().setStatus?.("Building…");
      try {
        await api.runBuild?.();
      } catch (e) {
        s().setStatus?.("Build failed: " + e.message);
      }
    },
  });

  // ── Git ──────────────────────────────────────────────────────────────
  registerCommand({
    id: "git.status",
    label: "Git: Status",
    category: "Git",
    run: async () => {
      try {
        const st = await api.gitStatus();
        s().setGitStatus?.(st);
        s().setGitOpen?.(true);
      } catch (e) {
        s().setStatus?.("Git status failed: " + e.message);
      }
    },
  });

  registerCommand({
    id: "git.stage",
    label: "Git: Stage All",
    category: "Git",
    keywords: ["add"],
    run: async () => {
      try {
        await api.gitStage?.(".");
        const st = await api.gitStatus();
        s().setGitStatus?.(st);
        s().setStatus?.("Staged");
      } catch (e) {
        s().setStatus?.("Stage failed: " + e.message);
      }
    },
  });

  registerCommand({
    id: "git.commit",
    label: "Git: Commit…",
    category: "Git",
    run: async () => {
      const msg = prompt("Commit message:");
      if (!msg) return;
      try {
        await api.gitCommit?.(msg);
        const st = await api.gitStatus();
        s().setGitStatus?.(st);
        s().setStatus?.("Committed");
      } catch (e) {
        s().setStatus?.("Commit failed: " + e.message);
      }
    },
  });

  registerCommand({
    id: "git.branch",
    label: "Git: Branch…",
    category: "Git",
    run: async () => {
      const name = prompt("Branch name (leave empty to list):");
      try {
        if (name) {
          await api.gitCheckout?.(name, { create: true });
        }
        const st = await api.gitStatus();
        s().setGitStatus?.(st);
        s().setGitOpen?.(true);
      } catch (e) {
        s().setStatus?.("Branch failed: " + e.message);
      }
    },
  });

  // ── Agent ────────────────────────────────────────────────────────────
  registerCommand({
    id: "agent.ask",
    label: "Agent: Ask",
    category: "Agent",
    keywords: ["hai", "question"],
    run: () => {
      s().setAgentOpen?.(true);
      s().setAgentMode?.("ask");
    },
  });

  registerCommand({
    id: "agent.edit",
    label: "Agent: Edit",
    category: "Agent",
    keywords: ["hai", "edit mode"],
    run: () => {
      s().setAgentOpen?.(true);
      s().setAgentMode?.("edit");
    },
  });

  registerCommand({
    id: "agent.agent",
    label: "Agent: Agent Mode",
    category: "Agent",
    keywords: ["hai", "autonomous"],
    run: () => {
      s().setAgentOpen?.(true);
      s().setAgentMode?.("agent");
    },
  });

  registerCommand({
    id: "agent.review",
    label: "Agent: Review",
    category: "Agent",
    keywords: ["hai", "review mode"],
    run: () => {
      s().setAgentOpen?.(true);
      s().setAgentMode?.("review");
    },
  });

  registerCommand({
    id: "agent.cancel",
    label: "Agent: Cancel Task",
    category: "Agent",
    keywords: ["stop", "abort"],
    run: async () => {
      const taskId = s().activeAgentTaskId;
      if (!taskId) {
        s().setStatus?.("No active agent task");
        return;
      }
      try {
        await api.cancelAgentTask?.(taskId);
        s().setStatus?.("Cancellation requested");
      } catch (e) {
        s().setStatus?.("Cancel failed: " + e.message);
      }
    },
  });

  // ── Diff ─────────────────────────────────────────────────────────────
  registerCommand({
    id: "diff.show",
    label: "Show Diff",
    category: "Diff",
    keywords: ["changes", "patch"],
    run: () => {
      s().setDiffOpen?.(true);
      window.dispatchEvent(new CustomEvent("devos:show-diff"));
    },
  });
}
