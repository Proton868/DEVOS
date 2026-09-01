/**
 * DevOS Theme Context
 * Provides theme state to React components and a hook for theme operations.
 */
import React, { createContext, useContext, useEffect, useMemo } from "react";
import { useThemeStore, applyThemeToDOM } from "../store/themeStore";
import { themeRegistry } from "./themeRegistry";

const ThemeContext = createContext(null);

export function ThemeProvider({ children }) {
  const theme = useThemeStore();

  // Apply theme to DOM on mount and whenever relevant state changes
  useEffect(() => {
    applyThemeToDOM();
  }, [theme.activeTheme, theme.customization, theme.reducedMotion]);

  // Detect system reduced-motion preference on first mount
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handler = (e) => {
      if (e.matches && !localStorage.getItem("devos_motion_pref_set")) {
        useThemeStore.getState().setReducedMotion(true);
      }
    };
    handler(mq);
    mq.addEventListener?.("change", handler);
    return () => mq.removeEventListener?.("change", handler);
  }, []);

  const value = useMemo(
    () => ({
      activeTheme: theme.activeTheme,
      customization: theme.customization,
      reducedMotion: theme.reducedMotion,
      isDark: theme.isDark(),
      themeMeta: theme.getThemeMeta(),
      themes: themeRegistry.getAll(),
      setTheme: theme.setTheme,
      setCustomization: theme.setCustomization,
      resetCustomization: theme.resetCustomization,
      setReducedMotion: theme.setReducedMotion,
      toggleDarkLight: theme.toggleDarkLight,
    }),
    [theme]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return ctx;
}

export default ThemeContext;
