/**
 * DevOS Panel Registrations
 * Registers all existing components as DevOS panels.
 * This is the migration layer — existing components become dockable panels.
 */
import { lazy } from "react";
import {
  Workflow, FolderKanban, MessageSquare, Terminal as TerminalIcon,
  GitBranch, Bot, Code, Settings, Activity, Database, ScrollText, Cpu,
} from "lucide-react";
import { wrapAsPanel } from "./wrapAsPanel";
import PlaceholderPanel from "./PlaceholderPanel";

// ── Core Panels (lazy loaded) ───────────────────────────────
// Workflow Canvas — the heart of the application
wrapAsPanel("workflow", {
  name: "Workflow Canvas",
  description: "Spatial orchestration with living nodes",
  component: lazy(() => import("../automation/AutomationHub")),
  icon: Workflow,
  category: "workflow",
  defaultSize: { width: 800, height: 600 },
  defaultConfig: { view: "graph" },
});

// AI Chat — flexible multi-mode chat
wrapAsPanel("chat", {
  name: "AI Chat",
  description: "Converse with AI teammates",
  component: lazy(() => import("../sidebar/ChatSidebar")),
  icon: MessageSquare,
  category: "core",
  defaultSize: { width: 420, height: 600 },
  defaultConfig: { mode: "docked" },
});

// Terminal — Smart/Manual modes
wrapAsPanel("terminal", {
  name: "Terminal",
  description: "Smart terminal with dual modes",
  component: lazy(() => import("../terminal/TerminalPanel")),
  icon: TerminalIcon,
  category: "core",
  defaultSize: { width: 600, height: 300 },
  defaultConfig: { mode: "smart" },
});

// ── IDE Panels ──────────────────────────────────────────────
// Code Editor / IDE
wrapAsPanel("ide", {
  name: "Code Editor",
  description: "Monaco-based code editor",
  component: lazy(() => import("../editor/CodeEditor")),
  icon: Code,
  category: "ide",
  defaultSize: { width: 700, height: 500 },
});

// File Explorer
wrapAsPanel("files", {
  name: "File Explorer",
  description: "Browse project files",
  component: lazy(() => import("../automation/FileTreeWrapper")),
  icon: FolderKanban,
  category: "ide",
  defaultSize: { width: 300, height: 500 },
  defaultConfig: { title: "Project Files" },
});

// ── Agent Panels ────────────────────────────────────────────
// Agent Inspector (Workers)
wrapAsPanel("agents", {
  name: "Agent Inspector",
  description: "Inspect agent identity, memory, execution",
  component: lazy(() => import("../workers/WorkersPanel")),
  icon: Bot,
  category: "agent",
  defaultSize: { width: 380, height: 500 },
});

// Agent Panel (background agents)
wrapAsPanel("agent-ops", {
  name: "Agent Operations",
  description: "Background agent tasks",
  component: lazy(() => import("../sidebar/AgentPanel")),
  icon: Cpu,
  category: "agent",
  defaultSize: { width: 380, height: 500 },
  sidebarItem: false,
});

// ── Tools Panels ────────────────────────────────────────────
// Git Panel
wrapAsPanel("git", {
  name: "Source Control",
  description: "Git status, commits, branches",
  component: lazy(() => import("../sidebar/GitPanel")),
  icon: GitBranch,
  category: "tools",
  defaultSize: { width: 380, height: 500 },
});

// Search Panel
wrapAsPanel("search", {
  name: "Search",
  description: "Search across files and memory",
  component: lazy(() => import("../sidebar/SearchPanel")),
  icon: Database,
  category: "tools",
  defaultSize: { width: 380, height: 400 },
  sidebarItem: false,
});

// ── Operations Panels (placeholders for Stage 5) ───────────
wrapAsPanel("metrics", {
  name: "Metrics",
  description: "System performance metrics",
  component: PlaceholderPanel,
  icon: Activity,
  category: "tools",
  defaultSize: { width: 600, height: 400 },
});

wrapAsPanel("logs", {
  name: "Logs",
  description: "Real-time log streaming",
  component: PlaceholderPanel,
  icon: ScrollText,
  category: "tools",
  defaultSize: { width: 600, height: 300 },
});

wrapAsPanel("memory", {
  name: "Memory",
  description: "Agent memory viewer",
  component: PlaceholderPanel,
  icon: Database,
  category: "agent",
  defaultSize: { width: 400, height: 500 },
});

export { PlaceholderPanel };
export default null;
