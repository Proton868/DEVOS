import React, { useEffect, useMemo, useState } from "react";
import { Play, RefreshCw, Loader2 } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

function StatusBadge({ status }) {
  const map = {
    success: { cls: "bg-emerald-500/15 text-emerald-400 border-emerald-500/25", label: "success" },
    failed: { cls: "bg-rose-500/15 text-rose-400 border-rose-500/25", label: "failed" },
    running: { cls: "bg-mint-400/15 text-mint-300 border-mint-400/30", label: "running", pulse: true },
  };
  const conf = map[status] || { cls: "bg-slate-500/15 text-slate-400 border-slate-500/25", label: status || "idle" };
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-semibold border ${conf.cls}`}>
      {conf.pulse && <span className="w-1.5 h-1.5 rounded-full bg-mint-400 animate-pulse" />}
      {conf.label}
    </span>
  );
}

function languageName(lang) {
  const map = { python: "Python", node: "Node.js", javascript: "Node.js", bash: "Bash", sh: "Shell" };
  return map[lang?.toLowerCase()] || lang || "Unknown";
}

function inferVenv(script) {
  const lang = script.language?.toLowerCase() || "python";
  if (lang === "python") return "venv / system";
  if (["node", "javascript"].includes(lang)) return "npm";
  if (["bash", "sh"].includes(lang)) return "sh";
  return lang;
}

export default function PyRunnerMatrix() {
  const { openScriptInEditor, setStatus } = useStore();
  const [scripts, setScripts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [runningId, setRunningId] = useState(null);

  const fetchData = async () => {
    setLoading(true);
    try {
      const list = await api.flowScripts();
      const withRuns = await Promise.all(
        list.map(async (s) => {
          try {
            const runs = await api.runInfos(s.id);
            const last = runs?.[0];
            return { ...s, lastRun: last || null };
          } catch {
            return { ...s, lastRun: null };
          }
        })
      );
      setScripts(withRuns);
    } catch (e) {
      setStatus("Failed to load scripts: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleRun = async (id) => {
    setRunningId(id);
    try {
      await api.runFlowScript(id);
      setStatus("Script run triggered");
      await fetchData();
    } catch (e) {
      setStatus("Run failed: " + e.message);
    } finally {
      setRunningId(null);
    }
  };

  const handleOpen = async (script) => {
    try {
      const full = await api.flowScript(script.id);
      openScriptInEditor({
        id: full.id,
        name: (full.name || `script_${full.id}`) + (full.language === "python" ? ".py" : ""),
        code: full.code || "",
        language: full.language || "python",
      });
      setStatus(`Opened ${full.name || "script"} in editor`);
    } catch (e) {
      setStatus("Open failed: " + e.message);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400 text-sm">
        <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Loading PyRunner scripts...
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full glass-panel overflow-hidden">
      <div className="h-10 flex items-center justify-between px-3 border-b border-white/[0.08]">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">PyRunner Matrix</span>
        <button onClick={fetchData} className="p-1.5 rounded-md hover:bg-white/[0.08] text-slate-400">
          <RefreshCw size={13} />
        </button>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-obsidian-800/90 backdrop-blur">
            <tr>
              <th className="px-3 py-2 font-medium text-slate-400">Script</th>
              <th className="px-3 py-2 font-medium text-slate-400">Status</th>
              <th className="px-3 py-2 font-medium text-slate-400">Env</th>
              <th className="px-3 py-2 font-medium text-slate-400">Schedule</th>
              <th className="px-3 py-2 font-medium text-slate-400">Last Run</th>
              <th className="px-3 py-2 font-medium text-slate-400 text-right">Run</th>
            </tr>
          </thead>
          <tbody>
            {scripts.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-slate-500">
                  No scripts found. Create one via the Flow API.
                </td>
              </tr>
            ) : (
              scripts.map((s) => (
                <tr
                  key={s.id}
                  onClick={() => handleOpen(s)}
                  className="border-b border-white/[0.05] hover:bg-white/[0.05] cursor-pointer transition"
                >
                  <td className="px-3 py-2.5">
                    <div className="font-medium text-slate-200">{s.name || s.id}</div>
                    <div className="text-[10px] text-slate-500">{languageName(s.language)}</div>
                  </td>
                  <td className="px-3 py-2.5"><StatusBadge status={s.lastRun?.status} /></td>
                  <td className="px-3 py-2.5 text-slate-400 font-mono">{inferVenv(s)}</td>
                  <td className="px-3 py-2.5 text-slate-400">{s.schedule_type || "manual"}</td>
                  <td className="px-3 py-2.5 text-slate-400">
                    {s.lastRun ? (
                      <span>{new Date(s.lastRun.started_at).toLocaleString()}</span>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <button
                      onClick={(e) => { e.stopPropagation(); handleRun(s.id); }}
                      disabled={runningId === s.id}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-mint-500/15 text-mint-300 border border-mint-400/25 hover:bg-mint-500/25 disabled:opacity-40 text-[11px] font-semibold"
                    >
                      {runningId === s.id ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
                      Run
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
