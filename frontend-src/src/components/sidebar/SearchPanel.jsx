import React, { useState, useRef } from "react";
import { Search, X, File, ChevronRight } from "lucide-react";
import useStore from "../../store/useStore";
import { api, getLanguageFromPath } from "../../services/api";

export default function SearchPanel() {
  const { searchOpen, setSearchOpen, searchResults, setSearchResults, openFile } = useStore();
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState("semantic"); // semantic | text
  const inputRef = useRef(null);

  const doSearch = async () => {
    if (!query.trim()) return;
    setLoading(true);
    try {
      const result = await api.searchFiles(query.trim());
      setSearchResults(result.files || []);
    } catch (e) {
      setSearchResults([]);
    } finally {
      setLoading(false);
    }
  };

  const openResult = async (result) => {
    try {
      const { content } = await api.readFile(result.path);
      openFile({
        path: result.path,
        name: result.path.split("/").pop(),
        content,
        language: getLanguageFromPath(result.path),
      });
    } catch {}
  };

  if (!searchOpen) return null;

  return (
    <div className="search-panel">
      <div className="search-header">
        <Search size={13} />
        <span>Search</span>
        <button className="search-close" onClick={() => setSearchOpen(false)}><X size={13} /></button>
      </div>

      <div className="search-mode-tabs">
        <button className={mode === "semantic" ? "active" : ""} onClick={() => setMode("semantic")}>Semantic</button>
        <button className={mode === "text" ? "active" : ""} onClick={() => setMode("text")}>Text</button>
      </div>

      <div className="search-input-row">
        <input
          ref={inputRef}
          className="search-input"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") doSearch(); }}
          placeholder={mode === "semantic" ? "Search by meaning…" : "Search text…"}
          autoFocus
        />
        <button className="search-run" onClick={doSearch} disabled={loading || !query.trim()}>
          {loading ? <span className="spinner" /> : <Search size={13} />}
        </button>
      </div>

      {mode === "semantic" && (
        <p className="search-hint">Semantic search finds code by concept, not just keywords.</p>
      )}

      <div className="search-results">
        {searchResults.length === 0 && query && !loading && (
          <p className="search-empty">No results found.</p>
        )}
        {searchResults.map((r, i) => (
          <div key={i} className="search-result-item" onClick={() => openResult(r)}>
            <div className="search-result-header">
              <File size={11} />
              <span className="search-result-path">{r.path}</span>
              {r.score !== undefined && (
                <span className="search-result-score">{Math.round(r.score * 100)}%</span>
              )}
            </div>
            {r.chunk && (
              <div className="search-result-loc">
                Lines {r.chunk.startLine + 1}–{r.chunk.endLine + 1}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
