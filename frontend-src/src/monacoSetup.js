/**
 * Deterministic same-origin Monaco initialization.
 *
 * Assets live at /static/monaco/vs (HTTP 200 in production).
 * We do NOT rely on @monaco-editor/react's implicit CDN/path race.
 *
 * Lifecycle:
 *   inject loader.js (once)
 *   → require.config({ paths: { vs } })
 *   → require(["vs/editor/editor.main"])
 *   → loader.config({ monaco })
 *   → resolve(monaco)
 */
import { loader } from "@monaco-editor/react";

const VS = "/static/monaco/vs";
const LOADER_URL = `${VS}/loader.js`;

let monacoPromise = null;

export function monacoVsPath() {
  return VS;
}

function configureWorkers() {
  if (typeof window === "undefined") return;
  // Same-origin workers under CSP worker-src 'self' blob:
  window.MonacoEnvironment = {
    getWorkerUrl(_moduleId, label) {
      const lang = String(label || "");
      if (lang === "json") return `${VS}/language/json/jsonWorker.js`;
      if (lang === "css" || lang === "scss" || lang === "less") {
        return `${VS}/language/css/cssWorker.js`;
      }
      if (lang === "html" || lang === "handlebars" || lang === "razor") {
        return `${VS}/language/html/htmlWorker.js`;
      }
      if (lang === "typescript" || lang === "javascript") {
        return `${VS}/language/typescript/tsWorker.js`;
      }
      return `${VS}/base/worker/workerMain.js`;
    },
  };
}

function injectScript(src) {
  return new Promise((resolve, reject) => {
    if (typeof document === "undefined") {
      reject(new Error("No document — cannot inject Monaco loader"));
      return;
    }
    // Already present?
    const existing = document.querySelector(`script[data-devos-monaco-loader="1"]`);
    if (existing) {
      if (window.require && typeof window.require.config === "function") {
        resolve();
        return;
      }
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () =>
        reject(new Error(`Monaco loader script failed: ${src}`))
      );
      return;
    }
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.dataset.devosMonacoLoader = "1";
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`Failed to load Monaco AMD loader from ${src}`));
    document.head.appendChild(s);
  });
}

/**
 * Load Monaco exactly once. Resolves with the monaco module from AMD.
 * Rejects with a concrete stage-tagged Error.
 */
export function loadMonaco() {
  if (monacoPromise) return monacoPromise;

  monacoPromise = (async () => {
    if (typeof window === "undefined") {
      throw new Error("monaco:init — no window");
    }

    configureWorkers();

    // 1) Inject AMD loader
    try {
      await injectScript(LOADER_URL);
    } catch (e) {
      const err = new Error(`monaco:loader — ${e.message || e}`);
      err.cause = e;
      throw err;
    }

    const req = window.require;
    if (!req || typeof req.config !== "function") {
      throw new Error(
        "monaco:amd — window.require is missing after loader.js (wrong loader or CSP blocked script)"
      );
    }

    // 2) Point AMD at same-origin vs tree
    try {
      req.config({ paths: { vs: VS } });
    } catch (e) {
      throw new Error(`monaco:config — require.config failed: ${e.message || e}`);
    }

    // 3) Require editor.main — this is true readiness, not window.monaco polling
    const monaco = await new Promise((resolve, reject) => {
      try {
        req(
          ["vs/editor/editor.main"],
          (m) => {
            if (m && (m.editor || m.languages)) {
              resolve(m);
            } else if (window.monaco && window.monaco.editor) {
              resolve(window.monaco);
            } else {
              reject(new Error("monaco:module — editor.main returned empty module"));
            }
          },
          (err) => {
            reject(
              new Error(
                `monaco:require — AMD failed to load vs/editor/editor.main: ${
                  err && (err.message || err.requireType || String(err))
                }`
              )
            );
          }
        );
      } catch (e) {
        reject(new Error(`monaco:require — ${e.message || e}`));
      }
    });

    // 4) Hand the resolved module to @monaco-editor/react so <Editor> does not re-fetch
    try {
      loader.config({ monaco });
    } catch (e) {
      // Non-fatal if already configured; Editor may still work with window.monaco
      console.warn("monaco:react-loader-config", e);
    }

    return monaco;
  })().catch((e) => {
    // Allow retry after failure
    monacoPromise = null;
    throw e;
  });

  return monacoPromise;
}

/** @deprecated use loadMonaco — kept for call-site compatibility */
export function ensureMonaco() {
  // Fire-and-forget config only; real load is loadMonaco()
  configureWorkers();
  try {
    loader.config({ paths: { vs: VS } });
  } catch (_) {
    /* ignore */
  }
}

export async function probeMonacoAssets(fetchImpl = fetch) {
  const url = LOADER_URL;
  try {
    const res = await fetchImpl(url, { method: "GET", cache: "no-store" });
    const ct = (res.headers.get("content-type") || "").toLowerCase();
    const text = await res.text();
    if (!res.ok) {
      return { ok: false, status: res.status, contentType: ct, reason: `HTTP ${res.status} for ${url}` };
    }
    if (ct.includes("text/html")) {
      return {
        ok: false,
        status: res.status,
        contentType: ct,
        reason: "loader.js returned HTML (SPA fallback?)",
      };
    }
    if (!text.includes("define") && !text.includes("require")) {
      return {
        ok: false,
        status: res.status,
        contentType: ct,
        reason: "loader.js body is not Monaco AMD loader",
      };
    }
    return { ok: true, status: res.status, contentType: ct, reason: "ok" };
  } catch (e) {
    return {
      ok: false,
      status: 0,
      contentType: "",
      reason: String(e && e.message ? e.message : e),
    };
  }
}

/** Test helper: reset singleton (not for production UI) */
export function __resetMonacoLoaderForTests() {
  monacoPromise = null;
}
