import { strict as assert } from "node:assert";
import {
  openTab,
  closeTab,
  patchTab,
  openSplit,
  closeSplit,
  languageFromPath,
  mergeDiagnostics,
  flattenDiagnostics,
} from "./ideTabs.js";

{
  let { tabs, activePath } = openTab([], { path: "a.py" });
  assert.equal(tabs.length, 1);
  assert.equal(activePath, "a.py");
  ({ tabs, activePath } = openTab(tabs, { path: "b.js" }));
  assert.equal(tabs.length, 2);
  assert.equal(activePath, "b.js");
  ({ tabs, activePath } = openTab(tabs, { path: "a.py" }));
  assert.equal(tabs.length, 2);
  assert.equal(activePath, "a.py");
}

{
  const tabs = [{ path: "a" }, { path: "b" }, { path: "c" }];
  const r = closeTab(tabs, "b", { activePath: "b", splitPath: "b" });
  assert.equal(r.tabs.length, 2);
  assert.equal(r.splitPath, null);
  assert.ok(r.activePath === "a" || r.activePath === "c");
}

{
  const tabs = patchTab([{ path: "a", modified: false }], "a", { modified: true });
  assert.equal(tabs[0].modified, true);
}

{
  const s = openSplit("a.py", null, "b.py");
  assert.equal(s.splitPath, "b.py");
  assert.equal(closeSplit("a.py").splitPath, null);
}

assert.equal(languageFromPath("src/app.tsx"), "typescript");
assert.equal(languageFromPath("main.py"), "python");

{
  let map = mergeDiagnostics({}, {
    uri: "file:///home/x/main.py",
    diagnostics: [{ severity: 1, message: "err", range: { start: { line: 0 } } }],
  });
  assert.ok(Object.keys(map).length >= 1);
  const flat = flattenDiagnostics(map);
  assert.equal(flat.length, 1);
  assert.equal(flat[0].message, "err");
}

console.log("ideTabs.test.mjs: ok");
