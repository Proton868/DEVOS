/**
 * DevOS PlaceholderPanel
 * Placeholder for panels registered but not yet implemented.
 */
import React from "react";

export default function PlaceholderPanel({ panel }) {
  return (
    <div className="devos-placeholder-panel">
      <div className="devos-placeholder-content">
        <h3 className="text-base font-semibold mb-2" style={{ color: "var(--text-1)" }}>
          {panel ? panel.type : "Panel"}
        </h3>
        <p className="text-xs" style={{ color: "var(--text-3)" }}>
          This panel is registered but its content is coming in a later stage.
        </p>
      </div>
    </div>
  );
}
