/**
 * NuhaTaskProgress — execution progress panel driven by real SSE/task statuses.
 * Does not invent backend steps; only maps known orchestration status values.
 */
import React, { useEffect, useMemo, useState } from "react";

/** Ordered phases derived from statuses emitted by /api/chat SSE (see AICopilot). */
const PHASE_DEFS = [
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

const ALL_KEYS = PHASE_DEFS.flatMap((p) => p.steps.flatMap((s) => s.statusKeys));

function stepState(step, seen, current, terminalFail) {
  if (terminalFail && current && step.statusKeys.includes(current)) return "failed";
  if (step.statusKeys.some((k) => seen.has(k))) {
    if (current && step.statusKeys.includes(current) && !terminalFail) return "active";
    // completed if we have seen this step and moved past it, or terminal success
    if (current && step.statusKeys.includes(current)) return "active";
    return "completed";
  }
  if (current && step.statusKeys.includes(current)) return "active";
  return "pending";
}

function refineStates(seen, current, terminalFail, terminalOk) {
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
    if (terminalFail && current && s.statusKeys.includes(current)) return { ...s, state: "failed" };
    if (s.statusKeys.some((k) => seen.has(k)) || (current && s.statusKeys.includes(current))) {
      if (current && s.statusKeys.includes(current) && !terminalOk && !terminalFail) {
        return { ...s, state: "active" };
      }
      if (i < maxIdx || terminalOk) return { ...s, state: "completed" };
      if (i === maxIdx && !terminalOk) return { ...s, state: "active" };
      return { ...s, state: "completed" };
    }
    return { ...s, state: "pending" };
  });
}

function formatElapsed(ms) {
  if (ms == null || ms < 0) return null;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${r}s`;
}

/**
 * @param {object} props
 * @param {string|null} props.currentStatus - latest orchestration status from SSE
 * @param {string[]} props.seenStatuses - ordered unique statuses observed this run
 * @param {boolean} props.active - streaming / in progress
 * @param {number|null} props.startedAt - epoch ms when work began
 * @param {string} props.personaName
 * @param {string|null} props.headline - optional human description
 * @param {string|null} props.detail - optional phase note from backend
 */
export default function NuhaTaskProgress({
  currentStatus = null,
  seenStatuses = [],
  active = false,
  startedAt = null,
  personaName = "Nuha",
  headline = null,
  detail = null,
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active || !startedAt) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active, startedAt]);

  const seen = useMemo(() => new Set(seenStatuses.filter((s) => ALL_KEYS.includes(s))), [seenStatuses]);
  const terminalFail = currentStatus === "failed" || currentStatus === "cancelled";
  const terminalOk =
    !active &&
    (seen.has("validation_completed") || seen.has("artifact_created") || currentStatus === "completed");

  const steps = useMemo(
    () => refineStates(seen, currentStatus, terminalFail, terminalOk),
    [seen, currentStatus, terminalFail, terminalOk],
  );

  const hasAny = seen.size > 0 || (currentStatus && ALL_KEYS.includes(currentStatus));
  if (!active && !hasAny) return null;

  const elapsed =
    startedAt != null ? formatElapsed((active ? now : Date.now()) - startedAt) : null;

  const statusLabel = terminalFail
    ? currentStatus === "cancelled"
      ? "Cancelled"
      : "Failed"
    : active
      ? "Working…"
      : terminalOk
        ? "Completed"
        : currentStatus || "In progress";

  const description =
    headline ||
    detail ||
    (active
      ? `${personaName} is running a governed mission.`
      : terminalFail
        ? `${personaName} stopped with status: ${currentStatus}.`
        : `${personaName} finished this run.`);

  // Group refined steps back into sections
  const sections = PHASE_DEFS.map((sec) => ({
    ...sec,
    steps: sec.steps.map((def) => {
      const refined = steps.find((x) => x.id === def.id);
      return { ...def, state: refined?.state || "pending" };
    }),
  })).filter((sec) => sec.steps.some((s) => s.state !== "pending") || active);

  return (
    <div
      className="sp-nuha-task"
      role="status"
      aria-live="polite"
      aria-label={`${personaName} task progress: ${statusLabel}`}
    >
      <div className="sp-nuha-task-head">
        <span className="sp-nuha-task-avatar" aria-hidden="true">
          ✦
        </span>
        <div className="sp-nuha-task-id">
          <span className="sp-nuha-task-name">{personaName}</span>
          <span className={`sp-nuha-task-badge st-${terminalFail ? "fail" : active ? "run" : "ok"}`}>
            {statusLabel}
          </span>
        </div>
        {elapsed && (
          <span className="sp-nuha-task-elapsed" title="Elapsed time">
            {elapsed}
          </span>
        )}
      </div>
      <p className="sp-nuha-task-desc">{description}</p>
      <div className="sp-nuha-task-sections">
        {sections.map((sec) => (
          <div key={sec.id} className="sp-nuha-task-section">
            <div className="sp-nuha-task-section-title">{sec.title}</div>
            <ul className="sp-nuha-task-steps">
              {sec.steps.map((s) => (
                <li key={s.id} className={`sp-nuha-task-step st-${s.state}`}>
                  <span className="sp-nuha-task-icon" aria-hidden="true">
                    {s.state === "completed" ? "✓" : s.state === "failed" ? "✕" : s.state === "active" ? "◉" : "○"}
                  </span>
                  <span className="sp-nuha-task-label">
                    {s.label}
                    <span className="sp-nuha-task-sr">{s.state}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {detail && detail !== description && (
        <div className="sp-nuha-task-detail">{detail}</div>
      )}
    </div>
  );
}
