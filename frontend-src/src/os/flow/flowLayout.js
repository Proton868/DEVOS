/**
 * Flow dimension internal spatial layout.
 * Decides how canvas vs inspector/config/logs share the Flow surface.
 */

/**
 * @param {{ width: number, height: number, orientation?: string }} area
 * @param {{ inspectorOpen?: boolean, logsOpen?: boolean }} flags
 */
export function resolveFlowLayout(area, flags = {}) {
  const w = Math.max(0, Number(area?.width) || 0);
  const h = Math.max(0, Number(area?.height) || 0);
  const orientation =
    area?.orientation || (h >= w ? "portrait" : "landscape");

  const inspectorOpen = !!flags.inspectorOpen;
  const logsOpen = !!flags.logsOpen;

  const INSPECTOR_MIN = 280;
  const CANVAS_MIN = 320;

  let inspectorMode = "hidden"; // beside | overlay | drawer | fullscreen | hidden
  if (inspectorOpen) {
    if (w >= CANVAS_MIN + INSPECTOR_MIN && orientation === "landscape") {
      inspectorMode = "beside";
    } else if (w >= 520 && orientation === "landscape") {
      inspectorMode = "overlay";
    } else if (h >= 420) {
      inspectorMode = "drawer";
    } else {
      inspectorMode = "fullscreen";
    }
  }

  let logsMode = "hidden";
  if (logsOpen) {
    if (h >= 480 && inspectorMode !== "fullscreen") {
      logsMode = "drawer";
    } else if (w >= CANVAS_MIN + 240 && inspectorMode === "beside") {
      logsMode = "beside";
    } else {
      logsMode = "overlay";
    }
  }

  // Portrait / narrow: never force full desktop chrome density
  const chromeDensity =
    w < 480 ? "compact" : w < 800 ? "medium" : "large";

  return {
    area: { width: w, height: h, orientation },
    chromeDensity,
    inspectorMode,
    logsMode,
    showCanvas: inspectorMode !== "fullscreen",
    canvasTouches: true,
    inspectorWidth: Math.min(360, Math.max(INSPECTOR_MIN, Math.floor(w * 0.32))),
    drawerHeight: Math.min(320, Math.max(180, Math.floor(h * 0.42))),
  };
}
