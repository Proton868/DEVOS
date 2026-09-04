import { normalizeSurfaceIntent, applySurfaceIntent } from "./surfaceIntent.js";

function assert(c, m) { if (!c) throw new Error(m || "fail"); }

const inv = normalizeSurfaceIntent({ surface: "foobar", action: "open" });
assert(inv.invalid, "invalid surface");

const chat = normalizeSurfaceIntent({ surface: "chat", action: "none", required: false });
assert(chat.surface === "chat");

const ide = normalizeSurfaceIntent({ surface: "ide", action: "open", required: true });
assert(ide.required === true);

const prev = normalizeSurfaceIntent({
  surface: "preview",
  action: "open",
  required: true,
  context: { filePath: "index.html" },
});
assert(prev.surface === "preview", "preview surface");
assert(prev.action === "open", "preview action");

let opened = false;
let previewOpened = false;
const state = {
  openEditor: () => { opened = true; },
  openPreview: () => { previewOpened = true; },
  closePreview: () => { previewOpened = false; },
  setOverlay: () => {},
  setOmniOpen: () => {},
  setDashboardOpen: () => {},
};
const store = { getState: () => state };

const r = applySurfaceIntent({ surface: "ide", action: "open", required: true }, store);
assert(r.ok && r.status === "ide_opened", "ide open");
assert(opened === true, "editor opened");

const rPrev = applySurfaceIntent(
  { surface: "preview", action: "open", required: true, context: { filePath: "index.html" } },
  store,
);
assert(rPrev.ok && rPrev.status === "preview_opened", "preview open");
assert(previewOpened === true, "preview state");

const r2 = applySurfaceIntent({ surface: "unknown_x", action: "open" }, store);
assert(!r2.ok && r2.status === "invalid_intent", "invalid ignored");

const r3 = applySurfaceIntent({ surface: "chat", action: "none" }, store);
assert(r3.status === "remain_chat");

console.log("surfaceIntent tests OK");
