/**
 * DevOS Design Tokens
 * The single source of truth for all visual design decisions.
 * Themes override these values via CSS custom properties.
 */

// ── Density Presets ─────────────────────────────────────────
export const DENSITY = {
  compact: {
    spacingUnit: 4,
    controlHeight: 28,
    controlHeightLg: 32,
    padding: 8,
    paddingLg: 12,
    fontSize: 12,
    lineHeight: 1.4,
  },
  comfortable: {
    spacingUnit: 6,
    controlHeight: 32,
    controlHeightLg: 38,
    padding: 12,
    paddingLg: 16,
    fontSize: 13,
    lineHeight: 1.5,
  },
  spacious: {
    spacingUnit: 8,
    controlHeight: 38,
    controlHeightLg: 44,
    padding: 16,
    paddingLg: 20,
    fontSize: 14,
    lineHeight: 1.6,
  },
};

// ── Typography Scale ────────────────────────────────────────
export const TYPOGRAPHY = {
  fontFamily: {
    ui: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    mono: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
    display: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
  },
  fontSize: {
    xs: 10,
    sm: 11,
    base: 13,
    md: 14,
    lg: 16,
    xl: 18,
    xxl: 22,
    xxxl: 28,
  },
  fontWeight: {
    regular: 400,
    medium: 500,
    semibold: 600,
    bold: 700,
  },
  letterSpacing: {
    tight: -0.02,
    normal: 0,
    wide: 0.02,
    wider: 0.05,
    widest: 0.1,
  },
};

// ── Spacing Scale (4px base unit) ───────────────────────────
export const SPACING = {
  0: 0,
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  5: 20,
  6: 24,
  8: 32,
  10: 40,
  12: 48,
  16: 64,
  20: 80,
};

// ── Border Radius ───────────────────────────────────────────
export const RADIUS = {
  none: 0,
  sm: 4,
  base: 6,
  md: 8,
  lg: 12,
  xl: 16,
  xxl: 20,
  full: 9999,
};

// ── Elevation / Shadows ─────────────────────────────────────
export const ELEVATION = {
  none: "none",
  low: "0 1px 3px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.08)",
  medium: "0 4px 12px rgba(0,0,0,0.15), 0 2px 4px rgba(0,0,0,0.1)",
  high: "0 8px 24px rgba(0,0,0,0.2), 0 4px 8px rgba(0,0,0,0.12)",
  maximum: "0 16px 48px rgba(0,0,0,0.3), 0 8px 16px rgba(0,0,0,0.15)",
  glow: "0 0 20px rgba(var(--accent-rgb), 0.4)",
};

// ── Motion Tokens ───────────────────────────────────────────
export const MOTION = {
  duration: {
    instant: 80,
    fast: 150,
    normal: 240,
    slow: 360,
    deliberate: 500,
    cinematic: 800,
  },
  easing: {
    // Material-style standard easing
    standard: "cubic-bezier(0.4, 0.0, 0.2, 1)",
    // Decelerate: entering screen, expanding
    decelerate: "cubic-bezier(0.0, 0.0, 0.2, 1)",
    // Accelerate: leaving screen, collapsing
    accelerate: "cubic-bezier(0.4, 0.0, 1, 1)",
    // Spring-like for playful interactions
    spring: "cubic-bezier(0.34, 1.56, 0.64, 1)",
    // Linear for progress bars
    linear: "linear",
  },
  // Living node animation phases
  node: {
    idle: {
      duration: 3000,
      easing: "ease-in-out",
      property: "opacity",
      from: 1,
      to: 0.7,
    },
    thinking: {
      duration: 1200,
      easing: "ease-in-out",
      property: "opacity",
      from: 1,
      to: 0.5,
    },
    executing: {
      duration: 600,
      easing: "linear",
    },
    success: {
      duration: 800,
      easing: "cubic-bezier(0.0, 0.0, 0.2, 1)",
    },
    failed: {
      duration: 600,
      easing: "ease-in-out",
    },
  },
};

