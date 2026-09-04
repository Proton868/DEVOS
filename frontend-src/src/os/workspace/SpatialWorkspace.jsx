/**
 * SpatialWorkspace — ONE workspace. Canvas is primary; IDE / Copilot /
 * Inspector appear as attached spatial focus surfaces; Ghost Terminal
 * appears contextually (or pinned via toggle); Agency Dashboard floats as HUD.
 * Chat can be docked in the focus column or floating/movable.
 */
import React from "react";
import useOsStore from "../store/osStore";
import OrchestrationCanvas from "../canvas/OrchestrationCanvas";
import DevOSIde from "../focus/DevOSIde";
import AICopilot from "../focus/AICopilot";
import AgentInspector from "../focus/AgentInspector";
import GhostTerminal from "../terminal/GhostTerminal";
import AgencyDashboard from "../dashboard/AgencyDashboard";
import MissionGlowOverlay from "../canvas/MissionGlowOverlay";

export default function SpatialWorkspace({ isMobile }) {
  const { editor, copilot, inspector, overlay, chatMode, terminal } = useOsStore();
  const dockedChat = copilot.open && chatMode === "docked";
  const floatingChat = copilot.open && chatMode === "floating";
  const focusOpen = (editor.open || dockedChat || inspector.open) && !overlay;

  return (
    <div className="sp-work">
      <div className="sp-canvas-region">
        <OrchestrationCanvas />
        {!overlay && <MissionGlowOverlay />}
        {!overlay && <AgencyDashboard />}
      </div>

      {focusOpen && (
        <div className={`sp-focus-col ${isMobile ? "mobile" : ""}`}>
          {editor.open && (
            <DevOSIde
              onClose={() => {
                if (!dockedChat && !inspector.open) useOsStore.getState().closeEditor();
              }}
            />
          )}
          {inspector.open && <AgentInspector />}
          {dockedChat && <AICopilot />}
        </div>
      )}

      {floatingChat && (
        <div className="sp-chat-float">
          <AICopilot floating />
        </div>
      )}

      {/* Ghost Terminal: open when contextual execution OR user toggled/pinned */}
      {!overlay && (terminal.open || terminal.pinned) && <GhostTerminal />}
    </div>
  );
}
