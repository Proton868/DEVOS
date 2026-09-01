/**
 * DevOS MemoryViewer — Agent memory inspection panel.
 * Displays episodic, semantic, and working memory with search and filtering.
 */
import React, { useState, useEffect, useMemo } from "react";
import { Database, Search, Brain, Clock, FileText, Edit3, Trash2, Download } from "lucide-react";
import { api } from "../../services/api";

const MEMORY_TYPES = {
  EPISODIC: "episodic",
  SEMANTIC: "semantic",
  WORKING: "working",
  LONG_TERM: "long-term",
};

const TYPE_ICONS = {
  [MEMORY_TYPES.EPISODIC]: Clock,
  [MEMORY_TYPES.SEMANTIC]: Brain,
  [MEMORY_TYPES.WORKING]: FileText,
  [MEMORY_TYPES.LONG_TERM]: Database,
};

const TYPE_LABELS = {
  [MEMORY_TYPES.EPISODIC]: "Episodic — what happened, in order",
  [MEMORY_TYPES.SEMANTIC]: "Semantic — facts, entities, relationships",
  [MEMORY_TYPES.WORKING]: "Working — active task scratch space",
  [MEMORY_TYPES.LONG_TERM]: "Long-term — consolidated knowledge",
};

export default function MemoryViewer({ agentId }) {
  const [memories, setMemories] = useState([]);
  const [activeType, setActiveType] = useState(MEMORY_TYPES.EPISODIC);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState("");

  useEffect(() => {
    loadMemories();
  }, [agentId, activeType]);

  const loadMemories = async () => {
    setLoading(true);
    try {
      const result = await api.getMemory({ agentId, type: activeType });
      setMemories(result.items || result.memories || []);
    } catch (e) {
      setMemories([]);
    }
    setLoading(false);
  };

  const filtered = useMemo(() => {
    if (!query) return memories;
    return memories.filter((m) =>
      (m.content || m.text || "").toLowerCase().includes(query.toLowerCase())
    );
  }, [memories, query]);

  const handleEdit = (memory) => {
    setEditingId(memory.id);
    setEditText(memory.content || memory.text || "");
  };

  const handleSaveEdit = async (id) => {
    try {
      await api.updateMemory(id, { content: editText });
      setMemories((prev) => prev.map((m) => m.id === id ? { ...m, content: editText } : m));
      setEditingId(null);
    } catch (e) {
      console.error("Failed to update memory:", e);
    }
  };

  const handleDelete = async (id) => {
    try {
      await api.deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } catch (e) {
      console.error("Failed to delete memory:", e);
    }
  };

  const handleExport = () => {
    const data = JSON.stringify({ type: activeType, items: memories }, null, 2);
    const blob = new Blob([data], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "memory-" + activeType + ".json";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="devos-memory-viewer">
      {/* Type tabs */}
      <div className="devos-memory-tabs">
        {Object.values(MEMORY_TYPES).map((type) => {
          const Icon = TYPE_ICONS[type];
          return (
            <button
              key={type}
              onClick={() => setActiveType(type)}
              className={"devos-memory-tab" + (activeType === type ? " active" : "")}
              title={TYPE_LABELS[type]}
            >
              <Icon size={12} />
              <span className="devos-memory-tab-label">{type}</span>
            </button>
          );
        })}
      </div>

      {/* Search bar */}
      <div className="devos-memory-search">
        <Search size={12} />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search memories..."
          className="devos-memory-search-input"
          aria-label="Search memories"
        />
        <button onClick={handleExport} className="devos-memory-btn" title="Export" aria-label="Export memories">
          <Download size={12} />
        </button>
      </div>

      {/* Memory list */}
      <div className="devos-memory-list">
        {loading ? (
          <div className="devos-memory-empty">Loading...</div>
        ) : filtered.length === 0 ? (
          <div className="devos-memory-empty">No memories found</div>
        ) : (
          filtered.map((memory) => (
            <div key={memory.id} className="devos-memory-item">
              {editingId === memory.id ? (
                <div className="devos-memory-edit">
                  <textarea
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    className="devos-memory-edit-input"
                    rows={3}
                  />
                  <div className="devos-memory-edit-actions">
                    <button onClick={() => handleSaveEdit(memory.id)} className="devos-memory-btn save">Save</button>
                    <button onClick={() => setEditingId(null)} className="devos-memory-btn">Cancel</button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="devos-memory-content">{memory.content || memory.text}</div>
                  {memory.timestamp && (
                    <div className="devos-memory-meta">
                      <Clock size={9} /> {memory.timestamp}
                      {memory.score && <span className="devos-memory-score">Score: {memory.score}</span>}
                    </div>
                  )}
                  <div className="devos-memory-actions">
                    <button onClick={() => handleEdit(memory)} className="devos-memory-btn" title="Edit" aria-label="Edit memory">
                      <Edit3 size={10} />
                    </button>
                    <button onClick={() => handleDelete(memory.id)} className="devos-memory-btn danger" title="Delete" aria-label="Delete memory">
                      <Trash2 size={10} />
                    </button>
                  </div>
                </>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
