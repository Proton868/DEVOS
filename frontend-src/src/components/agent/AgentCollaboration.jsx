/**
 * DevOS AgentCollaboration — Multi-agent collaboration panel.
 * Shows active agents, their roles, inter-agent communication, and coordination.
 *
 * Instead of "Worker", users see:
 *   CodeSmith, Architect, Sentinel, Harvester, Atlas, Mercury, Apollo
 */
import React, { useState, useEffect } from "react";
import { Bot, Activity, MessageSquare, Zap, Circle } from "lucide-react";
import { api, subscribeToEvents } from "../../services/api";

const AGENT_PERSONALITIES = {
  codesmith: { name: "CodeSmith", color: "#58a6ff", specialty: "Code generation" },
  architect: { name: "Architect", color: "#bc8cff", specialty: "System design" },
  sentinel: { name: "Sentinel", color: "#f85149", specialty: "Security & review" },
  harvester: { name: "Harvester", color: "#3fb950", specialty: "Data collection" },
  atlas: { name: "Atlas", color: "#d29922", specialty: "Heavy lifting" },
  mercury: { name: "Mercury", color: "#39c5cf", specialty: "Fast execution" },
  apollo: { name: "Apollo", color: "#fb923c", specialty: "Planning & strategy" },
};

const STATUS_COLORS = {
  idle: "var(--text-3)",
  thinking: "var(--yellow)",
  executing: "var(--accent)",
  success: "var(--green)",
  failed: "var(--red)",
};

export default function AgentCollaboration() {
  const [agents, setAgents] = useState([]);
  const [messages, setMessages] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState(null);

  useEffect(() => {
    loadAgents();
    const unsub = subscribeToEvents(
      (event) => {
        if (event.type === "agent.status") {
          setAgents((prev) => prev.map((a) => a.id === event.data.id ? { ...a, ...event.data } : a));
        } else if (event.type === "agent.message") {
          setMessages((prev) => [...prev.slice(-50), event.data]);
        }
      },
      () => {}
    );
    return () => unsub();
  }, []);

  const loadAgents = async () => {
    try {
      const result = await api.getWorkers ? api.getWorkers() : [];
      const agentList = result.agents || result.workers || result || [];
      setAgents(agentList);
    } catch (e) {
      setAgents([]);
    }
  };

  const filteredMessages = selectedAgent
    ? messages.filter((m) => m.from === selectedAgent || m.to === selectedAgent)
    : messages;

  return (
    <div className="devos-agent-collab">
      {/* Agent roster */}
      <div className="devos-agent-roster">
        <div className="devos-agent-roster-header">
          <Bot size={13} />
          <span>Active Agents</span>
          <span className="devos-agent-count">{agents.length}</span>
        </div>
        <div className="devos-agent-list">
          {agents.length === 0 ? (
            <div className="devos-agent-empty">No active agents</div>
          ) : (
            agents.map((agent) => {
              const persona = AGENT_PERSONALITIES[agent.personality] || AGENT_PERSONALITIES.codesmith;
              const status = agent.status || "idle";
              return (
                <button
                  key={agent.id}
                  onClick={() => setSelectedAgent(selectedAgent === agent.id ? null : agent.id)}
                  className={"devos-agent-card" + (selectedAgent === agent.id ? " selected" : "")}
                  style={{ borderColor: persona.color }}
                >
                  <div className="devos-agent-avatar" style={{ background: persona.color }}>
                    {persona.name.charAt(0)}
                  </div>
                  <div className="devos-agent-info">
                    <span className="devos-agent-name">{agent.name || persona.name}</span>
                    <span className="devos-agent-specialty">{persona.specialty}</span>
                  </div>
                  <div className="devos-agent-status" style={{ color: STATUS_COLORS[status] }}>
                    <Circle size={8} fill={STATUS_COLORS[status]} />
                    <span>{status}</span>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      {/* Communication feed */}
      <div className="devos-agent-comm">
        <div className="devos-agent-comm-header">
          <MessageSquare size={13} />
          <span>Communication</span>
          {selectedAgent && (
            <button onClick={() => setSelectedAgent(null)} className="devos-agent-clear">Clear filter</button>
          )}
        </div>
        <div className="devos-agent-messages">
          {filteredMessages.length === 0 ? (
            <div className="devos-agent-empty">No messages</div>
          ) : (
            filteredMessages.map((msg, i) => {
              const fromPersona = AGENT_PERSONALALITIES_LOOKUP(msg.from);
              return (
                <div key={i} className="devos-agent-message">
                  <div className="devos-agent-message-from" style={{ color: fromPersona.color }}>
                    {fromPersona.name}
                  </div>
                  <div className="devos-agent-message-text">{msg.text || msg.content}</div>
                  {msg.to && <div className="devos-agent-message-to">→ {msg.to}</div>}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

function AGENT_PERSONALALITIES_LOOKUP(id) {
  if (!id) return AGENT_PERSONALITIES.codesmith;
  const key = Object.keys(AGENT_PERSONALITIES).find((k) => id.toLowerCase().includes(k));
  return AGENT_PERSONALITIES[key] || AGENT_PERSONALITIES.codesmith;
}
