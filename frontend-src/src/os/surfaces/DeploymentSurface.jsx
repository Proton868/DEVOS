import React, { useEffect, useState } from "react";
import { getToken, baseUrl } from "../../services/api";

export default function DeploymentSurface({ projectId }) {
  const [providers, setProviders] = useState([]);
  const [cf, setCf] = useState(null);
  const [last, setLast] = useState(null);
  const pid = projectId || "default";
  useEffect(() => {
    const tok = getToken();
    if (!tok) return;
    const h = { Authorization: `Bearer ${tok}` };
    fetch(`${baseUrl()}/api/delivery/deploy/providers`, { headers: h })
      .then((r) => r.json()).then((d) => setProviders(d.providers || [])).catch(() => {});
    fetch(`${baseUrl()}/api/delivery/cloudflared/info`, { headers: h })
      .then((r) => r.json()).then(setCf).catch(() => {});
  }, []);
  const deploy = async (provider) => {
    const tok = getToken();
    const r = await fetch(`${baseUrl()}/api/delivery/${encodeURIComponent(pid)}/deploy`, {
      method: "POST",
      headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    setLast(await r.json());
  };
  return (
    <div className="sp-surface sp-deploy-surface" data-surface="deployment">
      <h3>Deployments</h3>
      <div className="sp-meta">Providers: {(providers || []).join(", ") || "—"}</div>
      <div className="sp-meta">cloudflared: {cf ? (cf.available ? "available" : "missing") : "…"}</div>
      <div className="sp-actions">
        {(providers || []).map((p) => (
          <button key={p} type="button" className="sp-btn" onClick={() => deploy(p)}>{p}</button>
        ))}
      </div>
      {last ? <pre className="sp-json">{JSON.stringify(last, null, 2)}</pre> : null}
    </div>
  );
}
