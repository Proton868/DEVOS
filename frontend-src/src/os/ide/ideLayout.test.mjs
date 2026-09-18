import { strict as assert } from "node:assert";
import { resolveIdeLayout, DEFAULT_IDE_LAYOUT } from "./ideLayout.js";

{
  const r = resolveIdeLayout({ width: 1000, height: 700 }, {
    ...DEFAULT_IDE_LAYOUT,
    sidebarOpen: true,
    activity: "explorer",
    bottomOpen: true,
    bottom: "terminal",
  });
  assert.equal(r.sidebarMode, "docked");
  assert.equal(r.bottomMode, "docked");
  assert.equal(r.editorPrimary, true);
}

{
  const r = resolveIdeLayout({ width: 400, height: 700 }, {
    ...DEFAULT_IDE_LAYOUT,
    sidebarOpen: true,
    activity: "search",
  });
  assert.equal(r.sidebarMode, "sheet");
}

{
  const r = resolveIdeLayout({ width: 900, height: 320 }, {
    ...DEFAULT_IDE_LAYOUT,
    bottomOpen: true,
    bottom: "problems",
    sidebarOpen: false,
  });
  assert.ok(r.bottomMode === "sheet" || r.bottomMode === "docked");
}

console.log("ideLayout.test.mjs: ok");
