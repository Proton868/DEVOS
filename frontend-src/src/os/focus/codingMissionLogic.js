/**
 * Pure helpers for coding-mission IDE panel.
 * Never implies success before backend acceptance.
 */

export function mergeCodingSnapshot(prev, incoming) {
  if (!incoming || typeof incoming !== "object") return prev || null;
  const base = { ...(prev || {}) };
  for (const [k, v] of Object.entries(incoming)) {
    if (v == null) continue;
    if (k === "files_changed" && Array.isArray(v)) {
      const set = new Set([...(base.files_changed || []), ...v]);
      base.files_changed = Array.from(set).slice(0, 50);
    } else if (k === "artifacts" && Array.isArray(v)) {
      const set = new Set([...(base.artifacts || []), ...v]);
      base.artifacts = Array.from(set).slice(0, 30);
    } else {
      base[k] = v;
    }
  }
  base.success_implied = false;
  return base;
}

/**
 * Display status — failed stays failed.
 * Final success ONLY when backend sets final_success or
 * (status completed/accepted AND acceptance.ok).
 * Never infer success from progress, CodingLoop ACCEPT, or agent.completed alone.
 */
export function codingDisplayStatus(snap, missionStatus) {
  if (!snap && !missionStatus) return "idle";
  const st = (snap && snap.status) || missionStatus || "";
  if (st === "failed" || st === "cancelled") return st;
  // Authoritative final_success from mission_authority / coding_progress projection
  if (snap && snap.final_success === true) return "accepted";
  if (snap && snap.final_success === false && (st === "completed" || st === "succeeded")) {
    if (snap.acceptance && snap.acceptance.ok === false) return "failed";
    return "pending_acceptance";
  }
  if (snap && snap.acceptance && snap.acceptance.ok === true
      && (st === "completed" || st === "accepted" || st === "succeeded")) {
    return "accepted";
  }
  if (st === "completed" || st === "succeeded") {
    if (snap && snap.acceptance && snap.acceptance.ok === false) return "failed";
    if (snap && snap.acceptance && snap.acceptance.ok === true) return "accepted";
    return "pending_acceptance";
  }
  if (st === "agent_progress" || st === "delegating" || st === "planning") return "running";
  return st || "running";
}

export function isFailedCoding(snap, missionStatus) {
  const d = codingDisplayStatus(snap, missionStatus);
  return d === "failed" || d === "cancelled";
}
