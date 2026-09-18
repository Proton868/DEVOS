/**
 * SpatialWorkspace — renders surfaces according to the spatial engine view-model.
 * Does not redesign individual tools; adapts layout from constraints.
 */
import React, { useCallback, useMemo } from "react";
import useOsStore from "../store/osStore";
import FlowDimension from "../flow/FlowDimension";
import DevOSIde from "../focus/DevOSIde";
import AICopilot from "../focus/AICopilot";
import AgentInspector from "../focus/AgentInspector";
import GhostTerminal from "../terminal/GhostTerminal";
import AgencyDashboard from "../dashboard/AgencyDashboard";
import MissionGlowOverlay from "../canvas/MissionGlowOverlay";
import WebIntelSurface from "../surfaces/WebIntelSurface";
import PreviewSurface from "./PreviewSurface";
import { Code2, MessageSquare, Eye, Workflow, Maximize2, Minimize2 } from "lucide-react";
import {
  resolveLayout,
  openIdsFromLegacyFlags,
  normalizeActiveId,
} from "../spatial/resolveLayout";
import { PRIMARY_NAV_ORDER } from "../spatial/registry";
import { useSpatialViewport } from "../spatial/useSpatialViewport";

export default function SpatialWorkspace() {
  const webIntelOpen = useOsStore((s) => s.webIntel?.open);
  const {
    editor,
    copilot,
    inspector,
    overlay,
    chatMode,
    terminal,
    layout,
    preview,
  } = useOsStore();
  const setFocusCollapsed = useOsStore((s) => s.setFocusCollapsed);
  const setFocusWidthPct = useOsStore((s) => s.setFocusWidthPct);
  const openEditor = useOsStore((s) => s.openEditor);
  const openCopilot = useOsStore((s) => s.openCopilot);
  const restorePreview = useOsStore((s) => s.restorePreview);
  const setActiveWorkspace = useOsStore((s) => s.setActiveWorkspace);
  const spatialFullscreen = useOsStore((s) => s.spatialFullscreenId);
  const setSpatialFullscreen = useOsStore((s) => s.setSpatialFullscreenId);
  const setSpatialMeta = useOsStore((s) => s.setSpatialMeta);

  const viewport = useSpatialViewport();

  const openIds = useMemo(
    () =>
      openIdsFromLegacyFlags({
        copilotOpen: !!copilot?.open,
        editorOpen: !!editor?.open,
        previewOpen: !!(preview?.open && !preview?.minimized),
        terminalOpen: !!(terminal?.open || terminal?.pinned),
        inspectorOpen: !!inspector?.open,
        filesOpen: overlay === "files",
      }),
    [copilot?.open, editor?.open, preview?.open, preview?.minimized, terminal?.open, terminal?.pinned, inspector?.open, overlay]
  );

  const activeId = normalizeActiveId(layout?.activeWorkspace || "chat");

  const plan = useMemo(() => {
    const collapsedIds = [];
    if (layout?.focusCollapsed) {
      if (editor?.open) collapsedIds.push("ide");
      if (copilot?.open && chatMode === "docked") collapsedIds.push("chat");
    }
    return resolveLayout(
      viewport,
      {
        activeId,
        openIds,
        collapsedIds,
        fullscreenId: spatialFullscreen || null,
        overlayId: overlay || null,
        widthPct: { focus: layout?.focusWidthPct || 62 },
      },
      { railWidth: 52, missionBarHeight: 44 }
    );
  }, [
    viewport,
    activeId,
    openIds,
    layout?.focusCollapsed,
    layout?.focusWidthPct,
    spatialFullscreen,
    overlay,
    editor?.open,
    copilot?.open,
    chatMode,
  ]);

  // Publish plan meta for chrome / debug (no second layout system)
  React.useEffect(() => {
    setSpatialMeta?.({
      presentation: plan.presentation,
      activeId: plan.activeId,
      orientation: plan.orientation,
      showDimensionNav: plan.showDimensionNav,
      visibleIds: plan.visibleIds,
    });
  }, [plan.presentation, plan.activeId, plan.orientation, plan.showDimensionNav, plan.visibleIds, setSpatialMeta]);

  const dockedChat = copilot.open && chatMode === "docked";
  const floatingChat = copilot.open && chatMode === "floating";

  const ideMode = plan.surfaces.ide?.mode;
  const chatModeSurface = plan.surfaces.chat?.mode;
  const flowMode = plan.surfaces.flow?.mode;

  const showIde =
    editor.open &&
    (ideMode === "visible" || ideMode === "fullscreen") &&
    !layout?.focusCollapsed;
  const showDockedChat =
    dockedChat &&
    (chatModeSurface === "visible" || chatModeSurface === "fullscreen") &&
    !layout?.focusCollapsed;
  const flowHostsInspector =
    flowVisible &&
    (plan.activeId === "flow" || plan.presentation === "stack" || !focusOpen);
  const showInspector =
    inspector.open &&
    !flowHostsInspector &&
    (plan.surfaces.inspector?.mode === "visible" ||
      plan.surfaces.inspector?.mode === "overlay" ||
      plan.surfaces.inspector?.mode === "fullscreen");

  const focusOpen = showIde || showDockedChat || showInspector;
  const stack = plan.presentation === "stack";
  const ideDominant = focusOpen && showIde && !stack;

  const onResizeStart = useCallback(
    (e) => {
      if (stack) return;
      e.preventDefault();
      const startX = e.clientX;
      const startPct = layout?.focusWidthPct || plan.focusWidthPct || 62;
      const onMove = (ev) => {
        const dx = startX - ev.clientX;
        const pct = startPct + (dx / window.innerWidth) * 100;
        setFocusWidthPct(pct);
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [stack, layout?.focusWidthPct, plan.focusWidthPct, setFocusWidthPct]
  );

  const focusPct = layout?.focusWidthPct || plan.focusWidthPct || 62;
  const focusStyle =
    !stack && focusOpen
      ? {
          width: `${focusPct}%`,
          minWidth: 280,
          maxWidth: "85%",
        }
      : stack && focusOpen
        ? { flex: 1, width: "100%", minWidth: 0 }
        : undefined;

  const switchDimension = (id) => {
    setActiveWorkspace?.(id === "flow" ? "canvas" : id);
    if (id === "chat") {
      setFocusCollapsed(false);
      openCopilot?.();
    } else if (id === "ide") {
      setFocusCollapsed(false);
      if (!editor?.open) openEditor?.({ file: null });
    } else if (id === "preview") {
      restorePreview?.();
    } else if (id === "flow") {
      setFocusCollapsed(true);
    }
  };

  const toggleFullscreenActive = () => {
    if (spatialFullscreen) setSpatialFullscreen(null);
    else setSpatialFullscreen(plan.activeId);
  };

  const flowVisible =
    flowMode === "visible" ||
    flowMode === "fullscreen" ||
    (!focusOpen && !spatialFullscreen);

  return (
    <div
      className={`sp-workspace ${stack ? "sp-workspace--stack" : "sp-workspace--split"}`}
      data-spatial-presentation={plan.presentation}
      data-spatial-orientation={plan.orientation}
      data-spatial-active={plan.activeId}
    >
      {flowVisible && (
        <div
          className={`sp-canvas-layer ${
            focusOpen && !stack ? "sp-canvas-layer--with-focus" : ""
          } ${flowMode === "fullscreen" || plan.activeId === "flow" ? "sp-dim-fullscreen" : ""}`}
        >
          <FlowDimension active={flowVisible} />
          <MissionGlowOverlay />
          {layout?.fleetCollapsed === false && <AgencyDashboard />}
        </div>
      )}

      {focusOpen && (
        <>
          {!stack && (
            <div
              className="sp-resize-handle"
              onMouseDown={onResizeStart}
              role="separator"
              aria-orientation="vertical"
            />
          )}
          <div
            className={`sp-focus-col ${stack ? "mobile stack" : ""} ${
              ideDominant ? "sp-focus-col--primary" : ""
            }`}
            style={focusStyle}
          >
            <div className="sp-dim-toolbar">
              <button
                type="button"
                className="sp-edge-tab"
                onClick={toggleFullscreenActive}
                title={spatialFullscreen ? "Exit fullscreen" : "Fullscreen dimension"}
              >
                {spatialFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                <span>{spatialFullscreen ? "Exit" : "Full"}</span>
              </button>
            </div>
            {showIde && (
              <DevOSIde
                onCollapse={() => setFocusCollapsed(true)}
                onClose={() => {
                  if (!dockedChat && !inspector.open) useOsStore.getState().closeEditor();
                }}
              />
            )}
            {showInspector && <AgentInspector />}
            {showDockedChat && <AICopilot />}
          </div>
        </>
      )}

      {/* Collapsed dimension dock */}
      {(plan.collapsedIds?.length > 0 || layout?.focusCollapsed) && !overlay && (
        <div className="sp-edge-dock" role="toolbar" aria-label="Collapsed workspace dimensions">
          {(editor.open || plan.collapsedIds?.includes("ide")) && (
            <button
              type="button"
              className="sp-edge-tab"
              onClick={() => switchDimension("ide")}
              title="Restore IDE"
            >
              <Code2 size={14} />
              <span>IDE</span>
            </button>
          )}
          {(dockedChat || plan.collapsedIds?.includes("chat")) && (
            <button
              type="button"
              className="sp-edge-tab"
              onClick={() => switchDimension("chat")}
              title="Restore Nuha"
            >
              <MessageSquare size={14} />
              <span>Nuha</span>
            </button>
          )}
          {plan.collapsedIds?.includes("flow") && (
            <button
              type="button"
              className="sp-edge-tab"
              onClick={() => switchDimension("flow")}
              title="Flow"
            >
              <Workflow size={14} />
              <span>Flow</span>
            </button>
          )}
          {(preview?.minimized || plan.collapsedIds?.includes("preview")) && (
            <button
              type="button"
              className="sp-edge-tab"
              onClick={() => switchDimension("preview")}
              title="Preview"
            >
              <Eye size={14} />
              <span>Preview</span>
            </button>
          )}
        </div>
      )}

      {/* Stack / constrained navigation between primary dimensions */}
      {plan.showDimensionNav && (
        <div className="sp-mobile-switcher sp-dimension-nav" role="tablist" aria-label="Workspace dimensions">
          {PRIMARY_NAV_ORDER.map((id) => {
            const open = openIds.includes(id) || id === "flow";
            if (!open && id !== plan.activeId) return null;
            const label =
              id === "chat" ? "Nuha" : id === "flow" ? "Flow" : id === "ide" ? "IDE" : "Preview";
            const Icon =
              id === "chat" ? MessageSquare : id === "flow" ? Workflow : id === "ide" ? Code2 : Eye;
            return (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={plan.activeId === id}
                className={`sp-edge-tab ${plan.activeId === id ? "sp-edge-tab--active" : ""}`}
                onClick={() => switchDimension(id)}
              >
                <Icon size={14} />
                <span>{label}</span>
              </button>
            );
          })}
        </div>
      )}

      {/* Preview dimension — first-class spatial surface */}
      {preview?.open &&
        !preview?.minimized &&
        ["visible", "fullscreen"].includes(plan.surfaces?.preview?.mode) && (
        <div
          className={`sp-preview-slot ${
            plan.surfaces?.preview?.mode === "fullscreen" || plan.activeId === "preview"
              ? "sp-preview-slot--primary"
              : ""
          }`}
          style={
            plan.presentation === "split" &&
            plan.surfaces?.preview?.mode === "visible" &&
            plan.surfaces?.preview?.widthPx
              ? { width: plan.surfaces.preview.widthPx, flex: "0 0 auto" }
              : plan.presentation === "stack" && plan.activeId === "preview"
                ? { flex: 1, minWidth: 0 }
                : undefined
          }
        >
          <PreviewSurface embedded />
        </div>
      )}

      {floatingChat && (
        <div className="sp-chat-float">
          <AICopilot floating />
        </div>
      )}

      {!overlay && (terminal.open || terminal.pinned) && <GhostTerminal />}
      {webIntelOpen && <WebIntelSurface />}
    </div>
  );
}
