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

/** Display status — failed stays failed; success only if acceptance.ok */
export function codingDisplayStatus(snap, missionStatus) {
  if (!snap && !missionStatus) return "idle";
  const st = (snap && snap.status) || missionStatus || "";
  if (st === "failed" || st === "cancelled") return st;
  if (snap && snap.acceptance && snap.acceptance.ok === true) return "accepted";
  if (st === "completed" || st === "succeeded") {
    // completed without acceptance still not "success" for UI badge
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
