/**
 * Spatial Remote Server Explorer — remote dimension for SSH hosts.
 * Visually distinct from local IDE files (mode badge + host identity).
 */
import React, { useMemo, useState } from "react";

const POCKETS = [
  { id: "terminal", label: "Terminal" },
  { id: "files", label: "Files" },
  { id: "processes", label: "Processes" },
  { id: "services", label: "Services" },
  { id: "logs", label: "Logs" },
  { id: "resources", label: "Resources" },
  { id: "containers", label: "Containers" },
  { id: "network", label: "Network" },
  { id: "agent", label: "Agent" },
];

export default function RemoteServerExplorer({
  connection = null,
  hostIdentity = null,
  onOpenTerminal,
}) {
  const [active, setActive] = useState("files");

  const hostTitle = useMemo(() => {
    if (!connection) return "No host selected";
    const host = connection.hostname || connection.label || connection.id;
    const user = connection.username || "";
    const port = connection.port && connection.port !== 22 ? `:${connection.port}` : "";
    return user ? `${user}@${host}${port}` : `${host}${port}`;
  }, [connection]);

  return (
    <div className="remote-explorer" data-mode="remote_ssh" data-testid="remote-server-explorer">
      <header className="remote-explorer__header">
        <span className="remote-explorer__badge" title="Remote SSH — not local workspace">
          REMOTE SSH
        </span>
        <div className="remote-explorer__host">
          <strong>{hostTitle}</strong>
          {hostIdentity?.fingerprint_sha256 && (
            <code className="remote-explorer__fp" title="Verified host fingerprint">
              {hostIdentity.key_type || "key"} {hostIdentity.fingerprint_sha256}
            </code>
          )}
          {hostIdentity?.trust_state && (
            <span className="remote-explorer__trust">{hostIdentity.trust_state}</span>
          )}
        </div>
      </header>

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
        {active === "terminal" && (
          <div className="remote-explorer__pane">
            <p>Interactive remote shell for this host.</p>
            <button
              type="button"
              onClick={() => connection && onOpenTerminal?.(connection.id)}
              disabled={!connection}
            >
              Open remote terminal
            </button>
          </div>
        )}
        {active === "files" && (
          <div className="remote-explorer__pane">
            <p className="remote-explorer__warn">
              Remote filesystem — paths are not local project files.
            </p>
            <ul className="remote-explorer__placeholder">
              <li>/</li>
              <li className="muted">Connect and list via governed SFTP</li>
            </ul>
          </div>
        )}
        {active === "agent" && (
          <div className="remote-explorer__pane">
            <p>Nuha acts through governed SSH capabilities only — no raw credentials.</p>
          </div>
        )}
        {!["terminal", "files", "agent"].includes(active) && (
          <div className="remote-explorer__pane">
            <p>
              {POCKETS.find((x) => x.id === active)?.label} — remote inspect surface
              (governed read commands).
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
