import React, { useState } from "react";
import { Network, Table2 } from "lucide-react";
import GraphCanvas from "./GraphCanvas";
import PyRunnerMatrix from "./PyRunnerMatrix";

export default function AutomationHub() {
  const [view, setView] = useState("graph"); // "graph" | "matrix"

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex items-center justify-between h-12 glass-panel px-4">
        <div className="flex items-center gap-6">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">Automation Switcher</h2>
            <p className="text-[10px] text-slate-500">Graph view or PyRunner execution matrix</p>
          </div>
          <div className="flex items-center gap-1 bg-obsidian-800/80 rounded-lg p-1 border border-white/[0.08]">
            <button
              onClick={() => setView("graph")}
              className={[
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition",
                view === "graph"
                  ? "bg-mint-500/15 text-mint-300 neon-border"
                  : "text-slate-400 hover:text-slate-200 hover:bg-white/[0.06]",
              ].join(" ")}
            >
              <Network size={13} /> Graph
            </button>
            <button
              onClick={() => setView("matrix")}
              className={[
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition",
                view === "matrix"
                  ? "bg-mint-500/15 text-mint-300 neon-border"
                  : "text-slate-400 hover:text-slate-200 hover:bg-white/[0.06]",
              ].join(" ")}
            >
              <Table2 size={13} /> Matrix
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0">
        {view === "graph" ? <GraphCanvas /> : <PyRunnerMatrix />}
      </div>
    </div>
  );
}
