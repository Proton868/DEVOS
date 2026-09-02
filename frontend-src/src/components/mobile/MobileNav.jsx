import React, { useState, useMemo } from "react";
import {
  Workflow, MessageSquare, Terminal, Bot, Menu,
  Code, FolderKanban, GitBranch, X, Search, Activity,
} from "lucide-react";
import { usePanelStore } from "../../store/panelStore";
import { PANEL_STATES } from "../../theme/tokens";

/**
 * Mobile bottom navigation — single-panel UX for viewports < 768px.
 * Opens/focuses panels via panelStore (not the legacy boolean flags).
 */
const PRIMARY_TABS = [
  { type: "workflow", label: "Flow", icon: Workflow },
  { type: "chat", label: "Chat", icon: MessageSquare },
  { type: "terminal", label: "Term", icon: Terminal },
  { type: "agents", label: "Agent", icon: Bot },
];

const MORE_ITEMS = [
  { type: "ide", label: "Editor", icon: Code },
  { type: "files", label: "Files", icon: FolderKanban },
  { type: "git", label: "Git", icon: GitBranch },
  { type: "logs", label: "Logs", icon: Activity },
  { type: "search", label: "Search", icon: Search },
  { type: "metrics", label: "Metrics", icon: Activity },
];

function activatePanel(type) {
  const store = usePanelStore.getState();
  const existing = store.panels.find((p) => p.type === type);

  // Batch: hide other visible panels, show/focus target
  usePanelStore.setState((s) => {
    let panels = s.panels.map((p) => {
      if (existing && p.id === existing.id) {
        return {
          ...p,
          state: PANEL_STATES.DOCKED,
          dock: "center",
        };
      }
      if (p.state === PANEL_STATES.HIDDEN) return p;
      // Keep target if we are about to open new one - hide others
      if (existing && p.id === existing.id) return p;
      return { ...p, state: PANEL_STATES.HIDDEN };
    });

    let dockOrder = {
      top: [],
      bottom: [],
      left: [],
      right: [],
      center: existing ? [existing.id] : [],
    };
    let activePanelId = existing ? existing.id : s.activePanelId;
    return { panels, dockOrder, activePanelId, focusedPanelId: activePanelId };
  });

  if (!existing) {
    store.openPanel(type, { dock: "center", state: PANEL_STATES.DOCKED });
  } else {
    // Ensure persistence
    try {
      const st = usePanelStore.getState();
      localStorage.setItem(
        "devos_panels",
        JSON.stringify({
          panels: st.panels,
          activePanelId: st.activePanelId,
          dockOrder: st.dockOrder,
        })
      );
    } catch (_) {}
  }
}

export default function MobileNav() {
  const [moreOpen, setMoreOpen] = useState(false);
  const panels = usePanelStore((s) => s.panels);
  const activePanelId = usePanelStore((s) => s.activePanelId);

  const activeType = useMemo(() => {
    const active = panels.find((p) => p.id === activePanelId);
    return active?.type || null;
  }, [panels, activePanelId]);

  const handleTab = (type) => {
    setMoreOpen(false);
    activatePanel(type);
  };

  return (
    <nav className="mobile-nav" role="navigation" aria-label="Main navigation">
      {PRIMARY_TABS.map((tab) => {
        const Icon = tab.icon;
        const active = activeType === tab.type;
        return (
          <button
            key={tab.type}
            type="button"
            className={`mobile-nav-tab${active ? " active" : ""}`}
            onClick={() => handleTab(tab.type)}
            aria-label={tab.label}
            aria-pressed={active}
          >
            <Icon size={20} strokeWidth={active ? 2.25 : 1.75} />
            <span className="mobile-nav-label">{tab.label}</span>
          </button>
        );
      })}
      <button
        type="button"
        className={`mobile-nav-tab${moreOpen ? " active" : ""}`}
        onClick={() => setMoreOpen((v) => !v)}
        aria-label="More panels"
        aria-expanded={moreOpen}
      >
        <Menu size={20} />
        <span className="mobile-nav-label">More</span>
      </button>

      {moreOpen && (
        <div className="mobile-more-menu" role="menu">
          <div className="mobile-more-header">
            <span>Panels</span>
            <button type="button" className="mobile-more-close-btn" onClick={() => setMoreOpen(false)} aria-label="Close">
              <X size={18} />
            </button>
          </div>
          <div className="mobile-more-grid">
            {MORE_ITEMS.map((item) => {
              const Icon = item.icon;
              const active = activeType === item.type;
              return (
                <button
                  key={item.type}
                  type="button"
                  role="menuitem"
                  className={`mobile-more-item${active ? " active" : ""}`}
                  onClick={() => handleTab(item.type)}
                >
                  <Icon size={18} />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </nav>
  );
}
