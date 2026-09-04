import React, { useEffect, useRef, useState } from "react";
import { getToken, baseUrl } from "../../services/api";

export default function RuntimeLogsSurface({ projectId }) {
  const [lines, setLines] = useState([]);
  const pid = projectId || "default";
  const esRef = useRef(null);
  useEffect(() => {
    const tok = getToken();
    // recent first
    fetch(`${baseUrl()}/api/delivery/${encodeURIComponent(pid)}/runtime/logs/recent`, {
      headers: tok ? { Authorization: `Bearer ${tok}` } : {},
    }).then((r) => r.json()).then((d) => setLines(d.logs || [])).catch(() => {});
    // EventSource cannot set Authorization easily; use fetch stream fallback via recent poll
    const iv = setInterval(() => {
      fetch(`${baseUrl()}/api/delivery/${encodeURIComponent(pid)}/runtime/logs/recent`, {
        headers: tok ? { Authorization: `Bearer ${tok}` } : {},
      }).then((r) => r.json()).then((d) => setLines(d.logs || [])).catch(() => {});
    }, 2000);
    return () => clearInterval(iv);
  }, [pid]);
  return (
    <div className="sp-surface sp-logs-surface" data-surface="runtime-logs">
      <h3>Application Logs</h3>
      <pre className="sp-logs">
        {(lines || []).map((l, i) => (
          <div key={i}>[{l.stream}] {l.line}</div>
        ))}
      </pre>
    </div>
  );
}
