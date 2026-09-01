/**
 * DevOS NodeLogs — Inline log streaming for workflow nodes.
 * Shows real-time logs flowing from nodes with filtering and search.
 */
import React, { useState, useRef, useEffect, memo } from "react";
import { Search, Download, ChevronDown, ChevronRight, Terminal } from "lucide-react";

const LOG_LEVELS = ["debug", "info", "warn", "error"];
const LEVEL_COLORS = {
  debug: "var(--text-3)",
  info: "var(--accent)",
  warn: "var(--yellow)",
  error: "var(--red)",
};

const NodeLogs = memo(function NodeLogs({ nodeId, logs = [], onExport }) {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [levelFilter, setLevelFilter] = useState(null);
  const logEndRef = useRef(null);

  const filtered = logs.filter((log) => {
    if (levelFilter && log.level !== levelFilter) return false;
    if (query && !log.message.toLowerCase().includes(query.toLowerCase())) return false;
    return true;
  });

  useEffect(() => {
    if (autoScroll && !collapsed && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [filtered, autoScroll, collapsed]);

  const handleExport = () => {
    const text = logs.map((l) => "[" + l.timestamp + "] [" + l.level + "] " + l.message).join("\n");
    if (onExport) onExport(text);
    else {
      const blob = new Blob([text], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "node-" + nodeId + "-logs.txt";
      a.click();
      URL.revokeObjectURL(url);
    }
  };

  return (
    <div className="devos-node-logs">
      <div className="devos-logs-header">
        <button
          className="devos-logs-toggle"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? "Expand logs" : "Collapse logs"}
        >
          {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
          <Terminal size={11} />
          <span>Logs ({filtered.length})</span>
        </button>
        <div className="devos-logs-actions">
          {!collapsed && (
            <>
              <div className="devos-logs-search">
                <Search size={11} />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Filter logs..."
                  className="devos-logs-search-input"
                  aria-label="Filter logs"
                />
              </div>
              <div className="devos-logs-levels">
                {LOG_LEVELS.map((level) => (
                  <button
                    key={level}
                    onClick={() => setLevelFilter(levelFilter === level ? null : level)}
                    className={"devos-logs-level" + (levelFilter === level ? " active" : "")}
                    style={{ color: LEVEL_COLORS[level] }}
                  >
                    {level}
                  </button>
                ))}
              </div>
              <label className="devos-logs-autoscroll">
                <input
                  type="checkbox"
                  checked={autoScroll}
                  onChange={(e) => setAutoScroll(e.target.checked)}
                />
                <span>Auto-scroll</span>
              </label>
            </>
          )}
          <button onClick={handleExport} className="devos-logs-export" title="Export logs" aria-label="Export logs">
            <Download size={11} />
          </button>
        </div>
      </div>
      {!collapsed && (
        <div className="devos-logs-body" role="log" aria-live="polite">
          {filtered.length === 0 ? (
            <div className="devos-logs-empty">No logs</div>
          ) : (
            filtered.map((log, i) => (
              <div key={i} className={"devos-log-line devos-log-" + log.level}>
                <span className="devos-log-time">{log.timestamp}</span>
                <span className="devos-log-level-tag" style={{ color: LEVEL_COLORS[log.level] }}>
                  {log.level.toUpperCase()}
                </span>
                <span className="devos-log-message">{log.message}</span>
              </div>
            ))
          )}
          <div ref={logEndRef} />
        </div>
      )}
    </div>
  );
});

export default NodeLogs;
