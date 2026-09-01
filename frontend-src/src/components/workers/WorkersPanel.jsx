import React, { useState, useEffect, useCallback, useRef } from "react";
import { Users, Play, Loader, X, ChevronDown, ChevronRight,
         GitBranch as DivisionIcon, Zap, CheckCircle, XCircle, AlertTriangle } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";
import "./WorkersPanel.css";

function groupByDivision(workers) {
  const groups = {};
  for (const w of workers) {
    (groups[w.division] ||= []).push(w);
  }
  return groups;
}

function WorkerPicker({ workers, selected, onSelect }) {
  const [expanded, setExpanded] = useState(new Set());
  const seenDivisions = useRef(new Set());

  // `workers` arrives asynchronously (see WorkersPanel's useEffect) — it's
  // empty on first render. Computing `expanded`'s initial value from
  // `workers` via a useState initializer (the first version of this
  // component did) only runs once, at that empty-array moment, so every
  // division would render permanently collapsed once real data arrived.
  // Instead, default-expand each division the first time it's ever seen,
  // tracked in a ref separate from `expanded` itself — using `expanded`
  // as the "have we seen this" check would re-expand a division a user
  // deliberately collapsed, the moment `workers` re-renders for any
  // unrelated reason.
  useEffect(() => {
    const divisions = Object.keys(groupByDivision(workers));
    const newOnes = divisions.filter((d) => !seenDivisions.current.has(d));
    if (newOnes.length === 0) return;
    newOnes.forEach((d) => seenDivisions.current.add(d));
    setExpanded((prev) => new Set([...prev, ...newOnes]));
  }, [workers]);

  const groups = groupByDivision(workers);

  const toggle = (division) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(division) ? next.delete(division) : next.add(division);
      return next;
    });
  };

  return (
    <div className="workers-picker">
      {Object.entries(groups).map(([division, list]) => (
        <div key={division} className="workers-division">
          <button className="workers-division-header" onClick={() => toggle(division)}>
            {expanded.has(division) ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <DivisionIcon size={12} />
            <span>{division}</span>
            <span className="workers-division-count">{list.length}</span>
          </button>
          {expanded.has(division) && (
            <div className="workers-division-list">
              {list.map((w) => (
                <button
                  key={w.slug}
                  className={`workers-item ${selected?.slug === w.slug ? "selected" : ""}`}
                  onClick={() => onSelect(w)}
                  title={w.description}
                >
                  <span className="workers-item-name">{w.name}</span>
                  {w.tools.includes("spawn_agent") && (
                    <span className="workers-item-badge" title="Can delegate to other Workers">
                      <Zap size={10} />
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function StepLine({ step }) {
  const icons = {
    think: <Loader size={11} className="spin-slow" />,
    plan: <CheckCircle size={11} color="#58a6ff" />,
    observe: <ChevronRight size={11} color="#8b949e" />,
    reflect: <AlertTriangle size={11} color="#d29922" />,
    hitl: <AlertTriangle size={11} color="#d29922" />,
    denied: <XCircle size={11} color="#f85149" />,
    done: <CheckCircle size={11} color="#3fb950" />,
  };
  return (
    <div className={`workers-step workers-step-${step.step_type}`}>
      {icons[step.step_type] || <ChevronRight size={11} />}
      <span>{step.content}</span>
    </div>
  );
}

export default function WorkersPanel() {
  const { setWorkersOpen } = useStore();
  const [workers, setWorkers] = useState([]);
  const [selected, setSelected] = useState(null);
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState("single"); // "single" | "coordinated"
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const stepsEndRef = useRef(null);

  useEffect(() => {
    api.listWorkers()
      .then(({ workers }) => setWorkers(workers))
      .catch((e) => setError(`Could not load workers: ${e.message}`));
  }, []);

  useEffect(() => {
    stepsEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [steps]);

  const runSingle = useCallback(async () => {
    setSteps([]);
    setResult(null);
    setError(null);
    setRunning(true);
    try {
      for await (const evt of api.streamWorker(selected.slug, goal)) {
        if (evt.type === "step") {
          setSteps((prev) => [...prev, evt]);
        } else if (evt.type === "done") {
          setResult(evt);
        } else if (evt.type === "error") {
          setError(evt.content || "Worker run failed.");
        }
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }, [selected, goal]);

  const runCoordinated = useCallback(async () => {
    setSteps([]);
    setResult(null);
    setError(null);
    setRunning(true);
    try {
      const plan = await api.runCoordinatedPlan(goal);
      setResult(plan);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }, [goal]);

  const canRun = !running && goal.trim() && (mode === "coordinated" || selected);

  return (
    <div className="workers-panel">
      <div className="workers-header">
        <Users size={14} />
        <span>Workers</span>
        <button className="workers-close" onClick={() => setWorkersOpen(false)} aria-label="Close Workers panel">
          <X size={14} />
        </button>
      </div>

      <div className="workers-mode-toggle">
        <button className={mode === "single" ? "active" : ""} onClick={() => setMode("single")}>
          Single Worker
        </button>
        <button className={mode === "coordinated" ? "active" : ""} onClick={() => setMode("coordinated")}>
          Coordinated Plan
        </button>
      </div>

      {mode === "single" && (
        <>
          <div className="workers-picker-label">
            {workers.length === 0 && !error ? "Loading workers…" : `${workers.length} specialists available`}
          </div>
          <WorkerPicker workers={workers} selected={selected} onSelect={setSelected} />
          {selected && <div className="workers-selected-desc">{selected.description}</div>}
        </>
      )}

      {mode === "coordinated" && (
        <div className="workers-coordinated-note">
          Describe the overall goal — it'll be broken into subtasks and each one routed
          to whichever specialist fits best, running independent subtasks concurrently.
        </div>
      )}

      <textarea
        className="workers-goal-input"
        placeholder={mode === "single" ? "Describe the task for this Worker…" : "Describe the overall goal…"}
        value={goal}
        onChange={(e) => setGoal(e.target.value)}
        disabled={running}
        rows={3}
      />

      <button
        className="workers-run-btn"
        disabled={!canRun}
        onClick={mode === "single" ? runSingle : runCoordinated}
      >
        {running ? <Loader size={13} className="spin-slow" /> : <Play size={13} />}
        {running ? "Running…" : "Run"}
      </button>

      {error && (
        <div className="workers-error" role="alert">
          <XCircle size={13} /> {error}
        </div>
      )}

      {mode === "single" && steps.length > 0 && (
        <div className="workers-steps">
          {steps.map((s, i) => <StepLine key={i} step={s} />)}
          <div ref={stepsEndRef} />
        </div>
      )}

      {mode === "single" && result && (
        <div className={`workers-result ${result.decision === "complete" ? "success" : "failed"}`}>
          <strong>{result.decision === "complete" ? "Completed" : "Did not complete"}</strong>
          <p>{result.final_answer}</p>
        </div>
      )}

      {mode === "coordinated" && result && (
        <div className="workers-plan-results">
          {result.results?.map((r) => (
            <div key={r.subtask_id} className={`workers-plan-item ${r.success ? "success" : "failed"}`}>
              {r.success ? <CheckCircle size={12} /> : <XCircle size={12} />}
              <span className="workers-plan-worker">{r.worker_slug}</span>
              <span className="workers-plan-output">{r.output}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
