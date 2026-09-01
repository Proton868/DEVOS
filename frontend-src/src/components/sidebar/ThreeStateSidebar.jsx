/**
 * DevOS ThreeStateSidebar
 * Three states: Expanded, Compact (icons only), Hidden (hover-to-reveal).
 * Like Raycast — slides in from the left edge when hidden.
 */
import React, { useState, useRef, useEffect } from "react";
import {
  Workflow, FolderKanban, MessageSquare, Terminal as TerminalIcon,
  GitBranch, Bot, Settings, ChevronLeft, ChevronRight, Search,
} from "lucide-react";
import { useWorkspaceStore } from "../../store/workspaceStore";
import { usePanelStore } from "../../store/panelStore";
import { panelRegistry } from "../panels/panelRegistry";
import { LAYOUT } from "../../theme/tokens";

// Icon mapping for panel types
const ICON_MAP = {
  workflow: Workflow,
  files: FolderKanban,
  chat: MessageSquare,
  terminal: TerminalIcon,
  git: GitBranch,
  agents: Bot,
  ide: ChevronRight,
};

export default function ThreeStateSidebar() {
  const { sidebarState, setSidebarState } = useWorkspaceStore();
  const { togglePanel, panels, panelNotifications, setSettingsOpen } = usePanelStore();
  const [hovered, setHovered] = useState(false);
  const [showTooltip, setShowTooltip] = useState(null);
  const hoverTimer = useRef(null);

  // Get sidebar items from registry
  const sidebarItems = panelRegistry.getSidebarPanels();

  // Hover-to-reveal when hidden
  useEffect(() => {
    if (sidebarState !== "hidden") return;
    const handleMouseMove = (e) => {
      if (e.clientX < 8) {
        hoverTimer.current = setTimeout(() => setHovered(true), 200);
      }
    };
    window.addEventListener("mousemove", handleMouseMove);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      clearTimeout(hoverTimer.current);
    };
  }, [sidebarState]);

  const isVisible = sidebarState === "expanded" || sidebarState === "compact" || hovered;

  const handleItemClick = (type) => {
    togglePanel(type);
    if (sidebarState === "hidden") {
      setTimeout(() => setHovered(false), 300);
    }
  };

  // Expanded state — full sidebar with labels
  if (isVisible && sidebarState === "expanded") {
    return (
      <nav
        className="devos-sidebar devos-sidebar-expanded"
        style={{ width: LAYOUT.sidebar.expanded }}
        role="navigation"
        aria-label="Main navigation"
        onMouseLeave={() => sidebarState === "hidden" && setHovered(false)}
      >
        <div className="devos-sidebar-header">
          <div className="devos-sidebar-logo">
            <span className="devos-sidebar-logo-text">DevOS</span>
          </div>
          <button
            onClick={() => setSidebarState("compact")}
            title="Collapse to icons"
            aria-label="Collapse sidebar"
            className="devos-sidebar-toggle"
          >
            <ChevronLeft size={14} />
          </button>
        </div>

        <div className="devos-sidebar-search">
          <Search size={13} className="devos-sidebar-search-icon" />
          <input
            type="text"
            placeholder="Search..."
            className="devos-sidebar-search-input"
            aria-label="Search panels"
          />
        </div>

        <div className="devos-sidebar-items">
          {sidebarItems.map((item) => {
            const Icon = ICON_MAP[item.id] || ChevronRight;
            const panel = panels.find((p) => p.type === item.id);
            const isActive = panel && panel.state !== "hidden";
            const notif = panelNotifications[panel?.id];
            return (
              <button
                key={item.id}
                onClick={() => handleItemClick(item.id)}
                title={item.name}
                className={`devos-sidebar-item ${isActive ? "active" : ""}`}
                aria-label={item.name}
              >
                <Icon size={16} />
                <span className="devos-sidebar-item-label">{item.name}</span>
                {notif > 0 && <span className="devos-sidebar-badge">{notif}</span>}
              </button>
            );
          })}
        </div>

        <div className="devos-sidebar-footer">
          <button
            onClick={() => setSettingsOpen(true)}
            title="Settings"
            aria-label="Settings"
            className="devos-sidebar-item"
          >
            <Settings size={16} />
            <span className="devos-sidebar-item-label">Settings</span>
          </button>
        </div>
      </nav>
    );
  }

  // Compact state — icons only
  if (isVisible && sidebarState === "compact") {
    return (
      <nav
        className="devos-sidebar devos-sidebar-compact"
        style={{ width: LAYOUT.sidebar.compact }}
        role="navigation"
        aria-label="Main navigation"
        onMouseLeave={() => sidebarState === "hidden" && setHovered(false)}
      >
        <button
          onClick={() => setSidebarState("hidden")}
          title="Hide sidebar"
          aria-label="Hide sidebar"
          className="devos-sidebar-toggle-compact"
        >
          <ChevronLeft size={14} />
        </button>

        <div className="devos-sidebar-items-compact">
          {sidebarItems.map((item) => {
            const Icon = ICON_MAP[item.id] || ChevronRight;
            const panel = panels.find((p) => p.type === item.id);
            const isActive = panel && panel.state !== "hidden";
            const notif = panelNotifications[panel?.id];
            return (
              <button
                key={item.id}
                onClick={() => handleItemClick(item.id)}
                title={item.name}
                className={`devos-sidebar-item-compact ${isActive ? "active" : ""}`}
                aria-label={item.name}
                onMouseEnter={() => setShowTooltip(item.id)}
                onMouseLeave={() => setShowTooltip(null)}
              >
                <Icon size={16} />
                {notif > 0 && <span className="devos-sidebar-badge-compact">{notif}</span>}
                {showTooltip === item.id && (
                  <span className="devos-sidebar-tooltip">{item.name}</span>
                )}
              </button>
            );
          })}
        </div>

        <button
          onClick={() => setSettingsOpen(true)}
          title="Settings"
          aria-label="Settings"
          className="devos-sidebar-item-compact"
        >
          <Settings size={16} />
        </button>

        <button
          onClick={() => setSidebarState("expanded")}
          title="Expand sidebar"
          aria-label="Expand sidebar"
          className="devos-sidebar-toggle-compact"
        >
          <ChevronRight size={14} />
        </button>
      </nav>
    );
  }

  // Hidden state — hover-to-reveal strip
  return (
    <div
      className="devos-sidebar-hidden-strip"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      role="navigation"
      aria-label="Sidebar (hover to reveal)"
    >
      {hovered && (
        <nav
          className="devos-sidebar devos-sidebar-compact devos-sidebar-hover-reveal"
          style={{ width: LAYOUT.sidebar.compact }}
        >
          <button
            onClick={() => setSidebarState("expanded")}
            title="Expand sidebar"
            aria-label="Expand sidebar"
            className="devos-sidebar-toggle-compact"
          >
            <ChevronRight size={14} />
          </button>
          {sidebarItems.map((item) => {
            const Icon = ICON_MAP[item.id] || ChevronRight;
            const panel = panels.find((p) => p.type === item.id);
            const isActive = panel && panel.state !== "hidden";
            const notif = panelNotifications[panel?.id];
            return (
              <button
                key={item.id}
                onClick={() => handleItemClick(item.id)}
                title={item.name}
                className={`devos-sidebar-item-compact ${isActive ? "active" : ""}`}
                aria-label={item.name}
              >
                <Icon size={16} />
                {notif > 0 && <span className="devos-sidebar-badge-compact">{notif}</span>}
              </button>
            );
          })}
          <button
            onClick={() => setSettingsOpen(true)}
            title="Settings"
            aria-label="Settings"
            className="devos-sidebar-item-compact"
          >
            <Settings size={16} />
          </button>
        </nav>
      )}
    </div>
  );
}
