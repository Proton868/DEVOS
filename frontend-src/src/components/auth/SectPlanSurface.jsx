import React, { useState } from "react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";
import "./AuthSurfaces.css";

const PLANS = [
  { id: "recruit", title: "Recruit", blurb: "Begin your journey. Nuha, core workspace, essential missions." },
  { id: "outer_sect", title: "Outer Sect", blurb: "Build more. Expanded workspace and project workflows." },
  { id: "inner_sect", title: "Inner Sect", blurb: "Go deeper. Richer orchestration and advanced AI workflows." },
  { id: "conclave", title: "Conclave", blurb: "Organization scale. Collaboration and larger workflows where available." },
];

export default function SectPlanSurface({ onDone }) {
  const setUser = useStore((s) => s.setUser);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const choose = async (planId) => {
    setBusy(planId);
    setError(null);
    try {
      const user = await api.selectPlan?.(planId) ?? await fetch("/api/account/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${localStorage.getItem("devos_token") || ""}` },
        body: JSON.stringify({ plan: planId }),
      }).then(async (r) => { if (!r.ok) throw new Error(await r.text()); return r.json(); });
      setUser(user);
      onDone?.(user);
    } catch (e) {
      setError(e.message || "Could not save plan");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="sp-auth-env" data-surface="sect-plan">
      <div className="sp-auth-ambient" aria-hidden />
      <div className="sp-auth-panel sp-auth-panel-wide">
        <h1 className="sp-auth-title">Choose your place within DevOS</h1>
        <p className="sp-auth-lead">
          Your path shapes orientation and defaults. It does not bypass security —
          every action still passes Specialty Policy and UCIP.
        </p>
        {error && <div className="sp-auth-error" role="alert">{error}</div>}
        <div className="sp-sect-grid">
          {PLANS.map((p) => (
            <button
              key={p.id}
              type="button"
              className="sp-sect-card"
              disabled={!!busy}
              onClick={() => choose(p.id)}
            >
              <span className="sp-sect-name">{p.title}</span>
              <span className="sp-sect-blurb">{p.blurb}</span>
              {busy === p.id && <span className="sp-sect-busy">Entering…</span>}
            </button>
          ))}
        </div>
        <p className="sp-auth-footnote">Elder and Hegemon are not public paths.</p>
      </div>
    </div>
  );
}
