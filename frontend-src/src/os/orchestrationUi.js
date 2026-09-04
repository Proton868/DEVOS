/**
 * Map orchestration node/plan status → spatial glow states (canvas + dashboard).
 * Reuses existing EXECUTING / SUCCESS / FAILED visual language — no new design system.
 */
export function nodeGlowState(status) {
  const s = (status || "").toString().toLowerCase();
  if (["running", "executing"].includes(s)) return "EXECUTING";
  if (["verifying", "recovering", "replanning", "thinking", "waiting_for_user", "awaiting_approval"].includes(s)) return "THINKING";
  if (["queued", "ready", "authorized", "authorization_pending", "pending"].includes(s)) return "QUEUED";
  if (["verified", "completed", "success", "succeeded"].includes(s)) return "SUCCESS";
  if (["failed", "blocked", "blocked_by_dependency", "denied"].includes(s)) return "FAILED";
  if (["cancelled", "cancelling", "cancellation_requested"].includes(s)) return "IDLE";
  return "IDLE";
}

export function planGlowState(status) {
  const s = (status || "").toString().toLowerCase();
  if (["running", "delegating", "delegated", "queued", "executing"].includes(s)) return "EXECUTING";
  if (["verifying", "recovering", "replanning", "planning", "context_gathering", "waiting_for_user"].includes(s)) return "THINKING";
  if (["plan_ready", "action_requested", "authorization_pending", "authorized"].includes(s)) return "QUEUED";
  if (["completed", "verified"].includes(s)) return "SUCCESS";
  if (["failed", "blocked", "verification_failed"].includes(s)) return "FAILED";
  if (["cancelled", "cancelling"].includes(s)) return "IDLE";
  return "IDLE";
}

export function isActiveMission(status) {
  const s = (status || "").toLowerCase();
  return ![
    "completed", "failed", "cancelled", "blocked", "idle", "",
  ].includes(s);
}
