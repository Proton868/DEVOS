/**
 * GhostTerminal — the contextual PyRunner runtime surface.
 * Real output only:
 *  - script run logs from /api/scripts/{id}/runs (poll while a run is live)
 *  - interactive shell via /api/terminal/{project}/run
 * Appears beneath the focused node/code; SHIFT+ENTER with a selected
 * node invokes contextual execution.
 */
import React, { useEffect, useRef, useState, useCallback } from "react";
import { X, Minus, Maximize2, Folder, Trash2, ChevronRight } from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

function classify(line) {
  const l = line.toLowerCase();
  if (l.includes("[stdout]")) return "lg-stdout";
  if (l.includes("[info]")) return "lg-info";
  if (l.includes("[success]")) return "lg-success";
  if (l.includes("[error]") || l.includes("traceback") || l.includes("failed")) return "lg-error";
  if (l.startsWith("$") || l.includes("[cmd]")) return "lg-cmd";
  return "lg-log";
}

export default function GhostTerminal() {
  const { terminal, closeTerminal, nodes } = useOsStore();
  const currentProject = useStore((s) => s.currentProject);
  const [lines, setLines] = useState([]);
  const [tall, setTall] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bodyRef = useRef(null);
  const pollRef = useRef(null);

  const node = terminal.nodeId ? nodes.find((n) => n.id === terminal.nodeId) : null;
  const sid = node?.scriptId;

  const push = useCallback((text, cls) => {
    setLines((ls) => [...ls.slice(-400), { text, cls: cls || classify(text), t: Date.now() }]);
  }, []);

  // Real script log streaming: poll the latest run while it is live
  useEffect(() => {
    if (!terminal.open || sid == null) { return; }
    let cancelled = false;
    let lastRunId = null;
    const seenLines = new Set();

    async function poll() {
      try {
        const runs = await api.flowScriptRuns(sid, 1);
        const latest = Array.isArray(runs) ? runs[0] : runs?.runs?.[0];
        if (!latest || cancelled) return;
        const status = (latest.status || latest.state || "").toUpperCase();
        const runId = latest.id ?? latest.run_id ?? "latest";
        if (runId !== lastRunId) {
          if (lastRunId !== null) push(`[INFO] New run started (id: ${runId})`, "lg-info");
          lastRunId = runId;
          const logs = latest.logs ?? latest.output ?? latest.stdout ?? "";
          const arr = Array.isArray(logs) ? logs : String(logs || "").split("\n");
          for (const l of arr) {
            if (l && !seenLines.has(l)) { seenLines.add(l); push(l); }
          }
        }
        if (["RUNNING", "EXECUTING", "IN_PROGRESS", "STARTED", "QUEUED", "PENDING"].includes(status)) {
          useOsStore.getState().setNodeState(sid, "EXECUTING");
        } else if (status) {
          const mapped = ["SUCCESS", "SUCCEEDED", "OK", "DONE", "COMPLETED"].includes(status) ? "SUCCESS" :
            ["FAILED", "ERROR", "FAILURE"].includes(status) ? "FAILED" : "IDLE";
          useOsStore.getState().setNodeState(sid, mapped);
          if (mapped === "SUCCESS") push("[SUCCESS] Operation complete.", "lg-success");
          if (mapped === "FAILED") push("[ERROR] Run failed — see execution history.", "lg-error");
          cancelled = true; // terminal state reached; stop polling
          return;
        }
      } catch { /* offline */ }
      if (!cancelled) pollRef.current = setTimeout(poll, 2500);
    }
    push(`[STDOUT] PyRunner connected — streaming workflow node ${terminal.nodeId}`, "lg-stdout");
    poll();
    return () => { cancelled = true; clearTimeout(pollRef.current); };
  }, [terminal.open, sid]);

  useEffect(() => {
    bodyRef.current?.scrollTo(0, bodyRef.current.scrollHeight);
  }, [lines]);

  const runCommand = async () => {
    const cmd = input.trim();
    if (!cmd || busy) return;
    setInput("");
    setBusy(true);
    push(`$ ${cmd}`, "lg-cmd");
    try {
      const r = await (api.runCommand || api.runTerminalCommand)(cmd, 60);
      const out = r?.output ?? r?.stdout ?? r?.result ?? "";
      const err = r?.error ?? r?.stderr ?? "";
      const code = r?.exit_code ?? r?.exitCode;
      String(out).split("\n").forEach((l) => { if (l !== undefined && l !== null) push(String(l)); });
      if (err) String(err).split("\n").filter((l) => l.length).forEach((l) => push(l, "lg-error"));
      if (!out && !err) push("[INFO] (no output)", "lg-info");
      if (typeof code === "number" && code !== 0) {
        push(`[exit ${code}]`, "lg-error");
      }
    } catch (e) {
      push(`[ERROR] ${e.message || e}`, "lg-error");
    } finally {
      setBusy(false);
    }
  };

  if (!terminal.open && !terminal.pinned) return null;

  return (
    <div className={`sp-terminal ${tall ? "tall" : ""}`}>
      <div className="sp-surface-head" style={{ borderBottom: "1px solid var(--sp-border)" }}>
        <span>PYRUNNER LIVE TERMINAL</span>
        {node && <span className="sub">· {node.title}</span>}
        <span className="spacer" />
        <button className="sp-iconbtn" title="Run history" onClick={() => useOsStore.getState().setOverlay("history")}><Folder size={14} /></button>
        <button className="sp-iconbtn" title="Clear" onClick={() => setLines([])}><Trash2 size={14} /></button>
        <button className="sp-iconbtn" title={tall ? "Collapse" : "Expand"} onClick={() => setTall((t) => !t)}>
          {tall ? <Minus size={14} /> : <Maximize2 size={13} />}
        </button>
        <button className="sp-iconbtn" title="Close" onClick={closeTerminal}><X size={15} /></button>
      </div>
      <div className="sp-term-spark" aria-hidden>
        {Array.from({ length: 32 }).map((_, i) => (
          <i key={i} style={{ height: `${4 + ((Math.sin(i * 0.7 + lines.length * 0.05) + 1) / 2) * 20}px` }} />
        ))}
      </div>
      <div className="sp-terminal-body" ref={bodyRef}>
        {lines.length === 0 && (
          <div className="lg-info">[INFO] Connected to project "{currentProject}". Select a node and press SHIFT+ENTER to execute, or type a shell command below.</div>
        )}
        {lines.map((l, i) => (
          <div key={i} className={l.cls}>{l.text}</div>
        ))}
      </div>
      <div className="sp-terminal-input">
        <span className="prompt">{currentProject} $</span>
        <input
          value={input}
          placeholder={busy ? "running…" : "shell command (real execution via DevOS runtime)"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") runCommand(); }}
          disabled={busy}
        />
        {busy && <ChevronRight size={14} />}
      </div>
    </div>
  );
}
