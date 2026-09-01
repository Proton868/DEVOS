/**
 * DevOS DockableLayout — The main application shell.
 * Replaces the old fixed App.jsx layout with a fully dockable panel system.
 *
 * Structure:
 *   ┌─────────────────────────────────────────────┐
 *   │              MissionBar (top)                │
 *   ├──────┬──────────────────────────────────┬───┤
 *   │ Side │     PanelContainer (docks)        │   │
 *   │ bar  │  ┌──────┬───────────┬─────────┐  │   │
 *   │      │  │ Left │  Center   │  Right  │  │   │
 *   │      │  │      │           │         │  │   │
 *   │      │  │      ├───────────┤         │  │   │
 *   │      │  │      │  Bottom   │         │  │   │
 *   │      │  └──────┴───────────┴─────────┘  │   │
 *   └──────┴──────────────────────────────────┴───┘
 */
import React, { useEffect } from "react";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import MissionBar from "./MissionBar";
import ThreeStateSidebar from "../sidebar/ThreeStateSidebar";
import { PanelContainer } from "../panels/PanelContainer";
import { useGlobalShortcuts } from "../../hooks/useGlobalShortcuts";
import { useResponsive } from "../../hooks/useResponsive";
import { applyThemeToDOM } from "../../store/themeStore";
import { useWorkspaceStore } from "../../store/workspaceStore";
import { lazy, Suspense } from "react";

// Lazy load onboarding (only shows on first run)
const OnboardingWizard = lazy(() => import("../onboarding/OnboardingWizard"));

export default function DockableLayout() {
  // Initialize global shortcuts
  const { showCheatsheet, toggleCheatsheet, shortcuts } = useGlobalShortcuts();
  const { isMobile, isTablet } = useResponsive();
  const { sidebarState } = useWorkspaceStore();

  // Apply theme on mount
  useEffect(() => {
    applyThemeToDOM();
  }, []);

  // Auto-collapse sidebar on mobile
  useEffect(() => {
    if (isMobile && sidebarState === "expanded") {
      useWorkspaceStore.getState().setSidebarState("hidden");
    }
  }, [isMobile, sidebarState]);

  return (
    <DndProvider backend={HTML5Backend}>
      <div className="devos-shell" data-mobile={isMobile} data-tablet={isTablet}>
        {/* Top mission bar */}
        <MissionBar />

        {/* Main content area */}
        <div className="devos-main">
          {/* Three-state sidebar */}
          <ThreeStateSidebar />

          {/* Panel container with dock zones */}
          <main className="devos-content" role="main">
            <PanelContainer />
          </main>
        </div>

        {/* Keyboard shortcut cheatsheet overlay */}
        {showCheatsheet && (
          <ShortcutCheatsheet shortcuts={shortcuts} onClose={toggleCheatsheet} />
        )}

        {/* Onboarding wizard (first run only) */}
        <Suspense fallback={null}>
          <OnboardingWizard />
        </Suspense>
      </div>
    </DndProvider>
  );
}

/**
 * Keyboard shortcut cheatsheet overlay.
 */
function ShortcutCheatsheet({ shortcuts, onClose }) {
  return (
    <div
      className="devos-shortcut-overlay"
      onClick={onClose}
      role="dialog"
      aria-label="Keyboard shortcuts"
    >
      <div className="devos-shortcut-modal" onClick={(e) => e.stopPropagation()}>
        <div className="devos-shortcut-header">
          <h2>Keyboard Shortcuts</h2>
          <button onClick={onClose} className="devos-shortcut-close" aria-label="Close">✕</button>
        </div>
        <div className="devos-shortcut-list">
          {shortcuts.map((s) => (
            <div key={s.id} className="devos-shortcut-item">
              <span className="devos-shortcut-desc">{s.description || s.id}</span>
              <kbd className="devos-shortcut-key">{s.combo}</kbd>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
