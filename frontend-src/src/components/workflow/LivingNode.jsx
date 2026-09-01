/**
 * DevOS LivingNode — A Node with living animation states.
 * Extends the base Node with meaningful motion:
 *   - Idle: soft breathing
 *   - Thinking: gentle pulse
 *   - Executing: data flowing
 *   - Waiting: dim
 *   - Success: green ripple
 *   - Failed: red glow
 */
import React, { memo, useEffect, useState } from "react";
import Node, { NODE_STATES } from "./Node";
import { useThemeStore } from "../../store/themeStore";

const LivingNode = memo(function LivingNode(props) {
  const { node, ...rest } = props;
  const reducedMotion = useThemeStore((s) => s.reducedMotion);
  const state = node.state || NODE_STATES.IDLE;
  const [rippleKey, setRippleKey] = useState(0);

  // Trigger ripple animation when state transitions to success
  useEffect(() => {
    if (state === NODE_STATES.SUCCESS) {
      setRippleKey((k) => k + 1);
    }
  }, [state]);

  return (
    <div
      className={
        "devos-living-node devos-living-" + state +
        (reducedMotion ? " reduced-motion" : "")
      }
      data-node-state={state}
    >
      {/* Success ripple */}
      {state === NODE_STATES.SUCCESS && !reducedMotion && (
        <div key={rippleKey} className="devos-node-ripple" aria-hidden="true" />
      )}
      {/* Failed glow ring */}
      {state === NODE_STATES.FAILED && !reducedMotion && (
        <div className="devos-node-glow-ring" aria-hidden="true" />
      )}
      {/* Thinking pulse ring */}
      {state === NODE_STATES.THINKING && !reducedMotion && (
        <div className="devos-node-pulse-ring" aria-hidden="true" />
      )}
      <Node node={node} {...rest} />
    </div>
  );
});

export default LivingNode;
