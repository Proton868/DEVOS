/**
 * DevOS Theme Registry
 * All built-in themes. Each theme defines a complete color system.
 * Customization overrides are applied on top of the active theme.
 */

import { hexToRgb } from "./tokens";

// ── Theme Structure ────────────────────────────────────────
// Each theme provides:
//   colors: { bg-0..bg-4, border, text-0..text-3, accent, accent2,
//            green, red, yellow, purple, ... }
//   isDark: boolean
//   meta: { name, description, category }

const themes = {
  // ─────────────────────────────────────────────────────────
  midnight: {
    meta: { name: "Midnight", description: "Deep blue darkness", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#0a0e1a", "bg-1": "#0f1420", "bg-2": "#161c2e", "bg-3": "#1e2438", "bg-4": "#2a3148",
      "border": "#2a3148",
      "text-0": "#e6edf3", "text-1": "#c9d1d9", "text-2": "#8b949e", "text-3": "#6e7681",
      "accent": "#58a6ff", "accent-2": "#1f6feb",
      "green": "#3fb950", "red": "#f85149", "yellow": "#d29922", "purple": "#bc8cff",
      "orange": "#db6d28", "cyan": "#39c5cf", "pink": "#f778ba",
    },
  },

  // ─────────────────────────────────────────────────────────
  aurora: {
    meta: { name: "Aurora", description: "Gradient greens and blues", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#0c1a1a", "bg-1": "#102525", "bg-2": "#153030", "bg-3": "#1a3a3a", "bg-4": "#244a4a",
      "border": "#244a4a",
      "text-0": "#e8f5f0", "text-1": "#c5e0d8", "text-2": "#8aa8a0", "text-3": "#6a8580",
      "accent": "#5eead4", "accent-2": "#2dd4bf",
      "green": "#4ade80", "red": "#f87171", "yellow": "#facc15", "purple": "#c084fc",
      "orange": "#fb923c", "cyan": "#22d3ee", "pink": "#f472b6",
    },
  },

  // ─────────────────────────────────────────────────────────
  carbon: {
    meta: { name: "Carbon", description: "Neutral dark gray", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#0d1117", "bg-1": "#161b22", "bg-2": "#1c2128", "bg-3": "#21262d", "bg-4": "#30363d",
      "border": "#30363d",
      "text-0": "#e6edf3", "text-1": "#c9d1d9", "text-2": "#8b949e", "text-3": "#6e7681",
      "accent": "#58a6ff", "accent-2": "#1f6feb",
      "green": "#3fb950", "red": "#f85149", "yellow": "#d29922", "purple": "#bc8cff",
      "orange": "#db6d28", "cyan": "#39c5cf", "pink": "#f778ba",
    },
  },

  // ─────────────────────────────────────────────────────────
  graphite: {
    meta: { name: "Graphite", description: "Slate gray tones", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#111317", "bg-1": "#181b21", "bg-2": "#1f232b", "bg-3": "#272c35", "bg-4": "#353b46",
      "border": "#353b46",
      "text-0": "#e2e8f0", "text-1": "#cbd5e1", "text-2": "#94a3b8", "text-3": "#64748b",
      "accent": "#60a5fa", "accent-2": "#3b82f6",
      "green": "#4ade80", "red": "#f87171", "yellow": "#fbbf24", "purple": "#a78bfa",
      "orange": "#fb923c", "cyan": "#22d3ee", "pink": "#f472b6",
    },
  },

  // ─────────────────────────────────────────────────────────
  nebula: {
    meta: { name: "Nebula", description: "Cosmic purple and blue", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#0d0a1a", "bg-1": "#130f24", "bg-2": "#1a1530", "bg-3": "#221c3e", "bg-4": "#2e2552",
      "border": "#2e2552",
      "text-0": "#ede9fe", "text-1": "#ddd6fe", "text-2": "#a5b4fc", "text-3": "#818cf8",
      "accent": "#a78bfa", "accent-2": "#7c3aed",
      "green": "#6ee7b7", "red": "#fb7185", "yellow": "#fcd34d", "purple": "#c084fc",
      "orange": "#fb923c", "cyan": "#22d3ee", "pink": "#f472b6",
    },
  },

  // ─────────────────────────────────────────────────────────
  ocean: {
    meta: { name: "Ocean", description: "Deep blue and teal", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#08141c", "bg-1": "#0d1e2a", "bg-2": "#132738", "bg-3": "#1a3145", "bg-4": "#243e54",
      "border": "#243e54",
      "text-0": "#e0f2fe", "text-1": "#bae6fd", "text-2": "#7dd3fc", "text-3": "#38bdf8",
      "accent": "#38bdf8", "accent-2": "#0284c7",
      "green": "#34d399", "red": "#fb7185", "yellow": "#fcd34d", "purple": "#a78bfa",
      "orange": "#fb923c", "cyan": "#22d3ee", "pink": "#f472b6",
    },
  },

  // ─────────────────────────────────────────────────────────
  nord: {
    meta: { name: "Nord", description: "Arctic north palette", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#2e3440", "bg-1": "#3b4252", "bg-2": "#434c5e", "bg-3": "#4c566a", "bg-4": "#5e6779",
      "border": "#4c566a",
      "text-0": "#eceff4", "text-1": "#d8dee9", "text-2": "#a3b1c9", "text-3": "#7a8699",
      "accent": "#88c0d0", "accent-2": "#5e81ac",
      "green": "#a3be8c", "red": "#bf616a", "yellow": "#ebcb8b", "purple": "#b48ead",
      "orange": "#d08770", "cyan": "#8fbcbb", "pink": "#d3869b",
    },
  },

  // ─────────────────────────────────────────────────────────
  tokyo: {
    meta: { name: "Tokyo", description: "Cyberpunk neon nights", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#0f0f14", "bg-1": "#16161e", "bg-2": "#1a1b26", "bg-3": "#22232e", "bg-4": "#2d2e3b",
      "border": "#2d2e3b",
      "text-0": "#c0caf5", "text-1": "#a9b1d6", "text-2": "#7a88e3", "text-3": "#565f89",
      "accent": "#7aa2f7", "accent-2": "#bb9af7",
      "green": "#9ece6a", "red": "#f7768e", "yellow": "#e0af68", "purple": "#bb9af7",
      "orange": "#ff9e64", "cyan": "#7dcfff", "pink": "#ff75a0",
    },
  },

  // ─────────────────────────────────────────────────────────
  solar: {
    meta: { name: "Solar", description: "Warm dark with golden accents", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#1a1208", "bg-1": "#241a0d", "bg-2": "#2e2316", "bg-3": "#3a2d1e", "bg-4": "#4a3a28",
      "border": "#4a3a28",
      "text-0": "#fdf6e3", "text-1": "#eee8d5", "text-2": "#b58900", "text-3": "#8a6d00",
      "accent": "#cb4b16", "accent-2": "#d33682",
      "green": "#859900", "red": "#dc322f", "yellow": "#b58900", "purple": "#6c71c4",
      "orange": "#cb4b16", "cyan": "#2aa198", "pink": "#d33682",
    },
  },

  // ─────────────────────────────────────────────────────────
  light: {
    meta: { name: "Light", description: "Clean neutral light", category: "light" },
    isDark: false,
    colors: {
      "bg-0": "#ffffff", "bg-1": "#f6f8fa", "bg-2": "#eaeef2", "bg-3": "#d0d7de", "bg-4": "#afb8c1",
      "border": "#d0d7de",
      "text-0": "#1f2328", "text-1": "#24292f", "text-2": "#57606a", "text-3": "#8c959f",
      "accent": "#0969da", "accent-2": "#0550ae",
      "green": "#1a7f37", "red": "#cf222e", "yellow": "#9a6700", "purple": "#8250df",
      "orange": "#bc4c00", "cyan": "#0969da", "pink": "#bf3989",
    },
  },

  // ─────────────────────────────────────────────────────────
  warmLight: {
    meta: { name: "Warm Light", description: "Warm cozy light tones", category: "light" },
    isDark: false,
    colors: {
      "bg-0": "#fdfbf7", "bg-1": "#f7f2e8", "bg-2": "#efe8d8", "bg-3": "#e0d4bc", "bg-4": "#c9b896",
      "border": "#e0d4bc",
      "text-0": "#3d2f1f", "text-1": "#5c4a32", "text-2": "#8a7355", "text-3": "#a89578",
      "accent": "#c2410c", "accent-2": "#9a3412",
      "green": "#166534", "red": "#991b1b", "yellow": "#854d0e", "purple": "#6b21a8",
      "orange": "#c2410c", "cyan": "#0e7490", "pink": "#9d174d",
    },
  },

  // ─────────────────────────────────────────────────────────
  cream: {
    meta: { name: "Cream", description: "Soft cream and beige", category: "light" },
    isDark: false,
    colors: {
      "bg-0": "#faf7f0", "bg-1": "#f3ede0", "bg-2": "#ebe2d0", "bg-3": "#dccfb4", "bg-4": "#c4b496",
      "border": "#dccfb4",
      "text-0": "#422a13", "text-1": "#5c3d1f", "text-2": "#8c6a4a", "text-3": "#a88a6a",
      "accent": "#b45309", "accent-2": "#92400e",
      "green": "#15803d", "red": "#b91c1c", "yellow": "#a16207", "purple": "#7e22ce",
      "orange": "#c2410c", "cyan": "#0e7490", "pink": "#be185d",
    },
  },

  // ─────────────────────────────────────────────────────────
  sepia: {
    meta: { name: "Sepia", description: "Vintage brown tones", category: "light" },
    isDark: false,
    colors: {
      "bg-0": "#f4ecd8", "bg-1": "#ebe0c4", "bg-2": "#ddcba0", "bg-3": "#c9b282", "bg-4": "#a8915f",
      "border": "#c9b282",
      "text-0": "#3e2723", "text-1": "#4e342e", "text-2": "#6d4c41", "text-3": "#8d6e63",
      "accent": "#6d4c41", "accent-2": "#4e342e",
      "green": "#33691e", "red": "#b71c1c", "yellow": "#827717", "purple": "#4527a0",
      "orange": "#bf360c", "cyan": "#006064", "pink": "#ad1457",
    },
  },

  // ─────────────────────────────────────────────────────────
  oled: {
    meta: { name: "OLED", description: "Pure black for OLED screens", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#000000", "bg-1": "#0a0a0a", "bg-2": "#121212", "bg-3": "#1a1a1a", "bg-4": "#242424",
      "border": "#1a1a1a",
      "text-0": "#ffffff", "text-1": "#e0e0e0", "text-2": "#a0a0a0", "text-3": "#707070",
      "accent": "#58a6ff", "accent-2": "#1f6feb",
      "green": "#3fb950", "red": "#f85149", "yellow": "#d29922", "purple": "#bc8cff",
      "orange": "#db6d28", "cyan": "#39c5cf", "pink": "#f778ba",
    },
  },

  // ─────────────────────────────────────────────────────────
  developerGreen: {
    meta: { name: "Developer Green", description: "Matrix-style green terminal", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#0a0e0a", "bg-1": "#0f1410", "bg-2": "#141a14", "bg-3": "#1a221a", "bg-4": "#243024",
      "border": "#1a221a",
      "text-0": "#b8f5b8", "text-1": "#90e0a0", "text-2": "#60c070", "text-3": "#409050",
      "accent": "#00ff7f", "accent-2": "#00cc66",
      "green": "#00ff7f", "red": "#ff5555", "yellow": "#f1fa8c", "purple": "#bd93f9",
      "orange": "#ffb86c", "cyan": "#8be9fd", "pink": "#ff79c6",
    },
  },

  // ─────────────────────────────────────────────────────────
  purple: {
    meta: { name: "Purple", description: "Vibrant purple darkness", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#100a14", "bg-1": "#1a1024", "bg-2": "#241634", "bg-3": "#2e1d44", "bg-4": "#3a2654",
      "border": "#2e1d44",
      "text-0": "#f3e8ff", "text-1": "#e9d5ff", "text-2": "#c4b5fd", "text-3": "#a78bfa",
      "accent": "#a855f7", "accent-2": "#7c3aed",
      "green": "#4ade80", "red": "#fb7185", "yellow": "#fcd34d", "purple": "#c084fc",
      "orange": "#fb923c", "cyan": "#22d3ee", "pink": "#f472b6",
    },
  },

  // ─────────────────────────────────────────────────────────
  glass: {
    meta: { name: "Glass", description: "Transparent with blur effects", category: "dark" },
    isDark: true,
    colors: {
      "bg-0": "#0d1117cc", "bg-1": "#161b22cc", "bg-2": "#1c2128cc", "bg-3": "#21262daa", "bg-4": "#30363daa",
      "border": "#ffffff14",
      "text-0": "#e6edf3", "text-1": "#c9d1d9", "text-2": "#8b949e", "text-3": "#6e7681",
      "accent": "#58a6ff", "accent-2": "#1f6feb",
      "green": "#3fb950", "red": "#f85149", "yellow": "#d29922", "purple": "#bc8cff",
      "orange": "#db6d28", "cyan": "#39c5cf", "pink": "#f778ba",
    },
    glass: true,
  },
};

