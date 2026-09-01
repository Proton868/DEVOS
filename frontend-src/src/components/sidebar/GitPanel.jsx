import React, { useState, useEffect, useCallback } from "react";
import { GitBranch, GitCommit, GitMerge, RefreshCw, Plus, Minus, X,
         Upload, Download, Check, AlertCircle, ChevronDown, ChevronRight,
         Clock, ExternalLink } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

const STATUS_ICONS = {
  M: { label: "Modified",  color: "#d29922", icon: "M" },
  A: { label: "Added",     color: "#3fb950", icon: "A" },
  D: { label: "Deleted",   color: "#f85149", icon: "D" },
  R: { label: "Renamed",   color: "#58a6ff", icon: "R" },
  "?": { label: "Untracked", color: "#8b949e", icon: "U" },
};

function FileStatusRow({ file, badge, onStage, onUnstage, onDiscard, onDiff }) {
  const info = STATUS_ICONS[badge] || STATUS_ICONS["?"];
  return (
    <div className="git-file-row" onClick={() => onDiff(file)}>
      <span className="git-file-badge" style={{ color: info.color }}>{info.icon}</span>
      <span className="git-file-name">{file.split("/").pop()}</span>
      <span className="git-file-path">{file}</span>
      <div className="git-file-actions">
        {onStage   && <button title="Stage"   onClick={e => { e.stopPropagation(); onStage(file); }}><Plus size={11} /></button>}
        {onUnstage && <button title="Unstage" onClick={e => { e.stopPropagation(); onUnstage(file); }}><Minus size={11} /></button>}
        {onDiscard && <button title="Discard" onClick={e => { e.stopPropagation(); onDiscard(file); }} className="discard-btn"><X size={11} /></button>}
      </div>
    </div>
  );
}

function DiffViewer({ diff, onClose }) {
  if (!diff) return null;
  const lines = (diff.unstaged || diff.staged || "").split("\n");
  return (
    <div className="diff-viewer">
      <div className="diff-header">
        <span>Diff</span>
        <button onClick={onClose}><X size={13} /></button>
      </div>
      <div className="diff-body">
        {lines.length <= 1 && !lines[0] ? (
          <p className="diff-empty">No changes to display</p>
        ) : lines.map((line, i) => {
          const cls = line.startsWith("+") ? "diff-add"
            : line.startsWith("-") ? "diff-del"
            : line.startsWith("@@") ? "diff-hunk"
            : "diff-ctx";
          return <div key={i} className={`diff-line ${cls}`}><span className="diff-ln">{i + 1}</span>{line}</div>;
        })}
      </div>
    </div>
  );
}

function CommitHistory({ commits }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? commits : commits.slice(0, 5);
  return (
    <div className="git-section">
      <div className="git-section-header" onClick={() => setExpanded(e => !e)}>
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Clock size={12} /> History ({commits.length})
      </div>
      {expanded && shown.map(c => (
        <div key={c.hash} className="git-commit-row" title={c.message}>
          <span className="git-commit-hash">{c.hash}</span>
          <span className="git-commit-msg">{c.message.slice(0, 52)}</span>
          <span className="git-commit-author">{c.author}</span>
        </div>
      ))}
    </div>
  );
}

