/**
 * DevOS Global Shortcuts Hook
 * Wrapper that registers all default shortcuts and the cheatsheet overlay.
 */
import { useState, useEffect, useCallback } from "react";
import { useKeyboardShortcuts, shortcutRegistry } from "./useKeyboardShortcuts";

export function useGlobalShortcuts() {
  useKeyboardShortcuts();
  const [showCheatsheet, setShowCheatsheet] = useState(false);

  useEffect(() => {
    const handler = () => setShowCheatsheet((v) => !v);
    window.addEventListener("devos:show-shortcuts", handler);
    return () => window.removeEventListener("devos:show-shortcuts", handler);
  }, []);

  const toggleCheatsheet = useCallback(() => setShowCheatsheet((v) => !v), []);

  return {
    showCheatsheet,
    toggleCheatsheet,
    shortcuts: shortcutRegistry.getAll(),
  };
}

export default useGlobalShortcuts;
