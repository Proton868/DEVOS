/**
 * NuhaTaskProgress — execution progress panel driven by real SSE/task statuses.
 * Does not invent backend steps, percentages, or fake activity.
 */
import React, { useEffect, useMemo, useState } from "react";
import {
  PHASE_DEFS,
  refineStates,
  formatElapsed,
  groupStepsByPhase,
  isTerminalStatus,
  TERMINAL_FAIL,
  TERMINAL_OK,
} from "./nuhaTaskProgressLogic";

/**
 * @param {object} props
 * @param {string|null} props.currentStatus - latest orchestration status from SSE
 * @param {string[]} props.seenStatuses - ordered unique statuses observed this run
 * @param {boolean} props.active - streaming / in progress
 * @param {number|null} props.startedAt - epoch ms when stream began (real only)
 * @param {string} props.personaName
 * @param {string|null} props.headline
 * @param {string|null} props.detail - phase note from backend only
 * @param {string|null} props.streamState - open | closed | aborted | error
 */
export default function NuhaTaskProgress({
  currentStatus = null,
  seenStatuses = [],
  active = false,
  startedAt = null,
  personaName = "Nuha",
  headline = null,
  detail = null,
  streamState = null,
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active || !startedAt) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active, startedAt]);

  const steps = useMemo(
    () => refineStates(seenStatuses, currentStatus, active),
    [seenStatuses, currentStatus, active]
  );
  const sections = useMemo(() => groupStepsByPhase(steps), [steps]);

  const elapsed =
    startedAt != null ? formatElapsed(Math.max(0, now - startedAt)) : null;

  const terminalFail = TERMINAL_FAIL.has(currentStatus);
  const terminalOk = TERMINAL_OK.has(currentStatus) || (!active && !terminalFail && seenStatuses.some((s) => ["validation_completed", "artifact_created"].includes(s)));

  let statusLine = "Idle";
  if (active) statusLine = "Running";
  else if (streamState === "aborted") statusLine = "Stream closed";
  else if (streamState === "error") statusLine = "Stream error";
  else if (terminalFail) statusLine = currentStatus === "cancelled" ? "Cancelled" : "Failed";
  else if (terminalOk || isTerminalStatus(currentStatus)) statusLine = "Completed";
  else if (seenStatuses.length) statusLine = currentStatus || "Finished";

  const description =
    headline ||
    (active
      ? `${personaName} is executing a governed mission…`
      : terminalFail
        ? "Mission did not complete successfully."
        : terminalOk
          ? "Mission finished."
          : null);

  if (!active && !seenStatuses.length && !currentStatus) {
    return null;
  }

  return (
    <div
      className="sp-nuha-task-progress"
      role="status"
      aria-live="polite"
      aria-busy={active ? "true" : "false"}
      data-status={currentStatus || ""}
      data-stream={streamState || ""}
    >
      <div className="sp-nuha-task-header">
        <span className="sp-nuha-task-title">Mission progress</span>
        <span className="sp-nuha-task-badge">{statusLine}</span>
        {elapsed && (
          <span className="sp-nuha-task-elapsed" title="Elapsed since stream start">
            {elapsed}
          </span>
        )}
      </div>
      {description && <div className="sp-nuha-task-headline">{description}</div>}
      <div className="sp-nuha-task-phases">
        {sections.map((sec) => (
          <div key={sec.id} className="sp-nuha-task-section">
            <div className="sp-nuha-task-section-title">{sec.title}</div>
            <ul className="sp-nuha-task-steps">
              {sec.steps.map((s) => (
                <li key={s.id} className={`sp-nuha-task-step st-${s.state}`}>
                  <span className="sp-nuha-task-icon" aria-hidden="true">
                    {s.state === "completed"
                      ? "✓"
                      : s.state === "failed"
                        ? "✕"
                        : s.state === "active"
                          ? "◉"
                          : "○"}
                  </span>
                  <span className="sp-nuha-task-label">
                    {s.label}
                    <span className="sp-nuha-task-sr"> {s.state}</span>
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
