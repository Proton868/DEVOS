import React, { useMemo, useState } from "react";
import useStore from "../../store/useStore";
import "./AuthSurfaces.css";

const STEPS = [
  { id: "nuha", title: "Nuha", body: "Nuha is your primary AI orchestrator. Talk to her, plan work, start missions, and delegate to specialists." },
  { id: "omni", title: "Omni", body: "Omni is universal command access — navigate surfaces, run commands, and reach quick actions without leaving the spatial shell." },
  { id: "ide", title: "Workspace / IDE", body: "Code, files, and terminal live here with project context. Artifacts from missions appear in the same workspace." },
  { id: "mission", title: "Mission / Flow", body: "Plans, DAG progress, agent execution, and verification are visible as mission state — not a separate product." },
  { id: "personas", title: "Agent Fleet", body: "Nuha and specialist personas (Writer, Storyteller, Script Writer, research, code, …) are selected through the existing persona system." },
  { id: "preview", title: "Artifacts / Preview", body: "Verified workspace artifacts can be previewed inside DevOS with isolation and optional browser handoff." },
  { id: "web", title: "Web Intelligence", body: "Public-web research and multi-page crawling produce evidence under UCIP — never a bypass of authentication walls." },
  { id: "settings", title: "Settings & Profile", body: "Your profile, plan label, appearance, and preferences live in Settings. Replay this tour from Settings when you need it." },
];

export default function SpatialTour({ onDone }) {
  const setUser = useStore((s) => s.setUser);
  const user = useStore((s) => s.user);
  const [idx, setIdx] = useState(0);
  const step = STEPS[idx];
  const progress = useMemo(() => `${idx + 1} / ${STEPS.length}`, [idx]);

  const persist = async (status) => {
    const token = localStorage.getItem("devos_token") || "";
    const r = await fetch("/api/account/onboarding", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ status }),
    });
    if (r.ok) {
      const next = await r.json();
      setUser(next);
      onDone?.(next);
    } else {
      onDone?.(user);
    }
  };

  return (
    <div className="sp-tour-overlay" data-surface="spatial-tour" role="dialog" aria-modal="true">
      <div className="sp-tour-dim" aria-hidden />
      <div className="sp-tour-hand" aria-hidden>☝</div>
      <div className="sp-tour-card">
        <div className="sp-tour-progress">{progress}</div>
        <h2 className="sp-tour-title">{step.title}</h2>
        <p className="sp-tour-body">{step.body}</p>
        <div className="sp-tour-actions">
          <button type="button" className="sp-auth-ghost" onClick={() => persist("SKIPPED")}>Skip Tour</button>
          <div className="sp-tour-nav">
            <button type="button" className="sp-auth-ghost" disabled={idx === 0} onClick={() => setIdx((i) => Math.max(0, i - 1))}>Back</button>
            {idx < STEPS.length - 1 ? (
              <button type="button" className="sp-auth-primary" onClick={() => setIdx((i) => i + 1)}>Next</button>
            ) : (
              <button type="button" className="sp-auth-primary" onClick={() => persist("COMPLETED")}>Enter DevOS</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
