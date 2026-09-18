import { strict as assert } from "node:assert";
import { resolveProfileLayout } from "./profileLayout.js";
assert.equal(resolveProfileLayout({ width: 1200, height: 800 }).columns, 2);
assert.equal(resolveProfileLayout({ width: 400, height: 800 }).sectionsAsDrawers, true);
console.log("profileLayout.test.mjs: ok");
