/**
 * Monaco loader — same-origin assets under /static/monaco/vs
 * Avoids CDN/CSP/worker cross-origin failures that leave the IDE on
 * "Starting editor…".
 */
import { loader } from "@monaco-editor/react";

let done = false;

export function ensureMonaco() {
  if (done) return;
  done = true;
  try {
    // Same origin as the app; matches CSP script-src 'self'
    const base =
      (typeof window !== "undefined" && window.location && window.location.origin) || "";
    loader.config({
      paths: {
        vs: `${base}/static/monaco/vs`,
      },
    });
  } catch (e) {
    console.warn("monacoSetup: loader.config failed", e);
  }
}

ensureMonaco();
