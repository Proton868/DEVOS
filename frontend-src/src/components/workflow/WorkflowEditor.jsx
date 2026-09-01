import React, { useState, useEffect, useCallback } from "react";
import {
  Plus, Trash2, Save, Play, Pause, Download,
  Upload, Code, GitBranch, Clock, ChevronRight,
  ChevronDown, X, Loader, Copy, Check, AlertCircle,
  ArrowRight, Settings, Tag, FileText,
} from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

export default function WorkflowEditor() {
  const { workflowOpen, setWorkflowOpen } = useStore();
  const [workflows, setWorkflows] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [steps, setSteps] = useState([]);
  const [triggers, setTriggers] = useState(["manual"]);
  const [schedule, setSchedule] = useState("");
  const [tags, setTags] = useState([]);
  const [tagInput, setTagInput] = useState("");
  const [yamlPreview, setYamlPreview] = useState("");
  const [showYaml, setShowYaml] = useState(false);
  const [importYaml, setImportYaml] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [expandedStep, setExpandedStep] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [availableCapabilities, setAvailableCapabilities] = useState([]);

  useEffect(() => {
    if (workflowOpen) {
      loadWorkflows();
      loadCapabilities();
    }
  }, [workflowOpen]);

  const loadWorkflows = async () => {
    try {
      const result = await api.listWorkflows();
      setWorkflows(result.workflows || []);
    } catch {}
  };

  const loadCapabilities = async () => {
    try {
      const result = await api.listCapabilities();
      setAvailableCapabilities(result.capabilities || []);
    } catch {}
  };

  const selectWorkflow = async (id) => {
    setSelectedId(id);
    try {
      const result = await api.getWorkflow(id);
      const wf = result.workflow;
      setName(wf.name || "");
      setDescription(wf.description || "");
      setSteps(wf.steps || []);
      setTriggers(wf.triggers || ["manual"]);
      setSchedule(wf.schedule || "");
      setTags(wf.tags || []);
      setEditing(true);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };

  const newWorkflow = () => {
    setSelectedId(null);
    setName("");
    setDescription("");
    setSteps([{
      id: "step_1",
      type: "capability",
      name: "First Step",
      capability: "",
      inputs: {},
      next_step: null,
      timeout_s: 300,
      retry: 0,
    }]);
    setTriggers(["manual"]);
    setSchedule("");
    setTags([]);
    setEditing(true);
    setError(null);
  };

  const addStep = () => {
    const newId = `step_${steps.length + 1}`;
    setSteps([...steps, {
      id: newId,
      type: "capability",
      name: `Step ${steps.length + 1}`,
      capability: "",
      inputs: {},
      next_step: null,
      timeout_s: 300,
      retry: 0,
    }]);
  };

  const removeStep = (idx) => {
    setSteps(steps.filter((_, i) => i !== idx));
  };

  const updateStep = (idx, field, value) => {
    const updated = [...steps];
    updated[idx] = { ...updated[idx], [field]: value };
    setSteps(updated);
  };

  const updateStepInputs = (idx, key, value) => {
    const updated = [...steps];
    updated[idx] = {
      ...updated[idx],
      inputs: { ...updated[idx].inputs, [key]: value },
    };
    setSteps(updated);
  };

  const removeStepInput = (idx, key) => {
    const updated = [...steps];
    const { [key]: _, ...rest } = updated[idx].inputs || {};
    updated[idx] = { ...updated[idx], inputs: rest };
    setSteps(updated);
  };

  const addStepInput = (idx) => {
    const key = `input_${Object.keys(steps[idx]?.inputs || {}).length + 1}`;
    updateStepInputs(idx, key, "");
  };

  const addTag = () => {
    const t = tagInput.trim();
    if (t && !tags.includes(t)) {
      setTags([...tags, t]);
      setTagInput("");
    }
  };

  const removeTag = (tag) => {
    setTags(tags.filter((t) => t !== tag));
  };

  const saveWorkflow = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const data = {
        name: name.trim(),
        description,
        steps: steps.map((s) => ({
          ...s,
          inputs: s.inputs || {},
          outputs: s.outputs || {},
          branches: s.branches || {},
          condition: s.condition || null,
          on_error: s.on_error || null,
          metadata: s.metadata || {},
        })),
        start_step: steps[0]?.id || null,
        triggers,
        schedule: schedule || null,
        tags,
      };

      if (selectedId) {
        await api.updateWorkflow(selectedId, data);
      } else {
        const result = await api.createWorkflow(data);
        setSelectedId(result.workflow?.workflow_id);
        if (result.yaml) setYamlPreview(result.yaml);
      }
      loadWorkflows();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const deleteWorkflow = async (id) => {
    try {
      await api.deleteWorkflow(id);
      if (selectedId === id) {
        setSelectedId(null);
        setEditing(false);
      }
      loadWorkflows();
    } catch (e) {
      setError(e.message);
    }
  };

  const exportWorkflow = async (id) => {
    try {
      const result = await api.exportWorkflow(id, "yaml");
      setYamlPreview(result.content);
      setShowYaml(true);
      setShowImport(false);
    } catch (e) {
      setError(e.message);
    }
  };

  const importWorkflowFromYaml = async () => {
    if (!importYaml.trim()) return;
    try {
      const result = await api.importWorkflow(importYaml.trim(), "yaml");
      setSelectedId(result.workflow?.workflow_id);
      loadWorkflows();
      setShowImport(false);
      setImportYaml("");
      if (result.workflow) selectWorkflow(result.workflow.workflow_id);
    } catch (e) {
      setError(e.message);
    }
  };

  const copyYaml = () => {
    navigator.clipboard.writeText(yamlPreview);
  };

  if (!workflowOpen) return null;

  const stepTypes = [
    { value: "capability", label: "Capability" },
    { value: "condition", label: "Condition" },
    { value: "parallel", label: "Parallel" },
    { value: "approval", label: "Approval" },
    { value: "subflow", label: "Subflow" },
    { value: "notify", label: "Notify" },
  ];

  return (
    <div className="workflow-editor">
      <div className="workflow-header">
        <GitBranch size={13} />
        <span>Workflow Editor</span>
        <button className="workflow-close" onClick={() => setWorkflowOpen(false)}>
          <X size={13} />
        </button>
      </div>

      <div className="workflow-body">
        {/* Toolbar */}
        <div className="workflow-toolbar">
          <button className="btn-primary-sm" onClick={newWorkflow}>
            <Plus size={12} /> New
          </button>
          <button className="btn-secondary-sm" onClick={() => setShowImport(true)}>
            <Upload size={12} /> Import
          </button>
        </div>

        {/* Import modal */}
        {showImport && (
          <div className="workflow-import-area">
            <textarea
              className="workflow-yaml-input"
              value={importYaml}
              onChange={(e) => setImportYaml(e.target.value)}
              placeholder="Paste YAML or JSON workflow definition..."
              rows={10}
            />
            <div className="workflow-import-actions">
              <button className="btn-primary-sm" onClick={importWorkflowFromYaml}>
                <Upload size={12} /> Import
              </button>
              <button className="btn-secondary-sm" onClick={() => setShowImport(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* YAML Preview */}
        {showYaml && (
          <div className="workflow-yaml-preview">
            <div className="yaml-header">
              <FileText size={12} /> YAML Preview
              <div className="yaml-actions">
                <button className="btn-icon-sm" onClick={copyYaml}>
                  <Copy size={12} />
                </button>
                <button className="btn-icon-sm" onClick={() => setShowYaml(false)}>
                  <X size={12} />
                </button>
              </div>
            </div>
            <pre className="yaml-content">{yamlPreview}</pre>
          </div>
        )}

        {/* Workflow list + editor */}
        <div className="workflow-split">
          {/* List */}
          <div className="workflow-list">
            <div className="list-header">Workflows ({workflows.length})</div>
            {workflows.map((wf) => (
              <div
                key={wf.workflow_id}
                className={`workflow-item ${wf.workflow_id === selectedId ? "active" : ""}`}
                onClick={() => selectWorkflow(wf.workflow_id)}
              >
                <div className="wf-item-main">
                  <span className="wf-name">{wf.name}</span>
                  <span className={`wf-status wf-status-${wf.status || "draft"}`}>
                    {wf.status || "draft"}
                  </span>
                </div>
                <div className="wf-item-meta">
                  <span className="wf-steps">{wf.steps?.length || 0} steps</span>
                  <span className="wf-version">v{wf.version || "1.0.0"}</span>
                </div>
                <div className="wf-item-actions">
                  <button
                    className="btn-icon-xs"
                    onClick={(e) => { e.stopPropagation(); exportWorkflow(wf.workflow_id); }}
                    title="Export"
                  >
                    <Download size={10} />
                  </button>
                  <button
                    className="btn-icon-xs btn-danger"
                    onClick={(e) => { e.stopPropagation(); deleteWorkflow(wf.workflow_id); }}
                    title="Delete"
                  >
                    <Trash2 size={10} />
                  </button>
                </div>
              </div>
            ))}
          </div>

          {/* Editor */}
          {editing && (
            <div className="workflow-editor-body">
              {/* Error */}
              {error && (
                <div className="workflow-error" role="alert">
                  <AlertCircle size={12} /> {error}
                </div>
              )}

              {/* Name & Description */}
              <div className="wf-field">
                <label>Name</label>
                <input
                  className="wf-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Workflow name"
                />
              </div>
              <div className="wf-field">
                <label>Description</label>
                <textarea
                  className="wf-textarea"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What does this workflow do?"
                  rows={2}
                />
              </div>

              {/* Triggers */}
              <div className="wf-field">
                <label>Triggers</label>
                <div className="wf-trigger-select">
                  {["manual", "webhook", "schedule", "event"].map((t) => (
                    <label key={t} className="wf-checkbox-label">
                      <input
                        type="checkbox"
                        checked={triggers.includes(t)}
                        onChange={(e) => {
                          if (e.target.checked) setTriggers([...triggers, t]);
                          else setTriggers(triggers.filter((x) => x !== t));
                        }}
                      />
                      {t}
                    </label>
                  ))}
                </div>
                {triggers.includes("schedule") && (
                  <input
                    className="wf-input"
                    value={schedule}
                    onChange={(e) => setSchedule(e.target.value)}
                    placeholder="Cron expression (e.g. 0 */6 * * *)"
                  />
                )}
              </div>

              {/* Tags */}
              <div className="wf-field">
                <label>Tags</label>
                <div className="wf-tags">
                  {tags.map((t) => (
                    <span key={t} className="wf-tag">
                      <Tag size={10} /> {t}
                      <button className="wf-tag-remove" onClick={() => removeTag(t)}>
                        <X size={10} />
                      </button>
                    </span>
                  ))}
                  <div className="wf-tag-input-row">
                    <input
                      className="wf-tag-input"
                      value={tagInput}
                      onChange={(e) => setTagInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } }}
                      placeholder="Add tag..."
                    />
                    <button className="btn-icon-xs" onClick={addTag}>
                      <Plus size={10} />
                    </button>
                  </div>
                </div>
              </div>

              {/* Steps */}
              <div className="wf-steps-section">
                <div className="wf-steps-header">
                  <label>Steps ({steps.length})</label>
                  <button className="btn-primary-sm" onClick={addStep}>
                    <Plus size={12} /> Add Step
                  </button>
                </div>

                {steps.map((step, idx) => (
                  <div key={step.id} className="wf-step-card">
                    <div
                      className="wf-step-header"
                      onClick={() => setExpandedStep(expandedStep === idx ? null : idx)}
                    >
                      <span className="step-expand">
                        {expandedStep === idx ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                      </span>
                      <span className="step-id">{step.id}</span>
                      <select
                        className="wf-step-type"
                        value={step.type}
                        onChange={(e) => updateStep(idx, "type", e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                      >
                        {stepTypes.map((st) => (
                          <option key={st.value} value={st.value}>{st.label}</option>
                        ))}
                      </select>
                      <input
                        className="wf-step-name"
                        value={step.name}
                        onChange={(e) => updateStep(idx, "name", e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                        placeholder="Step name"
                      />
                      <button
                        className="btn-icon-xs btn-danger"
                        onClick={(e) => { e.stopPropagation(); removeStep(idx); }}
                      >
                        <Trash2 size={10} />
                      </button>
                    </div>

                    {expandedStep === idx && (
                      <div className="wf-step-body">
                        {step.type === "capability" && (
                          <div className="wf-field">
                            <label>Capability</label>
                            <select
                              className="wf-input"
                              value={step.capability || ""}
                              onChange={(e) => updateStep(idx, "capability", e.target.value)}
                            >
                              <option value="">Select capability...</option>
                              {availableCapabilities.map((cap) => (
                                <option key={cap.slug} value={cap.slug}>
                                  {cap.slug} — {cap.name}
                                </option>
                              ))}
                            </select>
                          </div>
                        )}

                        {step.type === "condition" && (
                          <div className="wf-field">
                            <label>Condition</label>
                            <input
                              className="wf-input"
                              value={step.condition || ""}
                              onChange={(e) => updateStep(idx, "condition", e.target.value)}
                              placeholder="e.g. ${{ output.status }} == 'success'"
                            />
                          </div>
                        )}

                        <div className="wf-field">
                          <label>Next Step</label>
                          <select
                            className="wf-input"
                            value={step.next_step || ""}
                            onChange={(e) => updateStep(idx, "next_step", e.target.value || null)}
                          >
                            <option value="">(end)</option>
                            {steps.filter((s) => s.id !== step.id).map((s) => (
                              <option key={s.id} value={s.id}>{s.id} — {s.name}</option>
                            ))}
                          </select>
                        </div>

                        <div className="wf-field-row">
                          <div className="wf-field">
                            <label>Timeout (s)</label>
                            <input
                              type="number"
                              className="wf-input wf-input-sm"
                              value={step.timeout_s || 300}
                              onChange={(e) => updateStep(idx, "timeout_s", parseInt(e.target.value) || 300)}
                            />
                          </div>
                          <div className="wf-field">
                            <label>Retries</label>
                            <input
                              type="number"
                              className="wf-input wf-input-sm"
                              value={step.retry || 0}
                              onChange={(e) => updateStep(idx, "retry", parseInt(e.target.value) || 0)}
                            />
                          </div>
                        </div>

                        {/* Inputs */}
                        <div className="wf-field">
                          <label>Inputs</label>
                          {(Object.entries(step.inputs || {})).map(([key, value]) => (
                            <div key={key} className="wf-input-row">
                              <input
                                className="wf-input wf-input-sm"
                                value={key}
                                onChange={(e) => {
                                  const newKey = e.target.value;
                                  const newInputs = { ...step.inputs };
                                  delete newInputs[key];
                                  newInputs[newKey] = value;
                                  const updated = [...steps];
                                  updated[idx] = { ...updated[idx], inputs: newInputs };
                                  setSteps(updated);
                                }}
                                placeholder="key"
                              />
                              <input
                                className="wf-input wf-input-sm"
                                value={value}
                                onChange={(e) => updateStepInputs(idx, key, e.target.value)}
                                placeholder="value"
                              />
                              <button
                                className="btn-icon-xs btn-danger"
                                onClick={() => removeStepInput(idx, key)}
                              >
                                <X size={10} />
                              </button>
                            </div>
                          ))}
                          <button className="btn-secondary-sm" onClick={() => addStepInput(idx)}>
                            <Plus size={10} /> Add Input
                          </button>
                        </div>

                        {/* Description */}
                        <div className="wf-field">
                          <label>Description</label>
                          <textarea
                            className="wf-textarea"
                            value={step.description || ""}
                            onChange={(e) => updateStep(idx, "description", e.target.value)}
                            placeholder="What does this step do?"
                            rows={2}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* Save */}
              <div className="wf-save-area">
                <button
                  className="btn-primary"
                  onClick={saveWorkflow}
                  disabled={saving || !name.trim()}
                >
                  {saving ? <Loader size={13} className="spin-slow" /> : <Save size={13} />}
                  Save Workflow
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}