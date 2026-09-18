/**
 * Flow / Automation — first-class Spatial OS dimension.
 * Canvas is primary; inspector/logs adapt (beside / overlay / drawer / fullscreen).
 */
import React, { useEffect, useRef, useState } from "react";
import {
  Workflow, Maximize2, Minimize2, PanelRightOpen, X,
} from "lucide-react";
import OrchestrationCanvas from "../canvas/OrchestrationCanvas";
import AgentInspector from "../focus/AgentInspector";
import useOsStore from "../store/osStore";
import { resolveFlowLayout } from "./flowLayout";

export default function FlowDimension({ active = true }) {
  const inspector = useOsStore((s) => s.inspector);
  const closeInspector = useOsStore((s) => s.closeInspector);
  const openInspector = useOsStore((s) => s.openInspector);
  const terminal = useOsStore((s) => s.terminal);
  const openTerminal = useOsStore((s) => s.openTerminal);
  const closeTerminal = useOsStore((s) => s.closeTerminal);
  const setActiveWorkspace = useOsStore((s) => s.setActiveWorkspace);
  const spatialFullscreenId = useOsStore((s) => s.spatialFullscreenId);
  const setSpatialFullscreenId = useOsStore((s) => s.setSpatialFullscreenId);
  const selectedNode = useOsStore((s) => s.selectedNode);

  const rootRef = useRef(null);
  const [size, setSize] = useState({ width: 1000, height: 700 });

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (cr) {
        setSize({
          width: cr.width,
          height: cr.height,
          orientation: cr.height >= cr.width ? "portrait" : "landscape",
        });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const plan = resolveFlowLayout(size, {
    inspectorOpen: !!inspector?.open,
    logsOpen: !!(terminal?.open || terminal?.pinned),
  });

  const toggleFullscreen = () => {
    if (spatialFullscreenId === "flow") setSpatialFullscreenId(null);
    else {
      setActiveWorkspace?.("canvas");
      setSpatialFullscreenId("flow");
    }
  };

  if (!active) return null;

  return (
    <div
      className="sp-flow-dimension"
      ref={rootRef}
      data-flow-inspector={plan.inspectorMode}
      data-flow-chrome={plan.chromeDensity}
    >
      <div className="sp-flow-toolbar">
        <div className="sp-flow-title">
          <Workflow size={14} />
          <span>Flow</span>
          <span className="sp-flow-sub">Automation</span>
        </div>
        <div className="sp-flow-actions">
          <button
            type="button"
            className="sp-pv-btn"
            title="Inspect selected node"
            onClick={() => {
              if (inspector?.open) closeInspector();
              else openInspector(selectedNode || null);
            }}
          >
            <PanelRightOpen size={14} />
            {plan.chromeDensity === "large" && <span>Inspector</span>}
          </button>
          <button
            type="button"
            className="sp-pv-btn"
            title="Execution logs"
            onClick={() => {
              if (terminal?.open) closeTerminal();
              else openTerminal({});
            }}
          >
            {plan.chromeDensity === "large" ? "Logs" : "☰"}
          </button>
          <button type="button" className="sp-pv-btn" title="Fullscreen flow" onClick={toggleFullscreen}>
            {spatialFullscreenId === "flow" ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
      </div>

      <div className="sp-flow-body">
        {plan.showCanvas && (
          <div className="sp-flow-canvas-host">
            <OrchestrationCanvas touchEnabled />
          </div>
        )}

        {plan.inspectorMode === "beside" && inspector?.open && (
          <div className="sp-flow-inspector sp-flow-inspector--beside" style={{ width: plan.inspectorWidth }}>
            <AgentInspector />
          </div>
        )}

        {plan.inspectorMode === "overlay" && inspector?.open && (
          <div className="sp-flow-inspector sp-flow-inspector--overlay" style={{ width: plan.inspectorWidth }}>
            <AgentInspector />
          </div>
        )}

        {plan.inspectorMode === "drawer" && inspector?.open && (
          <div className="sp-flow-inspector sp-flow-inspector--drawer" style={{ height: plan.drawerHeight }}>
            <div className="sp-flow-drawer-head">
              <span>Inspector</span>
              <button type="button" className="sp-iconbtn" onClick={closeInspector} title="Close">
                <X size={14} />
              </button>
            </div>
            <AgentInspector />
          </div>
        )}

        {plan.inspectorMode === "fullscreen" && inspector?.open && (
          <div className="sp-flow-inspector sp-flow-inspector--fullscreen">
            <div className="sp-flow-drawer-head">
              <span>Inspector</span>
              <button type="button" className="sp-iconbtn" onClick={closeInspector} title="Back to canvas">
                <X size={14} />
              </button>
            </div>
            <AgentInspector />
          </div>
        )}
      </div>
    </div>
  );
}
