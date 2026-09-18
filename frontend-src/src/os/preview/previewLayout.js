/**
 * Adaptive Preview chrome density from available dimension size.
 */

/**
 * @param {{ width: number, height: number }} area
 */
export function resolvePreviewChrome(area) {
  const w = Math.max(0, Number(area?.width) || 0);

  if (w < 480) {
    return {
      density: "compact",
      showLabels: false,
      showDeviceInline: false,
      showZoomInline: false,
      showSecondaryInline: false,
      useOverflowMenu: true,
      toolbarMaxHeight: 44,
    };
  }

  if (w < 720) {
    return {
      density: "medium",
      showLabels: false,
      showDeviceInline: true,
      showZoomInline: false,
      showSecondaryInline: false,
      useOverflowMenu: true,
      toolbarMaxHeight: 44,
    };
  }

  return {
    density: "large",
    showLabels: true,
    showDeviceInline: true,
    showZoomInline: true,
    showSecondaryInline: true,
    useOverflowMenu: false,
    toolbarMaxHeight: 48,
  };
}
