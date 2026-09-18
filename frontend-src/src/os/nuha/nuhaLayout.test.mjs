import { strict as assert } from "node:assert";
import { resolveNuhaPresentation } from "./nuhaLayout.js";

assert.equal(
  resolveNuhaPresentation(
    { width: 1400, height: 900 },
    { otherPrimaryOpen: true }
  ).presentation,
  "docked"
);

assert.equal(
  resolveNuhaPresentation(
    { width: 390, height: 844, orientation: "portrait" },
    { otherPrimaryOpen: true }
  ).presentation,
  "sheet"
);

assert.equal(
  resolveNuhaPresentation(
    { width: 800, height: 600 },
    { otherPrimaryOpen: true }
  ).presentation,
  "overlay"
);

assert.equal(
  resolveNuhaPresentation(
    { width: 400, height: 800, orientation: "portrait" },
    { explicitChatDimension: true }
  ).presentation,
  "fullscreen"
);

assert.equal(
  resolveNuhaPresentation({ width: 1200, height: 800 }, { preferred: "fullscreen" })
    .presentation,
  "fullscreen"
);

console.log("nuhaLayout.test.mjs: ok");
