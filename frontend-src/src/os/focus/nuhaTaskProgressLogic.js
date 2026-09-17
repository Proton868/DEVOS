/**
 * Pure helpers for Nuha task progress UI.
 * Maps only statuses actually emitted by /api/chat SSE — no invented stages.
 */

export const PHASE_DEFS = [
  {
    id: "plan",
    title: "Plan",
    steps: [
      { id: "planning", statusKeys: ["planning"], label: "Understand goal and plan mission" },
      { id: "plan_created", statusKeys: ["plan_created"], label: "Create durable execution plan" },
    ],
  },
  {
    id: "execute",
    title: "Execute",
    steps: [
      { id: "delegating", statusKeys: ["delegating"], label: "Delegate to specialists (A2A)" },
      {
        id: "agent_work",
        statusKeys: ["agent_progress", "worker_completed"],
        label: "Specialist / agent work",
      },
    ],
  },
  {
    id: "verify",
    title: "Verify",
    steps: [
      {
        id: "validation",
        statusKeys: ["validation_started", "validation_completed"],
        label: "Validate results",
      },
      {
        id: "artifact",
        statusKeys: ["artifact_created"],
        label: "Record artifacts",
      },
    ],
  },
];

export const KNOWN_STATUS_KEYS = PHASE_DEFS.flatMap((p) =>
  p.steps.flatMap((s) => s.statusKeys)
);

/** Terminal statuses from chat SSE final payload / failures */
export const TERMINAL_FAIL = new Set(["failed", "cancelled"]);
export const TERMINAL_OK = new Set(["completed"]);

export function isTerminalStatus(status) {
  if (!status) return false;
  return TERMINAL_FAIL.has(status) || TERMINAL_OK.has(status);
}

/**
 * Build step states from real SSE observations only.
 * @param {string[]} seenStatuses
 * @param {string|null} current
 * @param {boolean} active - stream still open
 */
export function refineStates(seenStatuses, current, active) {
  const seen = new Set((seenStatuses || []).filter((s) => KNOWN_STATUS_KEYS.includes(s)));
  const terminalFail = TERMINAL_FAIL.has(current);
  const terminalOk =
    TERMINAL_OK.has(current) ||
    (!active &&
      (seen.has("validation_completed") ||
        seen.has("artifact_created") ||
        TERMINAL_OK.has(current)));

  const order = PHASE_DEFS.flatMap((p) => p.steps);
  let maxIdx = -1;
  order.forEach((s, i) => {
    if (s.statusKeys.some((k) => seen.has(k))) maxIdx = i;
  });
  if (current) {
    order.forEach((s, i) => {
      if (s.statusKeys.includes(current)) maxIdx = Math.max(maxIdx, i);
    });
  }

  return order.map((s, i) => {
    if (terminalFail && current && s.statusKeys.includes(current)) {
      return { ...s, state: "failed" };
    }
    const hit =
      s.statusKeys.some((k) => seen.has(k)) ||
      (current && s.statusKeys.includes(current));
    if (!hit) return { ...s, state: "pending" };
    if (current && s.statusKeys.includes(current) && !terminalOk && !terminalFail) {
      return { ...s, state: "active" };
    }
    if (i < maxIdx || terminalOk) return { ...s, state: "completed" };
    if (i === maxIdx && !terminalOk) return { ...s, state: "active" };
    return { ...s, state: "completed" };
  });
}

/** Elapsed only when startedAt is a real number; never invent duration. */
export function formatElapsed(ms) {
  if (ms == null || Number.isNaN(ms) || ms < 0) return null;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${r}s`;
}

export function groupStepsByPhase(steps) {
  const byId = Object.fromEntries(steps.map((s) => [s.id, s]));
  return PHASE_DEFS.map((p) => ({
    ...p,
    steps: p.steps.map((s) => byId[s.id]).filter(Boolean),
  }));
}
