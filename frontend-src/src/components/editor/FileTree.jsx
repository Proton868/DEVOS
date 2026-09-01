import React, { useState, useCallback } from "react";
import {
  ChevronRight, ChevronDown, File, Folder, FolderOpen,
  Plus, Trash2, Edit3, Download, RefreshCw,
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
  return <span style={{ color: colors[ext] || "#ccc", fontSize: 11, marginRight: 4 }}>
    {ext?.toUpperCase().slice(0, 3) || "•"}
  </span>;
}

function TreeNode({ node, depth = 0, onOpenFile }) {
  const [expanded, setExpanded] = useState(depth === 0);
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState(node.name);
  const { openFile, setFileTree, setStatus, setActiveView } = useStore();

  const indent = depth * 14;

  const handleClick = async () => {
    if (node.type === "directory") {
      setExpanded((e) => !e);
      return;
    }
    if (onOpenFile) { onOpenFile(node); return; }
    try {
      const { content } = await api.readFile(node.path);
      openFile({
        path: node.path,
        name: node.name,
        content,
        language: getLanguageFromPath(node.path),
      });
      setActiveView("chat");
    } catch (e) {
      setStatus("Error opening file: " + e.message);
    }
  };

  const handleDelete = async (e) => {
    e.stopPropagation();
    if (!window.confirm(`Delete "${node.name}"?`)) return;
    await api.deleteFile(node.path);
    const { tree } = await api.getTree();
    setFileTree(tree);
  };

  const handleRename = async () => {
    const newPath = node.path.replace(node.name, newName);
    await api.renameFile(node.path, newPath);
    const { tree } = await api.getTree();
    setFileTree(tree);
    setRenaming(false);
  };

  const handleNewFile = async (e) => {
    e.stopPropagation();
    const name = window.prompt("New file name:");
    if (!name) return;
    await api.createFile(`${node.path}/${name}`, "file");
    const { tree } = await api.getTree();
    setFileTree(tree);
    setExpanded(true);
  };

  const handleNewFolder = async (e) => {
    e.stopPropagation();
    const name = window.prompt("New folder name:");
    if (!name) return;
    await api.createFile(`${node.path}/${name}`, "directory");
    const { tree } = await api.getTree();
    setFileTree(tree);
    setExpanded(true);
  };

  return (
    <div>
      <div
        className="tree-node"
        style={{ paddingLeft: indent + 8 }}
        onClick={handleClick}
        title={node.path}
      >
        <span className="tree-icon">
          {node.type === "directory"
            ? expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />
            : <span style={{ width: 12, display: "inline-block" }} />}
        </span>

        {node.type === "directory"
          ? (expanded ? <FolderOpen size={14} color="#e8c07a" /> : <Folder size={14} color="#e8c07a" />)
          : <FileIcon name={node.name} />}

        {renaming ? (
          <input
            className="rename-input"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onBlur={handleRename}
            onKeyDown={(e) => { if (e.key === "Enter") handleRename(); if (e.key === "Escape") setRenaming(false); }}
            autoFocus
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span className="tree-label">{node.name}</span>
        )}

        <span className="tree-actions">
          {node.type === "directory" && (
            <>
              <button title="New File" onClick={handleNewFile}><Plus size={11} /></button>
              <button title="New Folder" onClick={handleNewFolder}><Folder size={11} /></button>
            </>
          )}
          <button title="Rename" onClick={(e) => { e.stopPropagation(); setRenaming(true); }}><Edit3 size={11} /></button>
          <button title="Delete" onClick={handleDelete}><Trash2 size={11} /></button>
          <a title="Download" href={api.downloadUrl(node.path)} onClick={(e) => e.stopPropagation()}><Download size={11} /></a>
        </span>
      </div>

      {node.type === "directory" && expanded && node.children?.map((child) => (
        <TreeNode key={child.path} node={child} depth={depth + 1} onOpenFile={onOpenFile} />
      ))}
    </div>
  );
}

export function FileTreeCompact({ title = "EXPLORER" }) {
  const { fileTree, setFileTree, setStatus, openFile, setActiveView } = useStore();

  const refresh = useCallback(async () => {
    setStatus("Refreshing...");
    const { tree } = await api.getTree();
    setFileTree(tree);
    setStatus("Ready");
  }, [setFileTree, setStatus]);

  const handleOpen = async (node) => {
    try {
      const { content } = await api.readFile(node.path);
      openFile({ path: node.path, name: node.name, content, language: getLanguageFromPath(node.path) });
      setActiveView("chat");
    } catch (e) {
      setStatus("Error opening file: " + e.message);
    }
  };

  const newRootFile = async () => {
    const name = window.prompt("New file name:");
    if (!name) return;
    await api.createFile(name, "file");
    await refresh();
  };

  return (
    <div className="file-tree">
      <div className="file-tree-header">
        <span>{title}</span>
        <div className="file-tree-header-actions">
          <button title="New File" onClick={newRootFile}><Plus size={13} /></button>
          <button title="Refresh" onClick={refresh}><RefreshCw size={13} /></button>
        </div>
      </div>
      <div className="file-tree-body">
        {fileTree.length === 0 ? (
          <div className="empty-workspace">
            <p>No files yet</p>
            <button className="btn-primary" onClick={newRootFile}>Create a file</button>
          </div>
        ) : (
          fileTree.map((node) => <TreeNode key={node.path} node={node} depth={0} onOpenFile={handleOpen} />)
        )}
      </div>
    </div>
  );
}

export default function FileTree() {
  const { fileTree, setFileTree, setStatus } = useStore();

  const refresh = useCallback(async () => {
    setStatus("Refreshing...");
    const { tree } = await api.getTree();
    setFileTree(tree);
    setStatus("Ready");
  }, [setFileTree, setStatus]);

  const newRootFile = async () => {
    const name = window.prompt("New file name:");
    if (!name) return;
    await api.createFile(name, "file");
    await refresh();
  };

  return (
    <div className="file-tree">
      <div className="file-tree-header">
        <span>EXPLORER</span>
        <div className="file-tree-header-actions">
          <button title="New File" onClick={newRootFile}><Plus size={13} /></button>
          <button title="Refresh" onClick={refresh}><RefreshCw size={13} /></button>
        </div>
      </div>
      <div className="file-tree-body">
        {fileTree.length === 0 ? (
          <div className="empty-workspace">
            <p>No files yet</p>
            <button className="btn-primary" onClick={newRootFile}>Create a file</button>
          </div>
        ) : (
          fileTree.map((node) => <TreeNode key={node.path} node={node} depth={0} />)
        )}
      </div>
    </div>
  );
}