export default function GitPanel() {
  const { gitOpen, setGitOpen, gitStatus, setGitStatus, setStatus } = useStore();
  const [commitMsg, setCommitMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [diff, setDiff] = useState(null);
  const [diffFile, setDiffFile] = useState(null);
  const [newBranch, setNewBranch] = useState("");
  const [remoteUrl, setRemoteUrl] = useState("");
  const [showRemoteForm, setShowRemoteForm] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await api.gitStatus();
      setGitStatus(s);
    } catch (e) {
      setStatus("Git error: " + e.message);
    }
  }, [setGitStatus, setStatus]);

  useEffect(() => { if (gitOpen) refresh(); }, [gitOpen, refresh]);

  const showDiff = async (file) => {
    setDiffFile(file);
    const d = await api.gitDiff(file);
    setDiff(d);
  };

  const stageFile = async (file) => { await api.gitStage([file]); refresh(); };
  const unstageFile = async (file) => { await api.gitUnstage([file]); refresh(); };
  const discardFile = async (file) => {
    if (!window.confirm(`Discard changes to "${file}"?`)) return;
    await api.gitDiscard(file);
    refresh();
  };
  const stageAll = async () => { await api.gitStage("all"); refresh(); };
  const unstageAll = async () => {
    const files = gitStatus?.staged || [];
    if (files.length) { await api.gitUnstage(files); refresh(); }
  };

  const doCommit = async () => {
    if (!commitMsg.trim()) return;
    setLoading(true);
    try {
      await api.gitCommit(commitMsg.trim());
      setCommitMsg("");
      setStatus("Committed ✓");
      refresh();
    } catch (e) {
      setStatus("Commit failed: " + e.message);
    } finally { setLoading(false); }
  };

  const doPush = async () => {
    setLoading(true);
    setStatus("Pushing…");
    try {
      await api.gitPush();
      setStatus("Pushed ✓");
      refresh();
    } catch (e) {
      setStatus("Push failed: " + e.message);
    } finally { setLoading(false); }
  };

  const doPull = async () => {
    setLoading(true);
    setStatus("Pulling…");
    try {
      await api.gitPull();
      setStatus("Pulled ✓");
      refresh();
    } catch (e) {
      setStatus("Pull failed: " + e.message);
    } finally { setLoading(false); }
  };

  const doCheckout = async (branch, create = false) => {
    await api.gitCheckout(branch, create);
    refresh();
  };

  const doAddRemote = async () => {
    if (!remoteUrl.trim()) return;
    await api.gitAddRemote("origin", remoteUrl.trim());
    setRemoteUrl(""); setShowRemoteForm(false);
    refresh();
  };

  const initRepo = async () => {
    await api.gitInit();
    refresh();
  };

  if (!gitOpen) return null;

  if (!gitStatus?.isRepo) return (
    <div className="git-panel">
      <div className="git-header">
        <GitBranch size={14} /> <span>Source Control</span>
        <button className="git-icon-btn" onClick={() => setGitOpen(false)}><X size={13} /></button>
      </div>
      <div className="git-no-repo">
        <p>Not a git repository.</p>
        <button className="btn-primary" onClick={initRepo}>Initialize Repository</button>
      </div>
    </div>
  );

  const { branch, staged = [], modified = [], created = [], deleted = [], untracked = [], commits = [], ahead = 0, behind = 0, remotes = [] } = gitStatus || {};

  const unstaged = [...new Set([...modified, ...created, ...deleted])];

  return (
    <div className="git-panel">
      <div className="git-header">
        <GitBranch size={13} />
        <span className="git-branch-name">{branch}</span>
        {ahead > 0 && <span className="git-ahead">↑{ahead}</span>}
        {behind > 0 && <span className="git-behind">↓{behind}</span>}
        <div className="git-header-actions">
          <button title="Pull" onClick={doPull} disabled={loading}><Download size={12} /></button>
          <button title="Push" onClick={doPush} disabled={loading}><Upload size={12} /></button>
          <button title="Refresh" onClick={refresh}><RefreshCw size={12} /></button>
          <button onClick={() => setGitOpen(false)}><X size={13} /></button>
        </div>
      </div>

      {diff && <DiffViewer diff={diff} onClose={() => { setDiff(null); setDiffFile(null); }} />}

      {/* Staged changes */}
      <div className="git-section">
        <div className="git-section-header">
          <Check size={11} color="#3fb950" />
          <span>Staged ({staged.length})</span>
          {staged.length > 0 && <button className="git-section-action" onClick={unstageAll} title="Unstage all"><Minus size={10} /></button>}
        </div>
        {staged.map(f => (
          <FileStatusRow key={f} file={f} badge="A" onUnstage={unstageFile} onDiff={showDiff} />
        ))}
      </div>

      {/* Changes */}
      <div className="git-section">
        <div className="git-section-header">
          <AlertCircle size={11} color="#d29922" />
          <span>Changes ({unstaged.length + untracked.length})</span>
          {(unstaged.length + untracked.length) > 0 && (
            <button className="git-section-action" onClick={stageAll} title="Stage all"><Plus size={10} /></button>
          )}
        </div>
        {unstaged.map(f => (
          <FileStatusRow key={f} file={f} badge="M" onStage={stageFile} onDiscard={discardFile} onDiff={showDiff} />
        ))}
        {untracked.map(f => (
          <FileStatusRow key={f} file={f} badge="?" onStage={stageFile} onDiff={showDiff} />
        ))}
      </div>

      {/* Commit */}
      <div className="git-commit-area">
        <textarea
          className="git-commit-input"
          value={commitMsg}
          onChange={e => setCommitMsg(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) doCommit(); }}
          placeholder="Commit message… (Ctrl+Enter to commit)"
          rows={3}
        />
        <div className="git-commit-actions">
          <button className="git-commit-btn" onClick={doCommit} disabled={!commitMsg.trim() || loading}>
            <GitCommit size={13} /> Commit
          </button>
        </div>
      </div>

      {/* Branch switcher */}
      <div className="git-section">
        <div className="git-section-header"><GitMerge size={11} /> Branches</div>
        <div className="git-branch-row">
          <input
            className="git-branch-input"
            placeholder="new-branch"
            value={newBranch}
            onChange={e => setNewBranch(e.target.value)}
          />
          <button className="git-branch-btn" onClick={() => { if (newBranch.trim()) { doCheckout(newBranch.trim(), true); setNewBranch(""); } }}>
            Create
          </button>
        </div>
        {gitStatus?.branches?.map(b => (
          <div key={b} className={`git-branch-item ${b === branch ? "active" : ""}`} onClick={() => b !== branch && doCheckout(b)}>
            {b === branch ? "● " : "  "}{b}
          </div>
        ))}
      </div>

      {/* Remotes */}
      <div className="git-section">
        <div className="git-section-header">
          <ExternalLink size={11} /> Remotes
          <button className="git-section-action" onClick={() => setShowRemoteForm(s => !s)}><Plus size={10} /></button>
        </div>
        {remotes.map(r => (
          <div key={r.name} className="git-remote-row">
            <span>{r.name}</span><span className="git-remote-url">{r.url}</span>
          </div>
        ))}
        {showRemoteForm && (
          <div className="git-remote-form">
            <input className="git-branch-input" value={remoteUrl} onChange={e => setRemoteUrl(e.target.value)} placeholder="https://github.com/user/repo.git" />
            <button className="git-branch-btn" onClick={doAddRemote}>Add</button>
          </div>
        )}
      </div>

      {commits.length > 0 && <CommitHistory commits={commits} />}
    </div>
  );
}