// ── Theme Manager ──────────────────────────────────────────
class ThemeRegistry {
  constructor() {
    this.themes = { ...themes };
    this.listeners = new Set();
  }

  get(themeId) {
    return this.themes[themeId];
  }

  getAll() {
    return Object.entries(this.themes).map(([id, theme]) => ({
      id,
      ...theme.meta,
      isDark: theme.isDark,
    }));
  }

  register(id, theme) {
    this.themes[id] = theme;
    this.notify();
  }

  on(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify() {
    this.listeners.forEach((l) => l());
  }

  // Convert theme + customization to CSS custom properties
  toCSSVars(themeId, customization = {}) {
    const theme = this.themes[themeId] || this.themes.carbon;
    const vars = {};
    // Apply theme colors
    for (const [key, value] of Object.entries(theme.colors)) {
      vars[`--${key}`] = value;
    }
    // Apply accent override
    if (customization.accent) {
      vars["--accent"] = customization.accent;
      vars["--accent-rgb"] = hexToRgb(customization.accent);
    } else {
      vars["--accent-rgb"] = hexToRgb(theme.colors.accent);
    }
    // Apply glass flag
    vars["--theme-glass"] = theme.glass ? "1" : "0";
    vars["--theme-dark"] = theme.isDark ? "1" : "0";
    // RGB values for accent colors
    for (const color of ["green", "red", "yellow", "purple", "orange", "cyan", "pink"]) {
      if (theme.colors[color]) {
        vars[`--${color}-rgb`] = hexToRgb(theme.colors[color]);
      }
    }
    return vars;
  }
}

export const themeRegistry = new ThemeRegistry();
export default themeRegistry;
