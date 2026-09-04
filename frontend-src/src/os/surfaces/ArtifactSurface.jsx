import React, { useEffect, useState } from "react";
import { getToken, baseUrl } from "../../services/api";

export default function ArtifactSurface({ projectId }) {
  const [data, setData] = useState({ artifacts: [], count: 0 });
  const [detect, setDetect] = useState(null);
  const pid = projectId || "default";
  useEffect(() => {
    const tok = getToken();
    if (!tok) return;
    const h = { Authorization: `Bearer ${tok}` };
    fetch(`${baseUrl()}/api/files/${encodeURIComponent(pid)}/artifacts`, { headers: h })
      .then((r) => r.json()).then(setData).catch(() => {});
    fetch(`${baseUrl()}/api/files/${encodeURIComponent(pid)}/app-detect`, { headers: h })
      .then((r) => r.json()).then(setDetect).catch(() => {});
  }, [pid]);
  return (
    <div className="sp-surface sp-artifact-surface" data-surface="artifact">
      <h3>Artifacts</h3>
      {detect ? <div className="sp-meta">App: {detect.kind} / {detect.framework || "—"}</div> : null}
      <div className="sp-meta">{data.count} files</div>
      <ul className="sp-artifact-list">
        {(data.artifacts || []).slice(0, 50).map((a) => (
          <li key={a.path}>
            <code>{a.path}</code> <span>{a.type}</span> <span>{a.size}B</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
