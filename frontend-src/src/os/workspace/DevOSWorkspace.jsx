/**
 * DevOSWorkspace — the single unified spatial shell.
 * MissionBar + CosmicSidebar + SpatialWorkspace + overlays + CommandBar.
 * Global shortcuts: CMD/CTRL+K or SPACE -> command bar; SHIFT+ENTER ->
 * contextual execution of the selected node into the Ghost Terminal.
 */
import React, { useEffect, useState, useCallback } from "react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api } from "../../services/api";
import MissionBar from "./MissionBar";
import CosmicSidebar from "./CosmicSidebar";
import SpatialWorkspace from "./SpatialWorkspace";
import OverlaySurface from "./OverlaySurface";
import PreviewSurface from "./PreviewSurface";
import CommandBar from "../command/CommandBar";

function useIsMobile() {
  const [m, setM] = useState(() => window.innerWidth <= 760);
  useEffect(() => {
    const h = () => setM(window.innerWidth <= 760);
    window.addEventListener("resize", h);
    return () => window.removeEventListener("resize", h);
  }, []);
  return m;
}

export default function DevOSWorkspace() {
  // Nuha-first: open primary AI conversation on authenticated workspace entry
  useEffect(() => {
    const st = useOsStore.getState();
    if (!st.copilot?.open) {
      st.setActivePersona?.("nuha");
      st.openCopilot(null, null, "nuha");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const isMobile = useIsMobile();
  const statusMessage = useStore((s) => s.statusMessage);
  const setCommandBar = useOsStore((s) => s.setCommandBar);
  const [graphKey, setGraphKey] = useState(0);

  // Rebuild graph when project changes (graph-changed event)
  useEffect(() => {
    const h = () => setGraphKey((k) => k + 1);
    window.addEventListener("devos:graph-changed", h);
    return () => window.removeEventListener("devos:graph-changed", h);
  }, []);

  // Global shortcuts
  const handleKey = useCallback((e) => {
    const mod = e.ctrlKey || e.metaKey;
    const inInput = ["INPUT", "TEXTAREA"].includes(e.target?.tagName) || e.target?.isContentEditable;

    if (mod && e.key.toLowerCase() === "k") {
      e.preventDefault();
      const open = useOsStore.getState().commandBar.open;
      setCommandBar(!open);
      return;
    }
    // SPACE opens the command interface when not typing anywhere
    if (e.key === " " && !inInput) {
      e.preventDefault();
      setCommandBar(true);
      return;
    }
    // SHIFT+ENTER: contextual execution of the selected node
    if (e.key === "Enter" && e.shiftKey && !inInput) {
      e.preventDefault();
      const st = useOsStore.getState();
      const node = st.nodes.find((n) => n.id === st.selectedNode);
      if (node?.scriptId != null) {
        st.setNodeState(node.scriptId, "EXECUTING");
        useStore.getState().setStatus(`Running ${node.title}…`);
        st.openTerminal(node.id);
        api.runFlowScript(node.scriptId)
          .then(() => useStore.getState().setStatus("Execution started"))
          .catch((err) => {
            st.setNodeState(node.scriptId, "FAILED");
            useStore.getState().setStatus("Run failed: " + err.message);
          });
      } else {
        useStore.getState().setStatus("Select a workflow node to execute (SHIFT+ENTER)");
      }
    }
  }, [setCommandBar]);

  useEffect(() => {
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [handleKey]);

  return (
    <div className="sp-root">
      <MissionBar />
      <div className="sp-body">
        <CosmicSidebar />
        <div style={{ flex: 1, position: "relative", display: "flex", flexDirection: "column", minWidth: 0 }}>
          <div style={{ flex: 1, position: "relative", display: "flex", minHeight: 0 }}>
            <SpatialWorkspace key={graphKey} isMobile={isMobile} />
            <PreviewSurface />
      <OverlaySurface isMobile={isMobile} />
          </div>
          <CommandBar reloadGraph={() => setGraphKey((k) => k + 1)} />
        </div>
      </div>
      {statusMessage && statusMessage !== "Ready" && (
        <div
          className="sp-glass"
          style={{
            position: "fixed", bottom: 16, left: "50%", transform: "translateX(-50%)",
            padding: "7px 16px", fontSize: 12, color: "var(--sp-text-1)", zIndex: 950,
            maxWidth: "70vw", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          }}
        >
          {statusMessage}
        </div>
      )}
    </div>
  );
}