import { useEffect, useRef, useCallback } from "react";
import useStore from "../store/useStore";

const WS_BASE = (process.env.REACT_APP_BACKEND_URL || "http://localhost:3001")
  .replace("http://", "ws://")
  .replace("https://", "wss://");

// Languages that have LSP server support
const LSP_LANGUAGES = new Set(["typescript", "javascript", "python", "css", "html"]);

// Active WebSocket connections per language
const connections = new Map();
let msgId = 1;
const pendingRequests = new Map();
const diagnosticHandlers = new Set();

function getOrConnect(language) {
  if (connections.has(language)) return connections.get(language);

  const ws = new WebSocket(`${WS_BASE}?type=lsp&lang=${language}`);
  const pending = [];

  ws.onopen = () => {
    // Flush queued messages
    for (const msg of pending) ws.send(msg);
    pending.length = 0;
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      // Handle responses to requests
      if (msg.id && pendingRequests.has(msg.id)) {
        const { resolve, reject } = pendingRequests.get(msg.id);
        pendingRequests.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message));
        else resolve(msg.result);
      }
      // Handle diagnostics notification
      if (msg.method === "textDocument/publishDiagnostics") {
        for (const handler of diagnosticHandlers) handler(msg.params);
      }
    } catch {}
  };

  ws.onerror = () => {};
  ws.onclose = () => { connections.delete(language); };

  const conn = {
    ws,
    pending,
    send: (msg) => {
      const str = JSON.stringify(msg);
      if (ws.readyState === WebSocket.OPEN) ws.send(str);
      else pending.push(str);
    },
  };

  connections.set(language, conn);
  return conn;
}

function sendRequest(language, method, params) {
  if (!LSP_LANGUAGES.has(language)) return Promise.resolve(null);
  const conn = getOrConnect(language);
  const id = msgId++;
  return new Promise((resolve, reject) => {
    pendingRequests.set(id, { resolve, reject });
    conn.send({ jsonrpc: "2.0", id, method, params });
    // Timeout after 5s
    setTimeout(() => {
      if (pendingRequests.has(id)) {
        pendingRequests.delete(id);
        resolve(null);
      }
    }, 5000);
  });
}

function sendNotification(language, method, params) {
  if (!LSP_LANGUAGES.has(language)) return;
  const conn = getOrConnect(language);
  conn.send({ jsonrpc: "2.0", method, params });
}

// Convert Monaco URI to LSP URI
function toUri(path) {
  return `file://${path.startsWith("/") ? path : "/" + path}`;
}

// Convert LSP severity (1=Error, 2=Warning, 3=Info, 4=Hint)
function severityName(n) {
  return n === 1 ? "error" : n === 2 ? "warning" : "info";
}

export function useLSP(editorRef, monacoRef, filePath, language) {
  const { setProblems, clearProblemsForFile } = useStore();
  const initializedRef = useRef(false);

  // Register diagnostic handler
  useEffect(() => {
    const handler = (params) => {
      const uri = params.uri?.replace("file://", "") || "";
      const relPath = uri.startsWith("/workspace/") ? uri.slice("/workspace/".length) : uri;
      const diagnostics = (params.diagnostics || []).map(d => ({
        path: relPath || filePath,
        line: (d.range?.start?.line || 0) + 1,
        col: (d.range?.start?.character || 0) + 1,
        message: d.message,
        severity: severityName(d.severity),
        source: d.source,
      }));
      // Replace problems for this file
      useStore.setState(s => ({
        problems: [
          ...s.problems.filter(p => p.path !== (relPath || filePath)),
          ...diagnostics,
        ],
      }));
      // Also apply Monaco markers
      if (editorRef.current && monacoRef.current && filePath === (relPath || filePath)) {
        const model = editorRef.current.getModel();
        if (model) {
          const markers = (params.diagnostics || []).map(d => ({
            startLineNumber: (d.range?.start?.line || 0) + 1,
            startColumn: (d.range?.start?.character || 0) + 1,
            endLineNumber: (d.range?.end?.line || 0) + 1,
            endColumn: (d.range?.end?.character || 0) + 1,
            message: d.message,
            severity: d.severity === 1
              ? monacoRef.current.MarkerSeverity.Error
              : d.severity === 2
              ? monacoRef.current.MarkerSeverity.Warning
              : monacoRef.current.MarkerSeverity.Info,
            source: d.source,
          }));
          monacoRef.current.editor.setModelMarkers(model, "lsp", markers);
        }
      }
    };
    diagnosticHandlers.add(handler);
    return () => diagnosticHandlers.delete(handler);
  }, [filePath, editorRef, monacoRef]);

  // Initialize LSP for this file
  useEffect(() => {
    if (!filePath || !language || !LSP_LANGUAGES.has(language)) return;

    const uri = toUri(filePath);

    if (!initializedRef.current) {
      // Initialize connection
      sendRequest(language, "initialize", {
        processId: null,
        rootUri: "file:///workspace",
        capabilities: {
          textDocument: {
            completion: { completionItem: { snippetSupport: true } },
            hover: {},
            definition: {},
            publishDiagnostics: { relatedInformation: true },
          },
        },
        workspaceFolders: [{ uri: "file:///workspace", name: "workspace" }],
      }).then(() => {
        sendNotification(language, "initialized", {});
        initializedRef.current = true;
      });
    }

    return () => {
      // Don't close connection on unmount — keep alive for the session
    };
  }, [language, filePath]);

  // Notify LSP when file opens
  const notifyOpen = useCallback((content) => {
    if (!filePath || !LSP_LANGUAGES.has(language)) return;
    sendNotification(language, "textDocument/didOpen", {
      textDocument: { uri: toUri(filePath), languageId: language, version: 1, text: content },
    });
  }, [filePath, language]);

  // Notify LSP when content changes
  const notifyChange = useCallback((content, version) => {
    if (!filePath || !LSP_LANGUAGES.has(language)) return;
    sendNotification(language, "textDocument/didChange", {
      textDocument: { uri: toUri(filePath), version },
      contentChanges: [{ text: content }],
    });
  }, [filePath, language]);

  // Hover info
  const getHover = useCallback(async (line, col) => {
    if (!LSP_LANGUAGES.has(language)) return null;
    return sendRequest(language, "textDocument/hover", {
      textDocument: { uri: toUri(filePath) },
      position: { line: line - 1, character: col - 1 },
    });
  }, [filePath, language]);

  // Go to definition
  const goToDefinition = useCallback(async (line, col) => {
    if (!LSP_LANGUAGES.has(language)) return null;
    return sendRequest(language, "textDocument/definition", {
      textDocument: { uri: toUri(filePath) },
      position: { line: line - 1, character: col - 1 },
    });
  }, [filePath, language]);

  return { notifyOpen, notifyChange, getHover, goToDefinition };
}
