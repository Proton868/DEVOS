/**
 * DevOS MissionBar — Top status bar
 * Displays: workspace switcher, command palette trigger, status indicators,
 * quick actions (save layout, switch preset, settings).
 */
import React, { useState, useRef, useEffect } from "react";
import {
  Search, ChevronDown, Save, Layout, Settings,
  Cpu, Activity, Bell, Check, Zap,
} from "lucide-react";
import { useWorkspaceStore } from "../../store/workspaceStore";
import { useLayoutStore } from "../../store/layoutStore";
import { useThemeStore } from "../../store/themeStore";
import useStore from "../../store/useStore";
import { LAYOUT } from "../../theme/tokens";

export default function MissionBar() {
  const { getActiveWorkspace, workspaces, switchWorkspace, sidebarState, toggleSidebarState } = useWorkspaceStore();
  const { getAllPresets, activePreset, applyPreset, savePreset } = useLayoutStore();
  const { isDark, themeMeta, toggleDarkLight } = useThemeStore();
  const { setPaletteOpen, setSettingsOpen, providers, selectedProvider } = useStore();

  const [showWorkspaceMenu, setShowWorkspaceMenu] = useState(false);
  const [showPresetMenu, setShowPresetMenu] = useState(false);
  const [showNotifs, setShowNotifs] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);
  const wsRef = useRef(null);
  const presetRef = useRef(null);

  const workspace = getActiveWorkspace();
  const presets = getAllPresets();
  const activePresetObj = presets[activePreset];

  // Close menus on outside click
  useEffect(() => {
    const handler = (e) => {
      if (wsRef.current && !wsRef.current.contains(e.target)) setShowWorkspaceMenu(false);
      if (presetRef.current && !presetRef.current.contains(e.target)) setShowPresetMenu(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleSaveLayout = () => {
    const name = `Layout ${new Date().toLocaleTimeString()}`;
    savePreset(name, "Saved from current layout");
    setSaveStatus("saved");
    setTimeout(() => setSaveStatus(null), 2000);
  };

  return (
    <header
      className="devos-mission-bar"
      style={{ height: LAYOUT.missionBar.height }}
      role="banner"
    >
      {/* Left: Workspace switcher */}
      <div className="devos-mission-left">
        <button
          onClick={toggleSidebarState}
          title="Toggle sidebar (Ctrl+B)"
          aria-label="Toggle sidebar"
          className="devos-mission-btn"
        >
          <Layout size={14} />
        </button>

        <div className="devos-workspace-switcher" ref={wsRef}>
          <button
            onClick={() => setShowWorkspaceMenu((v) => !v)}
            className="devos-workspace-btn"
            aria-label="Switch workspace"
            aria-expanded={showWorkspaceMenu}
          >
            <span className="devos-workspace-name">{workspace?.name || "Default"}</span>
            <ChevronDown size={12} />
          </button>
          {showWorkspaceMenu && (
            <div className="devos-dropdown devos-workspace-menu" role="menu">
              {Object.values(workspaces).map((ws) => (
                <button
                  key={ws.id}
                  onClick={() => { switchWorkspace(ws.id); setShowWorkspaceMenu(false); }}
                  className={`devos-dropdown-item ${ws.id === workspace?.id ? "active" : ""}`}
                  role="menuitem"
                >
                  {ws.name}
                </button>
              ))}
              <div className="devos-dropdown-divider" />
              <button
                onClick={() => {
                  const name = prompt("Workspace name:");
                  if (name) useWorkspaceStore.getState().createWorkspace(name);
                  setShowWorkspaceMenu(false);
                }}
                className="devos-dropdown-item"
                role="menuitem"
              >
                + New Workspace
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Center: Command palette trigger */}
      <button
        onClick={() => setPaletteOpen(true)}
        className="devos-palette-trigger"
        aria-label="Open command palette"
        title="Command palette (Ctrl+K)"
      >
        <Search size={13} />
        <span className="devos-palette-trigger-text">Search or type a command...</span>
        <kbd className="devos-kbd">⌘K</kbd>
      </button>

      {/* Right: Status indicators + quick actions */}
      <div className="devos-mission-right">
        {/* Preset switcher */}
        <div className="devos-preset-switcher" ref={presetRef}>
          <button
            onClick={() => setShowPresetMenu((v) => !v)}
            className="devos-mission-btn"
            aria-label="Switch layout preset"
            aria-expanded={showPresetMenu}
            title="Layout presets"
          >
            <Layout size={13} />
            <span className="devos-preset-name">{activePresetObj?.name || "Builder"}</span>
            <ChevronDown size={11} />
          </button>
          {showPresetMenu && (
            <div className="devos-dropdown devos-preset-menu" role="menu">
              {Object.values(presets).map((preset) => (
                <button
                  key={preset.id}
                  onClick={() => { applyPreset(preset.id); setShowPresetMenu(false); }}
                  className={`devos-dropdown-item ${preset.id === activePreset ? "active" : ""}`}
                  role="menuitem"
                >
                  <div className="devos-preset-item">
                    <span className="devos-preset-item-name">{preset.name}</span>
                    <span className="devos-preset-item-desc">{preset.description}</span>
                  </div>
                </button>
              ))}
              <div className="devos-dropdown-divider" />
              <button
                onClick={() => { handleSaveLayout(); setShowPresetMenu(false); }}
                className="devos-dropdown-item"
                role="menuitem"
              >
                <Save size={12} /> Save current layout
              </button>
            </div>
          )}
        </div>

        {/* Save status */}
        {saveStatus === "saved" && (
          <span className="devos-save-indicator">
            <Check size={12} /> Saved
          </span>
        )}

        {/* Theme toggle */}
        <button
          onClick={toggleDarkLight}
          className="devos-mission-btn"
          aria-label="Toggle dark/light theme"
          title={`Theme: ${themeMeta?.name}`}
        >
          {isDark ? "🌙" : "☀️"}
        </button>

        {/* Provider status */}
        <div className="devos-status-indicator" title={`AI Provider: ${selectedProvider}`}>
          <Cpu size={12} />
          <span className="devos-status-text">{selectedProvider}</span>
          <span className="devos-status-dot online" />
        </div>

        {/* Notifications */}
        <button
          onClick={() => setShowNotifs((v) => !v)}
          className="devos-mission-btn devos-notif-btn"
          aria-label="Notifications"
          title="Notifications"
        >
          <Bell size={13} />
          <span className="devos-notif-badge" />
        </button>

        {/* Settings */}
        <button
          onClick={() => setSettingsOpen(true)}
          className="devos-mission-btn"
          aria-label="Settings"
          title="Settings (Ctrl+,)"
        >
          <Settings size={13} />
        </button>
      </div>
    </header>
  );
}
