/**
 * Web Intelligence crawl surface — SpatialOS window pattern (not a dashboard redesign).
 * Backend is authoritative; this is presentation only.
 */
import React, { useEffect, useState } from "react";
import useOsStore from "../store/osStore";
import { api } from "../../services/api";

export default function WebIntelSurface() {
  const crawlId = useOsStore((s) => s.webIntel?.crawlId);
  const close = useOsStore((s) => s.closeWebIntel);
  const [crawl, setCrawl] = useState(null);
  const [pages, setPages] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!crawlId) return undefined;
    let alive = true;
    const tick = async () => {
      try {
        const c = await api.webCrawlGet?.(crawlId);
        const p = await api.webCrawlPages?.(crawlId);
        if (!alive) return;
        setCrawl(c);
        setPages(p?.pages || []);
        setError(null);
      } catch (e) {
        if (alive) setError(e?.message || "unavailable");
      }
    };
    tick();
    const iv = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(iv); };
  }, [crawlId]);

  if (!crawlId) return null;

  const by = {};
  for (const p of pages) by[p.status] = (by[p.status] || 0) + 1;
  const stats = (() => { try { return JSON.parse(crawl?.stats_json || "{}"); } catch { return {}; } })();

  return (
    <div className="sp-surface sp-webintel-surface" data-surface="web" data-testid="web-intel-surface">
      <div className="sp-surface-head">
        <h3>Web Intelligence</h3>
        <button type="button" className="sp-btn ghost" onClick={() => close?.()}>Close</button>
      </div>
      {error && <div className="sp-preview-error">{error}</div>}
      <div className="sp-meta">Root: {crawl?.root_url || "—"}</div>
      <div className="sp-meta">Status: <strong data-crawl-status={crawl?.status}>{crawl?.status || "…"}</strong></div>
      <div className="sp-meta">
        Fetched {stats.pages_fetched || by.EXTRACTED || 0}
        {" · "}Failed {stats.pages_failed || by.FAILED || 0}
        {" · "}Blocked {by.BLOCKED || 0}
        {" · "}Dup {by.DUPLICATE || 0}
        {" · "}Queued {by.QUEUED || 0}
      </div>
      <div className="sp-meta">Requests {stats.requests || 0} · Bytes {stats.bytes || 0}</div>
      <ul className="sp-webintel-pages">
        {pages.slice(0, 40).map((p) => (
          <li key={p.page_id} data-status={p.status}>
            <span className="sp-webintel-status">{p.status}</span>
            <span className="sp-webintel-url">{p.normalized_url || p.url}</span>
          </li>
        ))}
      </ul>
      {crawlId && (
        <div className="sp-actions">
          <button type="button" className="sp-btn" onClick={() => api.webCrawlCancel?.(crawlId)}>Cancel</button>
          <button type="button" className="sp-btn" onClick={() => api.webCrawlResume?.(crawlId)}>Resume</button>
        </div>
      )}
    </div>
  );
}
