import React from "react";
import { Terminal, GitBranch, FolderKanban, Workflow, MessageSquare, Bot, Settings } from "lucide-react";
import useStore from "../../store/useStore";

const NAV = [
  { id: "automation", label: "Flow", icon: Workflow },
  { id: "files", label: "Files", icon: FolderKanban },
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "terminal", label: "Term", icon: Terminal },
  { id: "git", label: "Git", icon: GitBranch },
  { id: "workers", label: "Agents", icon: Bot },
];

export default function GlobalSidebar() {
  const { activeView, setActiveView, setSettingsOpen } = useStore();

  return (
    <nav className="w-14 flex-shrink-0 flex flex-col items-center py-3 gap-2 glass border-r border-white/[0.08] z-20">
      <div className="mb-3 flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-br from-mint-400 to-emerald-600 shadow-glow">
        <span className="text-obsidian-900 font-bold text-sm">C</span>
      </div>
      {NAV.map((item) => {
        const Icon = item.icon;
        const isActive = activeView === item.id;
        return (
          <button
            key={item.id}
            onClick={() => setActiveView(item.id)}
            title={item.label}
            className={[
              "group relative flex items-center justify-center w-10 h-10 rounded-xl transition-all",
              isActive
                ? "bg-mint-400/15 text-mint-300 neon-border"
                : "text-slate-400 hover:text-slate-100 hover:bg-white/[0.06]"
            ].join(" ")}
          >
            <Icon size={18} />
            {isActive && <span className="absolute -right-[1px] top-1/2 -translate-y-1/2 w-[2px] h-5 bg-mint-400 rounded-l-full" />}
            <span className="absolute left-12 px-2 py-1 rounded-md text-xs bg-obsidian-800 border border-white/[0.08] text-slate-300 opacity-0 group-hover:opacity-100 transition pointer-events-none whitespace-nowrap z-50">
              {item.label}
            </span>
          </button>
        );
      })}
      <div className="flex-1" />
      <button
        onClick={() => setSettingsOpen(true)}
        title="Settings"
        className="flex items-center justify-center w-10 h-10 rounded-xl text-slate-400 hover:text-slate-100 hover:bg-white/[0.06] transition-all"
      >
        <Settings size={18} />
      </button>
    </nav>
  );
}
