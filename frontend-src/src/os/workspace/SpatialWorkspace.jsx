/**
 * SpatialWorkspace — ONE workspace. Active surface gets priority;
 * inactive tools collapse to recoverable edge docks (not fixed columns).
 */
import React, { useCallback, useRef } from "react";
import useOsStore from "../store/osStore";
import OrchestrationCanvas from "../canvas/OrchestrationCanvas";
import DevOSIde from "../focus/DevOSIde";
import AICopilot from "../focus/AICopilot";
import AgentInspector from "../focus/AgentInspector";
import GhostTerminal from "../terminal/GhostTerminal";
import AgencyDashboard from "../dashboard/AgencyDashboard";
import MissionGlowOverlay from "../canvas/MissionGlowOverlay";
import WebIntelSurface from "../surfaces/WebIntelSurface";
import { Code2, MessageSquare, Workflow, Eye } from "lucide-react";

export default function SpatialWorkspace({ isMobile }) {
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

  const dockedChat = copilot.open && chatMode === "docked";
  const floatingChat = copilot.open && chatMode === "floating";
  const focusWanted = editor.open || dockedChat || inspector.open;
  const focusCollapsed = !!layout?.focusCollapsed;
  const focusOpen = focusWanted && !focusCollapsed && !overlay;
  const ideDominant = focusOpen && editor.open && !isMobile;

  const onResizeStart = useCallback(
    (e) => {
      if (isMobile) return;
      e.preventDefault();
      const startX = e.clientX;
      const startPct = layout?.focusWidthPct || 62;
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
    [isMobile, layout?.focusWidthPct, setFocusWidthPct]
  );

  const focusStyle =
    !isMobile && focusOpen
      ? {
          width: `${layout?.focusWidthPct || 62}%`,
          minWidth: 280,
          maxWidth: "82%",
          flex: "0 0 auto",
        }
      : undefined;

  return (
    <div
      className={[
        "sp-work",
        ideDominant ? "sp-work--ide" : "",
        isMobile && focusOpen ? "sp-work--mobile-focus" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div
        className={[
          "sp-canvas-region",
          focusOpen && !isMobile ? "sp-canvas-region--secondary" : "",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        <OrchestrationCanvas />
        {!overlay && <MissionGlowOverlay />}
        {!overlay && <AgencyDashboard />}
      </div>

      {focusOpen && (
        <>
          {!isMobile && (
            <div
              className="sp-focus-resizer"
              onMouseDown={onResizeStart}
              title="Drag to resize workspace"
            />
          )}
          <div
            className={`sp-focus-col ${isMobile ? "mobile" : ""} ${
              ideDominant ? "sp-focus-col--primary" : ""
            }`}
            style={focusStyle}
          >
            {editor.open && (
              <DevOSIde
                onCollapse={() => setFocusCollapsed(true)}
                onClose={() => {
                  if (!dockedChat && !inspector.open) useOsStore.getState().closeEditor();
                }}
              />
            )}
            {inspector.open && <AgentInspector />}
            {dockedChat && <AICopilot />}
          </div>
        </>
      )}

      {focusWanted && focusCollapsed && !overlay && (
        <div className="sp-edge-dock" role="toolbar" aria-label="Collapsed workspace panels">
          {editor.open && (
            <button
              type="button"
              className="sp-edge-tab"
              onClick={() => {
                setFocusCollapsed(false);
                useOsStore.getState().setActiveWorkspace?.("ide");
              }}
              title="Restore IDE"
            >
              <Code2 size={14} />
              <span>IDE</span>
            </button>
          )}
          {dockedChat && (
            <button
              type="button"
              className="sp-edge-tab"
              onClick={() => {
                setFocusCollapsed(false);
                useOsStore.getState().setActiveWorkspace?.("chat");
              }}
              title="Restore Nuha"
            >
              <MessageSquare size={14} />
              <span>Nuha</span>
            </button>
          )}
        </div>
      )}

      {isMobile && focusOpen && (
        <div className="sp-mobile-switcher">
          <button type="button" className="sp-edge-tab" onClick={() => setFocusCollapsed(true)}>
            <Workflow size={14} />
            <span>Flow</span>
          </button>
          {!editor.open && (
            <button
              type="button"
              className="sp-edge-tab"
              onClick={() => openEditor({ file: "index.html" })}
            >
              <Code2 size={14} />
              <span>IDE</span>
            </button>
          )}
          {!copilot.open && (
            <button type="button" className="sp-edge-tab" onClick={() => openCopilot()}>
              <MessageSquare size={14} />
              <span>Nuha</span>
            </button>
          )}
          {preview?.minimized && (
            <button type="button" className="sp-edge-tab" onClick={() => restorePreview()}>
              <Eye size={14} />
              <span>Preview</span>
            </button>
          )}
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
