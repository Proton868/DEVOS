import { strict as assert } from "node:assert";
import { resolveFlowLayout } from "./flowLayout.js";

{
  const r = resolveFlowLayout(
    { width: 1400, height: 900, orientation: "landscape" },
    { inspectorOpen: true }
  );
  assert.equal(r.inspectorMode, "beside");
  assert.equal(r.showCanvas, true);
}

{
  const r = resolveFlowLayout(
    { width: 390, height: 844, orientation: "portrait" },
    { inspectorOpen: true }
  );
  assert.ok(["drawer", "fullscreen", "overlay"].includes(r.inspectorMode));
  assert.equal(r.chromeDensity, "compact");
}

{
  const r = resolveFlowLayout({ width: 400, height: 300 }, { inspectorOpen: true });
  assert.equal(r.inspectorMode, "fullscreen");
  assert.equal(r.showCanvas, false);
}

{
  const r = resolveFlowLayout({ width: 1000, height: 700 }, { inspectorOpen: false });
  assert.equal(r.inspectorMode, "hidden");
}

console.log("flowLayout.test.mjs: ok");
