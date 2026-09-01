/**
 * DevOS Panel — Base Component
 * Every panel (Workflow, IDE, Chat, Terminal, etc.) wraps its content in this component.
 * Provides:
 *   - Header with title, controls (dock, float, pin, hide, fullscreen, close)
 *   - Draggable via react-dnd
 *   - Resizable via react-resizable-panels
 *   - State management via panelStore
 *   - Accessibility (ARIA, keyboard, focus management)
 */
import React, { useRef, useEffect, useCallback } from "react";
import { useDrag } from "react-dnd";
import {
  X, Pin, Maximize2, Minimize2, Square,
  PanelLeft, PanelRight, PanelBottom,
} from "lucide-react";
import { usePanelStore } from "../../store/panelStore";
import { panelRegistry } from "./panelRegistry";
import { PANEL_STATES } from "../../theme/tokens";

export const PANEL_DRAG_TYPE = "DEVOS_PANEL";

/**
 * Panel Header — title bar with window controls.
 */
function PanelHeader({ panel, def, isActive }) {
  const {
    closePanel, hidePanel, floatPanel, pinPanel,
    fullscreenPanel, restorePanel, dockPanel, focusPanel,
  } = usePanelStore();

  const Icon = panelRegistry.get(panel.type) && panelRegistry.get(panel.type).icon;
  const isFloating = panel.state === PANEL_STATES.FLOATING;
  const isPinned = panel.state === PANEL_STATES.PINNED;
  const isFullscreen = panel.state === PANEL_STATES.FULLSCREEN;

  return (
    <div
      className={"devos-panel-header" + (isActive ? " active" : "")}
      role="toolbar"
      aria-label={(def && def.name || panel.type) + " panel controls"}
    >
      <div className="devos-panel-title">
        {Icon && <Icon size={13} />}
        <span>{def && def.name || panel.type}</span>
        {panel.config && panel.config.badge && (
          <span className="devos-panel-badge">{panel.config.badge}</span>
        )}
      </div>
      <div className="devos-panel-controls">
        {!isFullscreen && (
          <>
            {/* Dock left */}
            <button
              onClick={() => dockPanel(panel.id, "left")}
              title="Dock left"
              aria-label="Dock panel left"
              className="devos-panel-btn"
            >
              <PanelLeft size={13} />
            </button>
            {/* Dock bottom */}
            <button
              onClick={() => dockPanel(panel.id, "bottom")}
              title="Dock bottom"
              aria-label="Dock panel bottom"
              className="devos-panel-btn"
            >
              <PanelBottom size={13} />
            </button>
            {/* Dock right */}
            <button
              onClick={() => dockPanel(panel.id, "right")}
              title="Dock right"
              aria-label="Dock panel right"
              className="devos-panel-btn"
            >
              <PanelRight size={13} />
            </button>
            {/* Float */}
            <button
              onClick={() => {
                if (isFloating) dockPanel(panel.id, "center");
                else floatPanel(panel.id, { x: 200, y: 150 });
              }}
              title={isFloating ? "Dock panel" : "Float panel"}
              aria-label={isFloating ? "Dock panel" : "Float panel"}
              className="devos-panel-btn"
            >
              <Square size={13} />
            </button>
            {/* Pin */}
            <button
              onClick={() => isPinned ? restorePanel(panel.id) : pinPanel(panel.id)}
              title={isPinned ? "Unpin panel" : "Pin panel"}
              aria-label={isPinned ? "Unpin panel" : "Pin panel"}
              className={"devos-panel-btn" + (isPinned ? " active" : "")}
            >
              <Pin size={13} />
            </button>
            {/* Fullscreen */}
            <button
              onClick={() => fullscreenPanel(panel.id)}
              title="Fullscreen panel"
              aria-label="Fullscreen panel"
              className="devos-panel-btn"
            >
              <Maximize2 size={13} />
            </button>
            {/* Hide */}
            <button
              onClick={() => hidePanel(panel.id)}
              title="Hide panel"
              aria-label="Hide panel"
              className="devos-panel-btn"
            >
              <Minimize2 size={13} />
            </button>
          </>
        )}
        {isFullscreen && (
          <button
            onClick={() => restorePanel(panel.id)}
            title="Exit fullscreen"
            aria-label="Exit fullscreen"
            className="devos-panel-btn"
          >
            <Minimize2 size={13} />
          </button>
        )}
        {/* Close */}
        <button
          onClick={() => closePanel(panel.id)}
          title="Close panel"
          aria-label="Close panel"
          className="devos-panel-btn close"
        >
          <X size={13} />
        </button>
      </div>
    </div>
  );
}

