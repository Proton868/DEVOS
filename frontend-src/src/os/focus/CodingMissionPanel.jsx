/**
 * Coding mission detail — driven by SSE `coding` snapshots only.
 * Does not invent success; failed missions stay failed.
 */
import React from "react";
import { codingDisplayStatus, isFailedCoding } from "./codingMissionLogic";

export default function CodingMissionPanel({ coding = null, missionStatus = null }) {
  if (!coding) return null;
  const display = codingDisplayStatus(coding, missionStatus);
  const failed = isFailedCoding(coding, missionStatus);

  return (
    <div
      className={`sp-coding-mission ${failed ? "is-failed" : ""}`}
      data-testid="coding-mission-panel"
      data-display={display}
      role="region"
      aria-label="Coding mission status"
    >
      <div className="sp-coding-mission-head">
        <span className="sp-coding-mission-title">Coding mission</span>
        <span className={`sp-coding-mission-badge st-${display}`}>{display}</span>
      </div>
      <dl className="sp-coding-mission-grid">
        {coding.mission_id && (
          <>
            <dt>Mission</dt>
            <dd className="mono">{coding.mission_id}</dd>
          </>
        )}
        {(coding.agent_id || coding.persona_id) && (
          <>
            <dt>Agent</dt>
            <dd>{coding.persona_id || coding.agent_id}</dd>
          </>
        )}
        {coding.current_task && (
          <>
            <dt>Task</dt>
            <dd>{coding.current_task}</dd>
          </>
        )}
        {(coding.provider || coding.model) && (
          <>
            <dt>Provider</dt>
            <dd>
              {coding.provider || "—"}
              {coding.model ? ` / ${coding.model}` : ""}
              {coding.fallback_provider ? ` → ${coding.fallback_provider}` : ""}
            </dd>
          </>
        )}
        {coding.retry_count != null && (
          <>
            <dt>Retries</dt>
            <dd>{coding.retry_count}</dd>
          </>
        )}
        {coding.command && (
          <>
            <dt>Command</dt>
            <dd className="mono">
              {coding.command}
              {coding.command_exit_code != null ? ` (exit ${coding.command_exit_code})` : ""}
            </dd>
          </>
        )}
        {coding.check_kind && (
          <>
            <dt>Check</dt>
            <dd>
              {coding.check_kind}: {coding.check_status || "—"}
            </dd>
          </>
        )}
        {(coding.files_changed || []).length > 0 && (
          <>
            <dt>Files</dt>
            <dd>
              <ul className="sp-coding-file-list">
                {(coding.files_changed || []).slice(0, 12).map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            </dd>
          </>
        )}
        {coding.validation && (
          <>
            <dt>Validation</dt>
            <dd>
              {coding.validation.works
                ? "works"
                : coding.validation.ok
                  ? "ok (not claimed working)"
                  : coding.validation.message || "pending"}
            </dd>
          </>
        )}
        {(coding.artifacts || []).length > 0 && (
          <>
            <dt>Artifacts</dt>
            <dd className="mono">{(coding.artifacts || []).slice(0, 8).join(", ")}</dd>
          </>
        )}
        {coding.error && (
          <>
            <dt>Error</dt>
            <dd className="err">{coding.error}</dd>
          </>
        )}
        {coding.acceptance && (
          <>
            <dt>Acceptance</dt>
            <dd>
              {coding.acceptance.ok ? "accepted" : `not accepted (${coding.acceptance.reason || "—"})`}
            </dd>
          </>
        )}
        {coding.evidence_id && (
          <>
            <dt>Evidence</dt>
            <dd className="mono">{coding.evidence_id}</dd>
          </>
        )}
        {coding.usage && (
          <>
            <dt>Token usage</dt>
            <dd className="mono" data-testid="coding-mission-usage">
              {[
                coding.usage.input_tokens != null && `in ${coding.usage.input_tokens}`,
                coding.usage.output_tokens != null && `out ${coding.usage.output_tokens}`,
                coding.usage.cached_tokens != null && `cache ${coding.usage.cached_tokens}`,
                coding.usage.total_tokens != null && `total ${coding.usage.total_tokens}`,
                coding.usage.latency_ms != null && `${coding.usage.latency_ms}ms`,
                coding.usage.retry_attempt != null && `retry ${coding.usage.retry_attempt}`,
                coding.usage.fallback_used && "fallback",
              ]
                .filter(Boolean)
                .join(" · ") || "—"}
            </dd>
          </>
        )}
      </dl>
      {(coding.command_stdout_tail || coding.command_stderr_tail) && (
        <pre className="sp-coding-mission-log" aria-label="Command output">
          {coding.command_stderr_tail || coding.command_stdout_tail}
        </pre>
      )}
    </div>
  );
}
