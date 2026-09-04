/**
 * Monaco loader config.
 *
 * Production CSP historically used script-src 'self' only, which blocked the
 * default CDN and left @monaco-editor/react stuck on "Loading…".
 * We point at jsDelivr and rely on a matching CSP allow in public/index.html.
 */
import { loader } from "@monaco-editor/react";

let done = false;

export function ensureMonaco() {
  if (done) return;
  done = true;
  try {
    loader.config({
      paths: {
        vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.44.0/min/vs",
      },
    });
  } catch (e) {
    console.warn("monacoSetup: loader.config failed", e);
  }
}

ensureMonaco();