/**
 * Panel — The main panel component.
 * @param {object} panel - Panel instance from panelStore
 * @param {React.ReactNode} children - Panel content
 */
export const Panel = React.memo(function Panel({ panel, children }) {
  const panelRef = useRef(null);
  const activePanelId = usePanelStore((s) => s.activePanelId);
  const focusPanel = usePanelStore((s) => s.focusPanel);
  const def = panelRegistry.get(panel.type);
  const isActive = activePanelId === panel.id;

  // Drag behavior for floating panels
  const [{ isDragging }, dragRef] = useDrag(() => ({
    type: PANEL_DRAG_TYPE,
    item: { id: panel.id, type: panel.type },
    collect: (monitor) => ({ isDragging: monitor.isDragging() }),
  }), [panel.id, panel.type]);

  // Focus management
  const handleFocus = useCallback(() => {
    focusPanel(panel.id);
  }, [panel.id, focusPanel]);

  // Keyboard: Escape to exit fullscreen
  useEffect(() => {
    if (panel.state !== PANEL_STATES.FULLSCREEN) return;
    const handler = (e) => {
      if (e.key === "Escape") {
        usePanelStore.getState().restorePanel(panel.id);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [panel.id, panel.state]);

  if (panel.state === PANEL_STATES.HIDDEN) return null;

  const isFloating = panel.state === PANEL_STATES.FLOATING || panel.state === PANEL_STATES.PINNED;
  const isFullscreen = panel.state === PANEL_STATES.FULLSCREEN;

  // Fullscreen mode
  if (isFullscreen) {
    return (
      <div
        ref={panelRef}
        className="devos-panel devos-panel-fullscreen"
        role="dialog"
        aria-modal="true"
        aria-label={(def && def.name || panel.type) + " — fullscreen"}
        onFocus={handleFocus}
        tabIndex={-1}
        style={{ zIndex: panel.zIndex }}
      >
        <PanelHeader panel={panel} def={def} isActive={isActive} />
        <div className="devos-panel-body">{children}</div>
      </div>
    );
  }

  // Floating mode
  if (isFloating) {
    return (
      <div
        ref={(node) => {
          panelRef.current = node;
          dragRef(node);
        }}
        className={"devos-panel devos-panel-floating" + (isDragging ? " dragging" : "")}
        role="dialog"
        aria-label={(def && def.name || panel.type) + " — floating"}
        onFocus={handleFocus}
        tabIndex={-1}
        style={{
          left: panel.position.x,
          top: panel.position.y,
          width: panel.size.width,
          height: panel.size.height,
          zIndex: panel.zIndex,
        }}
      >
        <PanelHeader panel={panel} def={def} isActive={isActive} />
        <div className="devos-panel-body">{children}</div>
      </div>
    );
  }

  // Docked mode
  return (
    <div
      ref={panelRef}
      className={"devos-panel devos-panel-docked" + (isActive ? " active" : "")}
      role="region"
      aria-label={def && def.name || panel.type}
      onFocus={handleFocus}
      tabIndex={-1}
    >
      <PanelHeader panel={panel} def={def} isActive={isActive} />
      <div className="devos-panel-body">{children}</div>
    </div>
  );
});

export default Panel;
