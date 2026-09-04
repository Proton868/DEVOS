/**
 * AICopilot — Nuha / persona chat surface (spatial focus layer).
 * Real backend: /api/chat/send (SSE via api.streamChat) with persona_id.
 */
import React, { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Send, X, Move, PanelRight, UserRound } from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

function buildContextNote({ node, editorFile, editorContent, personaName }) {
  const proj = useStore.getState().currentProject;
  let p = `Context: project "${proj}". Speaking as ${personaName || "Nuha"}.`;
  if (node) {
    p += ` User is focused on workflow node "${node.title}" (${node.kind}).`;
  }
  if (editorFile) p += ` Open IDE file: ${editorFile}.`;
  if (editorContent) {
    const code = editorContent.length > 2500 ? editorContent.slice(0, 2500) + "\n…(truncated)" : editorContent;
    p += `\nCode excerpt:\n\`\`\`\n${code}\n\`\`\``;
  }
  return p;
}

export default function AICopilot({ floating = false }) {
  const {
    copilot, closeCopilot, nodes, editor, chatMode, toggleChatMode,
    activePersonaId, setActivePersona, openPersonaProfile,
    nuhaMode, setActivePlanId, setOrchestrationStatus, applyOrchestrationPlan,
  } = useOsStore();
  const [pos, setPos] = useState({ x: null, y: null });
  const dragRef = useRef(null);
  const dragging = useRef(false);
  const offset = useRef({ x: 0, y: 0 });
  const { selectedProvider, selectedModel } = useStore();
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [personaMeta, setPersonaMeta] = useState({ name: "Nuha", id: "nuha" });
  const [error, setError] = useState(null);
  const bodyRef = useRef(null);
  const sessionIdRef = useRef(undefined);

  const personaId = (copilot.personaId || activePersonaId || "nuha").toLowerCase();

  useEffect(() => {
    let cancelled = false;
    api.getPersonaProfile?.(personaId)
      .then((p) => {
        if (!cancelled && p) {
          setPersonaMeta({
            id: p.persona_id || personaId,
            name: p.display_name || personaId,
            level: p.level,
            specialty: p.specialty,
            provider: p.provider,
            model: p.model,
          });
        }
      })
      .catch(() => {
        if (!cancelled) setPersonaMeta({ id: personaId, name: personaId === "nuha" ? "Nuha" : personaId });
      });
    return () => { cancelled = true; };
  }, [personaId, copilot.open]);

  // Reset session when persona switches
  useEffect(() => {
    sessionIdRef.current = undefined;
    setMessages([]);
    setError(null);
  }, [personaId]);

  const onDragStart = (e) => {
    if (!floating) return;
    dragging.current = true;
    const rect = dragRef.current?.getBoundingClientRect();
    offset.current = { x: e.clientX - (rect?.left || 0), y: e.clientY - (rect?.top || 0) };
    e.preventDefault();
  };

  useEffect(() => {
    if (!floating) return;
    const move = (e) => {
      if (!dragging.current) return;
      setPos({ x: e.clientX - offset.current.x, y: e.clientY - offset.current.y });
    };
    const up = () => { dragging.current = false; };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, [floating]);

  const node = copilot.nodeId ? nodes.find((n) => n.id === copilot.nodeId) : null;

  useEffect(() => {
    if (copilot.open && copilot.seed && messages.length === 0) {
      setMessages([{ role: "system-note", content: copilot.seed }]);
    }
  }, [copilot.open, copilot.seed]);

  useEffect(() => {
    bodyRef.current?.scrollTo(0, bodyRef.current.scrollHeight);
  }, [messages, streaming]);

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setError(null);
    const next = [...messages.filter((m) => m.role !== "system-note"), { role: "user", content: text }];
    setMessages([...next, { role: "assistant", content: "" }]);
    setStreaming(true);
    try {
      if (nuhaMode === "plan" || nuhaMode === "action") {
        setOrchestrationStatus(nuhaMode === "plan" ? "planning…" : "executing…");
        const res = nuhaMode === "plan"
          ? await api.orchestrationPlan({ goal: text, persona_id: personaId })
          : await api.orchestrationRun({ goal: text, persona_id: personaId });
        if (res.plan_id) setActivePlanId(res.plan_id);
        setOrchestrationStatus(res.status || null);
        const plan = res.plan || {};
        if (plan.id || plan.plan_id || res.plan_id) {
          applyOrchestrationPlan({ ...plan, id: plan.id || plan.plan_id || res.plan_id, status: res.status || plan.status });
        }
        const steps = (plan.steps || []).map((s, i) =>
          `${i + 1}. [${s.persona_id}] ${s.description}`
        ).join("\n");
        const body = [
          `**Nuha ${nuhaMode.toUpperCase()}** — \`${res.status}\``,
          plan.authority_note ? `_${plan.authority_note}_` : "",
          "",
          `**Goal:** ${plan.goal || text}`,
          `**Risk:** ${plan.risk_level || "—"} · **HITL:** ${plan.requires_hitl ? "yes" : "no"}`,
          `**Personas:** ${(plan.personas || []).join(", ") || "—"}`,
          `**Capabilities (requirements, not grants):** ${(plan.capabilities || []).join(", ") || "—"}`,
          "",
          "**Steps:**",
          steps || "(none)",
          "",
          "**Verification:**",
          (plan.verification_plan || []).map((v) => `• ${v}`).join("\n") || "—",
          nuhaMode === "plan"
            ? "\n_Plan only — no files modified. Switch to Action or say Execute that plan to run._"
            : `\n_Agent tasks: ${(plan.agent_task_ids || []).join(", ") || "—"}_`,
        ].filter(Boolean).join("\n");
        setMessages((ms) => {
          const copy = [...ms];
          copy[copy.length - 1] = { role: "assistant", content: body };
          return copy;
        });
      } else {
      const contextNote = buildContextNote({
        node,
        editorFile: editor.open ? editor.file : null,
        editorContent: null,
        personaName: personaMeta.name,
      });
      const providerId = personaMeta.provider || selectedProvider;
      const model = personaMeta.model || selectedModel;
      const iter = api.streamChat({
        providerId,
        model,
        message: text,
        session_id: sessionIdRef.current,
        persona_id: personaId,
        system_prompt: contextNote,
      });
      for await (const evt of iter) {
        if (evt.session_id) sessionIdRef.current = evt.session_id;
        if (evt.error) {
          setMessages((ms) => {
            const copy = [...ms];
            copy[copy.length - 1] = { role: "assistant", content: `⚠️ ${evt.error}` };
            return copy;
          });
          setError(evt.error);
          break;
        }
        if (evt.delta || evt.text) {
          const chunk = evt.delta || evt.text || "";
          setMessages((ms) => {
            const copy = [...ms];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = { role: "assistant", content: (last?.content || "") + chunk };
            return copy;
          });
        }
      }
      }
    } catch (e) {
      setError(e.message || "Chat failed");
      setMessages((ms) => {
        const copy = [...ms];
        copy[copy.length - 1] = { role: "assistant", content: `⚠️ ${e.message || "Chat failed"}` };
        return copy;
      });
    } finally {
      setStreaming(false);
    }
  }

  if (!copilot.open) return null;

  const style = floating
    ? {
        position: "fixed",
        left: pos.x != null ? pos.x : undefined,
        top: pos.y != null ? pos.y : undefined,
        right: pos.x == null ? 16 : undefined,
        bottom: pos.y == null ? 80 : undefined,
        width: "min(380px, 92vw)",
        height: "min(520px, 70vh)",
        zIndex: 420,
      }
    : undefined;

  return (
    <div
      ref={dragRef}
      className={`sp-copilot ${floating ? "floating" : "docked"}`}
      style={style}
    >
      <div className="sp-copilot-head" onMouseDown={onDragStart}>
        <span className="sp-copilot-title">
          <UserRound size={14} />
          {personaMeta.name}
          {personaMeta.level != null && (
            <span className="sp-persona-lv">Lv.{personaMeta.level}</span>
          )}
        </span>
        <span className="spacer" />
        <button
          className="sp-iconbtn"
          title="Persona profile"
          onClick={() => openPersonaProfile(personaId)}
        >
          ◉
        </button>
        <button className="sp-iconbtn" title="Dock / float" onClick={toggleChatMode}>
          {floating ? <PanelRight size={14} /> : <Move size={14} />}
        </button>
        <button className="sp-iconbtn" title="Close" onClick={closeCopilot}>
          <X size={15} />
        </button>
      </div>
      <div className="sp-copilot-sub">
        {personaMeta.specialty || "Orchestrator"} · {personaId === "nuha" ? "Default" : "Specialist"}
        {(personaMeta.provider || selectedProvider) && (
          <span> · {personaMeta.provider || selectedProvider}{personaMeta.model || selectedModel ? ` / ${personaMeta.model || selectedModel}` : ""}</span>
        )}
      </div>
      <div className="sp-copilot-body" ref={bodyRef}>
        {messages.length === 0 && (
          <div className="sp-copilot-empty">
            <div className="sp-nuha-hero-title">{personaId === "nuha" ? "What are we building?" : `Talk to ${personaMeta.name}`}</div>
            <div className="sp-nuha-hero-sub">
              {personaId === "nuha"
                ? "Nuha is DevOS intelligence. Chat here, or ask Nuha to open IDE / Flow when the work needs a canvas."
                : "Specialist persona — Nuha remains the primary orchestrator."}
            </div>
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "system-note" ? (
            <div key={i} className="sp-copilot-note">{m.content}</div>
          ) : (
            <div key={i} className={`sp-copilot-msg ${m.role}`}>
              <div className="sp-copilot-bubble">
                {m.role === "assistant" ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {m.content || (streaming && i === messages.length - 1 ? "…" : "")}
                  </ReactMarkdown>
                ) : (
                  m.content
                )}
              </div>
            </div>
          )
        )}
        {error && <div className="sp-copilot-error">{error}</div>}
        {streaming && (
          <div className="sp-copilot-thinking" aria-live="polite">
            <span className="sp-think-glow" />
            {personaMeta.name} is thinking…
          </div>
        )}
      </div>
      <div className="sp-copilot-input">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`Message ${personaMeta.name}…`}
          rows={1}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button className="sp-send-btn" onClick={send} disabled={streaming || !input.trim()}>
          <Send size={15} />
        </button>
      </div>
    </div>
  );
}
