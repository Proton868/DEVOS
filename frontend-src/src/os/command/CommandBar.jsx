/**
 * CommandBar — the DevOS command interface (CMD+K / CTRL+K / SPACE).
 * Every command executes a REAL DevOS capability via api.js.
 * Natural-language input is matched against the command set; unmatched
 * requests are offered to the AI Copilot (which answers honestly).
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Search, Play, FileCode2, Bot, GitBranch, FolderOpen, Settings, Terminal,
  Plus, History, Cpu, MessageSquare, X, Sparkles,
} from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

function buildCommands(os, store, close, { reloadGraph, searchResults }) {
  const s = () => useStore.getState();
  const o = () => useOsStore.getState();
  const cmds = [
    // Workflows
    {
      id: "workflow.create", label: "Create Workflow", icon: Plus, cat: "Workflows",
      keywords: ["create an agent", "create workflow", "new workflow", "new script"],
      run: async () => {
        const name = window.prompt("Workflow name:", "New Workflow");
        if (!name) return "cancelled";
        try {
          const script = await api.createFlowScript({
            name,
            language: "python",
            code: "# DevOS workflow script\nprint('hello from DevOS')\n",
          });
          s().setStatus("Workflow created: " + name);
          reloadGraph();
          const id = script?.id ?? script?.script_id;
          if (id != null) o().openEditor({ scriptId: id });
          return "created";
        } catch (e) {
          s().setStatus("Create failed: " + e.message);
          return "error";
        }
      },
    },
    {
      id: "workflow.run", label: "Run Selected Workflow", icon: Play, cat: "Workflows",
      keywords: ["run this workflow", "run workflow", "execute workflow"],
      when: () => !!o().selectedNode,
      run: async () => {
        const node = o().nodes.find((n) => n.id === o().selectedNode);
        if (!node?.scriptId) { s().setStatus("Select a workflow node first"); return "error"; }
        o().openTerminal(node.id);
        try {
          await api.runFlowScript(node.scriptId);
          s().setStatus("Execution started");
          return "ok";
        } catch (e) { s().setStatus("Run failed: " + e.message); return "error"; }
      },
    },
    // Files
    {
      id: "files.open", label: "Open Files Surface", icon: FolderOpen, cat: "Files",
      keywords: ["files", "file tree", "browse files"],
      run: () => { o().setOverlay("files"); return "ok"; },
    },
    {
      id: "file.search", label: searchResults.length
        ? `Open "${(searchResults[0] || "").split("/").pop()}"`
        : "Search Files for…", icon: FileCode2, cat: "Files",
      keywords: ["search files", "find file", "open file", "authentication"],
      run: async () => {
        if (searchResults.length) {
          o().openEditor({ file: searchResults[0] });
          return "ok";
        }
        const q = window.prompt("Search project files for:", "authentication");
        if (!q) return "cancelled";
        try {
          const r = await api.searchFiles(q, 10);
          const paths = (r?.results || r?.files || r || []).map((x) => x.path || x.file || x).filter((p) => typeof p === "string");
          if (!paths.length) { s().setStatus("No matching files"); return "none"; }
          o().openEditor({ file: paths[0] });
          s().setStatus(`Opened ${paths[0]} (${paths.length} matches)`);
          return "ok";
        } catch (e) { s().setStatus("Search failed: " + e.message); return "error"; }
      },
    },
    // Agents
    {
      id: "agents.show", label: "Show Active Agents", icon: Bot, cat: "Agents",
      keywords: ["show active agents", "agent fleet", "agents"],
      run: () => { o().setDashboardOpen(true); s().setStatus("Agency Dashboard visible on canvas"); return "ok"; },
    },
    {
      id: "agent.ask", label: "Ask DevOS…", icon: MessageSquare, cat: "Agents",
      keywords: ["why did this execution fail", "ask", "copilot", "chat", "ai"],
      run: () => { o().openCopilot(o().selectedNode); return "ok"; },
    },
    // Runtime
    {
      id: "terminal.open", label: "Open PyRunner Runtime", icon: Terminal, cat: "Runtime",
      keywords: ["terminal", "pyrunner", "runtime", "run this code", "shell"],
      run: () => { o().openTerminal(o().selectedNode); return "ok"; },
    },
    {
      id: "history.open", label: "Open Execution History", icon: History, cat: "Runtime",
      keywords: ["inspect failed execution", "execution history", "runs", "logs"],
      run: () => { o().setOverlay("history"); return "ok"; },
    },
    // System
    {
      id: "git.open", label: "Open Git", icon: GitBranch, cat: "System",
      keywords: ["git", "commit", "push", "status"],
      run: () => { o().setOverlay("git"); return "ok"; },
    },
    {
      id: "system.open", label: "System OS Status", icon: Cpu, cat: "System",
      keywords: ["system", "health", "resources"],
      run: () => { o().setOverlay("system"); return "ok"; },
    },
    {
      id: "settings.open", label: "Open Settings", icon: Settings, cat: "System",
      keywords: ["settings", "preferences", "config"],
      run: () => { o().setOverlay("settings"); return "ok"; },
    },
    {
      id: "deploy.project", label: "Deploy Project (via Git push)", icon: Sparkles, cat: "System",
      keywords: ["deploy project", "deploy", "connect github"],
      run: async () => {
        try {
          const st = await api.gitStatus();
          const branch = st?.branch || "main";
          await api.gitPush("origin", branch);
          s().setStatus(`Pushed to origin/${branch}`);
          return "ok";
        } catch (e) { s().setStatus("Deploy (push) failed: " + e.message); return "error"; }
      },
    },
  ];
  return cmds;
}

export default function CommandBar({ reloadGraph }) {
  const { commandBar, setCommandBar, openCopilot } = useOsStore();
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(0);
  const [fileHits, setFileHits] = useState([]);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  useEffect(() => {
    if (commandBar.open) {
      setQuery(commandBar.query || "");
      setSel(0);
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [commandBar.open, commandBar.query]);

  // Light file search as you type (real /api/search/files, debounced)
  useEffect(() => {
    if (!commandBar.open) return;
    const q = query.trim();
    if (q.length < 3) { setFileHits([]); return; }
    const t = setTimeout(async () => {
      try {
        const r = await api.searchFiles(q, 6);
        const paths = (r?.results || r?.files || r || [])
          .map((x) => (typeof x === "string" ? x : x.path || x.file || ""))
          .filter(Boolean);
        setFileHits(paths);
      } catch { setFileHits([]); }
    }, 350);
    return () => clearTimeout(t);
  }, [query, commandBar.open]);

  const store = useStore.getState();
  const os = useOsStore.getState();

  const commands = useMemo(
    () => buildCommands(os, store, () => setCommandBar(false), { reloadGraph, searchResults: fileHits }),
    [fileHits, reloadGraph]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands
      .map((c) => {
        const hay = (c.label + " " + (c.keywords || []).join(" ")).toLowerCase();
        const words = q.split(/\s+/);
        const score = words.reduce((acc, w) => acc + (hay.includes(w) ? 1 : 0), 0);
        return { c, score };
      })
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((x) => x.c);
  }, [query, commands]);

  // File hit results as pseudo-commands
  const fileCommands = useMemo(() => {
    if (!query.trim() || !fileHits.length) return [];
    return fileHits.slice(0, 5).map((p) => ({
      id: `file:${p}`, label: p, icon: FileCode2, cat: "File",
      run: () => { useOsStore.getState().openEditor({ file: p }); },
    }));
  }, [query, fileHits]);

  const items = [...fileCommands, ...filtered];

  const execute = async (c) => {
    setCommandBar(false);
    try {
      const res = await c.run();
      if (res === "cancelled" || res === "none") return;
    } catch (e) {
      useStore.getState().setStatus("Command failed: " + e.message);
    }
  };

  const askCopilot = () => {
    setCommandBar(false);
    openCopilot(useOsStore.getState().selectedNode,
      `The user asked the command bar: "${query}". No direct DevOS command matched — help them accomplish it.`);
  };

  if (!commandBar.open) return null;

  return (
    <div className="sp-command-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setCommandBar(false); }}>
      <div className="sp-command">
        <div className="sp-command-input">
          <Search size={16} style={{ color: "var(--sp-text-2)" }} />
          <input
            ref={inputRef}
            value={query}
            placeholder="Type a command, or ask DevOS in natural language…"
            onChange={(e) => { setQuery(e.target.value); setSel(0); }}
            onKeyDown={(e) => {
              if (e.key === "Escape") setCommandBar(false);
              if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, items.length - 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
              if (e.key === "Enter") {
                e.preventDefault();
                if (items[sel]) execute(items[sel]);
                else if (query.trim()) askCopilot();
              }
            }}
          />
          <button className="sp-iconbtn" onClick={() => setCommandBar(false)}><X size={15} /></button>
        </div>
        <div className="sp-command-list" ref={listRef}>
          {fileCommands.map((c, i) => (
            <button key={c.id} className={`sp-command-item ${i === sel ? "sel" : ""}`} onClick={() => execute(c)}>
              <c.icon size={14} /> {c.label} <span className="ci-cat">{c.cat}</span>
            </button>
          ))}
          {filtered.map((c, i) => {
            const idx = fileCommands.length + i;
            return (
              <button key={c.id} className={`sp-command-item ${idx === sel ? "sel" : ""}`} onClick={() => execute(c)}>
                <c.icon size={14} /> {c.label} <span className="ci-cat">{c.cat}</span>
              </button>
            );
          })}
          {items.length === 0 && query.trim() && (
            <button className="sp-command-item" onClick={askCopilot}>
              <MessageSquare size={14} /> Ask DevOS: "{query}" <span className="ci-cat">AI</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}