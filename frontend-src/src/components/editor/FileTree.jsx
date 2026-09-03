import React, { useState, useCallback, useEffect, useRef } from "react";
import {
  ChevronRight, ChevronDown, Plus, Trash2, Edit3, RefreshCw, FolderPlus, FilePlus,
} from "lucide-react";
import useStore from "../../store/useStore";
import { api, getLanguageFromPath } from "../../services/api";

function FileIcon({ name }) {
  const ext = name.split(".").pop()?.toLowerCase();
  const colors = {
    js: "#f7df1e", jsx: "#61dafb", ts: "#3178c6", tsx: "#61dafb",
    py: "#3776ab", go: "#00add8", rs: "#ce422b", java: "#ed8b00",
    html: "#e34f26", css: "#1572b6", json: "#92d192", md: "#aaa",
    sh: "#4eaa25", dockerfile: "#0db7ed", sql: "#ff6b35",
  };
  return (
    <span style={{ color: colors[ext] || "#ccc", fontSize: 11, marginRight: 4 }}>
      {ext?.toUpperCase().slice(0, 3) || "•"}
    </span>
  );
}

function ContextMenu({ x, y, items, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const close = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="file-tree-ctx"
      style={{
        position: "fixed", left: x, top: y, zIndex: 9999,
        background: "var(--bg-2, #1e1e2e)", border: "1px solid var(--border, #333)",
        borderRadius: 6, minWidth: 160, padding: "4px 0",
        boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
      }}
      role="menu"
    >
      {items.map((it, i) =>
        it.separator ? (
          <div key={i} style={{ height: 1, background: "var(--border,#333)", margin: "4px 0" }} />
        ) : (
          <button
            key={it.id || i}
            role="menuitem"
            onClick={() => { onClose(); it.onClick?.(); }}
            disabled={it.disabled}
            style={{
              display: "flex", alignItems: "center", gap: 8, width: "100%",
              padding: "6px 12px", background: "transparent", border: "none",
              color: it.danger ? "#f87171" : "var(--text, #e2e8f0)",
              fontSize: 12, cursor: it.disabled ? "not-allowed" : "pointer",
              opacity: it.disabled ? 0.4 : 1, textAlign: "left",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-3,#2a2a3c)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
          >
            {it.icon} {it.label}
          </button>
        )
      )}
    </div>
  );
}

function TreeNode({
  node,
  depth = 0,
  onOpenFile,
  selectedPath,
  setSelectedPath,
  onContext,
  onNavigate,
  flatIndex,
}) {
  const [expanded, setExpanded] = useState(depth === 0 && !node.lazy);
  const [loading, setLoading] = useState(false);
  const [children, setChildren] = useState(node.children || []);
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState(node.name);
  const { openFile, setFileTree, setStatus, setActiveView, openTabs, gitStatus } = useStore();
  const isDirty = openTabs?.some((t) => t.path === node.path && (t.modified || t.dirty));
  const gitEntry = (gitStatus?.files || gitStatus?.changed || []).find?.(
    (f) => (f.path || f) === node.path
  );
  const gitMark = gitEntry
    ? (gitEntry.status || gitEntry.xy || "?").toString().trim().charAt(0).toUpperCase()
    : null;
  const isActive = selectedPath === node.path;
  const indent = depth * 14;
  const isDir = node.type === "directory" || node.type === "dir";

  const refreshTree = async () => {
    const { tree } = await api.getTree({ depth: 1 });
    setFileTree(tree || []);
  };

  const expand = async () => {
    if (!isDir) return;
    if (expanded) {
      setExpanded(false);
      return;
    }
    // Lazy load children when marked lazy or children empty
    if (node.lazy || (Array.isArray(children) && children.length === 0 && !loading)) {
      setLoading(true);
      try {
        const entries = await api.listDir(node.path);
        setChildren(Array.isArray(entries) ? entries : []);
      } catch (e) {
        setStatus("Expand failed: " + e.message);
      } finally {
        setLoading(false);
      }
    }
    setExpanded(true);
  };

  const handleClick = async () => {
    setSelectedPath(node.path);
    if (isDir) {
      await expand();
      return;
    }
    if (onOpenFile) {
      onOpenFile(node);
      return;
    }
    try {
      const { content } = await api.readFile(node.path);
      openFile({
        path: node.path,
        name: node.name,
        content,
        language: getLanguageFromPath(node.path),
      });
      setActiveView?.("chat");
    } catch (e) {
      setStatus("Error opening file: " + e.message);
    }
  };

  const handleRename = async () => {
    const trimmed = (newName || "").trim();
    if (!trimmed || trimmed === node.name) { setRenaming(false); return; }
    const parent = node.path.includes("/") ? node.path.slice(0, node.path.lastIndexOf("/")) : "";
    const newPath = parent ? `${parent}/${trimmed}` : trimmed;
    try {
      await api.renameFile(node.path, newPath);
      await refreshTree();
      setStatus(`Renamed → ${newPath}`);
    } catch (e) {
      setStatus("Rename failed: " + e.message);
    }
    setRenaming(false);
  };

  const handleDelete = async () => {
    if (!window.confirm(`Delete "${node.name}"?`)) return;
    try {
      await api.deleteFile(node.path);
      await refreshTree();
      setStatus(`Deleted ${node.path}`);
    } catch (e) {
      setStatus("Delete failed: " + e.message);
    }
  };

  const handleNewFile = async () => {
    const name = window.prompt("New file name:");
    if (!name) return;
    const base = node.type === "directory" ? node.path : (node.path.includes("/") ? node.path.slice(0, node.path.lastIndexOf("/")) : "");
    const path = base ? `${base}/${name}` : name;
    try {
      await api.createFile(path, "file");
      await refreshTree();
      setExpanded(true);
    } catch (e) {
      setStatus("Create failed: " + e.message);
    }
  };

  const handleNewFolder = async () => {
    const name = window.prompt("New folder name:");
    if (!name) return;
    const base = node.type === "directory" ? node.path : (node.path.includes("/") ? node.path.slice(0, node.path.lastIndexOf("/")) : "");
    const path = base ? `${base}/${name}` : name;
    try {
      await api.createFile(path, "directory");
      await refreshTree();
      setExpanded(true);
    } catch (e) {
      setStatus("Create folder failed: " + e.message);
    }
  };

  const openCtx = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setSelectedPath(node.path);
    onContext?.(e.clientX, e.clientY, {
      node,
      rename: () => { setNewName(node.name); setRenaming(true); },
      delete: handleDelete,
      newFile: handleNewFile,
      newFolder: handleNewFolder,
      open: handleClick,
    });
  };

  return (
    <div>
      <div
        className={"file-tree-node" + (isActive ? " active" : "")}
        style={{
          paddingLeft: indent + 8,
          display: "flex", alignItems: "center", gap: 4,
          height: 24, cursor: "pointer", fontSize: 12,
          background: isActive ? "var(--bg-3, #2a2a3c)" : "transparent",
          color: "var(--text, #e2e8f0)",
        }}
        onClick={handleClick}
        onContextMenu={openCtx}
        onDoubleClick={() => node.type === "file" && handleClick()}
        tabIndex={0}
        role="treeitem"
        aria-expanded={isDir ? expanded : undefined}
        aria-selected={isActive}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleClick(); }
          if (e.key === "F2") { e.preventDefault(); setRenaming(true); setNewName(node.name); }
          if (e.key === "Delete") { e.preventDefault(); handleDelete(); }
          if (e.key === "ArrowRight" && isDir) { e.preventDefault(); if (!expanded) expand(); }
          if (e.key === "ArrowLeft" && isDir) { e.preventDefault(); setExpanded(false); }
          if (e.key === "ArrowDown") { e.preventDefault(); onNavigate?.(flatIndex, 1); }
          if (e.key === "ArrowUp") { e.preventDefault(); onNavigate?.(flatIndex, -1); }
          if (e.key === "Home") { e.preventDefault(); onNavigate?.(0, 0, true); }
          if (e.key === "End") { e.preventDefault(); onNavigate?.(-1, 0, true); }
        }}
        title={node.path}
      >
        {isDir ? (
          loading ? <span style={{ width: 12, fontSize: 9 }}>…</span>
            : expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />
        ) : (
          <span style={{ width: 12 }} />
        )}
        {node.type === "file" && <FileIcon name={node.name} />}
        {renaming ? (
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onBlur={handleRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleRename();
              if (e.key === "Escape") setRenaming(false);
            }}
            onClick={(e) => e.stopPropagation()}
            style={{
              flex: 1, fontSize: 12, background: "var(--bg-1)", color: "inherit",
              border: "1px solid var(--accent)", borderRadius: 3, padding: "0 4px",
            }}
          />
        ) : (
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {node.name}
            {isDirty ? <span style={{ color: "#fbbf24", marginLeft: 4 }} title="Unsaved">●</span> : null}
            {gitMark ? (
              <span
                style={{
                  color: gitMark === "M" ? "#fbbf24" : gitMark === "A" ? "#4ade80" : gitMark === "D" ? "#f87171" : "#94a3b8",
                  marginLeft: 4,
                  fontSize: 10,
                  fontWeight: 600,
                }}
                title={`Git: ${gitMark}`}
              >
                {gitMark}
              </span>
            ) : null}
          </span>
        )}
      </div>
      {isDir && expanded && (children || []).map((child, i) => (
        <TreeNode
          key={child.path}
          node={child}
          depth={depth + 1}
          onOpenFile={onOpenFile}
          selectedPath={selectedPath}
          setSelectedPath={setSelectedPath}
          onContext={onContext}
          onNavigate={onNavigate}
          flatIndex={(flatIndex ?? 0) + 1 + i}
        />
      ))}
    </div>
  );
}

