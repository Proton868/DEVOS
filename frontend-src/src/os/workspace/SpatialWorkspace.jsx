/**
 * SpatialWorkspace — ONE workspace. Canvas is primary; IDE / Copilot /
 * Inspector appear as attached spatial focus surfaces; Ghost Terminal
 * appears contextually beneath; Agency Dashboard floats as HUD.
 */
import React from "react";
import useOsStore from "../store/osStore";
import OrchestrationCanvas from "../canvas/OrchestrationCanvas";
import DevOSIde from "../focus/DevOSIde";
import AICopilot from "../focus/AICopilot";
import AgentInspector from "../focus/AgentInspector";
import GhostTerminal from "../terminal/GhostTerminal";
import AgencyDashboard from "../dashboard/AgencyDashboard";

export default function SpatialWorkspace({ isMobile }) {
  const { editor, copilot, inspector, overlay } = useOsStore();
  const focusOpen = (editor.open || copilot.open || inspector.open) && !overlay;

  return (
    <div className="sp-work">
      <div className="sp-canvas-region">
        <OrchestrationCanvas />
        {!overlay && <AgencyDashboard />}
        {!isMobile && overlay === null && null}
      </div>

      {focusOpen && (
        <div className={`sp-focus-col ${isMobile ? "mobile" : ""}`}>
          {editor.open && <DevOSIde onClose={() => { if (!copilot.open && !inspector.open) useOsStore.getState().closeEditor(); }} />}
          {inspector.open && <AgentInspector />}
          {copilot.open && <AICopilot />}
        </div>
      )}

      {!overlay && <GhostTerminal />}
    </div>
  );
}