/**
 * DevOS IDEPanel — Modular IDE container.
 * The IDE itself becomes modular with dockable sub-panels:
 *   Explorer, Editor, Problems, Outline, Git, Debugger, Terminal, AI
 * Everything movable. Exactly like VS Code.
 */
import React, { Suspense, lazy, useState } from "react";
import { PanelGroup, Panel as ResizablePanel, PanelResizeHandle } from "react-resizable-panels";
import { FolderTree, Code, AlertCircle, ListTree, GitBranch, Bug, Terminal, Sparkles } from "lucide-react";

// Lazy load sub-panels
const FileTree = lazy(() => import("../editor/FileTree"));
const CodeEditor = lazy(() => import("../editor/CodeEditor"));
const ProblemsPanel = lazy(() => import("../editor/ProblemsPanel"));
const SmartTerminal = lazy(() => import("../terminal/SmartTerminal"));
const DebugInspector = lazy(() => import("../debug/DebugInspector"));

const SUB_PANELS = [
  { id: "explorer", label: "Explorer", icon: FolderTree, component: FileTree, defaultDock: "left", defaultSize: 20 },
  { id: "editor", label: "Editor", icon: Code, component: CodeEditor, defaultDock: "center", defaultSize: 50 },
  { id: "problems", label: "Problems", icon: AlertCircle, component: ProblemsPanel, defaultDock: "bottom", defaultSize: 30 },
  { id: "outline", label: "Outline", icon: ListTree, component: null, defaultDock: "right", defaultSize: 25 },
  { id: "git", label: "Git", icon: GitBranch, component: null, defaultDock: "left", defaultSize: 20 },
  { id: "debugger", label: "Debugger", icon: Bug, component: DebugInspector, defaultDock: "bottom", defaultSize: 30 },
  { id: "terminal", label: "Terminal", icon: Terminal, component: SmartTerminal, defaultDock: "bottom", defaultSize: 30 },
  { id: "ai", label: "AI", icon: Sparkles, component: null, defaultDock: "right", defaultSize: 25 },
];

const Spin = () => <div className="text-xs text-slate-400 p-4">Loading...</div>;

function SubPanelTab({ panel, active, onClick }) {
  const Icon = panel.icon;
  return (
    <button
      onClick={onClick}
      className={"devos-ide-tab" + (active ? " active" : "")}
      title={panel.label}
      aria-label={panel.label}
    >
      <Icon size={12} />
      <span className="devos-ide-tab-label">{panel.label}</span>
    </button>
  );
}

export default function IDEPanel() {
  const [leftPanel, setLeftPanel] = useState("explorer");
  const [rightPanel, setRightPanel] = useState("ai");
  const [bottomPanel, setBottomPanel] = useState("terminal");

  const leftDef = SUB_PANELS.find((p) => p.id === leftPanel);
  const rightDef = SUB_PANELS.find((p) => p.id === rightPanel);
  const bottomDef = SUB_PANELS.find((p) => p.id === bottomPanel);

  const LeftComponent = leftDef && leftDef.component;
  const RightComponent = rightDef && rightDef.component;
  const BottomComponent = bottomDef && bottomDef.component;

  return (
    <div className="devos-ide-panel">
      <PanelGroup direction="horizontal" autoSaveId="devos-ide-h">
        {/* Left sidebar */}
        <div className="devos-ide-sidebar">
          <div className="devos-ide-sidebar-tabs">
            {SUB_PANELS.filter((p) => ["explorer", "git", "outline"].includes(p.id)).map((p) => (
              <SubPanelTab key={p.id} panel={p} active={leftPanel === p.id} onClick={() => setLeftPanel(p.id)} />
            ))}
          </div>
          <div className="devos-ide-sidebar-content">
            <Suspense fallback={<Spin />}>
              {LeftComponent ? <LeftComponent /> : <div className="text-xs text-slate-500 p-2">{leftDef && leftDef.label} panel</div>}
            </Suspense>
          </div>
        </div>

        <PanelResizeHandle className="devos-resize-handle" />

        {/* Center: editor + bottom panel */}
        <ResizablePanel minSize={30}>
          <PanelGroup direction="vertical" autoSaveId="devos-ide-v">
            <ResizablePanel minSize={20}>
              <div className="devos-ide-editor">
                <Suspense fallback={<Spin />}>
                  <CodeEditor />
                </Suspense>
              </div>
            </ResizablePanel>
            <PanelResizeHandle className="devos-resize-handle horizontal" />
            <ResizablePanel defaultSize={30} minSize={10} maxSize={70}>
              <div className="devos-ide-bottom">
                <div className="devos-ide-bottom-tabs">
                  {SUB_PANELS.filter((p) => ["problems", "terminal", "debugger"].includes(p.id)).map((p) => (
                    <SubPanelTab key={p.id} panel={p} active={bottomPanel === p.id} onClick={() => setBottomPanel(p.id)} />
                  ))}
                </div>
                <div className="devos-ide-bottom-content">
                  <Suspense fallback={<Spin />}>
                    {BottomComponent ? <BottomComponent /> : <div className="text-xs text-slate-500 p-2">{bottomDef && bottomDef.label} panel</div>}
                  </Suspense>
                </div>
              </div>
            </ResizablePanel>
          </PanelGroup>
        </ResizablePanel>

        <PanelResizeHandle className="devos-resize-handle" />

        {/* Right sidebar */}
        <div className="devos-ide-sidebar right">
          <div className="devos-ide-sidebar-tabs">
            {SUB_PANELS.filter((p) => ["ai", "outline"].includes(p.id)).map((p) => (
              <SubPanelTab key={p.id} panel={p} active={rightPanel === p.id} onClick={() => setRightPanel(p.id)} />
            ))}
          </div>
          <div className="devos-ide-sidebar-content">
            <Suspense fallback={<Spin />}>
              {RightComponent ? <RightComponent /> : <div className="text-xs text-slate-500 p-2">{rightDef && rightDef.label} panel</div>}
            </Suspense>
          </div>
        </div>
      </PanelGroup>
    </div>
  );
}
