import { useState, useEffect } from "react";

/**
 * Live viewport metrics for the spatial engine (width, height, orientation).
 */
export function useSpatialViewport() {
  const read = () => {
    if (typeof window === "undefined") {
      return { width: 1280, height: 800, orientation: "landscape" };
    }
    const width = window.innerWidth;
    const height = window.innerHeight;
    return {
      width,
      height,
      orientation: height >= width ? "portrait" : "landscape",
    };
  };

  const [viewport, setViewport] = useState(read);

  useEffect(() => {
    let raf = 0;
    const onChange = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setViewport(read()));
    };
    window.addEventListener("resize", onChange);
    window.addEventListener("orientationchange", onChange);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onChange);
      window.removeEventListener("orientationchange", onChange);
    };
  }, []);

  return viewport;
}

export default useSpatialViewport;
