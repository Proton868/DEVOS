/**
 * Lightweight DAG mission glow over the orchestration canvas.
 * Does not replace workflow script nodes — shows Nuha mission steps only.
 * Visual states reflect backend node status only — no fake progress.
 */
import React, { useEffect } from "react";
import useOsStore from "../store/osStore";
import { api } from "../../services/api";
import {
  nodeGlowState, planGlowState, isActiveMission,
  nodeStatusLabel, planNeedsHitl, planPivotInfo, honestEmptyMissionMessage,
} from "../orchestrationUi";

export default function MissionGlowOverlay() {
  const mission = useOsStore((s) => s.orchestrationMission);
  const applyOrchestrationPlan = useOsStore((s) => s.applyOrchestrationPlan);
  const activePlanId = useOsStore((s) => s.activePlanId);
  const openPersonaProfile = useOsStore((s) => s.openPersonaProfile);

  useEffect(() => {
    if (!activePlanId || !isActiveMission(mission?.status)) return undefined;
    let alive = true;
    const tick = async () => {
      try {
        const plan = await api.getOrchestration(activePlanId);
        if (alive && plan) applyOrchestrationPlan(plan);
      } catch { /* offline — leave last known state */ }
    };
    tick();
    const iv = setInterval(tick, 4000);
    return () => { alive = false; clearInterval(iv); };
  }, [activePlanId, mission?.status, applyOrchestrationPlan]);

  const empty = honestEmptyMissionMessage(mission);
  if (empty && !mission) return null;
  if (!mission || !(mission.nodes || []).length) {
    return (
      <div className="sp-mission-glow empty" data-testid="mission-glow-empty" aria-live="polite">
        <span className="sp-mission-empty">{empty || "NO ACTIVE MISSION"}</span>
      </div>
    );
  }

  const planGlow = planGlowState(mission.status);
  const needsHitl = planNeedsHitl(mission);
  const pivot = planPivotInfo(mission);

  return (
    <div
      className={`sp-mission-glow plan-${planGlow}`}
      data-testid="mission-glow"
      data-plan-status={mission.status || ""}
      role="region"
      aria-label={`Mission: ${mission.goal || "active"}`}
    >
      <div className="sp-mission-glow-head">
        <span className="sp-mission-goal">{mission.goal || "Mission"}</span>
        <span className={`sp-mission-status glow-${planGlow}`} data-status={mission.status}>
          {nodeStatusLabel(mission.status)}
        </span>
      </div>

      {needsHitl && (
        <div className="sp-mission-hitl" role="status" data-testid="mission-hitl-banner">
          Waiting for your approval — use Mission bar notifications to Approve or Deny.
        </div>
      )}

      {pivot?.reached && (
        <div className="sp-mission-pivot" role="status" data-testid="mission-pivot-banner">
          Pivot reached ({pivot.action}). Automatic rollback of this external effect is unavailable.
        </div>
      )}

      <ul className="sp-mission-nodes" aria-label="Mission nodes">
        {(mission.nodes || []).map((n) => {
          const glow = nodeGlowState(n.status);
          const label = nodeStatusLabel(n.status);
          return (
            <li
              key={n.id}
              className={`sp-mission-node glow-${glow}`}
              data-node-id={n.id}
              data-status={n.status || ""}
              data-glow={glow}
            >
              <button
                type="button"
                className="sp-mission-node-btn"
                onClick={() => n.persona_id && openPersonaProfile?.(n.persona_id)}
                title={n.description || n.id}
              >
                <span className="sp-mission-node-persona">{n.persona_id || "nuha"}</span>
                <span className="sp-mission-node-desc">{n.description || n.id}</span>
                <span className={`sp-mission-node-state`} aria-label={label}>{label}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