// ── Z-Index Scale ───────────────────────────────────────────
export const Z_INDEX = {
  base: 0,
  docked: 10,
  sidebar: 20,
  panel: 30,
  resizeHandle: 40,
  floating: 100,
  dropdown: 200,
  popover: 300,
  modal: 400,
  commandPalette: 500,
  toast: 600,
  tooltip: 700,
  dragPreview: 800,
};

// ── Layout Dimensions ────────────────────────────────────────
export const LAYOUT = {
  sidebar: {
    expanded: 240,
    compact: 56,
    collapsed: 0,
  },
  panel: {
    minWidth: 200,
    minHeight: 120,
    defaultWidth: 420,
    defaultHeight: 300,
    headerHeight: 36,
    tabBarHeight: 32,
  },
  missionBar: {
    height: 40,
  },
  statusBar: {
    height: 24,
  },
  dock: {
    handleSize: 4,
    minSize: 48,
  },
};

// ── Responsive Breakpoints ──────────────────────────────────
export const BREAKPOINTS = {
  mobile: 768,
  tablet: 1024,
  desktop: 1280,
  wide: 1920,
};

// ── Panel States ────────────────────────────────────────────
export const PANEL_STATES = {
  DOCKED: "docked",
  FLOATING: "floating",
  PINNED: "pinned",
  HIDDEN: "hidden",
  FULLSCREEN: "fullscreen",
};

export const DOCK_POSITIONS = {
  TOP: "top",
  BOTTOM: "bottom",
  LEFT: "left",
  RIGHT: "right",
  CENTER: "center",
  FLOATING: "floating",
};

// ── Default Customization ───────────────────────────────────
export const DEFAULT_CUSTOMIZATION = {
  accent: "#58a6ff",
  accentRgb: "88, 166, 255",
  borderRadius: 8,
  glowIntensity: 0.4,
  blur: 12,
  density: "comfortable",
  typographyScale: 1,
  animationSpeed: 1,
  borderWidth: 1,
};

// ── Helper: Convert hex to rgb string ──────────────────────
export function hexToRgb(hex) {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return "0, 0, 0";
  return `${parseInt(result[1], 16)}, ${parseInt(result[2], 16)}, ${parseInt(result[3], 16)}`;
}

// ── Helper: Generate CSS custom properties from tokens ─────
export function tokensToCSSVars(customization = DEFAULT_CUSTOMIZATION) {
  const density = DENSITY[customization.density] || DENSITY.comfortable;
  const scale = customization.typographyScale;
  return {
    "--accent": customization.accent,
    "--accent-rgb": hexToRgb(customization.accent),
    "--radius-base": `${customization.borderRadius}px`,
    "--radius-sm": `${Math.max(2, customization.borderRadius - 4)}px`,
    "--radius-md": `${customization.borderRadius}px`,
    "--radius-lg": `${customization.borderRadius + 4}px`,
    "--glow-intensity": customization.glowIntensity,
    "--blur": `${customization.blur}px`,
    "--border-width": `${customization.borderWidth}px`,
    "--space-unit": `${density.spacingUnit}px`,
    "--control-h": `${density.controlHeight}px`,
    "--control-h-lg": `${density.controlHeightLg}px`,
    "--pad": `${density.padding}px`,
    "--pad-lg": `${density.paddingLg}px`,
    "--font-size": `${density.fontSize * scale}px`,
    "--line-height": density.lineHeight,
    "--anim-speed": customization.animationSpeed,
  };
}

export default {
  DENSITY,
  TYPOGRAPHY,
  SPACING,
  RADIUS,
  ELEVATION,
  MOTION,
  Z_INDEX,
  LAYOUT,
  BREAKPOINTS,
  PANEL_STATES,
  DOCK_POSITIONS,
  DEFAULT_CUSTOMIZATION,
  hexToRgb,
  tokensToCSSVars,
};
