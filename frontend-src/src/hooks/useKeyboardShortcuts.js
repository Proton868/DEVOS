/**
 * DevOS Keyboard Shortcut System
 * Centralized shortcut registry with conflict resolution and context awareness.
 */
import { useEffect } from "react";
import useStore from "../store/useStore";
import { usePanelStore } from "../store/panelStore";
import { useThemeStore } from "../store/themeStore";
import { useWorkspaceStore } from "../store/workspaceStore";

class ShortcutRegistry {
  constructor() {
    this.shortcuts = new Map();
    this.activeContext = "global";
  }

  parseShortcut(combo) {
    const parts = combo.toLowerCase().split("+");
    const key = parts[parts.length - 1];
    return {
      key,
      ctrl: parts.includes("ctrl") || parts.includes("mod"),
      meta: parts.includes("cmd") || parts.includes("meta") || parts.includes("mod"),
      shift: parts.includes("shift"),
      alt: parts.includes("alt") || parts.includes("option"),
    };
  }

  matches(parsed, e) {
    const key = e.key.toLowerCase();
    if (parsed.key !== key) return false;
    if (parsed.ctrl !== (e.ctrlKey || false)) return false;
    if (parsed.meta !== (e.metaKey || false)) return false;
    if (parsed.shift !== (e.shiftKey || false)) return false;
    if (parsed.alt !== (e.altKey || false)) return false;
    return true;
  }

  register(id, combo, handler, options = {}) {
    this.shortcuts.set(id, {
      id,
      combo,
      parsed: this.parseShortcut(combo),
      handler,
      context: options.context || "global",
      description: options.description || "",
      preventDefault: options.preventDefault !== false,
    });
    return () => this.shortcuts.delete(id);
  }

  unregister(id) {
    this.shortcuts.delete(id);
  }

  setActiveContext(context) {
    this.activeContext = context;
  }

  getAll() {
    return Array.from(this.shortcuts.values());
  }

  getByContext(context) {
    return this.getAll().filter((s) => s.context === context || s.context === "global");
  }

  handleEvent(e) {
    const shortcuts = this.getByContext(this.activeContext);
    for (const shortcut of shortcuts) {
      if (this.matches(shortcut.parsed, e)) {
        if (shortcut.preventDefault) e.preventDefault();
        shortcut.handler(e);
        return true;
      }
    }
    return false;
  }
}

export const shortcutRegistry = new ShortcutRegistry();

let defaultsRegistered = false;

export function registerDefaultShortcuts() {
  if (defaultsRegistered) return;
  defaultsRegistered = true;
  const R = shortcutRegistry;

  R.register("palette-open", "Mod+K", () => {
    useStore.getState().setPaletteOpen(true);
  }, { description: "Open command palette" });

  R.register("terminal-toggle", "Mod+`", () => {
    usePanelStore.getState().togglePanel("terminal");
  }, { description: "Toggle terminal" });

  R.register("chat-toggle", "Mod+Shift+L", () => {
    usePanelStore.getState().togglePanel("chat");
  }, { description: "Toggle AI chat" });

  R.register("settings-open", "Mod+,", () => {
    useStore.getState().setSettingsOpen(true);
  }, { description: "Open settings" });

  R.register("sidebar-toggle", "Mod+B", () => {
    useWorkspaceStore.getState().toggleSidebarState();
  }, { description: "Toggle sidebar" });

  R.register("panel-fullscreen", "Mod+\\", () => {
    const ps = usePanelStore.getState();
    if (!ps.activePanelId) return;
    const fs = ps.getFullscreenPanel();
    if (fs?.id === ps.activePanelId) ps.restorePanel(ps.activePanelId);
    else ps.fullscreenPanel(ps.activePanelId);
  }, { description: "Toggle panel fullscreen" });

  R.register("panel-float", "Mod+Shift+I", () => {
    const ps = usePanelStore.getState();
    if (!ps.activePanelId) return;
    const panel = ps.panels.find((p) => p.id === ps.activePanelId);
    if (panel?.state === "floating") return;
    ps.floatPanel(ps.activePanelId, { x: 200, y: 150 });
  }, { description: "Float active panel" });

  R.register("panel-close", "Mod+W", () => {
    const ps = usePanelStore.getState();
    if (ps.activePanelId) ps.closePanel(ps.activePanelId);
  }, { description: "Close active panel" });

  R.register("layout-reset", "Mod+Shift+R", () => {
    usePanelStore.getState().resetLayout();
  }, { description: "Reset layout to default" });

  R.register("theme-toggle", "Mod+Shift+T", () => {
    useThemeStore.getState().toggleDarkLight();
  }, { description: "Toggle dark/light theme" });

  R.register("shortcuts-help", "?", () => {
    window.dispatchEvent(new CustomEvent("devos:show-shortcuts"));
  }, { description: "Show keyboard shortcuts", preventDefault: false });
}

/**
 * React hook to use the keyboard shortcut system.
 */
export function useKeyboardShortcuts() {
  useEffect(() => {
    registerDefaultShortcuts();
    const handler = (e) => shortcutRegistry.handleEvent(e);
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
}

export default useKeyboardShortcuts;
