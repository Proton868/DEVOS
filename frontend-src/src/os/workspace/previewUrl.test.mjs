function buildPreviewUrl(projectId, path, token) {
  const pid = encodeURIComponent(projectId || "default");
  const clean = (path || "index.html").replace(/^\/+/, "");
  const base = `/api/files/${pid}/preview/${clean.split("/").map(encodeURIComponent).join("/")}`;
  if (token) return `${base}?token=${encodeURIComponent(token)}`;
  return base;
}

function assert(c, m) { if (!c) throw new Error(m || "fail"); }

assert(buildPreviewUrl("ws1", "index.html", "tok") === "/api/files/ws1/preview/index.html?token=tok");
assert(buildPreviewUrl("ws1", "nested/app.js", null) === "/api/files/ws1/preview/nested/app.js");
console.log("previewUrl tests OK");
