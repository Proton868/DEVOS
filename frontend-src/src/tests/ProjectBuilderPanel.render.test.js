require("jsdom-global")();
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = require("react");
const { createRoot } = require("react-dom/client");
const { act } = require("react");
const store = {};
global.localStorage = {
  getItem: k => store[k] ?? null,
  setItem: (k, v) => store[k] = String(v),
  removeItem: k => delete store[k],
};

const srcRoot = "/tmp/devos-src-cjs";
const Module = require("module");
const path = require("path");
const fs = require("fs");
const origLoad = Module._load;
const stubbed = new Set([
  "react-resizable-panels",
  "@monaco-editor/react",
  "xterm",
  "xterm-addon-fit",
  "xterm-addon-web-links",
  "react-markdown",
  "remark-gfm",
  "./supabase",
]);
Module._load = function(request, parent, isMain){
  if (stubbed.has(request)) {
    if (request === "react-markdown") return ({ children }) => React.createElement("div", null, children);
    return {};
  }
  return origLoad.apply(this, arguments);
};
const origResolve = Module._resolveFilename;
Module._resolveFilename = function(request, parent, isMain, options){
  const resolved = origResolve.call(this, request, parent, isMain, options);
  if (resolved.startsWith(srcRoot)) return resolved;
  if (parent && parent.filename && parent.filename.startsWith(srcRoot)) {
    const baseDir = path.dirname(parent.filename);
    const relParts = path.relative(srcRoot, baseDir).split(path.sep);
    const candidates = [];
    if (request.startsWith(".")) {
      for (let up = 0; up < 6; up++) {
        const parts = [...relParts];
        for (let i = 0; i < up; i++) parts.pop();
        const base = path.join(srcRoot, ...parts);
        candidates.push(
          path.join(base, request + ".js"),
          path.join(base, request + ".jsx"),
          path.join(base, request, "index.js"),
          path.join(base, request, "index.jsx")
        );
      }
    } else {
      candidates.push(
        path.join(srcRoot, request + ".js"),
        path.join(srcRoot, request + ".jsx"),
        path.join(srcRoot, request, "index.js"),
        path.join(srcRoot, request, "index.jsx")
      );
    }
    for (const c of candidates) {
      if (fs.existsSync(c)) return c;
    }
  }
  return resolved;
};

const calls = [];
global.fetch = async (url, opts={}) => {
  calls.push({url, opts});
  if (url.includes("/api/extras/stacks")) {
    return { ok: true, status: 200, text: async () => JSON.stringify({ stacks: [
      { id: "fastapi", language: "python", description: "FastAPI API" },
      { id: "html", language: "html", description: "Static site" },
    ]}) };
  }
  if (url.includes("/api/extras/projects")) {
    return { ok: true, status: 200, text: async () => JSON.stringify({ projects: [] }) };
  }
  if (url.includes("/api/extras/build")) {
    const body = JSON.parse(opts.body);
    return { ok: true, status: 200, text: async () => JSON.stringify({
      project_id: "abc123",
      spec: { name: body.name, stack: body.stack, language: "html" },
      files: [{ path: "index.html" }],
      setup_commands: ["open index.html"],
      errors: [],
    })};
  }
  return { ok: false, status: 404, text: async () => "{}" };
};

const ProjectBuilderPanel = require(path.join(srcRoot, "components/builder/ProjectBuilderPanel")).default;
function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }
(async () => {
  const container = document.createElement("div");
  document.body.appendChild(container);
  await act(async () => {
    createRoot(container).render(React.createElement(ProjectBuilderPanel));
  });
  await sleep(50);

  const stackOptions = container.querySelectorAll("option");
  console.log("options count", stackOptions.length);
  if (stackOptions.length !== 2) throw new Error("expected 2 stack options");

  const nameInput = container.querySelector('[data-testid="builder-name-input"]');
  const stackSelect = container.querySelector('[data-testid="builder-stack-select"]');
  const submitBtn = container.querySelector('[data-testid="builder-submit-btn"]');

  await act(async () => {
    nameInput.value = "test-app";
    nameInput.dispatchEvent(new Event("input", { bubbles: true }));
    stackSelect.value = "html";
    stackSelect.dispatchEvent(new Event("change", { bubbles: true }));
  });

  await act(async () => submitBtn.click());
  await sleep(150);

  const text = container.textContent;
  console.log("panel text snippet:", text.slice(0, 240));
  if (!text.includes("Build complete")) throw new Error("expected 'Build complete' but got: " + text);
  if (!text.includes("abc123")) throw new Error("expected project id");

  const buildCalls = calls.filter(c => c.url.includes("/api/extras/build"));
  if (buildCalls.length !== 1) throw new Error("expected 1 build call, got " + buildCalls.length);

  console.log("PASS: ProjectBuilderPanel renders and submits against backend shapes.");
})().catch(e => { console.error("FAIL:", e.message || e); process.exit(1); });
