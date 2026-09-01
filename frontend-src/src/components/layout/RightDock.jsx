import React from "react";
import useStore from "../../store/useStore";
import CodeEditor from "../editor/CodeEditor";
import ChatSidebar from "../sidebar/ChatSidebar";

export default function RightDock() {
  const { activeView, activeScriptCode } = useStore();

  return (
    <aside className="w-[420px] flex-shrink-0 flex flex-col glass border-l border-white/[0.08] overflow-hidden">
      <div className="h-9 flex items-center px-3 border-b border-white/[0.08] text-xs font-semibold text-slate-300 uppercase tracking-wider">
        DevOS IDE
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        <CodeEditor />
      </div>
      {activeView === "chat" && (
        <div className="h-[46%] border-t border-white/[0.08] min-h-[220px] flex flex-col">
          <ChatSidebar />
        </div>
      )}
    </aside>
  );
}
