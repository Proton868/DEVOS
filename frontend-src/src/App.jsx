import React, { Suspense, lazy, useEffect, useCallback } from "react";
import useStore from "./store/useStore";
import useOsStore from "./os/store/osStore";
import { api, verifySession, subscribeToEvents } from "./services/api";
import { ThemeProvider } from "./theme/ThemeContext";
import LoginScreen from "./components/auth/LoginScreen";
import ErrorBoundary from "./components/ErrorBoundary";
import DevOSWorkspace from "./os/workspace/DevOSWorkspace";
import { registerCoreCommands } from "./commands/registerCoreCommands";

// Lazy load modals
const DiffViewer = lazy(() => import("./components/editor/DiffViewer"));

const Spin = () => (
  <div className="flex items-center justify-center h-full text-slate-400 text-xs">
    Loading...
  </div>
);

function HitlApprovalToasts() {
  const { pendingHitlRequests, removePendingHitlRequest, setStatus } = useStore();
  if (!pendingHitlRequests || !pendingHitlRequests.length) return null;

  const handleApprove = async (reqId) => {
    try {
      await api.approveHitl(reqId);
      setStatus("HITL approved");
      removePendingHitlRequest(reqId);
    } catch (e) {
      setStatus("Approval failed: " + e.message);
    }
  };
  const handleDeny = async (reqId) => {
    try {
      await api.denyHitl(reqId);
      setStatus("HITL denied");
      removePendingHitlRequest(reqId);
    } catch (e) {
      setStatus("Deny failed: " + e.message);
    }
  };

  return (
    <div className="fixed top-4 right-4 z-[600] flex flex-col gap-3 max-w-sm">
      {pendingHitlRequests.map((req) => (
        <div key={req.id} className="glass-panel p-4 border-l-4" style={{ borderLeftColor: "var(--accent)" }}>
          <div className="flex items-center gap-2 mb-2 text-sm font-semibold text-slate-100">
            <span style={{ color: "var(--accent)" }}>⚡</span> Human Approval Required
          </div>
          <div className="text-xs text-slate-300 mb-3 leading-relaxed">{req.description}</div>
          <div className="flex items-center gap-2 justify-end">
            <button
              onClick={() => handleDeny(req.id)}
              className="px-3 py-1.5 rounded-md text-slate-300 text-xs hover:bg-white/[0.10]"
              style={{ background: "var(--bg-3)" }}
            >
              Deny
            </button>
            <button
              onClick={() => handleApprove(req.id)}
              className="px-3 py-1.5 rounded-md text-xs font-semibold text-white"
              style={{ background: "var(--accent)" }}
            >
              Approve
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

async function handleSupabaseRedirect(setUser, setStatus) {
  const hash = window.location.hash;
  if (!hash || !hash.includes("access_token")) return false;
  const params = new URLSearchParams(hash.slice(1));
  const accessToken = params.get("access_token");
  if (!accessToken) return false;
  try {
    const user = await api.supabaseExchange(accessToken).then((r) => {
      localStorage.setItem("devos_token", r.token);
      return r.user;
    });
    setUser(user);
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    return true;
  } catch (e) {
    setStatus("OAuth sign-in failed: " + e.message);
    return false;
  }
}

export default function App() {
  const {
    setFileTree, setProviders, setProvider, setWorkspaceSettings,
    setIndexStats, setGitStatus,
        setUser, setStatus,
    isAuthenticated, authChecked,
  } = useStore();

  const { addPendingHitlRequest, removePendingHitlRequest } = useStore();

  // Canonical command registry (Requirement 20) — once per session
  useEffect(() => {
    registerCoreCommands();
  }, []);

  // Auth check
  useEffect(() => {
    handleSupabaseRedirect(setUser, setStatus).finally(() => {
      verifySession().then((user) => setUser(user));
    });
  }, [setUser, setStatus]);

  // Prefer Supabase SDK session events over manual hash parsing alone
  useEffect(() => {
    let unsub = () => {};
    (async () => {
      try {
        const { supabase } = await import("./services/supabase");
        if (!supabase) return;
        const { data } = supabase.auth.onAuthStateChange(async (event, session) => {
          if ((event === "SIGNED_IN" || event === "TOKEN_REFRESHED") && session?.access_token) {
            try {
              const { api } = await import("./services/api");
              // exchange/sync path
              const r = await fetch("/api/auth/supabase/sync", {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  Authorization: `Bearer ${session.access_token}`,
                },
              });
              if (r.ok) {
                const user = await r.json();
                setUser(user);
              }
            } catch (e) {}
          }
          if (event === "SIGNED_OUT") {
            setUser(null);
          }
        });
        unsub = () => data?.subscription?.unsubscribe?.();
      } catch (e) {}
    })();
    return () => unsub();
  }, [setUser]);


  // Event subscription
  useEffect(() => {
    if (!isAuthenticated) return;
    const unsub = subscribeToEvents(
      (event) => {
        if (event.type === "hitl.pending") addPendingHitlRequest(event.data);
        else if (["hitl.resolved", "hitl.deny", "hitl.approve"].includes(event.type)) {
          removePendingHitlRequest(event.data.id || event.data);
        }
      },
      () => {}
    );
    return () => unsub();
  }, [isAuthenticated, addPendingHitlRequest, removePendingHitlRequest]);

  // API initialization
  useEffect(() => {
    if (!isAuthenticated) return;
    api.getProviders().then((p) => {
      setProviders(p);
      if (!localStorage.getItem("devos_provider")) {
        const first = Object.entries(p).find(([, v]) => v.configured);
        if (first) setProvider(first[0]);
      }
    }).catch(() => useStore.getState().setStatus("⚠️ Backend offline — start the server"));

    api.getTree({ depth: 1 }).then(({ tree }) => setFileTree(tree || [])).catch(() => {});
    api.getIndexStatus().then(setIndexStats).catch(() => {});
    api.getSettings().then((s) => setWorkspaceSettings(s)).catch(() => {});
    api.gitStatus().then(setGitStatus).catch(() => {});
  }, [isAuthenticated, setFileTree, setProviders, setProvider, setWorkspaceSettings, setIndexStats, setGitStatus]);

  // File watcher WebSocket
  useEffect(() => {
    if (!isAuthenticated) return;
    const wsBase = process.env.REACT_APP_DEVOS_URL
      ? process.env.REACT_APP_DEVOS_URL.replace(/^http/, "ws")
      : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;
    const ws = new WebSocket(`${wsBase}?type=filewatcher`);
    ws.onmessage = () => api.getTree({ depth: 1 }).then(({ tree }) => setFileTree(tree || [])).catch(() => {});
    ws.onerror = () => {};
    return () => ws.close();
  }, [isAuthenticated, setFileTree]);

       // Keyboard shortcut for CMD+, (open Settings overlay via Spatial OS)
    // Keyboard shortcut for CMD+, (open Settings overlay via Spatial OS)
  const handleKey = useCallback((e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key === ",") {
      e.preventDefault();
      useOsStore.getState().setOverlay("settings");
    }
  }, []);

  useEffect(() => {
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [handleKey]);

  // Loading state
  if (!authChecked) {
    return (
      <div className="h-screen w-screen flex items-center justify-center text-slate-300" style={{ background: "var(--bg-0)" }}>
        <Spin />
      </div>
    );
  }

  return (
    <ErrorBoundary>
      <ThemeProvider>
        {!isAuthenticated ? (
          <LoginScreen />
        ) : (
          <>
            <DevOSWorkspace />
            <HitlApprovalToasts />
            <Suspense fallback={null}>
              <DiffViewer />
            </Suspense>
          </>
        )}
      </ThemeProvider>
    </ErrorBoundary>
  );
}
