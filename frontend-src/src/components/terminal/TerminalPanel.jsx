import React, { useEffect, useRef } from "react";
import { Terminal as XTerm } from "xterm";
import { FitAddon } from "xterm-addon-fit";
import { WebLinksAddon } from "xterm-addon-web-links";
import "xterm/css/xterm.css";
import { X } from "lucide-react";
import useStore from "../../store/useStore";

// Same-origin-safe derivation, matching services/api.js's BASE convention.
const WS_BASE = process.env.REACT_APP_DEVOS_URL
  ? process.env.REACT_APP_DEVOS_URL.replace(/^http/, "ws")
  : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;

export default function TerminalPanel() {
  const { terminalOpen, setTerminalOpen, currentProject } = useStore();
  const containerRef = useRef(null);
  const termRef = useRef(null);
  const wsRef = useRef(null);
  const fitRef = useRef(null);

  useEffect(() => {
    if (!terminalOpen || !containerRef.current) return;

    const term = new XTerm({
      theme: {
        background: "#0d1117",
        foreground: "#c9d1d9",
        cursor: "#58a6ff",
        selectionBackground: "#264f78",
        black: "#484f58", red: "#ff7b72", green: "#3fb950",
        yellow: "#d29922", blue: "#58a6ff", magenta: "#bc8cff",
        cyan: "#39c5cf", white: "#b1bac4",
      },
      fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      fontSize: 13,
      cursorBlink: true,
      convertEol: true,
      scrollback: 5000,
    });

    const fitAddon = new FitAddon();
    const linksAddon = new WebLinksAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(linksAddon);
    term.open(containerRef.current);
    fitAddon.fit();
    termRef.current = term;
    fitRef.current = fitAddon;

    const projectId = currentProject || "default";
    const ws = new WebSocket(`${WS_BASE}/api/terminal/${projectId}/ws`);
    wsRef.current = ws;

    ws.onopen = () => {
      // Send auth token as first message (matching backend's auth_msg handling)
      const token = localStorage.getItem("devos_token") || "";
      ws.send(JSON.stringify({ token }));
      term.writeln("\x1b[32m● DevOS Terminal ready\x1b[0m");
    };
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "data") term.write(msg.data);
        if (msg.type === "error") term.writeln(`\x1b[31m${msg.message}\x1b[0m`);
        if (msg.type === "exit") term.writeln(`\x1b[33m[Process exited: ${msg.exitCode}]\x1b[0m`);
      } catch {}
    };
    ws.onerror = () => term.writeln("\x1b[31m● WebSocket error\x1b[0m");
    ws.onclose = () => term.writeln("\x1b[33m● Disconnected\x1b[0m");

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });

    const resizeObs = new ResizeObserver(() => {
      fitAddon.fit();
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    });
    resizeObs.observe(containerRef.current);

    return () => {
      resizeObs.disconnect();
      term.dispose();
      ws.close();
      termRef.current = null;
      wsRef.current = null;
    };
  }, [terminalOpen, currentProject]);

  if (!terminalOpen) return null;

  return (
    <div className="terminal-panel">
      <div className="terminal-header">
        <span>⚡ Terminal</span>
        <button onClick={() => setTerminalOpen(false)}><X size={13} /></button>
      </div>
      <div ref={containerRef} className="terminal-body" />
    </div>
  );
}
