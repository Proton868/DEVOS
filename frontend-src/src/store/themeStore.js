/**
 * DevOS Theme Store
 * Manages active theme, customization overrides, and motion preferences.
 * Separate from the main useStore for clean separation of concerns.
 */
import { create } from "zustand";
import { themeRegistry } from "../theme/themeRegistry";
import { DEFAULT_CUSTOMIZATION, tokensToCSSVars } from "../theme/tokens";
import { injectMotionKeyframes } from "../theme/motion";

const STORAGE_KEY = "devos_theme_state";

function loadPersisted() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function persist(state) {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        activeTheme: state.activeTheme,
        customization: state.customization,
        reducedMotion: state.reducedMotion,
      })
    );
  } catch {
    /* ignore quota errors */
  }
}

const persisted = loadPersisted();

export const useThemeStore = create((set, get) => ({
  // ── State ───────────────────────────────────────────────
  activeTheme: persisted?.activeTheme || "carbon",
  customization: { ...DEFAULT_CUSTOMIZATION, ...persisted?.customization },
  reducedMotion: persisted?.reducedMotion || false,

  // ── Actions ─────────────────────────────────────────────
  setTheme: (themeId) => {
    if (!themeRegistry.get(themeId)) return;
    set({ activeTheme: themeId });
    persist(get());
  },

  setCustomization: (patch) => {
    set((s) => ({ customization: { ...s.customization, ...patch } }));
    persist(get());
  },

  resetCustomization: () => {
    set({ customization: { ...DEFAULT_CUSTOMIZATION } });
    persist(get());
  },

  setReducedMotion: (v) => {
    set({ reducedMotion: v });
    persist(get());
  },

  // Toggle between dark/light category themes
  toggleDarkLight: () => {
    const theme = themeRegistry.get(get().activeTheme);
    if (theme?.isDark) {
      get().setTheme("light");
    } else {
      get().setTheme("carbon");
    }
  },

  // ── Derived ─────────────────────────────────────────────
  getCSSVars: () => {
    const { activeTheme, customization } = get();
    return {
      ...themeRegistry.toCSSVars(activeTheme, customization),
      ...tokensToCSSVars(customization),
    };
  },

  isDark: () => {
    const theme = themeRegistry.get(get().activeTheme);
    return theme ? theme.isDark : true;
  },

  getThemeMeta: () => {
    const theme = themeRegistry.get(get().activeTheme);
    return theme ? { id: get().activeTheme, ...theme.meta, isDark: theme.isDark } : null;
  },
}));

/**
 * Apply theme CSS variables to the document root.
 * Call this once on app mount and whenever theme/customization changes.
 */
export function applyThemeToDOM() {
  const state = useThemeStore.getState();
  const vars = state.getCSSVars();
  const root = document.documentElement;
  for (const [key, value] of Object.entries(vars)) {
    root.style.setProperty(key, value);
  }
  root.setAttribute("data-theme", state.activeTheme);
  root.setAttribute("data-density", state.customization.density);
  root.setAttribute("data-theme-dark", state.isDark() ? "true" : "false");
  root.setAttribute("data-reduced-motion", state.reducedMotion ? "true" : "false");
  // Inject motion keyframes
  injectMotionKeyframes();
}

// Auto-apply theme whenever store changes
useThemeStore.subscribe(applyThemeToDOM);

export default useThemeStore;
