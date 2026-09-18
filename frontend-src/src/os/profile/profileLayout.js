/** Adaptive professional profile layout from available space. */
export function resolveProfileLayout(area) {
  const w = Math.max(0, Number(area?.width) || 0);
  const h = Math.max(0, Number(area?.height) || 0);
  const orientation = h >= w ? "portrait" : "landscape";
  if (w >= 960 && orientation === "landscape") {
    return { density: "rich", columns: 2, showSideRail: true };
  }
  if (w >= 640) {
    return { density: "comfortable", columns: 1, showSideRail: true };
  }
  return { density: "compact", columns: 1, showSideRail: false, sectionsAsDrawers: true };
}
