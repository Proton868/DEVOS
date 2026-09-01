import React, { useState } from "react";
import { Bot, X, Play, CheckCircle, XCircle, Loader,
         ChevronDown, ChevronRight, Clock } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

/**
 * Background Agents — Claude Code / Codex style
 * Submit a task and it runs in the background while you keep coding.
 * Results appear here when done.
 */
export default function BackgroundAgents() {
  const { bgAgents, addBgAgent, updateBgAgent, removeBgAgent,
    selectedProvider, selectedModel, setFileTree } = useStore();
  const [task, setTask] = useState("");
  const [expanded, setExpanded] = useState(null);

  const launchAgent = async () => {
    if (!task.trim()) return;
    const id = `bg-${Date.now()}`;
    const agent = {
      id,
      task: task.trim(),
      status: "running",
      startedAt: new Date().toISOString(),
      actions: [],
    };
    addBgAgent(agent);
    setTask("");

    try {
      const stream = api.streamAgent({
        providerId: selectedProvider,
        model: selectedModel,
        task: task.trim(),
      });

      for await (const action of stream) {
        updateBgAgent(id, {
          actions: [...(useStore.getState().bgAgents.find(a => a.id === id)?.actions || []), action],
        });
        if (action.type === "done" || action.type === "error") {
          updateBgAgent(id, { status: action.type === "error" ? "failed" : "done" });
          // Refresh file tree after agent may have changed files
          api.getTree().then(({ tree }) => setFileTree(tree || []));
          break;
        }
      }
    } catch (e) {
      updateBgAgent(id, { status: "failed", error: e.message });
    }
  };

  const getAnswer = (agent) => agent.actions.find(a => a.type === "answer")?.text || "";
  const toolCount = (agent) => agent.actions.filter(a => a.type === "tool_call").length;

  return (
    <div className="bg-agents-panel">
      <div className="bg-agents-header">
        <Bot size={14} color="#bc8cff" />
        <span>Background Agents</span>
        {bgAgents.filter(a => a.status === "running").length > 0 && (
          <span className="bg-agents-running-badge">
            {bgAgents.filter(a => a.status === "running").length} running
          </span>
        )}
      </div>

      <div className="bg-agents-input">
        <textarea
          className="bg-agents-textarea"
          value={task}
          onChange={e => setTask(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); launchAgent(); } }}
          placeholder="Describe a task… agent runs in background while you code"
          rows={2}
        />
        <button className="bg-agents-launch" onClick={launchAgent} disabled={!task.trim()}>
          <Play size={13} /> Launch
        </button>
      </div>

      <div className="bg-agents-list">
        {bgAgents.length === 0 && (
          <div className="bg-agents-empty">
            <p>No background agents yet.</p>
            <p className="bg-agents-empty-hint">Launch a task above — it runs while you keep editing.</p>
          </div>
        )}
        {[...bgAgents].reverse().map(agent => (
          <div key={agent.id} className={`bg-agent-card ${agent.status}`}>
            <div className="bg-agent-header" onClick={() => setExpanded(e => e === agent.id ? null : agent.id)}>
              <span className="bg-agent-icon">
                {agent.status === "running" ? <Loader size={12} className="spin-slow" color="#58a6ff" /> :
                 agent.status === "done"    ? <CheckCircle size={12} color="#3fb950" /> :
                                              <XCircle size={12} color="#f85149" />}
              </span>
              <span className="bg-agent-task">{agent.task.slice(0, 60)}{agent.task.length > 60 ? "…" : ""}</span>
              <span className="bg-agent-meta">
                <Clock size={10} /> {new Date(agent.startedAt).toLocaleTimeString()}
                {toolCount(agent) > 0 && <span> · {toolCount(agent)} tools</span>}
              </span>
              <span className="bg-agent-expand">
                {expanded === agent.id ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              </span>
              <button className="btn-icon" onClick={e => { e.stopPropagation(); removeBgAgent(agent.id); }}>
                <X size={11} />
              </button>
            </div>

            {expanded === agent.id && (
              <div className="bg-agent-detail">
                {getAnswer(agent) && (
                  <div className="bg-agent-answer">{getAnswer(agent)}</div>
                )}
                <div className="bg-agent-trace">
                  {agent.actions.filter(a => a.type === "tool_call" || a.type === "tool_result").map((a, i) => (
                    <div key={i} className={`bg-trace-row ${a.type === "tool_result" ? (a.ok ? "ok" : "fail") : ""}`}>
                      {a.type === "tool_call"
                        ? <><span className="bg-trace-tool">{a.tool}</span><span className="bg-trace-args">{JSON.stringify(a.args).slice(0, 60)}</span></>
                        : <><span className="bg-trace-result">{a.ok ? "✓" : "✗"}</span></>
                      }
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
