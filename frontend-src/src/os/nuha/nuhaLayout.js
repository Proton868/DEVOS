/**
 * Nuha / Chat spatial presentation — constraint-based, not device-named forks.
 */

/**
 * @param {{ width: number, height: number, orientation?: string }} viewport
 * @param {{
 *   preferred?: 'docked'|'overlay'|'sheet'|'fullscreen'|'auto',
 *   otherPrimaryOpen?: boolean,
 *   explicitChatDimension?: boolean,
 * }} [ctx]
 */
export function resolveNuhaPresentation(viewport, ctx = {}) {
  const w = Math.max(0, Number(viewport?.width) || 0);
  const h = Math.max(0, Number(viewport?.height) || 0);
  const orientation =
    viewport?.orientation || (h >= w ? "portrait" : "landscape");
  const preferred = ctx.preferred || "auto";
  const otherPrimary = !!ctx.otherPrimaryOpen;
  const explicitChat = !!ctx.explicitChatDimension;

  if (preferred && preferred !== "auto") {
    return {
      presentation: preferred,
      widthPct: preferred === "docked" ? 38 : 100,
      reason: "user_preferred",
      orientation,
    };
  }

  // Explicit Chat dimension (user switched to Nuha as active space)
  if (explicitChat && !otherPrimary) {
    if (w < 720 || orientation === "portrait") {
      return {
        presentation: "fullscreen",
        widthPct: 100,
        reason: "chat_dimension_stack",
        orientation,
      };
    }
    return {
      presentation: "docked",
      widthPct: 42,
      reason: "chat_dimension_split",
      orientation,
    };
  }

  // Invoked while another primary is active — do not steal the workspace
  if (otherPrimary) {
    if (w < 640 || orientation === "portrait") {
      return {
        presentation: "sheet",
        widthPct: 100,
        reason: "preserve_primary_mobile",
        orientation,
      };
    }
    if (w < 1100) {
      return {
        presentation: "overlay",
        widthPct: 40,
        reason: "preserve_primary_tablet",
        orientation,
      };
    }
    return {
      presentation: "docked",
      widthPct: 34,
      reason: "preserve_primary_desktop",
      orientation,
    };
  }

  // Nuha alone
  if (w < 720 || orientation === "portrait") {
    return {
      presentation: "fullscreen",
      widthPct: 100,
      reason: "solo_stack",
      orientation,
    };
  }
  return {
    presentation: "docked",
    widthPct: 40,
    reason: "solo_desktop",
    orientation,
  };
}
