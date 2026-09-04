/**
 * Lightweight DAG mission glow over the orchestration canvas.
 * Does not replace workflow script nodes — shows Nuha mission steps only.
 */
import React, { useEffect } from "react";
import useOsStore from "../store/osStore";
import { api } from "../../services/api";
import { nodeGlowState, planGlowState, isActiveMission } from "../orchestrationUi";

export default function MissionGlowOverlay() {
  const mission = useOsStore((s) => s.orchestrationMission);
  const applyOrchestrationPlan = useOsStore((s) => s.applyOrchestrationPlan);
  const activePlanId = useOsStore((s) => s.activePlanId);
  const openPersonaProfile = useOsStore((s) => s.openPersonaProfile);

  // Poll durable plan while mission is active
  useEffect(() => {
    if (!activePlanId || !isActiveMission(mission?.status)) return undefined;
    let alive = true;
    const tick = async () => {
      try {
        const plan = await api.getOrchestration(activePlanId);
        if (alive && plan) applyOrchestrationPlan(plan);
      } catch { /* offline */ }
    };
    tick();
    const iv = setInterval(tick, 4000);
    return () => { alive = false; clearInterval(iv); };
  }, [activePlanId, mission?.status]);

  if (!mission || !(mission.nodes || []).length) return null;

  const pst = planGlowState(mission.status);

  return (
    <div className={`sp-mission-glow-layer plan-${pst.toLowerCase()}`} aria-label="Nuha mission">
      <div className="sp-mission-glow-label">
        <span className={`live ${isActiveMission(mission.status) ? "on" : ""}`} />
        NUHA · {mission.status || "—"}
      </div>
      <div className="sp-mission-glow-row">
        {(mission.nodes || []).map((n, i) => {
          const st = nodeGlowState(n.status);
          return (
            <React.Fragment key={n.id}>
              {i > 0 && <span className="sp-mission-edge" />}
              <button
                type="button"
                className={`sp-canvas-mission-node st-${st}`}
                onClick={() => n.persona_id && openPersonaProfile(n.persona_id)}
                title={n.description || n.id}
              >
                <span className="cmn-persona">{n.persona_id || n.id}</span>
                <span className="cmn-status">{st}</span>
              </button>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
