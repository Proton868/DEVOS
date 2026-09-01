import React, { useState, useRef, useEffect } from "react";
import {
  Search, Loader, FileText, ExternalLink, Clock,
  CheckCircle, XCircle, ChevronRight, ChevronDown,
  BookOpen, Globe, X, RefreshCw, Zap,
} from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

export default function ResearchPanel() {
  const { researchOpen, setResearchOpen } = useStore();
  const [question, setQuestion] = useState("");
  const [depth, setDepth] = useState("standard");
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [activeJobId, setActiveJobId] = useState(null);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState(null);
  const [expandedSources, setExpandedSources] = useState({});
  const inputRef = useRef(null);

  useEffect(() => {
    if (researchOpen) {
      loadJobs();
    }
  }, [researchOpen]);

  const loadJobs = async () => {
    try {
      const result = await api.listResearchJobs();
      setJobs(result.jobs || []);
    } catch {}
  };

  const startResearch = async () => {
    if (!question.trim()) return;
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      const result = await api.startResearch(question.trim(), {
        depth,
        max_sources: depth === "deep" ? 10 : depth === "quick" ? 3 : 5,
      });
      setActiveJobId(result.job_id);
      setPolling(true);
      pollJob(result.job_id);
    } catch (e) {
      setError(e.message);
      setLoading(false);
    }
  };

  const quickResearch = async () => {
    if (!question.trim()) return;
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      const result = await api.quickResearch(question.trim(), {
        depth,
        max_sources: depth === "deep" ? 10 : depth === "quick" ? 3 : 5,
      });
      setReport(result.report || result);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const pollJob = async (jobId) => {
    const maxPolls = 60;
    let polls = 0;
    const interval = setInterval(async () => {
      polls++;
      try {
        const job = await api.getResearchJob(jobId);
        if (job.status === "done") {
          clearInterval(interval);
          setPolling(false);
          setLoading(false);
          setReport(job.report);
          loadJobs();
        } else if (job.status === "failed") {
          clearInterval(interval);
          setPolling(false);
          setLoading(false);
          setError(job.error || "Research failed");
          loadJobs();
        } else if (polls >= maxPolls) {
          clearInterval(interval);
          setPolling(false);
          setLoading(false);
          setError("Research timed out");
        }
      } catch {
        clearInterval(interval);
        setPolling(false);
        setLoading(false);
        setError("Lost connection to research job");
      }
    }, 2000);
  };

  const toggleSource = (id) => {
    setExpandedSources((s) => ({ ...s, [id]: !s[id] }));
  };

  if (!researchOpen) return null;

  return (
    <div className="research-panel">
      <div className="research-header">
        <BookOpen size={13} />
        <span>Deep Research</span>
        <button className="research-close" onClick={() => setResearchOpen(false)}>
          <X size={13} />
        </button>
      </div>

      <div className="research-body">
        {/* Query Input */}
        <div className="research-input-area">
          <textarea
            ref={inputRef}
            className="research-input"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                quickResearch();
              }
            }}
            placeholder="Ask a research question...&#10;e.g. 'What are the latest trends in Rust web frameworks?'"
            rows={3}
          />
          <div className="research-controls">
            <div className="research-depth-select">
              <button
                className={`depth-btn ${depth === "quick" ? "active" : ""}`}
                onClick={() => setDepth("quick")}
              >
                <Zap size={11} /> Quick
              </button>
              <button
                className={`depth-btn ${depth === "standard" ? "active" : ""}`}
                onClick={() => setDepth("standard")}
              >
                <Search size={11} /> Standard
              </button>
              <button
                className={`depth-btn ${depth === "deep" ? "active" : ""}`}
                onClick={() => setDepth("deep")}
              >
                <Globe size={11} /> Deep
              </button>
            </div>
            <div className="research-actions">
              <button
                className="btn-primary-sm"
                onClick={quickResearch}
                disabled={loading || !question.trim()}
              >
                {loading ? <Loader size={12} className="spin-slow" /> : <Search size={12} />}
                Research
              </button>
              <button
                className="btn-secondary-sm"
                onClick={startResearch}
                disabled={loading || !question.trim()}
              >
                <Clock size={12} /> Background
              </button>
            </div>
          </div>
        </div>

        {/* Status */}
        {polling && (
          <div className="research-status">
            <Loader size={12} className="spin-slow" />
            <span>Researching... {activeJobId && `(Job: ${activeJobId.slice(0, 8)}...)`}</span>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="research-error" role="alert">
            <XCircle size={12} /> {error}
          </div>
        )}

        {/* Report */}
        {report && (
          <div className="research-report">
            <div className="report-summary">
              <div className="report-title">
                <FileText size={14} />
                <span>{report.question || question}</span>
              </div>
              {report.summary && (
                <div className="report-summary-text">{report.summary}</div>
              )}
            </div>

            {/* Sources */}
            {report.sources && report.sources.length > 0 && (
              <div className="report-sources">
                <div className="sources-header">
                  <Globe size={12} /> {report.sources.length} Sources
                </div>
                {report.sources.map((source, i) => (
                  <div key={source.url || i} className="source-item">
                    <div
                      className="source-title"
                      onClick={() => toggleSource(i)}
                    >
                      <span>{expandedSources[i] ? <ChevronDown size={11} /> : <ChevronRight size={11} />}</span>
                      <span className="source-name">{source.title || source.url}</span>
                      {source.url && (
                        <a
                          href={source.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="source-link"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <ExternalLink size={10} />
                        </a>
                      )}
                    </div>
                    {expandedSources[i] && source.content && (
                      <div className="source-content">
                        {source.content.slice(0, 500)}
                        {source.content.length > 500 && "..."}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Citations */}
            {report.citations && report.citations.length > 0 && (
              <div className="report-citations">
                <div className="citations-header">
                  <BookOpen size={12} /> Citations
                </div>
                {report.citations.map((cite, i) => (
                  <div key={cite.id || i} className="citation-item">
                    <span className="citation-id">[{cite.id || i + 1}]</span>
                    <span className="citation-text">{cite.text || cite.content}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Full Report */}
            {report.full_report && (
              <div className="report-full">
                <div className="full-report-header">Full Report</div>
                <pre className="full-report-content">{report.full_report}</pre>
              </div>
            )}
          </div>
        )}

        {/* Recent Jobs */}
        {jobs.length > 0 && (
          <div className="research-jobs">
            <div className="jobs-header">
              <Clock size={12} /> Recent Jobs
            </div>
            {jobs.slice(0, 10).map((job) => (
              <div
                key={job.job_id}
                className={`job-item ${job.job_id === activeJobId ? "active" : ""}`}
                onClick={() => {
                  if (job.status === "done") {
                    setActiveJobId(job.job_id);
                    api.getResearchJob(job.job_id).then((j) => {
                      if (j.report) setReport(j.report);
                    });
                  }
                }}
              >
                <span className="job-status">
                  {job.status === "running" && <Loader size={10} className="spin-slow" />}
                  {job.status === "done" && <CheckCircle size={10} className="text-green" />}
                  {job.status === "failed" && <XCircle size={10} className="text-red" />}
                </span>
                <span className="job-question">
                  {job.question?.slice(0, 60)}
                  {job.question?.length > 60 && "..."}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}