export function ProjectExplorer({ title = "EXPLORER", onOpenFile }) {
  const { fileTree, setFileTree, setStatus, openFile, setActiveView } = useStore();
  const [selectedPath, setSelectedPath] = useState(null);
  const [ctx, setCtx] = useState(null);
  const [showHidden, setShowHidden] = useState(false);

  const filterTree = useCallback((nodes) => {
    if (!nodes) return [];
    return nodes
      .filter((n) => showHidden || !n.name?.startsWith("."))
      .map((n) =>
        n.type === "directory" && n.children
          ? { ...n, children: filterTree(n.children) }
          : n
      );
  }, [showHidden]);

  const visibleTree = filterTree(fileTree);

  const refresh = useCallback(async () => {
    setStatus("Refreshing…");
    try {
      // Lazy root listing — expand loads children on demand
      const { tree } = await api.getTree({ depth: 1 });
      setFileTree(tree || []);
      setStatus("Ready");
    } catch (e) {
      setStatus("Refresh failed: " + e.message);
    }
  }, [setFileTree, setStatus]);

  // Flatten visible paths for ArrowUp/Down navigation
  const flatPaths = useCallback(() => {
    const out = [];
    const walk = (nodes) => {
      for (const n of nodes || []) {
        out.push(n.path);
        // only walk already-expanded children held in DOM via recursive TreeNode state
      }
    };
    walk(visibleTree);
    return out;
  }, [visibleTree]);

  const onNavigate = useCallback((fromIndex, delta, absolute) => {
    const paths = [];
    // Collect from DOM treeitems for accurate expanded state
    document.querySelectorAll('.file-tree-body [role="treeitem"]').forEach((el) => {
      const title = el.getAttribute("title");
      if (title) paths.push(title);
    });
    if (!paths.length) return;
    let idx;
    if (absolute) {
      idx = delta === 0 && fromIndex === 0 ? 0 : paths.length - 1;
    } else {
      idx = Math.max(0, Math.min(paths.length - 1, (fromIndex || 0) + delta));
    }
    const path = paths[idx];
    if (path) {
      setSelectedPath(path);
      const el = document.querySelector(`.file-tree-body [role="treeitem"][title="${CSS.escape(path)}"]`);
      el?.focus();
    }
  }, []);

  const handleOpen = async (node) => {
    try {
      const { content } = await api.readFile(node.path);
      openFile({ path: node.path, name: node.name, content, language: getLanguageFromPath(node.path) });
      setActiveView?.("chat");
    } catch (e) {
      setStatus("Error opening file: " + e.message);
    }
  };

  const newRootFile = async () => {
    const name = window.prompt("New file name:");
    if (!name) return;
    try {
      await api.createFile(name, "file");
      await refresh();
    } catch (e) {
      setStatus("Create failed: " + e.message);
    }
  };

  const newRootFolder = async () => {
    const name = window.prompt("New folder name:");
    if (!name) return;
    try {
      await api.createFile(name, "directory");
      await refresh();
    } catch (e) {
      setStatus("Create folder failed: " + e.message);
    }
  };

  const onContext = (x, y, actions) => {
    const items = [
      { id: "open", label: "Open", icon: <ChevronRight size={12} />, onClick: actions.open },
      { separator: true },
      { id: "new-file", label: "New File", icon: <FilePlus size={12} />, onClick: actions.newFile },
      { id: "new-folder", label: "New Folder", icon: <FolderPlus size={12} />, onClick: actions.newFolder },
      { separator: true },
      { id: "rename", label: "Rename", icon: <Edit3 size={12} />, onClick: actions.rename },
      { id: "delete", label: "Delete", icon: <Trash2 size={12} />, onClick: actions.delete, danger: true },
    ];
    setCtx({ x, y, items });
  };

  return (
    <div className="file-tree" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="file-tree-header" style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "6px 10px", fontSize: 11, fontWeight: 600, letterSpacing: 0.5,
        color: "var(--text-muted, #94a3b8)", borderBottom: "1px solid var(--border, #333)",
      }}>
        <span>{title}</span>
        <div className="file-tree-header-actions" style={{ display: "flex", gap: 4 }}>
          <button
            title={showHidden ? "Hide hidden files" : "Show hidden files"}
            onClick={() => setShowHidden((v) => !v)}
            style={{
              background: showHidden ? "var(--bg-3,#2a2a3c)" : "transparent",
              border: "none", color: "inherit", cursor: "pointer",
              fontSize: 10, padding: "0 4px", borderRadius: 3,
            }}
          >
            .{showHidden ? "" : "·"}
          </button>
          <button title="New File" onClick={newRootFile} style={{ background: "transparent", border: "none", color: "inherit", cursor: "pointer" }}><Plus size={13} /></button>
          <button title="New Folder" onClick={newRootFolder} style={{ background: "transparent", border: "none", color: "inherit", cursor: "pointer" }}><FolderPlus size={13} /></button>
          <button title="Refresh" onClick={refresh} style={{ background: "transparent", border: "none", color: "inherit", cursor: "pointer" }}><RefreshCw size={13} /></button>
        </div>
      </div>
      <div className="file-tree-body" style={{ flex: 1, overflow: "auto" }} role="tree">
        {(!visibleTree || visibleTree.length === 0) ? (
          <div className="empty-workspace" style={{ padding: 16, textAlign: "center", color: "#64748b", fontSize: 12 }}>
            <p>No files yet</p>
            <button className="btn-primary" onClick={newRootFile} style={{ marginTop: 8 }}>Create a file</button>
          </div>
        ) : (
          visibleTree.map((node, i) => (
            <TreeNode
              key={node.path}
              node={node}
              depth={0}
              onOpenFile={onOpenFile || handleOpen}
              selectedPath={selectedPath}
              setSelectedPath={setSelectedPath}
              onContext={onContext}
              onNavigate={onNavigate}
              flatIndex={i}
            />
          ))
        )}
      </div>
      {ctx && (
        <ContextMenu
          x={ctx.x}
          y={ctx.y}
          items={ctx.items}
          onClose={() => setCtx(null)}
        />
      )}
    </div>
  );
}

export default function FileTree() {
  return <ProjectExplorer title="EXPLORER" />;
}
