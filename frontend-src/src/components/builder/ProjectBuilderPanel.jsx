/* cspell:words fastapi vite */
import React, { useEffect, useMemo, useState } from "react";
import {
  Sparkles, X, Play, Loader, CheckCircle2, AlertCircle,
  ChevronRight, Rocket, FileCode, RefreshCw, FolderOpen,
} from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

const DEFAULT_STACK = "fastapi";

function statusPill(status) {
  if (!status || status === "idle") return null;
  const variants = {
    running: { label: "Building...", color: "#d29922" },
    success: { label: "Built", color: "#3fb950" },
    error: { label: "Failed", color: "#f85149" },
  };
  const style = variants[status] || { label: status, color: "#8b949e" };
  return <span className="builder-status-pill" style={{ color: style.color }}>{style.label}</span>;
}

export default function ProjectBuilderPanel() {
  const { setStatus, currentProject, setCurrentProject } = useStore();
  const [open, setOpen] = useState(true);
  const [stacks, setStacks] = useState([]);
  const [projects, setProjects] = useState([]);
  const [name, setName] = useState("sample-app");
  const [description, setDescription] = useState("A small full-stack app to automate a workflow.");
  const [stack, setStack] = useState(DEFAULT_STACK);
  const [features, setFeatures] = useState("CRUD, auth, dashboard");
  const [loading, setLoading] = useState(false);
  const [status, setBuildStatus] = useState("idle");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    api.listStacks().then((data) => {
      if (!active) return;
      setStacks(data.stacks || []);
      const first = (data.stacks || [])[0]?.id || DEFAULT_STACK;
      setStack(first);
    }).catch(() => {});
    api.listProjects().then((data) => {
      if (!active) return;
      setProjects(data.projects || []);
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  const selectedStack = useMemo(
    () => stacks.find((s) => s.id === stack) || stacks[0],
    [stacks, stack]
  );

  const refreshProjects = async () => {
    try {
      const next = await api.listProjects();
      setProjects(next.projects || []);
    } catch {}
  };

  const submit = async () => {
    if (!name.trim()) {
      setError("Project name is required.");
      return;
    }
    setLoading(true);
    setError(null);
    setBuildStatus("running");
    setStatus("Building project…");
    try {
      const payload = {
        name: name.trim(),
        description: description.trim(),
        stack,
        features: features.split(/[,;\n]+/).map((item) => item.trim()).filter(Boolean),
        verify: false,
      };
      const res = await api.buildProject(payload);
      setResult(res);
      const hasErrors = Array.isArray(res?.errors) && res.errors.length > 0;
      setBuildStatus(hasErrors ? "error" : "success");
      setStatus(hasErrors ? "Build completed with warnings" : "Build completed");
      // Switch the IDE into the generated project immediately, so the file
      // tree reflects the new code.
      await setCurrentProject(res.project_id);
      await refreshProjects();
    } catch (err) {
      setError(err.message || "Build failed");
      setBuildStatus("error");
      setStatus("Build failed");
    } finally {
      setLoading(false);
    }
  };

  if (!open) return null;

  return (
    <div className="builder-panel">
      <div className="builder-header">
        <div className="builder-header-left">
          <Sparkles size={14} color="#3fb950" />
          <strong>New Project</strong>
          {statusPill(status)}
        </div>
        <div className="builder-header-right">
          <button className="btn-icon" onClick={refreshProjects} title="Refresh list"><RefreshCw size={13} /></button>
          <button className="btn-icon" onClick={() => setOpen(false)}><X size={14} /></button>
        </div>
      </div>

      <div className="builder-form">
        <label>Project name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="my-app"
          data-testid="builder-name-input"
        />

        <label>Description</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
          placeholder="What should this project do?"
          data-testid="builder-description-input"
        />

        <label>Stack</label>
        <select
          value={stack}
          onChange={(e) => setStack(e.target.value)}
          data-testid="builder-stack-select"
        >
          {stacks.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.id} ({entry.language})
            </option>
          ))}
        </select>
        {selectedStack && (
          <div className="builder-stack-description">{selectedStack.description}</div>
        )}

        <label>Features (comma separated)</label>
        <textarea
          value={features}
          onChange={(e) => setFeatures(e.target.value)}
          rows={2}
          placeholder="auth, dashboard, API, admin"
          data-testid="builder-features-input"
        />

        <button
          className="btn-primary"
          onClick={submit}
          disabled={loading}
          data-testid="builder-submit-btn"
        >
          {loading ? <Loader size={14} className="spin-slow" /> : <Rocket size={14} />}
          {loading ? "Building..." : "Build project"}
        </button>
      </div>

      {error && (
        <div className="builder-alert builder-alert-error">
          <AlertCircle size={13} /> {error}
        </div>
      )}

      {result && (
        <div className="builder-result">
          <div className="builder-result-header">
            {result?.errors?.length ? <AlertCircle size={14} color="#d29922" /> : <CheckCircle2 size={14} color="#3fb950" />}
            <strong>{result?.errors?.length ? "Build completed with warnings" : "Build complete"}</strong>
          </div>
          <div className="builder-result-row">
            <FileCode size={13} /> <span>Project ID:</span> <code>{result.project_id}</code>
          </div>
          <div className="builder-result-row">
            <FolderOpen size={13} /> <span>Files generated:</span> {result.files?.length || 0}
          </div>
          {result.setup_commands?.length > 0 && (
            <div className="builder-result-row">
              <Play size={13} /> <span>Next steps:</span> <code>{result.setup_commands.join(" / ")}</code>
            </div>
          )}
          {result.errors?.length > 0 && (
            <div className="builder-alert builder-alert-error" style={{ marginTop: 8 }}>
              {result.errors.join(" • ")}
            </div>
          )}
        </div>
      )}

      {projects.length > 0 && (
        <div className="builder-recent">
          <div className="builder-recent-title">
            Recent projects
            <button className="btn-secondary-sm" onClick={refreshProjects}>Refresh</button>
          </div>
          {projects.slice(0, 6).map((item) => {
            const isActive = currentProject === item.project_id;
            return (
              <button
                key={item.project_id}
                className={`builder-project-row ${isActive ? "active" : ""}`}
                onClick={async () => {
                  setStatus(`Switched to project: ${item.spec?.name || item.project_id}`);
                  await setCurrentProject(item.project_id);
                }}
                title="Click to load project into workspace"
                data-testid={`builder-project-${item.project_id}`}
              >
                <span className="builder-project-name">
                  {isActive ? "⚡ " : ""}{item.spec?.name || item.project_id}
                </span>
                <span className="builder-project-stack">{item.spec?.stack || "app"}</span>
                <ChevronRight size={13} className="builder-project-chevron" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
