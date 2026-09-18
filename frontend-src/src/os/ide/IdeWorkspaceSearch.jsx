import React, { useState } from "react";
import { Search } from "lucide-react";
import { api } from "../../services/api";
import useOsStore from "../store/osStore";

export default function IdeWorkspaceSearch() {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState([]);
  const openEditor = useOsStore((s) => s.openEditor);

  const run = async (e) => {
    e?.preventDefault?.();
    const q = query.trim();
    if (!q) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.searchFiles(q, 40);
      const items = Array.isArray(r) ? r : r?.results || r?.items || [];
      setResults(items);
    } catch (err) {
      setError(err.message || String(err));
      setResults([]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sp-ide-search">
      <form className="sp-ide-search-form" onSubmit={run}>
        <Search size={14} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search workspace…"
          aria-label="Search workspace"
        />
        <button type="submit" className="sp-pv-btn" disabled={busy}>
          {busy ? "…" : "Search"}
        </button>
      </form>
      {error && <div className="sp-ide-search-err">{error}</div>}
      <ul className="sp-ide-search-results">
        {results.map((item, i) => {
          const path = item.path || item.file || item.filename || String(item);
          const line = item.line || item.line_number;
          const snippet = item.snippet || item.preview || item.content || "";
          return (
            <li key={`${path}:${line}:${i}`}>
              <button
                type="button"
                className="sp-ide-search-hit"
                onClick={() => openEditor({ file: path })}
              >
                <span className="sp-ide-search-path">
                  {path}
                  {line != null ? `:${line}` : ""}
                </span>
                {snippet ? <span className="sp-ide-search-snip">{String(snippet).slice(0, 120)}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>
      {!busy && query && !results.length && !error && (
        <div className="sp-ide-search-empty">No matches</div>
      )}
    </div>
  );
}
