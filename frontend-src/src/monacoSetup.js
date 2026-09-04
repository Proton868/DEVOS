/**
 * Monaco loader — same-origin assets at /static/monaco/vs
 *
 * Production must serve frontend/static/monaco/vs (committed + deploy-vendored).
 * CDN is intentionally NOT used (CSP script-src 'self').
 */
import { loader } from "@monaco-editor/react";

let done = false;

export function monacoVsPath() {
  return "/static/monaco/vs";
}

export function ensureMonaco() {
  if (done) return;
  done = true;
  const vs = monacoVsPath();
  try {
    // Workers must load from same origin under CSP worker-src 'self' blob:
    if (typeof window !== "undefined") {
      window.MonacoEnvironment = {
        getWorkerUrl(moduleId, label) {
          // Prefer classic AMD worker bootstrap from same-origin vs tree
          return `${vs}/base/worker/workerMain.js`;
        },
      };
    }
    loader.config({ paths: { vs } });
  } catch (e) {
    console.warn("monacoSetup: loader.config failed", e);
  }
}

/**
 * Probe that production actually delivers the Monaco loader (not SPA/HTML/404).
 * Returns { ok, status, contentType, reason }
 */
export async function probeMonacoAssets(fetchImpl = fetch) {
  const url = `${monacoVsPath()}/loader.js`;
  try {
    const res = await fetchImpl(url, { method: "GET", cache: "no-store" });
    const ct = (res.headers.get("content-type") || "").toLowerCase();
    const text = await res.text();
    if (!res.ok) {
      return { ok: false, status: res.status, contentType: ct, reason: `HTTP ${res.status} for ${url}` };
    }
    if (ct.includes("text/html")) {
      return { ok: false, status: res.status, contentType: ct, reason: "loader.js returned HTML (SPA fallback?)" };
    }
    if (!text.includes("define") && !text.includes("monaco") && !text.includes("require")) {
      return { ok: false, status: res.status, contentType: ct, reason: "loader.js body is not Monaco AMD loader" };
    }
    return { ok: true, status: res.status, contentType: ct, reason: "ok" };
  } catch (e) {
    return { ok: false, status: 0, contentType: "", reason: String(e && e.message ? e.message : e) };
  }
}

ensureMonaco();
