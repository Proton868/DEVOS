/**
 * DevOS SmartTerminal
 * Dual-mode terminal:
 *   - Smart Mode: Logs stream from nodes until expanded to full terminal
 *   - Manual Mode: Traditional persistent shell (xterm.js)
 *   - Fullscreen Mode: Real shell
 *
 * A little switch: ○ Smart  ○ Terminal
 */
import React, { useState, useRef, useEffect, useCallback } from "react";
import { Terminal as TerminalIcon, Maximize2, Minimize2, Zap, ChevronDown } from "lucide-react";
import { api, subscribeToEvents } from "../../services/api";

export const TERMINAL_MODES = {
  SMART: "smart",
  MANUAL: "manual",
  FULLSCREEN: "fullscreen",
};

export default function SmartTerminal({ config = {} }) {
  const [mode, setMode] = useState(config.mode || TERMINAL_MODES.SMART);
  const [smartLogs, setSmartLogs] = useState([]);
  const [expanded, setExpanded] = useState(false);
  const [manualInput, setManualInput] = useState("");
  const [manualHistory, setManualHistory] = useState([]);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const logEndRef = useRef(null);
  const manualInputRef = useRef(null);

  // Smart mode: subscribe to log events
  useEffect(() => {
    if (mode !== TERMINAL_MODES.SMART) return;
    const unsub = subscribeToEvents(
      (event) => {
        if (event.type === "log" || event.type === "terminal.output") {
          setSmartLogs((prev) => [...prev.slice(-200), {
            timestamp: new Date().toISOString().split("T")[1].split(".")[0],
            level: event.data?.level || "info",
            message: event.data?.message || event.data?.output || "",
            source: event.data?.source || "system",
          }]);
        }
      },
      () => {}
    );
    return () => unsub();
  }, [mode]);

  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [smartLogs, manualHistory]);

  const runManualCommand = useCallback(async () => {
    const cmd = manualInput.trim();
    if (!cmd) return;
    setManualInput("");
    setManualHistory((prev) => [...prev, { type: "input", text: "$ " + cmd }]);

    if (cmd === "clear") {
      setManualHistory([]);
      return;
    }

    try {
      const result = await api.runTerminalCommand(cmd);
      setManualHistory((prev) => [...prev, { type: "output", text: result.output || result }]);
    } catch (e) {
      setManualHistory((prev) => [...prev, { type: "error", text: e.message }]);
    }
  }, [manualInput]);

  const toggleFullscreen = () => {
    setIsFullscreen((f) => !f);
  };

  // Fullscreen mode
  if (isFullscreen || mode === TERMINAL_MODES.FULLSCREEN) {
    return (
      <div className="devos-smart-terminal devos-terminal-fullscreen">
        <div className="devos-terminal-header">
          <div className="devos-terminal-title">
            <TerminalIcon size={13} />
            <span>Terminal — Fullscreen</span>
          </div>
          <div className="devos-terminal-controls">
            <ModeToggle mode={mode} setMode={setMode} />
            <button onClick={toggleFullscreen} className="devos-terminal-btn" aria-label="Exit fullscreen">
              <Minimize2 size={13} />
            </button>
          </div>
        </div>
        <div className="devos-terminal-body" onClick={() => manualInputRef.current?.focus()}>
          <TerminalOutput
            mode={mode}
            smartLogs={smartLogs}
            manualHistory={manualHistory}
            logEndRef={logEndRef}
          />
          {mode === TERMINAL_MODES.MANUAL && (
            <div className="devos-terminal-input-line">
              <span className="devos-terminal-prompt">$</span>
              <input
                ref={manualInputRef}
                type="text"
                value={manualInput}
                onChange={(e) => setManualInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") runManualCommand(); }}
                className="devos-terminal-input"
                aria-label="Terminal input"
                autoFocus
              />
            </div>
          )}
        </div>
      </div>
    );
  }

  // Smart mode (collapsed) — shows streaming logs inline
  if (mode === TERMINAL_MODES.SMART && !expanded) {
    return (
      <div className="devos-smart-terminal devos-smart-terminal-collapsed">
        <div className="devos-terminal-header" onClick={() => setExpanded(true)} style={{ cursor: "pointer" }}>
          <div className="devos-terminal-title">
            <Zap size={13} />
            <span>Smart Terminal</span>
            {smartLogs.length > 0 && (
              <span className="devos-terminal-badge">{smartLogs.length}</span>
            )}
          </div>
          <div className="devos-terminal-controls">
            <ModeToggle mode={mode} setMode={setMode} compact />
            <button onClick={(e) => { e.stopPropagation(); setExpanded(true); }} className="devos-terminal-btn" aria-label="Expand terminal">
              <ChevronDown size={13} />
            </button>
            <button onClick={(e) => { e.stopPropagation(); toggleFullscreen(); }} className="devos-terminal-btn" aria-label="Fullscreen terminal">
              <Maximize2 size={13} />
            </button>
          </div>
        </div>
        {smartLogs.length > 0 && (
          <div className="devos-terminal-preview">
            <span className="devos-terminal-preview-line">
              {smartLogs[smartLogs.length - 1]?.message}
            </span>
          </div>
        )}
      </div>
    );
  }

  // Expanded / Manual mode
  return (
    <div className="devos-smart-terminal devos-smart-terminal-expanded">
      <div className="devos-terminal-header">
        <div className="devos-terminal-title">
          <TerminalIcon size={13} />
          <span>{mode === TERMINAL_MODES.SMART ? "Smart Terminal" : "Terminal"}</span>
        </div>
        <div className="devos-terminal-controls">
          <ModeToggle mode={mode} setMode={setMode} />
          {mode === TERMINAL_MODES.SMART && (
            <button onClick={() => setExpanded(false)} className="devos-terminal-btn" aria-label="Collapse terminal">
              <ChevronDown size={13} style={{ transform: "rotate(180deg)" }} />
            </button>
          )}
          <button onClick={toggleFullscreen} className="devos-terminal-btn" aria-label="Fullscreen terminal">
            <Maximize2 size={13} />
          </button>
        </div>
      </div>
      <div className="devos-terminal-body" onClick={() => mode === TERMINAL_MODES.MANUAL && manualInputRef.current?.focus()}>
        <TerminalOutput
          mode={mode}
          smartLogs={smartLogs}
          manualHistory={manualHistory}
          logEndRef={logEndRef}
        />
        {mode === TERMINAL_MODES.MANUAL && (
          <div className="devos-terminal-input-line">
            <span className="devos-terminal-prompt">$</span>
            <input
              ref={manualInputRef}
              type="text"
              value={manualInput}
              onChange={(e) => setManualInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") runManualCommand(); }}
              className="devos-terminal-input"
              aria-label="Terminal input"
              autoFocus
            />
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Mode toggle switch: ○ Smart  ○ Terminal
 */
function ModeToggle({ mode, setMode, compact = false }) {
  return (
    <div className={"devos-mode-toggle" + (compact ? " compact" : "")} role="radiogroup" aria-label="Terminal mode">
      <button
        onClick={() => setMode(TERMINAL_MODES.SMART)}
        className={mode === TERMINAL_MODES.SMART ? "active" : ""}
        role="radio"
        aria-checked={mode === TERMINAL_MODES.SMART}
        title="Smart mode — logs stream from nodes"
      >
        <Zap size={10} /> Smart
      </button>
      <button
        onClick={() => setMode(TERMINAL_MODES.MANUAL)}
        className={mode === TERMINAL_MODES.MANUAL ? "active" : ""}
        role="radio"
        aria-checked={mode === TERMINAL_MODES.MANUAL}
        title="Manual mode — traditional shell"
      >
        <TerminalIcon size={10} /> Terminal
      </button>
    </div>
  );
}

/**
 * Terminal output display.
 */
function TerminalOutput({ mode, smartLogs, manualHistory, logEndRef }) {
  if (mode === TERMINAL_MODES.SMART) {
    if (smartLogs.length === 0) {
      return <div className="devos-terminal-empty">Waiting for output...</div>;
    }
    return (
      <div className="devos-terminal-output">
        {smartLogs.map((log, i) => (
          <div key={i} className={"devos-terminal-line devos-log-" + log.level}>
            <span className="devos-log-time">{log.timestamp}</span>
            {log.source && <span className="devos-log-source">[{log.source}]</span>}
            <span className="devos-log-message">{log.message}</span>
          </div>
        ))}
        <div ref={logEndRef} />
      </div>
    );
  }

  // Manual mode
  if (manualHistory.length === 0) {
    return <div className="devos-terminal-empty">$ Type a command and press Enter...</div>;
  }
  return (
    <div className="devos-terminal-output">
      {manualHistory.map((entry, i) => (
        <div key={i} className={"devos-terminal-line devos-terminal-" + entry.type}>
          <pre>{entry.text}</pre>
        </div>
      ))}
      <div ref={logEndRef} />
    </div>
  );
}
