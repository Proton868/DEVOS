import React, { useRef, useEffect } from "react";
import { X, Copy, Zap, Download } from "lucide-react";
import useStore from "../../store/useStore";

export default function ScratchPad() {
  const { scratchOpen, setScratchOpen, scratchContent, setScratchContent,
    setCmdkOpen, selectedProvider, selectedModel } = useStore();
  const textareaRef = useRef(null);

  useEffect(() => {
    if (scratchOpen) setTimeout(() => textareaRef.current?.focus(), 50);
  }, [scratchOpen]);

  const copy = () => navigator.clipboard.writeText(scratchContent);

  const download = () => {
    const blob = new Blob([scratchContent], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "scratch.txt"; a.click();
    URL.revokeObjectURL(url);
  };

  const sendToAI = () => {
    const selection = window.getSelection()?.toString() || scratchContent;
    if (!selection.trim()) return;
    setCmdkOpen(true, { selectedCode: selection, language: "plaintext" });
  };

  if (!scratchOpen) return null;

  return (
    <div className="scratch-overlay" onClick={e => { if (e.target === e.currentTarget) setScratchOpen(false); }}>
      <div className="scratch-modal">
        <div className="scratch-header">
          <span className="scratch-title">📝 Scratch Pad</span>
          <span className="scratch-hint">Auto-saved · Ctrl+K to edit with AI</span>
          <div className="scratch-actions">
            <button className="btn-icon" title="Copy all" onClick={copy}><Copy size={13} /></button>
            <button className="btn-icon" title="Download" onClick={download}><Download size={13} /></button>
            <button className="btn-icon scratch-ai-btn" title="Edit with AI" onClick={sendToAI}>
              <Zap size={13} />
            </button>
            <button className="btn-icon" onClick={() => setScratchOpen(false)}><X size={14} /></button>
          </div>
        </div>
        <textarea
          ref={textareaRef}
          className="scratch-body"
          value={scratchContent}
          onChange={e => setScratchContent(e.target.value)}
          spellCheck={false}
          placeholder="Quick notes, code snippets, anything..."
          onKeyDown={e => {
            if ((e.ctrlKey || e.metaKey) && e.key === "k") {
              e.preventDefault();
              const sel = textareaRef.current;
              const selected = scratchContent.slice(sel.selectionStart, sel.selectionEnd);
              setCmdkOpen(true, { selectedCode: selected || scratchContent, language: "plaintext" });
            }
          }}
        />
        <div className="scratch-footer">
          {scratchContent.split("\n").length} lines · {scratchContent.length} chars
        </div>
      </div>
    </div>
  );
}
