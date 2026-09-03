/**
 * Monaco ↔ DEVOS LSP manager (WebSocket JSON-RPC proxy).
 *
 * Connects to /api/lsp/{projectId}/ws?lang=...
 * First message authenticates with { token }.
 * Language servers run on the backend, confined to the project workspace.
 */
import { useEffect, useRef, useCallback } from "react";
import useStore from "../store/useStore";
import { getToken, getCurrentProject, baseUrl } from "../services/api";

const LSP_LANGUAGES = new Set([
  "typescript", "javascript", "python", "css", "html", "json", "yaml",
]);

// key: `${projectId}::${language}` → connection
const connections = new Map();
let msgId = 1;
const pendingRequests = new Map();
const diagnosticHandlers = new Set();

function wsBase() {
  const b = (typeof baseUrl === "function" ? baseUrl() : baseUrl) || "";
  if (b) {
    return String(b).replace(/^http/, "ws");
  }
  const loc = window.location;
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${loc.host}`;
}

function connKey(language) {
  return `${getCurrentProject()}::${language}`;
}

function getOrConnect(language) {
  const key = connKey(language);
  if (connections.has(key)) return connections.get(key);

  const projectId = getCurrentProject();
  const url = `${wsBase()}/api/lsp/${encodeURIComponent(projectId)}/ws?lang=${encodeURIComponent(language)}`;
  const ws = new WebSocket(url);
  const pending = [];
  let authenticated = false;

  ws.onopen = () => {
    const token = getToken() || "";
    ws.send(JSON.stringify({ token }));
    authenticated = true;
    for (const msg of pending) ws.send(msg);
    pending.length = 0;
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg && (msg.type === "lsp.unavailable" || msg.type === "lsp.ready")) {
        return;
      }
      if (msg.id != null && pendingRequests.has(msg.id)) {
        const { resolve, reject } = pendingRequests.get(msg.id);
        pendingRequests.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message || "LSP error"));
        else resolve(msg.result);
      }
      if (msg.method === "textDocument/publishDiagnostics") {
        for (const handler of diagnosticHandlers) handler(msg.params);
      }
    } catch (_) {
      /* ignore */
    }
  };

  ws.onerror = () => {};
  ws.onclose = () => {
    connections.delete(key);
  };

  const conn = {
    ws,
    pending,
    send: (msg) => {
      const str = JSON.stringify(msg);
      if (ws.readyState === WebSocket.OPEN && authenticated) ws.send(str);
      else pending.push(str);
    },
  };

  connections.set(key, conn);
  return conn;
}

function sendRequest(language, method, params) {
  if (!LSP_LANGUAGES.has(language)) return Promise.resolve(null);
  const conn = getOrConnect(language);
  const id = msgId++;
  return new Promise((resolve, reject) => {
    pendingRequests.set(id, { resolve, reject });
    conn.send({ jsonrpc: "2.0", id, method, params });
    setTimeout(() => {
      if (pendingRequests.has(id)) {
        pendingRequests.delete(id);
        resolve(null);
      }
    }, 8000);
  });
}

function sendNotification(language, method, params) {
  if (!LSP_LANGUAGES.has(language)) return;
  const conn = getOrConnect(language);
  conn.send({ jsonrpc: "2.0", method, params });
}

function toUri(path) {
  if (!path) return "file:///workspace/untitled";
  const clean = String(path).replace(/^\/+/, "");
  return `file:///workspace/${clean}`;
}

function severityName(n) {
  return n === 1 ? "error" : n === 2 ? "warning" : n === 3 ? "info" : "hint";
}

