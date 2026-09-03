/**
 * AICopilot — contextual DevOS AI assistant.
 * Understands current project, selected node/workflow, open file and code.
 * Real backend: /api/chat/send (SSE stream via api.streamChat).
 */
import React, { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Send, X, ChevronRight, Move, PanelRight } from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

function buildSystemPrompt({ node, editorFile, editorContent }) {
  const proj = useStore.getState().currentProject;
  let p = `You are DevOS AI, the operating intelligence of the DevOS AI Developer Operating System. ` +
    `Current project: "${proj}".`;
  if (node) {
    p += `\nThe user is looking at the "${node.title}" node (kind: ${node.kind}) on the workflow orchestration canvas.`;
    if (node.script?.language) p += ` It is a ${node.script.language} script (PyRunner workflow node, id ${node.scriptId}).`;
  }
  if (editorFile) p += `\nAn open file is in the DevOS IDE: ${editorFile}.`;
  if (editorContent) {
    const code = editorContent.length > 3000 ? editorContent.slice(0, 3000) + "\n... (truncated)" : editorContent;
    p += `\nCurrent code:\n\`\`\`\n${code}\n\`\`\``;
  }
  p += `\nWhen asked to run, edit, or connect things, explain the exact DevOS action (node context menu, CMD+K command) that performs it.`;
  return p;
}

export default function AICopilot({ floating = false }) {
  const { copilot, closeCopilot, nodes, editor, chatMode, toggleChatMode } = useOsStore();
  const [pos, setPos] = useState({ x: null, y: null });
  const dragRef = useRef(null);
  const dragging = useRef(false);
  const offset = useRef({ x: 0, y: 0 });
  const { selectedProvider, selectedModel } = useStore();
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const bodyRef = useRef(null);
  const sessionIdRef = useRef(undefined);

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
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
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
    const next = [...messages.filter((m) => m.role !== "system-note"), { role: "user", content: text }];
    setMessages([...next, { role: "assistant", content: "" }]);
    setStreaming(true);
    try {
      const system = buildSystemPrompt({
        node,
        editorFile: editor.open ? editor.file : null,
        editorContent: editor.open && editor.file ? null : node?.script?.code || null,
      });
      for await (const evt of api.streamChat({
        providerId: selectedProvider,
        model: selectedModel,
        message: text,
        session_id: sessionIdRef.current,
        system_prompt: system,
      })) {
        if (evt.session_id) sessionIdRef.current = evt.session_id;
        if (evt.error) {
          setMessages((ms) => {
            const copy = [...ms];
            copy[copy.length - 1] = { role: "assistant", content: `⚠️ ${evt.error}` };
            return copy;
          });
          break;
        }
        if (evt.text) {
          setMessages((ms) => {
            const copy = [...ms];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = { ...last, content: (last.content || "") + evt.text };
            return copy;
          });
        }
      }
    } catch (e) {
      setMessages((ms) => {
        const copy = [...ms];
        copy[copy.length - 1] = { role: "assistant", content: `⚠️ ${e.message}` };
        return copy;
      });
    } finally {
      setStreaming(false);
    }
  }

  if (!copilot.open) return null;

  const floatStyle = floating && pos.x != null
    ? { position: "fixed", left: pos.x, top: pos.y, width: 380, height: 480, zIndex: 520, flex: "none" }
    : floating
    ? { position: "fixed", right: 18, bottom: 72, width: 380, height: 480, zIndex: 520, flex: "none" }
    : { flex: "0 0 42%", minHeight: 260 };

  return (
    <div className="sp-surface" style={floatStyle} ref={dragRef}>
      <div
        className="sp-surface-head"
        style={floating ? { cursor: "grab" } : undefined}
        onMouseDown={onDragStart}
      >
        {floating && <Move size={13} style={{ opacity: 0.55, marginRight: 4 }} />}
        <span>AI Copilot Chat</span>
        {node && <span className="sub">· {node.title}</span>}
        <span className="spacer" />
        <button
          className="sp-iconbtn"
          title={floating ? "Dock chat to focus column" : "Float chat (movable)"}
          onClick={(e) => { e.stopPropagation(); toggleChatMode(); }}
        >
          <PanelRight size={14} />
        </button>
        <button className="sp-iconbtn" title="Close copilot" onClick={closeCopilot}><X size={15} /></button>
      </div>
      <div className="sp-ctx-strip">
        <span className="sp-ctx-chip">project: {useStore.getState().currentProject}</span>
        {node && <span className="sp-ctx-chip">node: {node.title}</span>}
        {editor.open && editor.file && <span className="sp-ctx-chip">file: {editor.file}</span>}
      </div>
      <div className="sp-copilot-body" ref={bodyRef}>
        {messages.length === 0 && (
          <div style={{ color: "var(--sp-text-2)", fontSize: 12 }}>
            Contextual assistant — I can see the selected node, workflow, and code. Try:
            "Why did this fail?", "Add retries", "Create a test", "Connect this to PyRunner".
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "system-note" ? (
            <div key={i} style={{ color: "var(--sp-text-2)", fontSize: 11.5, display: "flex", alignItems: "center", gap: 6 }}>
              <ChevronRight size={12} /> {m.content}
            </div>
          ) : (
            <div key={i} className={`sp-cmsg ${m.role === "user" ? "user" : ""}`}>
              <span className="who">{m.role === "user" ? "User" : "DevOS AI"}</span>
              <div className="bubble">
                {m.role === "user" ? m.content : (
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ code: CodeBlock, pre: ({ children }) => <>{children}</> }}>
                    {m.content || (streaming && i === messages.length - 1 ? "▍" : "")}
                  </ReactMarkdown>
                )}
              </div>
            </div>
          )
        )}
      </div>
      <div className="sp-copilot-input">
        <textarea
          placeholder="Ask about this node, code, or execution…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
        />
        <button className="sp-send-btn" onClick={send} disabled={streaming || !input.trim()}>
          <Send size={15} />
        </button>
      </div>
    </div>
  );
}

function CodeBlock({ children, className }) {
  const code = String(children).replace(/\n$/, "");
  return <pre><code className={className}>{code}</code></pre>;
}
