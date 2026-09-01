/**
 * DevOS Responsive Hook
 * Detects viewport breakpoints and provides responsive utilities.
 */
import { useState, useEffect, useCallback } from "react";
import { BREAKPOINTS } from "../theme/tokens";

export const useResponsive = () => {
  const getBreakpoint = useCallback(() => {
    const width = window.innerWidth;
    if (width < BREAKPOINTS.mobile) return "mobile";
    if (width < BREAKPOINTS.tablet) return "tablet";
    if (width < BREAKPOINTS.desktop) return "desktop";
    return "wide";
  }, []);

  const [breakpoint, setBreakpoint] = useState(getBreakpoint());

  useEffect(() => {
    let ticking = false;
    const handler = () => {
      if (!ticking) {
        window.requestAnimationFrame(() => {
          setBreakpoint(getBreakpoint());
          ticking = false;
        });
        ticking = true;
      }
    };
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, [getBreakpoint]);

  const isMobile = breakpoint === "mobile";
  const isTablet = breakpoint === "tablet";
  const isDesktop = breakpoint === "desktop" || breakpoint === "wide";
  const isWide = breakpoint === "wide";

  // Multi-monitor detection (best effort)
  const multiMonitor = typeof window !== "undefined" && window.screen && "availWidth" in window.screen;

  return {
    breakpoint,
    isMobile,
    isTablet,
    isDesktop,
    isWide,
    multiMonitor,
    width: typeof window !== "undefined" ? window.innerWidth : 0,
    height: typeof window !== "undefined" ? window.innerHeight : 0,
  };
};

export default useResponsive;