export function useLSP(editorRef, monacoRef, filePath, language) {
  const initializedRef = useRef(false);

  useEffect(() => {
    const handler = (params) => {
      const uri = params?.uri?.replace("file://", "") || "";
      const relPath = uri.startsWith("/workspace/")
        ? uri.slice("/workspace/".length)
        : uri.replace(/^\/workspace\//, "");
      const diagnostics = (params.diagnostics || []).map((d) => ({
        path: relPath || filePath,
        line: (d.range?.start?.line || 0) + 1,
        col: (d.range?.start?.character || 0) + 1,
        message: d.message,
        severity: severityName(d.severity),
        source: d.source || "lsp",
      }));
      useStore.setState((s) => ({
        problems: [
          ...(s.problems || []).filter((p) => p.path !== (relPath || filePath)),
          ...diagnostics,
        ],
      }));
      if (monacoRef?.current && editorRef?.current) {
        const model = editorRef.current.getModel?.();
        if (model) {
          const markers = (params.diagnostics || []).map((d) => ({
            severity:
              d.severity === 1
                ? monacoRef.current.MarkerSeverity.Error
                : d.severity === 2
                  ? monacoRef.current.MarkerSeverity.Warning
                  : monacoRef.current.MarkerSeverity.Info,
            startLineNumber: (d.range?.start?.line || 0) + 1,
            startColumn: (d.range?.start?.character || 0) + 1,
            endLineNumber: (d.range?.end?.line || 0) + 1,
            endColumn: (d.range?.end?.character || 0) + 1,
            message: d.message,
            source: d.source || "lsp",
          }));
          monacoRef.current.editor.setModelMarkers(model, "lsp", markers);
        }
      }
    };
    diagnosticHandlers.add(handler);
    return () => diagnosticHandlers.delete(handler);
  }, [filePath, editorRef, monacoRef]);

  useEffect(() => {
    if (!filePath || !language || !LSP_LANGUAGES.has(language)) return;
    sendRequest(language, "initialize", {
      processId: null,
      rootUri: "file:///workspace/",
      capabilities: {},
    }).catch(() => null);
    initializedRef.current = true;
    return () => {
      if (filePath) {
        sendNotification(language, "textDocument/didClose", {
          textDocument: { uri: toUri(filePath) },
        });
      }
    };
  }, [filePath, language]);

  const notifyOpen = useCallback(
    (content) => {
      if (!filePath || !LSP_LANGUAGES.has(language)) return;
      sendNotification(language, "textDocument/didOpen", {
        textDocument: {
          uri: toUri(filePath),
          languageId: language,
          version: 1,
          text: content || "",
        },
      });
    },
    [filePath, language]
  );

  const notifyChange = useCallback(
    (content, version = 2) => {
      if (!filePath || !LSP_LANGUAGES.has(language)) return;
      sendNotification(language, "textDocument/didChange", {
        textDocument: { uri: toUri(filePath), version },
        contentChanges: [{ text: content || "" }],
      });
    },
    [filePath, language]
  );

  const getHover = useCallback(
    async (line, col) => {
      if (!LSP_LANGUAGES.has(language)) return null;
      return sendRequest(language, "textDocument/hover", {
        textDocument: { uri: toUri(filePath) },
        position: { line: line - 1, character: col - 1 },
      });
    },
    [filePath, language]
  );

  const goToDefinition = useCallback(
    async (line, col) => {
      if (!LSP_LANGUAGES.has(language)) return null;
      return sendRequest(language, "textDocument/definition", {
        textDocument: { uri: toUri(filePath) },
        position: { line: line - 1, character: col - 1 },
      });
    },
    [filePath, language]
  );

  const findReferences = useCallback(
    async (line, col) => {
      if (!LSP_LANGUAGES.has(language)) return null;
      return sendRequest(language, "textDocument/references", {
        textDocument: { uri: toUri(filePath) },
        position: { line: line - 1, character: col - 1 },
        context: { includeDeclaration: true },
      });
    },
    [filePath, language]
  );

  const documentSymbols = useCallback(async () => {
    if (!LSP_LANGUAGES.has(language)) return null;
    return sendRequest(language, "textDocument/documentSymbol", {
      textDocument: { uri: toUri(filePath) },
    });
  }, [filePath, language]);

  return {
    notifyOpen,
    notifyChange,
    getHover,
    goToDefinition,
    findReferences,
    documentSymbols,
  };
}

export function getLspSupportedLanguages() {
  return Array.from(LSP_LANGUAGES);
}
