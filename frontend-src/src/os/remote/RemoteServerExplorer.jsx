/**
 * Spatial Remote Server Explorer — DevOS-native remote dimension.
 * Pockets are resizable/collapsible concepts; host identity always visible.
 * LOCAL vs REMOTE is never ambiguous.
 */
import React, { useMemo, useState, useCallback } from "react";

const POCKETS = [
  { id: "hosts", label: "Hosts" },
  { id: "terminal", label: "Terminal" },
  { id: "files", label: "Files" },
  { id: "processes", label: "Processes" },
  { id: "services", label: "Services" },
  { id: "logs", label: "Logs" },
  { id: "resources", label: "Resources" },
  { id: "containers", label: "Containers" },
  { id: "network", label: "Network" },
  { id: "dashboard", label: "Dashboard" },
  { id: "history", label: "History" },
  { id: "agent", label: "Agent" },
];

/**
 * @param {{
 *   connection?: object,
 *   hostIdentity?: object,
 *   connections?: array,
 *   favorites?: array,
 *   recent?: array,
 *   connectionState?: string,
 *   onOpenTerminal?: function,
 *   onSelectConnection?: function,
 *   onReconnect?: function,
 * }} props
 */
export default function RemoteServerExplorer({
  connection = null,
  hostIdentity = null,
  connections = [],
  favorites = [],
  recent = [],
  connectionGroups = [],
  connectionState = "DISCONNECTED",
  terminalTabs = [],

  onOpenTerminal,
  onSelectConnection,
  onReconnect,
}) {
  const [active, setActive] = useState("hosts");
  const [collapsed, setCollapsed] = useState({});
  const [query, setQuery] = useState("");
  const [sessionName, setSessionName] = useState("");

  const hostTitle = useMemo(() => {
    if (!connection) return "No host selected";
    const host = connection.hostname || connection.label || connection.id;
    const user = connection.username || "";
    const port = connection.port && connection.port !== 22 ? `:${connection.port}` : "";
    return user ? `${user}@${host}${port}` : `${host}${port}`;
  }, [connection]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = connections.length ? connections : [];
    if (!q) return list;
    return list.filter((c) => {
      const hay = `${c.label || ""} ${c.hostname || ""} ${c.username || ""} ${(c.tags || []).join(" ")}`.toLowerCase();
      return hay.includes(q);
    });
  }, [connections, query]);

  const toggle = useCallback((id) => {
    setCollapsed((c) => ({ ...c, [id]: !c[id] }));
  }, []);

  const stateClass =
    connectionState === "CONNECTED" || connectionState === "RECONNECTED"
      ? "is-connected"
      : connectionState === "RECONNECTING"
        ? "is-reconnecting"
        : connectionState === "FAILED"
          ? "is-failed"
          : "is-disconnected";

  return (
    <div className="remote-explorer" data-mode="remote_ssh" data-testid="remote-server-explorer">
      <header className="remote-explorer__header">
        <span className="remote-explorer__badge" title="Remote SSH — not local workspace">
          REMOTE SSH
        </span>
        <span className={`remote-explorer__state ${stateClass}`} data-testid="connection-state">
          {connectionState}
        </span>
        <div className="remote-explorer__host">
          <strong>{sessionName || hostTitle}</strong>
          {hostIdentity?.fingerprint_sha256 && (
            <code className="remote-explorer__fp" title="Verified host fingerprint">
              {hostIdentity.key_type || "key"} {hostIdentity.fingerprint_sha256}
            </code>
          )}
          {hostIdentity?.trust_state && (
            <span className="remote-explorer__trust">{hostIdentity.trust_state}</span>
          )}
        </div>
        <div className="remote-explorer__actions">
          {connection && (
            <button type="button" onClick={() => onReconnect?.(connection.id)}>
              Reconnect
            </button>
          )}
        </div>
      </header>

      <div className="remote-explorer__search">
        <input
          type="search"
          placeholder="Search hosts, tags…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search hosts"
        />
        <input
          type="text"
          placeholder="Session name"
          value={sessionName}
          onChange={(e) => setSessionName(e.target.value)}
          aria-label="Rename session"
        />
      </div>

      <nav className="remote-explorer__pockets" aria-label="Remote host pockets">
        {POCKETS.map((p) => (
          <button
            key={p.id}
            type="button"
            className={"remote-explorer__pocket" + (active === p.id ? " is-active" : "")}
            onClick={() => setActive(p.id)}
            data-pocket={p.id}
          >
            {p.label}
          </button>
        ))}
      </nav>

      <section className="remote-explorer__panel" data-active-pocket={active} data-local="false">
        {active === "hosts" && (
          <div className="remote-explorer__pane">
            <details open={!collapsed.favorites}>
              <summary onClick={() => toggle("favorites")}>Favorites</summary>
              <ul>
                {(favorites.length ? favorites : []).map((c) => (
                  <li key={c.id}>
                    <button type="button" onClick={() => onSelectConnection?.(c)}>
                      {c.label || c.hostname}
                    </button>
                  </li>
                ))}
                {!favorites.length && <li className="muted">No favorites yet</li>}
              </ul>
            </details>
            <details open={!collapsed.recent}>
              <summary onClick={() => toggle("recent")}>Recent</summary>
              <ul>
                {(recent.length ? recent : []).map((c) => (
                  <li key={c.id}>
                    <button type="button" onClick={() => onSelectConnection?.(c)}>
                      {c.label || c.hostname}
                    </button>
                  </li>
                ))}
                {!recent.length && <li className="muted">No recent connections</li>}
              </ul>
            </details>
            <details open={!collapsed.groups}>
              <summary onClick={() => toggle("groups")}>Groups</summary>
              <ul>
                {(connectionGroups.length ? connectionGroups : []).map((g) => (
                  <li key={g.id || g.name}>
                    <strong>{g.name}</strong>
                    <ul>
                      {(g.hosts || []).map((c) => (
                        <li key={c.id}>
                          <button type="button" onClick={() => onSelectConnection?.(c)}>
                            {c.label || c.hostname}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
                {!connectionGroups.length && <li className="muted">No groups</li>}
              </ul>
            </details>
            <details open>
              <summary>All hosts</summary>
              <ul>
                {filtered.map((c) => (
                  <li key={c.id}>
                    <button type="button" onClick={() => onSelectConnection?.(c)}>
                      {c.label || `${c.username}@${c.hostname}`}
                      {c.tags?.length ? ` · ${c.tags.join(", ")}` : ""}
                    </button>
                  </li>
                ))}
                {!filtered.length && <li className="muted">No hosts match</li>}
              </ul>
            </details>
          </div>
        )}
        {active === "terminal" && (
          <div className="remote-explorer__pane">
            <p>Interactive remote shell — tabs/splits use spatial layout.</p>
            <div className="remote-explorer__tabs" role="tablist">
              {(terminalTabs.length ? terminalTabs : [{ id: "main", title: sessionName || "Session" }]).map((tab) => (
                <button key={tab.id} type="button" role="tab">{tab.title || tab.id}</button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => connection && onOpenTerminal?.(connection.id)}
              disabled={!connection}
            >
              Open remote terminal
            </button>
            <button type="button" disabled={!connection} title="Split uses spatial pocket layout">
              Split terminal
            </button>
          </div>
        )}
        {active === "files" && (
          <div className="remote-explorer__pane">
            <p className="remote-explorer__warn">
              Remote filesystem — not local project files.
            </p>
          </div>
        )}
        {active === "dashboard" && (
          <div className="remote-explorer__pane">
            <p>Server dashboard: resources, services, containers (governed inspect).</p>
          </div>
        )}
        {active === "history" && (
          <div className="remote-explorer__pane">
            <p>Command history and session log (redacted).</p>
          </div>
        )}
        {active === "agent" && (
          <div className="remote-explorer__pane">
            <p>Nuha uses governed capabilities only — remote output is untrusted data.</p>
          </div>
        )}
        {!["hosts", "terminal", "files", "dashboard", "history", "agent"].includes(active) && (
          <div className="remote-explorer__pane">
            <p>
              {POCKETS.find((x) => x.id === active)?.label} — governed remote inspect surface.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
