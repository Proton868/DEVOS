import React, { useState, useEffect, useRef, useMemo } from "react";
import { Search, File, Zap, GitBranch, Bot, Terminal, Settings,
         MessageSquare, RefreshCw, Split } from "lucide-react";
import useStore from "../../store/useStore";
import { api, getLanguageFromPath } from "../../services/api";
import { listCommands, executeCommand } from "../../commands/registry";

// Fuzzy match: returns score 0-1, highlights array
function fuzzyMatch(str, query) {
  if (!query) return { score: 1, highlights: [] };
  const s = str.toLowerCase();
  const q = query.toLowerCase();
  let si = 0, qi = 0, score = 0;
  const highlights = [];
  while (si < s.length && qi < q.length) {
    if (s[si] === q[qi]) {
      highlights.push(si);
      score += (si === 0 || str[si - 1] === "/" || str[si - 1] === ".") ? 2 : 1;
      qi++;
    }
    si++;
  }
  if (qi < q.length) return null; // no match
  return { score: score / (str.length + 1), highlights };
}

function HighlightedText({ text, highlights }) {
  if (!highlights?.length) return <span>{text}</span>;
  const chars = text.split("").map((ch, i) => (
    <span key={i} className={highlights.includes(i) ? "match-highlight" : ""}>{ch}</span>
  ));
  return <span>{chars}</span>;
}

// Fallback icons for registry commands (registry itself is icon-agnostic)
const CATEGORY_ICON = {
  File: <File size={13} />,
  Edit: <Search size={13} />,
  Search: <Search size={13} />,
  Navigation: <Zap size={13} />,
  View: <Split size={13} />,
  Terminal: <Terminal size={13} />,
  Test: <Zap size={13} />,
  Build: <RefreshCw size={13} />,
  Git: <GitBranch size={13} />,
  Agent: <Bot size={13} />,
  Diff: <Split size={13} />,
  General: <Settings size={13} />,
};

export default function CommandPalette() {
  const {
    paletteOpen, setPaletteOpen,
    fileTree, openFile, splitTab, openFileSplit,
    setTerminalOpen, terminalOpen,
    setChatOpen, chatOpen,
    setGitOpen, gitOpen,
    setAgentOpen, agentOpen,
    setSearchOpen,
    setProblemsOpen,
    setSettingsOpen,
    activeTab, openTabs,
  } = useStore();

  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  // Flatten file tree
  const allFiles = useMemo(() => {
    const flat = [];
    function walk(nodes) {
      for (const n of nodes) {
        if (n.type === "file") flat.push(n);
        if (n.children) walk(n.children);
      }
    }
    walk(fileTree);
    return flat;
  }, [fileTree]);

  const isCommand = query.startsWith(">");
  const searchQuery = isCommand ? query.slice(1).trim() : query;

  const items = useMemo(() => {
    if (isCommand) {
      // Prefer canonical registry (Requirement 20)
      const registered = listCommands({ query: searchQuery, includeDisabled: false });
      return registered.map((c) => {
        const m = searchQuery ? fuzzyMatch(c.label, searchQuery) : { score: 1, highlights: [] };
        return {
          ...c,
          type: "command",
          icon: CATEGORY_ICON[c.category] || CATEGORY_ICON.General,
          score: m?.score ?? 1,
          highlights: m?.highlights || [],
        };
      }).sort((a, b) => (b.score || 0) - (a.score || 0));
    }
    if (!searchQuery) {
      // Show recently opened first
      const recentPaths = new Set((openTabs || []).map((t) => (typeof t === "string" ? t : t.path)));
      const recent = allFiles.filter((f) => recentPaths.has(f.path));
      const rest = allFiles.filter((f) => !recentPaths.has(f.path)).slice(0, 20);
      return [...recent, ...rest].slice(0, 30).map((f) => ({ ...f, type: "file", score: 1, highlights: [] }));
    }
    return allFiles
      .map((f) => {
        const m = fuzzyMatch(f.path, searchQuery);
        return m ? { ...f, type: "file", ...m } : null;
      })
      .filter(Boolean)
      .sort((a, b) => b.score - a.score)
      .slice(0, 30);
  }, [query, allFiles, openTabs, isCommand, searchQuery]);

  useEffect(() => { setSelected(0); }, [query]);

  useEffect(() => {
    if (paletteOpen) { setQuery(""); setTimeout(() => inputRef.current?.focus(), 30); }
  }, [paletteOpen]);

  const runItem = async (item) => {
    if (!item) return;
    setPaletteOpen(false);

    if (item.type === "file") {
      try {
        const { content } = await api.readFile(item.path);
        openFile({ path: item.path, name: item.name, content, language: getLanguageFromPath(item.path) });
      } catch {}
      return;
    }

    // Canonical registry execution (Requirement 20)
    if (item.type === "command" && item.id) {
      try {
        await executeCommand(item.id);
      } catch (e) {
        // Fallback for any remaining legacy ids
        if (item.id === "toggle-chat") setChatOpen(!chatOpen);
        else if (item.id === "reindex") api.reindex?.().then((r) => useStore.getState().setIndexStats?.(r));
        else if (item.id === "split-editor" && activeTab) {
          const tab = (useStore.getState().openTabs || []).find((t) => t.path === activeTab || t === activeTab);
          if (tab) useStore.getState().openFileSplit?.(tab);
        } else {
          useStore.getState().setStatus?.("Command failed: " + (e?.message || e));
        }
      }
    }
  };

  const handleKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSelected(s => Math.min(s + 1, items.length - 1)); }
    if (e.key === "ArrowUp")   { e.preventDefault(); setSelected(s => Math.max(s - 1, 0)); }
    if (e.key === "Enter")     { e.preventDefault(); runItem(items[selected]); }
    if (e.key === "Escape")    { setPaletteOpen(false); }
  };

  useEffect(() => {
    const el = listRef.current?.children[selected];
    el?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  if (!paletteOpen) return null;

  return (
    <div className="palette-overlay" onClick={(e) => { if (e.target === e.currentTarget) setPaletteOpen(false); }}>
      <div className="palette-modal">
        <div className="palette-input-row">
          <Search size={14} className="palette-search-icon" />
          <input
            ref={inputRef}
            className="palette-input"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Search files… or > for commands"
          />
          {query && <span className="palette-hint">{items.length} results</span>}
        </div>

        {!query && (
          <div className="palette-section-label">
            {isCommand ? "Commands" : "Recent & All Files"} — type <kbd>&gt;</kbd> for commands
          </div>
        )}

        <div className="palette-list" ref={listRef}>
          {items.length === 0 && <div className="palette-empty">No results for "{searchQuery}"</div>}
          {items.map((item, i) => (
            <div
              key={item.type === "file" ? item.path : item.id}
              className={`palette-item ${i === selected ? "selected" : ""}`}
              onClick={() => runItem(item)}
              onMouseEnter={() => setSelected(i)}
            >
              <span className="palette-item-icon">
                {item.type === "file" ? <File size={13} /> : item.icon}
              </span>
              <span className="palette-item-label">
                {item.type === "file"
                  ? <><span className="palette-file-name">{item.name}</span>
                      <span className="palette-file-path"><HighlightedText text={item.path} highlights={item.highlights} /></span></>
                  : <HighlightedText text={item.label} highlights={item.highlights} />
                }
              </span>
              {item.shortcut && <kbd className="palette-shortcut">{item.shortcut}</kbd>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
