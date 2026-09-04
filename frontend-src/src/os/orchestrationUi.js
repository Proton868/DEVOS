/**
 * Map orchestration node/plan status → spatial glow states (canvas + dashboard).
 * Reuses existing EXECUTING / SUCCESS / FAILED visual language — no new design system.
 */
export function nodeGlowState(status) {
  const s = (status || "").toString().toLowerCase();
  if (["running", "executing"].includes(s)) return "EXECUTING";
  if (["waiting_for_user", "awaiting_approval", "authorization_pending"].includes(s)) return "WAITING";
  if (["verifying"].includes(s)) return "VERIFYING";
  if (["recovering", "replanning", "thinking"].includes(s)) return "THINKING";
  if (["queued", "ready", "authorized", "pending"].includes(s)) return "QUEUED";
  if (["verified", "completed", "success", "succeeded"].includes(s)) return "SUCCESS";
  if (["failed", "blocked", "blocked_by_dependency", "denied", "verification_failed"].includes(s)) return "FAILED";
  if (["cancelled", "cancelling", "cancellation_requested"].includes(s)) return "CANCELLED";
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

/** Distinct visual status for node chips / accessibility labels */
export function nodeStatusLabel(status) {
  const s = (status || "").toString().toLowerCase();
  if (["running", "executing"].includes(s)) return "Running";
  if (["verifying"].includes(s)) return "Verifying";
  if (["recovering", "replanning"].includes(s)) return "Recovering";
  if (["waiting_for_user", "awaiting_approval", "authorization_pending"].includes(s)) return "Waiting for you";
  if (["queued", "ready", "authorized", "pending"].includes(s)) return "Queued";
  if (["verified", "completed", "success", "succeeded"].includes(s)) return "Completed";
  if (["failed", "blocked", "denied", "verification_failed"].includes(s)) return "Failed";
  if (["cancelled", "cancelling", "cancellation_requested"].includes(s)) return "Cancelled";
  return "Idle";
}

/** Glow class refinement — WAITING is distinct from THINKING for HITL */
export function nodeGlowStateStrict(status) {
  const s = (status || "").toString().toLowerCase();
  if (["waiting_for_user", "awaiting_approval", "authorization_pending"].includes(s)) return "WAITING";
  if (["cancelled", "cancelling", "cancellation_requested"].includes(s)) return "CANCELLED";
  if (["verifying"].includes(s)) return "VERIFYING";
  return nodeGlowState(status);
}

export function planNeedsHitl(planOrMission) {
  if (!planOrMission) return false;
  const st = (planOrMission.status || "").toLowerCase();
  if (["waiting_for_user", "authorization_pending", "awaiting_approval"].includes(st)) return true;
  return !!(planOrMission.requires_hitl || planOrMission.requiresHitl);
}

export function planPivotInfo(planOrMission) {
  if (!planOrMission) return null;
  const pivot = planOrMission.pivot_reached || planOrMission.pivotReached
    || planOrMission.saga?.pivot_reached;
  if (!pivot) return null;
  return {
    reached: true,
    action: planOrMission.pivot_action || planOrMission.saga?.pivot_action || "external side effect",
    stepId: planOrMission.pivot_step_id || planOrMission.saga?.pivot_step_id,
  };
}

export function honestEmptyMissionMessage(mission) {
  if (!mission) return "NO ACTIVE MISSION";
  if (!(mission.nodes || []).length) return "NO MISSION NODES";
  return null;
}
