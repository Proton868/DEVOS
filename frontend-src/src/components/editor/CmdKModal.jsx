import React, { useState, useRef, useEffect } from "react";
import { Zap, X, Check, RefreshCw } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

export default function CmdKModal() {
  const { cmdkOpen, cmdkSelection, setCmdkOpen, selectedProvider, selectedModel, openTabs, activeTab, updateTabContent, setStatus } = useStore();
  const [instruction, setInstruction] = useState("");
  const [result, setResult] = useState("");
  const [loading, setLoading] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (cmdkOpen) {
      setInstruction("");
      setResult("");
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [cmdkOpen]);

  const activeFile = openTabs.find((t) => t.path === activeTab);

  const runEdit = async () => {
    if (!instruction.trim() || loading) return;
    setLoading(true);
    setResult("");

    try {
      let full = "";
      const stream = api.streamEdit({
        providerId: selectedProvider,
        model: selectedModel,
        instruction: instruction.trim(),
        selectedCode: cmdkSelection?.selectedCode || "",
        fullFile: activeFile?.content,
        language: cmdkSelection?.language || activeFile?.language,
      });

      for await (const chunk of stream) {
        if (chunk.text) { full += chunk.text; setResult(full); }
        if (chunk.error) { setResult(`Error: ${chunk.error}`); break; }
      }
    } catch (e) {
      setResult(`Error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const applyResult = () => {
    if (!result || !activeTab) return;
    if (cmdkSelection?.selectedCode && activeFile) {
      const newContent = activeFile.content.replace(cmdkSelection.selectedCode, result);
      updateTabContent(activeTab, newContent);
    } else if (activeTab) {
      updateTabContent(activeTab, result);
    }
    setStatus("AI edit applied ✓");
    setCmdkOpen(false);
  };

  if (!cmdkOpen) return null;

  return (
    <div className="cmdk-overlay" onClick={(e) => { if (e.target === e.currentTarget) setCmdkOpen(false); }}>
      <div className="cmdk-modal">
        <div className="cmdk-header">
          <Zap size={16} color="#f59e0b" />
          <span>AI Edit {cmdkSelection?.selectedCode ? "(selection)" : "(full file)"}</span>
          <button onClick={() => setCmdkOpen(false)}><X size={14} /></button>
        </div>

        {cmdkSelection?.selectedCode && (
          <div className="cmdk-selection-preview">
            <pre>{cmdkSelection.selectedCode.slice(0, 300)}{cmdkSelection.selectedCode.length > 300 ? "\n..." : ""}</pre>
          </div>
        )}

        <div className="cmdk-input-row">
          <input
            ref={inputRef}
            className="cmdk-input"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") runEdit(); if (e.key === "Escape") setCmdkOpen(false); }}
            placeholder='E.g. "Add error handling", "Convert to TypeScript", "Add JSDoc comments"'
          />
          <button className="cmdk-run" onClick={runEdit} disabled={loading || !instruction.trim()}>
            {loading ? <RefreshCw size={14} className="spin" /> : <Zap size={14} />}
          </button>
        </div>

        {result && (
          <>
            <div className="cmdk-result">
              <pre>{result}</pre>
            </div>
            <div className="cmdk-actions">
              <button className="btn-secondary" onClick={() => setResult("")}>Discard</button>
              <button className="btn-primary" onClick={applyResult}>
                <Check size={14} /> Apply
              </button>
            </div>
          </>
        )}

        <div className="cmdk-footer">
          Enter to run &nbsp;·&nbsp; Escape to close &nbsp;·&nbsp; Result replaces {cmdkSelection?.selectedCode ? "selection" : "file"}
        </div>
      </div>
    </div>
  );
}
