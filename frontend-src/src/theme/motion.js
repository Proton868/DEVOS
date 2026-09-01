/**
 * DevOS Motion System
 * Motion should explain, never decorate.
 */

export const MOTION_TOKENS = {
  // ── Durations ───────────────────────────────────────────
  duration: {
    instant: 80,      // Color changes, hover states
    fast: 150,        // Small UI transitions
    normal: 240,     // Standard panel transitions
    slow: 360,       // Layout changes
    deliberate: 500, // Significant state changes
    cinematic: 800,  // Camera zooms, large reveals
  },

  // ── Easings ──────────────────────────────────────────────
  easing: {
    standard: "cubic-bezier(0.4, 0.0, 0.2, 1)",
    decelerate: "cubic-bezier(0.0, 0.0, 0.2, 1)",
    accelerate: "cubic-bezier(0.4, 0.0, 1, 1)",
    spring: "cubic-bezier(0.34, 1.56, 0.64, 1)",
    linear: "linear",
  },

  // ── Stagger delays (for lists) ──────────────────────────
  stagger: {
    fast: 30,
    normal: 50,
    slow: 80,
  },

  // ── Living Node Animation Phases ────────────────────────
  nodeStates: {
    idle: {
      // Soft breathing - subtle opacity pulse
      keyframes: "devos-node-idle",
      duration: 3000,
      easing: "ease-in-out",
      iteration: "infinite",
      alternate: true,
    },
    thinking: {
      // Gentle pulse - faster breathing
      keyframes: "devos-node-thinking",
      duration: 1200,
      easing: "ease-in-out",
      iteration: "infinite",
      alternate: true,
    },
    executing: {
      // Data flowing - particle animation along edges
      keyframes: "devos-node-executing",
      duration: 600,
      easing: "linear",
      iteration: "infinite",
    },
    waiting: {
      // Dim state - reduced opacity, no animation
      opacity: 0.5,
    },
    success: {
      // Green ripple - expanding ring
      keyframes: "devos-node-success",
      duration: 800,
      easing: "cubic-bezier(0.0, 0.0, 0.2, 1)",
      iteration: 1,
    },
    failed: {
      // Red glow - pulsing red border
      keyframes: "devos-node-failed",
      duration: 600,
      easing: "ease-in-out",
      iteration: "infinite",
      alternate: true,
    },
  },

  // ── Panel Animations ─────────────────────────────────────
  panel: {
    open: {
      duration: 240,
      easing: "cubic-bezier(0.34, 1.56, 0.64, 1)",
    },
    close: {
      duration: 180,
      easing: "cubic-bezier(0.4, 0.0, 1, 1)",
    },
    dock: {
      duration: 300,
      easing: "cubic-bezier(0.0, 0.0, 0.2, 1)",
    },
    float: {
      duration: 240,
      easing: "cubic-bezier(0.34, 1.56, 0.64, 1)",
    },
    resize: {
      duration: 0, // Immediate, with throttled state updates
      easing: "linear",
    },
  },

  // ── Layout Transitions ──────────────────────────────────
  layout: {
    sidebar: {
      expand: {
        duration: 240,
        easing: "cubic-bezier(0.0, 0.0, 0.2, 1)",
      },
      collapse: {
        duration: 200,
        easing: "cubic-bezier(0.4, 0.0, 1, 1)",
      },
    },
    presetSwitch: {
      duration: 360,
      easing: "cubic-bezier(0.4, 0.0, 0.2, 1)",
    },
  },

  // ── Camera / Viewport Animations ─────────────────────────
  camera: {
    zoomIn: {
      // Open IDE: camera zoom
      duration: 500,
      easing: "cubic-bezier(0.0, 0.0, 0.2, 1)",
    },
    zoomOut: {
      // Close IDE: camera zoom back
      duration: 400,
      easing: "cubic-bezier(0.4, 0.0, 1, 1)",
    },
    focus: {
      // Click node: everything else softens
      duration: 300,
      easing: "cubic-bezier(0.0, 0.0, 0.2, 1)",
    },
  },

  // ── Data Flow ────────────────────────────────────────────
  dataFlow: {
    // Logs flow from node
    logStream: {
      duration: 400,
      easing: "cubic-bezier(0.0, 0.0, 0.2, 1)",
      stagger: 50,
    },
    // Workflow pan with inertia
    pan: {
      duration: 600,
      easing: "cubic-bezier(0.0, 0.0, 0.2, 1)",
    },
  },
};

// ── Keyframes as CSS string (injected into DOM) ───────────
export const KEYFRAMES_CSS = `
@keyframes devos-node-idle {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.85; transform: scale(0.99); }
}
@keyframes devos-node-thinking {
  0%, 100% { opacity: 1; transform: scale(1); box-shadow: 0 0 0 0 rgba(var(--accent-rgb), 0.3); }
  50% { opacity: 0.9; transform: scale(1.01); box-shadow: 0 0 12px 4px rgba(var(--accent-rgb), 0.2); }
}
@keyframes devos-node-executing {
  0% { stroke-dashoffset: 20; }
  100% { stroke-dashoffset: 0; }
}
@keyframes devos-node-success {
  0% { box-shadow: 0 0 0 0 rgba(63, 185, 80, 0.6); }
  100% { box-shadow: 0 0 0 24px rgba(63, 185, 80, 0); }
}
@keyframes devos-node-failed {
  0%, 100% { box-shadow: 0 0 0 0 rgba(248, 81, 73, 0); border-color: rgba(248, 81, 73, 0.4); }
  50% { box-shadow: 0 0 12px 2px rgba(248, 81, 73, 0.3); border-color: rgba(248, 81, 73, 0.8); }
}
@keyframes devos-data-particle {
  0% { offset-distance: 0%; opacity: 0; }
  10% { opacity: 1; }
  90% { opacity: 1; }
  100% { offset-distance: 100%; opacity: 0; }
}
@keyframes devos-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes devos-fade-out {
  from { opacity: 1; }
  to { opacity: 0; }
}
@keyframes devos-slide-up {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes devos-slide-down {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes devos-scale-in {
  from { opacity: 0; transform: scale(0.96); }
  to { opacity: 1; transform: scale(1); }
}
@keyframes devos-ripple {
  0% { transform: scale(0); opacity: 0.5; }
  100% { transform: scale(2); opacity: 0; }
}
`;

// ── Helper: Get animation with reduced motion support ────
export function getAnimation(motion, reducedMotion = false) {
  if (reducedMotion) {
    return { animation: "none", transition: "none" };
  }
  const { keyframes, duration, easing, iteration = 1, alternate = false } = motion;
  return {
    animation: `${keyframes} ${duration}ms ${easing} ${iteration}${alternate ? " alternate" : ""}`,
  };
}

// ── Helper: Inject keyframes into DOM ─────────────────────
let keyframesInjected = false;
export function injectMotionKeyframes() {
  if (keyframesInjected || typeof document === "undefined") return;
  const style = document.createElement("style");
  style.id = "devos-motion-keyframes";
  style.textContent = KEYFRAMES_CSS;
  document.head.appendChild(style);
  keyframesInjected = true;
}

export default MOTION_TOKENS;
