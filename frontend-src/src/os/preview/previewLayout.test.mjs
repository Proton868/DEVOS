import { strict as assert } from "node:assert";
import { resolvePreviewChrome } from "./previewLayout.js";

assert.equal(resolvePreviewChrome({ width: 360 }).density, "compact");
assert.equal(resolvePreviewChrome({ width: 360 }).useOverflowMenu, true);
assert.equal(resolvePreviewChrome({ width: 600 }).density, "medium");
assert.equal(resolvePreviewChrome({ width: 1000 }).density, "large");
assert.equal(resolvePreviewChrome({ width: 1000 }).showSecondaryInline, true);
console.log("previewLayout.test.mjs: ok");
