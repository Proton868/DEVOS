function buildPreviewUrl(projectId, path, token) {
  const pid = encodeURIComponent(projectId || "default");
  const clean = (path || "index.html").replace(/^\/+/, "");
  const base = `/api/files/${pid}/preview/${clean.split("/").map(encodeURIComponent).join("/")}`;
  if (token) return `${base}?token=${encodeURIComponent(token)}`;
  return base;
}

const PREVIEW_IFRAME_SANDBOX = "allow-scripts";

function assert(c, m) { if (!c) throw new Error(m || "fail"); }

assert(buildPreviewUrl("ws1", "index.html", "tok") === "/api/files/ws1/preview/index.html?token=tok");
assert(buildPreviewUrl("ws1", "nested/app.js", null) === "/api/files/ws1/preview/nested/app.js");
// Sandbox must NOT include allow-same-origin (same-origin + scripts = sandbox escape risk)
assert(PREVIEW_IFRAME_SANDBOX === "allow-scripts");
assert(!PREVIEW_IFRAME_SANDBOX.includes("allow-same-origin"));
assert(!PREVIEW_IFRAME_SANDBOX.includes("allow-top-navigation"));
assert(!PREVIEW_IFRAME_SANDBOX.includes("allow-popups"));
console.log("previewUrl tests OK");
