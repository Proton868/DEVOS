/**
 * DevOS PanelContainer — Manages rendering of all panels across dock zones.
 * Reads panel state from panelStore and renders each panel in its correct dock position.
 * Uses react-resizable-panels for docked panel resizing.
 */
import React, { Suspense, lazy, useMemo } from "react";
import { PanelGroup, Panel as ResizablePanel, PanelResizeHandle } from "react-resizable-panels";
import { usePanelStore } from "../../store/panelStore";
import { panelRegistry } from "./panelRegistry";
import { Panel } from "./Panel";
import { DockZone, FloatingDockZone } from "./DockZone";
import { PANEL_STATES } from "../../theme/tokens";

const Spin = () => (
  <div className="flex items-center justify-center h-full text-slate-400 text-xs">
    Loading...
  </div>
);

/**
 * Render a single panel's content using lazy loading.
 */
function PanelContent({ panel }) {
  const def = panelRegistry.get(panel.type);

  if (!def?.component) {
    return (
      <div className="devos-panel-empty">
        <p className="text-xs text-slate-500">Panel type "{panel.type}" not registered</p>
      </div>
    );
  }

  const Component = def.component;
  return (
    <Suspense fallback={<Spin />}>
      <Component panel={panel} config={panel.config} />
    </Suspense>
  );
}

/**
 * Render all panels in a specific dock position.
 */
function DockContent({ position }) {
  const panels = usePanelStore((s) => s.panels);
  const dockOrder = usePanelStore((s) => s.dockOrder);
  const ids = dockOrder[position] || [];
  const dockedPanels = ids
    .map((id) => panels.find((p) => p.id === id))
    .filter(Boolean)
    .filter((p) => p.state === PANEL_STATES.DOCKED);

  if (dockedPanels.length === 0) return null;

  return (
    <DockZone position={position} className="devos-dock-content">
      {dockedPanels.map((panel) => (
        <Panel key={panel.id} panel={panel}>
          <PanelContent panel={panel} />
        </Panel>
      ))}
    </DockZone>
  );
}

/**
 * Render all floating panels.
 */
function FloatingPanels() {
  const panels = usePanelStore((s) => s.panels);
  const floatingPanels = panels.filter(
    (p) => p.state === PANEL_STATES.FLOATING || p.state === PANEL_STATES.PINNED
  );

  if (floatingPanels.length === 0) return null;

  return (
    <div className="devos-floating-layer">
      {floatingPanels
        .sort((a, b) => (a.zIndex || 0) - (b.zIndex || 0))
        .map((panel) => (
          <Panel key={panel.id} panel={panel}>
            <PanelContent panel={panel} />
          </Panel>
        ))}
    </div>
  );
}

/**
 * Render the fullscreen panel overlay (if any).
 */
function FullscreenPanel() {
  const panels = usePanelStore((s) => s.panels);
  const panel = panels.find((p) => p.state === "fullscreen");

  if (!panel) return null;

  return (
    <div className="devos-fullscreen-layer">
      <Panel panel={panel}>
        <PanelContent panel={panel} />
      </Panel>
    </div>
  );
}

/**
 * PanelContainer — The main container that arranges all panels.
 * Uses a responsive panel group layout:
 *   [left | center | right] with optional top/bottom rows.
 */
export const PanelContainer = React.memo(function PanelContainer() {
  const panels = usePanelStore((s) => s.panels);
  const dockOrder = usePanelStore((s) => s.dockOrder);

  const hasLeft = (dockOrder.left || []).length > 0;
  const hasRight = (dockOrder.right || []).length > 0;
  const hasTop = (dockOrder.top || []).length > 0;
  const hasBottom = (dockOrder.bottom || []).length > 0;

  return (
    <FloatingDockZone>
      <div className="devos-panel-container">
        {/* Top dock (full width) */}
        {hasTop && (
          <div className="devos-dock-row devos-dock-top">
            <DockContent position="top" />
          </div>
        )}

        {/* Main row: left | center | right */}
        <div className="devos-dock-main">
          <PanelGroup direction="horizontal" autoSaveId="devos-main-horizontal">
            {hasLeft && (
              <>
                <ResizablePanel defaultSize={20} minSize={15} maxSize={40}>
                  <DockContent position="left" />
                </ResizablePanel>
                <PanelResizeHandle className="devos-resize-handle" />
              </>
            )}
            <ResizablePanel minSize={30}>
              <PanelGroup direction="vertical" autoSaveId="devos-center-vertical">
                <ResizablePanel minSize={20}>
                  <DockContent position="center" />
                </ResizablePanel>
                {hasBottom && (
                  <>
                    <PanelResizeHandle className="devos-resize-handle horizontal" />
                    <ResizablePanel defaultSize={30} minSize={10} maxSize={70}>
                      <DockContent position="bottom" />
                    </ResizablePanel>
                  </>
                )}
              </PanelGroup>
            </ResizablePanel>
            {hasRight && (
              <>
                <PanelResizeHandle className="devos-resize-handle" />
                <ResizablePanel defaultSize={25} minSize={15} maxSize={45}>
                  <DockContent position="right" />
                </ResizablePanel>
              </>
            )}
          </PanelGroup>
        </div>

        {/* Floating panels layer */}
        <FloatingPanels />

        {/* Fullscreen overlay */}
        <FullscreenPanel />
      </div>
    </FloatingDockZone>
  );
});

export default PanelContainer;
