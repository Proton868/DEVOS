/**
 * Nuha → Spatial Surface Intent contract.
 * Presentation only — never authorizes execution / UCIP.
 */

export const SURFACE_TYPES = new Set(["chat", "ide", "flow", "research", "canvas", "preview", "artifact", "deployment", "logs", "web", "none"]);
export const SURFACE_ACTIONS = new Set(["open", "focus", "reveal", "close", "none"]);

/**
 * @typedef {Object} SurfaceIntent
 * @property {"chat"|"ide"|"flow"|"research"|"canvas"|"none"} surface
 * @property {"open"|"focus"|"reveal"|"close"|"none"} action
 * @property {boolean} required
 * @property {string} [reason]
 * @property {number} [confidence]
 * @property {Object} [context]
 */

export function normalizeSurfaceIntent(raw) {
  if (!raw || typeof raw !== "object") return null;
  const surface = String(raw.surface || "none").toLowerCase();
  const action = String(raw.action || "none").toLowerCase();
  if (!SURFACE_TYPES.has(surface)) {
    return { invalid: true, surface, action, reason: "unsupported_surface" };
  }
  if (!SURFACE_ACTIONS.has(action)) {
    return { invalid: true, surface, action, reason: "unsupported_action" };
  }
  return {
    surface,
    action,
    required: !!raw.required,
    reason: raw.reason || "",
    confidence: typeof raw.confidence === "number" ? raw.confidence : undefined,
    context: raw.context && typeof raw.context === "object" ? raw.context : {},
    intent_classes: raw.intent_classes || [],
  };
}

/**
 * Apply intent via existing osStore surfaces. Returns result status.
 * Does not create parallel shell state.
 */
export function applySurfaceIntent(intent, store) {
  const n = normalizeSurfaceIntent(intent);
  if (!n) return { ok: true, status: "noop" };
  if (n.invalid) {
    console.warn("[surface_intent] ignored invalid", n);
    return { ok: false, status: "invalid_intent", detail: n };
  }
  if (n.surface === "none" || n.surface === "chat" || n.action === "none") {
    return { ok: true, status: "remain_chat", intent: n };
  }

  const s = store?.getState ? store.getState() : store;
  if (!s) return { ok: false, status: "no_store" };

  try {
    if (n.surface === "ide") {
      if (typeof s.openEditor === "function") {
        s.openEditor({ file: n.context?.filePath || null });
        if (typeof s.openCopilot === "function" && s.copilot && !s.copilot.open) {
          // keep Nuha available but IDE focused
        }
        return { ok: true, status: "ide_opened", intent: n };
      }
      if (n.required) {
        return { ok: false, status: "surface_unavailable", surface: "ide", intent: n };
      }
      return { ok: true, status: "optional_unavailable", surface: "ide", intent: n };
    }
    if (n.surface === "flow") {
      // Flow = orchestration canvas primary; clear overlay, ensure dashboard/canvas
      if (typeof s.setOverlay === "function") s.setOverlay(null);
      if (typeof s.setOmniOpen === "function") s.setOmniOpen(true);
      if (typeof s.setDashboardOpen === "function") s.setDashboardOpen(true);
      return { ok: true, status: "flow_focused", intent: n };
    }
    if (n.surface === "canvas") {
      if (typeof s.setOverlay === "function") s.setOverlay(null);
      return { ok: true, status: "canvas_focused", intent: n };
    }
    if (n.surface === "research") {
      // Research remains conversational unless a research overlay exists
      if (typeof s.setOverlay === "function" && s.overlay !== undefined) {
        // leave chat; research overlay not assumed
      }
      return { ok: true, status: "remain_chat", intent: n };
    }
    if (n.surface === "preview") {
      if (n.action === "close") {
        if (typeof s.closePreview === "function") s.closePreview();
        return { ok: true, status: "preview_closed", intent: n };
      }
      if (typeof s.openPreview === "function") {
        s.openPreview({
          projectId: n.context?.projectId || n.context?.workspaceId || null,
          path: n.context?.filePath || n.context?.path || "index.html",
          title: n.context?.title || "Preview",
        });
        return { ok: true, status: "preview_opened", intent: n };
      }
      if (n.required) {
        return { ok: false, status: "surface_unavailable", surface: "preview", intent: n };
      }
      return { ok: true, status: "optional_unavailable", surface: "preview", intent: n };
    }
    if (n.surface === "artifact") {
      if (typeof store.openOverlay === "function") store.openOverlay("artifacts");
      else if (typeof store.setOverlay === "function") store.setOverlay("artifacts");
      return { ok: true, status: "opened", surface: "artifact", intent: n };
    }
    if (n.surface === "deployment") {
      if (typeof store.openOverlay === "function") store.openOverlay("deployment");
      else if (typeof store.setOverlay === "function") store.setOverlay("deployment");
      return { ok: true, status: "opened", surface: "deployment", intent: n };
    }
    if (n.surface === "web") {
      if (typeof store.openOverlay === "function") store.openOverlay("web");
      else if (typeof store.setOverlay === "function") store.setOverlay("web");
      else if (typeof store.openWebIntel === "function") store.openWebIntel(n.context || {});
      return { ok: true, status: "opened", surface: "web", intent: n };
    }
    if (n.surface === "logs") {
      if (typeof store.openOverlay === "function") store.openOverlay("logs");
      else if (typeof store.setOverlay === "function") store.setOverlay("logs");
      return { ok: true, status: "opened", surface: "logs", intent: n };
    }
    return { ok: true, status: "noop", intent: n };
  } catch (e) {
    console.warn("[surface_intent] apply failed", e);
    if (n.required) {
      return { ok: false, status: "surface_unavailable", error: String(e?.message || e), intent: n };
    }
    return { ok: true, status: "optional_failed", error: String(e?.message || e), intent: n };
  }
}
